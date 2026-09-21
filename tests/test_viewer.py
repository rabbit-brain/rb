"""The generated HTML report.

The viewer's own arithmetic is covered by tests/test_conformance.py, which runs the TypeScript
against this package. What is tested here is the document rb writes: that it carries everything the
viewer needs, that it carries nothing that would break it when opened from disk, and that it says
what the run's limits actually were.
"""
import hashlib
import json
import re
from pathlib import Path

import pytest

from rabbit_brain import viewer
from rabbit_brain.models import CheckV2, ChecksV2, Limits

from tests.conformance.corpus import METRIC, bundle, case

CASES = [case("one", "First", 1.0, 2.0, [4.0, 2.0, 1.0, 0.5], [4.0, 2.0, 1.0, 0.5]),
         case("two", "Second", 1.0, 1.0)]

# The template is inlined whole into every report, so its size is the floor on every file rb writes
# and it travels in the wheel. A jump means something was pulled into the bundle by accident.
TEMPLATE_CEILING = 1_100_000


def payload(html: str) -> dict:
    block = re.search(r'<script id="rb-data" type="application/json">(.*?)</script>', html, re.S)
    assert block, "the report has no data block"
    return json.loads(block.group(1).replace("<\\/script", "</script"))


def test_the_template_ships_with_the_package():
    assert viewer.TEMPLATE.is_file(), f"no viewer template at {viewer.TEMPLATE}; the wheel was built without it"
    size = viewer.TEMPLATE.stat().st_size
    assert size < TEMPLATE_CEILING, f"the viewer template is {size} bytes, over the {TEMPLATE_CEILING} ceiling"
    text = viewer.TEMPLATE.read_text(encoding="utf-8")
    assert "__RB_TITLE__" in text and "__RB_DATA__" in text
    # Opened from disk, a fetch of a sibling file is blocked, so nothing may be referenced by path.
    assert not re.search(r'<(script|link)[^>]*\s(src|href)=', text), "the template loads something over the network"


def test_the_conformance_fixture_is_the_viewer_this_package_ships():
    """tests/conformance/comparison.ts is the TypeScript the conformance test runs. It is a copy, so
    it can go stale, and a stale copy would prove the wrong two things agree. The viewer build
    records a hash of every file it was built from; this ties the copy to that record."""
    fingerprint = viewer.TEMPLATE.parent / "report-template.sources.json"
    assert fingerprint.is_file(), f"no viewer fingerprint at {fingerprint}"
    recorded = json.loads(fingerprint.read_text(encoding="utf-8"))
    assert "lib/comparison.ts" in recorded["perFile"], "the viewer build did not record lib/comparison.ts"

    copy = Path(__file__).resolve().parent / "conformance" / "comparison.ts"
    assert copy.is_file(), f"no vendored comparison.ts at {copy}"
    digest = hashlib.sha256(copy.read_bytes()).hexdigest()
    assert digest == recorded["perFile"]["lib/comparison.ts"], (
        "tests/conformance/comparison.ts is not the file the shipped viewer was built from. "
        "Copy lib/comparison.ts across from the site repository together with the template, or the "
        "conformance test is comparing rb against code it does not ship. See tests/conformance/README.md."
    )


def test_the_report_carries_the_limits_the_run_was_checked_at():
    limits = Limits(max_regression=0.5, max_late_share=0.2, max_reversals=1, max_trajectory_regression=0.4)
    data = payload(viewer.build(bundle("r", CASES, limits)))
    assert data["limits"] == {"threshold": 0.5, "lateShare": 0.2, "reversals": 1, "trajectoryRegression": 0.4}


def test_limits_that_are_off_are_absent_rather_than_zero():
    """A zero limit is the strictest setting there is. Sending one for a limit that is switched off
    would flag every case in the run."""
    data = payload(viewer.build(bundle("r", CASES, Limits())))
    assert "trajectoryRegression" not in data["limits"] and "lastUpdate" not in data["limits"]
    assert viewer.limits_json(Limits(max_trajectory_regression=0.0))["trajectoryRegression"] == 0.0


def test_errors_are_not_rounded_on_the_way_in():
    """Rounding here moves cases across the limit: 1.300001 against a 0.3 limit is a regression in
    report.md and would read as stable in the HTML if it arrived as 1.3."""
    edge = [case("edge", "On the edge", 1.0, 1.3 + 1e-6)]
    data = payload(viewer.build(bundle("r", edge, Limits())))
    assert data["run"]["cases"][0]["candidate_error"] == 1.3 + 1e-6


def test_a_policy_this_version_cannot_check_is_stated_in_the_report():
    limits = Limits(extra={"max_pose_drift_cm": 1.5})
    data = payload(viewer.build(bundle("r", CASES, limits)))
    assert "max_pose_drift_cm" in data["notice"]
    assert payload(viewer.build(bundle("r", CASES, Limits())))["notice"] == ""


def test_saved_checks_travel_with_the_project_they_were_saved_for():
    checks = ChecksV2(version=2, project="alpha", checks=[
        CheckV2(case_id="one", name="First", max_error=1.0, max_late_share=0.25, max_reversals=1)])
    data = payload(viewer.build(bundle("r", CASES, Limits()), checks=checks))
    assert data["checks"] == [{"id": "one", "name": "First", "project": "alpha", "max_error": 1.0,
                               "max_late_share": 0.25, "max_reversals": 1}]


def test_evidence_is_relative_beside_the_run_and_inlined_when_asked(tmp_path):
    run_dir = tmp_path / "run"
    sheet_dir = run_dir / "evidence" / "one"
    sheet_dir.mkdir(parents=True)
    for name, _, _ in viewer.SHEETS:
        (sheet_dir / name).write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 32)
    (sheet_dir / "not-a-sheet.png").write_bytes(b"ignored")

    relative = viewer.evidence_map(run_dir)
    assert [s["src"] for s in relative["one"]] == [f"evidence/one/{n}" for n, _, _ in viewer.SHEETS]
    assert all(s["label"] and s["note"] and s["alt"] for s in relative["one"])

    embedded = viewer.evidence_map(run_dir, embed=True)
    assert all(s["src"].startswith("data:image/png;base64,") for s in embedded["one"])
    assert viewer.evidence_map(None) == {} and viewer.evidence_map(tmp_path / "nothing") == {}


def test_a_missing_sheet_is_skipped_rather_than_rendered_broken(tmp_path):
    run_dir = tmp_path / "run"
    (run_dir / "evidence" / "one").mkdir(parents=True)
    (run_dir / "evidence" / "one" / "error.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (run_dir / "evidence" / "empty").mkdir()
    sheets = viewer.evidence_map(run_dir)
    assert [s["src"] for s in sheets["one"]] == ["evidence/one/error.png"]
    assert "empty" not in sheets


def test_a_closing_script_tag_in_the_data_cannot_end_the_block_early():
    tricky = [case("one", "</script><script>alert(1)</script>", 1.0, 1.0)]
    html = viewer.build(bundle("r", tricky, Limits(), project="</script>"))
    body = html.split('<script id="rb-data" type="application/json">', 1)[1]
    assert body.split("</script>", 1)[0].strip().endswith("}")
    assert payload(html)["run"]["project"] == "</script>"


def test_the_title_is_the_comparison_and_cannot_inject_markup():
    html = viewer.build(bundle("r", CASES, Limits(), project="<img src=x>"))
    assert "<title><img src=x>" not in html
    assert "&lt;img src=x&gt;: current against candidate" in html


def test_a_build_without_the_template_says_so(monkeypatch, tmp_path):
    monkeypatch.setattr(viewer, "TEMPLATE", tmp_path / "gone.html")
    with pytest.raises(FileNotFoundError, match="does not include the report viewer"):
        viewer.build(bundle("r", CASES, Limits()))


def test_unmeasured_cases_are_left_out_rather_than_sent_as_zero():
    """The viewer's format requires an error on both sides. A case with no ground truth has none, and
    sending zero would report it as a large improvement."""
    from rabbit_brain.models import CaseV1
    mixed = [case("measured", "Has labels", 1.0, 2.0)]
    b = bundle("r", mixed, Limits())
    b = b.model_copy(update={"cases": [*b.cases, b.cases[0].model_copy(update={
        "id": "unmeasured", "baseline_error": None, "candidate_error": None, "has_gt": False})]})
    ids = [c["id"] for c in viewer.comparison_json(b)["cases"]]
    assert ids == ["measured"]
    assert METRIC.unit == "px" and CaseV1 is not None
