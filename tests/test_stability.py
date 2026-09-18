"""The arithmetic must agree with the workspace (TypeScript) on the example data, number for number and string for string."""
import math

from rabbit_brain.example import MINIMAL_EXAMPLE, example_comparison
from rabbit_brain.models import CheckV2, Limits
from rabbit_brain.report import report_core
from rabbit_brain.stability import (SummaryNumbers, error_outcome, is_settled, priority, rank, stability_outcome, trajectory_stats, verdict_text)

L = Limits()


def test_example_data_is_identical_to_the_workspace(expected):
    ours = example_comparison().model_dump(exclude_none=True)
    theirs = expected["example"]
    assert ours["project"] == theirs["project"] and ours["metric"] == theirs["metric"] and ours["unit"] == theirs["unit"]
    assert [c["id"] for c in ours["cases"]] == [c["id"] for c in theirs["cases"]]
    for a, b in zip(ours["cases"], theirs["cases"]):
        for key in ("baseline_error", "candidate_error", "baseline_frames", "candidate_frames", "baseline_trajectory", "candidate_trajectory", "tags"):
            assert a.get(key) == b.get(key), (a["id"], key)
        assert a.get("notes") == b.get("notes")


def test_summary_and_ranking(expected):
    cases = example_comparison().cases
    s = SummaryNumbers(cases, L)
    e = expected["summary"]
    assert (s.regressions, s.improved, s.unstable, s.unstable_passing, s.with_trajectories, s.flagged) == (e["regressions"], e["improved"], e["unstable"], e["unstablePassing"], e["withTrajectories"], e["flagged"])
    assert math.isclose(s.baseline, e["baseline"]) and math.isclose(s.candidate, e["candidate"]) and math.isclose(s.change, e["change"])
    assert [c.id for c in rank(cases, L)] == expected["ranked_ids"]
    assert [c.id for c in rank(cases, L)][:5] == ["aisle-042", "crossing-007", "loading-018", "aisle-011", "turn-026"]


def test_per_case_outcomes_and_stats(expected):
    for c in example_comparison().cases:
        e = expected["outcomes"][c.id]
        assert error_outcome(c, L.max_regression) == e["error"]
        assert stability_outcome(c, L) == ("not_assessed" if e["stability"] == "unknown" else e["stability"])
        assert priority(c, L) == e["priority"]
        st = trajectory_stats(c.candidate_trajectory)
        assert math.isclose(st.late_share, e["stats"]["lateShare"]) and st.reversals == e["stats"]["reversals"] and st.peak_iteration == e["stats"]["peakIteration"]
        bs = trajectory_stats(c.baseline_trajectory)
        assert math.isclose(bs.late_share, e["base_stats"]["lateShare"]) and bs.reversals == e["base_stats"]["reversals"]


def test_signature_cases():
    by_id = {c.id: c for c in example_comparison().cases}
    assert stability_outcome(by_id["aisle-042"], L) == "unstable"    # regression + unstable
    assert stability_outcome(by_id["loading-018"], L) == "settled"   # regression, confidently worse
    assert stability_outcome(by_id["aisle-011"], L) == "unstable"    # improved on error, unstable
    assert error_outcome(by_id["aisle-011"], L.max_regression) == "improved"
    assert stability_outcome(by_id["turn-026"], L) == "unstable"
    assert stability_outcome(by_id["pallet-003"], L) == "settled"
    assert stability_outcome(by_id["pallet-003"].model_copy(update={"candidate_trajectory": None}), L) == "not_assessed"


def test_stats_edge_cases():
    settled = trajectory_stats([2.4, 1.5, .9, .6, .4, .3, .2, .15, .1, .08, .06, .05])
    assert settled.late_share < 0.06 and settled.reversals == 0 and is_settled(settled, L)
    late = trajectory_stats([2.3, 1.4, .9, .6, .43, .36, .31, .55, .84, .92, .78, .66])
    assert late.late_share > 0.3 and late.reversals == 3 and not is_settled(late, L)
    assert trajectory_stats([0, 0, 0]).late_share == 0, "all-zero trajectory does not divide by zero"
    assert trajectory_stats([1, 1.04, 1.02]).reversals == 0, "5% tolerance ignores noise"


def test_verdict_matches_workspace(expected):
    cases = example_comparison().cases
    v = verdict_text(cases, L)
    e = expected["verdict"]
    assert (v["ready"], v["text"], v["start"], v["start_name"]) == (e["ready"], e["text"], e["start"], e["startName"])
    clean = [c.model_copy(update={"candidate_error": min(c.candidate_error, c.baseline_error), "candidate_trajectory": cases[5].baseline_trajectory}) for c in cases]
    assert verdict_text(clean, L)["ready"] is True


def test_report_core_is_byte_identical_to_workspace(expected):
    cmp = example_comparison()
    strict = CheckV2(case_id="aisle-042", name="Reflective floor", max_error=3.7, max_late_share=0.25)
    assert report_core(cmp, L, [strict]) == expected["report"]
    assert report_core(cmp, L, []) == expected["report_no_checks"]


def test_minimal_example_text_matches(expected):
    assert MINIMAL_EXAMPLE == expected["minimal_example"]
