"""Run directories: `rb-runs/<run_id>/{bundle.json, record.json, findings.json, report.md}`."""
from __future__ import annotations

import hashlib
import json
import os
import platform
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from pydantic import ValidationError

from . import __version__
from .checks import evaluate_all
from .errors import RBError
from .importer import parse_comparison_value
from .models import (Bundle, CaseV2, CheckCounts, ChecksV2, ComparisonV1, DatasetRef, Findings, Limits, MeanError, ModelRef, QueueItem, Record, Summary, Verdict)
from .prose import why
from .stability import SummaryNumbers, case_stability, delta, error_outcome, flags_for, rank, stability_outcome, trajectory_change, trajectory_stats, verdict_text

DEFAULT_RUNS_DIR = "rb-runs"


def runs_dir(explicit: Optional[str] = None) -> Path:
    return Path(explicit or os.environ.get("RB_RUNS_DIR") or DEFAULT_RUNS_DIR)


def slug(text: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9_.-]+", "-", text.strip()).strip("-").lower()
    return s[:40] or "candidate"


def new_run_id(candidate: str, base: Path, now: Optional[datetime] = None) -> str:
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M")
    rid = f"{stamp}-{slug(candidate)}"
    candidate_id, n = rid, 2
    while (base / candidate_id).exists():
        candidate_id = f"{rid}-{n}"
        n += 1
    return candidate_id


def content_hash(cases) -> Optional[str]:
    """sha256 over the bytes of every file a case names (inputs and ground truth, when they are paths), in case order."""
    h = hashlib.sha256()
    seen = 0
    for c in cases:
        paths = []
        inputs = c.inputs if isinstance(c.inputs, (list, tuple)) else [c.inputs]
        for item in [*inputs, c.gt]:
            if isinstance(item, str) and Path(item).is_file():
                paths.append(item)
        for p in paths:
            h.update(p.encode("utf-8"))
            with open(p, "rb") as f:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    h.update(chunk)
            seen += 1
    return h.hexdigest() if seen else None


def case_list_hash(ids: list[str]) -> str:
    return hashlib.sha256("\n".join(sorted(ids)).encode("utf-8")).hexdigest()[:16]


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------- derive

def derive_case(case, limits: Limits) -> CaseV2:
    data = case.model_dump(include=set(type(case).model_fields) & {"id", "name", "tags", "baseline_error", "candidate_error", "baseline_frames", "candidate_frames", "baseline_trajectory", "candidate_trajectory", "baseline_convergence", "candidate_convergence", "notes"})
    return CaseV2(
        **data,
        has_gt=getattr(case, "has_gt", True) and data.get("baseline_error") is not None and data.get("candidate_error") is not None,
        error_change=delta(case),
        late_update_change=trajectory_change(case),
        stability=case_stability(case),
        error_outcome=error_outcome(case, limits.max_regression),
        stability_outcome=stability_outcome(case, limits),
        flags=flags_for(case, limits),
        evidence=getattr(case, "evidence", None),
    )


def bundle_from_comparison(cmp: ComparisonV1, run_id: str, limits: Limits, source: str) -> Bundle:
    return Bundle(
        version=2, run_id=run_id, project=cmp.project, task="generic", source=source,  # type: ignore[arg-type]
        metric=cmp.metric_obj(),
        baseline=ModelRef(name=cmp.baseline), candidate=ModelRef(name=cmp.candidate),
        dataset=DatasetRef(name=cmp.dataset, count=len(cmp.cases), case_list_hash=case_list_hash([c.id for c in cmp.cases])),
        limits=limits, record="record.json",
        cases=[derive_case(c, limits) for c in cmp.cases],
    )


def rederive(bundle: Bundle, limits: Limits) -> Bundle:
    """Recompute the limit-dependent fields under different limits (does not touch the run directory)."""
    return bundle.model_copy(update={"limits": limits, "cases": [derive_case(c, limits) for c in bundle.cases]})


def compute_findings(bundle: Bundle, limits: Optional[Limits] = None, checks: Optional[ChecksV2] = None) -> Findings:
    limits = limits or bundle.limits
    cases = bundle.cases
    unit = bundle.metric.unit
    s = SummaryNumbers(cases, limits)
    counts = CheckCounts()
    results = evaluate_all(checks, bundle, unit) if checks else []
    for r in results:
        counts.saved += 1
        if r.status == "passing":
            counts.passing += 1
        elif r.status == "failing":
            counts.failing += 1
        elif r.status == "missing":
            counts.missing += 1
        else:
            counts.other_project += 1
    v = verdict_text(cases, limits)
    line = v["text"]
    if counts.failing or counts.missing:
        n = counts.failing + counts.missing
        line = line.rstrip(".") + f"; {n} saved check{'' if n == 1 else 's'} failing." if not v["ready"] else f"{n} saved check{'' if n == 1 else 's'} failing. {line}"
        status = "checks_failing"
    else:
        status = "clear" if v["ready"] else "investigate"
    if not v["ready"]:
        line = f"{line} Start with {v['start']}."
    queue: list[QueueItem] = []
    for i, c in enumerate(rank(cases, limits), start=1):
        st = case_stability(c)
        queue.append(QueueItem(
            rank=i, id=c.id, name=c.name, tags=list(c.tags), flags=flags_for(c, limits),
            error_outcome=error_outcome(c, limits.max_regression), stability_outcome=stability_outcome(c, limits),  # type: ignore[arg-type]
            baseline_error=c.baseline_error, candidate_error=c.candidate_error, error_change=delta(c),
            baseline_late_share=st.baseline.late_share if st.baseline else None, baseline_reversals=st.baseline.reversals if st.baseline else None,
            candidate_late_share=st.candidate.late_share if st.candidate else None, candidate_reversals=st.candidate.reversals if st.candidate else None,
            baseline_late_update=st.baseline.late_update if st.baseline else None, candidate_late_update=st.candidate.late_update if st.candidate else None,
            late_update_change=trajectory_change(c),
            why=why(c, limits, unit), notes=c.notes, evidence=(c.evidence.dir if c.evidence else None), rerun=None,
        ))
    summary = Summary(
        cases=s.cases, with_gt=s.with_gt, with_trajectories=s.with_trajectories,
        mean_error=MeanError(baseline=s.baseline, candidate=s.candidate, change_pct=s.change),
        regressions=s.regressions, improved=s.improved, stable=s.stable, unstable=s.unstable,
        improved_unstable=s.unstable_passing, settled_regressions=s.settled_regressions, flagged=s.flagged, checks=counts,
    )
    return Findings(run_id=bundle.run_id, project=bundle.project, metric=bundle.metric, limits=limits, summary=summary,
                    verdict=Verdict(status=status, ready=v["ready"], line=line, start=v["start"], start_name=v["start_name"]), queue=queue)  # type: ignore[arg-type]


# ---------------------------------------------------------------- write / read

def environment() -> dict:
    return {"python": platform.python_version(), "platform": platform.platform(), "rb_version": __version__}


def import_record(bundle: Bundle, cmp: ComparisonV1, input_path: Optional[Path], input_format: str, started: datetime, command: str, note: str = "") -> Record:
    finished = datetime.now().astimezone()
    lengths = sorted(len(c.candidate_trajectory) for c in cmp.cases if c.candidate_trajectory)
    hook = {"status": "imported" if lengths else "disabled", "verified": None, "iterations": lengths[len(lengths) // 2] if lengths else None,
            "note": "Trajectories were supplied by the evaluator; rb did not record them." if lengths else "No trajectories in the import; stability not assessed."}
    return Record(
        rb_version=__version__, run_id=bundle.run_id, command=command,
        started=started.isoformat(timespec="seconds"), finished=finished.isoformat(timespec="seconds"),
        wall_seconds=round((finished - started).total_seconds(), 3), source=bundle.source,
        checkpoints={"baseline": bundle.baseline, "candidate": bundle.candidate}, dataset=bundle.dataset,
        environment=environment(), hook=hook, limits=bundle.limits,
        input={"path": str(input_path) if input_path else None, "sha256": file_sha256(input_path) if input_path else None, "format": input_format,
               "bytes": input_path.stat().st_size if input_path else None},
        notes=note or "",
    )


def write_run(base: Path, bundle: Bundle, record: Record, findings: Findings, report_md: str) -> Path:
    run_dir = base / bundle.run_id
    try:
        if (run_dir / "bundle.json").exists():
            raise RBError("E_WRITE_FAILED", message=f"{run_dir} already holds a run.")
        run_dir.mkdir(parents=True, exist_ok=True)  # evidence rendering may have created it already
        # nulls are written explicitly so the files and `--json` output are the same documents
        (run_dir / "bundle.json").write_text(json.dumps(bundle.model_dump(), indent=1) + "\n", encoding="utf-8")
        (run_dir / "record.json").write_text(json.dumps(record.model_dump(), indent=1) + "\n", encoding="utf-8")
        (run_dir / "findings.json").write_text(json.dumps(findings.model_dump(), indent=1) + "\n", encoding="utf-8")
        (run_dir / "report.md").write_text(report_md, encoding="utf-8")
    except OSError as exc:
        raise RBError("E_WRITE_FAILED", message=f"Could not write {run_dir}: {exc}")
    return run_dir


def load_bundle_file(path: Path) -> Bundle:
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError:
        raise RBError("E_RUN_NOT_FOUND", message=f"{path} is not valid JSON.")
    if isinstance(raw, dict) and raw.get("version") == 2:
        try:
            return Bundle.model_validate(raw)
        except ValidationError as exc:
            raise RBError("E_RUN_NOT_FOUND", message=f"{path}: not a valid bundle ({exc.errors()[0].get('msg', 'invalid')}).")
    cmp = parse_comparison_value(raw)
    return bundle_from_comparison(cmp, f"adhoc-{slug(path.stem)}", Limits(), "example" if cmp.source == "example" else "imported")


def resolve_run(arg: str, base: Path) -> tuple[Bundle, Optional[Path]]:
    """A run id under the runs directory, a run directory, a bundle.json, or a version-1 comparison file (read in place)."""
    p = Path(arg)
    if p.is_dir() and (p / "bundle.json").exists():
        return load_bundle_file(p / "bundle.json"), p
    if p.is_file():
        bundle = load_bundle_file(p)
        return bundle, (p.parent if p.name == "bundle.json" else None)
    candidate = base / arg
    if (candidate / "bundle.json").exists():
        return load_bundle_file(candidate / "bundle.json"), candidate
    raise RBError("E_RUN_NOT_FOUND", message=f"No run '{arg}' under {base}/ and no such file.")


def list_runs(base: Path) -> list[str]:
    if not base.exists():
        return []
    return sorted(p.name for p in base.iterdir() if (p / "bundle.json").exists())

