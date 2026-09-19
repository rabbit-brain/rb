"""Plain-language findings, worded exactly as the workspace shows them, so the CLI, the report and the browser agree."""
from __future__ import annotations

from .fmt import pct, plain, to_fixed
from .models import CaseV1, Limits
from .stability import EPS, borderline_scan, delta, error_outcome, is_flagged, is_settled, side_stats, stability_outcome, trajectory_change, trajectory_regressed


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
    stats = side_stats(case, "candidate")
    base = side_stats(case, "baseline")
    regression = error_outcome(case, limits.max_regression) == "regression"
    if trajectory_regressed(case, limits) and is_settled(stats, limits):
        d = trajectory_change(case)
        text = (f"The candidate was still moving its answer by {stats.late_update:.3f} per iteration at the end, {d:.3f} more than the current model "
                f"on this case ({base.late_update:.3f}), above your {plain(limits.max_trajectory_regression)} limit.")
        if regression:
            text += " The error regressed as well."
        elif error_outcome(case, limits.max_regression) == "not_measured":
            text += " No ground truth here, so this is the only regression signal for this case."
        else:
            text += " The error looks fine; the answer is not settled."
        return text
    if not is_settled(stats, limits):
        over_last = limits.max_last_update is not None and stats.last_update is not None and stats.last_update > limits.max_last_update + EPS
        over_v1 = stats.late_share > limits.max_late_share + EPS or stats.reversals > limits.max_reversals
        if over_last and not over_v1:
            text = f"The candidate was still moving its answer by {stats.last_update:.3f} per iteration at the end, above your {plain(limits.max_last_update)} limit"
            if base and base.last_update is not None:
                text += f" (current model: {base.last_update:.3f})"
            text += "."
        else:
            over_late = stats.late_share > limits.max_late_share + EPS
            over_rev = stats.reversals > limits.max_reversals
            share = share_text(stats.late_share, limits.max_late_share)
            times = f"{stats.reversals} time{'' if stats.reversals == 1 else 's'}"
            if over_late and over_rev:
                text = f"The candidate made {share} of its refinement in the last third of its iterations and its update grew again {times}, above your {pct(limits.max_late_share)} and {limits.max_reversals}-reversal limits."
            elif over_late:
                text = f"The candidate made {share} of its refinement in the last third of its iterations, above your {pct(limits.max_late_share)} limit"
                text += f", and its update grew again {times} (within your limit of {limits.max_reversals})." if stats.reversals > 0 else "."
            else:
                text = f"The candidate's update grew again {times}, above your limit of {limits.max_reversals} reversals, and it made {share} of its refinement in the last third of its iterations (limit {pct(limits.max_late_share)})."
            text += " " + current_model_sentence(base, limits)
        if error_outcome(case, limits.max_regression) == "not_measured":
            text += " No ground truth here, so the trajectory is the only signal for this case."
        elif not regression:
            text += " The error looks fine; the answer is not settled."
        return text
    text = f"The candidate settled: {pct(stats.late_share)} late revision, {stats.reversals} reversal{'' if stats.reversals == 1 else 's'}."
    if base:
        text += " " + current_model_sentence(base, limits, short=True)
    if regression:
        text += " It converged to a worse answer: a data or training gap rather than instability."
    return text


def share_text(share: float, limit: float) -> str:
    """A late share as a percentage; one decimal when it sits within a point of the limit, so "25% ... above your 25% limit" cannot happen."""
    if abs(share - limit) < 0.01:
        return f"{share * 100:.1f}%"
    return pct(share)


def current_model_sentence(base, limits: Limits, short: bool = False) -> str:
    """How the current model did on the same case, never calling an over-limit trajectory 'settled'."""
    if base is None:
        return ""
    if is_settled(base, limits):
        return f"Current model: {pct(base.late_share)}." if short else f"The current model settled at {pct(base.late_share)}."
    detail = f"{pct(base.late_share)} late revision, {base.reversals} reversal{'' if base.reversals == 1 else 's'}"
    return f"Current model: {detail}, not settled either." if short else f"The current model is not settled on this case either ({detail})."


OUTCOME_WORDS = {"regression": "a regression", "improved": "improved", "stable": "within tolerance", "settled": "settled", "unstable": "unstable"}
LIMIT_WORDS = {
    "max_regression": "the error limit",
    "max_late_share": "the late-revision limit",
    "max_reversals": "the reversal limit",
    "max_trajectory_regression": "the late-movement limit",
    "max_last_update": "the last-update limit",
}


def borderline_sentence(case: CaseV1, limits: Limits) -> str:
    """Said only when it is true, and about the thing that actually moves: an error regression stays flagged
    whatever its trajectory does, so for that case the fragile part is the stability wording, not the flag."""
    names, moves, alts = borderline_scan(case, limits)
    if not names:
        return ""
    which = " and ".join(LIMIT_WORDS[n] for n in names)
    margin = "One reversal" if names == ["max_reversals"] else "A tenth of the limit"
    if "flag" in moves:
        tail = "may not flag it" if is_flagged(case, limits) else "may flag it"
    else:
        key = "stability" if "stability" in moves else "error"
        tail = f"may call it {OUTCOME_WORDS.get(alts[key], alts[key])} instead"
    return f"Borderline: {margin} either way on {which} changes this, so the same checkpoints on another GPU or torch build {tail}."


def why(case: CaseV1, limits: Limits, unit: str) -> str:
    return " ".join(s for s in (error_sentence(case, limits, unit), stability_sentence(case, limits), borderline_sentence(case, limits)) if s)


DEFINITIONS = (
    "A case is a regression when the candidate's error exceeds the current model's by more than {max_regression} {unit}. "
    "Late share is the fraction of all refinement that happened in the last third of the iterations; a reversal is an iteration "
    "where the update grew by more than 5% over the previous one. A case is unstable when late share exceeds {late} or reversals "
    "exceed {reversals}. Improved-but-unstable cases pass on error and still need a look."
)
LAST_UPDATE_DEFINITION = " A case is also unstable when the final update is larger than {last_update} (the model was still moving its answer when it stopped)."
TRAJECTORY_REGRESSION_DEFINITION = (" A case is a trajectory regression when the candidate's late movement (mean update over the last quarter of iterations) "
                                    "exceeds the current model's on the same case by more than {limit}; it counts as unstable, needs no ground truth, and is the regression test for unlabeled cases.")


def definitions(limits: Limits, unit: str) -> str:
    text = DEFINITIONS.format(max_regression=plain(limits.max_regression), unit=unit, late=pct(limits.max_late_share), reversals=limits.max_reversals)
    if limits.max_last_update is not None:
        text += LAST_UPDATE_DEFINITION.format(last_update=plain(limits.max_last_update))
    if limits.max_trajectory_regression is not None:
        text += TRAJECTORY_REGRESSION_DEFINITION.format(limit=plain(limits.max_trajectory_regression))
    return text
