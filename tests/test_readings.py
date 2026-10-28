"""What a finding reads, and what it keeps when that changes.

An analysis that replaces rb's reading of the runs leaves that reading on record, as history, and says whether it
re-read the same runs. An analysis edited outside rb is not read; what rb wrote stays as history, and the record says
what changed. What an analysis rests on is traced however many steps back it goes. An analysis that counts more
independent units than the runs it read form, by the project's declared unit, is a problem. A comparison no criterion
reads is listed, and a question and a resource say who reads them.

Every number here is synthetic.
"""
import os
import subprocess
from pathlib import Path

import pytest

from rabbit_brain.models import Envelope
from rbtest import as_the_user, main, rbyaml


@pytest.fixture
def rb(workdir, monkeypatch, tmp_path_factory, capsys):
    as_the_user(monkeypatch)
    cfg = tmp_path_factory.mktemp("gitconfig") / "config"
    cfg.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(cfg))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    subprocess.run(["git", "init", "-q"], check=True)
    monkeypatch.setenv("RB_ACTOR", "human:t")

    def run(*argv) -> Envelope:
        code = main([*argv, "--json"])
        env = Envelope.model_validate_json(capsys.readouterr().out)
        assert code in (0, 1) and env.ok, (argv, code, env.errors)
        return env

    run("init", "readings")
    run("metric", "add", "score", "--maximize")
    run("experiment", "add", "readouts", "--id", "e1", "--baseline", "old", "--candidate", "new")
    run("criterion", "add", "new beats old on score", "-e", "e1", "--metric", "score", "--subject", "new", "--reference", "old",
        "--better", "--id", "c1")
    run.root = Path.cwd()
    return run


def three_seeds(rb) -> list[str]:
    rb("spec", "set", "e1", "seed", "--per-run")
    return [rb("run", "add", "e1", f"new.score={a}", f"old.score={m}", "--set", f"seed={i}", "--command", f"eval {i}").data["object"]["id"]
            for i, (a, m) in enumerate(((0.62, 0.60), (0.65, 0.61), (0.63, 0.60)))]


def test_an_analysis_that_replaces_the_runs_reading_keeps_it_as_history(rb):
    runs = three_seeds(rb)
    before = rb("show", "c1").data["finding"]["comparison"]
    assert before["source"] == "runs" and before["n"] == 3
    rb("analysis", "add", "e1", "--compare", "new/old:score=0.01[-0.02,0.04]", "--reads", ",".join(runs),
       "--method", "clustered bootstrap", "--unit", "scene", "--units", "40", "--id", "a1")
    f = rb("show", "c1").data["finding"]
    assert f["comparison"]["source"] == "a1" and f["result"] == "unresolved"
    [old] = [h for h in f["history"] if h["source"] == "runs"]
    assert old["estimate"] == pytest.approx(before["estimate"]) and "replaced by a1, which re-reads these runs" in old["superseded"]


def test_an_analysis_edited_outside_rb_is_not_read_and_what_rb_wrote_stays(rb):
    rb("analysis", "add", "e1", "--compare", "new/old:score=0.01[-0.02,0.04]", "--method", "bootstrap", "--unit", "pair",
       "--units", "40", "--id", "a1")
    path = rb.root / ".rb" / "experiments" / "e1" / "analyses" / "a1.yaml"
    doc = rbyaml.loads(path.read_text(encoding="utf-8"))
    doc["comparisons"][0]["estimate"] = 0.03
    path.write_text(rbyaml.dumps(doc), encoding="utf-8")
    f = rb("show", "c1").data["finding"]
    assert f["comparison"] is None and f["rule_outcome"] == "undetermined" and "edited outside rb" in f["says"]["rule"]
    [h] = f["history"]
    assert h["estimate"] == pytest.approx(0.01) and "as rb wrote it" in h["superseded"]           # never the forged number
    assert any(p["code"] == "edited_outside_rb" and p["subject"] == "a1" for p in f["integrity"])
    shown = rb("show", "a1").data["edited_outside_rb"]
    assert "comparisons.0.estimate: 0.01 -> 0.03" in shown["changes"]


def test_what_an_analysis_rests_on_is_traced_every_step_back(rb):
    rb("metric", "add", "prec", "--maximize")
    rb("criterion", "add", "new beats old on prec", "-e", "e1", "--metric", "prec", "--subject", "new", "--reference", "old",
       "--better", "--id", "c2")
    rb("resource", "add", "cache", "--type", "artifact", "--name", "cached inference")
    rb("analysis", "add", "e1", "--compare", "new/old:score=0.01[-0.02,0.04]", "--input", "cache:inference", "--id", "a1")
    rb("analysis", "add", "e1", "--compare", "new/old:prec=0.08[0.03,0.13]", "--reads", "a1", "--id", "a2")
    assert rb("show", "c2").data["finding"]["integrity"] == []
    rb("retract", "cache", "--why", "the cache held stale features")
    f = rb("show", "c2").data["finding"]
    [p] = [p for p in f["integrity"] if p["code"] == "input_retracted"]
    assert p["subject"] == "a2" and "a2 read a1, which rests on cache" in p["text"]
    assert rb("show", "cache").data["read_by"]["analyses"] == ["a1", "a2"]


def test_an_analysis_counting_more_units_than_the_runs_form_is_a_problem(rb):
    rb("spec", "set", "e1", "pair", "--per-run")
    rb("spec", "set", "e1", "view", "--per-run")
    rb("spec", "set", "e1", "population.unit", "pair")
    runs = [rb("run", "add", "e1", f"new.score={0.61 + i / 100}", f"old.score={0.60 + i / 200}", "--set", f"pair={i // 2}",
               "--set", f"view={i % 2}", "--command", f"eval {i}").data["object"]["id"] for i in range(4)]
    rb("analysis", "add", "e1", "--compare", "new/old:score=0.02[0.01,0.03]", "--reads", ",".join(runs), "--unit", "view",
       "--units", "4", "--id", "a1")
    f = rb("show", "c1").data["finding"]
    [p] = [p for p in f["integrity"] if p["code"] == "units"]
    assert "a1 counts 4 views, and the runs it read form 2 pairs" in p["text"]
    assert any(h["source"] == "runs" and h["n"] == 2 for h in f["history"])          # rb's own reading, in pairs, is kept


def test_a_comparison_no_criterion_reads_is_listed(rb):
    rb("analysis", "add", "e1", "--compare", "new/old:score=0.01[-0.02,0.04]", "--compare", "new/alt:score=0.05[0.02,0.08]",
       "--id", "a1")
    [it] = [i for i in rb("status").data["open"] if i["code"] == "unread_comparison"]
    assert "new vs alt" in it["what"] and "new vs old" not in it["what"]
    assert it["do"] == ""                                  # no rule nobody stated is suggested


def test_a_level_an_analysis_reports_is_said_without_a_sign(rb, capsys):
    """A level is one arm's number, never a change: rb analysis add, rb show and what no criterion reads say it bare,
    and a comparison keeps its signs. So does a document that does not state them."""
    assert main(["analysis", "add", "e1", "--compare", "new:score=0.81[0.78,0.84]", "--compare", "new/old:score=0.02[-0.01,0.05]", "--id", "a1"]) == 0
    said = capsys.readouterr().out
    assert "  score: new: 0.81 [0.78, 0.84]" in said and "  score: new vs old: +0.02 [-0.01, +0.05]" in said
    assert main(["show", "a1"]) == 0
    said = capsys.readouterr().out
    assert "  score: new: 0.81 [0.78, 0.84]" in said and "  score: new vs old: +0.02 [-0.01, +0.05]" in said
    Path("rep.md").write_text("nothing stated here\n", encoding="utf-8")
    for compare, what in (("new:score=0.91[0.88,0.94]", "score 0.91 [0.88, 0.94]"), ("new/old:score=0.03[0.01,0.05]", "score +0.03 [+0.01, +0.05]")):
        assert main(["analysis", "add", "e1", "--compare", compare, "--source", "rep.md"]) == 2
        assert f"rep.md has no line that states {what}." in capsys.readouterr().err
    [it] = [i for i in rb("status").data["open"] if i["code"] == "unread_comparison"]
    assert it["what"].startswith("e1: score: new 0.81 [0.78, 0.84] (a1): no recorded criterion reads it")


def test_a_question_says_where_it_stands_and_an_experiment_who_follows_it(rb):
    rb("question", "add", "does new help?", "--id", "q1")
    rb("experiment", "file", "e1", "q1")
    rb("decide", "q1", "park", "--why", "later")
    assert rb("show", "q1").data["state"] == "parked"
    rb("experiment", "add", "on motion", "--id", "e2", "--baseline", "old", "--candidate", "new")
    rb("experiment", "relate", "e2", "follows_from", "e1", "--note", "narrows it to motion blur")
    assert rb("show", "e1").data["related_from"] == [{"experiment": "e2", "kind": "follows_from", "note": "narrows it to motion blur"}]


def test_declaring_the_unit_after_runs_does_not_move_them_to_an_earlier_spec(rb):
    """population.* says how the runs are counted, not how they were made: declared late, it recounts them."""
    three_seeds(rb)
    rb("spec", "set", "e1", "population.unit", "seed", "--no-verify")
    f = rb("show", "c1").data["finding"]
    assert f["units"]["n"] == 3 and all(o["counted"] for o in f["observations"])


def test_superseding_one_arms_measurement_leaves_the_other_arms_level(rb):
    rb("criterion", "add", "old stays above 0.5", "-e", "e1", "--metric", "old.score", "--at-least", "0.5", "--id", "c2")
    runs = three_seeds(rb)
    rb("retract", runs[0], "--scope", "new.score", "--why", "the new arm's number was read from a stale cache")
    level = rb("show", "c2").data["finding"]
    assert level["units"]["n"] == 3                                       # old.score still counts in every run
    diff = rb("show", "c1").data["finding"]
    assert diff["units"]["n"] == 2                                        # the comparison needs new's number


def test_a_criterion_on_an_estimand_reads_only_an_analysis_that_reports_it(rb):
    rb("criterion", "add", "new beats old, pooled", "-e", "e1", "--metric", "score", "--subject", "new", "--reference", "old",
       "--estimand", "pooled", "--better", "--id", "c2")
    three_seeds(rb)
    f = rb("show", "c2").data["finding"]
    assert f["comparison"] is None and f["rule_outcome"] == "untested"
    assert any(c["code"] == "estimand" for c in f["caveats"])
    rb("analysis", "add", "e1", "--compare", "new/old:score@pooled=0.02[0.01,0.03]", "--id", "a1")
    assert rb("show", "c2").data["finding"]["comparison"]["source"] == "a1"


def test_an_export_carries_the_document_an_analysis_was_checked_against(rb, tmp_path):
    Path("REPORT.md").write_text("| arm | delta |\n| new vs old | 0.01 [-0.02, 0.04] |\n", encoding="utf-8")
    rb("analysis", "add", "e1", "--compare", "new/old:score=0.01[-0.02,0.04]", "--source", "REPORT.md", "--id", "a1")
    assert rb("show", "c1").data["finding"]["integrity"] == []
    out = tmp_path / "bundle"
    rb("export", "--with-record", "--out", str(out))
    assert (out / "record" / "REPORT.md").is_file()
    here = os.getcwd()
    os.chdir(out / "record")
    try:
        assert rb("show", "c1").data["finding"]["integrity"] == []
    finally:
        os.chdir(here)


def test_an_export_carries_a_document_pinned_to_a_commit_as_it_is_there(rb, tmp_path):
    """The bundle has no history to read the commit in: the document travels as it is at that commit, and is read
    there as it is read here, though the working copy has moved on."""
    Path("REPORT.md").write_text("| arm | delta |\n| new vs old | 0.01 [-0.02, 0.04] |\n", encoding="utf-8")
    git = ["git", "-c", "user.email=t@example.com", "-c", "user.name=t"]
    subprocess.run([*git, "add", "REPORT.md"], check=True)
    subprocess.run([*git, "commit", "-qm", "the report"], check=True)
    head = subprocess.run(["git", "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
    rb("analysis", "add", "e1", "--compare", "new/old:score=0.01[-0.02,0.04]", "--source", "REPORT.md", "--commit", head, "--id", "a1")
    Path("REPORT.md").write_text("| arm | delta |\n| new vs old | 0.02 [-0.01, 0.05] |\n", encoding="utf-8")
    assert rb("show", "c1").data["finding"]["integrity"] == []
    out = tmp_path / "bundle"
    rb("export", "--with-record", "--out", str(out))
    assert "0.01 [-0.02, 0.04]" in (out / "record" / ".rb" / "cache" / "commits" / head / "REPORT.md").read_text(encoding="utf-8")
    here = os.getcwd()
    os.chdir(out / "record")
    try:
        assert rb("show", "c1").data["finding"]["integrity"] == []
        assert rb("show", "a1").data["caveats"] == []
    finally:
        os.chdir(here)


def test_show_an_analysis_rechecks_the_document_that_states_its_numbers(rb, capsys):
    Path("REPORT.md").write_text("| arm | delta |\n| new vs old | 0.01 [-0.02, 0.04] |\n", encoding="utf-8")
    rb("analysis", "add", "e1", "--compare", "new/old:score=0.01[-0.02,0.04]", "--source", "REPORT.md", "--id", "a1")
    assert rb("show", "a1").data["caveats"] == []
    Path("REPORT.md").write_text("| arm | delta |\n| new vs old | 0.02 [-0.01, 0.05] |\n", encoding="utf-8")
    [now] = rb("show", "a1").data["caveats"]
    assert (now["code"], now["subject"], now["problem"]) == ("analysis_source_changed", "a1", True)
    assert "no longer states 0.01, -0.02, 0.04" in now["text"]
    assert [p["code"] for p in rb("show", "c1").data["finding"]["integrity"]] == ["analysis_source_changed"]   # the same read
    assert main(["show", "a1"]) == 0
    assert "    ✗ REPORT.md" in capsys.readouterr().out


def test_the_unit_hint_names_the_coarsest_setting_the_runs_nest_in(rb):
    """Two images, four noise cases each: the hint names photo, the likeliest independent unit, not noise."""
    rb("spec", "set", "e1", "photo", "--per-run")
    rb("spec", "set", "e1", "noise", "--per-run")
    for i in range(8):
        rb("run", "add", "e1", f"new.score={0.61 + i / 1000}", f"old.score={0.60 + i / 2000}", "--set", f"photo={'ab'[i // 4]}",
           "--set", f"noise={i % 4}", "--command", f"eval {i}")
    [hint] = [c for c in rb("show", "c1").data["finding"]["caveats"] if c["code"] == "unit_hint"]
    assert "photo takes 2 values across these 8 units (also noise (4))" in hint["text"]
    assert "population.unit photo" in hint["text"]


def test_a_retracted_resource_is_missing_from_what_a_rerun_needs(rb):
    rb("resource", "add", "cache", "--type", "artifact", "--name", "cached inference")
    rb("run", "add", "e1", "new.score=0.61", "old.score=0.60", "--input", "cache:inference", "--command", "eval")
    rb("retract", "cache", "--why", "stale features")
    [row] = [r for r in rb("show", "e1").data["rerunnability"] if r.get("resource") == "cache"]
    assert row["state"] == "missing" and "retracted" in row["detail"]


def test_a_line_someone_decided_is_not_listed_as_planned(rb):
    rb("experiment", "add", "a proof", "--id", "thm", "--purpose", "a proof, not a run")
    assert any(i["code"] == "planned" and i["subject"] == "thm" for i in rb("status").data["open"])
    rb("decide", "thm", "accept", "--why", "the proof was checked")
    assert not any(i["code"] == "planned" and i["subject"] == "thm" for i in rb("status").data["open"])
