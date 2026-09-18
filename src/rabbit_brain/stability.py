"""The arithmetic. One implementation, identical to the workspace (lib/comparison.ts) and to the v5 kit's check.py.

  late_share = sum(updates in the last third of iterations) / sum(all updates)
  reversals  = iterations where the update grew by more than 5% over the previous one
  unstable   = late_share > max_late_share  or  reversals > max_reversals
               or (when max_last_update is set) last_update > max_last_update
  regression = candidate_error - baseline_error > max_regression

A converged model keeps shrinking its updates; one that keeps revising late, or re-opens its
estimate, is unstable on that case whatever the final error says.

The same values also give the paper's convergence statistics (quarter windows, absolute magnitudes):
last_update, late_update (mean over the last quarter), early_update (first quarter), late_to_early.
Runs add direction and displacement statistics computed from the update fields (see recorder.py);
those ride along in `Convergence` and are merged into the stats here.
"""
from __future__ import annotations

from typing import Iterable, Optional, Sequence

from .models import CaseStability, CaseV1, Convergence, Limits, TrajectoryStats

EPS = 1e-9


def trajectory_stats(values: Sequence[float], convergence: Optional[Convergence] = None) -> TrajectoryStats:
    t = [float(v) for v in values]
    total = sum(t)
    start = (len(t) * 2) // 3
    late = sum(t[start:])
    reversals = sum(1 for i in range(1, len(t)) if t[i] > t[i - 1] * 1.05 + EPS)
    peak = 0
    for i in range(1, len(t)):
        if t[i] > t[peak]:
            peak = i
    quarter = max(1, len(t) // 4)
    late_update = sum(t[-quarter:]) / quarter if t else None
    early_update = sum(t[:quarter]) / quarter if t else None
    extra = convergence.model_dump() if convergence is not None else {}
    return TrajectoryStats(
        iterations=len(t), late_share=(late / total) if total > 0 else 0.0, reversals=reversals, peak_iteration=peak, total=total,
        last_update=t[-1] if t else None, late_update=late_update, early_update=early_update,
        late_to_early=(late_update / early_update) if early_update else None,
        **extra,
    )


def late_start(length: int) -> int:
    return (length * 2) // 3


def is_settled(stats: TrajectoryStats, limits: Limits) -> bool:
    if stats.late_share > limits.max_late_share + EPS or stats.reversals > limits.max_reversals:
        return False
    if limits.max_last_update is not None and stats.last_update is not None and stats.last_update > limits.max_last_update + EPS:
        return False
    return True


def measured(case: CaseV1) -> bool:
    return case.baseline_error is not None and case.candidate_error is not None


def delta(case: CaseV1) -> Optional[float]:
    if not measured(case):
        return None
    return case.candidate_error - case.baseline_error


def error_outcome(case: CaseV1, max_regression: float) -> str:
    d = delta(case)
    if d is None:
        return "not_measured"
    if d > max_regression + EPS:
        return "regression"
    if d < -max_regression - EPS:
        return "improved"
    return "stable"


def side_stats(case: CaseV1, side: str) -> Optional[TrajectoryStats]:
    values = getattr(case, f"{side}_trajectory", None)
    if not values:
        return None
    return trajectory_stats(values, getattr(case, f"{side}_convergence", None))


def stability_outcome(case: CaseV1, limits: Limits) -> str:
    stats = side_stats(case, "candidate")
    if stats is None:
        return "not_assessed"
    return "settled" if is_settled(stats, limits) else "unstable"


def case_stability(case: CaseV1) -> CaseStability:
    return CaseStability(baseline=side_stats(case, "baseline"), candidate=side_stats(case, "candidate"))


def flags_for(case: CaseV1, limits: Limits) -> list[str]:
    out: list[str] = []
    e = error_outcome(case, limits.max_regression)
    s = stability_outcome(case, limits)
    if e == "regression":
        out.append("regression")
    elif e == "improved":
        out.append("improved")
    if s == "unstable":
        out.append("unstable")
    base = side_stats(case, "baseline")
    if base is not None and not is_settled(base, limits):
        out.append("baseline_unstable")
    if e == "improved" and s == "unstable":
        out.append("improved_unstable")
    if e == "regression" and s == "settled":
        out.append("settled_regression")
    return out


def is_flagged(case: CaseV1, limits: Limits) -> bool:
    return error_outcome(case, limits.max_regression) == "regression" or stability_outcome(case, limits) == "unstable"


def priority(case: CaseV1, limits: Limits) -> int:
    """Cases that need a decision first: regression+unstable (3), regression (2), unstable (1), rest (0)."""
    regression = error_outcome(case, limits.max_regression) == "regression"
    unstable = stability_outcome(case, limits) == "unstable"
    return 3 if regression and unstable else 2 if regression else 1 if unstable else 0


def rank(cases: Iterable[CaseV1], limits: Limits) -> list[CaseV1]:
    """Priority first, largest error increase within each group. Stable for ties, like the workspace."""
    return sorted(cases, key=lambda c: (-priority(c, limits), 0 if measured(c) else 1, -(delta(c) or 0.0)))


class SummaryNumbers:
    """Plain container so the arithmetic stays free of the JSON models."""

    def __init__(self, cases: Sequence[CaseV1], limits: Limits) -> None:
        n = len(cases)
        labeled = [c for c in cases if measured(c)]
        m = len(labeled)
        self.cases = n
        self.with_gt = m
        self.baseline = sum(c.baseline_error for c in labeled) / m if m else 0.0
        self.candidate = sum(c.candidate_error for c in labeled) / m if m else 0.0
        self.change: Optional[float] = ((self.candidate - self.baseline) / self.baseline * 100) if self.baseline else None
        outcomes = [error_outcome(c, limits.max_regression) for c in cases]
        stabilities = [stability_outcome(c, limits) for c in cases]
        self.regressions = sum(1 for o in outcomes if o == "regression")
        self.improved = sum(1 for o in outcomes if o == "improved")
        self.stable = sum(1 for o in outcomes if o == "stable")
        self.not_measured = sum(1 for o in outcomes if o == "not_measured")
        self.unstable = sum(1 for s in stabilities if s == "unstable")
        self.unstable_passing = sum(1 for o, s in zip(outcomes, stabilities) if s == "unstable" and o not in ("regression", "not_measured"))
        self.unstable_unmeasured = sum(1 for o, s in zip(outcomes, stabilities) if s == "unstable" and o == "not_measured")
        self.improved_unstable = sum(1 for o, s in zip(outcomes, stabilities) if s == "unstable" and o == "improved")
        self.settled_regressions = sum(1 for o, s in zip(outcomes, stabilities) if o == "regression" and s == "settled")
        self.with_trajectories = sum(1 for c in cases if c.candidate_trajectory)
        self.flagged = sum(1 for c in cases if is_flagged(c, limits))


def verdict_text(cases: Sequence[CaseV1], limits: Limits) -> dict:
    """One line an engineer can act on. Same wording as the workspace."""
    s = SummaryNumbers(cases, limits)
    if s.flagged == 0:
        text = (
            "Nothing flagged: no error regressions, every candidate trajectory settled."
            if s.with_trajectories > 0
            else "Nothing flagged: no error regressions. Export trajectories to assess stability too."
        )
        return {"ready": True, "text": text, "start": None, "start_name": None}
    parts: list[str] = []
    if s.regressions:
        parts.append(f"{s.regressions} error regression{'' if s.regressions == 1 else 's'}")
    if s.unstable_passing:
        parts.append(f"{s.unstable_passing} unstable {'case that passes' if s.unstable_passing == 1 else 'cases that pass'} on error")
    if s.unstable_unmeasured:
        parts.append(f"{s.unstable_unmeasured} unstable case{'' if s.unstable_unmeasured == 1 else 's'} without ground truth")
    if not s.unstable_passing and not s.unstable_unmeasured and s.unstable:
        parts.append(f"{s.unstable} unstable case{'' if s.unstable == 1 else 's'}")
    first = rank(cases, limits)[0]
    return {"ready": False, "text": f"Not ready: {', '.join(parts)}.", "start": first.id, "start_name": first.name}
