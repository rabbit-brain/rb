"""What a record rests on, typed underneath and quiet on screen: a command given later, a file written after its run,
a note, when a run was made against when it was recorded, the words a writer uses of its own writing, and the late
criteria of a record begun after its work.

Promises pinned here:

- A command reads as `command (as given)` only when it was given while the run was made, rb's journal watched it make
  the numbers, or a log line written by then records that launch (--command-source, checked). A command given for a
  run made earlier is kept and reads as a command given later, not observed, on every surface: rb show, rb context, the
  screens, --json (command.given), MCP and the SDK. rb run add says so, and rb status counts such runs in one item.
- The file a run's numbers were read from is the run's own output only when it existed by the time the run was made,
  or the run is a row read from the database itself. A file written later reads as "file written <date>, after the
  run" on rb show, rb page and rb open, the criteria that read it say so, and rb status counts such runs.
- A note reads as a note: never as a file's words or a result, and a number in it is never read.
- rb show and rb page say when a run was produced, as reported, and when it was recorded, and say the repository's
  state at recording as such. rb run add nudges when a file from the last hour dates the run. rb status and rb context
  print the record-start line. The page's receipt is lines, never a stored dict.
- Words an agent writes about its own writing (its runtime or model id; agent, session, memory or UNSUPPORTED about
  the record's writing) are kept and pointed out after the write, and counted; research names never are.
- An experiment's late criteria are one item: with no more runs expected it asks for nothing; with runs expected its
  command names both arms.

Every name, number and session id here is made up.
"""
import json
import os
import re
import shutil
import sqlite3
import subprocess
import time
import warnings
from pathlib import Path

import pytest

import rabbit_brain
from rabbit_brain import RBWarning
from rbtest import AGENT_VARS, Mcp, command, environment, object_card

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="a run's files are dated by git and their own times")

AGENT = {"CLAUDECODE": "1", "AI_AGENT": "claude-code_2-1-295_agent", "CLAUDE_CODE_SESSION_ID": "sess-types-4c1d", "RB_MODEL": "example-model-7"}
MAY = "2026-05-07T20:07:47Z"
LATER = "--command is kept as given and reads as a command given later, not observed"


class Rec:
    """A git repository whose user is human:ana, at their own terminal; `agent=` runs rb in an agent session."""

    def __init__(self, root: Path, home: Path) -> None:
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        gitconfig = home / "gitconfig"
        gitconfig.write_text("", encoding="utf-8")
        self.env = {k: v for k, v in environment().items() if k not in AGENT_VARS}
        self.env.update(GIT_CONFIG_GLOBAL=str(gitconfig), GIT_CONFIG_NOSYSTEM="1", RB_TELEMETRY="0", DO_NOT_TRACK="1", HOME=str(home),
                        USERPROFILE=str(home))
        for args in (["init", "-q"], ["config", "user.email", "ana@example.org"], ["config", "user.name", "ana"]):
            subprocess.run(["git", *args], cwd=root, env=self.env, check=True, capture_output=True)

    def run(self, *argv: str, agent: dict | None = None, extra: dict | None = None) -> subprocess.CompletedProcess:
        return subprocess.run([*command(), *argv], cwd=self.root, env={**self.env, **(agent or {}), **(extra or {})}, capture_output=True,
                              stdin=subprocess.DEVNULL, encoding="utf-8", errors="replace")

    def rb(self, *argv: str, agent: dict | None = None, extra: dict | None = None) -> tuple[int, dict]:
        r = self.run(*argv, "--json", agent=agent, extra=extra)
        return r.returncode, json.loads(r.stdout)

    def ok(self, *argv: str, agent: dict | None = None, extra: dict | None = None) -> dict:
        code, env = self.rb(*argv, agent=agent, extra=extra)
        assert code in (0, 1) and env["ok"], (argv, code, env["errors"])
        return env

    def text(self, *argv: str, agent: dict | None = None) -> str:
        r = self.run(*argv, agent=agent)
        assert r.returncode in (0, 1), (argv, r.returncode, r.stdout, r.stderr)
        return r.stdout

    def write(self, path: str, text: str, mtime: float | None = None) -> str:
        p = self.root / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        if mtime is not None:
            os.utime(p, (mtime, mtime))
        return path

    def run_id(self, env: dict) -> str:
        return env["data"]["object"]["id"]

    def open_items(self, code: str) -> list:
        return [o for o in self.ok("status")["data"]["open"] if o["code"] == code]


def started(rec: Rec) -> Rec:
    rec.ok("init", "INT8 ResNet-50", "--no-agents")
    rec.ok("question", "add", "Does INT8 keep top-1 within 0.5 of FP32?", "--id", "q1")
    rec.ok("metric", "add", "top1", "--unit", "%", "--maximize")
    rec.ok("experiment", "add", "INT8 vs FP32", "--id", "int8", "-q", "q1", "--baseline", "fp32", "--candidate", "int8")
    rec.ok("spec", "set", "int8", "seed", "--per-run")
    return rec


@pytest.fixture
def rec(tmp_path, tmp_path_factory) -> Rec:
    return started(Rec(tmp_path / "work", tmp_path_factory.mktemp("home")))


def at_the_users_terminal(rec: Rec, monkeypatch) -> None:
    monkeypatch.chdir(rec.root)
    for k, v in rec.env.items():
        monkeypatch.setenv(k, v)
    for k in AGENT_VARS:
        monkeypatch.delenv(k, raising=False)


def mcp(rec: Rec, *calls: tuple) -> list:
    """Each (tool, arguments) over rb mcp in this record, in an agent's session: each answer's structuredContent."""
    server = Mcp(root=rec.root, env={**rec.env, "RB_SESSION": "sess-types-mcp-1"})
    try:
        server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "clientInfo": {"name": "types-client"}}})
        out = []
        for i, (name, arguments) in enumerate(calls, 2):
            answer = server.handle({"jsonrpc": "2.0", "id": i, "method": "tools/call", "params": {"name": name, "arguments": arguments}})
            out.append(answer["result"]["structuredContent"])
        return out
    finally:
        server.close()


def told(caught) -> list:
    return [str(w.message) for w in caught if issubclass(w.category, RBWarning)]


# ---------------------------------------------------------------- a command given later


def test_a_command_given_for_an_old_run_reads_as_given_later_everywhere(rec, monkeypatch, tmp_path):
    """A paper's recipe given as the command of a run made in May is kept, and reads as a command given later, not
    observed: rb run add says so, and rb show, rb context, rb status, the screens and the page never call it the
    command (as given). A run recorded as it is made keeps its command (as given)."""
    recipe = "python eval.py --precision int8 --calib 512"
    env = rec.ok("run", "add", "int8", "fp32.top1=76.1", "int8.top1=75.9", "--set", "seed=1", "--command", recipe, "--produced-at", MAY)
    old = rec.run_id(env)
    assert [w for w in env["data"]["warnings"] if w.startswith(LATER)], env["data"]["warnings"]
    now = rec.run_id(rec.ok("run", "add", "int8", "fp32.top1=76.0", "int8.top1=75.8", "--set", "seed=2", "--command", "python eval.py --seed 2"))
    shown = rec.text("show", old)
    assert f"  command given later, not observed: {recipe}" in shown.splitlines() and "command (as given)" not in shown
    data = rec.ok("show", old)["data"]
    assert data["command"]["given"] == "later" and data["command"]["text"] == recipe and data["made_before_recorded"] is True
    assert "  command (as given): python eval.py --seed 2" in rec.text("show", now).splitlines()
    assert rec.ok("show", now)["data"]["command"]["given"] == "at_the_time"
    context = rec.text("context")
    assert f"command given later, not observed: {recipe}" in context and f"command (as given): {recipe}" not in context
    rows = {r["id"]: r for r in rec.ok("status")["data"]["runs"]}
    assert rows[old]["command"]["given"] == "later" and rows[now]["command"]["given"] == "at_the_time"
    [item] = rec.open_items("command_later")
    assert item["subject"] == old and item["records"] == [old] and item["what"].startswith(f"1 run gives a command given later, not observed ({old})")
    # the user's screens: the card's label says which it is, and the page draws no command (as given) for it
    at_the_users_terminal(rec, monkeypatch)
    facts = object_card(old).facts
    assert f"command given later, not observed: {recipe}" in facts and f"command (as given): {recipe}" not in facts
    [row] = [r for r in rabbit_brain.open().status()["runs"] if r["id"] == old]
    assert row["command"]["given"] == "later"
    rec.ok("page", "--out", str(tmp_path / "page.html"))
    page = (tmp_path / "page.html").read_text(encoding="utf-8")
    assert f"command given later, not observed: {recipe}" in page and f"command (as given): {recipe}" not in page


def test_a_command_rb_watched_or_a_log_line_records_is_the_runs(rec):
    """A command rb journal run watched make the numbers, recorded later with the time it ran, reads as the command
    (as given), and so does one a log line written by then records. A log written after the run does not make it the
    run's, and a line that does not hold the command is refused."""
    report = 'import json; print(json.dumps({"metrics": {"int8.top1": 75.7}}))'
    ran = rec.run("journal", "run", "--json", "--", "python3", "-c", report, extra={"RB_TEST_NOW": "2026-05-07T20:00:00Z"})
    assert ran.returncode == 0 and json.loads(ran.stdout)["ok"], ran.stderr
    watched = subprocess.list2cmdline(["python3", "-c", report]) if os.name == "nt" else "python3 -c '" + report + "'"
    env = rec.ok("run", "add", "int8", "int8.top1=75.7", "--set", "seed=1", "--command", watched, "--produced-at", "2026-05-07T20:00:00Z")
    assert not [w for w in env["data"].get("warnings", []) if w.startswith(LATER)]
    assert rec.ok("show", rec.run_id(env))["data"]["command"] == {
        "text": watched, "given": "observed", "how": "rb journal run watched it make these numbers, 2026-05-07"}
    may = time.mktime((2026, 5, 7, 20, 1, 0, 0, 0, -1))
    rec.write("logs/train.log", "20:00 launch: python train.py --precision int8 --seed 3\n", mtime=may)
    env = rec.ok("run", "add", "int8", "int8.top1=75.5", "--set", "seed=3", "--command", "python train.py --precision int8 --seed 3",
                 "--command-source", "logs/train.log:1", "--produced-at", "2026-05-07T20:30:00Z")
    run = rec.run_id(env)
    assert "  command (as given): python train.py --precision int8 --seed 3 (logs/train.log:1 records this launch)" in rec.text("show", run).splitlines()
    rec.write("logs/rebuilt.log", "launch: python train.py --precision int8 --seed 4\n")
    env = rec.ok("run", "add", "int8", "int8.top1=75.4", "--set", "seed=4", "--command", "python train.py --precision int8 --seed 4",
                 "--command-source", "logs/rebuilt.log", "--produced-at", "2026-05-07T20:30:00Z")
    [said] = [w for w in env["data"]["warnings"] if w.startswith(LATER)]
    assert "logs/rebuilt.log was written " in said and ", after the run." in said
    code, env = rec.rb("run", "add", "int8", "int8.top1=75.3", "--set", "seed=5", "--command", "python train.py --seed 5",
                       "--command-source", "logs/train.log:1", "--produced-at", "2026-05-07T20:30:00Z")
    assert code != 0 and env["errors"][0]["code"] == "E_SOURCE_UNRESOLVED" and "does not hold the command" in env["errors"][0]["message"]


def test_the_sdk_and_mcp_type_a_command_given_later_as_the_command_line_does(rec, monkeypatch):
    """exp.record with a command for a run made earlier warns, as MCP's run_add does in data.warnings; both read it as
    given later, and command_source cites a log line from both."""
    at_the_users_terminal(rec, monkeypatch)
    exp = rabbit_brain.open().experiment("int8")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        run = exp.record({"fp32.top1": 76.1, "int8.top1": 75.9}, per_run={"seed": 1}, command="python eval.py", produced_at=MAY)
    assert [w for w in told(caught) if w.startswith(LATER)]
    may = time.mktime((2026, 5, 7, 20, 1, 0, 0, 0, -1))
    rec.write("logs/train.log", "launch: python train.py --seed 2\n", mtime=may)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        logged = exp.record({"int8.top1": 75.8}, per_run={"seed": 2}, command="python train.py --seed 2", command_source="logs/train.log:1",
                            produced_at="2026-05-07T20:30:00Z")
    assert not [w for w in told(caught) if w.startswith(LATER)]
    rows = {r["id"]: r["command"]["given"] for r in rabbit_brain.open().status()["runs"]}
    assert rows[run.id] == "later" and rows[logged.id] == "logged"
    added, shown, logged_mcp = mcp(rec, ("run_add", {"experiment": "int8", "metrics": {"int8.top1": 75.6}, "set": {"seed": "3"},
                                                     "command": "python eval.py --seed 3", "produced_at": MAY}),
                                   ("show", {"id": "int8"}),
                                   ("run_add", {"experiment": "int8", "metrics": {"int8.top1": 75.5}, "set": {"seed": "4"},
                                                "command": "python train.py --seed 2", "command_source": "logs/train.log:1",
                                                "produced_at": "2026-05-07T20:30:00Z"}))
    assert [w for w in added["data"]["warnings"] if w.startswith(LATER)]
    assert rec.ok("show", added["data"]["object"]["id"])["data"]["command"]["given"] == "later"
    assert rec.ok("show", logged_mcp["data"]["object"]["id"])["data"]["command"]["given"] == "logged"


def commit(rec: Rec, path: str, at: str) -> None:
    """Commit one file of the scratch repository at a made-up date."""
    dated = {**rec.env, "GIT_AUTHOR_DATE": at, "GIT_COMMITTER_DATE": at}
    for args in (["add", path], ["commit", "-q", "-m", f"add {path}"]):
        subprocess.run(["git", *args], cwd=rec.root, env=dated, check=True, capture_output=True)


def test_a_recipe_in_a_document_or_an_old_file_is_never_the_runs_command(rec, monkeypatch):
    """A README's recipe line cited with --command-source is refused from the CLI, the SDK and MCP: a document gives a
    recipe, not a launch. A log written months before the run holds no launch of it either: it reads as given later."""
    rec.write("README.md", "# INT8\nTo reproduce: python eval.py --model int8 --seed 1\n")
    commit(rec, "README.md", "2026-04-01T09:00:00Z")
    argv = ("run", "add", "int8", "int8.top1=75.9", "--set", "seed=2", "--produced-at", MAY, "--command", "python eval.py --model int8 --seed 1")
    code, env = rec.rb(*argv, "--command-source", "README.md:2", agent=AGENT)
    assert code != 0 and env["errors"][0]["code"] == "E_SOURCE_UNRESOLVED" and "README.md is documentation" in env["errors"][0]["message"]
    rec.write("logs/old.log", "launch: python eval.py --model int8 --seed 1\n", mtime=time.mktime((2026, 4, 1, 9, 0, 0, 0, 0, -1)))
    env = rec.ok(*argv, "--command-source", "logs/old.log:1", agent=AGENT)
    [said] = [w for w in env["data"]["warnings"] if w.startswith(LATER)]
    assert "logs/old.log was written 2026-04-01, more than a week before the run" in said
    shown = rec.ok("show", rec.run_id(env))["data"]["command"]
    assert shown["given"] == "later" and "command (as given)" not in rec.text("show", rec.run_id(env))
    assert [o for o in rec.open_items("command_later") if rec.run_id(env) in o["records"]]
    at_the_users_terminal(rec, monkeypatch)
    exp = rabbit_brain.open().experiment("int8")
    with pytest.raises(rabbit_brain.RBError) as raised:
        exp.record({"int8.top1": 75.8}, per_run={"seed": 3}, command="python eval.py --model int8 --seed 1", command_source="README.md:2",
                   produced_at=MAY)
    assert raised.value.code == "E_SOURCE_UNRESOLVED"
    [answer] = mcp(rec, ("run_add", {"experiment": "int8", "metrics": {"int8.top1": 75.7}, "set": {"seed": "4"}, "produced_at": MAY,
                                     "command": "python eval.py --model int8 --seed 1", "command_source": "README.md:2"}))
    assert not answer["ok"] and answer["errors"][0]["code"] == "E_SOURCE_UNRESOLVED"


def test_a_watched_command_given_with_other_quotes_is_still_the_runs(rec):
    """rb journal run keeps a command quoted as a POSIX shell quotes it; the same command given to rb run add --command
    in double quotes, as typed in a terminal (and Windows' own command line form), read as given later, not observed.
    A command is matched by its words, however they are quoted."""
    report = 'import json; print(json.dumps({"metrics": {"int8.top1": 75.7}}))'
    ran = rec.run("journal", "run", "--json", "--", "python3", "-c", report, extra={"RB_TEST_NOW": "2026-05-07T20:00:00Z"})
    assert ran.returncode == 0 and json.loads(ran.stdout)["ok"], ran.stderr
    typed = 'python3 -c "' + report.replace('"', '\\"') + '"'
    env = rec.ok("run", "add", "int8", "int8.top1=75.7", "--set", "seed=1", "--command", typed, "--produced-at", "2026-05-07T20:00:00Z")
    assert not [w for w in env["data"].get("warnings", []) if w.startswith(LATER)], env["data"].get("warnings")
    assert rec.ok("show", rec.run_id(env))["data"]["command"]["given"] == "observed"


def test_a_recipe_rb_watched_today_is_not_the_command_of_a_may_run(rec, monkeypatch):
    """rb journal run watched a recipe run today; the run recorded from its file says it was made in May. The journal's
    sight is after the run, so the command reads as given later, from the CLI and the SDK."""
    write = 'import json; json.dump({"int8.top1": 75.7}, open("r3.json", "w")); json.dump({"int8.top1": 75.6}, open("r4.json", "w"))'
    ran = rec.run("journal", "run", "--json", "--", "python3", "-c", write)
    assert ran.returncode == 0 and json.loads(ran.stdout)["ok"], ran.stderr
    watched = subprocess.list2cmdline(["python3", "-c", write]) if os.name == "nt" else "python3 -c '" + write + "'"
    env = rec.ok("run", "add", "int8", "--from", "r3.json", "--set", "seed=3", "--produced-at", MAY, "--command", watched)
    given = rec.ok("show", rec.run_id(env))["data"]["command"]
    assert given["given"] == "later" and "rb's journal saw it run " in given["how"] and given["how"].endswith(", after the run")
    assert [w for w in env["data"]["warnings"] if w.startswith(LATER)]
    at_the_users_terminal(rec, monkeypatch)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        run = rabbit_brain.open().experiment("int8").record_from("r4.json", per_run={"seed": 4}, produced_at=MAY, command=watched)
    assert [w for w in told(caught) if w.startswith(LATER)]
    assert rec.ok("show", run.id)["data"]["command"]["given"] == "later"


# ---------------------------------------------------------------- a file written after its run


def test_a_file_written_after_its_run_is_not_its_own_output(rec, monkeypatch, tmp_path):
    """An export written today for a run made in May reads as a file written after the run: rb run add says so, rb
    show, the card and the page say it, the criterion that reads it says so, and rb status counts it with the way to
    read the store. A file older than its run is the run's own."""
    rec.write("exports/seed1.json", '{"fp32.top1": 76.1, "int8.top1": 75.9}')
    env = rec.ok("run", "add", "int8", "--from", "exports/seed1.json", "--set", "seed=1", "--produced-at", MAY)
    export = rec.run_id(env)
    [said] = [w for w in env["data"]["warnings"] if w.startswith("exports/seed1.json was written ")]
    assert ", after the run (produced 2026-05-07): it is not the run's own output" in said
    day = time.strftime("%Y-%m-%d", time.gmtime())
    assert f"  file written {day}, after the run: the file its numbers were read from is not the run's own output" in rec.text("show", export).splitlines()
    assert rec.ok("show", export)["data"]["file_written_after"].startswith(day)
    may = time.mktime((2026, 5, 7, 20, 0, 0, 0, 0, -1))
    rec.write("out/seed2.json", '{"fp32.top1": 76.0, "int8.top1": 75.8}', mtime=may)
    own = rec.run_id(rec.ok("run", "add", "int8", "--from", "out/seed2.json", "--set", "seed=2", "--produced-at", MAY))
    assert rec.ok("show", own)["data"]["file_written_after"] is None
    [item] = rec.open_items("file_written_after")
    assert item["records"] == [export] and "rb run add int8 --from <database> --table <table> --where <column>=<value>" in item["do"]
    rec.ok("criterion", "add", "INT8 within 0.5", "-e", "int8", "-q", "q1", "--metric", "top1", "--subject", "int8", "--reference", "fp32",
           "--at-least", "-0.5", "--id", "c1")
    [finding] = [f for f in rec.ok("status")["data"]["findings"] if f["criterion"] == "c1"]
    assert [n["text"] for n in finding["caveats"] if n["code"] == "file_written_after"] == [
        f"read from files written after their runs, not the runs' own output: {export}"]
    at_the_users_terminal(rec, monkeypatch)
    assert any(f.startswith(f"file written {day}, after the run") for f in object_card(export).facts)
    rec.ok("page", "--out", str(tmp_path / "page.html"))
    assert f"file written {day}, after the run" in (tmp_path / "page.html").read_text(encoding="utf-8")


def test_a_file_for_a_run_whose_time_is_unknown_is_not_shown_to_be_its_own(rec, monkeypatch, tmp_path):
    """A file written seconds before recording, for a run whose time is unknown, is never read as the run's own output:
    rb run add says so, rb show, the card and the page say when the file was written and that the run's time is
    unknown, the criterion says so, and rb status counts it; the SDK and MCP say the same. Its command says the time is
    unknown, never that the run was made before it was recorded."""
    rec.write("export.json", '{"fp32.top1": 76.1, "int8.top1": 75.9}')
    env = rec.ok("run", "add", "int8", "--from", "export.json", "--set", "seed=4", "--produced-at", "unknown", "--command", "python eval.py")
    run = rec.run_id(env)
    day = time.strftime("%Y-%m-%d", time.gmtime())
    assert [w for w in env["data"]["warnings"] if w.startswith(f"export.json was written {day}, and when the run was made is unknown")]
    [later] = [w for w in env["data"]["warnings"] if w.startswith(LATER)]
    assert "when it was made is unknown" in later and "made before it was recorded" not in later
    sentence = f"file written {day}; when the run was made is unknown, so rb cannot show the file is its own output"
    assert f"  {sentence}" in rec.text("show", run).splitlines()
    data = rec.ok("show", run)["data"]
    assert data["file_written"].startswith(day) and data["file_written_after"] is None
    assert [r["file_written"][:10] for r in rec.ok("status")["data"]["runs"] if r["id"] == run] == [day]
    [item] = rec.open_items("file_run_time_unknown")
    assert item["records"] == [run] and "--replaces " + run in item["do"]
    [cl] = rec.open_items("command_later")
    assert "when it was made is unknown" in cl["what"] and "made before it was recorded" not in cl["what"]
    rec.ok("criterion", "add", "INT8 within 0.5", "-e", "int8", "-q", "q1", "--metric", "top1", "--subject", "int8", "--reference", "fp32",
           "--at-least", "-0.5", "--id", "c1")
    [finding] = [f for f in rec.ok("status")["data"]["findings"] if f["criterion"] == "c1"]
    assert [n["code"] for n in finding["caveats"] if n["code"] == "file_run_time_unknown"] == ["file_run_time_unknown"]
    assert f"file written {day}; when the run was made is unknown" in rec.text("context")
    at_the_users_terminal(rec, monkeypatch)
    assert sentence in object_card(run).facts
    rec.ok("page", "--out", str(tmp_path / "page.html"))
    assert sentence in (tmp_path / "page.html").read_text(encoding="utf-8")
    rec.write("export2.json", '{"fp32.top1": 76.0, "int8.top1": 75.8}')
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        rabbit_brain.open().experiment("int8").record_from("export2.json", per_run={"seed": 5}, produced_at="unknown")
    assert [w for w in told(caught) if w.startswith(f"export2.json was written {day}, and when the run was made is unknown")]
    rec.write("export3.json", '{"fp32.top1": 76.2, "int8.top1": 75.7}')
    [answer] = mcp(rec, ("run_add", {"experiment": "int8", "from_file": "export3.json", "set": {"seed": "6"}, "produced_at": "unknown"}))
    assert [w for w in answer["data"]["warnings"] if w.startswith(f"export3.json was written {day}, and when the run was made is unknown")]


def test_a_row_read_from_the_store_is_its_own_output(rec):
    """The database a row is read from is the store itself, however late its file was written."""
    db = sqlite3.connect(rec.root / "results.db")
    db.execute("create table evals (seed integer, top1 real, ended text)")
    db.execute("insert into evals values (3, 75.7, '2026-05-07 21:00:00')")
    db.commit()
    db.close()
    env = rec.ok("run", "add", "int8", "--variant", "int8", "--from", "results.db", "--table", "evals", "--where", "seed=3", "--set", "seed=@seed",
                 "--produced-at", "@ended")
    assert rec.ok("show", rec.run_id(env))["data"]["file_written_after"] is None and not rec.open_items("file_written_after")


# ---------------------------------------------------------------- notes


def test_a_note_reads_as_a_note_and_its_numbers_are_never_results(rec):
    """A note that holds numbers and a quote reads as a note on every surface, and nothing reads its numbers: no
    finding moves, and rb reconcile finds nothing in it. A run's own note reads as a note in rb show and rb context."""
    rec.ok("run", "add", "int8", "fp32.top1=76.1", "int8.top1=75.9", "--set", "seed=1")
    rec.ok("criterion", "add", "INT8 within 0.5", "-e", "int8", "-q", "q1", "--metric", "top1", "--subject", "int8", "--reference", "fp32",
           "--at-least", "-0.5", "--id", "c1")
    before = rec.ok("show", "c1")["data"]["finding"]
    nid = rec.ok("note", "add", 'int8.top1=99.0 and fp32.top1=12.0, "as the README says"', "-e", "int8")["data"]["object"]["id"]
    after = rec.ok("show", "c1")["data"]["finding"]
    assert (after["comparison"], after["rule_outcome"], after["observations"]) == (before["comparison"], before["rule_outcome"], before["observations"])
    status = rec.text("status")
    assert f'          note {nid}: int8.top1=99.0 and fp32.top1=12.0, "as the README says"' in status.splitlines()
    assert f"- note {nid}: int8.top1=99.0" in rec.text("context")
    # what rb reconcile found holds none of the note's numbers; when it looked is the clock's, here one that holds 99 too
    found = json.dumps(rec.ok("reconcile", extra={"RB_TEST_NOW": "2026-10-18T08:15:08.997+00:00"})["data"])
    assert "99" not in re.sub(r"\d{4}-\d\d-\d\dT[\d:.]+(?:Z|[+-]\d\d:\d\d)", "<time>", found)
    run = rec.run_id(rec.ok("run", "add", "int8", "fp32.top1=76.0", "int8.top1=75.8", "--set", "seed=2", "--note", "the export was rebuilt"))
    assert "  note: the export was rebuilt" in rec.text("show", run).splitlines()
    assert f"{run} (typed)" in rec.text("context") and " · note: the export was rebuilt" in rec.text("context")


# ---------------------------------------------------------------- when a run happened


def test_a_run_says_when_it_was_produced_and_when_it_was_recorded(rec, monkeypatch, tmp_path):
    """rb show and the page say the produced time as reported beside the recording time, and the repository's state as
    the state when recorded, not when run; the page's receipt is lines, not a stored dict."""
    run = rec.run_id(rec.ok("run", "add", "int8", "fp32.top1=76.1", "int8.top1=75.9", "--set", "seed=1", "--produced-at", MAY))
    lines = rec.text("show", run).splitlines()
    assert lines[1].startswith("  produced 2026-05-07 20:07 UTC (as reported) · recorded "), lines
    assert any(line.startswith("  repository when recorded, not when run: ") for line in lines), lines
    assert not any(line.startswith("  produced: ") for line in lines)
    at_the_users_terminal(rec, monkeypatch)
    facts = object_card(run).facts
    assert facts[0].startswith("produced 2026-05-07 20:07 UTC (as reported) · recorded ")
    assert any(f.startswith("repository when recorded, not when run: ") for f in facts)
    rec.ok("page", "--out", str(tmp_path / "page.html"))
    page = (tmp_path / "page.html").read_text(encoding="utf-8")
    # the run's receipt says it as rb show does, once; its row in Runs says the day it was produced first
    # the card's head says the days once; the time and how rb knows it are its Source details row (DESIGN-PASS-1)
    assert '<span class="dim sm">in <a href="#x-' in page and " · produced 2026-05-07 · recorded " in page and "produced produced" not in page
    assert "<dt>produced</dt><dd>2026-05-07 20:07 UTC (as reported)" in page and page.count("2026-05-07 20:07 UTC (as reported)") == 1
    assert "{&#x27;" not in page and "&#x27;at&#x27;:" not in page
    # Runs says both days, once above a table whose runs share them (design pass 1, target 5)
    assert re.search(r'<p class="dim sm rq">[^<]*Produced 2026-05-07, recorded \d{4}-\d{2}-\d{2}</p>', page)


def test_a_file_from_the_last_hour_dates_the_run_and_rb_says_so(rec, monkeypatch):
    """rb run add --from a file written minutes ago, with no --produced-at, says the file dates the run; a time given
    says nothing more. The SDK's record_from says the same."""
    rec.write("out/m.json", '{"fp32.top1": 76.1, "int8.top1": 75.9}')
    env = rec.ok("run", "add", "int8", "--from", "out/m.json", "--set", "seed=1")
    assert ("out/m.json was written less than a minute before this run was recorded, and rb dates the run by it. If it holds an older run, say "
            "when it ran: --produced-at <time>.") in env["data"]["warnings"]
    rec.write("out/n.json", '{"fp32.top1": 76.0, "int8.top1": 75.8}')
    env = rec.ok("run", "add", "int8", "--from", "out/n.json", "--set", "seed=2", "--produced-at", MAY)
    assert not [w for w in env["data"].get("warnings", []) if "If it holds an older run" in w]
    rec.write("out/o.json", '{"fp32.top1": 76.2, "int8.top1": 75.7}')
    at_the_users_terminal(rec, monkeypatch)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        rabbit_brain.open().experiment("int8").record_from("out/o.json", per_run={"seed": 3})
    assert [w for w in told(caught) if w.startswith("out/o.json was written less than a minute before")]


def test_status_and_context_say_when_the_record_began(rec, tmp_path, monkeypatch):
    """A record begun after some of its runs were made says so under its title, from the witness facts, in the record
    line's sentence (0.6.1: one wording in every view), the day as rb's text gives days: in one quiet line, on rb
    status, rb context, rb open's data and rb ui alike. One whose runs all came after says nothing. A page says the
    same fact in the record line's words, its day as a page gives days, and never the old began line."""
    assert "Kept with rb since" not in rec.text("status") and rec.ok("status")["data"]["record_start"] is None
    rec.ok("run", "add", "int8", "fp32.top1=76.1", "int8.top1=75.9", "--set", "seed=1", "--produced-at", MAY)
    rec.ok("run", "add", "int8", "fp32.top1=76.0", "int8.top1=75.8", "--set", "seed=2", "--produced-at", "2026-05-08")
    rec.ok("run", "add", "int8", "fp32.top1=76.2", "int8.top1=75.7", "--set", "seed=3")
    day = rec.ok("log", "-n", "500")["data"]["entries"][0]["at"][:10]
    line = f"Kept with rb since {day}. 2 runs were recorded afterwards, not as they happened."
    assert rec.text("status").splitlines()[1] == line
    assert rec.text("context").splitlines()[2] == line
    start = rec.ok("status")["data"]["record_start"]
    assert {k: start[k] for k in ("before", "runs", "unknown")} == {"before": 2, "runs": 3, "unknown": 0}
    rec.ok("page", "--out", str(tmp_path / "page.html"))
    page = (tmp_path / "page.html").read_text(encoding="utf-8")
    assert line not in page and "what came before it is" not in page
    y, m, d = day.split("-")
    month = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()[int(m) - 1]
    said = f"Kept with rb since {int(d)} {month} {y}. 2 runs were recorded afterwards, not as they happened."
    assert page.count(f'<p class="dim sm wit">{said}</p>') == 2         # the Overview and About
    from rbtest import home
    monkeypatch.chdir(rec.root)
    for k, v in rec.env.items():
        monkeypatch.setenv(k, v)
    for k in AGENT_VARS:
        monkeypatch.delenv(k, raising=False)
    assert home(rec.root).began == line                        # what rb open's state.json and rb ui's head carry


def test_the_record_start_line_counts_two_runs_as_both(rec):
    """The pilot's record said 'after all 2 of its runs were made': two runs made before the record began read as both."""
    rec.ok("run", "add", "int8", "fp32.top1=76.1", "int8.top1=75.9", "--set", "seed=1", "--produced-at", MAY)
    assert rec.text("status").splitlines()[1].endswith(". 1 run was recorded afterwards, not as it happened.")
    rec.ok("run", "add", "int8", "fp32.top1=76.0", "int8.top1=75.8", "--set", "seed=2", "--produced-at", "2026-05-08")
    for screen in ("status", "context"):
        said = [x for x in rec.text(screen).splitlines() if x.startswith("Kept with rb since ")]
        assert len(said) == 1 and said[0].endswith(". Both runs were recorded afterwards, not as they happened."), screen


# ---------------------------------------------------------------- words about the writing


def test_words_about_who_wrote_it_are_kept_and_pointed_out(rec, monkeypatch):
    """An agent's note that says who wrote it, or how, is kept verbatim, and rb points it out after the write (text and
    data.warnings) and counts it in rb status and rb context; the SDK and MCP say the same. A human's words, and research
    names, get no such line."""
    text = "UNSUPPORTED: the top-1 drop, as example-model-7 read it"
    env = rec.ok("note", "add", text, "-e", "int8", agent=AGENT)
    n1 = env["data"]["object"]["id"]
    [said] = [w for w in env["data"]["warnings"] if w.startswith("Words about who wrote it")]
    assert said == ('Words about who wrote it, or how, in text ("UNSUPPORTED", "example-model-7"). The record keeps who made each write: say what '
                    'the text rests on instead, a file and line, or nothing.')
    second = rec.text("note", "add", "the recording agent saw it", "-e", "int8", agent=AGENT)
    assert "Words about who wrote it, or how, in text" in second
    n2 = re.search(r"^Note (n[a-z0-9]+) on int8", second, re.M).group(1)
    plain = rec.ok("note", "add", "ResNet-50, ViT-B/16 and T5-11B run on ImageNet; the PPO agent fits in GPU memory", "-e", "int8", agent=AGENT)
    assert not [w for w in plain["data"].get("warnings", []) if w.startswith("Words about who wrote it")]
    human = rec.ok("note", "add", "UNSUPPORTED by the recording agent", "-e", "int8")
    assert not [w for w in human["data"].get("warnings", []) if w.startswith("Words about who wrote it")]
    [item] = rec.open_items("writer_words")
    line = f"2 texts say who wrote them, or how (note {n1} on int8; note {n2} on int8)"
    assert item["subject"] == "int8" and item["what"] == line
    # rb's own line names where the words are, never the writer ids they hold
    assert f"  - {line}" in rec.text("status").splitlines()
    assert f"  - {line}" in rec.text("context").splitlines()
    assert [m for t in item["texts"] for m in t["marks"]] == ["UNSUPPORTED", "example-model-7", "recording agent"]
    assert "texts say who wrote them, or how" in rec.text("context")
    assert text in rec.text("show", "int8")
    at_the_users_terminal(rec, monkeypatch)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        rabbit_brain.open(agent="notebook-agent").add_note("from my memory of the run")
    assert [w for w in told(caught) if w.startswith("Words about who wrote it") and '"from my memory"' in w]
    [answer] = mcp(rec, ("note_add", {"text": "[AGENT OBSERVATION] the loss fell", "experiment": "int8"}))
    assert [w for w in answer["data"]["warnings"] if w.startswith("Words about who wrote it") and '"[AGENT OBSERVATION]"' in w]


def test_the_agent_and_its_bare_name_about_the_writing_are_pointed_out(rec, monkeypatch):
    """"The agent inferred", "written by Claude" and "numbers recalled" in a Claude session get the after-write line,
    from the CLI, the SDK and MCP; Claude-3 as a research name, and an RL agent that learned, do not."""
    for text, mark in (("The agent inferred the seed", '"The agent inferred"'), ("Written by Claude, UNSUPPORTED claim", '"Claude"'),
                       ("Numbers recalled, not read", '"Numbers recalled"')):
        env = rec.ok("note", "add", text, "-e", "int8", agent=AGENT)
        assert [w for w in env["data"]["warnings"] if w.startswith("Words about who wrote it") and mark in w], (text, env["data"].get("warnings"))
    for text in ("Claude-3 and Claude 3.5 inferred labels on ImageNet", "the agent learned to balance"):
        env = rec.ok("note", "add", text, "-e", "int8", agent=AGENT)
        assert not [w for w in env["data"].get("warnings", []) if w.startswith("Words about who wrote it")], text
    at_the_users_terminal(rec, monkeypatch)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        rabbit_brain.open(agent="notebook-agent").add_note("the agent recalled these numbers")
    assert [w for w in told(caught) if '"the agent recalled"' in w]
    [answer] = mcp(rec, ("note_add", {"text": "The agent added these runs", "experiment": "int8"}))
    assert [w for w in answer["data"]["warnings"] if '"The agent added"' in w]


def test_words_that_name_the_user_or_the_chat_as_a_source_are_pointed_out(rec, monkeypatch):
    """A session's note, why or gate reason that calls something the user's, or cites the chat or the session as its
    source, is kept verbatim and pointed out after the write, from the CLI, the SDK and MCP, and rb status and rb context
    count it as attributed_words, naming where the words are and no writer. A quoted file line that says 'researcher',
    and the user's own writes, get no such line."""
    lead = "Words that name the user or the chat as a text's source"
    note = "The researcher's rule is that INT8 must stay within 0.5; that threshold is left to the researcher."
    env = rec.ok("note", "add", note, "-e", "int8", agent=AGENT)
    nid = env["data"]["object"]["id"]
    [said] = [w for w in env["data"]["warnings"] if w.startswith(lead)]
    assert said == (f'{lead}, in text ("The researcher\'s rule", "left to the researcher"). A text says what it rests on, a file and line, '
                    'or nothing; words recalled from the user go in --words, which rb shows as words (as given).')
    gate = rec.ok("experiment", "gate", "int8", "--if-holds", "INT8 ships.", "--words", "If INT8 holds, INT8 ships.",
                  "--why", "the researcher's rule for the next step, stated in this chat", agent=AGENT)
    assert [w for w in gate["data"]["warnings"] if w.startswith(f'{lead}, in why ("the researcher\'s rule", "stated in this chat")')]
    quoted = rec.ok("note", "add", "README.md:3 reads 'the researcher's choice of seed is arbitrary'", "-e", "int8", agent=AGENT)
    assert not [w for w in quoted["data"].get("warnings", []) if w.startswith(lead)]
    human = rec.ok("note", "add", "the account given in the session is mine", "-e", "int8")
    assert not [w for w in human["data"].get("warnings", []) if w.startswith(lead)]
    [item] = rec.open_items("attributed_words")
    line = f"  - 2 texts name the user or the chat as their source (note {nid} on int8; int8 gate why)"
    assert (item["who"], item["subject"], item["what"]) == ("agent", "int8", line[4:])
    assert [m for t in item["texts"] for m in t["marks"]] == ["The researcher's rule", "left to the researcher", "the researcher's rule", "stated in this chat"]
    assert line in rec.text("status").splitlines() and line in rec.text("context").splitlines()
    assert not rec.open_items("writer_words")
    # a reason two gate writes give is one text, read once
    rec.ok("experiment", "gate", "int8", "--if-not", "INT8 waits.", "--why", "the researcher's rule for the next step, stated in this chat", agent=AGENT)
    assert rec.open_items("attributed_words")[0]["what"] == line[4:]
    assert note in rec.text("show", "int8")
    at_the_users_terminal(rec, monkeypatch)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        rabbit_brain.open(agent="notebook-agent").add_note("the account given in the session is that it was stopped by hand")
    assert [w for w in told(caught) if w.startswith(lead) and '"given in the session"' in w]
    [answer] = mcp(rec, ("note_add", {"text": "As the user said, INT8 ships if it holds", "experiment": "int8"}))
    assert [w for w in answer["data"]["warnings"] if w.startswith(lead) and '"the user said"' in w]


def test_words_that_name_the_user_or_the_chat_clear_once_taken_back_or_replaced(rec):
    """Review of group 9: attributed_words, and writer_words with it, counted a retracted note and a gate's reason a
    later gate write replaced, so nothing could clear them; the count said texts where the list said places, and its
    command, rb show, does not show a reason only the log keeps. Only text in force counts, counted by place, and a
    reason only the log keeps asks for nothing (no write but the next of its kind replaces it)."""
    first = rec.ok("note", "add", "Stopped by hand on May 7, the researcher said.", "-e", "int8", agent=AGENT)["data"]["object"]["id"]
    second = rec.ok("note", "add", "As the user said, seed 2 failed.", "-e", "int8", agent=AGENT)["data"]["object"]["id"]
    rec.ok("experiment", "gate", "int8", "--if-holds", "INT8 ships.", "--why", "the researcher's rule for the next step, stated in this chat", agent=AGENT)
    wrote = rec.ok("note", "add", "the recording agent saw seed 2 fail", "-e", "int8", agent=AGENT)["data"]["object"]["id"]
    [item] = rec.open_items("attributed_words")
    assert item["what"] == f"3 texts name the user or the chat as their source (note {first} on int8; note {second} on int8; int8 gate why)"
    assert item["do"].startswith("rb show int8   ")
    for nid in (first, second):
        rec.ok("retract", nid, "--why", "written in the wrong voice", agent=AGENT)
    [item] = rec.open_items("attributed_words")
    assert item["what"] == "1 text names the user or the chat as its source (int8 gate why)" and item["do"] == "", item
    assert "  - 1 text names the user or the chat as its source (int8 gate why)" in rec.text("status").split("Nothing to do")[1]
    rec.ok("experiment", "gate", "int8", "--if-holds", "INT8 ships next week.", "--why", "docs/plan.md:6 states the rule", agent=AGENT)
    assert not rec.open_items("attributed_words")
    assert rec.open_items("writer_words")
    rec.ok("retract", wrote, "--why", "the record keeps who wrote it", agent=AGENT)
    assert not rec.open_items("writer_words")


# ---------------------------------------------------------------- late items


def brownfield(rec: Rec) -> None:
    """Three runs made in May, recorded now, and two rules declared after them."""
    for seed, (b, c) in enumerate((("76.1", "75.9"), ("76.0", "75.8"), ("76.2", "75.9")), 1):
        rec.ok("run", "add", "int8", f"fp32.top1={b}", f"int8.top1={c}", "--set", f"seed={seed}", "--produced-at", f"2026-05-0{seed}T10:00:00Z")
    for i, at_least in enumerate(("-0.5", "-1.0"), 1):
        rec.ok("criterion", "add", f"INT8 within {at_least[1:]}", "-e", "int8", "-q", "q1", "--metric", "top1", "--subject", "int8", "--reference",
               "fp32", "--at-least", at_least, "--id", f"c{i}")


def test_late_criteria_ask_for_no_run_where_none_is_expected(rec):
    """In a record begun after its work, with no gate and no run since, an experiment's late criteria are one item
    that asks for nothing; rb status says it without a command."""
    brownfield(rec)
    [item] = rec.open_items("late")
    assert (item["subject"], item["what"], item["do"], item["criteria"]) == ("int8", "int8: 2 criteria were declared after their evidence existed", "", ["c1", "c2"])
    lines = rec.text("status").splitlines()
    at = lines.index("  - int8: 2 criteria were declared after their evidence existed")
    assert not lines[at + 1].startswith("      ")
    # it asks for nothing, so it is listed apart from the work
    assert lines[at - 1] == "Nothing to do (1)" and not [x for x in lines if x.startswith("Can be done now")]
    context = rec.text("context")
    assert "## Nothing to do\n\n  - int8: 2 criteria were declared after their evidence existed" in context
    [answer] = mcp(rec, ("status", {}))
    assert [o["do"] for o in answer["data"]["open"] if o["code"] == "late"] == [""]


def test_late_criteria_name_both_arms_where_runs_are_expected(rec):
    """With a gate set, more runs are expected: the one item names both arms, and says a reading of its own needs
    its subject's runs made after the declaration. So it does once a run is made after the record began."""
    brownfield(rec)
    rec.ok("experiment", "gate", "int8", "--if-holds", "ship INT8", "--why", "the next step")
    [item] = rec.open_items("late")
    assert item["what"] == "int8: 2 criteria were declared after their evidence existed: a reading of their own needs their subjects' runs made after the declaration"
    assert item["do"].startswith("rb run add int8 fp32.top1=<value> int8.top1=<value> --set seed=<value>")
    assert rabbit_brain.open(rec.root).status()["open"] == rec.ok("status")["data"]["open"]


@pytest.mark.skipif(not os.environ.get("RB_OLD_BIN"), reason="RB_OLD_BIN names an rb from before these guards")
def test_writes_that_use_no_new_feature_leave_the_record_as_an_older_rb_reads_it(rec):
    """A run with a command, a run from a file with --produced-at, and a note leave the record as an older rb reads
    it; only a cited log line (--command-source) adds a key to a receipt."""
    rec.write("out/m.json", '{"fp32.top1": 76.1, "int8.top1": 75.9}')
    rec.ok("run", "add", "int8", "--from", "out/m.json", "--set", "seed=1", "--produced-at", MAY, "--command", "python eval.py")
    rec.ok("note", "add", "UNSUPPORTED by the recording agent", "-e", "int8", agent=AGENT)
    for argv in (["status"], ["show", "int8"], ["doctor"]):
        r = subprocess.run([os.environ["RB_OLD_BIN"], *argv, "--json"], cwd=rec.root, capture_output=True, text=True, env=rec.env)
        env = json.loads(r.stdout)
        assert env["ok"], (argv, env["errors"])
        assert not [e for e in env["errors"] if e.get("code") in ("E_STATE_NEWER", "E_STATE_EDITED")], (argv, env["errors"])
