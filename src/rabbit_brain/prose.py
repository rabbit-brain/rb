"""Plain-language findings, worded exactly as the workspace shows them, so the CLI, the report and the browser agree."""
from __future__ import annotations

from .fmt import pct, plain, to_fixed
from .models import CaseV1, Limits
from .stability import delta, error_outcome, is_settled, trajectory_stats


def error_sentence(case: CaseV1, limits: Limits, unit: str) -> str:
    d = delta(case)
    outcome = error_outcome(case, limits.max_regression)
    if outcome == "not_measured" or d is None:
        return "No ground truth for this case, so the error was not measured."
    if outcome == "regression":
        return f"Error increased by {to_fixed(d)} {unit}, exceeding your {plain(limits.max_regression)} {unit} limit."
    if outcome == "improved":
        return f"Error fell by {to_fixed(-d)} {unit} on this case."
    return f"The change stays within your {plain(limits.max_regression)} {unit} tolerance."


def stability_sentence(case: CaseV1, limits: Limits) -> str:
    if not case.candidate_trajectory:
        return "No trajectory was exported for the candidate, so stability is not assessed for this case."
    stats = trajectory_stats(case.candidate_trajectory)
    base = trajectory_stats(case.baseline_trajectory) if case.baseline_trajectory else None
    regression = error_outcome(case, limits.max_regression) == "regression"
    if not is_settled(stats, limits):
        text = f"The candidate made {pct(stats.late_share)} of its refinement in the last third of its iterations"
        if stats.reversals > 0:
            text += f" and reversed direction {stats.reversals} time{'' if stats.reversals == 1 else 's'}"
        text += f", above your {pct(limits.max_late_share)} / {limits.max_reversals} limits."
        if base:
            text += f" The current model settled at {pct(base.late_share)}."
        if not regression:
            text += " The error looks fine; the answer is not settled."
        return text
    text = f"The candidate settled: {pct(stats.late_share)} late revision, {stats.reversals} reversal{'' if stats.reversals == 1 else 's'}."
    if base:
        text += f" Current model: {pct(base.late_share)}."
    if regression:
        text += " It converged to a worse answer: a data or training gap rather than instability."
    return text


def why(case: CaseV1, limits: Limits, unit: str) -> str:
    return f"{error_sentence(case, limits, unit)} {stability_sentence(case, limits)}"


DEFINITIONS = (
    "A case is a regression when the candidate's error exceeds the current model's by more than {max_regression} {unit}. "
    "Late share is the fraction of all refinement that happened in the last third of the iterations; a reversal is an iteration "
    "where the update grew by more than 5% over the previous one. A case is unstable when late share exceeds {late} or reversals "
    "exceed {reversals}. Improved-but-unstable cases pass on error and still need a look."
)


def definitions(limits: Limits, unit: str) -> str:
    return DEFINITIONS.format(max_regression=plain(limits.max_regression), unit=unit, late=pct(limits.max_late_share), reversals=limits.max_reversals)
