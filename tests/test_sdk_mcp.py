"""The Python SDK and `rb mcp` are the same state and the same rules as the CLI, reached another way.

Pinned here: a run block attaches what it logged only when it ends cleanly, with the script's command and `via: sdk`;
the person's calls raise E_HUMAN_ONLY with the `rb` line to hand over; over MCP every call is recorded as the connecting
agent whatever RB_ACTOR says, and a person's call comes back as a handoff without `--json` in it."""
import io
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

import rabbit_brain as rb
from rabbit_brain import RBError
from rabbit_brain.mcp import TOOLS, Server, argv_for, serve

HAS_GIT = shutil.which("git") is not None
AGENT_VARS = ("CLAUDECODE", "AI_AGENT", "GITHUB_ACTIONS", "RB_ACTOR")


@pytest.fixture
def project(workdir, monkeypatch):
    for k in list(os.environ):
        if k in AGENT_VARS or k.startswith("CODEX_"):
            monkeypatch.delenv(k, raising=False)
    Path("configs").mkdir()
    Path("configs/train.yaml").write_text("optim:\n  lr: 1e-4\ndata:\n  batch_size: 6\n", encoding="utf-8")
    if HAS_GIT:
        for cmd in (["init", "-q"], ["config", "user.email", "t@example.com"], ["config", "user.name", "t"], ["add", "."], ["commit", "-qm", "init"]):
            subprocess.run(["git", *cmd], check=True, capture_output=True)
    else:
        monkeypatch.setenv("USER", "t")
    return workdir


def started():
    state = rb.init("INT8 quality")
    exp = state.add_experiment("INT8 vs FP32", id="int8", baseline="fp32", candidates=["int8"], varies=["precision"])
    exp.spec.set("precision", "fp32", variant="fp32", source="note:default")
    exp.spec.set("precision", "int8", variant="int8", source="note:quantised")
    return state, exp


def test_sdk_reads_a_value_from_its_key_and_verifies_it(project):
    state, exp = started()
    s = exp.spec.set("optim.lr", source="configs/train.yaml#optim.lr")
    assert s.value == pytest.approx(1e-4) and s.status == "verified"
    assert "optim.lr" in exp.spec and exp.spec["optim.lr"].status == "verified"


def test_run_block_attaches_what_it_logged_with_the_scripts_command(project, monkeypatch):
    monkeypatch.setattr("sys.argv", ["eval.py", "--seed", "1"])
    state, exp = started()
    exp.spec.set("seed", per_run=True)
    with exp.run(seed=1) as run:
        run.log({"fp32.epe": 5.61})
        run.log(**{"int8.epe": 5.63})
    ev = run.evidence
    assert ev is not None and ev.metrics == {"fp32.epe": 5.61, "int8.epe": 5.63} and ev.per_run == {"seed": 1}
    assert ev.basis == "logged" and ev.receipt.command == "python eval.py --seed 1" and ev.receipt.via == "sdk"
    last = [json.loads(line) for line in Path(".rb/log.jsonl").read_text().splitlines()][-1]
    assert last["via"] == "sdk" and last["op"] == "attach"


def test_run_block_that_raises_attaches_nothing(project):
    state, exp = started()
    with pytest.raises(ValueError):
        with exp.run() as run:
            run.log(epe=1.0)
            raise ValueError("the eval crashed")
    assert run.evidence is None and not state.ledger.all("evidence")


def test_run_block_that_logs_nothing_is_an_error_not_silence(project):
    state, exp = started()
    with pytest.raises(RBError) as e:
        with exp.run():
            pass
    assert e.value.code == "E_OBJECT_INVALID"


def test_run_block_refuses_a_value_that_is_not_a_number(project):
    state, exp = started()
    with pytest.raises(RBError):
        with exp.run() as run:
            run.log(epe="5.6")


def test_identical_evidence_is_not_attached_twice(project):
    state, exp = started()
    assert exp.attach({"int8.epe": 5.6}) is not None
    assert exp.attach({"int8.epe": 5.6}) is None
    assert exp.attach({"int8.epe": 5.6}, again="a deliberate repeat") is not None


def test_sdk_verdict_matches_the_rules(project):
    state, exp = started()
    claim = exp.claim("INT8 costs at most 0.05", metric="change.epe", at_most=0.05)
    with exp.run() as run:
        run.log({"fp32.epe": 5.60, "int8.epe": 5.62})
    v = claim.verdict()
    assert v.status == "supported" and not v.established and "provisional" in v.not_established_because
    assert v.observations[0].value == pytest.approx(0.02) and v.observations[0].value == 0.02   # no float noise from the subtraction


def test_sdk_person_calls_raise_with_the_rb_line_for_an_agent(project, monkeypatch):
    started()
    state = rb.open(agent="my-agent")
    assert state.actor == "agent:my-agent"
    with pytest.raises(RBError) as e:
        state.experiment("int8").freeze(why="before runs")
    assert e.value.code == "E_HUMAN_ONLY" and e.value.extra["handoff"]["command"] == "rb freeze int8 -m 'before runs'"


def test_sdk_claim_needs_exactly_one_criterion(project):
    state, exp = started()
    with pytest.raises(RBError):
        exp.claim("two criteria", metric="epe", at_most=1, at_least=0)


# ---------------------------------------------------------------- MCP

def rpc(server, method, params=None, id=1):
    return server.handle({"jsonrpc": "2.0", "id": id, "method": method, "params": params or {}})


def test_mcp_tools_map_onto_real_commands():
    from rabbit_brain.cli import COMMANDS
    for t in TOOLS:
        words = " ".join(argv_for(t["name"], {k: "x" for k in t.get("required", [])})[:2])
        assert any(words == c or words.split(" ")[0] == c for c in COMMANDS), t["name"]


def test_mcp_argv_for_evidence_and_settings():
    assert argv_for("evidence_attach", {"experiment": "int8", "metrics": {"int8.epe": 5.64}, "set": {"seed": 2}, "again": True, "why": "repeat"}) == \
        ["evidence", "attach", "int8", "int8.epe=5.64", "--set", "seed=2", "--again", "--why", "repeat"]
    assert argv_for("spec_set", {"experiment": "e", "name": "crop", "value": [288, 960], "keys": ["a.*", "b"]}) == \
        ["spec", "set", "e", "crop", "[288, 960]", "--keys", "a.*,b"]
    assert argv_for("metric_add", {"name": "epe", "direction": "minimize", "alias": ["endpoint_error"]}) == \
        ["metric", "add", "epe", "--minimize", "--alias", "endpoint_error"]
    with pytest.raises(ValueError):
        argv_for("status", {"sneaky": 1})


def test_mcp_records_every_call_as_the_agent_whatever_rb_actor_says(project, monkeypatch):
    started()
    monkeypatch.setenv("RB_ACTOR", "human:t")
    server = Server()
    init = rpc(server, "initialize", {"protocolVersion": "2025-06-18", "clientInfo": {"name": "Claude Desktop"}})
    assert init["result"]["serverInfo"]["name"] == "rabbit-brain" and "person" in init["result"]["instructions"]
    r = rpc(server, "tools/call", {"name": "question_add", "arguments": {"text": "Does batch size matter?"}})["result"]
    assert not r["isError"] and r["structuredContent"]["data"]["actor"]["id"] == "agent:claude-desktop"
    last = [json.loads(line) for line in Path(".rb/log.jsonl").read_text().splitlines()][-1]
    assert last["actor"] == "agent:claude-desktop" and last["via"] == "mcp"


def test_mcp_person_call_returns_a_handoff_without_json(project):
    started()
    server = Server(agent="claude-code")
    r = rpc(server, "tools/call", {"name": "freeze", "arguments": {"experiment": "int8", "why": "before runs"}})["result"]
    err = r["structuredContent"]["errors"][0]
    assert r["isError"] and err["code"] == "E_HUMAN_ONLY"
    assert err["handoff"]["command"] == "rb freeze int8 --why 'before runs'"
    assert "RB_ACTOR" in err["fix"]


def test_mcp_context_resource_and_tool_are_markdown(project):
    started()
    server = Server(agent="claude-code")
    tool = rpc(server, "tools/call", {"name": "context", "arguments": {}})["result"]
    assert tool["content"][0]["text"].startswith("# Research state: INT8 quality")
    res = rpc(server, "resources/read", {"uri": "rb://context"})["result"]
    assert res["contents"][0]["text"].startswith("# Research state")
    docs = rpc(server, "resources/read", {"uri": "rb://docs"})["result"]
    assert docs["contents"][0]["text"].startswith("# Rabbit Brain: AGENTS.md")


def test_mcp_errors_are_answers_not_crashes(project):
    started()
    server = Server(agent="a")
    assert rpc(server, "tools/call", {"name": "nope"})["error"]["code"] == -32602
    assert rpc(server, "no/such")["error"]["code"] == -32601
    r = rpc(server, "tools/call", {"name": "show", "arguments": {"id": "zzzz"}})["result"]
    assert r["isError"] and r["structuredContent"]["errors"][0]["code"] == "E_OBJECT_NOT_FOUND"
    assert server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


def test_mcp_serves_line_delimited_json_over_stdio(project):
    started()
    lines = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"clientInfo": {"name": "x"}}},
             {"jsonrpc": "2.0", "method": "notifications/initialized"},
             {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}]
    out = io.StringIO()
    serve(io.StringIO("\n".join(json.dumps(m) for m in lines) + "\nnot json\n"), out, log=lambda s: None)
    replies = [json.loads(line) for line in out.getvalue().splitlines()]
    assert [r.get("id") for r in replies] == [1, 2, None]
    assert {t["name"] for t in replies[1]["result"]["tools"]} >= {"context", "claim_add", "evidence_attach", "freeze", "decide"}
    assert replies[2]["error"]["code"] == -32700
