"""`rb run`: execute both checkpoints on the case set through the adapter, record trajectories, write the run directory."""
from __future__ import annotations

import math
import os
import platform
import random
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

from . import __version__
from .adapters import load_adapter
from .adapters.base import Case
from .checks import evaluate_all
from .config import Config
from .errors import RBError
from .models import Bundle, CaseV2, ChecksV2, DatasetRef, Evidence, Limits, ModelRef, Record
from .recorder import TrajectoryRecorder
from .runs import case_list_hash, compute_findings, derive_case, file_sha256, new_run_id, write_run
from .report import report_markdown

Progress = Optional[Callable[[str], None]]


# ---------------------------------------------------------------- environment

def set_seeds(seed: int) -> tuple[dict, Optional[bool]]:
    seeds = {"python": seed}
    random.seed(seed)
    try:
        import numpy as np
        np.random.seed(seed)
        seeds["numpy"] = seed
    except ImportError:
        pass
    deterministic: Optional[bool] = None
    try:
        import torch
        torch.manual_seed(seed)
        seeds["torch"] = seed
        try:
            deterministic = bool(torch.are_deterministic_algorithms_enabled())
        except Exception:  # pragma: no cover
            deterministic = None
    except ImportError:
        pass
    return seeds, deterministic


def environment_info(device: Optional[str] = None) -> dict:
    env: dict[str, Any] = {"python": platform.python_version(), "platform": platform.platform(), "rb_version": __version__}
    try:
        import torch
        env["torch"] = torch.__version__
        env["cuda"] = getattr(torch.version, "cuda", None)
        env["cuda_available"] = bool(torch.cuda.is_available())
        if torch.cuda.is_available():
            env["gpu"] = torch.cuda.get_device_name(0)
    except ImportError:
        env["torch"] = None
    if device:
        env["device"] = device
    return env


def checkpoint_ref(path: Path, name: Optional[str] = None) -> ModelRef:
    if not path.exists():
        raise RBError("E_CHECKPOINT_NOT_FOUND", message=f"No such checkpoint: {path}")
    return ModelRef(name=name or path.stem, checkpoint=str(path), sha256=file_sha256(path))


# ---------------------------------------------------------------- evaluation

def evaluate_model(adapter: Any, model: Any, cases: list[Case], *, record_trajectories: bool, role: str, progress: Progress = None) -> dict[str, dict]:
    results: dict[str, dict] = {}
    t0 = time.time()
    scale = float(getattr(adapter, "trajectory_scale", 1.0) or 1.0)
    for i, case in enumerate(cases, start=1):
        rec = TrajectoryRecorder(scale=scale)
        try:
            pred = adapter.infer(model, case, rec)
            error = adapter.metric_value(pred, case)
        except RBError:
            raise
        except Exception as exc:  # one case failing must not lose the run; it is recorded and excluded
            results[case.id] = {"error": None, "trajectory": None, "frames": None, "skipped": f"{type(exc).__name__}: {str(exc)[:200]}"}
            if progress:
                progress(f"{role}: {case.id} skipped ({type(exc).__name__})")
            continue
        if error is not None and not math.isfinite(float(error)):
            results[case.id] = {"error": None, "trajectory": None, "frames": None, "skipped": "non-finite error (NaN or inf) from the adapter", "fired": len(rec.values)}
            if progress:
                progress(f"{role}: {case.id} skipped (non-finite error)")
            continue
        values = list(rec.values)
        finite = all(math.isfinite(v) for v in values)
        traj = values if (record_trajectories and finite and 2 <= len(values) <= 64) else None
        convergence = None
        if traj is not None:
            try:
                convergence = rec.convergence()
            except Exception as exc:  # noqa: BLE001  (statistics are optional; the run must not fail on them)
                if progress:
                    progress(f"{role}: {case.id} convergence statistics not computed ({type(exc).__name__})")
        rec.reset()
        results[case.id] = {"error": error, "trajectory": traj, "convergence": convergence, "frames": getattr(pred, "per_frame", None), "skipped": None, "fired": len(values), "finite": finite}
        if progress and (i % 10 == 0 or i == len(cases)):
            progress(f"{role}: {i}/{len(cases)} cases · {time.time() - t0:.0f}s")
    return results


def hook_status(results: dict[str, dict], expected: Optional[int], record_trajectories: bool) -> dict:
    if not record_trajectories:
        return {"status": "disabled", "verified": None, "iterations": None, "expected": expected, "note": "Trajectories were not recorded (--no-trajectories); stability not assessed."}
    fired = [r["fired"] for r in results.values() if r.get("skipped") is None and "fired" in r]
    if not fired or max(fired) == 0:
        return {"status": "not_reachable", "verified": False, "iterations": 0, "expected": expected, "note": "The recorder never fired: the update loop is not instrumented. Stability not assessed."}
    lengths = sorted(fired)
    median = lengths[len(lengths) // 2]
    verified = (expected is None) or all(f == expected for f in fired)
    non_finite = sum(1 for r in results.values() if r.get("skipped") is None and r.get("finite") is False)
    note = "Recorded once per refinement iteration." if verified else f"Recorded {min(fired)}–{max(fired)} values per case, expected {expected}: check the hook fires once per iteration."
    if non_finite:
        note += f" {non_finite} case(s) had non-finite update magnitudes and no trajectory was kept for them."
    return {"status": "recorded" if verified else "length_mismatch", "verified": verified, "iterations": median, "expected": expected, "note": note}


# ---------------------------------------------------------------- the run

def run(cfg: Config, baseline: Path, candidate: Path, *, limits: Optional[Limits] = None, runs_dir: Path, command: str,
        device: Optional[str] = None, seed: int = 0, no_trajectories: bool = False, limit: Optional[int] = None,
        baseline_name: Optional[str] = None, candidate_name: Optional[str] = None, checks: Optional[ChecksV2] = None,
        progress: Progress = None, evidence: Optional[str] = None) -> tuple[Path, Bundle, Record, Any, str]:
    started = datetime.now().astimezone()
    t0 = time.time()
    limits = limits or cfg.limits
    device = device or cfg.adapter.device
    seeds, deterministic = set_seeds(seed)
    adapter = load_adapter(cfg)
    base_ref, cand_ref = checkpoint_ref(baseline, baseline_name), checkpoint_ref(candidate, candidate_name)
    cases = list(adapter.cases())
    if limit:
        cases = cases[:limit]
    if not cases:
        raise RBError("E_DATASET_EMPTY", message="The adapter produced no cases.")
    ids = [c.id for c in cases]
    if len(set(ids)) != len(ids):
        raise RBError("E_DATASET_EMPTY", message="The adapter produced duplicate case ids.")
    expected = adapter.expected_iterations()
    if progress:
        progress(f"{len(cases)} cases · adapter {adapter.describe().get('id', '?')} · device {device}")

    model = adapter.load(baseline, device)
    base_results = evaluate_model(adapter, model, cases, record_trajectories=not no_trajectories, role="baseline", progress=progress)
    del model
    model = adapter.load(candidate, device)
    cand_results = evaluate_model(adapter, model, cases, record_trajectories=not no_trajectories, role="candidate", progress=progress)
    del model

    skipped = {cid: {"baseline": base_results[cid]["skipped"], "candidate": cand_results[cid]["skipped"]} for cid in ids if base_results[cid]["skipped"] or cand_results[cid]["skipped"]}
    kept = [c for c in cases if c.id not in skipped]
    if not kept:
        raise RBError("E_INFERENCE_FAILED", message=f"Every case failed inference. First failure: {next(iter(skipped.values()))}")

    v2_cases: list[CaseV2] = []
    for c in kept:
        b, k = base_results[c.id], cand_results[c.id]
        raw = CaseV2(
            id=c.id, name=c.name, tags=list(c.tags)[:8], notes=c.notes,
            baseline_error=b["error"], candidate_error=k["error"],
            baseline_trajectory=b["trajectory"], candidate_trajectory=k["trajectory"],
            baseline_convergence=b.get("convergence"), candidate_convergence=k.get("convergence"),
            baseline_frames=b["frames"] if (b["frames"] and k["frames"] and len(b["frames"]) == len(k["frames"])) else None,
            candidate_frames=k["frames"] if (b["frames"] and k["frames"] and len(b["frames"]) == len(k["frames"])) else None,
            has_gt=b["error"] is not None and k["error"] is not None,
        )
        v2_cases.append(derive_case(raw, limits))

    run_id = new_run_id(cand_ref.name, runs_dir)
    dataset = DatasetRef(name=cfg.dataset.name, count=len(v2_cases), case_list_hash=case_list_hash([c.id for c in v2_cases]))
    bundle = Bundle(version=2, run_id=run_id, project=cfg.project.name, task=cfg.project.task, source="run", metric=adapter.metric,
                    baseline=base_ref, candidate=cand_ref, dataset=dataset, limits=limits, record="record.json",
                    adapter=adapter.describe(), cases=v2_cases)
    hook = hook_status(cand_results, expected, not no_trajectories)
    finished = datetime.now().astimezone()
    describe = adapter.describe()
    record = Record(
        rb_version=__version__, run_id=run_id, command=command,
        started=started.isoformat(timespec="seconds"), finished=finished.isoformat(timespec="seconds"), wall_seconds=round(time.time() - t0, 3),
        source="run", checkpoints={"baseline": base_ref, "candidate": cand_ref}, dataset=dataset,
        environment=environment_info(device), hook=hook, limits=limits,
        adapter=describe, model_code={"path": describe.get("model_code"), **(describe.get("git_sha") or {})} if describe.get("model_code") else None,
        seeds=seeds, deterministic_algorithms=deterministic,
        input={"format": "run", "dataset_path": cfg.dataset.path, "cases_selector": cfg.dataset.cases, "limit": limit},
        skipped=skipped,
        notes=("Synthetic adapter: a test double, not a real model; the numbers are illustrative. " if getattr(adapter, "synthetic", False) else "")
              + (f"{len(skipped)} case(s) skipped after inference errors; see skipped." if skipped else ""),
    )
    findings = compute_findings(bundle, limits, checks)
    evidence_level = evidence or cfg.evidence.level
    if evidence_level != "none" and findings.queue:
        top_n = cfg.evidence.top if evidence_level == "standard" else len(findings.queue)
        targets = [q.id for q in findings.queue if "regression" in q.flags or "unstable" in q.flags][:top_n] if evidence_level == "standard" else [q.id for q in findings.queue]
        if targets:
            from .evidence import render_case
            run_dir_planned = runs_dir / run_id
            loaded = {"baseline": adapter.load(baseline, device), "candidate": adapter.load(candidate, device)}
            rendered = {}
            for cid in targets:
                try:
                    rendered[cid] = render_case(cfg, bundle, cid, run_dir_planned, device=device, models=loaded)
                except RBError as exc:
                    if exc.code == "E_EVIDENCE_DEPS":
                        if progress:
                            progress(f"evidence skipped: {exc.message}")
                        break
                    raise
                except Exception as exc:  # noqa: BLE001  (a rendering failure must not lose the run; the numbers are already computed)
                    if progress:
                        progress(f"evidence for {cid} failed ({type(exc).__name__}: {str(exc)[:120]}); the run continues")
                    continue
                if progress:
                    progress(f"evidence: {cid} → {rendered[cid]['dir']}")
            del loaded
            if rendered:
                by_id = {c.id: c for c in bundle.cases}
                for cid, info in rendered.items():
                    by_id[cid].evidence = Evidence(dir=str(Path(info["dir"]).relative_to(run_dir_planned)), files=info["files"])
                record.evidence = {"level": evidence_level, "cases": list(rendered), "checks": {cid: info["check"] for cid, info in rendered.items()}}
                findings = compute_findings(bundle, limits, checks)
    md = report_markdown(bundle, record, findings, checks.checks if checks else ())
    if getattr(adapter, "synthetic", False):
        md = md.replace("\n\n## Verdict", "\n\n**Synthetic adapter: a test double, not a real model. The numbers are illustrative.**\n\n## Verdict", 1)
    run_dir = write_run(runs_dir, bundle, record, findings, md)
    return run_dir, bundle, record, findings, md


# ---------------------------------------------------------------- verify-hook and doctor

def verify_hook(cfg: Config, checkpoint: Optional[Path], device: Optional[str] = None, case_id: Optional[str] = None) -> dict:
    device = device or cfg.adapter.device
    adapter = load_adapter(cfg)
    expected = adapter.expected_iterations()
    if checkpoint is not None:
        model = adapter.load(checkpoint, device)
    elif hasattr(adapter, "new_model"):
        model = adapter.new_model(device)
    else:
        raise RBError("E_CHECKPOINT_NOT_FOUND", message="rb verify-hook needs --checkpoint for this adapter.")
    case: Optional[Case] = None
    try:
        for c in adapter.cases():
            if case_id is None or c.id == case_id:
                case = c
                break
    except RBError:
        case = None
    if case is None and hasattr(adapter, "sample_inputs"):
        case = Case(id="sample", name="synthetic sample input (no dataset found)", inputs=adapter.sample_inputs(), gt=None)
    if case is None:
        raise RBError("E_DATASET_EMPTY", message=f"No case to run{f' with id {case_id}' if case_id else ''}.")
    rec = TrajectoryRecorder(scale=float(getattr(adapter, "trajectory_scale", 1.0) or 1.0))
    t0 = time.time()
    adapter.infer(model, case, rec)
    values = list(rec.values)
    problems: list[str] = []
    if len(values) == 0:
        problems.append("The recorder never fired: the update loop is not instrumented (E_HOOK_NOT_REACHABLE).")
    elif expected is not None and len(values) != expected:
        problems.append(f"Recorded {len(values)} values, expected {expected} (E_HOOK_LENGTH): the hook must fire once per iteration.")
    if any(not (v == v and abs(v) != float('inf')) for v in values):
        problems.append("Some recorded values are not finite.")
    if len(values) == 1:
        problems.append("A trajectory needs at least 2 iterations.")
    return {"ok": not problems, "case": case.id, "fired": len(values), "expected": expected, "values": values, "seconds": round(time.time() - t0, 3), "problems": problems,
            "adapter": adapter.describe(), "device": device}


def doctor(cfg: Optional[Config], checkpoints: list[Path], device: Optional[str] = None) -> list[dict]:
    checks: list[dict] = []

    def add(name: str, ok: Optional[bool], detail: str, fix: Optional[str] = None) -> None:
        checks.append({"check": name, "status": "ok" if ok else ("warn" if ok is None else "fail"), "detail": detail, **({"fix": fix} if fix and not ok else {})})

    add("python", True, platform.python_version())
    add("rb", True, __version__)
    if cfg is None:
        add("config", False, "no rb.toml in the current directory", "rb init --project <name> --adapter raft --model-code ./raft --dataset <path>")
        return checks
    add("config", True, f"project '{cfg.project.name}', task {cfg.project.task}, adapter {cfg.adapter.id or cfg.adapter.module}")
    device = device or cfg.adapter.device
    try:
        import torch
        cuda = torch.cuda.is_available()
        add("torch", True, f"{torch.__version__} (cuda {'available' if cuda else 'not available'})")
        if device.startswith("cuda") and not cuda:
            add("device", False, f"device '{device}' requested but CUDA is not available", "set [adapter] device = \"cpu\" or run on a GPU machine")
        else:
            add("device", True, device)
    except ImportError:
        add("torch", None if cfg.adapter.id == "synthetic" else False, "not importable", None if cfg.adapter.id == "synthetic" else "install torch in this environment (the model's own requirements)")
    try:
        adapter = load_adapter(cfg)
        add("adapter", True, str(adapter.describe()))
    except RBError as exc:
        add("adapter", False, exc.message or exc.code, exc.fix)
        return checks
    if cfg.adapter.model_code:
        p = Path(cfg.adapter.model_code)
        add("model_code", p.exists(), str(p) + ("" if p.exists() else " (missing)"), "point [adapter] model_code at the model repository checkout")
    try:
        cases = list(adapter.cases())
        add("dataset", bool(cases), f"{len(cases)} cases from {cfg.dataset.path or cfg.dataset.name}" + (f"; {sum(1 for c in cases if c.gt is not None)} with ground truth" if cases else ""), "check [dataset] path")
    except RBError as exc:
        add("dataset", False, exc.message or exc.code, exc.fix)
    for ck in checkpoints:
        add(f"checkpoint {ck.name}", ck.exists(), str(ck) + ("" if ck.exists() else " (missing)"), "check the path")
    add("hook", None, "not verified yet", "rb verify-hook --checkpoint <path>")
    return checks
