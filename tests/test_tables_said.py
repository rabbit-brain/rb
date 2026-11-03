"""Tables and the words about them: reconcile names the top folder's results tables and reports, a table whose rows are
all runs, and a table with no header line; a table row reads as a row wherever rb says where a run's numbers came from,
and one row recorded in two experiments is said, as a database's is; a run outside a rule's selectors says so; an
analysis's estimate and bounds read from two lines of one table; a cited rule met on its estimate alone says so; and
--every-row records every row --where picks. Every name and number is synthetic: an INT8 ResNet-50 on ImageNet,
recorded by human:ana."""
import json
import subprocess
from pathlib import Path

import pytest

from rabbit_brain.models import Envelope
from rbtest import as_the_user, main


@pytest.fixture
def rb(tmp_path, monkeypatch, capsys):
    as_the_user(monkeypatch)
    monkeypatch.setenv("RB_ACTOR", "human:ana")
    root = tmp_path / "int8"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    monkeypatch.chdir(root)

    def run(*argv, ok=True, text=False):
        if text:
            code = main(list(argv), typed=True)
            return code, capsys.readouterr().out
        code = main([*argv, "--json"], typed=True)
        env = Envelope.model_validate_json(capsys.readouterr().out)
        if ok:
            assert code in (0, 1) and env.ok, (argv, code, env.errors)
        return env

    return run


def ledger(rows=4):
    Path("results.tsv").write_text("run\tprecision\ttop1\n" + "".join(f"r{i}\tint8\t75.{i}\n" for i in range(rows)), encoding="utf-8")


def test_reconcile_names_the_top_folder_s_results_table_and_report(rb):
    ledger()
    Path("RESULTS.md").write_text("# Results\n\nINT8 keeps 75.6% top-1.\n", encoding="utf-8")
    Path("notes.md").write_text("# Notes\n", encoding="utf-8")
    code, out = rb("reconcile", text=True)
    assert "0 results folders" not in out and "Nothing to compile" not in out, out
    assert "2 results files in the top folder" in out, out
    assert "results.tsv: a TSV table of 4 rows" in out
    assert "rb run add <experiment> --from results.tsv --where <column>=<value>" in out
    assert "RESULTS.md: prose: rb reads no run from it; a number there is a cited rule" in out
    assert "--source RESULTS.md --quote" in out
    assert "notes.md" not in out
    rb("init", "INT8 ResNet-50", "--no-agents")
    files = {f["path"]: f["kind"] for f in rb("reconcile").data["loose_files"]}
    assert files == {"results.tsv": "table", "RESULTS.md": "prose"}, files


def test_a_table_whose_rows_are_all_runs_stays_named(rb):
    rb("init", "INT8 ResNet-50", "--no-agents")
    rb("metric", "add", "top1", "--maximize")
    rb("experiment", "add", "INT8 alone", "--id", "e1", "--purpose", "the ledger", "--baseline", "int8")
    rb("spec", "set", "e1", "run", "--per-run")
    Path("results").mkdir()
    Path("results/ledger.jsonl").write_text("".join(json.dumps({"run": f"r{i}", "top1": 75 + i / 10}) + "\n" for i in range(3)), encoding="utf-8")
    for i in range(3):
        rb("run", "add", "e1", "--from", "results/ledger.jsonl", "--where", f"run=r{i}", "--set", "run=@run", "--produced-at", "2026-05-01")
    code, out = rb("reconcile", text=True)
    assert "0 results folders" not in out and "Nothing to compile" not in out, out
    assert "results/ledger.jsonl: a JSON Lines table of 3 rows" in out and "3 of 3 rows recorded as runs" in out, out


def test_a_table_with_no_header_line_is_said_so(rb):
    Path("results").mkdir()
    Path("results/sweep.tsv").write_text("r1\tbatch=64\t75.61\t1620\nr2\tbatch=128\t75.12\t1610\n", encoding="utf-8")
    code, out = rb("reconcile", text=True)
    assert "seems to have no header line" in out and "2 rows" in out, out
    assert "numbers in 75.61" not in out
    rb("init", "INT8 ResNet-50", "--no-agents")
    rb("experiment", "add", "INT8 alone", "--id", "e1", "--purpose", "the sweep", "--baseline", "int8")
    env = rb("run", "add", "e1", "--from", "results/sweep.tsv", "--where", "r1=r2", ok=False)
    assert not env.ok and "seems to have no header line" in env.errors[0].message
    assert "--alias" not in env.errors[0].fix


def two_experiments_on_one_ledger(rb):
    rb("init", "INT8 ResNet-50", "--no-agents")
    rb("metric", "add", "top1", "--maximize")
    for e in ("e1", "e2"):
        rb("experiment", "add", f"INT8 alone, {e}", "--id", e, "--purpose", "the ledger", "--baseline", "int8")
    ledger()


@pytest.mark.parametrize("name", ["results.tsv"])
def test_a_table_row_reads_as_a_row_and_one_row_in_two_experiments_is_said(rb, name):
    two_experiments_on_one_ledger(rb)
    env = rb("run", "add", "e1", "--from", name, "--where", "run=r1", "--produced-at", "2026-05-01")
    rid = env.data["object"]["id"]
    code, out = rb("show", rid, text=True)
    assert f"Run {rid} on e1 (row)" in out, out
    env = rb("run", "add", "e2", "--from", name, "--where", "run=r1", "--produced-at", "2026-05-01")
    assert any("is also recorded as" in w and rid in w for w in env.data.get("warnings", [])), env.data.get("warnings")
    items = [i for i in rb("status").data["open"] if i["code"] == "row_in_several_experiments"]
    assert items and set(items[0]["experiments"]) == {"e1", "e2"}, items


def test_a_run_outside_a_rule_s_selectors_says_so(rb):
    rb("init", "INT8 ResNet-50", "--no-agents")
    rb("metric", "add", "top1", "--maximize")
    rb("experiment", "add", "INT8 against FP32", "--id", "e3", "--purpose", "the checkers", "--baseline", "fp32", "--candidate", "int8")
    rb("spec", "set", "e3", "checker", "--per-run")
    rb("criterion", "add", "INT8 gains", "-e", "e3", "--metric", "top1", "--subject", "int8", "--reference", "fp32",
       "--subject-where", "checker=strict", "--reference-where", "checker=loose", "--at-least", "0", "--id", "c1")
    rb("run", "add", "e3", "fp32.top1=75.1", "--set", "checker=strict", "--produced-at", "2026-05-01")
    notes = [c["text"] for c in rb("show", "c1").data["finding"]["caveats"] if c["code"] == "counts_toward_nothing"]
    assert notes and "a number this rule reads, outside its per-run selectors (int8 at checker=strict; fp32 at checker=loose)" in notes[0], notes
    assert "and this rule reads" not in notes[0]


def test_an_analysis_reads_an_estimate_and_its_bounds_from_two_lines_of_one_table(rb):
    rb("init", "INT8 ResNet-50", "--no-agents")
    rb("metric", "add", "top1", "--maximize")
    rb("experiment", "add", "INT8 alone", "--id", "e4", "--purpose", "the reruns", "--baseline", "int8")
    Path("RESULTS.md").write_text(
        "# Results\n\n## Statistics\n\n| statistic | value |\n| --- | --- |\n| Mean | 75.6100 |\n| Std | 0.12 |\n| Mean 95% CI | [75.4000, 75.8200] |\n",
        encoding="utf-8",
    )
    env = rb("analysis", "add", "e4", "--compare", "int8:top1=75.61[75.40,75.82]", "--method", "t interval", "--unit", "run", "--units", "20",
             "--source", "RESULTS.md", "--locator", "Statistics")
    a = env.data["object"]
    src = a["comparisons"][0]["source"]
    assert src["line"] == 7 and src.get("resolved") is None, src
    assert a["receipt"]["stated_lines"][0]["lines"] == [7, 9], a["receipt"]
    code, out = rb("show", a["id"], text=True)
    assert "read :7 '| Mean | 75.6100 |' and :9 '| Mean 95% CI | [75.4000, 75.8200] |'" in out, out
    # the lines are re-read: a changed bound is the document's change
    Path("RESULTS.md").write_text(Path("RESULTS.md").read_text(encoding="utf-8").replace("75.8200", "75.9200"), encoding="utf-8")
    code, out = rb("show", a["id"], text=True)
    assert "no longer" in out, out


def test_an_analysis_no_line_states_says_how_to_read_two(rb):
    rb("init", "INT8 ResNet-50", "--no-agents")
    rb("experiment", "add", "INT8 alone", "--id", "e5", "--purpose", "the reruns", "--baseline", "int8")
    Path("RESULTS.md").write_text("# Results\n\nMean 75.61.\n", encoding="utf-8")
    env = rb("analysis", "add", "e5", "--compare", "int8:top1=75.61[75.40,75.82]", "--source", "RESULTS.md", ok=False)
    assert not env.ok and "--locator the section's heading or the lines, 83-91" in env.errors[0].fix


def test_a_cited_rule_met_on_its_estimate_alone_says_so(rb):
    rb("init", "INT8 ResNet-50", "--no-agents")
    rb("metric", "add", "top1", "--maximize")
    rb("experiment", "add", "INT8 alone", "--id", "e6", "--purpose", "the level", "--baseline", "int8")
    Path("README.md").write_text("INT8 scores 75.6 top-1.\n", encoding="utf-8")
    rb("criterion", "add", "The README's top-1", "-e", "e6", "--metric", "top1", "--equals", "75.6", "--tolerance", "0.05", "--source", "README.md", "--quote", "75.6", "--id", "c2")
    rb("run", "add", "e6", "top1=75.61", "--produced-at", "2026-05-01")
    says = rb("show", "c2").data["finding"]["says"]
    assert says["rule"].startswith("reproduced on the estimate"), says
    assert says["headline"].startswith("reproduced on the estimate · "), says


def test_every_row_records_each_row_where_picks(rb):
    rb("init", "INT8 ResNet-50", "--no-agents")
    rb("metric", "add", "top1", "--maximize")
    rb("experiment", "add", "INT8 alone", "--id", "e7", "--purpose", "the ledger", "--baseline", "int8")
    rb("spec", "set", "e7", "run", "--per-run")
    ledger(rows=5)
    env = rb("run", "add", "e7", "--from", "results.tsv", "--where", "precision=int8", "--set", "run=@run", "--produced-at", "2026-05-01", ok=False)
    assert not env.ok and "--every-row" in env.errors[0].fix
    env = rb("run", "add", "e7", "--from", "results.tsv", "--where", "precision=int8", "--set", "run=@run", "--produced-at", "2026-05-01", "--every-row")
    assert env.data["recorded_count"] == 5 and len(env.data["runs"]) == 5
    runs = [p.read_text(encoding="utf-8") for p in Path(".rb/experiments/e7/runs").glob("*.yaml")]
    assert sorted(r.split("run: ")[1].split("\n")[0] for r in runs if "per_run:" in r) == [f"r{i}" for i in range(5)]
    env = rb("run", "add", "e7", "--from", "results.tsv", "--where", "precision=int8", "--set", "run=@run", "--produced-at", "2026-05-01", "--every-row")
    assert env.data["recorded_count"] == 0 and env.data["already_count"] == 5


def test_every_row_refuses_rows_told_apart_by_their_numbers_alone(rb):
    rb("init", "INT8 ResNet-50", "--no-agents")
    rb("metric", "add", "top1", "--maximize")
    rb("experiment", "add", "INT8 alone", "--id", "e8", "--purpose", "the ledger", "--baseline", "int8")
    Path("results.jsonl").write_text('{"group": "x", "top1": 75.1}\n{"group": "x", "top1": 75.2}\n', encoding="utf-8")
    env = rb("run", "add", "e8", "--from", "results.jsonl", "--where", "group=x", "--produced-at", "2026-05-01", "--every-row", ok=False)
    assert not env.ok and "lines 1 and 2, hold the same cells but their numbers" in env.errors[0].message
    assert not list(Path(".rb/experiments/e8").glob("runs/*.yaml"))


def test_two_lines_longer_than_rb_keeps_still_read_as_unchanged(rb):
    rb("init", "INT8 ResNet-50", "--no-agents")
    rb("experiment", "add", "INT8 alone", "--id", "e9", "--purpose", "the reruns", "--baseline", "int8")
    Path("long.md").write_text("# Stats\nMean " + "x" * 410 + " 75.6\nCI 75.4 75.8\n", encoding="utf-8")
    rb("analysis", "add", "e9", "--compare", "int8:top1=75.6[75.4,75.8]", "--source", "long.md", "--locator", "Stats", "--id", "long")
    code, out = rb("show", "long", text=True)
    assert "no longer" not in out and "✗" not in out, out


def test_a_line_range_outside_the_document_reads_no_other_section(rb):
    rb("init", "INT8 ResNet-50", "--no-agents")
    rb("experiment", "add", "INT8 alone", "--id", "e10", "--purpose", "the reruns", "--baseline", "int8")
    Path("ranges.md").write_text("# Other\nmean 75.6\nCI 75.4 75.8\n# Selected\nno numbers here\n", encoding="utf-8")
    env = rb("analysis", "add", "e10", "--compare", "int8:top1=75.6[75.4,75.8]", "--source", "ranges.md", "--locator", "99-100", ok=False)
    assert not env.ok and env.errors[0].code == "E_SOURCE_UNRESOLVED"


def test_an_analysis_never_reads_its_numbers_from_lines_far_apart(rb):
    rb("init", "INT8 ResNet-50", "--no-agents")
    rb("experiment", "add", "INT8 alone", "--id", "e11", "--purpose", "the log", "--baseline", "int8")
    Path("train.log").write_text("\n".join(f"step {i} loss 0.{i:04d}1" for i in range(300)) + "\nloss 0.032\n\nwarmup 0.023\n\ndecay 0.014\n", encoding="utf-8")
    env = rb("analysis", "add", "e11", "--compare", "int8:loss=0.023[0.014,0.032]", "--source", "train.log", ok=False)
    assert not env.ok and env.errors[0].code == "E_SOURCE_UNRESOLVED"


def test_commit_at_a_column_refuses_a_number(rb):
    rb("init", "INT8 ResNet-50", "--no-agents")
    rb("metric", "add", "top1", "--maximize")
    rb("experiment", "add", "INT8 alone", "--id", "e12", "--purpose", "the ledger", "--baseline", "int8")
    Path("results.csv").write_text("run,top1,steps\nr1,75.6,5000\n", encoding="utf-8")
    env = rb("run", "add", "e12", "--from", "results.csv", "--where", "run=r1", "--commit", "@steps", ok=False)
    assert not env.ok and "which is not a commit" in env.errors[0].message


def test_every_row_writes_nothing_where_a_row_cannot_be_read_and_takes_no_replaces(rb):
    rb("init", "INT8 ResNet-50", "--no-agents")
    rb("metric", "add", "top1", "--maximize")
    rb("experiment", "add", "INT8 alone", "--id", "e13", "--purpose", "the ledger", "--baseline", "int8")
    Path("all.csv").write_text("grp,run,top1,sha\nx,r1,75.6,41e79187d2f794c98c869b6d5a2f7ec4811d125d\nx,r2,75.7,ana\n", encoding="utf-8")
    env = rb("run", "add", "e13", "--from", "all.csv", "--where", "grp=x", "--commit", "@sha", "--every-row", ok=False)
    assert not env.ok and "row 2 of the 2" in env.errors[0].message
    assert not list(Path(".rb/experiments/e13").glob("runs/*.yaml"))
    env = rb("run", "add", "e13", "--from", "all.csv", "--where", "grp=x", "--every-row", "--replaces", "run1234", "--why", "x", ok=False)
    assert not env.ok and "--replaces and --repeats name one run" in env.errors[0].message


def test_a_header_with_whole_numbers_names_its_columns(rb):
    rb("init", "INT8 ResNet-50", "--no-agents")
    rb("metric", "add", "top1", "--maximize")
    rb("experiment", "add", "INT8 alone", "--id", "e14", "--purpose", "the ledger", "--baseline", "int8")
    Path("results.csv").write_text("model,top1,1,5\nint8,75.6,0.1,0.2\n", encoding="utf-8")
    env = rb("run", "add", "e14", "--from", "results.csv", "--where", "model=int8", "--produced-at", "2026-05-01")
    assert env.data["object"]["metrics"]


def test_two_rows_that_differ_in_numbers_alone_are_not_one_row_across_experiments(rb):
    two_experiments_on_one_ledger(rb)
    Path("results.tsv").write_text("arm\ttop1\nint8\t75.6\nint8\t75.65\n", encoding="utf-8")
    rb("run", "add", "e1", "--from", "results.tsv", "--where", "arm=int8,top1=75.6", "--produced-at", "2026-05-01")
    env = rb("run", "add", "e2", "--from", "results.tsv", "--where", "arm=int8,top1=75.65", "--produced-at", "2026-05-01")
    assert not any("is also recorded as" in w for w in env.data.get("warnings", [])), env.data.get("warnings")
