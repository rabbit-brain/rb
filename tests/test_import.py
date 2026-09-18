"""Import validation reports every problem in plain language, exactly as the workspace does."""
import json

import pytest

from rabbit_brain.errors import RBError
from rabbit_brain.example import MINIMAL_EXAMPLE, example_comparison
from rabbit_brain.importer import csv_to_comparison, parse_comparison_text, parse_comparison_value
from rabbit_brain.models import Limits
from rabbit_brain.stability import SummaryNumbers


def problems(value) -> list[str]:
    with pytest.raises(RBError) as exc:
        parse_comparison_value(value)
    return exc.value.problems


def test_problems_match_workspace(expected):
    ex = json.loads(json.dumps(expected["example"]))
    e = expected["import_problems"]
    assert problems({"not": "valid"}) == e["not_valid"]
    assert problems({**ex, "cases": [ex["cases"][0], ex["cases"][0]]}) == e["dup"]
    assert problems({**ex, "cases": [{**ex["cases"][0], "candidate_frames": [1, 2]}]}) == e["frames"]
    assert problems({**ex, "cases": [{**ex["cases"][0], "candidate_trajectory": [1]}]}) == e["traj1"]
    assert problems({**ex, "cases": [{"id": "a"}]}) == e["badcase"]


def test_broken_json_and_size():
    with pytest.raises(RBError) as exc:
        parse_comparison_text("{broken")
    assert exc.value.code == "E_IMPORT_NOT_JSON" and exc.value.problems[0].startswith("This is not valid JSON.")
    with pytest.raises(RBError) as exc:
        parse_comparison_text("x" * 2_000_001)
    assert exc.value.code == "E_IMPORT_TOO_LARGE"


def test_minimal_example_imports():
    cmp = parse_comparison_text(MINIMAL_EXAMPLE)
    assert cmp.source == "imported" and cmp.unit == "px" and len(cmp.cases) == 1


def test_legacy_metric_literal_and_no_trajectories():
    ex = example_comparison().model_dump(exclude_none=True)
    legacy = {**ex, "metric": "mean_endpoint_error_px"}
    del legacy["unit"]
    for c in legacy["cases"]:
        c.pop("baseline_trajectory"); c.pop("candidate_trajectory")
    cmp = parse_comparison_value(legacy)
    m = cmp.metric_obj()
    assert (m.name, m.unit) == ("mean endpoint error", "px")
    s = SummaryNumbers(cmp.cases, Limits())
    assert (s.with_trajectories, s.unstable, s.regressions) == (0, 0, 3)


def test_too_many_problems_are_capped():
    cases = [{"id": f"c{i}"} for i in range(10)]
    ex = example_comparison().model_dump(exclude_none=True)
    p = problems({**ex, "cases": cases})
    assert len(p) == 11 and p[-1].startswith("…and ") and p[-1].endswith(" more.")


def test_csv_import(tmp_path):
    csv = tmp_path / "m.csv"
    csv.write_text(
        "case_id,name,tags,baseline_error,candidate_error,baseline_trajectory,candidate_trajectory\n"
        "seq-1,First,a;b,2.0,2.5,2.4;1.5;0.9;0.6;0.4;0.3;0.2;0.15;0.1;0.08;0.06;0.05,2.3;1.4;0.9;0.6;0.45;0.4;0.35;0.5;0.8;0.9;0.75;0.6\n"
        "seq-2,,,3.0,2.0,,\n"
    )
    cmp = csv_to_comparison(csv, project="P", baseline="a", candidate="b", dataset="d", metric="abs_depth_error", unit="cm")
    assert [c.id for c in cmp.cases] == ["seq-1", "seq-2"] and cmp.cases[1].name == "seq-2" and cmp.cases[0].tags == ["a", "b"]
    assert cmp.cases[0].candidate_trajectory and cmp.cases[1].candidate_trajectory is None
    assert cmp.metric_obj().unit == "cm"
    with pytest.raises(RBError) as exc:
        csv_to_comparison(csv, project="", baseline="a", candidate="b", dataset="d", metric="m", unit="cm")
    assert exc.value.code == "E_IMPORT_CONFIG"
    bad = tmp_path / "bad.csv"
    bad.write_text("id,err\n1,2\n")
    with pytest.raises(RBError) as exc:
        csv_to_comparison(bad, project="P", baseline="a", candidate="b", dataset="d", metric="m", unit="cm")
    assert exc.value.code == "E_CSV_INVALID"
