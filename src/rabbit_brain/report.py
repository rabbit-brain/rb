"""`report.md`: the receipt a human reads. The core is byte-identical to the workspace's report; the receipt adds provenance and how to reproduce."""
from __future__ import annotations

from typing import Optional, Sequence, Union

from .checks import evaluate_check
from .fmt import pct, plain, to_fixed
from .models import Bundle, CheckV2, ComparisonV1, Findings, Limits, Record
from .stability import SummaryNumbers, delta, error_outcome, rank, stability_outcome, trajectory_stats


def _metric_parts(run: Union[ComparisonV1, Bundle]) -> tuple[str, str]:
    if isinstance(run, Bundle):
        return run.metric.name, run.metric.unit
    m = run.metric_obj()
    return m.name, m.unit


def _names(run: Union[ComparisonV1, Bundle]) -> tuple[str, str, str]:
    if isinstance(run, Bundle):
        return run.baseline.name, run.candidate.name, run.dataset.name
    return run.baseline, run.candidate, run.dataset


def report_core(run: Union[ComparisonV1, Bundle], limits: Limits, checks: Sequence[CheckV2] = ()) -> str:
    """Exactly the workspace's reportMarkdown()."""
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
        return f"{base} {f'{pct(stab.late_share)} · {stab.reversals} rev.' if stab else 'n/a'} | {so_text} |"

    source_line = "Illustrative example data. No model inference was performed.\n\n" if run.source == "example" else "Imported evaluation results.\n\n"
    stability_line = (
        f"Stability limits: late revision ≤ {pct(limits.max_late_share)}, reversals ≤ {limits.max_reversals}. {s.unstable} of {s.with_trajectories} cases with trajectories are unstable; {s.unstable_passing} of those pass on error.\n"
        if with_traj else "No refinement trajectories were exported, so stability was not assessed.\n"
    )
    header = f"| Case | Current ({unit}) | Candidate ({unit}) | Change ({unit}) | Error |{' Candidate late revision | Stability |' if with_traj else ''}"
    divider = f"|---|---:|---:|---:|---|{'---|---|' if with_traj else ''}"
    rows = "\n".join(row(c) for c in rank(run.cases, limits))
    check_lines = "\n".join(
        f"- {c.case_id}: candidate error ≤ {to_fixed(c.max_error)} {unit}{f', late revision ≤ {pct(c.max_late_share)}' if c.max_late_share is not None else ''}{f', reversals ≤ {c.max_reversals}' if c.max_reversals is not None else ''}: {evaluate_check(c, run, unit).status}"
        for c in checks
    ) or "No saved checks for this project."
    return (
        f"# {run.project}: model comparison\n\n{source_line}Current: {baseline}\nCandidate: {candidate}\nDataset: {dataset}\nMetric: {metric_name} ({unit}), lower is better.\n\n"
        f"Mean of case errors: {to_fixed(s.baseline)} → {to_fixed(s.candidate)} {unit}. Cases are weighted equally; this is not a pooled per-pixel mean.\n"
        f"Regression threshold: increase greater than {plain(limits.max_regression)} {unit}.\n{s.regressions} of {len(run.cases)} cases regress on error.\n{f'{s.not_measured} of {len(run.cases)} cases have no ground truth; their error was not measured.' + chr(10) if s.not_measured else ''}{stability_line}\n"
        f"{header}\n{divider}\n{rows}\n\n## Saved checks\n{check_lines}\n\n"
        "The check runner evaluates these supplied metrics and trajectories against the limits. It does not run inference or certify a model for deployment.\n"
    )


def report_markdown(bundle: Bundle, record: Optional[Record], findings: Findings, checks: Sequence[CheckV2] = ()) -> str:
    """The receipt: provenance header, verdict, the workspace-identical core, and how to reproduce."""
    core = report_core(bundle, findings.limits, checks)
    title, _, body = core.partition("\n\n")
    prov: list[str] = []
    if record is not None:
        prov.append(f"Run: {record.run_id} · rb {record.rb_version} · finished {record.finished}")
        prov.append(f"Command: `{record.command}`")
        if record.input and record.input.get("path"):
            sha = record.input.get("sha256") or ""
            prov.append(f"Input: {record.input['path']} (sha256 {sha[:16]}…)" if sha else f"Input: {record.input['path']}")
        for role in ("baseline", "candidate"):
            ref = record.checkpoints.get(role)
            if ref and (ref.checkpoint or ref.sha256):
                prov.append(f"{role.capitalize()} checkpoint: {ref.checkpoint or ''}{f' (sha256 {ref.sha256[:16]}…)' if ref.sha256 else ''}")
        env = record.environment or {}
        prov.append(f"Environment: python {env.get('python', '?')} · {env.get('platform', '?')}")
        hook = record.hook or {}
        prov.append(f"Trajectories: {hook.get('status', 'unknown')}{'. ' + hook['note'] if hook.get('note') else ''}")
    else:
        prov.append(f"Run: {bundle.run_id} (read in place, no record)")
    verdict = f"## Verdict\n\n{findings.verdict.line}\n"
    reproduce = (
        "## Reproduce\n\n"
        f"- Ranked queue under the same limits: `rb findings {bundle.run_id} --max-regression {plain(findings.limits.max_regression)} --max-late-share {plain(findings.limits.max_late_share)} --max-reversals {findings.limits.max_reversals}`\n"
        f"- One case with its evidence and reasoning: `rb case {bundle.run_id} <case_id>`\n"
        f"- Saved checks against this run: `rb check run {bundle.run_id} --checks checks.json`\n"
        "- Definitions: `rb docs`. Schemas: `rb schema bundle|findings|checks|record`.\n"
    )
    return f"{title}\n\n" + "\n".join(prov) + "\n\n" + verdict + "\n" + body + "\n" + reproduce
