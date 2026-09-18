"""The `rb` command line. Every command accepts --json and prints exactly one JSON object on stdout; logs go to stderr."""
from __future__ import annotations

import argparse
import json
import sys
import traceback
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from pydantic import ValidationError

from . import __version__
from .checks import (DEFAULT_CHECKS_PATH, default_check, describe_check, evaluate_all, evaluate_check, load_checks, save_checks, upsert_check)
from .config import CONFIG_NAME, GITIGNORE_SNIPPET, AdapterSection, Config, DatasetSection, EvidenceSection, ProjectSection, load_config, render_config
from .errors import EXIT_CHECK_FAILED, EXIT_ENVIRONMENT, EXIT_INVALID, EXIT_OK, ERRORS, RBError
from .example import MINIMAL_EXAMPLE, example_comparison
from .fmt import pct, plain, signed, to_fixed
from .importer import csv_to_comparison, load_comparison
from .models import SCHEMAS, Bundle, ChecksV2, Envelope, Findings, Limits
from .report import report_markdown
from .runs import (bundle_from_comparison, compute_findings, import_record, list_runs, new_run_id, rederive, resolve_run, runs_dir, write_run)
from .stability import case_stability
from . import runner as runner_mod

PLANNED = {"rerun", "open", "serve", "mcp"}
FILTERS = ["flagged", "all", "regressions", "unstable", "improved-unstable", "settled-regressions", "improved", "stable"]
SORTS = ["priority", "error-change", "late-share", "name"]

# ---------------------------------------------------------------- output


class Out:
    """Collects human lines or a JSON envelope; prints one or the other at the end."""

    def __init__(self, command: str, json_mode: bool) -> None:
        self.command, self.json_mode = command, json_mode
        self.lines: list[str] = []
        self.data: dict = {}
        self.next: list[str] = []
        self.run_id: Optional[str] = None
        self.ref: Optional[str] = None  # how the user named the run (id, directory or file); reused in `next`
        self.warnings: list[str] = []
        self.command_line: str = "rb"  # the exact invocation, recorded in record.json

    def say(self, *text: str) -> None:
        self.lines.extend(text)

    def warn(self, text: str) -> None:
        self.warnings.append(text)
        print(f"rb {self.command}: {text}", file=sys.stderr)

    def emit(self, ok: bool = True, errors: Optional[list[dict]] = None) -> None:
        if self.json_mode:
            data = dict(self.data)
            if self.warnings:
                data["warnings"] = list(self.warnings)
            env = Envelope(ok=ok, rb_version=__version__, command=self.command, run_id=self.run_id, data=data, errors=errors or [], next=self.next)
            print(json.dumps(env.model_dump(exclude_none=False), indent=1))
        else:
            if self.lines:
                print("\n".join(self.lines))
            if self.next:
                print("\nNext: " + "  ·  ".join(self.next))


def limits_from(args: argparse.Namespace, base: Optional[Limits] = None) -> Limits:
    base = base or Limits()
    try:
        return Limits(
            max_regression=base.max_regression if args.max_regression is None else args.max_regression,
            max_late_share=base.max_late_share if args.max_late_share is None else args.max_late_share,
            max_reversals=base.max_reversals if args.max_reversals is None else args.max_reversals,
            max_last_update=base.max_last_update if getattr(args, "max_last_update", None) is None else args.max_last_update,
        )
    except ValidationError as exc:
        raise RBError("E_LIMITS_INVALID", message=f"Limit out of range: {exc.errors()[0].get('msg', '')}")


def checks_for(args: argparse.Namespace, project: str, out: Optional["Out"] = None, required: bool = False) -> Optional[ChecksV2]:
    """The checks file that applies to this project: named with --checks, or checks.json in the current directory.
    An unnamed file for another project is ignored with a warning; a named one is an error."""
    explicit = bool(getattr(args, "checks", None))
    path = Path(args.checks) if explicit else DEFAULT_CHECKS_PATH
    if not path.exists():
        if required and explicit:
            raise RBError("E_FILE_NOT_FOUND", message=f"No such checks file: {path}")
        return None
    loaded = load_checks(path, project, strict=explicit)
    if not explicit and not loaded.checks and out is not None:
        out.warn(f"{path} holds no checks for project '{project}'; ignored. Pass --checks to use another file.")
    return loaded


# ---------------------------------------------------------------- commands


def cmd_import(args: argparse.Namespace, out: Out) -> int:
    started = datetime.now().astimezone()
    path = Path(args.file)
    if path.suffix.lower() == ".csv":
        cmp = csv_to_comparison(path, project=args.project or "", baseline=args.baseline_name or "", candidate=args.candidate_name or "", dataset=args.dataset or "", metric=args.metric or "", unit=args.unit or "")
        fmt = "csv"
    else:
        cmp = load_comparison(path)
        fmt = "json-v1"
        if args.project:
            cmp.project = args.project
    return _create_run(cmp, args, out, started, path, fmt)


def cmd_example(args: argparse.Namespace, out: Out) -> int:
    cmp = example_comparison()
    if args.write:
        target = Path(args.write)
        target.write_text(json.dumps(cmp.model_dump(exclude_none=True), indent=2) + "\n", encoding="utf-8")
        out.say(f"Wrote the example comparison (version 1, {len(cmp.cases)} cases) to {target}.")
        out.data = {"path": str(target), "cases": len(cmp.cases)}
        out.next = [f"rb import {target}", f"rb findings {target}"]
        return EXIT_OK
    return _create_run(cmp, args, out, datetime.now().astimezone(), None, "example")


def _create_run(cmp, args, out: Out, started, path: Optional[Path], fmt: str) -> int:
    base = runs_dir(args.runs_dir)
    limits = limits_from(args)
    run_id = new_run_id(cmp.candidate, base)
    bundle = bundle_from_comparison(cmp, run_id, limits, "example" if cmp.source == "example" else "imported")
    checks = checks_for(args, bundle.project, out)
    findings = compute_findings(bundle, limits, checks)
    record = import_record(bundle, cmp, path, fmt, started, out.command_line)
    md = report_markdown(bundle, record, findings, checks.checks if checks else ())
    run_dir = write_run(base, bundle, record, findings, md)
    out.run_id = run_id
    s = findings.summary
    out.say(
        f"{'Example data' if cmp.source == 'example' else 'Imported'}: {cmp.project} · {cmp.baseline} → {cmp.candidate} · {bundle.metric.name} ({bundle.metric.unit}), lower is better",
        f"{s.cases} cases · mean {to_fixed(s.mean_error.baseline)} → {to_fixed(s.mean_error.candidate)} {bundle.metric.unit} · {s.regressions} regressions · {s.unstable} unstable ({s.improved_unstable} pass on error) · {s.with_trajectories} with trajectories",
        f"Verdict: {findings.verdict.line}",
        f"Run written to {run_dir}/ (bundle.json, record.json, findings.json, report.md)",
    )
    if cmp.source == "example":
        out.say("This is illustrative data with intentional regressions and unstable cases. It is not evidence of any model's performance.")
    out.data = {"run_dir": str(run_dir), "summary": s.model_dump(), "verdict": findings.verdict.model_dump(), "limits": limits.model_dump(), "metric": bundle.metric.model_dump()}
    out.next = [f"rb findings {run_id} --top 5", f"rb case {run_id} {findings.verdict.start}" if findings.verdict.start else f"rb report {run_id} --print"]
    return EXIT_OK


def _load(args: argparse.Namespace, out: Out) -> tuple[Bundle, Optional[Path], Limits]:
    bundle, run_dir = resolve_run(args.run, runs_dir(args.runs_dir))
    limits = limits_from(args, bundle.limits)
    if limits != bundle.limits:
        bundle = rederive(bundle, limits)
    out.run_id = bundle.run_id
    out.ref = bundle.run_id if run_dir and run_dir.parent == runs_dir(args.runs_dir) else args.run
    return bundle, run_dir, limits


def _select(queue, flt: str):
    if flt == "all":
        return queue
    if flt == "flagged":
        return [q for q in queue if "regression" in q.flags or "unstable" in q.flags]
    if flt == "regressions":
        return [q for q in queue if "regression" in q.flags]
    if flt == "unstable":
        return [q for q in queue if "unstable" in q.flags]
    if flt == "improved-unstable":
        return [q for q in queue if "improved_unstable" in q.flags]
    if flt == "settled-regressions":
        return [q for q in queue if "settled_regression" in q.flags]
    if flt == "improved":
        return [q for q in queue if q.error_outcome == "improved"]
    if flt == "stable":
        return [q for q in queue if q.error_outcome == "stable"]
    return queue


def _sorted(items, sort: str):
    if sort == "error-change":
        return sorted(items, key=lambda q: -q.error_change)
    if sort == "late-share":
        return sorted(items, key=lambda q: -(q.candidate_late_share if q.candidate_late_share is not None else -1))
    if sort == "name":
        return sorted(items, key=lambda q: q.name.lower())
    return items


def cmd_findings(args: argparse.Namespace, out: Out) -> int:
    bundle, _, limits = _load(args, out)
    checks = checks_for(args, bundle.project, out)
    findings = compute_findings(bundle, limits, checks)
    shown = _sorted(_select(findings.queue, args.filter), args.sort)
    if args.top:
        shown = shown[: args.top]
    unit = bundle.metric.unit
    s = findings.summary
    out.say(
        f"{bundle.project} · {bundle.baseline.name} → {bundle.candidate.name} · {bundle.metric.name} ({unit}), lower is better",
        f"Limits: +{plain(limits.max_regression)} {unit} · {pct(limits.max_late_share)} late · {limits.max_reversals} reversals",
        f"Verdict: {findings.verdict.line}",
        f"{s.cases} cases · {s.regressions} regressions · {s.unstable} unstable ({s.improved_unstable} pass on error) · {s.settled_regressions} settled regressions · {s.flagged} flagged"
        + (f" · checks {s.checks.passing} pass / {s.checks.failing} fail / {s.checks.missing} missing" if s.checks.saved else ""),
        "",
        f"{'#':>3}  {'case':<24} {'change':>9}  {'late':>5} {'rev':>3}  flags",
    )
    for q in shown:
        late = pct(q.candidate_late_share) if q.candidate_late_share is not None else "n/a"
        rev = str(q.candidate_reversals) if q.candidate_reversals is not None else "-"
        change = signed(q.error_change) if q.error_change is not None else "n/a"
        out.say(f"{q.rank:>3}  {q.id:<24} {change:>7} {unit:<2} {late:>5} {rev:>3}  {', '.join(q.flags) or '-'}")
    out.say("", f"{len(shown)} of {s.cases} cases shown (filter: {args.filter}, sort: {args.sort}).")
    out.data = {**findings.model_dump(exclude={"queue"}), "queue": [q.model_dump() for q in shown], "shown": len(shown), "filter": args.filter, "sort": args.sort}
    ref = out.ref or bundle.run_id
    if shown:
        out.next = [f"rb case {ref} {shown[0].id}", f"rb check save {ref} {shown[0].id}", f"rb report {ref}"]
    else:
        out.next = [f"rb findings {ref} --filter all", f"rb report {ref}"]
    return EXIT_OK


def cmd_case(args: argparse.Namespace, out: Out) -> int:
    bundle, _, limits = _load(args, out)
    findings = compute_findings(bundle, limits, checks_for(args, bundle.project, out))
    q = next((q for q in findings.queue if q.id == args.case_id), None)
    if q is None:
        raise RBError("E_CASE_NOT_FOUND", message=f"Case '{args.case_id}' is not in run {bundle.run_id}.")
    c = next(c for c in bundle.cases if c.id == q.id)
    unit = bundle.metric.unit
    st = case_stability(c)
    out.say(
        f"{q.name} ({q.id}) · rank {q.rank} of {findings.summary.cases} · {', '.join(q.flags) or 'no flags'}",
        (f"Current {to_fixed(q.baseline_error)} {unit} → candidate {to_fixed(q.candidate_error)} {unit} ({signed(q.error_change)} {unit})" if q.error_change is not None else "Error not measured (no ground truth)")
        + f" · error: {q.error_outcome.replace('_', ' ')} · stability: {q.stability_outcome.replace('_', ' ')}",
    )
    def traj_line(label: str, t) -> str:
        line = f"{label}: {t.iterations} iterations · late share {pct(t.late_share)} · {t.reversals} reversals"
        if t.last_update is not None:
            line += f" · last update {t.last_update:.3f} {unit} (first quarter {t.early_update:.3f}, last quarter {t.late_update:.3f})"
        if t.sign_reversal_rate is not None:
            line += f" · direction reversals {pct(t.sign_reversal_rate)} · distance from final estimate mean {t.displacement_mean:.3f} max {t.displacement_max:.3f} {unit}"
        return line

    if st.candidate:
        out.say(traj_line("Candidate trajectory", st.candidate))
    if st.baseline:
        out.say(traj_line("Current-model trajectory", st.baseline))
    if c.candidate_frames:
        peak = max(range(len(c.candidate_frames)), key=lambda i: c.candidate_frames[i])
        out.say(f"Per-frame error: {len(c.candidate_frames)} frames · candidate peak {to_fixed(c.candidate_frames[peak])} {unit} at frame {peak + 1}")
    out.say("", q.why)
    if q.notes:
        out.say(f"Notes: {q.notes}")
    if q.tags:
        out.say(f"Tags: {', '.join(q.tags)}")
    out.data = {**q.model_dump(), "stability": st.model_dump(), "baseline_trajectory": c.baseline_trajectory, "candidate_trajectory": c.candidate_trajectory,
                "baseline_frames": c.baseline_frames, "candidate_frames": c.candidate_frames, "limits": limits.model_dump(), "metric": bundle.metric.model_dump()}
    ref = out.ref or bundle.run_id
    out.next = [f"rb check save {ref} {q.id}", f"rb findings {ref}"]
    return EXIT_OK


def cmd_check_save(args: argparse.Namespace, out: Out) -> int:
    bundle, _, limits = _load(args, out)
    path = Path(args.checks) if args.checks else DEFAULT_CHECKS_PATH
    checks = load_checks(path, bundle.project)
    if not checks.project:
        checks = ChecksV2(version=2, project=bundle.project, checks=checks.checks)
    new = default_check(bundle, args.case_id, max_error=args.max_error, max_late_share=args.max_late_share, max_reversals=args.max_reversals, require_settled=not args.no_settled, note=args.note or "")
    checks = upsert_check(checks, new)
    save_checks(path, checks)
    unit = bundle.metric.unit
    now = evaluate_check(new, bundle, unit)
    out.say(f"Saved check for {new.case_id} ({new.name}) in {path}: {describe_check(new, unit)}.")
    if now.status == "passing":
        out.say("Passes now. It will fail on a future candidate that exceeds these limits.")
    else:
        case = next(c for c in bundle.cases if c.id == new.case_id)
        out.say(f"Fails now: {now.reason} It passes once the case is fixed.")
        if case.candidate_error > new.max_error:
            out.say(f"To accept the candidate's current level instead: rb check save {out.ref or bundle.run_id} {new.case_id} --max-error {to_fixed(case.candidate_error + limits.max_regression)}")
    out.data = {"checks_file": str(path), "check": new.model_dump(exclude_none=True), "status_now": now.model_dump(), "total_checks": len(checks.checks)}
    out.next = [f"rb check run {out.ref or bundle.run_id} --checks {path}", f"rb check list --checks {path}"]
    return EXIT_OK


def cmd_check_run(args: argparse.Namespace, out: Out) -> int:
    bundle, _, limits = _load(args, out)
    unit = bundle.metric.unit
    checks = checks_for(args, bundle.project, out, required=True)
    findings = compute_findings(bundle, limits, checks)
    queue = findings.queue if not args.case_id else [q for q in findings.queue if q.id == args.case_id]
    if args.case_id and not queue:
        raise RBError("E_CASE_NOT_FOUND", message=f"Case '{args.case_id}' is not in run {bundle.run_id}.")
    results = evaluate_all(checks, bundle, unit, args.case_id) if checks else []
    flagged = [q for q in queue if "regression" in q.flags or "unstable" in q.flags]
    flags_failed = bool(flagged)
    checks_failed = any(r.status != "passing" for r in results)
    failed = {"any": flags_failed or checks_failed, "checks": checks_failed, "flags": flags_failed}[args.fail_on]
    if bundle.source == "example":
        out.say("EXAMPLE DATA - illustrative metrics, no inference was run.")
    out.say(f"{bundle.project} | {bundle.baseline.name} -> {bundle.candidate.name} | {bundle.metric.name} ({unit}), lower is better")
    for q in queue:
        status = "REGRESSION" if "regression" in q.flags else "OK"
        numbers = f"{q.baseline_error:.2f} -> {q.candidate_error:.2f} {unit} ({q.error_change:+.2f})" if q.error_change is not None else "error not measured (no ground truth)"
        line = f"{status:12} {q.id:24} {numbers}"
        if q.candidate_late_share is not None:
            line += f"  | {'UNSTABLE' if 'unstable' in q.flags else 'settled':8} late {q.candidate_late_share * 100:4.0f}%  reversals {q.candidate_reversals}"
        out.say(line)
    for r in results:
        tag = {"passing": "PASS", "failing": "FAIL", "missing": "MISSING", "other-project": "SKIP"}[r.status]
        limits_text = f"max {r.max_error:.2f} {unit}" + (f", late <= {r.max_late_share * 100:.0f}%" if r.max_late_share is not None else "") + (f", reversals <= {r.max_reversals}" if r.max_reversals is not None else "")
        out.say(f"CHECK {tag:7} {r.case_id} | {limits_text}" + ("" if r.status == "passing" else f" | {r.reason}"))
    if checks is None:
        out.say("No checks file found (checks.json); only the regression and stability limits were applied.")
    out.say("CHECKS NEED ATTENTION" if failed else "CHECKS PASSED")
    out.data = {"failed": failed, "fail_on": args.fail_on, "flagged": [q.id for q in flagged], "checks": [r.model_dump() for r in results],
                "summary": findings.summary.model_dump(), "verdict": findings.verdict.model_dump(), "limits": limits.model_dump()}
    ref = out.ref or bundle.run_id
    out.next = [f"rb case {ref} {flagged[0].id}" if flagged else f"rb report {ref}"]
    return EXIT_CHECK_FAILED if failed else EXIT_OK


def cmd_check_list(args: argparse.Namespace, out: Out) -> int:
    path = Path(args.checks) if args.checks else DEFAULT_CHECKS_PATH
    checks = load_checks(path, args.project)
    if not checks.checks:
        out.say(f"No saved checks in {path}.")
    else:
        out.say(f"{len(checks.checks)} saved check{'' if len(checks.checks) == 1 else 's'} for '{checks.project}' in {path}:")
        for c in checks.checks:
            out.say(f"- {c.case_id}{f' ({c.name})' if c.name else ''}: {describe_check(c, args.unit or 'units')}{f' · from {c.from_run}' if c.from_run else ''}{f' · {c.note}' if c.note else ''}")
    out.data = checks.model_dump(exclude_none=True)
    return EXIT_OK


def cmd_check_rm(args: argparse.Namespace, out: Out) -> int:
    path = Path(args.checks) if args.checks else DEFAULT_CHECKS_PATH
    checks = load_checks(path)
    before = len(checks.checks)
    checks = ChecksV2(version=2, project=checks.project, checks=[c for c in checks.checks if c.case_id != args.case_id])
    if len(checks.checks) == before:
        raise RBError("E_CASE_NOT_FOUND", message=f"No saved check for '{args.case_id}' in {path}.")
    save_checks(path, checks)
    out.say(f"Removed the check for {args.case_id} from {path}. {len(checks.checks)} remain.")
    out.data = {"removed": args.case_id, "remaining": len(checks.checks)}
    return EXIT_OK


def cmd_report(args: argparse.Namespace, out: Out) -> int:
    bundle, run_dir, limits = _load(args, out)
    checks = checks_for(args, bundle.project, out)
    findings = compute_findings(bundle, limits, checks)
    record = None
    if run_dir and (run_dir / "record.json").exists():
        from .models import Record
        try:
            record = Record.model_validate_json((run_dir / "record.json").read_text(encoding="utf-8"))
        except ValidationError:
            record = None
    md = report_markdown(bundle, record, findings, checks.checks if checks else ())
    target = Path(args.out) if args.out else (run_dir / "report.md" if run_dir else None)
    if target:
        try:
            target.write_text(md, encoding="utf-8")
        except OSError as exc:
            raise RBError("E_WRITE_FAILED", message=f"Could not write {target}: {exc}")
        out.say(f"Report written to {target}")
    if args.print or not target:
        out.say("", md)
    out.data = {"path": str(target) if target else None, "markdown": md, "verdict": findings.verdict.model_dump()}
    out.next = [f"rb findings {out.ref or bundle.run_id}"]
    return EXIT_OK


# ---------------------------------------------------------------- runner commands (0.2)


def cmd_init(args: argparse.Namespace, out: Out) -> int:
    path = Path(CONFIG_NAME)
    if path.exists() and not args.force:
        raise RBError("E_CONFIG_INVALID", message=f"{path} already exists; pass --force to overwrite it.")
    if args.demo:
        from .adapters.synthetic import DEMO_CHECKPOINTS
        ckpt_dir = Path("ckpt")
        ckpt_dir.mkdir(exist_ok=True)
        for name, body in DEMO_CHECKPOINTS.items():
            (ckpt_dir / name).write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
        cfg = Config(project=ProjectSection(name=args.project or "synthetic-demo", task="flow"),
                     adapter=AdapterSection(id="synthetic", iterations=args.iterations or 12, device="cpu"),
                     dataset=DatasetSection(name="synthetic-24", kind="synthetic", cases="all"))
    else:
        if not args.project:
            raise RBError("E_CONFIG_INVALID", message="rb init needs --project <name> (or --demo).")
        adapter_id = args.adapter or "raft"
        cfg = Config(project=ProjectSection(name=args.project, task=args.task or "flow"),
                     adapter=AdapterSection(id=adapter_id if ":" not in adapter_id else None, module=adapter_id if ":" in adapter_id else None,
                                            model_code=args.model_code or ("./raft" if adapter_id == "raft" else None), iterations=args.iterations or 12,
                                            device=args.device or "cuda", small=bool(args.small)),
                     dataset=DatasetSection(name=args.dataset_name or (Path(args.dataset).name if args.dataset else "cases"), kind=args.kind or "kitti", path=args.dataset, cases="all"))
    text = render_config(cfg)
    path.write_text(text, encoding="utf-8")
    gi = Path(".gitignore")
    if "rb-runs/*/evidence/" not in (gi.read_text(encoding="utf-8") if gi.exists() else ""):
        with gi.open("a", encoding="utf-8") as f:
            f.write(("\n" if gi.exists() and gi.stat().st_size else "") + GITIGNORE_SNIPPET)
    out.say(f"Wrote {path} for project '{cfg.project.name}' (adapter {cfg.adapter.id or cfg.adapter.module}).", "Added rb-runs/*/evidence/ to .gitignore.")
    if args.demo:
        out.say("Demo checkpoints: ckpt/synth-current.json and ckpt/synth-candidate.json (synthetic, not a real model).")
        out.next = ["rb doctor", "rb run --baseline ckpt/synth-current.json --candidate ckpt/synth-candidate.json"]
    else:
        out.next = ["rb doctor", "rb verify-hook --checkpoint <path>", "rb run --baseline <ckpt-A> --candidate <ckpt-B>"]
    out.data = {"config_path": str(path), "config": cfg.model_dump(exclude_none=True), "text": text}
    return EXIT_OK


def cmd_doctor(args: argparse.Namespace, out: Out) -> int:
    cfg = load_config() if Path(CONFIG_NAME).exists() else None
    checks = runner_mod.doctor(cfg, [Path(c) for c in (args.checkpoint or [])], args.device)
    failed = [c for c in checks if c["status"] == "fail"]
    for c in checks:
        mark = {"ok": "ok  ", "warn": "warn", "fail": "FAIL"}[c["status"]]
        out.say(f"{mark}  {c['check']:<22} {c['detail']}" + (f"\n      → {c['fix']}" if c.get("fix") else ""))
    out.say("", "Environment problems found." if failed else "Ready to run." if cfg else "Not configured.")
    out.data = {"checks": checks, "ok": not failed}
    out.next = ["rb verify-hook --checkpoint <path>", "rb run --baseline <ckpt-A> --candidate <ckpt-B>"] if cfg and not failed else ["rb init --project <name> --adapter raft --model-code ./raft --dataset <path>"] if not cfg else []
    return EXIT_ENVIRONMENT if failed else EXIT_OK


def cmd_verify_hook(args: argparse.Namespace, out: Out) -> int:
    cfg = load_config()
    result = runner_mod.verify_hook(cfg, Path(args.checkpoint) if args.checkpoint else None, args.device, args.case_id)
    vals = ", ".join(f"{v:.4g}" for v in result["values"][:16]) + (" …" if len(result["values"]) > 16 else "")
    out.say(f"Case {result['case']} on {result['device']}: recorder fired {result['fired']} time(s)" + (f", expected {result['expected']}" if result["expected"] else "") + f" · {result['seconds']}s",
            f"Values: [{vals}]" if result["values"] else "Values: none")
    if result["ok"]:
        out.say("Hook verified: one value per refinement iteration.")
    for pr in result["problems"]:
        out.say(f"Problem: {pr}")
    out.data = result
    out.next = ["rb run --baseline <ckpt-A> --candidate <ckpt-B>"] if result["ok"] else ["rb docs"]
    if not result["ok"]:
        code = "E_HOOK_NOT_REACHABLE" if result["fired"] == 0 else "E_HOOK_LENGTH"
        raise RBError(code, message=result["problems"][0])
    return EXIT_OK


def cmd_run(args: argparse.Namespace, out: Out) -> int:
    cfg = load_config()
    limits = limits_from(args, cfg.limits)
    base = runs_dir(args.runs_dir)
    checks = checks_for(args, cfg.project.name, out)
    quiet = getattr(args, "quiet", False)
    progress = (lambda msg: print(f"rb run: {msg}", file=sys.stderr)) if not quiet else None
    run_dir, bundle, record, findings, _ = runner_mod.run(
        cfg, Path(args.baseline), Path(args.candidate), limits=limits, runs_dir=base, command=out.command_line,
        device=args.device, seed=args.seed, no_trajectories=args.no_trajectories, limit=args.limit,
        baseline_name=args.baseline_name, candidate_name=args.candidate_name, checks=checks, progress=progress,
    )
    out.run_id = bundle.run_id
    s = findings.summary
    unit = bundle.metric.unit
    if getattr(record, "notes", "") and "Synthetic" in record.notes:
        out.say("SYNTHETIC ADAPTER: a test double, not a real model. The numbers are illustrative.")
    out.say(
        f"Run: {cfg.project.name} · {bundle.baseline.name} → {bundle.candidate.name} · {bundle.metric.name} ({unit}), lower is better · {record.wall_seconds}s",
        f"{s.cases} cases ({s.with_gt} with ground truth) · mean {to_fixed(s.mean_error.baseline)} → {to_fixed(s.mean_error.candidate)} {unit} · {s.regressions} regressions · {s.unstable} unstable ({s.improved_unstable} pass on error)",
        f"Trajectories: {record.hook.get('status')}. {record.hook.get('note', '')}",
        f"Verdict: {findings.verdict.line}",
        f"Run written to {run_dir}/ (bundle.json, record.json, findings.json, report.md)",
    )
    skipped = getattr(record, "skipped", None) or {}
    if skipped:
        out.say(f"{len(skipped)} case(s) skipped after inference errors: {', '.join(list(skipped)[:5])}{' …' if len(skipped) > 5 else ''}")
    flagged = [q for q in findings.queue if "regression" in q.flags or "unstable" in q.flags]
    failed = {"none": False, "regressions": s.regressions > 0, "flags": bool(flagged), "checks": s.checks.failing + s.checks.missing > 0}[args.fail_on]
    out.data = {"run_dir": str(run_dir), "summary": s.model_dump(), "verdict": findings.verdict.model_dump(), "limits": limits.model_dump(), "metric": bundle.metric.model_dump(),
                "hook": record.hook, "checkpoints": {k: v.model_dump() for k, v in record.checkpoints.items()}, "skipped": skipped, "fail_on": args.fail_on, "failed": failed}
    out.next = [f"rb findings {bundle.run_id} --top 5", f"rb case {bundle.run_id} {findings.verdict.start}" if findings.verdict.start else f"rb report {bundle.run_id} --print"]
    return EXIT_CHECK_FAILED if failed else EXIT_OK


def docs_text() -> str:
    here = Path(__file__).resolve().parent
    for candidate in (here / "docs" / "AGENTS.md", here.parent.parent / "AGENTS.md"):
        if candidate.exists():
            return candidate.read_text(encoding="utf-8")
    return "AGENTS.md is not bundled with this build. See https://github.com/rabbit-brain/rb/blob/main/AGENTS.md\n"


def cmd_docs(args: argparse.Namespace, out: Out) -> int:
    if args.errors:
        lines = [f"{code}: {meaning} → {fix}" for code, (meaning, fix) in ERRORS.items()]
        out.say(*lines)
        out.data = {"errors": {code: {"meaning": m, "fix": f} for code, (m, f) in ERRORS.items()}}
        return EXIT_OK
    text = docs_text()
    out.say(text.rstrip("\n"))
    out.data = {"markdown": text}
    return EXIT_OK


def cmd_schema(args: argparse.Namespace, out: Out) -> int:
    if args.name == "example":
        out.say(MINIMAL_EXAMPLE)
        out.data = json.loads(MINIMAL_EXAMPLE)
        return EXIT_OK
    if args.name == "full-example":
        cmp = example_comparison().model_dump(exclude_none=True)
        out.say(json.dumps(cmp, indent=2))
        out.data = cmp
        return EXIT_OK
    schema = SCHEMAS[args.name].model_json_schema()
    out.say(json.dumps(schema, indent=2))
    out.data = schema
    return EXIT_OK


def cmd_version(args: argparse.Namespace, out: Out) -> int:
    out.say(f"rabbit-brain {__version__}")
    out.data = {"version": __version__}
    return EXIT_OK


def cmd_runs(args: argparse.Namespace, out: Out) -> int:
    base = runs_dir(args.runs_dir)
    ids = list_runs(base)
    out.say(*(ids or [f"No runs under {base}/."]))
    out.data = {"runs_dir": str(base), "runs": ids}
    if ids:
        out.next = [f"rb findings {ids[-1]}"]
    return EXIT_OK


# ---------------------------------------------------------------- parser


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", help="print one JSON object (the envelope) instead of text")
    common.add_argument("--runs-dir", default=None, help="where runs live (default rb-runs/, or $RB_RUNS_DIR)")
    lim = argparse.ArgumentParser(add_help=False)
    lim.add_argument("--max-regression", type=float, default=None, help="allowed error increase over the current model, in the metric's unit (default 0.3)")
    lim.add_argument("--max-late-share", type=float, default=None, help="allowed share of refinement in the last third of iterations (default 0.25)")
    lim.add_argument("--max-reversals", type=int, default=None, help="allowed number of iterations where the update grew (default 2)")
    lim.add_argument("--max-last-update", type=float, default=None, help="allowed size of the final update, in the trajectory's unit (off unless set)")
    chk = argparse.ArgumentParser(add_help=False)
    chk.add_argument("--checks", default=None, help="saved checks file (default checks.json in the current directory)")

    p = argparse.ArgumentParser(prog="rb", description="Rabbit Brain: release review for iterative perception models. Docs for agents and humans: rb docs.")
    p.add_argument("--version", action="version", version=f"rabbit-brain {__version__}")
    sub = p.add_subparsers(dest="command", metavar="<command>")

    s = sub.add_parser("import", parents=[common, lim, chk], help="import a version-1 results JSON or a metrics CSV into a new run")
    s.add_argument("file")
    s.add_argument("--project", help="project name (overrides the file's; required for CSV)")
    s.add_argument("--baseline-name", help="current model's name (CSV)")
    s.add_argument("--candidate-name", help="candidate model's name (CSV)")
    s.add_argument("--dataset", help="evaluation set's name (CSV)")
    s.add_argument("--metric", help="error metric name, e.g. mean_endpoint_error (CSV)")
    s.add_argument("--unit", help="metric unit, e.g. px or cm (CSV)")
    s.set_defaults(func=cmd_import)

    s = sub.add_parser("example", parents=[common, lim, chk], help="create a run from the built-in example data, or write it as a version-1 JSON")
    s.add_argument("--write", metavar="PATH", help="write the example comparison JSON here instead of creating a run")
    s.set_defaults(func=cmd_example)

    s = sub.add_parser("findings", parents=[common, lim, chk], help="the ranked queue, summary and verdict for a run")
    s.add_argument("run", help="run id, run directory, bundle.json, or a version-1 comparison JSON")
    s.add_argument("--filter", choices=FILTERS, default="flagged")
    s.add_argument("--top", type=int, default=None, help="show at most N cases")
    s.add_argument("--sort", choices=SORTS, default="priority")
    s.set_defaults(func=cmd_findings)

    s = sub.add_parser("case", parents=[common, lim, chk], help="one case: numbers, trajectory statistics, reasoning")
    s.add_argument("run")
    s.add_argument("case_id")
    s.set_defaults(func=cmd_case)

    c = sub.add_parser("check", help="saved checks: save, run, list, rm")
    csub = c.add_subparsers(dest="check_command", metavar="<save|run|list|rm>")
    s = csub.add_parser("save", parents=[common, lim, chk], help="keep a case for the next checkpoint (default limit: current error + max regression)")
    s.add_argument("run")
    s.add_argument("case_id")
    s.add_argument("--max-error", type=float, default=None, help="absolute candidate error limit (default: current model's error + max regression)")
    s.add_argument("--no-settled", action="store_true", help="do not require a settled trajectory")
    s.add_argument("--note", default="")
    s.set_defaults(func=cmd_check_save)
    s = csub.add_parser("run", parents=[common, lim, chk], help="evaluate saved checks and limits against a run; exit 1 if anything fails")
    s.add_argument("run")
    s.add_argument("--case", dest="case_id", default=None, help="only this case")
    s.add_argument("--fail-on", choices=["any", "checks", "flags"], default="any", help="what makes the exit code 1 (default any: a flagged case or a failing/missing check)")
    s.set_defaults(func=cmd_check_run)
    s = csub.add_parser("list", parents=[common, chk], help="list saved checks")
    s.add_argument("--project", default=None)
    s.add_argument("--unit", default=None)
    s.set_defaults(func=cmd_check_list)
    s = csub.add_parser("rm", parents=[common, chk], help="remove a saved check")
    s.add_argument("case_id")
    s.set_defaults(func=cmd_check_rm)

    s = sub.add_parser("report", parents=[common, lim, chk], help="write report.md for a run (the receipt)")
    s.add_argument("run")
    s.add_argument("--out", default=None, help="write here instead of the run directory")
    s.add_argument("--print", action="store_true", help="also print the report")
    s.set_defaults(func=cmd_report)

    s = sub.add_parser("init", parents=[common], help="write rb.toml for this project (or --demo for a synthetic project that runs anywhere)")
    s.add_argument("--project", help="project name (saved checks follow it)")
    s.add_argument("--task", default=None, help="flow (default) | stereo | depth | generic")
    s.add_argument("--adapter", default=None, help="raft (default) | synthetic | package.module:Class")
    s.add_argument("--model-code", default=None, help="model repository checkout (RAFT: contains core/raft.py)")
    s.add_argument("--dataset", default=None, help="dataset directory (KITTI layout for raft)")
    s.add_argument("--dataset-name", default=None)
    s.add_argument("--kind", default=None, help="dataset kind for the adapter (raft: kitti)")
    s.add_argument("--iterations", type=int, default=None, help="refinement iterations per case (default 12)")
    s.add_argument("--device", default=None, help="cuda (default) or cpu")
    s.add_argument("--small", action="store_true", help="RAFT: build random-weight models as raft-small (real checkpoints are detected)")
    s.add_argument("--demo", action="store_true", help="synthetic project with two demo checkpoints; runs on any machine")
    s.add_argument("--force", action="store_true")
    s.set_defaults(func=cmd_init)

    s = sub.add_parser("doctor", parents=[common], help="check the environment, adapter, model code, dataset and checkpoints")
    s.add_argument("--checkpoint", action="append", default=None, help="checkpoint path to check (repeatable)")
    s.add_argument("--device", default=None)
    s.set_defaults(func=cmd_doctor)

    s = sub.add_parser("verify-hook", parents=[common], help="run one case and confirm the trajectory recorder fires once per iteration")
    s.add_argument("--checkpoint", default=None, help="checkpoint to load (raft: omit to use random weights for a mechanics check)")
    s.add_argument("--case", dest="case_id", default=None)
    s.add_argument("--device", default=None)
    s.set_defaults(func=cmd_verify_hook)

    s = sub.add_parser("run", parents=[common, lim, chk], help="evaluate the current and candidate checkpoints on the case set and write a run")
    s.add_argument("--baseline", required=True, help="current model's checkpoint")
    s.add_argument("--candidate", required=True, help="candidate checkpoint")
    s.add_argument("--baseline-name", default=None)
    s.add_argument("--candidate-name", default=None)
    s.add_argument("--limit", type=int, default=None, help="only the first N cases (pilot runs)")
    s.add_argument("--no-trajectories", action="store_true", help="skip recording; stability is then 'not assessed'")
    s.add_argument("--device", default=None)
    s.add_argument("--seed", type=int, default=0)
    s.add_argument("--fail-on", choices=["none", "regressions", "flags", "checks"], default="none", help="exit 1 when… (default none: findings are data)")
    s.add_argument("--quiet", action="store_true", help="no progress on stderr")
    s.set_defaults(func=cmd_run)

    s = sub.add_parser("docs", parents=[common], help="print AGENTS.md (or --errors for the error table)")
    s.add_argument("--errors", action="store_true")
    s.set_defaults(func=cmd_docs)

    s = sub.add_parser("schema", parents=[common], help="print a JSON Schema, the minimal example, or the full example")
    s.add_argument("name", choices=[*SCHEMAS.keys(), "example", "full-example"])
    s.set_defaults(func=cmd_schema)

    s = sub.add_parser("runs", parents=[common], help="list runs")
    s.set_defaults(func=cmd_runs)

    s = sub.add_parser("version", parents=[common], help="print the version")
    s.set_defaults(func=cmd_version)
    return p


COMMANDS = ["init", "doctor", "verify-hook", "run", "import", "example", "findings", "case", "check save", "check run", "check list", "check rm", "report", "docs", "schema", "runs", "version"]


def main(argv: Optional[list[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    json_mode = "--json" in argv
    parser = build_parser()
    if not argv or argv[0] in ("-h", "--help"):
        parser.print_help()
        return EXIT_OK
    if argv[0] in PLANNED:
        out = Out(argv[0], json_mode)
        err = RBError("E_NOT_AVAILABLE", message=f"`rb {argv[0]}` is not part of rabbit-brain {__version__}.")
        if not json_mode:
            print(f"rb {argv[0]}: {err.message} {err.fix}", file=sys.stderr)
        out.emit(ok=False, errors=[err.to_dict()])
        return EXIT_INVALID
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:  # argparse already printed usage
        return int(exc.code) if isinstance(exc.code, int) else EXIT_INVALID
    if args.command == "check" and not getattr(args, "check_command", None):
        parser.parse_args(["check", "--help"])
        return EXIT_OK
    command = args.command + (f" {args.check_command}" if args.command == "check" else "")
    out = Out(command, getattr(args, "json", False))
    out.command_line = " ".join(["rb", *argv])
    func: Callable = args.func
    try:
        code = func(args, out)
        out.emit(ok=code in (EXIT_OK, EXIT_CHECK_FAILED))
        return code
    except RBError as err:
        if not out.json_mode:
            print(f"rb {command}: {err.message}", file=sys.stderr)
            for pr in err.problems:
                print(f"  - {pr}", file=sys.stderr)
            if err.fix:
                print(f"  → {err.fix}", file=sys.stderr)
        out.emit(ok=False, errors=[err.to_dict()])
        return err.exit_code
    except Exception as exc:  # pragma: no cover - last resort
        traceback.print_exc(file=sys.stderr)
        err = RBError("E_INTERNAL", message=f"{type(exc).__name__}: {exc}", exit_code=EXIT_ENVIRONMENT)
        out.emit(ok=False, errors=[err.to_dict()])
        return EXIT_ENVIRONMENT


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
