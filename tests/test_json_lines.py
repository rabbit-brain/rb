"""Step 1 finds what is there: a results folder that holds tables of its own is named with its files, a JSON Lines
file is a table whose rows are runs, and a setting reads one column of one row of a CSV, TSV or JSON Lines table.
Every name and number is synthetic: an INT8 ResNet-50 on ImageNet, recorded by human:ana."""
import json
import subprocess

import pytest

from rabbit_brain.models import Envelope
from rbtest import as_the_user, main

ROWS = [
    {"precision": "fp32", "seed": 1, "eval": {"top1": 76.1}, "tag": "7", "finished": "2026-05-01T10:00:00Z"},
    {"precision": "int8", "seed": 1, "eval": {"top1": 75.6}, "tag": "7", "finished": "2026-05-01T11:00:00Z"},
    {"precision": "int8", "seed": 2, "eval": {"top1": 75.4}, "tag": "8", "finished": "2026-05-02T11:00:00Z"},
]
LEDGER = "".join(json.dumps(r) + "\n" for r in ROWS)


@pytest.fixture
def rb(tmp_path, monkeypatch, capsys):
    as_the_user(monkeypatch)
    monkeypatch.setenv("RB_ACTOR", "human:ana")
    root = tmp_path / "int8"
    (root / "results").mkdir(parents=True)
    (root / "results" / "ledger.jsonl").write_text(LEDGER, encoding="utf-8")
    (root / "results" / "sweep.csv").write_text("precision,bits,calib\nfp32,32,none\nint8,8,minmax\n", encoding="utf-8")
    (root / "results" / "preds.parquet").write_bytes(b"PAR1....PAR1")
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

    run.root = root
    run("init", "INT8 ResNet-50", "--no-agents")
    return run


def experiment(rb):
    rb("metric", "add", "top1", "--maximize", "--unit", "percent")
    rb("experiment", "add", "INT8 against FP32", "--id", "int8", "--purpose", "the sweep", "--baseline", "fp32", "--candidate", "int8")
    rb("spec", "set", "int8", "seed", "--per-run")


def test_reconcile_names_the_tables_a_results_folder_holds_of_its_own(rb):
    code, out = rb("reconcile", text=True)
    assert "0 results folders" not in out and "Nothing to compile" not in out
    assert "3 results files in results/" in out
    assert "results/ledger.jsonl: a JSON Lines table of 3 rows, numbers in seed, eval.top1" in out
    assert "a row is a run: rb run add <experiment> --from results/ledger.jsonl --where <column>=<value>" in out
    assert "results/sweep.csv: a CSV table of 2 rows, numbers in bits" in out
    assert "results/preds.parquet: a Parquet file, which rb does not read" in out
    env = rb("reconcile")
    kinds = {f["path"]: f["kind"] for f in env.data["loose_files"]}
    assert kinds == {"results/ledger.jsonl": "table", "results/preds.parquet": "unread", "results/sweep.csv": "table"}


def test_one_wording_of_where_rb_looks(rb, tmp_path, monkeypatch):
    for f in ("ledger.jsonl", "sweep.csv", "preds.parquet"):
        (rb.root / "results" / f).unlink()
    (rb.root / "notes").mkdir()
    (rb.root / "notes" / "a.json").write_text('{"x": 1}', encoding="utf-8")
    rb("reconcile", "--apply")  # the changed file is recorded by path, so only where rb looks is left to say
    code, out = rb("reconcile", text=True)
    assert "the usual results folders" not in out
    looks = "folders named for results (results/, eval/, metrics/, reports/, artifacts/ and the like), down to 4 levels from the top"
    assert f"rb looks for results in {looks}:" in out and f"Nothing to compile where rb looks for results: {looks}." in out


def test_a_json_lines_row_is_a_run_with_its_nested_keys(rb):
    experiment(rb)
    env = rb("run", "add", "int8", "--from", "results/ledger.jsonl", "--where", "precision=int8,seed=2", "--variant", "int8", "--set", "seed=@seed",
             "--produced-at", "@finished")
    run = env.data["object"]
    assert run["metrics"] == {"int8.eval.top1": 75.4} and run["per_run"] == {"seed": 2}
    row = run["receipt"]["table_row"]
    assert row["store"] == "jsonl" and row["line"] == 3 and row["where"] == {"precision": "int8", "seed": 2}
    # a JSON string is text, whatever it spells
    rb("spec", "set", "int8", "tag", "--per-run")
    env = rb("run", "add", "int8", "--from", "results/ledger.jsonl", "--where", "precision=int8,seed=1", "--variant", "int8", "--set", "seed=@seed",
             "--set", "tag=@tag", "--produced-at", "@finished")
    assert env.data["object"]["per_run"] == {"seed": 1, "tag": "7"}


def test_a_where_that_picks_no_row_or_several_says_what_the_rows_hold(rb):
    experiment(rb)
    env = rb("run", "add", "int8", "--from", "results/ledger.jsonl", "--where", "precision=int8", "--variant", "int8", ok=False)
    msg = env.errors[0].message
    assert "2 rows of results/ledger.jsonl have precision=int8" in msg and "They differ in " in msg and "seed (1, 2)" in msg and "tag (7, 8)" in msg and "75.6" not in msg.split("They differ in")[1]
    env = rb("run", "add", "int8", "--from", "results/ledger.jsonl", "--where", "precision=int4", "--variant", "int8", ok=False)
    assert "No row of results/ledger.jsonl has precision=int4. Its rows hold precision fp32, int8." in env.errors[0].message
    env = rb("run", "add", "int8", "--from", "results/ledger.jsonl", "--variant", "int8", ok=False)
    assert "is a JSON Lines file of 3 rows, and rb reads one row of it as a run" in env.errors[0].message


def test_where_on_a_file_that_is_no_table_names_every_kind_rb_reads_as_rows(rb):
    experiment(rb)
    (rb.root / "results" / "one.json").write_text('{"eval": {"top1": 75.0}}', encoding="utf-8")
    env = rb("run", "add", "int8", "--from", "results/one.json", "--where", "seed=1", "--variant", "int8", ok=False)
    msg = env.errors[0].message
    assert "SQLite database" in msg and "CSV, TSV or JSON Lines" in msg


def test_compile_lists_a_json_lines_tables_numbers(rb):
    experiment(rb)
    env = rb("compile", "results/ledger.jsonl")
    src = env.data["sources"][0]
    assert src["kind"] == "table" and src["rows"] == 3 and src["number_columns"] == ["seed", "eval.top1"]
    assert src["metrics"] == {"eval.top1": "top1"}
    (rb.root / "results" / "bad.jsonl").write_text('{"a": 1}\nnot json\n', encoding="utf-8")
    env = rb("compile", "results/bad.jsonl")
    assert "line 2, is not JSON" in env.data["sources"][0]["note"]


def test_a_setting_reads_one_column_of_one_row_of_a_table(rb):
    experiment(rb)
    for key in ("#[precision=int8].calib", "#sweep[precision=int8].calib"):
        code, out = rb("spec", "set", "int8", "int8.calib", "minmax", "--source", f"results/sweep.csv{key}", "--dry-run", text=True)
        assert code == 0 and "verified in results/sweep.csv" in out, out
    env = rb("spec", "set", "int8", "int8.calib", "--source", "results/sweep.csv#[precision=int8].calib")
    s = env.data["object"]
    assert s["value"] == "minmax" and s["source"]["path"] == "results/sweep.csv" and s["source"]["key"] == "[precision=int8].calib"
    code, out = rb("spec", "set", "int8", "fp32.calib", "minmax", "--source", "results/sweep.csv#[bits=32].calib", text=True)
    assert code == 1 and "CONFLICT" in out
    code, out = rb("spec", "set", "int8", "fp32.calib", "none", "--source", "results/sweep.csv#[calib=nope].calib", text=True)
    assert code == 1 and "no row of results/sweep.csv has calib=nope" in out


def test_a_key_into_a_file_rb_reads_by_no_key_is_refused_never_kept_as_a_path(rb):
    experiment(rb)
    (rb.root / "README.md").write_text("calibration: minmax\n", encoding="utf-8")
    env = rb("spec", "set", "int8", "int8.calib", "minmax", "--source", "README.md#calib", ok=False)
    assert env.errors[0].code == "E_SOURCE_UNRESOLVED" and "README.md is none of those" in env.errors[0].message
    assert not list((rb.root / ".rb" / "experiments" / "int8" / "variants").glob("*.yaml")) or "README.md#" not in (
        rb.root / ".rb" / "experiments" / "int8" / "variants" / "int8.yaml").read_text()


def test_every_file_where_results_land_is_named_with_why(rb):
    import sqlite3
    (rb.root / "results" / "scores.txt").write_text("top1 75.6\n", encoding="utf-8")
    con = sqlite3.connect(rb.root / "results" / "eval.sqlite")
    con.execute("create table evals(seed integer, top1 real)")
    con.execute("insert into evals values (1, 75.6)")
    con.commit()
    con.close()
    code, out = rb("reconcile", text=True)
    assert "results/eval.sqlite: a SQLite database" in out and "--from results/eval.sqlite --table <table>" in out
    # a document is named where nothing else is, in a folder below too, never a folder said to be empty
    assert "scores.txt" not in out and "4 results files in results/" in out
    for f in ("ledger.jsonl", "sweep.csv", "preds.parquet", "eval.sqlite"):
        (rb.root / "results" / f).unlink()
    (rb.root / "results" / "run1").mkdir()
    (rb.root / "results" / "scores.txt").rename(rb.root / "results" / "run1" / "scores.txt")
    code, out = rb("reconcile", text=True)
    assert "0 results folders" not in out and "Nothing to compile" not in out
    assert "results/run1/scores.txt: a .txt document" in out and "1 results file in results/run1/" in out


def test_a_json_lines_line_with_two_keys_that_read_alike_is_refused(rb):
    experiment(rb)
    (rb.root / "results" / "b.jsonl").write_text('{"precision": "int8", "eval.top1": 72, "eval": {"top1": 73}}\n', encoding="utf-8")
    env = rb("run", "add", "int8", "--from", "results/b.jsonl", "--where", "precision=int8", "--variant", "int8", ok=False)
    assert "two keys that both read as eval.top1" in env.errors[0].message


def test_a_row_picked_with_no_hash_is_refused_and_an_empty_table_offers_no_row(rb):
    experiment(rb)
    env = rb("spec", "set", "int8", "int8.calib", "minmax", "--source", "results/sweep.csv[precision=int8].calib", ok=False)
    assert env.errors[0].code == "E_SOURCE_UNRESOLVED" and "results/sweep.csv#[precision=int8].calib" in env.errors[0].fix
    (rb.root / "results" / "empty.jsonl").write_text("", encoding="utf-8")
    code, out = rb("reconcile", text=True)
    assert "results/empty.jsonl: a JSON Lines table of 0 rows" in out and "--from results/empty.jsonl --where" not in out
