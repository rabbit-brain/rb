"""rb compile: the reconciler's front door. Prose is reconciled against what the record knows,
never semantically extracted: terms match the record's own vocabulary, numbers ground against
stored and derived values with the line they sit on, structured files with an unambiguous home
get the exact rb run add, and what grounds nowhere is listed, never guessed at. The command reads;
it never writes; the agent's bootstrap follows the printed contract and rb verifies the spans."""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from rabbit_brain.models import Envelope
from rbtest import ROOT, as_the_user, main, Mcp

HAS_GIT = shutil.which("git") is not None
pytestmark = pytest.mark.skipif(not HAS_GIT, reason="the research state is played in a git repository")


@pytest.fixture
def rb(workdir, monkeypatch, tmp_path_factory, capsys):
    as_the_user(monkeypatch)
    cfg = tmp_path_factory.mktemp("gitconfig") / "config"
    cfg.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(cfg))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    subprocess.run(["git", "init", "-q"], check=True)
    subprocess.run(["git", "config", "user.email", "ana@example.com"], check=True)
    subprocess.run(["git", "config", "user.name", "ana"], check=True)

    def run(*argv):
        code = main([*argv, "--json"])
        return code, Envelope.model_validate_json(capsys.readouterr().out)

    return run


def seeded(rb):
    assert rb("init", "compile me")[0] == 0
    assert rb("experiment", "add", "int8 vs fp32", "--id", "e1", "--baseline", "fp32", "--candidate", "int8", "--varies", "precision")[0] == 0
    assert rb("metric", "add", "val_loss", "--unit", "nats", "--minimize", "--alias", "cross_entropy")[0] == 0
    assert rb("criterion", "add", "int8 costs at most 0.05 nats", "-e", "e1", "--metric", "change.val_loss", "--at-most", "0.05", "--id", "c1")[0] == 0
    assert rb("run", "add", "e1", "fp32.val_loss=5.61", "int8.val_loss=5.64")[0] == 0


def test_without_a_project_it_inventories_and_points_at_init(rb, capsys):
    Path("report.md").write_text("# Findings\nnothing here yet\n", encoding="utf-8")
    code = main(["compile", "report.md"])
    out = capsys.readouterr().out
    assert code == 0
    assert "report.md" in out and "Findings" in out
    assert "rb init" in out
    assert "contract" in out.lower()


def test_the_contract_it_prints_is_the_one_the_manual_describes(rb, capsys):
    """AGENTS.md describes the contract rb compile prints, and both say how a statement lifted from a report travels: in
    its own words, never reworded, as plain text with the markup dropped, while a --quote stays exact. A contract that
    kept the report's markup would contradict the line rb gives after an agent's write whose text holds LaTeX."""
    Path("report.md").write_text("# Results\nThe wide layer gains $\\Delta \\geq 0.3$ over \\emph{base} (Table~\\ref{tab:main}).\n",
                                 encoding="utf-8")
    code, env = rb("compile", "report.md")
    assert code == 0
    [statements] = [c for c in env.data["contract"] if c.startswith("Statements travel verbatim")]
    manual = (ROOT / "AGENTS.md").read_text(encoding="utf-8").replace("`", "")
    [described] = [p for p in manual.split("\n\n") if p.startswith("The rest is yours, under the contract it prints:")]
    for said in ("never reworded", "as plain text with the markup dropped", "--quote stays exact"):
        assert said in statements and said in described, said
    assert main(["compile", "report.md"]) == 0
    assert f"  3. {statements}" in capsys.readouterr().out.splitlines()


def test_the_contract_and_the_manual_give_a_past_call_no_path_around_the_confirmation(rb, capsys):
    """Review of round 6: rb compile's contract, the manual's account of it and llms.txt still said a document committed
    before the record began makes a past call one with no confirmation. Each now says it waits for the same one
    confirmation whatever document it cites, and that rb says beside it what git shows of the document."""
    Path("report.md").write_text("# Results\nnothing yet\n", encoding="utf-8")
    code, env = rb("compile", "report.md")
    assert code == 0
    [calls] = [c for c in env.data["contract"] if "--past" in c]
    manual = (ROOT / "AGENTS.md").read_text(encoding="utf-8").replace("`", "")
    [described] = [p for p in manual.split("\n\n") if p.startswith("The rest is yours, under the contract it prints:")]
    llms = (ROOT / "llms.txt").read_text(encoding="utf-8")
    for where, text in {"contract": calls, "manual": described, "llms.txt": llms}.items():
        assert "without one" not in text and "without a confirmation" not in text, where
        assert "waits for the same one confirmation whatever document it cites" in text, where
        assert "what git shows of" in text, where


def test_prose_grounds_against_the_record_and_the_rest_stays_unplaced(rb, capsys):
    seeded(rb)
    Path("NOTES.md").write_text(
        "# Weekly notes\n"
        "The int8 arm lands at a val_loss of 5.64 nats against 5.61 for fp32, a change of 0.03,\n"
        "inside the 0.05 budget. The number 777.77 belongs to nothing here.\n",
        encoding="utf-8")
    code, env = rb("compile", "NOTES.md")
    assert code == 0
    src = env.data["sources"][0]
    a = src["anchors"]
    terms = {t["term"]: t["kind"] for t in a["terms"]}
    assert terms.get("val_loss") == "metric" and "variant of e1" in terms.get("int8", "")
    grounded = {g["text"]: g["matches"] for g in a["grounded"]}
    assert any("int8.val_loss" in m for m in grounded.get("5.64", []))
    assert any("change in val_loss" in m for m in grounded.get("0.03", []))  # a derived value grounds
    assert any("the rule of c1" in m for m in grounded.get("0.05", []))      # so does the criterion's own rule
    assert "777.77" not in grounded                                          # unknown numbers stay unplaced
    # The command wrote nothing: compile is a read.
    assert not env.data["proposals"]
    code2, env2 = rb("status")
    assert env2.data["counts"]["run_count"] == 1


def test_a_name_written_with_an_escaped_underscore_is_the_record_s_own(rb, capsys):
    """Markdown and LaTeX escape an underscore (val\\_loss): the name is still the one the record knows."""
    seeded(rb)
    Path("NOTES.md").write_text("# Notes\nThe int8 arm keeps its val\\_loss within budget.\n", encoding="utf-8")
    code, env = rb("compile", "NOTES.md")
    terms = {t["term"]: t["kind"] for t in env.data["sources"][0]["anchors"]["terms"]}
    assert code == 0 and terms.get("val_loss") == "metric"


def test_a_structured_file_with_one_home_gets_the_exact_run_add(rb, capsys):
    seeded(rb)
    Path("metrics.json").write_text(json.dumps({"fp32": {"val_loss": 5.60}, "int8": {"val_loss": 5.65}}), encoding="utf-8")
    code, env = rb("compile", "metrics.json")
    assert code == 0
    [prop] = env.data["proposals"]
    assert {k: prop[k] for k in ("path", "experiment", "argv")} == {"path": "metrics.json", "experiment": "e1",
                                                                     "argv": ["run", "add", "e1", "--from", "metrics.json"]}
    assert prop["written"] and prop["before_experiment"] is False       # written after e1 existed: it can be e1's run
    # A file written before the experiment existed matches by name only: it is dated, and said to be likely not its run.
    old = Path("old.json")
    old.write_text(json.dumps({"fp32": {"val_loss": 5.70}, "int8": {"val_loss": 5.90}}), encoding="utf-8")
    os.utime(old, (1_600_000_000, 1_600_000_000))
    assert next(p for p in rb("compile", "old.json")[1].data["proposals"] if p["path"] == "old.json")["before_experiment"] is True
    assert env.next[0] == "rb run add e1 --from metrics.json"
    # Numbers already in the record are recognised, not proposed twice.
    assert rb("run", "add", "e1", "--from", "metrics.json")[0] == 0
    code, env = rb("compile", "metrics.json")
    assert env.data["proposals"] == []
    assert env.data["sources"][0]["note"] == "already in the record"


def test_paths_outside_the_project_are_named_not_read(rb, capsys, tmp_path):
    seeded(rb)
    stray = tmp_path.parent / f"{tmp_path.name}-stray.json"
    stray.write_text("{}", encoding="utf-8")
    code, env = rb("compile", str(stray))
    assert code == 0
    assert env.data["sources"] == [] and any(stray.as_posix() in o for o in env.data["outside"])


def test_a_directory_compiles_its_files_and_skips_the_machinery(rb, capsys):
    seeded(rb)
    Path("exhaust").mkdir()
    Path("exhaust/conclusion.md").write_text("# Conclusion\nthe change of 0.03 held\n", encoding="utf-8")
    Path("exhaust/run.py").write_text("print('hi')\n", encoding="utf-8")
    Path("exhaust/report.qmd").write_text("# Report\nthe change of 0.03 held\n", encoding="utf-8")   # Quarto is Markdown
    Path("exhaust/.hidden.md").write_text("skip me", encoding="utf-8")
    code, env = rb("compile", "exhaust")
    assert code == 0
    kinds = {s["path"]: s["kind"] for s in env.data["sources"]}
    assert kinds == {"exhaust/conclusion.md": "prose", "exhaust/report.qmd": "prose", "exhaust/run.py": "code"}


def test_the_ir_is_one_call_away_for_agents(rb, capsys):
    """The vocabulary reaches agents through the integration, not only the docs.
    `rb schema ir` and the rb://schema MCP resource serve the same vocabulary with its provenance."""
    seeded(rb)
    code, env = rb("schema", "ir")
    assert code == 0
    objects = env.data["objects"]
    assert set(objects) >= {"question", "experiment", "setting", "criterion", "run", "analysis", "finding", "decision"}
    assert "ruling" not in objects and "grant" not in objects
    assert "REQUIRED" in objects["run"]["provenance"] and "receipt" in objects["run"]["provenance"]
    assert "who made it" in objects["decision"]["provenance"] and "words" in objects["decision"]["provenance"]
    server = Mcp("claude-code")
    try:
        listed = server.handle({"jsonrpc": "2.0", "id": 1, "method": "resources/list", "params": {}})["result"]["resources"]
        read = server.handle({"jsonrpc": "2.0", "id": 2, "method": "resources/read", "params": {"uri": "rb://schema"}})["result"]
    finally:
        server.close()
    assert any(r["uri"] == "rb://schema" for r in listed)
    assert json.loads(read["contents"][0]["text"])["objects"] == objects


def test_with_no_question_recorded_its_next_step_is_the_question_not_rb_status(rb, capsys):
    """Review of round 10's fix: the start protocol puts the rules the frontier rests on in before the first rb status,
    but rb compile, run before any question exists, ended "Next: rb status". With no question recorded, its next step
    is the work's question; once one is recorded, rb status, as before."""
    assert rb("init", "INT8 ResNet-50 on ImageNet")[0] == 0
    Path("report.md").write_text("# Findings\nINT8 costs 0.006 top-1\n", encoding="utf-8")
    code, env = rb("compile", "report.md")
    assert code == 0 and env.next == ['rb question add "<the question the work answers>"'], env.next
    assert rb("question", "add", "Does INT8 cost top-1 accuracy?", "--id", "q1")[0] == 0
    code, env = rb("compile", "report.md")
    assert code == 0 and env.next == ["rb status"], env.next
