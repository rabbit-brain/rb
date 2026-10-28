"""A SQLite database is read where it is, and never written.

`rb run add <exp> --from <database> --table <table> --where <column>=<value>` reads exactly one row as a run: its
columns that name a metric the project declares are the run's numbers, under the column's own name (`--select` narrows
them), `@<column>` takes a per-run value or the time the run was made from the row, and the receipt keeps the database
with its sha256, the table, the row's key and the column each value came from. No row, or several, is refused with how
many and the first of them, and nothing is written. After every read the database's bytes, its time and its directory
are as they were, in either journal mode; a database rb cannot read without writing to it is refused. A file is a
database by its first bytes, never by its name, and `rb compile` lists a database's tables. The SDK and MCP read a row
as the command line does. Every name and number here is made up."""
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

import rabbit_brain as rb
from rabbit_brain.models import Envelope
from rbtest import as_the_user, main, Mcp, rbyaml

HAS_GIT = shutil.which("git") is not None
pytestmark = pytest.mark.skipif(not HAS_GIT, reason="the record is kept in a git repository")

EVALS = [("r1", "fp32", 1, 0.761, 1.02, 0.93, "2026-05-01 10:00:00"),
         ("r2", "int8", 1, 0.755, 1.05, None, "2026-05-01 11:00:00"),
         ("r3", "int8", 2, 0.757, 1.04, 0.92, "2026-05-02 09:30:00"),
         ("r4", "int8", 3, 0.751, 1.06, 0.91, "2026-05-03 08:15:00")]


def database(path, journal="delete", rows=EVALS) -> Path:
    """A results database as an evaluation script leaves it: table evals, one row per run, closed."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    assert con.execute(f"pragma journal_mode={journal}").fetchone()[0] == journal
    con.execute("create table evals (run_name text, precision text, seed integer, acc1 real, val_loss real, acc5 real, finished_at text)")
    con.executemany("insert into evals values (?, ?, ?, ?, ?, ?, ?)", rows)
    con.commit()
    con.close()
    return path


def files_in(folder) -> dict:
    """Every file in a folder, with its bytes and its modification time."""
    return {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in sorted(Path(folder).iterdir()) if p.is_file()}


def record_files() -> dict:
    """The record as files: every one under .rb/ but rb's own cache."""
    return {p.as_posix(): p.read_bytes() for p in sorted(Path(".rb").rglob("*")) if p.is_file() and "cache" not in p.parts}


def sha256(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@pytest.fixture
def rbj(workdir, monkeypatch, capsys, tmp_path_factory):
    """A study in a git repository, run by ana at her terminal: experiment int8 (fp32 against int8, a seed per run)
    and the metrics top1, which a column may call acc1, and val_loss. rbj(...) is rb ... --json: (exit code, envelope)."""
    as_the_user(monkeypatch)
    monkeypatch.setenv("RB_ACTOR", "human:ana")
    cfg = tmp_path_factory.mktemp("gitconfig") / "config"
    cfg.write_text("[user]\n\tname = ana\n\temail = ana@example.com\n", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(cfg))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    subprocess.run(["git", "init", "-q"], check=True)

    def run(*argv):
        code = main([*argv, "--json"])
        return code, Envelope.model_validate_json(capsys.readouterr().out)

    for argv in (["init", "INT8 ResNet-50 on ImageNet", "--no-agents"],
                 ["experiment", "add", "INT8 against FP32", "--id", "int8", "--baseline", "fp32", "--candidate", "int8", "--varies", "precision"],
                 ["spec", "set", "int8", "seed", "--per-run"],
                 ["metric", "add", "top1", "--maximize", "--alias", "acc1"],
                 ["metric", "add", "val_loss", "--minimize"]):
        assert run(*argv)[0] == 0, argv
    return run


def said(capsys, *argv) -> tuple:
    code = main(list(argv))
    out = capsys.readouterr()
    return code, out.out, out.err


def test_one_row_is_a_run_that_names_its_database_table_row_and_columns(rbj, capsys):
    db = database("results.db")
    code, env = rbj("run", "add", "int8", "--from", "results.db", "--table", "evals", "--where", "precision=int8,seed=2", "--set", "seed=2")
    assert code == 0, env.errors
    run = env.data["object"]
    assert run["metrics"] == {"acc1": 0.757, "val_loss": 1.04}            # acc5 names no declared metric: not a number of the run
    assert run["basis"] == "file" and run["files"] == [{"path": "results.db", "sha256": sha256(db), "bytes": db.stat().st_size}]
    row = run["receipt"]["row"]
    assert {k: row[k] for k in ("store", "path", "sha256", "table", "where", "rowid", "columns")} == {
        "store": "sqlite", "path": "results.db", "sha256": sha256(db), "table": "evals", "where": {"precision": "int8", "seed": 2},
        "rowid": 3, "columns": {"acc1": "acc1", "val_loss": "val_loss"}}
    stored = rbyaml.loads(next(Path(".rb").rglob(f"{run['id']}.yaml")).read_text(encoding="utf-8"))
    assert stored["receipt"]["row"] == {"store": "sqlite", "path": "results.db", "sha256": sha256(db), "table": "evals",
                                        "where": {"precision": "int8", "seed": 2}, "rowid": 3,
                                        "columns": {"acc1": "acc1", "val_loss": "val_loss"}}
    code, out, _ = said(capsys, "show", run["id"])
    assert code == 0 and "  read from results.db, table evals, rowid 3 (precision=int8, seed=2)" in out.splitlines()
    # read again, after the database has grown: the same row is the same run, and nothing is written
    con = sqlite3.connect(db)
    con.execute("insert into evals values ('r5', 'int8', 4, 0.749, 1.07, 0.9, '2026-05-04 10:00:00')")
    con.commit()
    con.close()
    before = record_files()
    code, env = rbj("run", "add", "int8", "--from", "results.db", "--table", "evals", "--where", "run_name=r3", "--set", "seed=2")
    assert code == 0 and env.data["duplicate_of"] == run["id"]
    assert record_files() == before
    # another row of the same database is another run
    code, env = rbj("run", "add", "int8", "--from", "results.db", "--table", "evals", "--where", "run_name=r5", "--set", "seed=4")
    assert code == 0 and env.data["object"]["id"] != run["id"] and env.data["object"]["receipt"]["row"]["rowid"] == 5


def update(db, sql):
    """A change the evaluation script makes to its database, committed and closed."""
    con = sqlite3.connect(db)
    con.execute(sql)
    con.commit()
    con.close()


def test_a_row_read_again_with_other_values_is_refused_for_one_row_is_one_run(rbj):
    """Read again after its values changed (metrics filled in when training ended, a row rewritten), a row is refused
    with what changed, and nothing is written; --replaces records it as it stands and retracts the earlier read. A change
    in a column the run does not read is the same run. A row VACUUM renumbered is still known by the key it was read by,
    and another row given its old rowid is another run."""
    db = database("results.db")
    update(db, "update evals set acc1 = 0.70, finished_at = null where run_name = 'r3'")
    assert rbj("criterion", "add", "INT8 keeps top-1 at 0.75 or more", "-e", "int8", "--metric", "int8.top1", "--at-least", "0.75", "--id", "c1")[0] == 0
    read = ["run", "add", "int8", "--variant", "int8", "--from", "results.db", "--table", "evals", "--set", "seed=@seed", "--where"]
    first = rbj(*read, "run_name=r3")[1].data["object"]["id"]
    update(db, "update evals set acc1 = 0.757 where run_name = 'r3'")
    before = record_files()
    code, env = rbj(*read, "run_name=r3")
    assert code == 2 and env.errors[0].message == (f"{first} was read from results.db, table evals, rowid 3, and the row gives other values now: "
                                                   "int8.acc1 0.7 recorded, 0.757 now. One row is one run.")
    assert env.errors[0].fix == (f'When {first} was read before the row was final, replace it: --replaces {first} --why "...". '
                                 'When another execution wrote the row again, --again --why "...".')
    code, env = rbj(*read, "precision=int8,seed=2")
    assert code == 2 and env.errors[0].message.startswith(f"{first} was read from results.db, table evals, rowid 3, and the row gives")
    assert record_files() == before
    code, env = rbj(*read, "run_name=r3", "--replaces", first, "--why", "read before its training finished")
    assert code == 0
    second = env.data["object"]["id"]
    finding = rbj("show", "c1")[1].data["finding"]
    assert finding["comparison"]["estimate"] == 0.757 and [o["run"] for o in finding["observations"]] == [second]
    update(db, "update evals set finished_at = '2026-05-02 09:30:00' where run_name = 'r3'")
    before = record_files()
    code, env = rbj(*read, "run_name=r3")
    assert code == 0 and env.data["duplicate_of"] == second and record_files() == before
    update(db, "delete from evals where run_name = 'r1'")
    update(db, "vacuum")
    code, env = rbj(*read, "run_name=r3")
    assert code == 0 and env.data["duplicate_of"] == second and record_files() == before
    code, env = rbj(*read, "run_name=r4")
    assert code == 0 and env.data["object"]["id"] != second and env.data["object"]["receipt"]["row"]["rowid"] == 3


def test_a_row_or_file_read_again_under_a_newer_spec_replaces_its_run(rbj):
    """Round 5: after the spec changed, reading the same row again with --replaces said 'Already recorded', so the only
    way on was to retract each run and read it again. --replaces now reads the same row, or the same file, again under
    the spec as it is now, with the same receipt, and retracts the run it replaces with the reason; the command line,
    the SDK and MCP alike. Without --replaces the same row is still the same run."""
    db = database("results.db")
    Path("metrics.json").write_text(json.dumps({"acc1": 0.74}), encoding="utf-8")
    assert rbj("criterion", "add", "INT8 keeps top-1 at 0.75 or more", "-e", "int8", "--metric", "int8.top1", "--at-least", "0.75", "--id", "c1")[0] == 0
    read = ["run", "add", "int8", "--variant", "int8", "--from", "results.db", "--table", "evals", "--set", "seed=@seed", "--where"]
    first = {w: rbj(*read, w)[1].data["object"] for w in ("run_name=r3", "run_name=r4")}
    from_file = rbj("run", "add", "int8", "--variant", "int8", "--from", "metrics.json", "--set", "seed=9")[1].data["object"]
    assert rbj("spec", "set", "int8", "calib", "512", "--no-verify")[0] == 0
    finding = rbj("show", "c1")[1].data["finding"]
    assert {o["reason"] for o in finding["observations"]} == {"under an earlier spec: calib unknown -> 512"}
    code, env = rbj(*read, "run_name=r3")
    assert code == 0 and env.data["duplicate_of"] == first["run_name=r3"]["id"]
    why = ["--why", "read again under the spec with calib"]
    code, env = rbj(*read, "run_name=r3", "--replaces", first["run_name=r3"]["id"], *why)
    assert code == 0, env.errors
    again = env.data["object"]
    assert again["id"] != first["run_name=r3"]["id"] and again["metrics"] == first["run_name=r3"]["metrics"]
    keep = ("store", "path", "sha256", "table", "where", "rowid", "columns", "per_run")
    assert {k: again["receipt"]["row"].get(k) for k in keep} == {k: first["run_name=r3"]["receipt"]["row"].get(k) for k in keep}
    old = rbj("show", first["run_name=r3"]["id"])[1].data["object"]
    assert old["retracted"]["why"] == f"replaced by {again['id']}: read again under the spec with calib"
    # the SDK reads the other row again, and MCP the file
    exp = rb.open().experiment("int8")
    sdk = exp.record_from("results.db", table="evals", where={"run_name": "r4"}, per_run={"seed": "@seed"}, variant="int8",
                          replaces=first["run_name=r4"]["id"], why="read again under the spec with calib")
    assert sdk is not None and sdk.id != first["run_name=r4"]["id"] and sdk.receipt.row.rowid == first["run_name=r4"]["receipt"]["row"]["rowid"]
    server = Mcp("claude-code")
    try:
        answer = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "run_add", "arguments": {
            "experiment": "int8", "variant": "int8", "from_file": "metrics.json", "set": {"seed": "9"}, "replaces": from_file["id"],
            "why": "read again under the spec with calib"}}})["result"]
    finally:
        server.close()
    assert not answer["isError"], answer
    assert answer["structuredContent"]["data"]["object"]["id"] != from_file["id"]
    finding = rbj("show", "c1")[1].data["finding"]
    assert sorted(o["run"] for o in finding["observations"] if o["counted"]) == sorted([again["id"], sdk.id, answer["structuredContent"]["data"]["object"]["id"]])


def test_after_a_spec_change_rb_points_at_replaces_never_at_again(rbj, capsys):
    """Review of round 6: after rb spec set made a setting per-run, rb said nothing of --replaces, and reading the same
    row again was answered with 'a second execution ... --again', which records a false second run. rb spec set says
    that the runs no longer count and how one is read again (as rb spec vary does); the refusal for the same row, file
    or values under an earlier spec names --replaces, as a warning the SDK and MCP get too; and rb status's untested
    item names the runs under the earlier spec."""
    database("results.db")
    assert rbj("criterion", "add", "INT8 keeps top-1 at 0.75 or more", "-e", "int8", "--metric", "int8.top1", "--at-least", "0.75", "--id", "c1")[0] == 0
    read = ["run", "add", "int8", "--variant", "int8", "--from", "results.db", "--table", "evals", "--set", "seed=@seed", "--where", "run_name=r3"]
    first = rbj(*read)[1].data["object"]["id"]
    code, out, _ = said(capsys, "spec", "set", "int8", "hardware", "--per-run")
    assert code == 0 and "To read one again under this spec, give its rb run add with --replaces <run>" in out
    read += ["--set", "hardware=a100"]
    code, out, err = said(capsys, *read)
    assert code == 0 and f"Already recorded as {first}" in out and "--again" not in out + err, (out, err)
    assert f"--replaces {first}" in err and "under an earlier spec" in err
    code, env = rbj(*read)
    assert env.data["duplicate_of"] == first and any(f"--replaces {first}" in w for w in env.data["warnings"])
    [item] = [i for i in rbj("status")[1].data["open"] if i["code"] == "untested"]
    assert f"1 run was recorded under an earlier spec ({first})" in item["what"] and "--replaces <run>" in item["what"]
    with pytest.warns(rb.RBWarning, match=f"--replaces {first}"):
        assert rb.open().experiment("int8").record_from("results.db", table="evals", where={"run_name": "r3"},
                                                         per_run={"seed": "@seed", "hardware": "a100"}, variant="int8") is None
    # a spec change with no run says nothing of runs
    assert rbj("experiment", "add", "FP16 against FP32", "--id", "fp16", "--baseline", "fp32", "--candidate", "fp16")[0] == 0
    code, out, _ = said(capsys, "spec", "set", "fp16", "hardware", "--per-run")
    assert code == 0 and "The spec changed" not in out


def test_one_row_read_as_a_run_in_two_experiments_is_said_at_the_write_and_by_status(rbj, capsys):
    """One row is one run: read in a second experiment, rb warns at the write, naming the run and experiment it is
    already, and rb status lists it, naming both runs and both experiments; read again in the same one it is that run."""
    database("results.db")
    assert rbj("experiment", "add", "INT8 again", "--id", "int8b", "--baseline", "fp32", "--candidate", "int8")[0] == 0
    assert rbj("spec", "set", "int8b", "seed", "--per-run")[0] == 0
    first = rbj("run", "add", "int8", "--from", "results.db", "--table", "evals", "--where", "run_name=r3", "--set", "seed=@seed")[1].data["object"]["id"]
    code, env = rbj("run", "add", "int8", "--from", "results.db", "--table", "evals", "--where", "run_name=r3", "--set", "seed=@seed")
    assert code == 0 and env.data["duplicate_of"] == first and not env.data.get("warnings")
    code, out, err = said(capsys, "run", "add", "int8b", "--from", "results.db", "--table", "evals", "--where", "run_name=r3", "--set", "seed=@seed")
    second = out.split()[0]
    assert code == 0 and f"results.db, table evals, rowid 3 is also recorded as {first} in int8: one row is one run, and it counts in both int8 and int8b" in err
    [item] = [i for i in rbj("status")[1].data["open"] if i["code"] == "row_in_several_experiments"]
    assert (item["who"], item["subject"], item["runs"], item["experiments"], item["do"]) == ("agent", second, [first, second], ["int8", "int8b"], "")
    assert item["what"] == f"{first} in int8, {second} in int8b were read from one row (results.db, table evals, rowid 3): one row is one run, and it counts in int8 and int8b"
    code, out, _ = said(capsys, "status")
    assert f"  - {item['what']}" in out
    assert rbj("retract", second, "--why", "read in the wrong experiment")[0] == 0
    assert not [i for i in rbj("status")[1].data["open"] if i["code"] == "row_in_several_experiments"]


def test_no_row_or_several_are_refused_with_how_many_and_nothing_is_written(rbj):
    database("results.db", rows=[*EVALS, EVALS[3]])
    before = record_files()
    code, env = rbj("run", "add", "int8", "--from", "results.db", "--table", "evals", "--where", "precision=int4,seed=1", "--set", "seed=1")
    [error] = env.errors
    assert code == 2 and error.code == "E_OBJECT_INVALID" and error.model_extra["matched"] == 0
    assert error.message == ("No row of table evals in results.db has precision=int4, seed=1; its first rows: rowid 1 (precision=fp32, seed=1); "
                             "rowid 2 (precision=int8, seed=1); rowid 3 (precision=int8, seed=2); rowid 4 (precision=int8, seed=3); "
                             "rowid 5 (precision=int8, seed=3).")
    code, env = rbj("run", "add", "int8", "--from", "results.db", "--table", "evals", "--where", "precision=int8", "--set", "seed=1")
    [error] = env.errors
    assert code == 2 and error.model_extra["matched"] == 4
    assert error.message == ("4 rows of table evals in results.db have precision=int8: rowid 2, 3, 4, 5; they differ in run_name (r2, r3, r4, r4), "
                             "seed (1, 2, 3, 3), finished_at (2026-05-01 11:00:00, 2026-05-02 09:30:00, 2026-05-03 08:15:00, 2026-05-03 08:15:00).")
    code, env = rbj("run", "add", "int8", "--from", "results.db", "--table", "evals", "--set", "seed=1")
    assert code == 2 and env.errors[0].message.startswith("Table evals in results.db has 5 rows: rowid 1, 2, 3, 4, 5; they differ in run_name (r1, r2, ")
    # two rows that hold the same values are told apart by their rowid
    code, env = rbj("run", "add", "int8", "--from", "results.db", "--table", "evals", "--where", "run_name=r4", "--set", "seed=3")
    assert code == 2 and env.errors[0].message.endswith("rowid 4, 5; they hold the same values.") and "--where rowid=<n>" in env.errors[0].fix
    assert record_files() == before
    code, env = rbj("run", "add", "int8", "--from", "results.db", "--table", "evals", "--where", "rowid=5", "--set", "seed=3")
    assert code == 0 and env.data["object"]["receipt"]["row"]["where"] == {"rowid": 5} and env.data["object"]["receipt"]["row"]["rowid"] == 5
    # a table or a column the database does not have is named with the ones it has
    code, env = rbj("run", "add", "int8", "--from", "results.db", "--table", "runs", "--where", "seed=1")
    assert code == 2 and env.errors[0].message == "results.db has no table runs: its tables are evals."
    code, env = rbj("run", "add", "int8", "--from", "results.db", "--table", "evals", "--where", "step=100")
    assert code == 2 and env.errors[0].message.startswith("--where step: table evals in results.db has no column step; its columns are run_name, ")
    code, env = rbj("run", "add", "int8", "--from", "results.db", "--set", "seed=1")
    assert code == 2 and env.errors[0].message == "results.db is a SQLite database, and rb reads one row of it as a run: its tables are evals (5 rows)."


def test_a_value_that_holds_a_comma_is_one_value(rbj, capsys):
    """A row may be named by text that holds commas and '=' (an override string). Quoted, it is one value on the command
    line, and a mapping keeps it whole in Python and over MCP; a --where that reads two ways is refused, never read as
    one of them, and the row's key is said in quotes."""
    db = database("results.db")
    con = sqlite3.connect(db)
    con.execute("create table runs (overrides text, bs integer, acc1 real)")
    con.executemany("insert into runs values (?, ?, ?)", [("lr=0.1", 32, 0.61), ("lr=0.1,bs=32", 32, 0.74), ("lr=0.1,bs=64", 64, 0.69),
                                                          ("lr=0.2,bs=32", 32, 0.71)])
    con.commit()
    con.close()
    row = ["--from", "results.db", "--table", "runs"]
    before = record_files()
    code, env = rbj("run", "add", "int8", *row, "--where", "overrides=lr=0.1,bs=32", "--set", "seed=1")
    assert code == 2 and env.errors[0].message == "--where overrides=lr=0.1,bs=32 reads two ways: overrides=lr=0.1 and bs=32, or overrides holding lr=0.1,bs=32."
    assert env.errors[0].fix == ("Quote a value that holds a comma, --where 'overrides=\"lr=0.1,bs=32\"', or give each pair its own --where: "
                                 "--where overrides=lr=0.1 --where bs=32.")
    assert record_files() == before
    code, env = rbj("run", "add", "int8", *row, "--where", 'overrides="lr=0.1,bs=32"', "--set", "seed=1")
    assert code == 0 and (env.data["object"]["receipt"]["row"]["rowid"], env.data["object"]["receipt"]["row"]["where"]) == (2, {"overrides": "lr=0.1,bs=32"})
    code, out, _ = said(capsys, "show", env.data["object"]["id"])
    assert '  read from results.db, table runs, rowid 2 (overrides="lr=0.1,bs=32")' in out.splitlines()
    # two pairs, each its own --where, the spaces around '=' dropped
    code, env = rbj("run", "add", "int8", *row, "--where", "overrides = lr=0.1", "--where", "bs = 32", "--set", "seed=2")
    assert code == 0 and env.data["object"]["receipt"]["row"]["where"] == {"overrides": "lr=0.1", "bs": 32} and env.data["object"]["receipt"]["row"]["rowid"] == 1
    run = rb.open().experiment("int8").record_from("results.db", table="runs", where={"overrides": "lr=0.1,bs=64"}, per_run={"seed": 3})
    assert (run.receipt.row.rowid, run.metrics) == (3, {"acc1": 0.69})
    server = Mcp("claude-code")
    try:
        answer = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "run_add", "arguments": {
            "experiment": "int8", "from_file": "results.db", "table": "runs", "where": {"overrides": "lr=0.2,bs=32"}, "set": {"seed": 4}}}})["result"]
    finally:
        server.close()
    assert not answer["isError"], answer
    over_mcp = answer["structuredContent"]["data"]["object"]
    assert (over_mcp["receipt"]["row"]["rowid"], over_mcp["receipt"]["row"]["where"], over_mcp["metrics"]) == (4, {"overrides": "lr=0.2,bs=32"}, {"acc1": 0.71})


def test_a_quoted_value_is_said_in_its_own_letters(rbj, capsys):
    """A value rb quotes so it reads as one value keeps its letters, never JSON's escapes for them."""
    update(database("results.db"), "create table runs (overrides text, acc1 real)")
    update("results.db", "insert into runs values ('précision=int8,lot=32', 0.74)")
    code, env = rbj("run", "add", "int8", "--from", "results.db", "--table", "runs", "--where", "overrides=précision=int8,lot=32", "--set", "seed=1")
    assert code == 2 and env.errors[0].fix.startswith("Quote a value that holds a comma, --where 'overrides=\"précision=int8,lot=32\"'")
    code, env = rbj("run", "add", "int8", "--from", "results.db", "--table", "runs", "--where", 'overrides="précision=int8,lot=32"', "--set", "seed=1")
    assert code == 0, env.errors
    code, out, _ = said(capsys, "show", env.data["object"]["id"])
    assert '  read from results.db, table runs, rowid 1 (overrides="précision=int8,lot=32")' in out.splitlines()


def test_a_column_named_by_an_alias_is_read_as_its_metric(rbj):
    database("results.db")
    assert rbj("criterion", "add", "INT8 keeps top-1 at 0.75 or more", "-e", "int8", "--metric", "int8.top1", "--at-least", "0.75", "--id", "c1")[0] == 0
    code, env = rbj("run", "add", "int8", "--variant", "int8", "--from", "results.db", "--table", "evals", "--where", "run_name=r3", "--set", "seed=2")
    assert code == 0 and env.data["object"]["metrics"] == {"int8.acc1": 0.757, "int8.val_loss": 1.04}
    assert env.data["object"]["receipt"]["row"]["columns"] == {"int8.acc1": "acc1", "int8.val_loss": "val_loss"}
    [observation] = rbj("show", "c1")[1].data["finding"]["observations"]
    assert observation["value"] == 0.757 and observation["counted"] and observation["basis"] == "file"


def test_select_narrows_the_metric_columns(rbj):
    database("results.db")
    where = ["--from", "results.db", "--table", "evals", "--where", "run_name=r3", "--set", "seed=2"]
    code, env = rbj("run", "add", "int8", *where, "--select", "val_loss")
    assert code == 0 and env.data["object"]["metrics"] == {"val_loss": 1.04} and env.data["object"]["receipt"]["row"]["columns"] == {"val_loss": "val_loss"}
    code, env = rbj("run", "add", "int8", *where, "--select", "acc5")
    assert code == 2 and env.errors[0].message == "--select acc5: acc5 names no metric the project declares."
    assert env.errors[0].fix.startswith("Declare it first: rb metric add acc5")
    code, env = rbj("run", "add", "int8", *where, "--select", "top5")
    assert code == 2 and env.errors[0].message.startswith("--select top5: table evals in results.db has no column top5; its columns are run_name, ")
    code, env = rbj("run", "add", "int8", *where, "--keys", "acc*")
    assert code == 2 and "--select picks" in env.errors[0].message
    code, env = rbj("run", "add", "int8", "--table", "evals", "--where", "run_name=r3", "--set", "seed=2")
    assert code == 2 and env.errors[0].message.endswith("give --from <database>.")


def test_per_run_values_and_the_produced_time_come_from_the_rows_columns(rbj, capsys):
    database("results.db")
    where = ["--from", "results.db", "--table", "evals", "--where", "run_name=r3"]
    code, out, err = said(capsys, "run", "add", "int8", *where, "--set", "seed=@seed", "--produced-at", "@finished_at")
    assert code == 0, err
    assert "  Produced: 2026-05-02 09:30:00 (reported, column finished_at)" in out.splitlines()
    run = rbj("show", out.split()[0])[1].data["object"]
    assert run["per_run"] == {"seed": 2} and run["produced_at"] == "2026-05-02T09:30:00.000+00:00" and run["produced_basis"] == "reported"
    assert (run["receipt"]["row"]["per_run"], run["receipt"]["row"]["produced_at"]) == ({"seed": "seed"}, "finished_at")
    before = record_files()
    code, env = rbj("run", "add", "int8", *where[:-1], "run_name=r4", "--set", "seed=@step")
    assert code == 2 and env.errors[0].message.startswith("--set seed=@step: table evals in results.db has no column step; its columns are ")
    code, env = rbj("run", "add", "int8", *where[:-1], "run_name=r2", "--set", "seed=@acc5")
    assert code == 2 and env.errors[0].message == "--set seed=@acc5: results.db, table evals, rowid 2 holds NULL in acc5, which is no per-run value."
    code, env = rbj("run", "add", "int8", *where[:-1], "run_name=r4", "--set", "seed=3", "--produced-at", "@acc1")
    assert code == 2 and env.errors[0].message == "--produced-at @acc1: results.db, table evals, rowid 4 holds 0.751 in acc1, which is not an ISO date or time."
    assert record_files() == before


def test_a_row_is_dated_by_its_own_time_never_by_its_databases(rbj, capsys):
    """A database's last write is not when its rows were written: a database written after a criterion was declared does
    not make an older row of it evidence declared before. A row's run is dated by --produced-at, @<column> or a time,
    and with neither, when it was made is unknown; the SDK dates it the same way."""
    db = database("results.db")
    assert rbj("criterion", "add", "INT8 keeps top-1 at 0.75 or more", "-e", "int8", "--metric", "int8.top1", "--at-least", "0.75", "--id", "c1")[0] == 0
    con = sqlite3.connect(db)
    con.execute("insert into evals values ('r5', 'int8', 4, 0.749, 1.07, 0.9, '2026-05-04 10:00:00')")
    con.commit()
    con.close()
    where = ["--variant", "int8", "--from", "results.db", "--table", "evals", "--set", "seed=@seed", "--where"]
    code, out, err = said(capsys, "run", "add", "int8", *where, "run_name=r3")
    assert code == 0, err
    assert "  Produced: when is unknown; a database's times are not its row's. --produced-at @<column> reads a time the row holds." in out.splitlines()
    run = rbj("show", out.split()[0])[1].data["object"]
    assert (run["produced_at"], run["produced_basis"]) == (None, "unknown")
    assert rbj("show", "c1")[1].data["finding"]["timing"] == "unknown"
    # the row's own time: it was made before the criterion was declared
    code, env = rbj("run", "add", "int8", *where, "run_name=r4", "--produced-at", "@finished_at")
    assert code == 0 and (env.data["object"]["produced_at"], env.data["object"]["produced_basis"]) == ("2026-05-03T08:15:00.000+00:00", "reported")
    assert rbj("show", "c1")[1].data["finding"]["timing"] == "after_evidence"
    run = rb.open().experiment("int8").record_from("results.db", table="evals", where={"run_name": "r5"}, variant="int8", per_run={"seed": "@seed"})
    assert (run.produced_at, run.produced_basis) == (None, "unknown")


def test_a_column_named_with_no_row_read_is_refused_never_kept_as_a_value(rbj):
    """@<column> is a value of the row a database gives. With no row read (typed numbers, a JSON file), it is refused
    on the command line, in Python and over MCP, never recorded as the per-run value or the time."""
    Path("metrics.json").write_text(json.dumps({"acc1": 0.74}), encoding="utf-8")
    before = record_files()
    for argv in (["int8.acc1=0.70"], ["--from", "metrics.json"]):
        code, env = rbj("run", "add", "int8", *argv, "--set", "seed=@seed")
        assert code == 2 and env.errors[0].message == ("--set seed=@seed: @seed is the value of a column of the database row --from reads, "
                                                       "and this run reads no row.")
        assert env.errors[0].fix == ("Give the value itself, --set seed=<value>; a row is read with --from <database> --table <table> "
                                     "--where <column>=<value>.")
        code, env = rbj("run", "add", "int8", *argv, "--set", "seed=1", "--produced-at", "@finished_at")
        assert code == 2 and env.errors[0].message.startswith("--produced-at @finished_at: @finished_at is the time in a column of the database row")
    exp = rb.open().experiment("int8")
    for record in (lambda: exp.record({"acc1": 0.72}, per_run={"seed": "@seed"}), lambda: exp.record_from("metrics.json", per_run={"seed": "@seed"})):
        with pytest.raises(rb.RBError) as refused:
            record()
        assert refused.value.message.startswith("--set seed=@seed: @seed is the value of a column")
    server = Mcp("claude-code")
    try:
        answer = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "run_add", "arguments": {
            "experiment": "int8", "metrics": {"acc1": 0.71}, "set": {"seed": "@seed"}}}})["result"]
    finally:
        server.close()
    assert answer["isError"] and answer["structuredContent"]["errors"][0]["message"].startswith("--set seed=@seed: @seed is the value of a column")
    assert record_files() == before


def test_a_seed_a_column_holds_as_2_0_is_the_unit_seed_2(rbj, capsys):
    """A REAL column holds a whole seed as 2.0 (pandas writes an integer column with a gap in it as REAL). The run keeps
    it as read, and it is counted in the unit seed=2, which a typed seed=2 is in too, as rb run add says it is."""
    db = database("results.db")
    update(db, "create table sweep (name text, seed real, acc1 real)")
    update(db, "insert into sweep values ('a', 2.0, 0.76)")
    assert rbj("criterion", "add", "INT8 keeps top-1 at 0.75 or more", "-e", "int8", "--metric", "int8.top1", "--at-least", "0.75", "--id", "c1")[0] == 0
    assert rbj("run", "add", "int8", "int8.acc1=0.75", "--set", "seed=2")[0] == 0
    code, out, err = said(capsys, "run", "add", "int8", "--variant", "int8", "--from", "results.db", "--table", "sweep", "--where", "name=a", "--set", "seed=@seed")
    assert code == 0 and "the two are one unit" in err
    run = rbj("show", out.split()[0])[1].data["object"]
    assert run["per_run"] == {"seed": 2.0} and isinstance(run["per_run"]["seed"], float)
    finding = rbj("show", "c1")[1].data["finding"]
    assert (finding["units"]["n"], finding["units"]["runs"]) == (1, 2) and {o["unit"] for o in finding["observations"]} == {"seed=2"}
    assert finding["comparison"]["estimate"] == pytest.approx(0.755)
    [row] = [r for r in rbj("compare", "int8")[1].data["rows"] if r["variant"] == "int8"]
    assert row["metrics"]["top1"]["n"] == 1


def test_rb_show_says_the_column_each_value_of_a_row_came_from(rbj, capsys):
    """rb show names the column a per-run value came from when its name is not the setting's, and when the run was made
    with the column that says so; a row read with no time says that time is unknown."""
    db = database("results.db")
    update(db, "create table sweep (name text, run_seed integer, ended text, acc1 real)")
    update(db, "insert into sweep values ('a', 11, '2026-05-01 10:00:00', 0.76), ('b', 12, '2026-05-01 11:00:00', 0.75)")
    read = ["run", "add", "int8", "--from", "results.db", "--table", "sweep", "--set", "seed=@run_seed", "--where"]
    code, env = rbj(*read, "name=a", "--produced-at", "@ended")
    assert code == 0
    code, out, _ = said(capsys, "show", env.data["object"]["id"])
    lines = out.splitlines()
    assert "  per-run: seed=11 (column run_seed)" in lines
    assert lines[1].startswith("  produced 2026-05-01 10:00 UTC (as reported, column ended) · recorded "), lines[1]
    code, env = rbj(*read, "name=b")
    code, out, _ = said(capsys, "show", env.data["object"]["id"])
    assert out.splitlines()[1].startswith("  produced: when is unknown · recorded ")


def sweep(db):
    """A second table, as a sweep script writes it: a column whose name holds a space, a time left empty, a row whose
    metrics were never filled in."""
    update(db, "create table sweep (name text, seed integer, \"val loss\" real, acc1 real, val_loss real, ended text)")
    update(db, "insert into sweep values ('a', 1, 1.1, 0.76, 1.04, ''), ('b', 2, 1.2, null, null, '2026-05-01 10:00:00')")


READ_SWEEP = ["run", "add", "int8", "--from", "results.db", "--table", "sweep", "--set", "seed=@seed", "--where"]


def test_a_refusal_shows_an_empty_value_in_quotes(rbj):
    sweep(database("results.db"))
    code, env = rbj(*READ_SWEEP, "name=a", "--produced-at", "@ended")
    assert code == 2 and env.errors[0].message == ('--produced-at @ended: results.db, table sweep, rowid 1 holds "" in ended, '
                                                   "which is not an ISO date or time.")


def test_a_refusal_quotes_a_column_name_that_holds_a_space_in_its_command(rbj):
    sweep(database("results.db"))
    code, env = rbj(*READ_SWEEP, "name=a", "--select", "val loss")
    assert code == 2 and env.errors[0].fix.startswith("Declare it first: rb metric add 'val loss', ")


def test_a_command_rb_gives_quotes_a_database_name_that_holds_a_space(rbj, capsys):
    database("my results.db")
    code, out, _ = said(capsys, "compile", "my results.db")
    assert "      a row is a run: rb run add <experiment> --from 'my results.db' --table <table> --where <column>=<value>" in out.splitlines()
    code, env = rbj("run", "add", "int8", "--from", "my results.db", "--set", "seed=1")
    assert code == 2 and env.errors[0].fix == "Name the row: rb run add <experiment> --from 'my results.db' --table <table> --where <column>=<value>"
    code, env = rbj("run", "add", "int8", "--from", "my results.db", "--table", "runs", "--where", "seed=1")
    assert code == 2 and env.errors[0].fix == "Name one with --table; rb compile 'my results.db' lists each with its columns and rows."


def test_a_row_whose_metric_columns_hold_no_number_is_refused_as_that_row(rbj):
    sweep(database("results.db"))
    before = record_files()
    code, env = rbj(*READ_SWEEP, "name=b")
    assert code == 2 and env.errors[0].message == "results.db, table sweep, rowid 2 holds no number in its metric columns: acc1 (NULL), val_loss (NULL)."
    assert record_files() == before


@pytest.mark.parametrize("journal", ["delete", "wal"])
def test_reading_leaves_the_database_its_bytes_time_and_directory_as_they_were(rbj, journal):
    """rb opens a database read-only, never writes it, and makes nothing beside it: no -wal, -shm or -journal, in a
    rollback-journal database and in a WAL one, which SQLite would give a -wal and an -shm on a read-only connection."""
    db = database("data/results.db", journal=journal)
    assert db.read_bytes()[18:20] == (b"\x02\x02" if journal == "wal" else b"\x01\x01")
    as_it_was = files_in("data")
    assert list(as_it_was) == ["results.db"]
    reads = [["run", "add", "int8", "--from", "data/results.db", "--table", "evals", "--where", "run_name=r3", "--set", "seed=@seed"],
             ["run", "add", "int8", "--from", "data/results.db", "--table", "evals", "--where", "run_name=r3", "--set", "seed=@seed"],
             ["run", "add", "int8", "--from", "data/results.db", "--table", "evals", "--where", "precision=int8", "--set", "seed=1"],
             ["compile", "data/results.db"], ["compile", "data"]]
    for argv in reads:
        rbj(*argv)
        assert files_in("data") == as_it_was, argv
    exp = rb.open().experiment("int8")
    assert exp.record_from("data/results.db", table="evals", where={"run_name": "r4"}, per_run={"seed": "@seed"}) is not None
    assert files_in("data") == as_it_was


def unfinished_write(path, tmp_path) -> Path:
    """A rollback-journal database whose last write spilled into its file and never finished, as a process killed
    during an update leaves it: the old pages in its -journal, which only a connection that writes rolls back."""
    db = database(path, rows=[*EVALS, *[(f"x{i}", "fp32", 100 + i, 0.5, 1.0, 0.9, "2026-05-05 00:00:00") for i in range(3000)]])
    script = ("import os, sqlite3, sys\ncon = sqlite3.connect(sys.argv[1], isolation_level=None)\ncon.execute('pragma cache_size=2')\n"
              "con.execute('begin')\ncon.execute('update evals set acc1 = acc1 + 1')\nos._exit(0)\n")
    (tmp_path / "unfinished.py").write_text(script, encoding="utf-8")
    subprocess.run([sys.executable, str(tmp_path / "unfinished.py"), str(db)], check=True)
    assert Path(f"{path}-journal").stat().st_size > 0
    return db


def test_a_database_rb_cannot_read_without_writing_to_it_is_refused_and_left_alone(rbj, tmp_path):
    """Writes still in a WAL database's -wal, and a write that did not finish in a rollback journal, are read only by a
    connection that writes: rb says so, reads nothing, and leaves every file as it was."""
    database("wal/results.db", journal="wal")
    writer = sqlite3.connect("wal/results.db")
    writer.execute("pragma wal_autocheckpoint=0")
    writer.execute("insert into evals values ('r5', 'int8', 4, 0.749, 1.07, 0.9, '2026-05-04 10:00:00')")
    writer.commit()
    try:
        as_it_was = files_in("wal")
        assert sorted(as_it_was) == ["results.db", "results.db-shm", "results.db-wal"]
        before = record_files()
        code, env = rbj("run", "add", "int8", "--from", "wal/results.db", "--table", "evals", "--where", "run_name=r5", "--set", "seed=4")
        assert code == 2 and env.errors[0].message.startswith("wal/results.db cannot be read without writing to it: its latest writes are still in wal/results.db-wal")
        code, env = rbj("compile", "wal/results.db")
        assert code == 0 and env.data["sources"][0]["note"].startswith("wal/results.db cannot be read without writing to it")
        assert files_in("wal") == as_it_was and record_files() == before
    finally:
        writer.close()
    # a write that spilled into the database file and never finished leaves a journal only a writer rolls back
    unfinished_write("journal/results.db", tmp_path)
    as_it_was = files_in("journal")
    assert sorted(as_it_was) == ["results.db", "results.db-journal"]
    before = record_files()
    code, env = rbj("run", "add", "int8", "--from", "journal/results.db", "--table", "evals", "--where", "run_name=r3", "--set", "seed=2")
    assert code == 2 and env.errors[0].message.startswith("journal/results.db cannot be read without writing to it: a write that did not finish")
    assert files_in("journal") == as_it_was and record_files() == before


def test_a_file_left_beside_a_database_never_lets_rb_read_a_write_that_did_not_finish(rbj, tmp_path):
    """An empty -wal left beside a rollback-journal database does not turn off SQLite's check for an unfinished write,
    and a journal holding one beside a WAL database is refused as well: rb reads nothing a write left half done, and
    leaves every file as it was."""
    unfinished_write("journal/results.db", tmp_path)
    Path("journal/results.db-wal").write_bytes(b"")
    database("wal/results.db", journal="wal")
    Path("wal/results.db-journal").write_bytes(Path("journal/results.db-journal").read_bytes()[:4096])
    before = record_files()
    for folder in ("journal", "wal"):
        as_it_was = files_in(folder)
        code, env = rbj("run", "add", "int8", "--from", f"{folder}/results.db", "--table", "evals", "--where", "run_name=r3", "--set", "seed=2")
        assert code == 2 and env.errors[0].message.startswith(f"{folder}/results.db cannot be read without writing to it: a write that did not finish"), env.errors
        code, env = rbj("compile", f"{folder}/results.db")
        assert code == 0 and env.data["sources"][0]["note"].startswith(f"{folder}/results.db cannot be read without writing to it: a write")
        assert files_in(folder) == as_it_was
    assert record_files() == before


@pytest.mark.skipif(sys.platform == "win32", reason="a link to a file takes a right Windows does not give by default")
def test_a_link_to_a_database_is_read_as_the_file_it_names(rbj):
    """SQLite keeps a database's -wal beside the file a link names, not beside the link: rb looks there, so writes still
    in it are refused through the link as they are without it, and nothing is read stale or written."""
    db = database("store/real.db", journal="wal")
    os.symlink(os.path.join("store", "real.db"), "results.db")
    writer = sqlite3.connect(db)
    writer.execute("pragma wal_autocheckpoint=0")
    writer.execute("update evals set acc1 = 0.999 where run_name = 'r3'")
    writer.commit()
    try:
        as_it_was, here, before = files_in("store"), sorted(os.listdir(".")), record_files()
        code, env = rbj("run", "add", "int8", "--from", "results.db", "--table", "evals", "--where", "run_name=r3", "--set", "seed=2")
        assert code == 2 and env.errors[0].message.startswith(
            "results.db cannot be read without writing to it: its latest writes are still in store/real.db-wal"), env.errors
        code, env = rbj("compile", "results.db")
        note = env.data["sources"][0]["note"]
        assert code == 0 and "cannot be read without writing to it" in note and "store/real.db-wal" in note
        assert files_in("store") == as_it_was and sorted(os.listdir(".")) == here and record_files() == before
    finally:
        writer.close()
    # once its writer has closed it, the row reads through the link as it stands
    code, env = rbj("run", "add", "int8", "--from", "results.db", "--table", "evals", "--where", "run_name=r3", "--set", "seed=2")
    assert code == 0 and env.data["object"]["metrics"]["acc1"] == 0.999


def test_the_format_says_a_row_was_read_from_sqlite_and_claims_no_more(rbj):
    """The format names the kind of file a row was read from. Whether that file is the original store, rb does not
    determine, and the format, which freezes, does not say it is."""
    code, env = rbj("schema", "run")
    described = env.data["$defs"]["RowRead"]["description"]
    assert code == 0 and "`store` is the kind of file rb read the row from" in described and "rb does not determine" in described
    assert "itself" not in described and "never a file written from it" not in described


def test_a_file_is_a_database_by_its_first_bytes_never_by_its_name(rbj):
    Path("notes.db").write_text(json.dumps({"acc1": 0.75, "val_loss": 1.1}), encoding="utf-8")
    database("results.json")
    code, env = rbj("run", "add", "int8", "--from", "notes.db", "--table", "evals", "--where", "run_name=r3", "--set", "seed=2")
    assert code == 2 and env.errors[0].message == ("--table, --where and --select read a row of a SQLite database, and notes.db is not one: "
                                                   "its first bytes are not SQLite's.")
    code, env = rbj("run", "add", "int8", "--from", "notes.db", "--set", "seed=2")
    assert code == 0 and env.data["object"]["metrics"] == {"acc1": 0.75, "val_loss": 1.1} and "row" not in rbyaml.loads(
        next(Path(".rb").rglob(f"{env.data['object']['id']}.yaml")).read_text(encoding="utf-8"))["receipt"]
    code, env = rbj("run", "add", "int8", "--from", "results.json", "--table", "evals", "--where", "run_name=r4", "--set", "seed=3")
    assert code == 0 and env.data["object"]["receipt"]["row"]["path"] == "results.json"
    sources = {s["path"]: s for s in rbj("compile", "notes.db", "results.json")[1].data["sources"]}
    assert sources["notes.db"]["kind"] == "other" and "tables" not in sources["notes.db"]
    assert sources["results.json"]["kind"] == "database" and [t["name"] for t in sources["results.json"]["tables"]] == ["evals"]


def test_a_database_that_is_not_there_or_cannot_be_read_is_said_so(rbj):
    """A mistyped path, a folder or a file rb may not read is said as what it is, never as a file whose first bytes are
    not SQLite's, which would point at an export of the store."""
    before = record_files()
    code, env = rbj("run", "add", "int8", "--from", "nosuch.db", "--table", "evals", "--where", "run_name=r3", "--set", "seed=2")
    assert code == 2 and env.errors[0].code == "E_FILE_NOT_FOUND" and env.errors[0].message == "No such file: nosuch.db"
    Path("results").mkdir()
    code, env = rbj("run", "add", "int8", "--from", "results", "--table", "evals", "--where", "run_name=r3", "--set", "seed=2")
    assert code == 2 and env.errors[0].message.startswith("results could not be read") and "SQLite" not in env.errors[0].message
    if os.name != "nt" and os.geteuid() != 0:
        os.chmod(database("locked.db"), 0)
        code, env = rbj("run", "add", "int8", "--from", "locked.db", "--table", "evals", "--where", "run_name=r3", "--set", "seed=2")
        assert code == 2 and env.errors[0].message.startswith("locked.db could not be read") and "SQLite" not in env.errors[0].message
        [source] = rbj("compile", "locked.db")[1].data["sources"]
        assert source["note"].startswith("locked.db could not be read")
    assert record_files() == before


def test_compile_lists_a_databases_tables_their_columns_rows_and_metric_columns(rbj, capsys):
    db = database("results.db")
    con = sqlite3.connect(db)
    con.execute("create table configs (id integer primary key autoincrement, precision text, batch integer)")
    con.execute("insert into configs (precision, batch) values ('int8', 256)")
    con.commit()
    con.close()
    as_it_was, before = files_in("."), record_files()
    code, env = rbj("compile", "results.db")
    assert code == 0
    [source] = env.data["sources"]
    assert source["kind"] == "database" and source["tables"] == [
        {"name": "evals", "rows": 4, "columns": ["run_name", "precision", "seed", "acc1", "val_loss", "acc5", "finished_at"],
         "metrics": {"acc1": "top1", "val_loss": "val_loss"}},
        {"name": "configs", "rows": 1, "columns": ["id", "precision", "batch"], "metrics": {}}]      # SQLite's own sqlite_sequence left out
    code, out, _ = said(capsys, "compile", "results.db")
    lines = out.splitlines()
    assert "  results.db  ·  database  ·  2 tables" in lines
    assert ("      table evals: 4 rows · columns run_name, precision, seed, acc1, val_loss, acc5, finished_at · metric columns acc1 (top1), val_loss"
            in lines)
    assert "      table configs: 1 row · columns id, precision, batch" in lines
    assert "      a row is a run: rb run add <experiment> --from results.db --table <table> --where <column>=<value>" in lines
    assert files_in(".") == as_it_was and record_files() == before


def test_the_sdk_and_mcp_read_a_row_as_the_command_line_does(rbj):
    db = database("results.db")
    Path("metrics.json").write_text(json.dumps({"acc1": 0.74}), encoding="utf-8")
    exp = rb.open().experiment("int8")
    run = exp.record_from("results.db", table="evals", where={"precision": "int8", "seed": 3}, per_run={"seed": "@seed"}, produced_at="@finished_at")
    assert run.metrics == {"acc1": 0.751, "val_loss": 1.06} and run.per_run == {"seed": 3} and run.basis == "file" and run.via == "sdk"
    assert run.receipt.command is None and run.produced_at == "2026-05-03T08:15:00.000+00:00"
    row = run.receipt.row
    assert (row.store, row.path, row.sha256, row.table, row.where, row.rowid, row.columns, row.per_run, row.produced_at) == (
        "sqlite", "results.db", sha256(db), "evals", {"precision": "int8", "seed": 3}, 4, {"acc1": "acc1", "val_loss": "val_loss"},
        {"seed": "seed"}, "finished_at")
    assert exp.record_from("results.db", table="evals", where={"run_name": "r4"}, per_run={"seed": "@seed"}) is None   # the same row
    from_json = exp.record_from("metrics.json", per_run={"seed": 9})
    assert from_json.metrics == {"acc1": 0.74} and from_json.basis == "file" and from_json.receipt.row is None and from_json.receipt.command is None
    with pytest.raises(rb.RBError) as refused:
        exp.record_from("results.db", table="evals", where={"precision": "int8"}, per_run={"seed": 1})
    assert refused.value.code == "E_OBJECT_INVALID" and refused.value.message.startswith("3 rows of table evals in results.db have precision=int8")
    server = Mcp("claude-code")
    try:
        answer = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "run_add", "arguments": {
            "experiment": "int8", "from_file": "results.db", "table": "evals", "where": {"precision": "int8", "seed": 1},
            "select": ["val_loss"], "set": {"seed": "@seed"}, "produced_at": "@finished_at"}}})["result"]
    finally:
        server.close()
    assert not answer["isError"], answer
    over_mcp = answer["structuredContent"]["data"]["object"]
    assert over_mcp["metrics"] == {"val_loss": 1.05} and over_mcp["per_run"] == {"seed": 1} and over_mcp["via"] == "mcp"
    assert {k: over_mcp["receipt"]["row"][k] for k in ("store", "path", "table", "where", "rowid", "columns", "per_run", "produced_at")} == {
        "store": "sqlite", "path": "results.db", "table": "evals", "where": {"precision": "int8", "seed": 1}, "rowid": 2,
        "columns": {"val_loss": "val_loss"}, "per_run": {"seed": "seed"}, "produced_at": "finished_at"}


def configs_database(path="results.db") -> Path:
    """A sweep's database: each config's settings in table configs, each evaluation of one in table results."""
    db = Path(path)
    con = sqlite3.connect(db)
    con.execute("create table configs (config_id text primary key, precision text, seed integer, width integer, calib text, finished_at text)")
    con.execute("create table results (result_id integer primary key, config_id text references configs(config_id), step integer, acc1 real, "
                "recorded_at text)")
    con.executemany("insert into configs values (?, ?, ?, ?, ?, ?)", [("c1", "fp32", 1, 512, None, "2026-05-01 10:00:00"),
                                                                       ("c2", "int8", 1, 512, "imagenet-val", "2026-05-01 11:00:00"),
                                                                       ("c3", "int8", 2, 512, None, "2026-05-02 11:00:00")])
    con.executemany("insert into results values (?, ?, ?, ?, ?)", [(1, "c1", 100, 0.761, "2026-05-01 10:00:00"), (2, "c2", 100, 0.755, "2026-05-01 11:00:00"),
                                                                    (3, "c3", 100, 0.757, "2026-05-02 11:00:00")])
    con.commit()
    con.close()
    return db


def test_a_setting_is_verified_from_one_column_of_one_row_of_a_database(rbj, capsys):
    """`--source results.db#configs[config_id=c2].width` names one column of the one row the brackets pick, read with
    the read-only reader as `--where` reads a row: verified with the database's sha256 and the row it read, a
    different value is a conflict, the value is read when not given, and a NULL, several rows, a column that is not the
    setting's or a --commit verify nothing. The database is left as it was."""
    db = configs_database()
    as_it_was = files_in(".")
    code, env = rbj("spec", "set", "int8", "width", "512", "--source", "results.db#configs[config_id=c2].width")
    assert code == 0 and env.data["object"]["status"] == "verified", env.errors
    resolved = env.data["object"]["source"]["resolved"]
    assert resolved["sha256"] == sha256(db) and resolved["read"] == 512 and resolved["line"] is None
    assert resolved["text"] == "results.db, table configs, rowid 2 (config_id=c2): width 512"
    code, env = rbj("spec", "set", "int8", "int8.calib", "--source", "results.db#configs[config_id=c2].calib")
    assert code == 0 and env.data["object"]["value"] == "imagenet-val" and env.data["object"]["status"] == "verified"
    code, env = rbj("spec", "set", "int8", "precision", "int8", "--source", "results.db#configs[config_id=c1].precision")
    assert env.data["object"]["status"] == "provisional"
    assert env.data["outcome"]["failures"][0]["reason"] == "the record says int8, but results.db#configs[config_id=c1].precision says fp32"
    refusals = {
        "results.db#configs[config_id=c1].calib": ("fp32.calib", "holds NULL in calib, which states no value"),
        "results.db#configs[precision=int8].width": ("width", "2 rows of table configs in results.db have precision=int8: rowid 2, 3"),
        "results.db#configs[config_id=c9].width": ("width", "No row of table configs in results.db has config_id=c9"),
        "results.db#configs[config_id=c2].seed": ("depth", "is the column seed, not depth"),
        "results.db#runs[config_id=c2].depth": ("depth", "results.db has no table runs"),
    }
    for source, (name, why) in refusals.items():
        code, env = rbj("spec", "set", "int8", name, "512", "--source", source)
        assert env.data["object"]["status"] == "provisional" and why in env.data["outcome"]["failures"][0]["reason"], (source, env.data["outcome"])
    code, env = rbj("spec", "set", "int8", "width", "512", "--source", "results.db#configs[config_id=c2].width", "--commit", "0123abc")
    assert "which rb reads where it is, never at a commit" in env.data["outcome"]["failures"][0]["reason"]
    assert files_in(".") == as_it_was
    # read again on every read: the row changed is a conflict, an integrity problem of the experiment's findings
    assert rbj("spec", "set", "int8", "width", "512", "--source", "results.db#configs[config_id=c2].width")[0] == 0
    update(db, "update configs set width = 256 where config_id = 'c2'")
    assert rbj("spec", "verify", "int8", "width")[0] == 1
    st = rbj("status")[1].data
    assert [i["code"] for i in st["open"] if i["subject"] == "int8/width"] == ["conflict"]


def test_a_key_value_table_names_the_setting_in_the_row_it_picks(rbj):
    """Review of round 9: results.db#meta[key=calib_warm].value with --term calib_warm stayed provisional, its hint
    asking for the --term it had. A key/value table names the setting in the row: the row's key is the setting's
    name, or the word --term gives; a row that names another setting still verifies nothing."""
    update("results.db", "create table meta (key text, value integer)")
    update("results.db", "insert into meta values ('calib_warm', 1024), ('calib', 512), ('width', 64)")
    for name, value, source, term in (("calib", "1024", "results.db#meta[key=calib_warm].value", ["--term", "calib_warm"]),
                                      ("calib", "512", "results.db#meta[key=calib].value", [])):
        code, env = rbj("spec", "set", "int8", name, value, "--source", source, *term)
        assert code == 0 and env.data["object"]["status"] == "verified", (source, env.data.get("outcome"))
    code, env = rbj("spec", "set", "int8", "calib", "64", "--source", "results.db#meta[key=width].value")
    assert env.data["object"]["status"] == "provisional"
    assert "is the column value, not calib" in env.data["outcome"]["failures"][0]["reason"]


def test_a_per_run_value_is_read_from_the_row_another_table_links_to_the_runs_row(rbj, capsys):
    """`--set seed=@configs.seed` reads seed from the one row of configs that holds the run's config_id, the one column
    the two tables share; `@configs[config_id].seed` names that column, as it must where they share none or several.
    The receipt names the row the value came from, and the SDK and MCP read it as the command line does."""
    configs_database()
    code, env = rbj("run", "add", "int8", "--variant", "int8", "--from", "results.db", "--table", "results", "--where", "result_id=2",
                    "--set", "seed=@configs.seed", "--produced-at", "@configs.finished_at")
    assert code == 0, env.errors
    run = env.data["object"]
    assert run["per_run"] == {"seed": 1} and run["produced_at"] == "2026-05-01T11:00:00.000+00:00"
    assert run["receipt"]["row"]["per_run"] == {"seed": "configs[config_id=c2].seed"} and run["receipt"]["row"]["produced_at"] == "configs[config_id=c2].finished_at"
    code, out, _ = said(capsys, "show", run["id"])
    assert "  per-run: seed=1 (column configs[config_id=c2].seed)" in out.splitlines()
    code, env = rbj("run", "add", "int8", "--variant", "int8", "--from", "results.db", "--table", "results", "--where", "result_id=3",
                    "--set", "seed=@configs[config_id].seed")
    assert code == 0 and env.data["object"]["per_run"] == {"seed": 2}
    update("results.db", "create table sweeps (config_id text, step integer, lr real)")
    update("results.db", "insert into sweeps values ('c1', 100, 0.1)")
    code, env = rbj("run", "add", "int8", "--variant", "fp32", "--from", "results.db", "--table", "results", "--where", "result_id=1",
                    "--set", "seed=@sweeps.lr")
    assert code == 2 and "share 2 column names (config_id, step)" in env.errors[0].message and env.errors[0].fix == "Name the column that links them: @sweeps[<column>].lr"
    exp = rb.open().experiment("int8")
    sdk = exp.record_from("results.db", table="results", where={"result_id": 1}, variant="fp32", per_run={"seed": "@configs.seed"})
    assert sdk.per_run == {"seed": 1} and sdk.receipt.row.per_run == {"seed": "configs[config_id=c1].seed"}
    server = Mcp("claude-code")
    try:
        answer = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "spec_set", "arguments": {
            "experiment": "int8", "name": "width", "source": "results.db#configs[config_id=c1].width"}}})["result"]
    finally:
        server.close()
    assert not answer["isError"], answer
    assert answer["structuredContent"]["data"]["object"]["value"] == 512 and answer["structuredContent"]["data"]["object"]["status"] == "verified"


def test_a_linked_row_that_is_not_one_row_or_holds_null_is_said_as_that_row(rbj, capsys):
    """A link that picks several rows of the other table, or none, asks for another link, never for --where (which
    picks the run's row); a NULL there is said as the linked row's, not the run's."""
    configs_database()
    update("results.db", "create table cfg2 (config_id text, seed integer)")
    update("results.db", "insert into cfg2 values ('c1', 1), ('c1', 2), ('c2', NULL)")

    def refused(result_id, value):
        code, env = rbj("run", "add", "int8", "--variant", "int8", "--from", "results.db", "--table", "results", "--where", f"result_id={result_id}",
                        "--set", f"seed={value}")
        assert code == 2, env.data
        return env.errors[0]

    several = refused(1, "@cfg2[config_id].seed")
    assert "2 rows of table cfg2 in results.db have config_id=c1" in several.message, several.message
    assert several.fix == "config_id does not pick one row of table cfg2: name the columns that do, @cfg2[<column>,<column>].seed" and "--where" not in several.fix
    null = refused(2, "@cfg2.seed")
    assert null.message == "--set seed=@cfg2.seed: results.db, table cfg2, rowid 3 (config_id=c2) holds NULL in seed, which is no per-run value.", null.message
    none = refused(3, "@cfg2.seed")
    assert none.fix == "No row of table cfg2 holds what this run's row holds in config_id: name the column that links them, @cfg2[<column>].seed", none.fix


def test_help_names_a_value_read_from_a_linked_row(rbj, capsys):
    """The command line's, the SDK's and MCP's help name @<table>.<column> where they name @<column>."""
    code, out, _ = said(capsys, "run", "add", "--help")
    assert "seed=@configs.seed (or @configs[config_id].seed)" in " ".join(out.split())
    assert '"@<table>.<column>" (or "@<table>[<link>].<column>")' in " ".join(rb.open().experiment("int8").record_from.__doc__.split())
    server = Mcp("claude-code")
    try:
        tools = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})["result"]["tools"]
    finally:
        server.close()
    assert "@configs.seed" in next(t for t in tools if t["name"] == "run_add")["description"]


def test_a_database_row_is_no_cited_number(rbj, capsys):
    """A cited criterion, a reported analysis and a cited value rest on a number someone else stated; a row of a
    database is a measurement, read as a run, so naming one there is refused, on the command line, the SDK and MCP alike.
    A setting still reads one."""
    configs_database()
    before = record_files()
    row = "results.db#results[result_id=1].acc1"
    attempts = [
        ("criterion", "add", "Top-1 matches the stored figure", "-e", "int8", "--metric", "fp32.top1", "--equals", "0.761", "--tolerance", "0.001",
         "--source", row, "--quote", "0.761"),
        ("analysis", "add", "int8", "--compare", "int8/fp32:top1=-0.006[-0.01,-0.002]", "--method", "paired bootstrap", "--unit", "seed",
         "--source", row),
        ("spec", "set", "int8", "width", "512", "--cited", "512", "--cited-source", "results.db#configs[config_id=c2].width"),
    ]
    for argv in attempts:
        code, env = rbj(*argv)
        assert code == 2 and "is a row of a database, a measurement" in env.errors[0].message, (argv, env.errors)
        assert env.errors[0].fix.startswith("Read the row as a run (rb run add")
    assert record_files() == before
    with pytest.raises(rb.RBError, match="is a row of a database"):
        rb.open().experiment("int8").criterion("Top-1 matches", metric="fp32.top1", equals=0.761, tolerance=0.001, source=row, quote="0.761")
    server = Mcp("claude-code")
    try:
        answer = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "criterion_add", "arguments": {
            "experiment": "int8", "statement": "Top-1 matches", "metric": "fp32.top1", "equals": 0.761, "tolerance": 0.001, "source": row,
            "quote": "0.761"}}})["result"]
    finally:
        server.close()
    assert answer["isError"] and "is a row of a database" in json.dumps(answer)
    assert record_files() == before
    code, out, _ = said(capsys, "criterion", "add", "--help")
    assert "database#" not in out
    assert rbj("spec", "set", "int8", "width", "--source", "results.db#configs[config_id=c2].width")[1].data["object"]["status"] == "verified"


@pytest.mark.skipif(not os.environ.get("RB_OLD_BIN"), reason="RB_OLD_BIN names an rb from before the SQLite reader")
def test_an_rb_from_before_reads_a_record_that_never_read_a_database(rbj):
    """Writes that read no database (typed numbers, a JSON file, a note) leave the record as an older rb reads it:
    status, show, doctor and log with no problem, and its own writes leave it whole for this rb."""
    Path("metrics.json").write_text(json.dumps({"int8": {"acc1": 0.75}, "fp32": {"acc1": 0.76}}), encoding="utf-8")
    assert rbj("criterion", "add", "INT8 keeps top-1 at 0.75 or more", "-e", "int8", "--metric", "int8.top1", "--at-least", "0.75", "--id", "c1")[0] == 0
    typed = rbj("run", "add", "int8", "int8.acc1=0.74", "--set", "seed=1")[1].data["object"]["id"]
    read = rbj("run", "add", "int8", "--from", "metrics.json", "--set", "seed=2")[1].data["object"]["id"]
    assert rbj("note", "add", "the held-out split is the 50k validation images", "-e", "int8")[0] == 0
    subprocess.run(["git", "add", "-A"], check=True)
    subprocess.run(["git", "commit", "-qm", "the record"], check=True)

    def older(*argv) -> dict:
        r = subprocess.run([os.environ["RB_OLD_BIN"], *argv, "--json"], capture_output=True, text=True, env=dict(os.environ))
        out = json.loads(r.stdout)
        assert out["ok"] and r.returncode in (0, 1), (argv, r.returncode, out["errors"])
        return out["data"]

    unsettled = {"state_newer", "edited_outside_rb", "merged_outside_rb"}
    assert not [i for i in older("status")["open"] if i["code"] in unsettled]
    assert older("show", "c1")["finding"]["integrity"] == []
    assert older("show", read)["object"]["files"][0]["path"] == "metrics.json" and older("show", typed)["object"]["metrics"] == {"int8.acc1": 0.74}
    assert not older("doctor").get("problems")
    older("note", "add", "an older rb's note", "-e", "int8")
    code, env = rbj("doctor")
    assert code == 0 and not env.data.get("problems")


def test_an_id_that_reads_as_a_number_too_large_to_hold_is_its_text(rbj):
    """The review of round 6: a config id 5310427e48210936 overflowed as a number, so --where refused it and a row
    went unread. A number form too large to hold is the text it is, and names its row."""
    db = database("results.db", rows=[*EVALS, ("5310427e48210936", "int8", 4, 0.753, 1.05, 0.92, "2026-05-04 08:15:00")])
    code, env = rbj("run", "add", "int8", "--from", str(db.name), "--table", "evals", "--where", "run_name=5310427e48210936", "--set", "seed=4")
    assert code == 0 and env.ok, env.errors
    assert env.data["object"]["metrics"] == {"acc1": 0.753, "val_loss": 1.05}
    assert env.data["object"]["receipt"]["row"]["where"] == {"run_name": "5310427e48210936"}


def test_a_run_read_from_a_row_is_said_to_be_a_row(rbj, capsys):
    """Review of round 6's build: rb show said a run read from a results.db row came from a file."""
    database("results.db")
    code, env = rbj("run", "add", "int8", "--from", "results.db", "--table", "evals", "--where", "precision=int8,seed=2", "--set", "seed=2")
    assert code == 0, env.errors
    rid = env.data["object"]["id"]
    code, shown, _ = said(capsys, "show", rid)
    assert shown.startswith(f"Run {rid} on int8 (row)"), shown
    assert env.data["object"]["basis"] == "file"                              # the record keeps what it kept


def test_what_a_file_holds_that_is_not_a_number_is_a_note_after_the_run(rbj, capsys):
    """D's take: rb run add --from a metrics file that also holds its run's name and times records the run, then says
    the keys it left out as a note on that run; no line opens "rb run add:" as a failure does, and --json keeps the
    note among data.warnings."""
    Path("out/seed1").mkdir(parents=True)
    Path("out/seed1/metrics.json").write_text(json.dumps({"run_id": "r1", "finished_at": "2026-05-01", "fp32": {"top1": 76.1},
                                                          "int8": {"top1": 75.9}}), encoding="utf-8")
    code, out, err = said(capsys, "run", "add", "int8", "--from", "out/seed1/metrics.json", "--set", "seed=1")
    assert code == 0, err
    assert "rb run add:" not in err + out, (out, err)
    assert "  out/seed1/metrics.json: not numbers, so not metrics: run_id, finished_at" in out.splitlines(), out
    Path("out/seed2").mkdir()
    Path("out/seed2/metrics.json").write_text(json.dumps({"run_id": "r2", "fp32": {"top1": 76.0}, "int8": {"top1": 75.8}}), encoding="utf-8")
    code, env = rbj("run", "add", "int8", "--from", "out/seed2/metrics.json", "--set", "seed=2")
    assert code == 0 and "out/seed2/metrics.json: not numbers, so not metrics: run_id" in env.data["warnings"], env.data.get("warnings")
