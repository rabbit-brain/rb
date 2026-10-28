"""Relationships retain both scopes, including fixed cells and repository evidence."""
import shutil

import pytest

import rabbit_brain
from rbtest import rbyaml
from test_finding_relations import record, reading, relate  # noqa: F401 - shared synthetic record fixture


def test_mixed_design_cells_do_not_become_a_reversal_of_their_average(record):
    record("spec", "set", "e1", "width", "--design-factor")
    record("run", "add", "e1", "new.loss=2", "old.loss=1", "--set", "width=512")
    record("run", "add", "e1", "new.loss=0", "old.loss=1", "--set", "width=768")
    record("run", "add", "e2", "new.loss=0", "old.loss=1")
    row = relate(record)["relation"]
    assert row["source_finding"]["comparison"]["design"]["count"] == 2
    assert row["source_direction"] == "mixed" and row["outcome"] == "mixed"


def test_incomplete_design_comparison_keeps_the_relationship_incomplete(record):
    record("spec", "set", "e1", "width", "--design-factor")
    record("run", "add", "e1", "new.loss=2", "old.loss=1", "--set", "width=512")
    record("run", "add", "e1", "new.loss=3", "--set", "width=768")
    record("run", "add", "e2", "new.loss=0", "old.loss=1")
    row = relate(record)["relation"]
    assert row["source_finding"]["comparison"]["design"]["incomplete"] == ["width=768"]
    assert row["source_finding"]["rule_outcome"] == "undetermined"
    assert row["outcome"] == "incomplete"


def test_scope_alignment_distinguishes_boolean_from_number_even_when_both_are_valid_selectors(record):
    record("spec", "set", "e2", "flag", "--per-run")
    record("criterion", "add", "Boolean slice", "-e", "e2", "--metric", "change.loss", "--at-most", "0",
           "--where", "flag=true", "--id", "slice")
    record("criterion", "relate", "c1", "reverses", "slice", "--orientation", "same", "--why", "Align this exact boolean slice")
    path = record.root / ".rb/experiments/e2/criteria/slice.yaml"
    content = rbyaml.loads(path.read_text(encoding="utf-8"))
    content["where"]["flag"] = 1
    path.write_text(rbyaml.dumps(content), encoding="utf-8")
    row = reading(record)
    assert row["outcome"] == "integrity"
    assert any(p["code"] == "relation_outdated" and p["subject"] == "slice" for p in row["integrity"])


@pytest.mark.parametrize("changed", ["report", "audit"])
def test_question_call_tracks_related_experiments_repository_without_direct_question_link(record, changed):
    folder = record.root / "results/e2"
    folder.mkdir(parents=True)
    report = folder / "README.md"
    report.write_text("# Training 2\n\n## Results\nThe saved comparison is ready for review.\n", encoding="utf-8")
    audit = folder / "independent_audit.json"
    audit.write_text('{"passed": true}', encoding="utf-8")
    record("reconcile", "--apply")
    assert record("show", "e2")["object"]["folder"] == "results/e2/"
    record("run", "add", "e1", "new.loss=2", "old.loss=1")
    record("run", "add", "e2", "new.loss=0", "old.loss=1")
    relate(record)
    call = record("decide", "q1", "continue", "--why", "Review the two stages together")["object"]
    assert set(call["inquiry_basis"]["criteria"]) == {"c1"}
    assert "e2#repository" in call["inquiry_basis"]["repository"]
    assert not any(p["code"] == "decision_outdated" and p["subject"] == "q1" for p in record("status")["open"])
    if changed == "report":
        report.write_text("# Training 2\n\n## Results\nThe saved comparison needs a source correction.\n", encoding="utf-8")
        record("reconcile", "--apply")
    else:
        audit.write_text('{"passed": false}', encoding="utf-8")
    assert any(p["code"] == "decision_outdated" and p["subject"] == "q1" for p in record("status")["open"])


@pytest.mark.skipif(shutil.which("git") is None, reason="rb clone starts a git repository")
def test_clone_preserves_declared_selector_design_and_alignment_without_importing_findings(record, tmp_path_factory):
    record("spec", "set", "e1", "width", "--design-factor")
    record("criterion", "add", "Selected cell", "-e", "e1", "--metric", "change.loss", "--at-most", "0",
           "--where", "width=512", "--id", "slice", "-q", "q1")
    record("criterion", "relate", "slice", "reverses", "c2", "--orientation", "same", "--why", "Align the selected cell")
    record("run", "add", "e1", "new.loss=2", "old.loss=1", "--set", "width=512")
    record("run", "add", "e2", "new.loss=0", "old.loss=1")
    before = record("show", "slice")["object"]
    bundle = tmp_path_factory.mktemp("scope-bundle") / "export"
    record("export", "--with-record", "--out", str(bundle))
    clone = rabbit_brain.clone(bundle, tmp_path_factory.mktemp("scope-clone") / "work", agent="cloner")
    copied = clone.criterion("slice").data
    assert copied.where == {"width": 512}
    assert [r.model_dump() for r in copied.relations] == before["relations"]
    assert clone.experiment("e1").spec["width"].design_factor
    assert clone.records("run") == [] and clone.records("decision") == []
    q = next(q["inquiry"] for q in clone.status()["questions"] if q["id"] == "q1")
    relation = next(r for r in q["relations"] if r["source"] == "slice")
    assert relation["outcome"] == "incomplete" and not relation["integrity"]
