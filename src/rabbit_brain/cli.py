"""The `rb` command line. Every command accepts --json and prints exactly one JSON object on stdout; logs go to stderr."""
from __future__ import annotations

import argparse
import json
import re
import shlex
import sys
import traceback
import warnings
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
from .importer import CSV_EXAMPLE, csv_to_comparison, load_comparison, parse_column_map
from .models import SCHEMAS, Bundle, ChecksV2, Envelope, Findings, Limits
from .onboard import BRIEF_NAME, BriefV1, EXAMPLE_BRIEF, write_package   # also registers the brief schema
from .report import agreement_line, report_markdown
from .adapters.base import task_metric as adapters_task_metric
from .runs import (bundle_from_comparison, compute_findings, import_record, list_runs, new_run_id, rederive, resolve_run, runs_dir, write_run)
from .stability import case_stability
from . import runner as runner_mod
from . import ledger_cli

PLANNED = {
    "paper": ("rb has no paper command yet. To record a paper by hand: save its text in the repository, then "
              "rb claim add \"...\" -e <exp> --metric <m> --equals <x> --tolerance <t> --source papers/<id>.txt --quote \"...\" --locator \"Table 1\"; "
              "rb spec set <exp> <name> --unknown for what it does not state."),
    "open": "rb has no local UI yet: rb status, rb show <id> and rb compare <experiment> read the same state.",
    "publish": "rb does not publish yet: commit .rb/ and push the repository.",
    "clone": "rb does not clone investigations yet: clone the repository; .rb/ comes with it.",
    "rerun": "rb has no rerun command: run the evaluation again with rb review run, then rb evidence attach.",
    "serve": "rb has no server: rb status, rb show <id> and rb compare <experiment> read the same state.",
    "reproduce": "rb has no reproduce command yet: rb experiment add --like <experiment> copies a spec to run again.",
}
FILTERS = ["flagged", "all", "regressions", "unstable", "improved-unstable", "settled-regressions", "improved", "stable"]
SORTS = ["priority", "error-change", "candidate-error", "current-error", "late-share", "name"]
INTEGRATION_HINT = "INTEGRATION.md has the ladder that turns this into a trusted run: doctor, the hook, the adapter check, a five-case run, then the review."

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
        self.runs_dir_flag: Optional[str] = None  # a --runs-dir in effect, repeated in `next` hints

    def say(self, *text: str) -> None:
        self.lines.extend(text)

    def warn(self, text: str) -> None:
        self.warnings.append(text)
        print(f"rb {self.command}: {text}", file=sys.stderr)

    def emit(self, ok: bool = True, errors: Optional[list[dict]] = None) -> None:
        if self.runs_dir_flag:
            self.next = [n + f" --runs-dir {self.runs_dir_flag}" if n.startswith(("rb review findings", "rb review case", "rb review report", "rb review check", "rb review runs")) else n for n in self.next]
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
            max_trajectory_regression=base.max_trajectory_regression if getattr(args, "max_trajectory_regression", None) is None else args.max_trajectory_regression,
            extra=base.extra,   # no CLI flag sets these; they come from the stored run and must survive a rebuild
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
        cmp = csv_to_comparison(path, project=args.project or "", baseline=args.baseline_name or "", candidate=args.candidate_name or "", dataset=args.dataset or "", metric=args.metric or "", unit=args.unit or "",
                                columns=parse_column_map(getattr(args, "columns", None)))
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
        out.next = [f"rb review import {target}", f"rb review findings {target}"]
        return EXIT_OK
    return _create_run(cmp, args, out, datetime.now().astimezone(), None, "example")


def _create_run(cmp, args, out: Out, started, path: Optional[Path], fmt: str) -> int:
    base = runs_dir(args.runs_dir)
    limits = limits_from(args)
    run_id = new_run_id(cmp.candidate, base)
    bundle = bundle_from_comparison(cmp, run_id, limits, "example" if cmp.source == "example" else "imported")
    checks = checks_for(args, bundle.project, out)
    findings = compute_findings(bundle, limits, checks)
    record = import_record(bundle, cmp, path, fmt, started, out.command_line, note=getattr(args, "note", "") or "")
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
    out.next = [f"rb review findings {run_id} --top 5", f"rb review case {run_id} {findings.verdict.start}" if findings.verdict.start else f"rb review report {run_id} --print"]
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
        return sorted(items, key=lambda q: -(q.error_change if q.error_change is not None else float("-inf")))
    if sort == "candidate-error":
        return sorted(items, key=lambda q: -(q.candidate_error if q.candidate_error is not None else -1))
    if sort == "current-error":
        return sorted(items, key=lambda q: -(q.baseline_error if q.baseline_error is not None else -1))
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
        f"Limits: +{plain(limits.max_regression)} {unit} · {pct(limits.max_late_share)} late · {limits.max_reversals} reversals" + (f" · +{plain(limits.max_trajectory_regression)} late movement" if limits.max_trajectory_regression is not None else ""),
        *([f"NOT CHECKED: {', '.join(limits.unenforced_limits())}. Recorded in this run's limits, not enforced by rb {__version__}. The verdict below does not account for them."] if limits.unenforced_limits() else []),
        f"Verdict: {findings.verdict.line}",
        f"{s.cases} cases · mean {to_fixed(s.mean_error.baseline)} → {to_fixed(s.mean_error.candidate)} {unit} · {s.regressions} regressions · {s.unstable} unstable ({s.improved_unstable} pass on error) · {s.settled_regressions} settled regressions · {s.flagged} flagged"
        + (f" · {s.borderline} borderline" if s.borderline else "")
        + (f" · checks {s.checks.passing} pass / {s.checks.failing} fail / {s.checks.missing} missing" if s.checks.saved else ""),
        "",
        f"{'rank':>4}  {'case':<24} {'current':>9} {'candidate':>9} {'change':>9}  {'late':>5} {'move':>7} {'rev':>3}  flags",
    )
    any_move = False
    any_near = False
    for q in shown:
        late = pct(q.candidate_late_share) if q.candidate_late_share is not None else "n/a"
        move = signed(q.late_update_change) if q.late_update_change is not None else "-"
        any_move = any_move or q.late_update_change is not None
        rev = str(q.candidate_reversals) if q.candidate_reversals is not None else "-"
        change = f"{signed(q.error_change)} {unit}" if q.error_change is not None else "no gt"
        cur = to_fixed(q.baseline_error) if q.baseline_error is not None else "-"
        cand = to_fixed(q.candidate_error) if q.candidate_error is not None else "-"
        any_near = any_near or bool(q.borderline)
        flags = ", ".join(q.flags) or "-"
        out.say(f"{q.rank:>4}  {q.id:<24} {cur:>9} {cand:>9} {change:>9}  {late:>5} {move:>7} {rev:>3}  {flags}{' (borderline)' if q.borderline else ''}")
    out.say("", f"{len(shown)} of {s.cases} cases shown (filter: {args.filter}, sort: {args.sort}); rank is the queue position under these limits.",
            f"late = candidate's share of refinement in the last third of its iterations; rev = its reversals"
            + (f"; move = candidate late movement minus the current model's, {unit} per iteration" if any_move else "") + ".")
    if any_near:
        out.say("borderline = the case turns on a margin of less than a tenth of a limit (one reversal, for the reversal limit), which is about "
                "what the same checkpoints differ by on another GPU or torch build: a re-run elsewhere may sort it the other way.")
    out.data = {**findings.model_dump(exclude={"queue"}), "queue": [q.model_dump() for q in shown], "shown": len(shown), "filter": args.filter, "sort": args.sort}
    ref = out.ref or bundle.run_id
    if shown:
        out.next = [f"rb review case {ref} {shown[0].id}", f"rb review check save {ref} {shown[0].id}", f"rb review report {ref}"]
    else:
        out.next = [f"rb review findings {ref} --filter all", f"rb review report {ref}"]
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
    if q.late_update_change is not None:
        out.say(f"Late movement vs current model: {signed(q.late_update_change)} {unit} per iteration" + (f" (limit +{plain(limits.max_trajectory_regression)})" if limits.max_trajectory_regression is not None else " (no trajectory-regression limit set)"))
    if c.candidate_frames:
        peak = max(range(len(c.candidate_frames)), key=lambda i: c.candidate_frames[i])
        out.say(f"Per-frame error: {len(c.candidate_frames)} frames · candidate peak {to_fixed(c.candidate_frames[peak])} {unit} at frame {peak + 1}")
    out.say("", q.why)
    if q.notes:
        out.say(f"Notes: {q.notes}")
    if q.tags:
        out.say(f"Tags: {', '.join(q.tags)}")
    rendered = None
    if getattr(args, "render", False):
        from .evidence import render_case
        run_dir = resolve_run(args.run, runs_dir(args.runs_dir))[1]
        if run_dir is None or not run_dir.is_dir():
            raise RBError("E_RUN_NOT_FOUND", message="Evidence needs a run directory (rb-runs/<run_id>), not a loose comparison file.")
        if bundle.source != "run":
            raise RBError("E_CONFIG_MISSING", message=f"Run {bundle.run_id} was {bundle.source}: rb has only its numbers, not the model or the data, so there is nothing to render. Evidence comes from `rb review run` (rb review init with the model code and the dataset, then rb review run).",
                          fix="Set up the runner with `rb review init --adapter raft --model-code ./raft --dataset <path>` and `rb review run` the two checkpoints; then `rb review case <run> <id> --render` works.")
        cfg = load_config()
        rendered = render_case(cfg, bundle, q.id, run_dir, device=getattr(args, "device", None))
        chk = rendered["check"]
        out.say(f"Evidence written to {rendered['dir']}/ ({', '.join(rendered['files'])}).",
                f"Re-run on this case {'reproduced' if chk['errors_match'] and chk['candidate_trajectory_matches'] else 'DIFFERS FROM'} the run's numbers: "
                f"current {chk['baseline_error']['now']} vs {chk['baseline_error']['run']}, candidate {chk['candidate_error']['now']} vs {chk['candidate_error']['run']}.")
    elif c.evidence:
        out.say(f"Evidence: {c.evidence.dir}/ ({', '.join(c.evidence.files) if c.evidence.files else 'see directory'}).")
    out.data = {**q.model_dump(), "stability": st.model_dump(), "baseline_trajectory": c.baseline_trajectory, "candidate_trajectory": c.candidate_trajectory,
                "baseline_frames": c.baseline_frames, "candidate_frames": c.candidate_frames, "limits": limits.model_dump(), "metric": bundle.metric.model_dump(),
                "evidence": rendered or (c.evidence.model_dump() if c.evidence else None)}
    ref = out.ref or bundle.run_id
    out.next = [f"rb review check save {ref} {q.id}", f"rb review findings {ref}"] + ([] if rendered or c.evidence or bundle.source != "run" else [f"rb review case {ref} {q.id} --render"])
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
            out.say(f"To accept the candidate's current level instead: rb review check save {out.ref or bundle.run_id} {new.case_id} --max-error {to_fixed(case.candidate_error + limits.max_regression)}")
    out.data = {"checks_file": str(path), "check": new.model_dump(exclude_none=True), "status_now": now.model_dump(), "total_checks": len(checks.checks)}
    out.next = [f"rb review check run {out.ref or bundle.run_id} --checks {path}", f"rb review check list --checks {path}"]
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
    shown = queue if (args.all or args.case_id) else flagged
    for q in shown:
        status = {"regression": "REGRESSION", "improved": "IMPROVED", "stable": "OK", "not_measured": "NO GT"}.get(q.error_outcome, "OK")
        numbers = f"{q.baseline_error:.2f} -> {q.candidate_error:.2f} {unit} ({q.error_change:+.2f})" if q.error_change is not None else "error not measured (no ground truth)"
        line = f"{status:12} {q.id:24} {numbers}"
        if q.candidate_late_share is not None:
            line += f"  | {'UNSTABLE' if 'unstable' in q.flags else 'settled':8} late {q.candidate_late_share * 100:4.0f}%  reversals {q.candidate_reversals}"
        out.say(line)
    if not args.all and not args.case_id:
        out.say(f"{len(queue) - len(flagged)} of {len(queue)} cases within limits and not shown (--all shows every case).")
    for r in results:
        tag = {"passing": "PASS", "failing": "FAIL", "missing": "MISSING", "other-project": "SKIP"}[r.status]
        limits_text = f"max {r.max_error:.2f} {unit}" + (f", late <= {r.max_late_share * 100:.0f}%" if r.max_late_share is not None else "") + (f", reversals <= {r.max_reversals}" if r.max_reversals is not None else "")
        out.say(f"CHECK {tag:7} {r.case_id} | {limits_text}" + ("" if r.status == "passing" else f" | {r.reason}"))
    if checks is None:
        out.say("No checks file found (checks.json); only the regression and stability limits were applied.")
    unenforced = limits.unenforced_limits()
    if unenforced:
        out.say(f"POLICY INCOMPLETE: {', '.join(unenforced)} could not be evaluated by rb {__version__}.",
                "This run does not establish that the saved policy passed. Remove the limit or use a version that checks it.")
    out.say("CHECKS NEED ATTENTION" if failed else ("CHECKS PASSED, POLICY INCOMPLETE" if unenforced else "CHECKS PASSED"))
    out.data = {"failed": failed, "fail_on": args.fail_on, "flagged": [q.id for q in flagged], "checks": [r.model_dump() for r in results],
                "summary": findings.summary.model_dump(), "verdict": findings.verdict.model_dump(), "limits": limits.model_dump(),
                # A limit that was declared but not evaluated must not read as one that passed, to a person or to CI.
                "policy": "incomplete" if unenforced else "complete", "unenforced_limits": unenforced}
    ref = out.ref or bundle.run_id
    out.next = [f"rb review case {ref} {flagged[0].id}" if flagged else f"rb review report {ref}"]
    return EXIT_CHECK_FAILED if (failed or unenforced) else EXIT_OK


def cmd_check_list(args: argparse.Namespace, out: Out) -> int:
    path = Path(args.checks) if args.checks else DEFAULT_CHECKS_PATH
    checks = load_checks(path, args.project)
    if not checks.checks:
        out.say(f"No saved checks in {path}.")
    else:
        out.say(f"{len(checks.checks)} saved check{'' if len(checks.checks) == 1 else 's'} for '{checks.project}' in {path}:")
        for c in checks.checks:
            out.say(f"- {c.case_id}{f' ({c.name})' if c.name else ''}: {describe_check(c, args.unit or c.unit or 'units')}{f' · from {c.from_run}' if c.from_run else ''}{f' · {c.note}' if c.note else ''}")
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


def cmd_share(args: argparse.Namespace, out: Out) -> int:
    from .share import build_share, consent_text, write_share
    bundle, run_dir, limits = _load(args, out)
    findings = compute_findings(bundle, limits, None)
    record = None
    if run_dir and (run_dir / "record.json").exists():
        from .models import Record
        try:
            record = Record.model_validate_json((run_dir / "record.json").read_text(encoding="utf-8"))
        except ValidationError:
            record = None
    share = build_share(bundle, record, findings)
    if args.print:
        out.say(json.dumps(share.model_dump(), indent=1))
        out.data = share.model_dump()
        return EXIT_OK
    if run_dir is None:
        raise RBError("E_RUN_NOT_FOUND", message="rb review share needs a run directory (rb-runs/<run_id>) to write share.json into; use --print for a file read in place.")
    path = write_share(run_dir, share)
    out.say(*consent_text(share, path))
    out.data = {"path": str(path), "cases": len(share.cases), "share_id": share.share_id, "included": list(__import__("rabbit_brain.share", fromlist=["INCLUDED"]).INCLUDED),
                "excluded": list(__import__("rabbit_brain.share", fromlist=["EXCLUDED"]).EXCLUDED), "sent": False}
    out.next = [f"cat {path}"]
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
    html_path = None
    if args.open or args.html or args.embed:
        html_path = _write_html(args, bundle, run_dir, target, out, checks)
    out.data = {"path": str(target) if target else None, "html": str(html_path) if html_path else None,
                "markdown": md, "verdict": findings.verdict.model_dump()}
    out.next = [f"rb review findings {out.ref or bundle.run_id}"]
    return EXIT_OK


def _html_target(args: argparse.Namespace, run_dir: Optional[Path], md_target: Optional[Path]) -> Path:
    if isinstance(args.html, str):
        return Path(args.html)
    if run_dir:
        return run_dir / "report.html"
    if md_target:
        return md_target.with_suffix(".html")
    src = Path(args.run)
    return (src.parent if src.is_file() else Path.cwd()) / "report.html"


def _write_html(args: argparse.Namespace, bundle: Bundle, run_dir: Optional[Path], md_target: Optional[Path], out: Out,
                checks: Optional[ChecksV2] = None) -> Path:
    from . import viewer
    target = _html_target(args, run_dir, md_target)
    # Evidence images are referenced relative to the HTML file, so they only resolve while the file
    # sits in the run directory. Written anywhere else they have to travel inside the document.
    away = run_dir is not None and target.parent.resolve() != run_dir.resolve()
    embed = bool(args.embed) or away
    try:
        html = viewer.build(bundle, run_dir, embed, checks)
    except FileNotFoundError as exc:
        raise RBError("E_WRITE_FAILED", message=str(exc))
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(html, encoding="utf-8")
    except OSError as exc:
        raise RBError("E_WRITE_FAILED", message=f"Could not write {target}: {exc}")
    size = f"{target.stat().st_size / 1_000_000:.1f} MB" if target.stat().st_size >= 1_000_000 else f"{target.stat().st_size // 1000} KB"
    out.say(f"HTML report written to {target} ({size}). It opens from disk and sends nothing anywhere.")
    if away and not args.embed:
        out.say("Evidence images were inlined because the file is not in the run directory, where their relative paths point.")
    if args.open:
        opened = False
        try:
            import webbrowser
            opened = webbrowser.open(target.resolve().as_uri())
        except Exception:
            opened = False
        out.say("Opened it in your browser." if opened else f"No browser to open here. Open the file yourself: {target}")
    return target


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
            raise RBError("E_CONFIG_INVALID", message="rb review init needs --project <name> (or --demo).")
        adapter_id = args.adapter or "raft"
        cfg = Config(project=ProjectSection(name=args.project, task=args.task or "flow"),
                     adapter=AdapterSection(id=adapter_id if ":" not in adapter_id else None, module=adapter_id if ":" in adapter_id else None,
                                            model_code=args.model_code or ("./raft" if adapter_id == "raft" else None), iterations=args.iterations or 12,
                                            device=args.device or "cuda", small=bool(args.small)),
                     dataset=DatasetSection(name=args.dataset_name or (Path(args.dataset).name if args.dataset else "cases"), kind=args.kind or "kitti", path=args.dataset, cases="all"))
    cfg.limits = Limits(max_trajectory_regression=cfg.limits.max_regression)  # paired, label-free regression test on by default for runs
    text = render_config(cfg)
    path.write_text(text, encoding="utf-8")
    gi = Path(".gitignore")
    gi_line = f".gitignore already ignores rb-runs/*/evidence/."
    if "rb-runs/*/evidence/" not in (gi.read_text(encoding="utf-8") if gi.exists() else ""):
        with gi.open("a", encoding="utf-8") as f:
            f.write(("\n" if gi.exists() and gi.stat().st_size else "") + GITIGNORE_SNIPPET)
        gi_line = f"{'Appended to' if gi.stat().st_size > len(GITIGNORE_SNIPPET) + 1 else 'Wrote'} .gitignore: rb-runs/*/evidence/ (evidence PNGs are large; the run's JSON and report are meant to be committed)."
    out.say(f"Wrote {path} for project '{cfg.project.name}' (adapter {cfg.adapter.id or cfg.adapter.module}); dataset name '{cfg.dataset.name}' (--dataset-name to change it).", gi_line)
    if args.demo:
        out.say("Demo checkpoints: ckpt/synth-current.json and ckpt/synth-candidate.json (synthetic, not a real model).")
        out.next = ["rb review doctor", "rb review run --baseline ckpt/synth-current.json --candidate ckpt/synth-candidate.json"]
    else:
        out.next = ["rb review doctor", "rb review verify-hook --checkpoint <path>", "rb review run --baseline <ckpt-A> --candidate <ckpt-B>"]
    out.data = {"config_path": str(path), "config": cfg.model_dump(exclude_none=True), "text": text}
    return EXIT_OK


def cmd_onboard(args: argparse.Namespace, out: Out) -> int:
    root = Path(args.dir or ".")
    if not args.brief:
        path = root / BRIEF_NAME
        if path.exists() and not args.force:
            raise RBError("E_CONFIG_INVALID", message=f"{path} already exists; fill it in and run `rb review onboard --brief {path}`, or pass --force to replace it with a fresh template.")
        root.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(EXAMPLE_BRIEF.model_dump(exclude_none=True), indent=1) + "\n", encoding="utf-8")
        out.say(f"Wrote {path}: a filled example, so you can see the shape. Replace the values with yours.",
                "It asks for your task, architecture, framework, where your model code and checkpoints are, how one case is stored, whether you have ground truth, and what decision this review has to support.",
                "It stays on this machine: rb review onboard only writes local files. `rb schema brief` prints the full schema.")
        out.data = {"brief_path": str(path), "brief": EXAMPLE_BRIEF.model_dump(exclude_none=True)}
        out.next = [f"rb review onboard --brief {path}"]
        return EXIT_OK
    src = Path(args.brief)
    if not src.exists():
        raise RBError("E_FILE_NOT_FOUND", message=f"No such brief: {src}")
    try:
        brief = BriefV1.model_validate(json.loads(src.read_text(encoding="utf-8")))
    except json.JSONDecodeError as exc:
        raise RBError("E_IMPORT_NOT_JSON", message=f"{src}: {exc}")
    except ValidationError as exc:
        first = exc.errors()[0]
        loc = ".".join(str(p) for p in first.get("loc", ()))
        raise RBError("E_CONFIG_INVALID", message=f"{src}: {loc}: {first.get('msg', 'invalid')}. `rb schema brief` prints what each field expects.")
    result = write_package(brief, root, force=args.force, source=src)
    # "in ." followed by the full stop renders as "in ..", which reads as the parent directory
    # in the first line of output a new user ever sees. Name the directory only when it is not this one.
    where = "here" if result["dir"] in (".", "") else f"in {result['dir']}"
    out.say(f"Wrote {', '.join(result['files'])} {where}.")
    if result["built_in"]:
        out.say(f"{brief.architecture} is covered by the built-in `{result['adapter']}` adapter, so there is nothing to write.")
    else:
        out.say(f"There is no built-in adapter for {brief.architecture}, so rb_adapter.py is the shape of one: {result['todos']} TODOs, each saying what it must return.",
                "That is the honest state, not a limitation being hidden: nothing can guess your loader, your valid mask or your metric formula.")
    out.say(f"{INTEGRATION_HINT}")
    out.data = result
    out.next = ["rb review doctor"] if result["built_in"] else [f"open {result['dir']}/INTEGRATION.md", "rb docs"]
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
    out.next = ["rb review verify-hook --checkpoint <path>", "rb review verify-adapter --checkpoint <path>", "rb review run --baseline <ckpt-A> --candidate <ckpt-B>"] if cfg and not failed else ["rb review init --project <name> --adapter raft --model-code ./raft --dataset <path>"] if not cfg else []
    if failed:
        first = failed[0]
        raise RBError(first.get("code") or "E_DOCTOR", message=f"{len(failed)} check(s) failed; first: {first['check']}: {first['detail']}", fix=first.get("fix"), exit_code=EXIT_ENVIRONMENT)
    return EXIT_OK


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
    out.next = ["rb review verify-adapter --checkpoint <path>", "rb review run --baseline <ckpt-A> --candidate <ckpt-B>"] if result["ok"] else ["rb docs"]
    if not result["ok"]:
        code = "E_HOOK_NOT_REACHABLE" if result["fired"] == 0 else "E_HOOK_LENGTH"
        raise RBError(code, message=result["problems"][0])
    return EXIT_OK


def cmd_verify_adapter(args: argparse.Namespace, out: Out) -> int:
    cfg = load_config()
    result = runner_mod.verify_adapter(cfg, Path(args.checkpoint), args.device, args.limit)
    unit = adapters_task_metric(cfg).unit
    status = result["status"]
    if status in ("agree", "disagree"):
        out.say(f"Adapter vs the model repository's own evaluation, {result['cases']} case(s) on {result['device']} · {result['seconds']}s",
                f"Reference path: {result.get('reference')}")
        out.say(f"{'case':<20} {'adapter':>12} {'reference':>12} {'diff':>10}")
        for r in result["per_case"]:
            out.say(f"{r['id']:<20} {r['adapter']:>12.5f} {r['reference']:>12.5f} {r['diff']:>10.2g}{'' if r['agree'] else '   DIFFERS'}")
    if status == "agree":
        out.say(f"Adapter verified: agrees with the reference evaluation on all {result['cases']} cases (max |diff| {result['max_abs_diff']:.2g} {unit}, tolerance {result['tolerance']:g} relative).")
        out.next = ["rb review run --baseline <ckpt-A> --candidate <ckpt-B>"]
    elif status == "not_available":
        out.say(f"Not established: {result.get('note')}", "Add reference_value(model, case) to the adapter: the same per-case error through the model repository's own loader, forward and metric formula.")
        out.next = ["rb docs"]
    out.data = result
    if status == "disagree":
        worst = max(result["per_case"], key=lambda r: r["diff"])
        raise RBError("E_ADAPTER_DISAGREES", message=f"The adapter disagrees with the reference evaluation on {len(result['disagreeing'])} of {result['cases']} cases (worst {worst['id']}: adapter {worst['adapter']:.4g} vs reference {worst['reference']:.4g} {unit}).")
    return EXIT_OK


def cmd_run(args: argparse.Namespace, out: Out) -> int:
    cfg = load_config()
    limits = limits_from(args, cfg.limits)
    base = runs_dir(args.runs_dir)
    checks = checks_for(args, cfg.project.name, out)
    quiet = getattr(args, "quiet", False)
    progress = (lambda msg: print(f"rb review run: {msg}", file=sys.stderr)) if not quiet else None
    run_dir, bundle, record, findings, _ = runner_mod.run(
        cfg, Path(args.baseline), Path(args.candidate), limits=limits, runs_dir=base, command=out.command_line,
        device=args.device, seed=args.seed, no_trajectories=args.no_trajectories, limit=args.limit,
        baseline_name=args.baseline_name, candidate_name=args.candidate_name, checks=checks, progress=progress,
        evidence=getattr(args, "evidence", None), skip_reference=getattr(args, "skip_reference", False),
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
        f"Adapter agreement: {agreement_line(record.adapter_agreement, unit)}" if getattr(record, "adapter_agreement", None) else "",
        f"Verdict: {findings.verdict.line}",
        f"Run written to {run_dir}/ (bundle.json, record.json, findings.json, report.md)",
    )
    ev = getattr(record, "evidence", None) or {}
    if ev.get("cases"):
        out.say(f"Evidence rendered for {len(ev['cases'])} case(s) under {run_dir}/evidence/ (case.png per case).")
    skipped = getattr(record, "skipped", None) or {}
    if skipped:
        out.say(f"{len(skipped)} case(s) skipped after inference errors: {', '.join(list(skipped)[:5])}{' …' if len(skipped) > 5 else ''}")
    flagged = [q for q in findings.queue if "regression" in q.flags or "unstable" in q.flags]
    failed = {"none": False, "regressions": s.regressions > 0, "flags": bool(flagged), "checks": s.checks.failing + s.checks.missing > 0}[args.fail_on]
    unenforced = limits.unenforced_limits()
    if unenforced:
        out.say(f"POLICY INCOMPLETE: {', '.join(unenforced)} could not be evaluated by rb {__version__}.",
                "This run does not establish that the saved policy passed.")
    out.data = {"run_dir": str(run_dir), "summary": s.model_dump(), "verdict": findings.verdict.model_dump(), "limits": limits.model_dump(), "metric": bundle.metric.model_dump(),
                "hook": record.hook, "adapter_agreement": getattr(record, "adapter_agreement", None),
                "checkpoints": {k: v.model_dump() for k, v in record.checkpoints.items()}, "skipped": skipped, "fail_on": args.fail_on, "failed": failed,
                "evidence": ev,
                "policy": "incomplete" if unenforced else "complete", "unenforced_limits": unenforced}
    out.next = [f"rb review findings {bundle.run_id} --top 5", f"rb review case {bundle.run_id} {findings.verdict.start}" if findings.verdict.start else f"rb review report {bundle.run_id} --print"]
    return EXIT_CHECK_FAILED if (failed or unenforced) else EXIT_OK


# ---------------------------------------------------------------- workspace (the paid surface)

def cmd_plans(args: argparse.Namespace, out: Out) -> int:
    """What a workspace costs. No token, no account: an agent can answer "what would this cost"
    before anyone has signed up for anything."""
    from . import workspace
    data = workspace.plans()
    out.say("Plans")
    for p in data.get("plans", []):
        mark = "" if p.get("available") else "   (published, not yet sellable)"
        out.say(f"  {p['id']:<20} {p.get('price','?'):<30}{mark}")
        for line in p.get("includes", [])[:3]:
            out.say(f"      + {line}")
    out.say("", data.get("note", ""))
    out.data = data
    out.next = ["rb workspace status"]
    return EXIT_OK


def cmd_workspace_status(args: argparse.Namespace, out: Out) -> int:
    from . import workspace
    data = workspace.status_of()
    ws = data.get("workspace", {})
    out.say(
        f"Workspace: {ws.get('name','?')} ({ws.get('slug','?')})",
        f"Plan: {data.get('plan')} · status {data.get('status')} · active {data.get('active')}",
        "Comparisons can be pushed." if data.get("can_post_comparisons")
        else "Comparisons cannot be pushed: this workspace has no active subscription.",
    )
    out.data = data
    out.next = ["rb workspace push <run>"] if data.get("can_post_comparisons") else ["rb workspace checkout"]
    return EXIT_OK


def cmd_workspace_checkout(args: argparse.Namespace, out: Out) -> int:
    """Returns a link. A person opens it and approves the payment; this command cannot and will not
    complete a purchase itself."""
    from . import workspace
    data = workspace.checkout(args.plan)
    out.say(
        f"Checkout for the {data.get('plan')} plan on workspace {data.get('workspace')}:",
        f"  {data.get('checkout_url')}",
        "",
        "Open that link and complete the payment. A person has to approve it; this command cannot.",
        "Then `rb workspace status` until active is true.",
    )
    out.data = data
    out.next = ["rb workspace status"]
    return EXIT_OK


def cmd_workspace_push(args: argparse.Namespace, out: Out) -> int:
    """Push one finished comparison into the workspace. The run stays on disk either way."""
    from . import workspace
    bundle, _ = resolve_run(args.run, runs_dir(args.runs_dir))
    out.run_id = bundle.run_id
    data = workspace.push(json.loads(bundle.model_dump_json()))
    out.say(
        f"Pushed {bundle.run_id} to workspace project {data.get('project')}.",
        f"{data.get('cases')} cases · {data.get('metrics')} metric(s) · policy {data.get('policy')}",
    )
    if data.get("policy") == "incomplete":
        out.say(f"POLICY INCOMPLETE: {', '.join(data.get('unenforced_limits') or [])} were not evaluated. "
                f"The workspace records this review as incomplete, as rb does.")
    out.data = data
    return EXIT_OK


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
    if args.name == "csv":
        out.say(CSV_EXAMPLE.rstrip("\n"))
        out.data = {"csv": CSV_EXAMPLE, "columns": "case_id, baseline_error, candidate_error required; name, tags, baseline_trajectory, candidate_trajectory, baseline_frames, candidate_frames, notes optional; series values separated by ';'; other column names map with --columns"}
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
    rows = []
    for rid in ids:
        row = {"run_id": rid}
        try:
            fj = json.loads((base / rid / "findings.json").read_text(encoding="utf-8"))
            bj = json.loads((base / rid / "bundle.json").read_text(encoding="utf-8"))
            row.update({"baseline": bj["baseline"]["name"], "candidate": bj["candidate"]["name"], "cases": len(bj.get("cases", [])), "source": bj.get("source"), "verdict": fj["verdict"]["line"]})
        except (OSError, ValueError, KeyError):
            pass
        rows.append(row)
    if not rows:
        out.say(f"No runs under {base}/.")
    for row in rows:
        out.say(f"{row['run_id']:<32} {row.get('baseline', '?')} → {row.get('candidate', '?')} · {row.get('cases', '?')} cases · {row.get('verdict', '(no findings.json)')}")
    out.data = {"runs_dir": str(base), "runs": ids, "details": rows}
    if ids:
        out.next = [f"rb review findings {ids[-1]}"]
    return EXIT_OK


# ---------------------------------------------------------------- parser


class UsageError(Exception):
    """argparse could not read the command line. main() turns it into an E_USAGE envelope under --json."""

    def __init__(self, message: str, usage: str) -> None:
        super().__init__(message)
        self.message, self.usage = message, usage


class RBArgumentParser(argparse.ArgumentParser):
    """argparse, except that a usage error is raised for main() to report (an agent with --json gets an envelope, not a
    bare usage line on stderr), and a negative number in scientific notation (--at-most -1e-3) is a value, not an option."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._negative_number_matcher = re.compile(r"^-(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?$")

    def error(self, message: str) -> None:  # type: ignore[override]
        raise UsageError(message, self.format_usage())


def add_review_parsers(sub, common: argparse.ArgumentParser, lim: argparse.ArgumentParser, chk: argparse.ArgumentParser) -> None:
    """Release review: compare two checkpoints of an iterative perception model case by case. Registered under
    `rb review`, and at the top level under the names it shipped with, which stay as unlisted aliases."""
    s = sub.add_parser("import", parents=[common, lim, chk], help="import a version-1 results JSON or a metrics CSV into a new run")
    s.add_argument("file")
    s.add_argument("--project", help="project name (overrides the file's; required for CSV)")
    s.add_argument("--baseline-name", help="current model's name (CSV)")
    s.add_argument("--candidate-name", help="candidate model's name (CSV)")
    s.add_argument("--dataset", help="evaluation set's name (CSV)")
    s.add_argument("--metric", help="error metric name, e.g. mean_endpoint_error (CSV)")
    s.add_argument("--unit", help="metric unit, e.g. px or cm (CSV)")
    s.add_argument("--columns", default=None, help="map rb's columns to the file's, e.g. case_id=frame,baseline_error=epe_current,candidate_error=epe_candidate,candidate_trajectory=updates (CSV)")
    s.add_argument("--note", default="", help="a note for the receipt (record.json), e.g. where the numbers came from")
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
    s.add_argument("--render", action="store_true", help="re-run both checkpoints on this case and write evidence PNGs under the run's evidence/ directory (needs rb.toml and the checkpoints)")
    s.add_argument("--device", default=None)
    s.set_defaults(func=cmd_case)

    c = sub.add_parser("check", help="saved checks: save, run, list, rm")
    csub = c.add_subparsers(dest="check_command", metavar="<save|run|list|rm>")
    s = csub.add_parser("save", parents=[common, lim, chk], help="keep a case for the next checkpoint (default limit: current error + max regression)")
    s.add_argument("run")
    s.add_argument("case_id")
    s.add_argument("--max-error", type=float, default=None, help="absolute candidate error limit (default: the better of the two models on this case + max regression)")
    s.add_argument("--no-settled", action="store_true", help="do not require a settled trajectory")
    s.add_argument("--note", default="")
    s.set_defaults(func=cmd_check_save)
    s = csub.add_parser("run", parents=[common, lim, chk], help="evaluate saved checks and limits against a run; exit 1 if anything fails")
    s.add_argument("run")
    s.add_argument("--case", dest="case_id", default=None, help="only this case")
    s.add_argument("--fail-on", choices=["any", "checks", "flags"], default="any", help="what makes the exit code 1 (default any: a flagged case or a failing/missing check)")
    s.add_argument("--all", action="store_true", help="print every case, not only the flagged ones")
    s.set_defaults(func=cmd_check_run)
    s = csub.add_parser("list", parents=[common, chk], help="list saved checks")
    s.add_argument("--project", default=None)
    s.add_argument("--unit", default=None)
    s.set_defaults(func=cmd_check_list)
    s = csub.add_parser("rm", parents=[common, chk], help="remove a saved check")
    s.add_argument("case_id")
    s.set_defaults(func=cmd_check_rm)

    s = sub.add_parser("report", parents=[common, lim, chk], help="write the run's receipt: report.md, and report.html to read in a browser with --open")
    s.add_argument("run")
    s.add_argument("--out", default=None, help="write here instead of the run directory")
    s.add_argument("--print", action="store_true", help="also print the report")
    s.add_argument("--open", action="store_true", help="also write report.html and open it in your browser")
    s.add_argument("--html", nargs="?", const=True, default=None, metavar="PATH", help="write the HTML report, optionally somewhere other than the run directory")
    s.add_argument("--embed", action="store_true", help="carry evidence images inside the HTML so the file travels on its own")
    s.set_defaults(func=cmd_report)

    s = sub.add_parser("init", parents=[common], help="write rb.toml for this project (or --demo for a synthetic project that runs anywhere)")  # rb review init
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

    s = sub.add_parser("onboard", parents=[common], help="turn a described setup into a configured project, or into the honest shape of one")
    s.add_argument("--brief", default=None, help="a brief.json; without it, writes a template to fill in")
    s.add_argument("--dir", default=None, help="where to write (default: here)")
    s.add_argument("--force", action="store_true", help="overwrite files that are already there")
    s.set_defaults(func=cmd_onboard)

    s = sub.add_parser("doctor", parents=[common], help="check the environment, adapter, model code, dataset and checkpoints")
    s.add_argument("--checkpoint", action="append", default=None, help="checkpoint path to check (repeatable)")
    s.add_argument("--device", default=None)
    s.set_defaults(func=cmd_doctor)

    s = sub.add_parser("verify-hook", parents=[common], help="run one case and confirm the trajectory recorder fires once per iteration")
    s.add_argument("--checkpoint", default=None, help="checkpoint to load (raft: omit to use random weights for a mechanics check)")
    s.add_argument("--case", dest="case_id", default=None)
    s.add_argument("--device", default=None)
    s.set_defaults(func=cmd_verify_hook)

    s = sub.add_parser("verify-adapter", parents=[common], help="compare the adapter's per-case error with the model repository's own evaluation on a few cases")
    s.add_argument("--checkpoint", required=True, help="checkpoint to load")
    s.add_argument("--limit", type=int, default=5, help="labeled cases to compare (default 5)")
    s.add_argument("--device", default=None)
    s.set_defaults(func=cmd_verify_adapter)

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
    s.add_argument("--evidence", choices=["none", "standard", "full"], default=None, help="render evidence PNGs for the top flagged cases (standard, rb.toml [evidence] top) or every case (full); default from rb.toml")
    s.add_argument("--skip-reference", action="store_true", help="do not check the adapter against the model repository's own evaluation first (the receipt says so)")
    s.set_defaults(func=cmd_run)

    s = sub.add_parser("share", parents=[common, lim], help="write the run's anonymised statistics to share.json for the calibration corpus; nothing is sent")
    s.add_argument("run")
    s.add_argument("--print", action="store_true", help="print the JSON instead of writing share.json")
    s.set_defaults(func=cmd_share)

    s = sub.add_parser("runs", parents=[common], help="list runs")
    s.set_defaults(func=cmd_runs)


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", help="print one JSON object (the envelope) instead of text")
    common.add_argument("--runs-dir", default=None, help="where runs live (default rb-runs/, or $RB_RUNS_DIR)")
    common.add_argument("--verbose", action="store_true", help="show warnings raised by the model code and libraries (they are counted and hidden otherwise)")
    lim = argparse.ArgumentParser(add_help=False)
    lim.add_argument("--max-regression", type=float, default=None, help="allowed error increase over the current model, in the metric's unit (default 0.3)")
    lim.add_argument("--max-late-share", type=float, default=None, help="allowed share of refinement in the last third of iterations (default 0.25)")
    lim.add_argument("--max-reversals", type=int, default=None, help="allowed number of iterations where the update grew (default 2)")
    lim.add_argument("--max-last-update", type=float, default=None, help="allowed size of the final update, in the trajectory's unit (off unless set)")
    lim.add_argument("--max-trajectory-regression", type=float, default=None, help="allowed increase of the candidate's late movement over the current model's on the same case, trajectory unit (rb init sets it to max_regression; off for rb review import unless set)")
    chk = argparse.ArgumentParser(add_help=False)
    chk.add_argument("--checks", default=None, help="saved checks file (default checks.json in the current directory)")

    research_common = argparse.ArgumentParser(add_help=False)
    research_common.add_argument("--json", action="store_true", help="print one JSON object (the envelope) instead of text")
    research_common.add_argument("--runs-dir", default=None, help=argparse.SUPPRESS)
    research_common.add_argument("--verbose", action="store_true", help=argparse.SUPPRESS)

    p = RBArgumentParser(prog="rb", description=DESCRIPTION)
    p.add_argument("--version", action="version", version=f"rabbit-brain {__version__}")
    sub = p.add_subparsers(dest="command", metavar="<command>")

    # research state: rb init starts it; with release-review flags (--project, --demo, ...) it still writes rb.toml
    s = sub.add_parser("init", parents=[common], help="start the research state (.rb/) here; with --project or --demo, set up release review (rb review init)")
    s.add_argument("title", nargs="?", default=None, help="what you are trying to establish (default: this directory's name)")
    s.add_argument("--id", default=None, help="a short id for the investigation (default: from the title)")
    for flag in RELEASE_INIT_FLAGS:
        s.add_argument(flag, default=None, type=int if flag == "--iterations" else str, help=argparse.SUPPRESS)
    for flag in RELEASE_INIT_SWITCHES:
        s.add_argument(flag, action="store_true", help=argparse.SUPPRESS)
    s.set_defaults(func=cmd_init_router)
    s = sub.add_parser("doctor", parents=[common], help="with .rb/ here: check the research state; otherwise release review's environment check")
    s.add_argument("--checkpoint", action="append", default=None, help="checkpoint path to check (release review)")
    s.add_argument("--device", default=None)
    s.set_defaults(func=cmd_doctor_router)
    ledger_cli.add_parsers(sub, common, research_common)

    add_review_parsers(_Skip(sub, {"init", "doctor"}), common, lim, chk)
    r = sub.add_parser("review", help="release review: compare two checkpoints case by case; its runs attach as evidence")
    rsub = r.add_subparsers(dest="review_command", metavar="<command>", required=True)
    add_review_parsers(rsub, common, lim, chk)

    s = sub.add_parser("mcp", help="serve the research state to an agent over MCP (stdio); every call is recorded as the agent's")
    s.add_argument("--agent", default=None, help="the agent's name in the record (default: the MCP client's name)")
    s = sub.add_parser("docs", parents=[common], help="print AGENTS.md (or --errors for the error table)")
    s.add_argument("--errors", action="store_true")
    s.set_defaults(func=cmd_docs)

    s = sub.add_parser("schema", parents=[common], help="print a JSON Schema, the minimal example, or the full example")
    s.add_argument("name", choices=[*SCHEMAS.keys(), "example", "full-example", "csv"])
    s.set_defaults(func=cmd_schema)

    s = sub.add_parser("plans", parents=[common], help="what a workspace costs (no token needed)")
    s.set_defaults(func=cmd_plans)

    s = sub.add_parser("workspace", parents=[common], help="the paid workspace: status, checkout, push")
    wsub = s.add_subparsers(dest="workspace_command", required=True)
    w = wsub.add_parser("status", parents=[common], help="is this workspace active, and can it accept comparisons")
    w.set_defaults(func=cmd_workspace_status)
    w = wsub.add_parser("checkout", parents=[common], help="get a link for a person to approve; this cannot buy anything itself")
    w.add_argument("--plan", default="workspace", help="which plan (default: workspace)")
    w.set_defaults(func=cmd_workspace_checkout)
    w = wsub.add_parser("push", parents=[common], help="push one finished comparison into the workspace")
    w.add_argument("run", help="a run id, a run directory, or a bundle.json")
    w.set_defaults(func=cmd_workspace_push)

    s = sub.add_parser("version", parents=[common], help="print the version")
    s.set_defaults(func=cmd_version)
    return p


REVIEW_COMMANDS = ["init", "onboard", "doctor", "verify-hook", "verify-adapter", "run", "import", "example", "findings", "case",
                   "check save", "check run", "check list", "check rm", "report", "share", "runs"]
COMMANDS = [*ledger_cli.LEDGER_COMMANDS, "doctor", *[f"review {c}" for c in REVIEW_COMMANDS], "mcp", "docs", "schema", "version", "plans", "workspace status", "workspace checkout", "workspace push"]
RELEASE_INIT_FLAGS = ["--project", "--task", "--adapter", "--model-code", "--dataset", "--dataset-name", "--kind", "--iterations", "--device"]
RELEASE_INIT_SWITCHES = ["--small", "--demo", "--force"]
DESCRIPTION = ("Research state for ML work done with coding agents: claims with criteria, settings with sources, evidence with receipts. "
               "Agents propose; rb checks and computes verdicts; you decide. Built in: release review for iterative perception models. "
               "Docs for agents and humans: rb docs.")

HELP_GROUPS = [
    ("Research state", ["init", "question", "hypothesis", "assumption", "experiment", "variant", "metric", "spec", "claim", "evidence",
                        "freeze", "decide", "retract", "status", "show", "compare", "log", "context", "doctor"]),
    ("Release review (built in; its runs attach as evidence)", ["review"]),
    ("For agents", ["mcp"]),
    ("Reference", ["docs", "schema", "version"]),
    ("Account (optional; talks to rabbitbrain.ai)", ["plans", "workspace"]),
]


class _Skip:
    """Registers subparsers on `sub` except the named ones, which the top level defines itself."""

    def __init__(self, sub, skip: set[str]) -> None:
        self.sub, self.skip = sub, skip

    def add_parser(self, name, **kwargs):
        if name in self.skip:
            return argparse.ArgumentParser(add_help=False)
        kwargs["help"] = argparse.SUPPRESS
        return self.sub.add_parser(name, **kwargs)


def print_help(parser: argparse.ArgumentParser) -> None:
    helps = {}
    for action in parser._subparsers._group_actions:
        for ca in action._choices_actions:
            helps[ca.dest] = ca.help
    lines = ["usage: rb <command> [...]", "", DESCRIPTION, ""]
    for title, names in HELP_GROUPS:
        lines.append(f"{title}:")
        for n in names:
            lines.append(f"  {n:<12} {helps.get(n) or ''}")
        lines.append("")
    lines.append("rb <command> --help for its arguments. Start: rb init \"<what you are trying to establish>\", then rb context.")
    print("\n".join(lines))


def cmd_init_router(args: argparse.Namespace, out: "Out") -> int:
    release = any(getattr(args, f.lstrip("-").replace("-", "_"), None) not in (None, False) for f in RELEASE_INIT_FLAGS + RELEASE_INIT_SWITCHES)
    if release:
        if args.title:
            raise RBError("E_USAGE", message="rb init takes a title for research state, or release-review flags (--project, --demo, ...), not both.")
        print("rb init: this form is now rb review init; it keeps working.", file=sys.stderr)
        return cmd_init(args, out)
    return ledger_cli.cmd_init(args, out)


def cmd_doctor_router(args: argparse.Namespace, out: "Out") -> int:
    from .ledger import Ledger, find_root
    if args.checkpoint or find_root() is None:
        return cmd_doctor(args, out)
    return ledger_cli.cmd_research_doctor(args, out, Ledger.open())


def main(argv: Optional[list[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    json_mode = "--json" in argv
    parser = build_parser()
    if not argv or argv[0] in ("-h", "--help"):
        print_help(parser)
        return EXIT_OK
    if argv[0] == "mcp" and not {"-h", "--help"} & set(argv):
        try:
            args = parser.parse_args(argv)
        except (SystemExit, UsageError) as exc:
            print(f"rb mcp: {getattr(exc, 'message', exc)}", file=sys.stderr)
            return EXIT_INVALID
        from .mcp import serve
        return serve(agent=args.agent)   # stdout is the protocol: nothing else may print there
    if argv[0] in PLANNED:
        out = Out(argv[0], json_mode)
        err = RBError("E_NOT_AVAILABLE", message=f"`rb {argv[0]}` is not part of rabbit-brain {__version__}.", fix=PLANNED[argv[0]])
        if not json_mode:
            print(f"rb {argv[0]}: {err.message} {err.fix}", file=sys.stderr)
        out.emit(ok=False, errors=[err.to_dict()])
        return EXIT_INVALID
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:  # --help printed and exited
        return int(exc.code) if isinstance(exc.code, int) else EXIT_INVALID
    except UsageError as exc:
        command = " ".join(a for a in argv[:2] if not a.startswith("-")) or "rb"
        err = RBError("E_USAGE", message=exc.message)
        if json_mode:
            Out(command, True).emit(ok=False, errors=[err.to_dict()])
        else:
            print(exc.usage.rstrip("\n"), file=sys.stderr)
            print(f"rb {command}: {exc.message}", file=sys.stderr)
        return EXIT_INVALID
    in_review = args.command == "review"
    if (args.command == "check" or (in_review and args.review_command == "check")) and not getattr(args, "check_command", None):
        parser.parse_args([*(["review"] if in_review else []), "check", "--help"])
        return EXIT_OK
    parts = [args.command] + ([args.review_command] if in_review else [])
    if getattr(args, "check_command", None):
        parts.append(args.check_command)
    elif getattr(args, "sub_command", None):
        parts.append(args.sub_command)
    command = " ".join(parts)
    out = Out(command, getattr(args, "json", False))
    out.command_line = shlex.join(["rb", *argv])  # quoted where needed, so the receipt's command pastes back
    out.runs_dir_flag = getattr(args, "runs_dir", None)
    func: Callable = args.func
    verbose = getattr(args, "verbose", False)
    caught: list = []
    try:
        with warnings.catch_warnings(record=not verbose) as rec:
            if not verbose:
                warnings.simplefilter("always")
            try:
                code = func(args, out)
            finally:
                if rec:
                    caught.extend(rec)
                    kinds = sorted({f"{w.category.__name__} from {Path(str(w.filename)).name}" for w in rec})
                    print(f"rb {command}: {len(rec)} warning(s) from the model code and libraries hidden ({'; '.join(kinds[:4])}{'; ...' if len(kinds) > 4 else ''}); --verbose shows them", file=sys.stderr)
        out.emit(ok=code in (EXIT_OK, EXIT_CHECK_FAILED))
        return code
    except RBError as err:
        handoff = None
        if err.code == "E_HUMAN_ONLY":
            handoff = shlex.join(["rb", *(a for a in argv if a != "--json")])  # the line the person runs: text, not an envelope
            err.extra["handoff"] = {"who": "person", "command": handoff}
        if handoff:
            err.fix = f"Hand this to the person, to run in their own terminal: {handoff}   Do not set or unset RB_ACTOR."
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
