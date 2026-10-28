"""A project's theme can change without creating a new identity or changing its research.

The write keeps its author, reason and quoted words; an identical request writes nothing,
and neither an invalid request nor an edited project can bypass the record's protections.
"""
import json
import shutil

import pytest

import rabbit_brain as rb
from rabbit_brain import RBError
from rabbit_brain.models import Envelope
from rabbit_brain.record import Project
from rbtest import Mcp, as_the_user, main, rbyaml


@pytest.fixture
def project(workdir, monkeypatch, capsys):
    as_the_user(monkeypatch)
    monkeypatch.setenv("RB_ACTOR", "human:t")

    def cli(*argv, ok=True):
        code = main([*argv, "--json"])
        env = Envelope.model_validate_json(capsys.readouterr().out)
        assert code == (0 if ok else 2), (argv, code, env.errors)
        assert env.ok is ok, (argv, env.errors)
        return env

    cli.root = workdir
    cli("init", "Original theme", "--id", "study", "--no-agents")
    return cli


def record_bytes(root):
    """Recorded files, excluding local read caches and the lock."""
    folder = root / ".rb"
    paths = [*folder.rglob("*.yaml"), folder / "log.jsonl"]
    return {p.relative_to(folder).as_posix(): p.read_bytes() for p in paths if "cache" not in p.relative_to(folder).parts}


def log_rows(root):
    return [json.loads(line) for line in (root / ".rb/log.jsonl").read_text(encoding="utf-8").splitlines()]


def test_title_changes_from_a_subdirectory_keep_identity_findings_and_decisions(project, monkeypatch, tmp_path_factory):
    state = rb.open()
    question = state.add_question("Does the lower precision meet the budget?", id="q1")
    exp = state.add_experiment("Precision comparison", id="e1", question=question, baseline="fp32", candidates=["int8"])
    crit = exp.criterion("The accuracy budget holds", metric="change.error", at_most=0.1, id="c1")
    exp.freeze(why="before the measurements")
    exp.record({"fp32.error": 1.0, "int8.error": 1.02})
    decision = state.decide("e1", "accept", why="the recorded comparison meets the budget")
    state.add_note("The project includes more than this first question.")
    before = state.project.model_dump()
    frozen = exp.data.frozen
    finding = crit.finding()
    called = state.get(decision, "decision")
    research = {p: content for p, content in record_bytes(project.root).items() if p not in {"project.yaml", "log.jsonl"}}
    writes = len(log_rows(project.root))

    nested = project.root / "src/eval"
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("RB_SESSION", "title-session")
    title = "  ResNet-50: quantization’s limits  "
    reason = "name the shared theme, keeping the inquiry as a question"
    words = "Call this project ResNet-50: quantization’s limits."
    env = project("project", "title", title, "-m", reason, "--words", words)
    assert env.data["object"]["kind"] == "project"
    assert state.project.model_dump() == {**before, "title": title}
    assert env.data["object"]["id"] == before["id"] and env.data["object"]["uid"] == before["uid"]
    assert exp.data.frozen == frozen and crit.finding() == finding
    assert state.get(decision, "decision") == called
    assert {p: content for p, content in record_bytes(project.root).items() if p not in {"project.yaml", "log.jsonl"}} == research
    assert not any(item["code"] == "decision_outdated" for item in state.status()["open"])
    rows = log_rows(project.root)
    assert [r["op"] for r in rows[writes:]] == ["session", "title"]      # a new agent session's first write says its agent first
    last = rows[-1]
    assert (last["op"], last["kind"], last["id"], last["actor"], last["via"], last["session"]) == \
        ("title", "project", before["id"], "agent:claude-code", "cli", "title-session")
    assert last["detail"] == {"before": before["title"], "title": title, "why": reason}
    assert last["words"] == words

    bundle = tmp_path_factory.mktemp("title-export") / "bundle"
    project("export", "--with-record", "--out", str(bundle))
    exported = rb.open(bundle / "record")
    assert exported.project == state.project
    assert exported.finding("c1") == finding
    assert exported.get(decision, "decision") == called
    assert title in exported.context()


def test_same_title_returns_the_project_without_rewriting_it(project):
    before = record_bytes(project.root)
    file = project.root / ".rb/project.yaml"
    stamp = file.stat().st_mtime_ns
    env = project("project", "title", "Original theme", "--why", "confirm the existing title")
    assert env.data["object"]["title"] == "Original theme"
    assert record_bytes(project.root) == before and file.stat().st_mtime_ns == stamp


@pytest.mark.parametrize("title,why", [
    ("", "a reason"), (" \t\n", "a reason"), ("x" * 201, "a reason"),
    ("New theme", None), ("New theme", ""), ("Original theme", " \t\n"), ("New theme", "x" * 1001),
])
def test_invalid_title_or_reason_leaves_the_record_unchanged(project, title, why):
    before = record_bytes(project.root)
    args = [] if why is None else ["--why", why]
    env = project("project", "title", title, *args, ok=False)
    assert env.errors[0].code in {"E_USAGE", "E_OBJECT_INVALID"}
    assert record_bytes(project.root) == before


@pytest.mark.parametrize("title", ["Hand edited theme", "New theme"])
def test_an_edited_project_is_refused_even_when_its_title_already_matches(project, title):
    file = project.root / ".rb/project.yaml"
    doc = rbyaml.load(file.read_text(encoding="utf-8"))
    doc.data["title"] = "Hand edited theme"
    file.write_text(rbyaml.dumps(doc.data), encoding="utf-8")
    before = record_bytes(project.root)
    env = project("project", "title", title, "--why", "a title must not adopt a hand edit", ok=False)
    assert env.errors[0].code == "E_STATE_EDITED"
    assert record_bytes(project.root) == before


def test_an_identical_title_does_not_write_over_a_newer_record(project):
    project("question", "add", "A future inquiry", "--id", "q1")
    file = project.root / ".rb/questions/q1.yaml"
    doc = rbyaml.load(file.read_text(encoding="utf-8"))
    doc.data["rb"] = 2
    file.write_text(rbyaml.dumps(doc.data), encoding="utf-8")
    before = record_bytes(project.root)
    env = project("project", "title", "Original theme", "--why", "confirm the title", ok=False)
    assert env.errors[0].code == "E_STATE_NEWER"
    assert record_bytes(project.root) == before


@pytest.mark.skipif(shutil.which("git") is None, reason="rb clone starts a git repository")
def test_sdk_title_keeps_clone_parent_and_rereads_the_current_project(project, tmp_path_factory):
    bundle = tmp_path_factory.mktemp("title-parent") / "bundle"
    project("export", "--with-record", "--out", str(bundle))
    state = rb.clone(bundle, tmp_path_factory.mktemp("title-fork") / "fork", agent="title-sdk")
    state.add_note("This fork investigates the second inquiry.")
    before = state.project.model_dump()
    assert before["parent"] is not None
    updated = state.set_project_title("The fork's theme", why="name this line of work", words="Use the fork's theme.")
    assert isinstance(updated, Project) and updated.model_dump() == {**before, "title": "The fork's theme"}
    last = log_rows(state.root)[-1]
    assert (last["op"], last["actor"], last["via"], last["words"]) == ("title", "agent:title-sdk", "sdk", "Use the fork's theme.")
    assert last["detail"] == {"before": before["title"], "title": "The fork's theme", "why": "name this line of work"}
    written = record_bytes(state.root)
    assert state.set_project_title("The fork's theme", why="same title") == updated
    assert record_bytes(state.root) == written
    other = rb.open(state.root, agent="another-session")
    other.set_project_title("A revised theme", why="another session revised it")
    final = state.set_project_title("Final theme", why="take the current title forward")
    assert final.model_dump() == {**before, "title": "Final theme"}
    assert log_rows(state.root)[-1]["detail"]["before"] == "A revised theme"
    written = record_bytes(state.root)
    with pytest.raises(RBError):
        state.set_project_title("Final theme", why=" ")
    assert record_bytes(state.root) == written


def test_mcp_title_attributes_each_write_and_keeps_repeated_calls_current(project):
    server = Mcp("title-mcp")

    def rpc(method, params=None):
        return server.handle({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}})["result"]

    def title(value, why="name the theme", words=None):
        args = {"title": value, "why": why}
        if words is not None:
            args["words"] = words
        return rpc("tools/call", {"name": "project_title", "arguments": args})

    try:
        tools = {tool["name"]: tool for tool in rpc("tools/list")["tools"]}
        assert {"title", "why"} <= set(tools["project_title"]["inputSchema"]["required"])
        answer = title("MCP theme", words="Name it MCP theme.")
        assert not answer["isError"] and answer["structuredContent"]["data"]["object"]["title"] == "MCP theme"
        last = log_rows(project.root)[-1]
        assert (last["actor"], last["via"], last["words"]) == ("agent:title-mcp", "mcp", "Name it MCP theme.")
        written = record_bytes(project.root)
        assert not title("MCP theme")["isError"]
        assert record_bytes(project.root) == written
        project("project", "title", "Outside theme", "--why", "changed outside this connection")
        assert not title("Final theme")["isError"]
        assert log_rows(project.root)[-1]["detail"]["before"] == "Outside theme"
        written = record_bytes(project.root)
        assert title("Final theme", why=" ")["isError"]
        assert record_bytes(project.root) == written
    finally:
        assert server.close() == 0
