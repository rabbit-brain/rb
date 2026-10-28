"""rb_delta: an agent session is told, once, what changed in the research since it last looked, on whatever rb command
it runs next. It is never told the history before it arrived, never told twice, and never told what research to do."""
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from rabbit_brain.models import Envelope
from rbtest import Mcp, ROOT, as_the_user, cli_parser, main

sys.path.insert(0, str(Path(__file__).parent / "fixtures"))
import repo_study  # noqa: E402


@pytest.fixture
def study(tmp_path, monkeypatch, tmp_path_factory, capsys):
    as_the_user(monkeypatch)
    cfg = tmp_path_factory.mktemp("gitconfig") / "config"
    cfg.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(cfg))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    root = repo_study.build(tmp_path / "study")
    monkeypatch.chdir(root)

    def rb(*argv, agent=None):
        """rb as the user (default) or as an agent session named `agent`, which gives its model as agents are told
        to, so no fact here asks for it (test_writers.py holds that one)."""
        if agent:
            monkeypatch.delenv("RB_ACTOR", raising=False)
            monkeypatch.setenv("CLAUDECODE", "1")
            monkeypatch.setenv("RB_SESSION", agent)
            monkeypatch.setenv("RB_MODEL", "test-model")
        else:
            monkeypatch.delenv("CLAUDECODE", raising=False)
            monkeypatch.delenv("RB_SESSION", raising=False)
            monkeypatch.delenv("RB_MODEL", raising=False)
            monkeypatch.setenv("RB_ACTOR", "human:researcher")
        args = list(argv)
        args.insert(args.index("--") if "--" in args else len(args), "--json")
        code = main(args)
        env = Envelope.model_validate_json(capsys.readouterr().out)
        assert env.ok and code in (0, 1), env.errors
        return env

    rb("init", "does the warmup schedule transfer?")
    rb("reconcile", "--apply")
    rb.root = root
    return rb


def _texts(env):
    return [d["text"] for d in env.rb_delta]


def _writes(env):
    return [d for d in env.rb_delta if d["kind"] == "write"]


def _git(*args):
    subprocess.run([*repo_study.GIT, *args], check=True, capture_output=True)


def test_a_session_is_told_what_is_outstanding_once_and_never_the_history(study):
    (study.root / "results" / "warmup_transfer" / "independent_audit.json").write_text('{"passed": false}')
    study("note", "add", "the user's own note")                                     # history before the agent came
    env = study("show", "warmup_transfer", agent="s1")
    told = {d["code"]: d for d in env.rb_delta}
    assert set(told) == {"audit_failed", "reindex"}               # the audit says it failed, and its folder changed
    assert "independent_audit.json says passed: false" in told["audit_failed"]["text"] and told["audit_failed"]["do"]
    assert not any(d["kind"] == "write" for d in env.rb_delta)                       # never the history before it arrived
    assert study("show", "warmup_transfer", agent="s1").rb_delta == []                # told once
    assert {d["code"] for d in study("show", "warmup_transfer", agent="s2").rb_delta} == {"audit_failed", "reindex"}   # each session once


def test_what_the_user_writes_reaches_the_agent_on_its_next_command(study):
    study("show", "warmup_transfer", agent="s1")
    study("decide", "warmup_transfer", "park", "--why", "not worth it until the ablation is in", "--words", "park it")
    env = study("show", "warmup_confirmation", agent="s1")
    writes = [d for d in env.rb_delta if d["kind"] == "write"]
    assert [w["by"] for w in writes] == ["human:researcher"]
    assert writes[0]["text"] == 'warmup_transfer parked: not worth it until the ablation is in · their words: "park it"'
    assert study("show", "warmup_confirmation", agent="s1").rb_delta == []
    # its own writes are never told back to it
    study("note", "add", "a note of the agent's own", "-e", "warmup_transfer", agent="s1")
    assert not [d for d in study("show", "warmup_transfer", agent="s1").rb_delta if d["kind"] == "write"]


def test_what_someone_else_wrote_is_told_with_what_it_changed_why_and_their_words(study):
    """A retitle, a question moved, a criterion linked to a question or taken off it, a relation declared or withdrawn,
    a setting set, a variant added and an amendment are told with what they changed, the reason and the user's words,
    on the command line and over MCP alike; a write no description covers is told with the user's words too, and words
    an agent session gave are told as given, never as the user's. No line says who wrote it: each write's `by` does."""
    study("question", "add", "Does warmup help at all?", "--id", "q0")
    study("question", "add", "Does warmup transfer?", "--id", "q1")
    study("metric", "add", "loss", "--minimize")
    study("experiment", "add", "warmup on the wide model", "--id", "e1", "-q", "q1", "--baseline", "cosine", "--candidate", "warmup")
    for c in ("c1", "c2"):
        study("criterion", "add", f"warmup costs no loss, {c}", "-e", "e1", "--metric", "change.loss", "--at-most", "0", "--id", c)
    study("resource", "add", "holdout", "--type", "dataset", "--name", "held-out set")
    title = study("status").data["project"]["title"]
    server = Mcp("claude-code")

    def told_over_mcp(name, arguments):
        answer = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments}})["result"]
        assert not answer["isError"], answer
        return [d["text"] for d in answer["structuredContent"]["rb_delta"] if d["kind"] == "write"]

    def told(said, by):
        env = study("show", "e1", agent="s1")
        assert _texts(env) == said and {w["by"] for w in _writes(env)} == {by}
        assert told_over_mcp("show", {"id": "e1"}) == said

    try:
        study("show", "e1", agent="s1")                                      # each session has looked
        told_over_mcp("show", {"id": "e1"})
        study("project", "title", "Warmup on wide models", "--why", "the user asked", "--words", "call it Warmup on wide models")
        study("question", "move", "q1", "--parent", "q0", "--why", "q1 is a case of q0", "--words", "put q1 under q0")
        study("criterion", "file", "c1", "q1", "--when-met", "Warmup transfers to the wide model", "--why", "c1 bears on q1", "--words", "c1 answers q1")
        study("criterion", "relate", "c1", "reverses", "c2", "--orientation", "same", "--why", "the same arms twice", "--words", "check them against each other")
        study("spec", "set", "e1", "width", "512", "--source", "note:the paper", "--why", "the paper's width", "--words", "use the paper's width")
        study("freeze", "e1", "-m", "before the runs")
        told([
            f'project retitled from "{title}" to "Warmup on wide models": the user asked · their words: "call it Warmup on wide models"',
            'question q1 moved under q0: q1 is a case of q0 · their words: "put q1 under q0"',
            'c1 linked to question q1 (when met: "Warmup transfers to the wide model"): c1 bears on q1 · their words: "c1 answers q1"',
            'relation declared: c1 reverses c2 (orientation same): the same arms twice · their words: "check them against each other"',
            "e1/width set (new, 512): the paper's width · their words: \"use the paper's width\"",
            "e1 frozen: before the runs",
        ], "human:researcher")
        study("criterion", "file", "c1", "q1", "--remove", "--amend", "--why", "q1 is answered elsewhere", "--words", "take c1 off q1")
        study("criterion", "relate", "c1", "reverses", "c2", "--remove", "--amend", "--why", "a duplicate", "--words", "drop that relation")
        study("variant", "add", "e1", "short", "--role", "ablation", "--amend", "--why", "a shorter warmup too", "--words", "add a short one")
        study("spec", "set", "e1", "width", "256", "--source", "note:the paper, again", "--amend", "--why", "the paper says 256", "--words", "use 256")
        study("spec", "vary", "e1", "width", "--amend", "--why", "width is what differs", "--words", "vary the width")
        study("analysis", "add", "e1", "--compare", "warmup/cosine:loss=-0.1[-0.15,-0.05]", "--method", "bootstrap", "--id", "a1",
              "--words", "the bootstrap numbers")
        study("resource", "expose", "holdout", "inspected", "-m", "looked at a few images", "--words", "have a look")
        study("retract", "e1/short", "--why", "one variant too many", "--words", "drop the short one")
        told([
            'e1 amended (criterion c1: unfile question q1): q1 is answered elsewhere · their words: "take c1 off q1"',
            'c1\'s link to question q1 withdrawn: q1 is answered elsewhere · their words: "take c1 off q1"',
            'e1 amended (criterion c1: withdraw reverses c2): a duplicate · their words: "drop that relation"',
            'relation withdrawn: c1 reverses c2: a duplicate · their words: "drop that relation"',
            'variant e1/short (ablation) added, amending frozen e1: a shorter warmup too · their words: "add a short one"',
            'e1/width set (512 -> 256; source note: the paper -> note: the paper, again), amending frozen e1: the paper says 256 · their words: "use 256"',
            'e1 varies width on purpose, amending frozen e1: width is what differs · their words: "vary the width"',
            'analysis a1 recorded on e1 · their words: "the bootstrap numbers"',
            'expose resource holdout · their words: "have a look"',
            'variant e1/short retracted, amending frozen e1: one variant too many · their words: "drop the short one"',
        ], "human:researcher")
        told_over_mcp("criterion_file", {"criterion": "c2", "question": "q0", "when_met": "", "why": "c2 bears on q0", "amend": True,
                                         "words": "link c2 to q0"})
    finally:
        assert server.close() == 0
    env = study("show", "e1", agent="s1")
    # words another agent session gave are told as given, never as the user's
    assert _texts(env) == [
        'e1 amended (criterion c2: file under question q0) · note: c2 bears on q0 · not confirmed · words (as given): "link c2 to q0"',
        'c2 linked to question q0 (with no interpretation) · note: c2 bears on q0 · not confirmed · words (as given): "link c2 to q0"',
    ]
    assert [w["by"] for w in _writes(env)] == ["agent:claude-code"] * 2


def test_a_merge_tells_the_lines_it_brought_in_once_wherever_they_sort(study):
    study("show", "warmup_transfer", agent="s1")
    _git("add", "-A")
    _git("commit", "-qm", "the record")
    _git("switch", "-qc", "elsewhere")
    study("note", "add", "written on another branch")                  # since s1 came, before the call it is told of next
    _git("add", "-A")
    _git("commit", "-qm", "a note")
    _git("switch", "-q", "-")
    study("decide", "warmup_transfer", "park", "--why", "not worth it until the ablation is in")
    assert [w["code"] for w in _writes(study("show", "warmup_transfer", agent="s1"))] == ["decide"]
    _git("add", "-A")
    _git("commit", "-qm", "parked")
    _git("merge", "-q", "--no-edit", "elsewhere")                      # the note's line sorts in before the call's
    writes = _writes(study("show", "warmup_transfer", agent="s1"))
    assert [(w["code"], w["by"]) for w in writes] == [("note", "human:researcher")]     # the call is not told again
    assert writes[0]["text"].endswith(": written on another branch")
    assert _writes(study("show", "warmup_transfer", agent="s1")) == []                   # told once


def test_a_branch_it_switches_to_tells_what_was_written_there_since_it_arrived_and_never_the_history(study):
    _git("add", "-A")
    _git("commit", "-qm", "the record")
    _git("switch", "-qc", "elsewhere")
    study("note", "add", "written on another branch before the agent came")
    _git("add", "-A")
    _git("commit", "-qm", "a note")
    _git("switch", "-q", "-")
    study("show", "warmup_transfer", agent="s1")
    store = study.root / ".rb" / "cache" / "sessions" / "s1.json"
    assert json.loads(store.read_text())["seen_lines"] == []          # history it is never told is not kept either
    _git("switch", "-q", "elsewhere")
    assert _writes(study("show", "warmup_transfer", agent="s1")) == []                   # its note is history to s1
    study("note", "add", "written there since")
    writes = _writes(study("show", "warmup_transfer", agent="s1"))
    assert len(writes) == 1 and writes[0]["text"].endswith(": written there since")
    _git("add", "-A")
    _git("commit", "-qm", "another note")
    _git("switch", "-q", "-")
    assert _writes(study("show", "warmup_transfer", agent="s1")) == []
    _git("switch", "-q", "elsewhere")
    assert _writes(study("show", "warmup_transfer", agent="s1")) == []                   # told once, whichever branch


def test_a_store_an_earlier_rb_wrote_is_read_as_it_read_it_once(study):
    study("show", "warmup_transfer", agent="s1")
    store = study.root / ".rb" / "cache" / "sessions" / "s1.json"
    kept = json.loads(store.read_text())
    store.write_text(json.dumps({k: kept[k] for k in ("session", "seen", "cursor")}))   # a count of lines alone
    study("note", "add", "the user's own note")
    assert [w["code"] for w in _writes(study("show", "warmup_transfer", agent="s1"))] == ["note"]
    kept = json.loads(store.read_text())
    assert kept["cursor"] == len((study.root / ".rb" / "log.jsonl").read_text().splitlines())   # the count still, for that rb
    assert _writes(study("show", "warmup_transfer", agent="s1")) == []


def test_status_and_context_tell_everything_so_after_them_nothing_repeats(study):
    (study.root / "results" / "warmup_transfer" / "independent_audit.json").write_text('{"passed": false}')
    assert study("status", agent="s1").rb_delta == []
    assert study("show", "warmup_transfer", agent="s1").rb_delta == []


def test_a_fact_that_clears_and_comes_back_is_told_again(study):
    audit = study.root / "results" / "warmup_transfer" / "independent_audit.json"
    audit.write_text('{"passed": false}')
    assert "audit_failed" in {d["code"] for d in study("show", "warmup_transfer", agent="s1").rb_delta}
    audit.write_text('{"passed": true, "checks": 12}')                       # cleared
    assert "audit_failed" not in {d["code"] for d in study("show", "warmup_transfer", agent="s1").rb_delta}
    audit.write_text('{"passed": false, "again": true}')                     # and back
    assert "audit_failed" in {d["code"] for d in study("show", "warmup_transfer", agent="s1").rb_delta}


def test_a_source_that_now_states_another_value_is_told(study):
    """A setting whose source now states another value is a source that changed, and the session is told so."""
    (study.root / "train.json").write_text('{"lr": 0.001}')
    study("spec", "set", "warmup_transfer", "lr", "--source", "train.json#lr")
    study("show", "warmup_transfer", agent="s1")                                   # the session has looked
    (study.root / "train.json").write_text('{"lr": 0.002}')
    told = {d["code"]: d for d in study("show", "warmup_transfer", agent="s1").rb_delta}
    assert told["conflict"]["text"] == "warmup_transfer/lr: the record says 0.001, but train.json#lr now says 0.002" and told["conflict"]["do"]


def test_a_watched_run_that_prints_its_metrics_tells_the_session_it_is_unrecorded(study):
    study("metric", "add", "steps", "--minimize")
    study("experiment", "add", "the peak rate", "--id", "peak")
    study("criterion", "add", "few steps", "-e", "peak", "--metric", "steps", "--at-most", "50")
    study("status", agent="s1")
    (study.root / "peak.py").write_text('print(\'{"metrics": {"steps": 41.5}}\')\n', encoding="utf-8")
    env = study("journal", "run", "--", sys.executable, "peak.py", agent="s1")
    assert [d["code"] for d in env.rb_delta] == ["unrecorded"] and env.rb_delta[0]["do"] == "rb reconcile"
    study("reconcile", "--apply", agent="s1")
    assert not [d for d in study("status", agent="s1").rb_delta]


def test_the_user_gets_no_delta_and_the_text_form_says_since_when(study, capsys, monkeypatch):
    (study.root / "results" / "warmup_transfer" / "independent_audit.json").write_text('{"passed": false}')
    assert study("show", "warmup_transfer").rb_delta == []                         # the user reads rb status
    monkeypatch.delenv("RB_ACTOR", raising=False)
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("RB_SESSION", "s3")
    monkeypatch.setenv("RB_MODEL", "test-model")
    assert main(["show", "warmup_transfer"]) == 0
    text = capsys.readouterr().out
    assert "Since this session last looked:" in text and "independent_audit.json says passed: false" in text
    stored = json.loads((study.root / ".rb" / "cache" / "sessions" / "s3.json").read_text())
    assert stored["seen"] == ["audit_failed:warmup_transfer", "reindex:warmup_transfer"]


def declared_commands() -> dict:
    """Every command the command line declares, as its words, with the function the registry runs for it (None for
    one rb answers itself, as rb mcp)."""
    out = {}
    for action in cli_parser.build_parser()._subparsers._group_actions:
        for name, sub in action.choices.items():
            if sub.get_default("func") or not sub._subparsers:
                out[name] = sub.get_default("func")
            for group in sub._subparsers._group_actions if sub._subparsers else []:
                out.update({f"{name} {word}": leaf.get_default("func") for word, leaf in group.choices.items()})
    return out


def test_every_write_on_the_record_carries_the_delta():
    """rb_delta rides on the commands in LEDGER_COMMANDS: the binary's list is the declared one, every declared command
    is named once in COMMANDS, and every write the registry runs from the record's commands is in the list."""
    registry = (ROOT / "rust" / "src" / "commands.rs").read_text(encoding="utf-8")
    binary = re.search(r"pub const LEDGER_COMMANDS: &\[&str\] = &\[(.*?)\];", registry, re.S).group(1)
    assert re.findall(r'"([^"]+)"', binary) == cli_parser.LEDGER_COMMANDS
    declared = declared_commands()
    assert set(declared) == set(cli_parser.COMMANDS) and len(cli_parser.COMMANDS) == len(set(cli_parser.COMMANDS))
    writes = set(re.findall(r'"(cmd_\w+)" => r::\1', registry))
    assert len(writes) > 20
    assert {words for words, func in declared.items() if func in writes} <= set(cli_parser.LEDGER_COMMANDS)
