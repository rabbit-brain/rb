"""`report.md`: the receipt a human reads. The core is byte-identical to the workspace's report; the receipt adds provenance and how to reproduce."""
from __future__ import annotations

from typing import Optional, Sequence, Union

from . import __version__
from .checks import evaluate_check
from .fmt import pct, plain, to_fixed
from .models import Bundle, CheckV2, ComparisonV1, Findings, Limits, Record
from .stability import SummaryNumbers, borderline, delta, error_outcome, is_flagged, rank, side_stats, stability_outcome, trajectory_stats


def _metric_parts(run: Union[ComparisonV1, Bundle]) -> tuple[str, str]:
    if isinstance(run, Bundle):
        return run.metric.name, run.metric.unit
    m = run.metric_obj()
    return m.name, m.unit


def _names(run: Union[ComparisonV1, Bundle]) -> tuple[str, str, str]:
    if isinstance(run, Bundle):
        return run.baseline.name, run.candidate.name, run.dataset.name
    return run.baseline, run.candidate, run.dataset


def report_core(run: Union[ComparisonV1, Bundle], limits: Limits, checks: Sequence[CheckV2] = (), max_rows: int = 40) -> str:
    """Exactly the workspace's reportMarkdown() for up to `max_rows` cases; larger case sets list the flagged cases and the
    top of the queue, and say how many more there are (a 200-row table is not a readable receipt)."""
    s = SummaryNumbers(run.cases, limits)
    with_traj = s.with_trajectories > 0
    metric_name, unit = _metric_parts(run)
    baseline, candidate, dataset = _names(run)

    def row(c) -> str:
        stab = trajectory_stats(c.candidate_trajectory) if c.candidate_trajectory else None
        d = delta(c)
        fmt = lambda v: "n/a" if v is None else to_fixed(v)
        base = f"| {c.id} | {fmt(c.baseline_error)} | {fmt(c.candidate_error)} | {fmt(d)} | {error_outcome(c, limits.max_regression).replace('_', ' ')} |"
        if not with_traj:
            return base
        so = stability_outcome(c, limits)
        so_text = "not assessed" if so == "not_assessed" else so
        return f"{base} {f'{pct(stab.late_share)} · {stab.reversals} rev.' if stab else 'n/a'} | {so_text}{' (borderline)' if borderline(c, limits) else ''} |"

    if run.source == "example":
        source_line = "Illustrative example data. No model inference was performed.\n\n"
    elif run.source == "run":
        source_line = "Evaluated by rb run: both checkpoints were run on the case set and the trajectories recorded by the adapter.\n\n"
    else:
        source_line = "Imported evaluation results.\n\n"
    extra_limits = ""
    if limits.max_trajectory_regression is not None:
        extra_limits += f", late movement above the current model's by more than {plain(limits.max_trajectory_regression)} {unit} (trajectory regression)"
    if limits.max_last_update is not None:
        extra_limits += f", final update ≤ {plain(limits.max_last_update)} {unit}"
    stability_line = (
        f"Stability limits: late revision ≤ {pct(limits.max_late_share)}, reversals ≤ {limits.max_reversals}{extra_limits}. {s.unstable} of {s.with_trajectories} cases with trajectories are unstable; {s.unstable_passing} of those pass on error.\n"
        if with_traj else "No refinement trajectories were exported, so stability was not assessed.\n"
    )
    header = f"| Case | Current ({unit}) | Candidate ({unit}) | Change ({unit}) | Error |{' Candidate late revision | Stability |' if with_traj else ''}"
    divider = f"|---|---:|---:|---:|---|{'---|---|' if with_traj else ''}"
    ranked = rank(run.cases, limits)
    near = [c for c in ranked if borderline(c, limits)]
    borderline_line = ""
    if near:
        listed = ", ".join(c.id for c in near[:12]) + (f" and {len(near) - 12} more" if len(near) > 12 else "")
        n_flagged = sum(1 for c in near if is_flagged(c, limits))
        one = len(near) == 1
        count = f"1 case ({listed}), {'flagged' if n_flagged else 'not flagged'}, turns" if one else f"{len(near)} cases ({listed}), {n_flagged} of them flagged, turn"
        borderline_line = (
            f"Borderline: {count} "
            "on a margin of less than a tenth of a limit (one reversal, for the reversal limit). "
            "The same checkpoints on another GPU or torch build give per-case values that differ by about that much, "
            "so a re-run elsewhere may sort these cases the other way; the environment line above says which machine this was.\n"
        )
    shown = ranked if len(ranked) <= max_rows else ranked[:max(max_rows, s.flagged)]  # every flagged case, then the top of the rest
    rows = "\n".join(row(c) for c in shown)
    if len(shown) < len(ranked):
        rows += f"\n\n{len(ranked) - len(shown)} more cases, none of them flagged: all cases are in bundle.json and `rb findings <run> --filter all`."
    check_lines = "\n".join(
        f"- {c.case_id}: candidate error ≤ {to_fixed(c.max_error)} {unit}{f', late revision ≤ {pct(c.max_late_share)}' if c.max_late_share is not None else ''}{f', reversals ≤ {c.max_reversals}' if c.max_reversals is not None else ''}: {evaluate_check(c, run, unit).status}"
        for c in checks
    ) or "No saved checks for this project."
    return (
        f"# {run.project}: model comparison\n\n{source_line}Current: {baseline}\nCandidate: {candidate}\nDataset: {dataset}\nMetric: {metric_name} ({unit}), lower is better.\n\n"
        f"Mean of case errors: {to_fixed(s.baseline)} → {to_fixed(s.candidate)} {unit}. Cases are weighted equally; this is not a pooled per-pixel mean.\n"
        f"Regression threshold: increase greater than {plain(limits.max_regression)} {unit}.\n{s.regressions} of {len(run.cases)} cases regress on error.\n{f'{s.not_measured} of {len(run.cases)} cases have no ground truth; their error was not measured.' + chr(10) if s.not_measured else ''}{stability_line}{borderline_line}\n"
        f"{header}\n{divider}\n{rows}\n\n## Saved checks\n{check_lines}\n\n"
        + (f"## Limits not checked\n\nThis run's limits also carry {', '.join(f'`{n}`' for n in limits.unenforced_limits())}, "
           f"which this version records but does not enforce. Nothing above tests {'them' if len(limits.unenforced_limits()) > 1 else 'it'}, "
           f"and the verdict does not account for {'them' if len(limits.unenforced_limits()) > 1 else 'it'}.\n\n"
           if limits.unenforced_limits() else "")
        + "The check runner evaluates these supplied metrics and trajectories against the limits. It does not run inference or certify a model for deployment.\n"
    )


def _quantile(xs: list[float], q: float) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(q * (len(xs) - 1))))]


def convergence_section(bundle: Bundle, limits: Limits) -> str:
    """Where the limits sit against this case set: per model, the distribution of the statistics the limits read
    plus the paper's absolute ones. Only for runs that recorded trajectories."""
    unit = bundle.metric.unit
    rows = []
    for side, name in (("baseline", bundle.baseline.name), ("candidate", bundle.candidate.name)):
        stats = [t for t in (side_stats(c, side) for c in bundle.cases) if t is not None]
        if not stats:
            continue
        cols = [f"| {name} | {len(stats)} "]
        for key, fmt in (("late_share", "pct"), ("reversals", "int"), ("last_update", "num"), ("late_to_early", "num"), ("sign_reversal_rate", "pct"), ("displacement_mean", "num")):
            vals = [getattr(t, key) for t in stats if getattr(t, key) is not None]
            if not vals:
                cols.append("| n/a ")
                continue
            p50, p90, mx = _quantile(vals, 0.5), _quantile(vals, 0.9), max(vals)
            if fmt == "pct":
                cols.append(f"| {pct(p50)} / {pct(p90)} / {pct(mx)} ")
            elif fmt == "int":
                cols.append(f"| {int(p50)} / {int(p90)} / {int(mx)} ")
            else:
                cols.append(f"| {p50:.3f} / {p90:.3f} / {mx:.3f} ")
        rows.append("".join(cols) + "|")
    if not rows:
        return ""
    limit_line = f"Limits in force: late share ≤ {pct(limits.max_late_share)}, reversals ≤ {limits.max_reversals}, trajectory regression " + (f"> {plain(limits.max_trajectory_regression)} {unit} late movement over the current model" if limits.max_trajectory_regression is not None else "off (`max_trajectory_regression` in rb.toml)") + ", last update " + (f"≤ {plain(limits.max_last_update)} {unit}" if limits.max_last_update is not None else "not limited") + "."
    return (
        "## Convergence on this case set\n\n"
        "Median / 90th percentile / max per model. Late share and reversals are what the stability limits read; a reversal is an "
        "iteration whose update grew by more than 5% over the previous one, which is a size, not a direction. Last update, "
        "late-to-early ratio, direction reversals (the separate measure: share of consecutive updates pointing in opposite ways) "
        "and the mean distance of intermediate estimates from the final one are the absolute statistics from the update fields, in the trajectory's unit.\n\n"
        f"| Model | Cases | Late share | Reversals | Last update ({unit}) | Late/early | Direction reversals | Distance from final ({unit}) |\n"
        "|---|---:|---|---|---|---|---|---|\n" + "\n".join(rows) + f"\n\n{limit_line}\n\n"
    )


def agreement_line(agreement: dict, unit: str) -> str:
    """One line per run: did the adapter reproduce the model repository's own evaluation, on how many cases, how closely."""
    parts = []
    for role in ("baseline", "candidate"):
        a = agreement.get(role) or {}
        status = a.get("status")
        if status == "agree":
            parts.append(f"{role} agrees with the reference evaluation on {a['cases']} cases (max |diff| {a['max_abs_diff']:.2g} {unit})")
        elif status == "disagree":
            parts.append(f"{role} DISAGREES with the reference evaluation on {len(a['disagreeing'])} of {a['cases']} cases")
        elif status == "skipped":
            parts.append(f"{role} not checked ({a.get('note', 'skipped')})")
        elif status == "not_available":
            parts.append(f"{role} not established (no reference path in the adapter)")
    ref = (agreement.get("baseline") or {}).get("reference") or (agreement.get("candidate") or {}).get("reference")
    return "; ".join(parts) + (f". Reference: {ref}." if ref else ".")


def report_markdown(bundle: Bundle, record: Optional[Record], findings: Findings, checks: Sequence[CheckV2] = ()) -> str:
    """The receipt: provenance header, verdict, the workspace-identical core, and how to reproduce."""
    core = report_core(bundle, findings.limits, checks)
    title, _, body = core.partition("\n\n")
    prov: list[str] = []
    if record is not None:
        prov.append(f"Run: {record.run_id} · rb {record.rb_version} · finished {record.finished}")
        if record.rb_version != __version__:
            prov.append(f"Report rendered by rb {__version__} from this run's bundle.json and record.json; the numbers are the run's, the wording and any later annotations are this version's.")
        prov.append(f"Command: `{record.command}`")
        if record.input and record.input.get("path"):
            sha = record.input.get("sha256") or ""
            prov.append(f"Input: {record.input['path']} (sha256 {sha[:16]}…)" if sha else f"Input: {record.input['path']}")
        for role in ("baseline", "candidate"):
            ref = record.checkpoints.get(role)
            if ref and (ref.checkpoint or ref.sha256):
                prov.append(f"{role.capitalize()} checkpoint: {ref.checkpoint or ''}{f' ({ref.architecture})' if getattr(ref, 'architecture', None) else ''}{f' (sha256 {ref.sha256[:16]}…)' if ref.sha256 else ''}")
        archs = {r: getattr(record.checkpoints.get(r), "architecture", None) for r in ("baseline", "candidate")}
        if archs["baseline"] and archs["candidate"] and archs["baseline"] != archs["candidate"]:
            prov.append(f"Note: the two checkpoints are different architectures ({archs['baseline']} vs {archs['candidate']}); this review compares two models, not a retrain of one.")
        env = record.environment or {}
        env_line = f"Environment: python {env.get('python', '?')} · {env.get('platform', '?')}"
        if env.get("torch"):
            env_line += f" · torch {env['torch']}" + (f" · cuda {env['cuda']} · {env['gpu']}" if env.get("gpu") else " · cpu")
        prov.append(env_line)
        if record.model_code and record.model_code.get("path"):
            mc = record.model_code
            prov.append(f"Model code: {mc['path']}" + (f" @ {mc['sha'][:12]}{' (uncommitted changes)' if mc.get('dirty') else ''}" if mc.get("sha") else " (not a git checkout; no commit to pin)"))
        if record.dataset and getattr(record.dataset, "content_hash", None):
            prov.append(f"Dataset: {record.dataset.name}, {record.dataset.count} cases, content sha256 {record.dataset.content_hash[:16]}…")
        hook = record.hook or {}
        prov.append(f"Trajectories: {hook.get('status', 'unknown')}{'. ' + hook['note'] if hook.get('note') else ''}")
        if record.notes:
            prov.append(f"Notes: {record.notes}")
        if record.adapter_agreement:
            prov.append("Adapter agreement: " + agreement_line(record.adapter_agreement, bundle.metric.unit))
    else:
        prov.append(f"Run: {bundle.run_id} (read in place, no record)")
    verdict = f"## Verdict\n\n{findings.verdict.line}\n"
    reproduce = (
        "## Reproduce\n\n"
        f"- Ranked queue under the same limits: `rb findings {bundle.run_id} --max-regression {plain(findings.limits.max_regression)} --max-late-share {plain(findings.limits.max_late_share)} --max-reversals {findings.limits.max_reversals}"
        + (f" --max-trajectory-regression {plain(findings.limits.max_trajectory_regression)}" if findings.limits.max_trajectory_regression is not None else "")
        + (f" --max-last-update {plain(findings.limits.max_last_update)}" if findings.limits.max_last_update is not None else "") + "`\n"
        f"- One case with its evidence and reasoning: `rb case {bundle.run_id} <case_id>`\n"
        f"- Saved checks against this run: `rb check run {bundle.run_id} --checks checks.json`\n"
        "- Definitions: `rb docs`. Schemas: `rb schema bundle|findings|checks|record`.\n"
    )
    convergence = convergence_section(bundle, findings.limits) if bundle.source != "example" else ""   # any run with trajectories, imported ones included
    with_evidence = [c for c in bundle.cases if c.evidence]
    evidence = ""
    if with_evidence:
        evidence = "## Evidence\n\n" + "\n".join(f"- {c.id}: `{c.evidence.dir}/case.png`" for c in with_evidence) + "\n\nEach case.png stacks the inputs, both flow fields with ground truth when present, the error maps, the per-iteration filmstrips and the trajectory plot; the caption says whether re-running the case reproduced the run's numbers. `rb case <run> <case_id> --render` makes one for any case.\n\n"
    return f"{title}\n\n" + "\n".join(prov) + "\n\n" + verdict + "\n" + body + "\n" + convergence + evidence + reproduce
