"""Who made each write, as the record keeps it.

The record keeps, for every write, who made it and how rb knew, through what (cli, sdk, mcp), and the chat session an
agent's write came from. A human's write carries a session only when RB_SESSION names one on purpose: the trace id an
editor's terminal sets for whoever types in it is no chat session.

An agent's write also carries agent{}: its name (its runtime's), its version (from AI_AGENT, or the MCP client's), its
provider (what it said, else rb's table of runtimes, marked inferred) and its model (only what it said: rb context
--model for its session, the context tool for its connection, RB_MODEL for one command). A session's line in the log
says those facts when they are first known or change; it records no write. Neither the facts nor the line ever land in
a record's own fields, so an rb from before them reads the record as it did. An adoption never changes a receipt's
writer, how rb knew, its channel, its session or its agent, nor the channel beside a record's writer, and rb's words
for such an edit name the field, never the values that say who wrote it.

Every name, session id and model id here is made up.
"""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from rabbit_brain import record
from rabbit_brain.models import Envelope
from rbtest import Mcp, ROOT, as_the_user, home, main, mark_seen, rbyaml, state

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="the record is kept in a git repository")

HUMAN = "human:writer-ana"
TRACE = {"CURSOR_TRACE_ID": "trace-0f3a77"}     # what every terminal of the editor sets, a human's included


def git(*args) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def rec(workdir, monkeypatch, tmp_path_factory):
    """A git repository whose human is human:writer-ana, at their own terminal, in no agent session."""
    as_the_user(monkeypatch)
    cfg = tmp_path_factory.mktemp("gitconfig") / "config"
    cfg.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(cfg))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    git("init", "-q")
    git("config", "user.email", "writer-ana@example.com")
    git("config", "user.name", "writer ana")
    return workdir


def rb(capsys, monkeypatch, *argv, env: dict = None) -> Envelope:
    """rb <argv> --json with `env` set for this one command; it must succeed."""
    with monkeypatch.context() as m:
        for k, v in (env or {}).items():
            m.setenv(k, v)
        code = main([*argv, "--json"])
    out = Envelope.model_validate_json(capsys.readouterr().out)
    assert code in (0, 1) and out.ok, (argv, code, out.errors)
    return out


def log(root: Path) -> list:
    return [json.loads(line) for line in (root / ".rb" / "log.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]


def started(capsys, monkeypatch, env: dict = None) -> None:
    """A record with one experiment and a criterion on it, written by whoever `env` makes the writer."""
    rb(capsys, monkeypatch, "init", "who wrote it", "--no-agents", env=env)
    rb(capsys, monkeypatch, "experiment", "add", "a wider layer", "--id", "e1", "--baseline", "base", "--candidate", "wide", env=env)
    rb(capsys, monkeypatch, "criterion", "add", "wide is at least as good", "-e", "e1", "--metric", "change.top1", "--at-least", "0", "--id", "c1",
       env=env)


# ---------------------------------------------------------------- the session


def test_a_human_write_in_an_editors_terminal_records_no_session(rec, capsys, monkeypatch):
    """The trace id an editor's terminal sets is no chat session of a human's: their log lines, run receipts and calls
    carry no session. An agent typing in the same terminal is in a session, and its writes carry one."""
    started(capsys, monkeypatch, env=TRACE)
    run = rb(capsys, monkeypatch, "run", "add", "e1", "change.top1=0.01", env=TRACE).data["object"]
    rb(capsys, monkeypatch, "decide", "c1", "accept", "--why", "it holds", env=TRACE)
    assert [line["actor"] for line in log(rec)] == [HUMAN] * 5
    assert not [line for line in log(rec) if "session" in line]
    assert run["receipt"].get("session") is None
    [d] = state(rec).records("decision", live=True)
    assert d.session is None
    agent = rb(capsys, monkeypatch, "run", "add", "e1", "change.top1=0.02", env={**TRACE, "CLAUDECODE": "1"}).data["object"]
    assert agent["receipt"]["session"] == log(rec)[-1]["session"] == "trace-0f3a77"


def test_rb_session_names_a_humans_session_on_purpose(rec, capsys, monkeypatch):
    """RB_SESSION is set on purpose, so it is kept for anyone, before any session the environment holds."""
    mine = {**TRACE, "RB_SESSION": "sess-ana-review-2"}
    started(capsys, monkeypatch, env=mine)
    run = rb(capsys, monkeypatch, "run", "add", "e1", "change.top1=0.01", env=mine).data["object"]
    rb(capsys, monkeypatch, "decide", "c1", "accept", "--why", "it holds", env=mine)
    assert {line.get("session") for line in log(rec)} == {"sess-ana-review-2"}
    assert run["receipt"]["session"] == "sess-ana-review-2"
    [d] = state(rec).records("decision", live=True)
    assert d.session == "sess-ana-review-2"


def test_a_humans_link_relation_and_analysis_in_an_editors_terminal_record_no_session(rec, capsys, monkeypatch):
    """The other places a write keeps its session follow the same rule: a human's question link, declared relation and
    analysis receipt carry none for the trace id of the terminal they type in."""
    started(capsys, monkeypatch, env=TRACE)
    rb(capsys, monkeypatch, "question", "add", "is the wider layer better?", "--id", "q1", env=TRACE)
    rb(capsys, monkeypatch, "criterion", "file", "c1", "q1", "--why", "it reads the wider layer", env=TRACE)
    rb(capsys, monkeypatch, "criterion", "add", "wide gains at most half", "-e", "e1", "--metric", "change.top1", "--at-most", "0.5", "--id", "c2",
       env=TRACE)
    rb(capsys, monkeypatch, "criterion", "relate", "c1", "reverses", "c2", "--orientation", "same", "--why", "both read one change", env=TRACE)
    analysis = rb(capsys, monkeypatch, "analysis", "add", "e1", "--compare", "wide/base:top1=0.01[0.0,0.02]", "--method", "paired difference",
                  env=TRACE).data["object"]
    c1 = rb(capsys, monkeypatch, "show", "c1").data["object"]
    sessions = {"question link": [(x["question"], x.get("session")) for x in c1["question_links"]],
                "relation": [(x["target"], x.get("session")) for x in c1["relations"]],
                "analysis": analysis["receipt"].get("session"), "log": [x["op"] for x in log(rec) if "session" in x]}
    assert sessions == {"question link": [("q1", None)], "relation": [("c2", None)], "analysis": None, "log": []}


# ---------------------------------------------------------------- the runtimes rb knows


CURSOR = {"CURSOR_AGENT": "1", "CURSOR_CONVERSATION_ID": "conv-test-9d1e", **TRACE}


def test_cursors_agent_is_an_agent_and_its_chat_is_the_session(rec, capsys, monkeypatch):
    """Cursor's agent runs its commands with CURSOR_AGENT=1 and the chat's CURSOR_CONVERSATION_ID: its writes are
    agent:cursor's, in that chat, not in the terminal's trace. rb init names rb's rule for its later sessions (the
    running one would not load it), and the user's screen is not an agent's to open."""
    env = rb(capsys, monkeypatch, "init", "who wrote it", env=CURSOR)
    assert env.data["actor"]["id"] == "agent:cursor" and env.data["actor"]["via"] == "detected:CURSOR_AGENT"
    assert env.data["agents"]["later"] == ["Cursor: rb skill add --cursor"] and not (rec / ".cursor").exists()
    [said, line] = log(rec)
    assert [(x["op"], x["actor"], x["session"]) for x in (said, line)] == [("session", "agent:cursor", "conv-test-9d1e"), ("init", "agent:cursor", "conv-test-9d1e")]
    with monkeypatch.context() as m:
        for k, v in CURSOR.items():
            m.setenv(k, v)
        assert main(["ui", "--json"]) == 2
    assert "agent session" in Envelope.model_validate_json(capsys.readouterr().out).errors[0].message


@pytest.mark.parametrize("variable", ["CODEX_THREAD_ID", "CODEX_SESSION_ID"])
def test_codex_named_by_its_thread_or_session_alone_is_an_agent(rec, capsys, monkeypatch, variable):
    """A Codex command with no sandbox variable still carries its thread or session id: the write is agent:codex's,
    in that chat."""
    codex = {variable: "thread-test-41b2"}
    started(capsys, monkeypatch, env=codex)
    assert {(line["actor"], line["actor_via"], line["session"]) for line in log(rec)} == {("agent:codex", f"detected:{variable}", "thread-test-41b2")}


# ---------------------------------------------------------------- what rb knows of the agent


CLAUDE = {"CLAUDECODE": "1", "AI_AGENT": "claude-code_2-1-295_agent", "CLAUDE_CODE_SESSION_ID": "sess-test-7f3c"}
OTHER = {**CLAUDE, "CLAUDE_CODE_SESSION_ID": "sess-test-other"}
NO_ID = {"CLAUDECODE": "1", "AI_AGENT": "claude-code_2-1-295_agent"}
CLAUDE_FACTS = {"name": "claude-code", "version": "2.1.295", "provider": "anthropic", "provider_from": "inferred"}


def run_file(root: Path, run: str) -> dict:
    """A run's file as rbyaml reads it, its keys in the order they are written."""
    [p] = (root / ".rb").glob(f"experiments/*/runs/{run}.yaml")
    return rbyaml.loads(p.read_text(encoding="utf-8"))


def recorded(capsys, monkeypatch, env: dict, value: str = "0.01") -> tuple:
    """A run recorded under `env`: (its log line, its receipt as stored)."""
    run = rb(capsys, monkeypatch, "run", "add", "e1", f"change.top1={value}", env=env).data["object"]["id"]
    line = [x for x in log(Path.cwd()) if x["id"] == run][-1]
    return line, run_file(Path.cwd(), run)["receipt"]


@pytest.mark.parametrize("env,actor,agent", [
    (CLAUDE, "agent:claude-code", CLAUDE_FACTS),
    ({"CODEX_SANDBOX": "seatbelt", "CODEX_THREAD_ID": "thread-test-41b2"}, "agent:codex", {"name": "codex", "provider": "openai", "provider_from": "inferred"}),
    (CURSOR, "agent:cursor", {"name": "cursor"}),
    ({"AI_AGENT": "pi-agent_0-9-3_agent"}, "agent:pi-agent", {"name": "pi-agent", "version": "0.9.3"}),
    ({"AI_AGENT": "pi-agent_0-9-3_agent", "RB_PROVIDER": "acme-labs"}, "agent:pi-agent",
     {"name": "pi-agent", "version": "0.9.3", "provider": "acme-labs", "provider_from": "env"}),
    ({**CLAUDE, "CLAUDE_CODE_USE_BEDROCK": "1"}, "agent:claude-code", {"name": "claude-code", "version": "2.1.295"}),
    ({**CLAUDE, "CLAUDE_CODE_USE_VERTEX": "true"}, "agent:claude-code", {"name": "claude-code", "version": "2.1.295"}),
    ({**CLAUDE, "CLAUDE_CODE_USE_BEDROCK": "0"}, "agent:claude-code", CLAUDE_FACTS),
    ({"GITHUB_ACTIONS": "true", "GITHUB_RUN_ID": "run-test-5150"}, "agent:github-actions", {"name": "github-actions"}),
    ({**CLAUDE, "RB_ACTOR": "agent:review-bot"}, "agent:review-bot", CLAUDE_FACTS),
    ({"RB_ACTOR": "agent:review-bot"}, "agent:review-bot", {"name": "review-bot"}),
])
def test_an_agents_write_records_its_name_version_and_provider(rec, capsys, monkeypatch, env, actor, agent):
    """Each runtime's log line and run receipt carry the same agent{}: the runtime's name, its version when AI_AGENT
    gives one, and its provider, inferred from rb's table or as the agent gave it (RB_PROVIDER, for one command: env).
    None is inferred while Claude Code runs its models through another provider's API (Bedrock, Vertex AI). No model
    is guessed."""
    started(capsys, monkeypatch, env=env)
    line, receipt = recorded(capsys, monkeypatch, env)
    assert (line["actor"], line["agent"], receipt["actor"], receipt["agent"]) == (actor, agent, actor, agent)
    assert all(x["agent"] == agent for x in log(rec) if x["op"] != "session")


def test_a_humans_write_records_no_session_and_no_agent(rec, capsys, monkeypatch):
    """A human's write names no agent: no agent{} on its log line or its receipt, and no session's line."""
    started(capsys, monkeypatch, env=TRACE)
    line, receipt = recorded(capsys, monkeypatch, TRACE)
    assert not [x for x in log(rec) if "agent" in x or "session" in x or x["op"] == "session"]
    assert "agent" not in receipt and "session" not in receipt


def test_a_model_said_once_holds_for_that_sessions_writes_and_no_others(rec, capsys, monkeypatch):
    """rb context --model keeps the model for the session's later writes, said; another session's writes carry no
    model; RB_MODEL speaks for one command and leaves what the session said in place."""
    started(capsys, monkeypatch, env=CLAUDE)
    assert "model" not in recorded(capsys, monkeypatch, CLAUDE)[0]["agent"]
    env = rb(capsys, monkeypatch, "context", "--model", "test-model-7b", env=CLAUDE)
    said = {**CLAUDE_FACTS, "model": "test-model-7b", "model_from": "said"}
    assert env.data["actor"]["agent"] == said
    line, receipt = recorded(capsys, monkeypatch, CLAUDE, "0.02")
    assert line["agent"] == receipt["agent"] == said
    line, receipt = recorded(capsys, monkeypatch, {**CLAUDE, "RB_MODEL": "test-model-env"}, "0.03")
    assert line["agent"] == receipt["agent"] == {**CLAUDE_FACTS, "model": "test-model-env", "model_from": "env"}
    assert recorded(capsys, monkeypatch, CLAUDE, "0.04")[0]["agent"] == said
    line, receipt = recorded(capsys, monkeypatch, OTHER, "0.05")
    assert line["agent"] == receipt["agent"] == CLAUDE_FACTS


def test_with_no_session_id_rb_remembers_no_model(rec, capsys, monkeypatch):
    """An agent session with no id has nothing to keep a model under: rb context --model says so and keeps nothing,
    later writes carry no model, and RB_MODEL still speaks for its one command."""
    started(capsys, monkeypatch, env=NO_ID)
    env = rb(capsys, monkeypatch, "context", "--model", "test-model-7b", env=NO_ID)
    assert "this session has no id" in env.data["warnings"][0]
    assert "model" not in recorded(capsys, monkeypatch, NO_ID)[0]["agent"]
    assert recorded(capsys, monkeypatch, {**NO_ID, "RB_MODEL": "test-model-env"}, "0.02")[0]["agent"]["model"] == "test-model-env"
    assert "model" not in recorded(capsys, monkeypatch, NO_ID, "0.03")[0]["agent"]
    assert not [x for x in log(rec) if x["op"] == "session"]
    assert not list((rec / ".rb" / "cache").glob("sessions/said/*.json"))


def test_a_model_said_in_a_humans_terminal_records_nothing(rec, capsys, monkeypatch):
    started(capsys, monkeypatch)
    env = rb(capsys, monkeypatch, "context", "--model", "test-model-7b", env={"RB_SESSION": "sess-ana-review-2"})
    assert "record nothing here" in env.data["warnings"][0] and "agent" not in env.data["actor"]
    assert not list((rec / ".rb").glob("cache/sessions/**/*.json"))
    assert [x["op"] for x in log(rec)] == ["init", "add", "add"]


def test_a_sessions_line_says_its_agent_facts_when_first_known_and_when_they_change(rec, capsys, monkeypatch):
    """One line with op "session", no hash and no path, before the session's first write and again when the agent
    says a model; never twice for the same facts. rb log's text lists writes only, --json every line."""
    started(capsys, monkeypatch, env=CLAUDE)
    rb(capsys, monkeypatch, "context", "--model", "test-model-7b", env=CLAUDE)
    rb(capsys, monkeypatch, "context", "--model", "test-model-7b", env=CLAUDE)
    recorded(capsys, monkeypatch, CLAUDE)
    recorded(capsys, monkeypatch, {**CLAUDE, "RB_MODEL": "test-model-env"}, "0.02")
    lines = log(rec)
    sessions = [x for x in lines if x["op"] == "session"]
    assert [x["agent"] for x in sessions] == [CLAUDE_FACTS, {**CLAUDE_FACTS, "model": "test-model-7b", "model_from": "said"}]
    assert lines[0]["op"] == "session" and lines[1]["op"] == "init"
    for x in sessions:
        assert (x["kind"], x["id"], x["session"]) == ("session", "sess-test-7f3c", "sess-test-7f3c")
        assert "sha256" not in x and "path" not in x and "parts" not in x
    assert len(rb(capsys, monkeypatch, "log", "-n", "50").data["entries"]) == len(lines)
    assert main(["log", "-n", "50"]) == 0
    assert " session " not in capsys.readouterr().out


def test_an_mcp_clients_version_and_the_model_its_agent_said_are_recorded(rec, capsys, monkeypatch):
    """Over MCP the version is the client's own (clientInfo.version, kept as far as it is version-shaped), and the
    model the agent gives the context tool holds for the rest of the connection when the runtime names no session,
    and the tool's answer gives the actor with it, as rb context --json does; a new connection starts with none."""
    started(capsys, monkeypatch)
    for k in ("CLAUDECODE", "AI_AGENT", "CLAUDE_CODE_SESSION_ID", "RB_SESSION"):
        monkeypatch.delenv(k, raising=False)

    def call(server, n, name, arguments):
        answer = server.handle({"jsonrpc": "2.0", "id": n, "method": "tools/call", "params": {"name": name, "arguments": arguments}})["result"]
        assert not answer["isError"], answer
        return answer

    first = Mcp()
    second = None
    try:
        first.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18",
                                                                                   "clientInfo": {"name": "Claude Code", "version": "2.1.295-beta.4"}}})
        told = call(first, 2, "context", {"model": "test-model-7b"})["structuredContent"]["data"]
        assert told["markdown"].startswith("# The record: who wrote it")
        assert told["actor"]["id"] == "agent:claude-code" and told["actor"]["agent"]["model"] == "test-model-7b"      # what rb kept, read back
        call(first, 3, "note_add", {"text": "a note from the connection", "experiment": "e1"})
        second = Mcp()
        second.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "clientInfo": {"name": "Claude Code"}}})
        call(second, 2, "note_add", {"text": "a note from a new connection", "experiment": "e1"})
    finally:
        first.close()
        if second:
            second.close()
    [said, new] = [x for x in log(rec) if x["op"] == "note"]
    assert said["actor"] == "agent:claude-code" and said["via"] == "mcp"
    assert said["agent"] == {**CLAUDE_FACTS, "model": "test-model-7b", "model_from": "said"}
    assert new["agent"] == {"name": "claude-code", "provider": "anthropic", "provider_from": "inferred"}


def test_an_agent_with_no_model_said_is_told_once_how_to_say_it(rec, capsys, monkeypatch):
    """rb context's text says how to say the model while none is recorded; rb_delta says it once, last, on the
    session's next command, and rb status does not use it up."""
    started(capsys, monkeypatch)
    env = rb(capsys, monkeypatch, "status", env=CLAUDE)
    assert env.rb_delta == []
    env = rb(capsys, monkeypatch, "note", "add", "first", "-e", "e1", env=CLAUDE)
    assert [(d["code"], d["do"]) for d in env.rb_delta][-1:] == [("model_unsaid", "rb context --model <your model id>")]
    assert not [d for d in rb(capsys, monkeypatch, "note", "add", "second", "-e", "e1", env=CLAUDE).rb_delta if d["code"] == "model_unsaid"]
    assert "rb context --model <your model id>" in rb(capsys, monkeypatch, "context", env=OTHER).data["markdown"]
    assert not [d for d in rb(capsys, monkeypatch, "note", "add", "third", "-e", "e1", env=OTHER).rb_delta if d["code"] == "model_unsaid"]
    with monkeypatch.context() as m:
        for k, v in {**CLAUDE, "CLAUDE_CODE_SESSION_ID": "sess-test-third"}.items():
            m.setenv(k, v)
        assert main(["note", "add", "fourth", "-e", "e1"]) == 0
    told = capsys.readouterr().out.split("Since this session last looked:\n")[1].split("\n\n")[0].splitlines()
    assert told[-1] == "  - No model is recorded for your writes yet   (rb context --model <your model id>)"
    rb(capsys, monkeypatch, "context", "--model", "test-model-7b", env=OTHER)
    assert "--model" not in rb(capsys, monkeypatch, "context", env=OTHER).data["markdown"].split("## Rules")[0]
    assert "--model" not in rb(capsys, monkeypatch, "context", env={**NO_ID, "RB_MODEL": "test-model-env"}).data["markdown"].split("## Rules")[0]


def test_the_manual_gives_each_way_rb_asks_for_a_model(rec, capsys, monkeypatch):
    """model_unsaid's `do` is rb context --model in a session rb can keep a model under, and RB_MODEL on each command in
    one with no id; the manual's rb_delta paragraph gives both, and says the briefing repeats it while none is
    recorded."""
    started(capsys, monkeypatch)
    dos = []
    for env in (CLAUDE, NO_ID):
        [hint] = [d for d in rb(capsys, monkeypatch, "note", "add", "a note", "-e", "e1", env=env).rb_delta if d["code"] == "model_unsaid"]
        dos.append(hint["do"])
        for _ in range(2):
            assert "No model is recorded for your writes" in rb(capsys, monkeypatch, "context", env=env).data["markdown"]
    [said] = [p for p in (ROOT / "AGENTS.md").read_text(encoding="utf-8").split("\n\n") if p.startswith("**What changed since you last looked.**")]
    assert dos == ["rb context --model <your model id>", "RB_MODEL=<your model id> on each command"]
    assert all(f"`{do}`" in said for do in dos) and "the briefing repeats" in said, said


def test_a_said_model_and_provider_are_kept_as_said(rec, capsys, monkeypatch):
    """A model's or a provider's id is kept as the agent gave it, but for the spaces around it: brackets and spaces
    included, in the record and in Python's canonical form of the receipt. One with a control character or a line
    break, or longer than 200 characters, is refused (E_USAGE), and what the session said before stands."""
    started(capsys, monkeypatch, env=CLAUDE)
    env = rb(capsys, monkeypatch, "context", "--model", " test-model-7b[1m] ", "--provider", "Acme Cloud", env=CLAUDE)
    said = {**CLAUDE_FACTS, "provider": "Acme Cloud", "provider_from": "said", "model": "test-model-7b[1m]", "model_from": "said"}
    assert env.data["actor"]["agent"] == said
    line, receipt = recorded(capsys, monkeypatch, CLAUDE)
    assert line["agent"] == receipt["agent"] == said
    body = {k: v for k, v in run_file(rec, line["id"]).items() if k not in ("rb", "kind")}
    assert rbyaml.content_sha(record.stored(record.Run.model_validate(body))) == line["sha256"]
    assert recorded(capsys, monkeypatch, {**CLAUDE, "RB_MODEL": "test-model-env[1m]"}, "0.02")[0]["agent"]["model"] == "test-model-env[1m]"
    for flag, bad in (("--model", "test-model\nsecond line"), ("--model", "test\tmodel"), ("--provider", "x" * 201)):
        with monkeypatch.context() as m:
            for k, v in CLAUDE.items():
                m.setenv(k, v)
            assert main(["context", flag, bad, "--json"]) == 2
        assert Envelope.model_validate_json(capsys.readouterr().out).errors[0].code == "E_USAGE"
    assert recorded(capsys, monkeypatch, CLAUDE, "0.03")[0]["agent"] == said


def older_store(root: Path, session: str) -> None:
    """The session's store as an rb from before agent facts writes it: its own keys, and nothing else it found there."""
    store = root / ".rb" / "cache" / "sessions" / f"{session}.json"
    store.parent.mkdir(parents=True, exist_ok=True)
    store.write_text(json.dumps({"session": session, "seen": [], "cursor": len(log(root)), "arrived": "2026-10-10T00:00:00.000+00:00",
                                 "seen_lines": []}, indent=1), encoding="utf-8")


def test_what_a_session_said_outlasts_an_older_rbs_rewrite_of_its_store(rec, capsys, monkeypatch):
    """An older rb in the same session rewrites the session's store with its own keys only. What the session said of
    its agent is kept apart from that store, so the session's later writes still carry its model."""
    started(capsys, monkeypatch, env=CLAUDE)
    rb(capsys, monkeypatch, "context", "--model", "test-model-7b", env=CLAUDE)
    older_store(rec, "sess-test-7f3c")
    line, receipt = recorded(capsys, monkeypatch, CLAUDE)
    assert line["agent"]["model"] == receipt["agent"]["model"] == "test-model-7b"


@pytest.mark.skipif(not os.environ.get("RB_OLD_BIN"), reason="RB_OLD_BIN names an rb from before session lines and agent{}")
def test_an_older_rb_in_the_same_session_leaves_the_model_said(rec, capsys, monkeypatch):
    started(capsys, monkeypatch, env=CLAUDE)
    rb(capsys, monkeypatch, "context", "--model", "test-model-7b", env=CLAUDE)
    r = subprocess.run([os.environ["RB_OLD_BIN"], "note", "add", "an older rb's note", "-e", "e1", "--json"], capture_output=True, text=True,
                       env={**os.environ, **CLAUDE})
    assert json.loads(r.stdout)["ok"], r.stdout
    assert recorded(capsys, monkeypatch, CLAUDE)[0]["agent"]["model"] == "test-model-7b"


def test_each_agent_in_a_session_says_its_facts_once(rec, capsys, monkeypatch):
    """A script the agent runs (the SDK, named train-script) and the agent itself write in one session: each has one
    session line, through its channel, however their writes alternate."""
    started(capsys, monkeypatch)
    with monkeypatch.context() as m:
        for k, v in CLAUDE.items():
            m.setenv(k, v)
        script = state(rec, agent="train-script")
        for value in (0.01, 0.02, 0.03):
            with script.experiment("e1").run("wide") as run:
                run.log({"change.top1": value})
            assert main(["note", "add", f"after {value}", "-e", "e1"]) == 0
    capsys.readouterr()
    lines = log(rec)
    assert [(x["actor"], x["via"], x["agent"]["name"]) for x in lines if x["op"] == "session"] == [
        ("agent:train-script", "sdk", "train-script"), ("agent:claude-code", "cli", "claude-code")]
    assert [x["op"] for x in lines if x["op"] in ("record", "note")] == ["record", "note"] * 3


@pytest.mark.parametrize("blocked", ["a file where its sessions go", "a read-only record"])
def test_rb_context_gives_the_briefing_when_it_cannot_keep_the_model(rec, capsys, monkeypatch, blocked):
    """rb context --model is an agent's first command: where the record cannot keep the model (a read-only checkout,
    a sandbox), it says so, keeps nothing, and still gives the briefing, exit 0."""
    if blocked == "a read-only record" and (os.name == "nt" or os.geteuid() == 0):
        pytest.skip("a folder is read-only only for a user that is not root, where POSIX permissions hold")
    started(capsys, monkeypatch)
    codex = {"CODEX_SANDBOX": "seatbelt", "CODEX_THREAD_ID": "thread-test-ro1"}
    sessions = rec / ".rb" / "cache" / "sessions"
    shutil.rmtree(sessions, ignore_errors=True)
    if blocked == "a file where its sessions go":
        sessions.write_text("not a folder\n", encoding="utf-8")
    else:
        subprocess.run(["chmod", "-R", "a-w", str(rec / ".rb")], check=True)
    try:
        with monkeypatch.context() as m:
            for k, v in codex.items():
                m.setenv(k, v)
            code = main(["context", "--model", "test-model-ro"])
            said = capsys.readouterr()
            assert main(["context", "--model", "test-model-ro", "--json"]) == 0
            env = Envelope.model_validate_json(capsys.readouterr().out)
    finally:
        if blocked == "a read-only record":
            subprocess.run(["chmod", "-R", "u+w", str(rec / ".rb")], check=True)
    assert code == 0 and said.out.startswith("# The record: who wrote it"), said
    assert "rb context: what you gave is not kept for this session's writes" in said.err, said.err
    assert env.ok and env.data["markdown"].startswith("# The record: who wrote it") and "model" not in env.data["actor"]["agent"]
    assert not [x for x in log(rec) if x["op"] == "session"]


def test_rb_logs_text_lists_the_last_writes_past_session_lines(rec, capsys, monkeypatch):
    """A session's line records no write: rb log -n N lists the last N writes when session lines are among the log's
    last lines, and says "Nothing logged." only where there is no write; --json gives the last N lines as they are."""
    started(capsys, monkeypatch)
    rb(capsys, monkeypatch, "context", "--model", "test-model-7b", env=CLAUDE)
    assert [x["op"] for x in log(rec)] == ["init", "add", "add", "session"]
    for n, ids in ((1, ["c1"]), (3, ["who-wrote-it", "e1", "c1"])):
        assert main(["log", "-n", str(n)]) == 0
        listed = capsys.readouterr().out.splitlines()
        assert [line.split(": ")[0].split()[-1] for line in listed] == ids, listed
    assert [x["op"] for x in rb(capsys, monkeypatch, "log", "-n", "1").data["entries"]] == ["session"]


def test_no_reading_of_the_log_as_writes_counts_a_sessions_line(rec, capsys, monkeypatch):
    """A session's line records no write, so nothing that reads the log as writes shows or counts it: rb context's
    recent activity and rb://context, what rb_delta tells another session, and the screen's since line, from a look
    rb remembers line by line and from an earlier rb's count."""
    started(capsys, monkeypatch)
    codex = {"CODEX_SANDBOX": "seatbelt", "CODEX_THREAD_ID": "thread-test-41b2"}
    rb(capsys, monkeypatch, "status", env=codex)                                    # the Codex session looks first
    mark_seen()                                                                     # and so does the user
    counted = {"log_lines": len(log(rec)), "at": "2026-10-10T00:00:00.000+00:00"}   # a count alone, as an earlier rb keeps
    rb(capsys, monkeypatch, "note", "add", "a note from the session", "-e", "e1", env=CLAUDE)
    assert [x["op"] for x in log(rec)][-2:] == ["session", "note"]
    briefing = rb(capsys, monkeypatch, "context").data["markdown"]
    server = Mcp()
    try:
        server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "clientInfo": {"name": "t"}}})
        resource = server.handle({"jsonrpc": "2.0", "id": 2, "method": "resources/read", "params": {"uri": "rb://context"}})["result"]
    finally:
        server.close()
    told = [(d["code"], d["subject"]) for d in rb(capsys, monkeypatch, "show", "c1", env=codex).rb_delta if d["kind"] == "write"]
    since = home().since
    (Path(".git") / "rb-last-seen.json").write_text(json.dumps(counted), encoding="utf-8")
    old, _ = mark_seen()
    since_count = home(since_baseline=old).since
    recent = [text.split("## Recent activity")[1] for text in (briefing, resource["contents"][0]["text"])]
    counts = {
        "rb context": [" session " in r or "sess-test-7f3c" in r for r in recent],
        "rb_delta": told,
        "since": ["1 write" in s and "2 writes" not in s for s in (since, since_count)],
    }
    assert counts == {"rb context": [False, False], "rb_delta": [("note", "e1")], "since": [True, True]}, (recent, since, since_count)


def test_python_writes_a_receipt_with_its_agent_in_the_order_rb_does(rec, capsys, monkeypatch):
    """The stored form Python's record model gives a run whose receipt carries agent{} is the file rb wrote, key for
    key and in order, and ordering it back from canonical JSON gives the same: one canonical form, so one hash."""
    started(capsys, monkeypatch, env=CLAUDE)
    rb(capsys, monkeypatch, "context", "--model", "test-model-7b", env=CLAUDE)
    run = rb(capsys, monkeypatch, "run", "add", "e1", "change.top1=0.01", "--command", "python eval.py", env=CLAUDE).data["object"]["id"]
    body = {k: v for k, v in run_file(rec, run).items() if k not in ("rb", "kind")}
    assert list(body["receipt"])[:9] == ["at", "actor", "actor_via", "via", "rb_version", "command", "session", "agent", "attached"]
    stored = record.stored(record.Run.model_validate(body))
    assert json.dumps(stored) == json.dumps(body)
    back = record.ordered(record.Run, json.loads(rbyaml.canonical_json(body)))
    assert back == body and list(back["receipt"]) == list(body["receipt"]) and list(back["receipt"]["agent"]) == list(body["receipt"]["agent"])
    assert [x for x in log(rec) if x["id"] == run][-1]["sha256"] == rbyaml.content_sha(stored)


@pytest.mark.skipif(not os.environ.get("RB_OLD_BIN"), reason="RB_OLD_BIN names an rb from before session lines and agent{}")
def test_an_rb_from_before_reads_a_record_with_session_lines_and_agent_facts(rec, capsys, monkeypatch):
    """A record written in an agent session, with nothing an older rb lacks but the session lines and agent{} on log
    lines and on run and analysis receipts: the older rb reads it as it read records before (status, show, doctor and
    log with no problem), and its own later writes leave the record whole for this rb."""
    started(capsys, monkeypatch, env=CLAUDE)
    rb(capsys, monkeypatch, "context", "--model", "test-model-7b", env=CLAUDE)
    run = rb(capsys, monkeypatch, "run", "add", "e1", "change.top1=0.01", env=CLAUDE).data["object"]["id"]
    rb(capsys, monkeypatch, "analysis", "add", "e1", "--compare", "wide/base:top1=0.01[0.0,0.02]", "--method", "paired difference", env=CLAUDE)
    git("add", "-A")
    git("commit", "-qm", "the record")
    assert [x["op"] for x in log(rec)].count("session") == 2 and run_file(rec, run)["receipt"]["agent"]["model"] == "test-model-7b"

    def older(*argv) -> dict:
        r = subprocess.run([os.environ["RB_OLD_BIN"], *argv, "--json"], capture_output=True, text=True, env=dict(os.environ))
        out = json.loads(r.stdout)
        assert out["ok"] and r.returncode in (0, 1), (argv, r.returncode, out["errors"])
        return out["data"]

    unsettled = {"state_newer", "edited_outside_rb", "merged_outside_rb"}
    assert not [i for i in older("status")["open"] if i["code"] in unsettled]
    assert older("show", "c1")["finding"]["integrity"] == []
    assert older("show", run)["object"]["receipt"]["agent"]["model"] == "test-model-7b"
    assert not older("doctor").get("problems")
    assert len(older("log", "-n", "100")["entries"]) == len(log(rec))
    older("note", "add", "an older rb's note", "-e", "e1")
    older("retract", run, "--why", "an older rb takes it back")
    assert run_file(rec, run)["receipt"]["agent"]["model"] == "test-model-7b"
    assert main(["doctor", "--json"]) == 0
    assert not Envelope.model_validate_json(capsys.readouterr().out).data.get("problems")
    assert not [i for i in rb(capsys, monkeypatch, "status").data["open"] if i["code"] in unsettled]


# ---------------------------------------------------------------- what an adoption cannot change


def hand_edit(path: Path, change) -> None:
    """A hand edit of a record file, outside rb, that leaves it a valid record."""
    doc = rbyaml.loads(path.read_text(encoding="utf-8"))
    change(doc)
    path.write_text(rbyaml.dumps(doc), encoding="utf-8")


@pytest.mark.parametrize("field,value", [
    ("actor", HUMAN), ("actor_via", "git-email"), ("via", "sdk"), ("session", "sess-test-forged"),
    ("agent", {**CLAUDE_FACTS, "model": "test-model-forged", "model_from": "said"}),
])
def test_an_adoption_never_changes_who_recorded_a_run(rec, capsys, monkeypatch, field, value):
    """A run's receipt keeps who recorded it, how rb knew, through what, in which session and what rb knew of the
    agent: rb doctor --adopt refuses a hand edit of any of them (E_ADOPT_AUTHORITY), whoever adopts, and rb doctor
    --restore puts it back. An edit to the run's own note is adopted as it stands."""
    started(capsys, monkeypatch, env=CLAUDE)
    run = rb(capsys, monkeypatch, "run", "add", "e1", "change.top1=0.01", env=CLAUDE).data["object"]["id"]
    [path] = rec.glob(f".rb/experiments/e1/runs/{run}.yaml")
    kept = rbyaml.loads(path.read_text(encoding="utf-8"))["receipt"][field]
    hand_edit(path, lambda d: d["receipt"].update({field: value}))
    assert main(["doctor", "--adopt", "--why", "the run was the user's", "--json"]) == 2
    error = Envelope.model_validate_json(capsys.readouterr().out).errors[0]
    assert error.code == "E_ADOPT_AUTHORITY" and any(p.startswith(f"{run}.receipt.{field}") for p in error.problems), error
    rb(capsys, monkeypatch, "doctor", "--restore")
    assert rbyaml.loads(path.read_text(encoding="utf-8"))["receipt"][field] == kept
    hand_edit(path, lambda d: d.update(note="read on the held-out half"))
    rb(capsys, monkeypatch, "doctor", "--adopt", "--why", "the note says which half")


def test_an_adoption_never_changes_the_channel_a_record_was_written_through(rec, capsys, monkeypatch):
    """`via` beside who wrote a record says through what they wrote it: a hand edit of it is refused like one of the
    writer."""
    started(capsys, monkeypatch, env=CLAUDE)
    path = rec / ".rb" / "experiments" / "e1" / "criteria" / "c1.yaml"
    hand_edit(path, lambda d: d.update(via="mcp"))
    assert main(["doctor", "--adopt", "--why", "it came over MCP", "--json"]) == 2
    error = Envelope.model_validate_json(capsys.readouterr().out).errors[0]
    assert error.code == "E_ADOPT_AUTHORITY" and any(p.startswith("c1.via") for p in error.problems), error


def test_a_run_rb_never_wrote_is_adopted_unless_it_names_a_human(rec, capsys, monkeypatch):
    """A run file copied by hand from an agent's run claims no one's authority, so it is adopted as it stands, its
    receipt's agent, channel and session included. One whose receipt names a human is refused (E_ADOPT_AUTHORITY):
    only rb records a human's write."""
    started(capsys, monkeypatch, env=CLAUDE)
    run = rb(capsys, monkeypatch, "run", "add", "e1", "change.top1=0.01", env=CLAUDE).data["object"]["id"]
    [path] = rec.glob(f".rb/experiments/e1/runs/{run}.yaml")
    doc = rbyaml.loads(path.read_text(encoding="utf-8"))
    path.with_name("runhandmd.yaml").write_text(rbyaml.dumps({**doc, "id": "runhandmd"}), encoding="utf-8")
    [adopted] = rb(capsys, monkeypatch, "doctor", "--adopt", "--why", "the copy is a run of its own").data["adopted"]
    assert adopted["done"] == "adopted as it stands"
    human = {**doc, "id": "runhandhm", "receipt": {**doc["receipt"], "actor": HUMAN, "actor_via": "git-email"}}
    path.with_name("runhandhm.yaml").write_text(rbyaml.dumps(human), encoding="utf-8")
    assert main(["doctor", "--adopt", "--why", "the user ran it", "--json"]) == 2
    error = Envelope.model_validate_json(capsys.readouterr().out).errors[0]
    assert (error.code, error.problems) == ("E_ADOPT_AUTHORITY", ["runhandhm was not written by rb, and as it stands it asserts: receipt.actor"])


def refused_adoption(capsys) -> list:
    """rb doctor --adopt, refused: the problems it gives, the same in its text and in --json."""
    assert main(["doctor", "--adopt", "--why", "the user wrote it"]) == 2
    text = [line[4:] for line in capsys.readouterr().err.splitlines() if line.startswith("  - ")]
    assert main(["doctor", "--adopt", "--why", "the user wrote it", "--json"]) == 2
    error = Envelope.model_validate_json(capsys.readouterr().out).errors[0]
    assert error.code == "E_ADOPT_AUTHORITY" and error.problems == text, (error, text)
    return text


def test_rbs_words_for_an_edit_name_no_writer(rec, capsys, monkeypatch):
    """What rb says of an edit made outside rb names each field that changed and never who made the write, how rb
    knew, through what, in which session or with which agent, nor a branch: not where it refuses the adoption, in text
    or in --json, not for a record it never wrote, and not in rb show's list of what changed, which still gives every
    other value. The record keeps all of them."""
    started(capsys, monkeypatch, env=CLAUDE)
    rb(capsys, monkeypatch, "context", "--model", "test-model-7b", env=CLAUDE)
    run = rb(capsys, monkeypatch, "run", "add", "e1", "change.top1=0.01", env=CLAUDE).data["object"]["id"]
    [path] = rec.glob(f".rb/experiments/e1/runs/{run}.yaml")

    def forged(d):
        d["receipt"].update(actor=HUMAN, actor_via="git-email", via="sdk", session="sess-test-forged")
        d["receipt"]["agent"] = {**d["receipt"]["agent"], "version": "9.9.9", "model": "test-model-forged"}
        d["receipt"]["attached"]["branch"] = "review/try-forged"
        d["note"] = "read on the held-out half"
    hand_edit(path, forged)
    fields = ("actor", "actor_via", "agent", "session", "via")
    assert refused_adoption(capsys) == [f"{run}.receipt.{f}: changed since rb wrote it" for f in fields]
    assert main(["show", run]) in (0, 1)
    warned = capsys.readouterr().err
    changes = rb(capsys, monkeypatch, "show", run).data["edited_outside_rb"]["changes"]
    assert changes == ["note: (absent) -> read on the held-out half", *(f"receipt.{f} changed" for f in
                       ("actor", "actor_via", "agent.model", "agent.version", "attached.branch", "session", "via"))]
    written = ("agent:claude-code", HUMAN, "detected:CLAUDECODE", "git-email", "sess-test-7f3c", "sess-test-forged", "test-model-7b",
               "test-model-forged", "anthropic", "2.1.295", "9.9.9", "review/try-forged")
    assert "receipt.actor changed" in warned and not [w for w in written if w in warned], warned
    rb(capsys, monkeypatch, "doctor", "--restore")
    assert (run_file(rec, run)["receipt"]["actor"], run_file(rec, run)["receipt"]["agent"]["model"]) == ("agent:claude-code", "test-model-7b")
    criterion = rec / ".rb" / "experiments" / "e1" / "criteria" / "c1.yaml"
    hand_edit(criterion, lambda d: d.update(created_by=HUMAN, via="mcp"))
    assert refused_adoption(capsys) == ["c1.created_by: changed since rb wrote it", "c1.via: changed since rb wrote it"]
    rb(capsys, monkeypatch, "doctor", "--restore")
    copy = {**rbyaml.loads(criterion.read_text(encoding="utf-8")), "id": "c9", "created_by": HUMAN}
    criterion.with_name("c9.yaml").write_text(rbyaml.dumps(copy), encoding="utf-8")
    assert refused_adoption(capsys) == ["c9 was not written by rb, and as it stands it asserts: created_by"]
