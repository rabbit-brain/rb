"""Saved-check semantics agree with the workspace's checkResult() and the kit's check.py."""
import json

from rabbit_brain.checks import evaluate_check, load_checks, save_checks, upsert_check
from rabbit_brain.example import example_comparison
from rabbit_brain.models import ChecksV2, CheckV2


def result(check: CheckV2, run):
    r = evaluate_check(check, run, "px")
    return {"status": r.status, "reason": r.reason}


def test_results_match_workspace(expected):
    ex = example_comparison()
    e = expected["check_results"]
    assert result(CheckV2(case_id="aisle-042", max_error=3.7), ex) == e["failing"]
    assert result(CheckV2(case_id="aisle-042", max_error=6, max_late_share=0.25), ex) == e["strict"]
    assert result(CheckV2(case_id="nope", max_error=6), ex) == e["missing"]
    assert result(CheckV2(case_id="pallet-003", max_error=6, max_late_share=0.25), ex) == e["passing"]


def test_fixed_candidate_passes_and_missing_trajectory_fails():
    ex = example_comparison()
    settled = ex.cases[5].baseline_trajectory
    fixed = ex.model_copy(update={"cases": [c.model_copy(update={"candidate_error": min(c.candidate_error, c.baseline_error), "candidate_trajectory": settled}) for c in ex.cases]})
    strict = CheckV2(case_id="aisle-042", max_error=6, max_late_share=0.25)
    assert evaluate_check(strict, fixed, "px").status == "passing"
    no_traj = fixed.model_copy(update={"cases": [c.model_copy(update={"candidate_trajectory": None}) for c in fixed.cases]})
    assert evaluate_check(strict, no_traj, "px").status == "failing"
    reversals = CheckV2(case_id="aisle-042", max_error=6, max_reversals=1)
    assert evaluate_check(reversals, ex, "px").status == "failing" and "Reversals" in evaluate_check(reversals, ex, "px").reason


def test_v1_checks_file_is_read_and_v2_written(tmp_path):
    v1 = tmp_path / "checks.json"
    v1.write_text(json.dumps({"version": 1, "checks": [
        {"id": "aisle-042", "name": "Reflective floor", "project": "Warehouse perception", "max_error": 3.7, "max_late_share": 0.25},
        {"id": "x", "name": "x", "project": "Other", "max_error": 1},
    ]}))
    checks = load_checks(v1, "Warehouse perception")
    assert checks.version == 2 and checks.project == "Warehouse perception" and [c.case_id for c in checks.checks] == ["aisle-042"]
    checks = upsert_check(checks, CheckV2(case_id="turn-026", max_error=3.5))
    save_checks(v1, checks)
    again = load_checks(v1, "Warehouse perception")
    assert [c.case_id for c in again.checks] == ["aisle-042", "turn-026"]
    lenient = load_checks(v1, "Somewhere else", strict=False)
    assert lenient.checks == []
