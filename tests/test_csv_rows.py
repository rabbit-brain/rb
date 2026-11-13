"""A row of a CSV or TSV table is a run, read as a row of a SQLite database is; rb compile lists a table's rows and the
columns that name a metric, and never cuts its list without saying so. Every name and number is synthetic: an INT8
ResNet-50 on ImageNet, recorded by human:ana."""
import json
import subprocess
from pathlib import Path

import pytest

from rabbit_brain.models import Envelope
from rbtest import as_the_user, main

TABLE = "precision,bits,seed,top1,finished\nfp32,32,1,76.1,2026-05-01T10:00:00Z\nint8,8,1,75.6,2026-05-01T11:00:00Z\nint8,8,2,75.4,2026-05-02T11:00:00Z\n"


@pytest.fixture
def rb(tmp_path, monkeypatch, capsys):
    as_the_user(monkeypatch)
    monkeypatch.setenv("RB_ACTOR", "human:ana")
    root = tmp_path / "int8"
    (root / "results").mkdir(parents=True)
    (root / "results" / "sweep.csv").write_text(TABLE, encoding="utf-8")
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
    run("metric", "add", "top1", "--maximize", "--unit", "percent")
    run("experiment", "add", "INT8 against FP32", "--id", "int8", "--purpose", "the sweep", "--baseline", "fp32", "--candidate", "int8")
    run("spec", "set", "int8", "seed", "--per-run")
    return run


def test_a_row_of_a_table_is_a_run(rb):
    env = rb("run", "add", "int8", "--from", "results/sweep.csv", "--where", "precision=int8,seed=2", "--variant", "int8", "--set", "seed=@seed",
             "--produced-at", "@finished")
    run = env.data["object"]
    assert run["metrics"] == {"int8.top1": 75.4} and run["per_run"] == {"seed": 2} and run["produced_at"].startswith("2026-05-02T11:00:00")
    row = run["receipt"]["table_row"]
    assert row["path"] == "results/sweep.csv" and row["line"] == 4 and row["where"] == {"precision": "int8", "seed": 2}
    code, out = rb("show", "--long", run["id"], text=True)
    assert "row of results/sweep.csv, line 4 (precision=int8, seed=2)" in out
    # another row of the same table is another run; the same row again is the run already recorded
    other = rb("run", "add", "int8", "--from", "results/sweep.csv", "--where", "precision=int8,seed=1", "--variant", "int8", "--set", "seed=@seed")
    assert other.data["object"]["id"] != run["id"] and "duplicate_of" not in other.data
    again = rb("run", "add", "int8", "--from", "results/sweep.csv", "--where", "precision=int8,seed=2", "--variant", "int8", "--set", "seed=@seed",
               "--produced-at", "@finished")
    assert again.data["duplicate_of"] == run["id"]


def test_a_table_row_must_be_named_and_one(rb):
    none = rb("run", "add", "int8", "--from", "results/sweep.csv", ok=False)
    assert none.errors[0].code == "E_OBJECT_INVALID" and "its columns are precision, bits, seed, top1, finished" in none.errors[0].message
    many = rb("run", "add", "int8", "--from", "results/sweep.csv", "--where", "precision=int8", "--variant", "int8", "--set", "seed=1", ok=False)
    assert "2 rows of results/sweep.csv have precision=int8; the first is line 3" in many.errors[0].message
    by_number = rb("run", "add", "int8", "--from", "results/sweep.csv", "--where", "bits=8.0,seed=1", "--variant", "int8", "--set", "seed=@seed")
    assert by_number.data["object"]["metrics"] == {"int8.top1": 75.6}


def test_compile_lists_a_tables_rows_and_metric_columns(rb):
    code, out = rb("compile", "results", text=True)
    assert "results/sweep.csv  ·  table  ·  3 rows  ·  columns precision, bits, seed, top1, finished  ·  numbers in bits, seed, top1" in out
    assert "metric columns top1" in out and "a row is a run: rb run add <experiment> --from results/sweep.csv --where <column>=<value>" in out


def test_compile_never_cuts_its_list_without_saying_so(rb):
    many = Path(rb.root, "many")
    many.mkdir()
    for i in range(205):
        (many / f"r{i:03d}.json").write_text(json.dumps({"top1": 75.0}), encoding="utf-8")
    env = rb("compile", "many")
    assert env.data["sources_total"] == 205 and env.data["sources_cut"] is True and len(env.data["sources"]) == 200
    code, out = rb("compile", "many", text=True)
    assert "Sources (the first 200 of 205, by path):" in out and "and 5 more files not listed" in out


def test_a_row_is_one_run_whichever_columns_name_it_and_from_any_folder(rb):
    first = rb("run", "add", "int8", "--from", "results/sweep.csv", "--where", "precision=fp32", "--variant", "fp32", "--set", "seed=@seed").data["object"]
    assert first["receipt"]["table_row"]["path"] == "results/sweep.csv"
    again = rb("run", "add", "int8", "--from", "results/sweep.csv", "--where", "precision=fp32,bits=32", "--variant", "fp32", "--set", "seed=@seed")
    assert again.data["duplicate_of"] == first["id"]
    import os
    here = os.getcwd()
    os.chdir(Path(rb.root, "results"))
    try:
        inside = rb("run", "add", "int8", "--from", "sweep.csv", "--where", "precision=fp32", "--variant", "fp32", "--set", "seed=@seed")
    finally:
        os.chdir(here)
    assert inside.data["duplicate_of"] == first["id"]


def test_a_table_with_a_column_named_twice_or_no_rows_is_refused(rb):
    Path(rb.root, "results", "twice.csv").write_text("precision,top1,top1\nint8,75.6,99\n", encoding="utf-8")
    Path(rb.root, "results", "empty.csv").write_text("precision,top1\n", encoding="utf-8")
    twice = rb("run", "add", "int8", "--from", "results/twice.csv", "--where", "precision=int8", ok=False)
    assert "names column top1 twice" in twice.errors[0].message
    empty = rb("run", "add", "int8", "--from", "results/empty.csv", "--where", "precision=int8", ok=False)
    assert "holds no rows under its first line" in empty.errors[0].message


def test_a_table_of_one_row_says_one_row(rb):
    Path(rb.root, "results", "one.csv").write_text("precision,seed,top1\nint8,1,75.6\n", encoding="utf-8")
    none = rb("run", "add", "int8", "--from", "results/one.csv", ok=False)
    assert "results/one.csv is a table of 1 row, and rb reads one row of it as a run" in none.errors[0].message, none.errors[0].message


def test_run_add_help_says_it_reads_a_csv_row(rb, capsys):
    code, out = rb("run", "add", "--help", text=True)
    said = " ".join(out.split())
    assert "a CSV, TSV or JSON Lines table with --where" in said and "with --table, or a CSV, TSV or JSON Lines table: the row's columns" in said, said


def test_a_row_picked_by_what_the_experiment_varies_names_its_variant(rb):
    rb("experiment", "add", "INT8 against FP32 by precision", "--id", "prec", "--purpose", "the sweep", "--baseline", "fp32", "--candidate", "int8",
       "--varies", "precision")
    rb("spec", "set", "prec", "seed", "--per-run")
    run = rb("run", "add", "prec", "--from", "results/sweep.csv", "--where", "precision=int8,seed=2", "--set", "seed=@seed").data["object"]
    assert run["metrics"] == {"int8.top1": 75.4}, run["metrics"]


def test_a_row_names_its_variant_by_the_same_number_written_otherwise(rb):
    """A variant's own value 8.0 is the row's bits=8: a value matches as text or as the same number."""
    rb("experiment", "add", "INT8 against FP32 by bits", "--id", "bits", "--purpose", "the sweep", "--baseline", "fp32", "--candidate", "int8",
       "--varies", "bits")
    rb("spec", "set", "bits", "fp32.bits", "32.0", "--no-verify")
    rb("spec", "set", "bits", "int8.bits", "8.0", "--no-verify")
    rb("spec", "set", "bits", "seed", "--per-run")
    run = rb("run", "add", "bits", "--from", "results/sweep.csv", "--where", "bits=8,seed=1", "--set", "seed=@seed").data["object"]
    assert run["metrics"] == {"int8.top1": 75.6}, run["metrics"]
