"""The fixtures both implementations have to agree on.

Built to sit on the edges rather than in the middle: deltas exactly at the error limit and a
hair either side, late shares at the limit, reversals at and one over, trajectories short
enough that the quarter window rounds to one iteration, an all-zero trajectory whose total is
zero, and every limit switched on and off. A corpus of comfortable cases would agree by luck.
"""
from __future__ import annotations

from rabbit_brain.models import Bundle, CaseV1, CheckV2, ChecksV2, DatasetRef, Limits, Metric, ModelRef
from rabbit_brain.runs import rederive

METRIC = Metric(id="mean_endpoint_error", name="mean endpoint error", unit="px", lower_is_better=True)

SETTLED = [4.0, 2.0, 1.0, 0.5, 0.25, 0.1]          # late share 0.09, no reversals
LATE = [0.2, 0.2, 0.2, 1.0, 1.0, 1.0]              # late share 0.44, one reversal
REVERSING = [1.0, 2.0, 1.0, 2.0, 1.0, 2.0]         # three reversals, late share 0.33
FLAT = [1.0, 1.0, 1.0, 1.0]                        # late share 0.25 exactly, no reversals
ZEROS = [0.0, 0.0, 0.0, 0.0]                       # total zero: the divide-by-zero branch
SHORT = [1.0, 0.2]                                 # two iterations: quarter window is one
BIG_TAIL = [0.1, 0.1, 0.1, 0.1, 0.1, 0.9]          # last update 0.9, late update 0.9
LONG = [round(3.0 * (0.72 ** i), 6) for i in range(16)]

# Settled on late share and reversals, and separable by the two paired limits: same shape, four
# scales. A case that is already unstable on its own cannot show whether the last-update or
# trajectory-regression limit was applied, so the corpus needs cases that turn only on those.
SMALL = [4.0, 2.0, 1.0, 0.5]                       # late share 0.2, last update 0.5, late update 0.5
BIG = [8.0, 4.0, 2.0, 1.0]                         # late update 1.0: 0.5 above SMALL
AT_TRAJ = [6.4, 3.2, 1.6, 0.8]                     # late update 0.8: exactly 0.3 above SMALL
AT_LAST = [2.4, 1.2, 0.6, 0.3]                     # last update exactly 0.3


def case(cid, name, be, ce, bt=None, ct=None, tags=()):
    return CaseV1(id=cid, name=name, tags=list(tags), baseline_error=be, candidate_error=ce,
                  baseline_trajectory=bt, candidate_trajectory=ct)


def bundle(run_id, cases, limits, project="conformance"):
    b = Bundle(version=2, run_id=run_id, project=project, source="imported", metric=METRIC,
               baseline=ModelRef(name="current"), candidate=ModelRef(name="candidate"),
               dataset=DatasetRef(name="fixtures", count=len(cases)), limits=limits, cases=[])
    return rederive(b.model_copy(update={"cases": cases}), limits)


def fixtures() -> list[dict]:
    """Each entry: a name, a bundle, and the saved checks to evaluate against it."""
    out: list[dict] = []

    # The error limit, approached from both sides. 0.3 is the default.
    edge = [
        case("exact", "Exactly at the limit", 1.0, 1.3),
        case("just_over", "A hair over", 1.0, 1.3 + 1e-6),
        case("just_under", "A hair under", 1.0, 1.3 - 1e-6),
        case("inside_eps", "Inside the epsilon", 1.0, 1.3 + 1e-12),
        case("improved_exact", "Improved by exactly the limit", 1.3, 1.0),
        case("improved_over", "Improved past it", 1.3, 1.0 - 1e-6),
        case("zero_base", "Zero baseline error", 0.0, 0.0),
    ]
    out.append({"name": "error-limit-edges", "bundle": bundle("edges", edge, Limits()), "checks": None})

    # Stability, with every trajectory shape and both models present.
    traj = [
        case("settled", "Settled", 1.0, 1.0, SETTLED, SETTLED),
        case("late", "Revises late", 1.0, 1.0, SETTLED, LATE),
        case("reversing", "Keeps reopening", 1.0, 1.0, SETTLED, REVERSING),
        case("flat", "Late share exactly at the limit", 1.0, 1.0, FLAT, FLAT),
        case("zeros", "No movement at all", 1.0, 1.0, ZEROS, ZEROS),
        case("short", "Two iterations", 1.0, 1.0, SHORT, SHORT),
        case("long", "Sixteen iterations", 1.0, 1.0, LONG, LONG),
        case("candidate_only", "Only the candidate has a trajectory", 1.0, 1.0, None, LATE),
        case("baseline_only", "Only the current model has one", 1.0, 1.0, SETTLED, None),
        case("none", "Neither has one", 1.0, 1.0, None, None),
        case("tail", "Big final update", 1.0, 1.0, SETTLED, BIG_TAIL),
        case("moved_late", "Settled, but moving more than the current model", 1.0, 1.0, SMALL, BIG),
        case("moved_less", "Settled, and moving less", 1.0, 1.0, BIG, SMALL),
        case("moved_same", "Settled, moving exactly as much", 1.0, 1.0, BIG, BIG),
        case("moved_at_limit", "Exactly at the late-movement limit", 1.0, 1.0, SMALL, AT_TRAJ),
        case("last_at_limit", "Final update exactly at the limit", 1.0, 1.0, SMALL, AT_LAST),
    ]
    out.append({"name": "stability-default-limits", "bundle": bundle("traj", traj, Limits()), "checks": None})

    # The two limits the viewer did not implement until this change.
    out.append({"name": "last-update-limit",
                "bundle": bundle("last", traj, Limits(max_last_update=0.3)), "checks": None})
    out.append({"name": "trajectory-regression-limit",
                "bundle": bundle("trajreg", traj, Limits(max_trajectory_regression=0.3)), "checks": None})
    out.append({"name": "trajectory-regression-zero",
                "bundle": bundle("trajreg0", traj, Limits(max_trajectory_regression=0.0)), "checks": None})
    out.append({"name": "every-limit-at-once",
                "bundle": bundle("all", traj, Limits(max_regression=0.05, max_late_share=0.1, max_reversals=0,
                                                     max_last_update=0.2, max_trajectory_regression=0.1)),
                "checks": None})
    out.append({"name": "limits-wide-open",
                "bundle": bundle("open", traj, Limits(max_regression=1000, max_late_share=1.0, max_reversals=64)),
                "checks": None})

    # Ranking: every priority class, with ties, so the ordering is compared and not just the counts.
    mixed = [
        case("a_improved", "Improved and settled", 5.0, 1.0, SETTLED, SETTLED),
        case("b_regressed", "Regressed but settled", 1.0, 3.0, SETTLED, SETTLED),
        case("c_both", "Regressed and unstable", 1.0, 2.0, SETTLED, LATE),
        case("d_unstable", "Unstable but fine on error", 1.0, 1.0, SETTLED, REVERSING),
        case("e_tie", "Same increase as another", 1.0, 3.0, SETTLED, SETTLED),
        case("f_tie", "Same increase again", 2.0, 4.0, SETTLED, SETTLED),
        case("g_flat", "Nothing to say", 1.0, 1.0, SETTLED, SETTLED),
    ]
    out.append({"name": "ranking", "bundle": bundle("rank", mixed, Limits()), "checks": None})

    # Nothing flagged: the other half of the verdict.
    clean = [case("one", "Fine", 1.0, 0.9, SETTLED, SETTLED), case("two", "Also fine", 2.0, 2.0, SETTLED, SETTLED)]
    out.append({"name": "nothing-flagged", "bundle": bundle("clean", clean, Limits()), "checks": None})
    out.append({"name": "nothing-flagged-no-trajectories",
                "bundle": bundle("cleannt", [case("one", "Fine", 1.0, 0.9), case("two", "Also fine", 2.0, 2.0)], Limits()),
                "checks": None})

    # One case, the smallest run there is.
    out.append({"name": "single-case", "bundle": bundle("single", [case("only", "The only case", 1.0, 9.0, LATE, LATE)], Limits()),
                "checks": None})

    # Saved checks: every status, including the two the viewer could not express before.
    checks = ChecksV2(version=2, project="conformance", checks=[
        CheckV2(case_id="settled", name="Settled", max_error=2.0),
        CheckV2(case_id="late", name="Revises late", max_error=0.5),
        CheckV2(case_id="reversing", name="Keeps reopening", max_error=2.0, max_late_share=0.25),
        CheckV2(case_id="flat", name="At the limit", max_error=2.0, max_late_share=0.25),
        CheckV2(case_id="tail", name="Big final update", max_error=2.0, max_reversals=0),
        CheckV2(case_id="none", name="No trajectory", max_error=2.0, max_late_share=0.25),
        CheckV2(case_id="not_here", name="Not in this run", max_error=2.0),
    ])
    out.append({"name": "checks", "bundle": bundle("checks", traj, Limits()), "checks": checks})
    # Checks saved for another project are not in the corpus: rb scopes the checks file to the run's
    # project before anything evaluates it, so that state never reaches a generated report. The
    # viewer still judges it per check, for the hosted workspace, and a unit test covers the stamp.
    out.append({"name": "checks-on-a-clean-run", "bundle": bundle("cleanchecks", clean, Limits()),
                "checks": ChecksV2(version=2, project="conformance", checks=[
                    CheckV2(case_id="one", name="Fine", max_error=0.01),
                ])})
    return out
