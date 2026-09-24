"""The integrity rules of the research state (.rb/, v2): what makes a claim established, and what cannot be faked.

Promises pinned here:

- Who: the actor is RB_ACTOR, else a detected agent runtime (CLAUDECODE, AI_AGENT, CODEX_*, GITHUB_ACTIONS, in that
  order), else a person named from git (email, then name), else the login. A malformed RB_ACTOR is refused. A person
  named inside an agent session is accepted and stamped `asserted_from` on the object and in the log.
- Person-only calls (freeze, decide, amend, retracting what something rests on) are refused for an agent with
  E_HUMAN_ONLY and a handoff naming the exact command for the person to run.
- Verification is strict: a comment never verifies; a prose line needs the setting's name or --term; numbers are
  standalone tokens compared exactly (1e-4 is 0.0001; resnet50 does not state 50; 1700000001 is not 1700000000);
  text is a whole token (adamw is not adam); lists are ordered; a YAML line is read as its key path and must be the
  setting's own line.
- A source stating another value is a conflict that re-verifying does not clear; a verified source is re-checked on
  every read (moved: shown; stale or conflict: blocks); a commit-pinned source never goes stale, and a hand-made
  resolution does not pass the re-check.
- Variants differing in anything not declared in --varies is a blocking confound.
- A frozen spec changes only by a person's --amend, kept with the hashes before and after; a spec edited by hand is
  drift.
- Observation roles: before the claim or before the freeze is exploratory; synthetic, a different spec, or a run config
  that differs from the spec is not counted.
- Established needs a person to have fixed the criterion, required settings verified or vouched (a vouch lapses when
  the value changes), enough runs, a margin outside the noise, and for a cited claim settings comparable to the
  source's.
- Retracted evidence stops counting and stays on record.
- Every object file is checked against the hash rb logged: a hand edit blocks establishment and fails any --fail-on
  gate; an unparseable file is E_STATE_CORRUPT; a source outside the project is E_SOURCE_OUTSIDE. Every write is
  logged with actor, actor_via, via and sha256, and concurrent writers lose nothing.
"""
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import threading
from pathlib import Path

import pytest

from rabbit_brain import actor as actor_mod
from rabbit_brain.cli import main
from rabbit_brain.errors import RBError
from rabbit_brain.investigation import Resolution, Source
from rabbit_brain.ledger import Ledger
from rabbit_brain.models import Envelope
from rabbit_brain.sources import recheck, sha256_bytes

HAS_GIT = shutil.which("git") is not None
needs_git = pytest.mark.skipif(not HAS_GIT, reason="git is not installed")
AGENT_VARS = ("CLAUDECODE", "AI_AGENT", "GITHUB_ACTIONS", "RB_ACTOR")


# ---------------------------------------------------------------- fixtures and helpers


def git(*args, cwd=None) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True, cwd=cwd).stdout.strip()


def _isolate_git(monkeypatch, tmp_path_factory) -> None:
    """No global or system git config (signing, another email) leaks into the tests."""
    cfg = tmp_path_factory.mktemp("gitconfig") / "config"
    cfg.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(cfg))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")


@pytest.fixture
def person(workdir, monkeypatch, tmp_path_factory):
    """The project root, where rb records the caller as the person human:t whatever environment runs pytest (which may
    itself be an agent session)."""
    for name in list(os.environ):
        if name in AGENT_VARS or name.startswith("CODEX_"):
            monkeypatch.delenv(name)
    _isolate_git(monkeypatch, tmp_path_factory)
    monkeypatch.setenv("LOGNAME", "t")          # the fallback when git is missing
    if HAS_GIT:
        git("init", "-q")
        git("config", "user.email", "t@example.com")
        git("config", "user.name", "t")
    return workdir


def rbj(capsys, *argv):
    code = main([*argv, "--json"])
    return code, Envelope.model_validate_json(capsys.readouterr().out)


def ok(capsys, *argv) -> Envelope:
    code, env = rbj(capsys, *argv)
    assert code == 0, (argv, env.errors, env.data.get("outcome"))
    return env


def start(capsys, *experiment_args) -> None:
    ok(capsys, "init", "integrity")
    ok(capsys, "experiment", "add", "x", "--id", "e1", *experiment_args)


def attach(capsys, *pairs, exp="e1") -> str:
    return ok(capsys, "evidence", "attach", exp, *pairs).data["object"]["id"]


def verdict(capsys, claim="c1") -> dict:
    return ok(capsys, "show", claim).data["verdict"]


def caveats(capsys, subject, exp="e1") -> list:
    """The experiment's caveats about one subject (e1/lr), as (code, blocks)."""
    return [(c["code"], c["blocks"]) for c in ok(capsys, "show", exp).data["caveats"] if c["subject"] == subject]


def setting(capsys, address) -> dict:
    return ok(capsys, "show", address).data["object"]


def spec_set(capsys, exp, name, value, *extra):
    """rb spec set; returns (exit code, the setting's status)."""
    code, env = rbj(capsys, "spec", "set", exp, name, value, *extra)
    assert env.ok, env.errors
    return code, env.data["object"]["status"]


def edit_json(path: Path, **changes) -> None:
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc.update(changes)
    path.write_text(json.dumps(doc, indent=2), encoding="utf-8")


# ---------------------------------------------------------------- who: actor detection


def test_rb_actor_wins_over_a_detected_runtime(person):
    a = actor_mod.current(person, {"RB_ACTOR": "agent:bot", "CLAUDECODE": "1"})
    assert (a.id, a.via, a.is_person) == ("agent:bot", "RB_ACTOR", False)


@pytest.mark.parametrize("env,expected,via", [
    ({"CLAUDECODE": "1", "AI_AGENT": "cursor", "CODEX_HOME": "x", "GITHUB_ACTIONS": "true"}, "agent:claude-code", "detected:CLAUDECODE"),
    ({"AI_AGENT": "cursor_1.2/agent", "CODEX_HOME": "x", "GITHUB_ACTIONS": "true"}, "agent:cursor", "detected:AI_AGENT"),
    ({"CODEX_SANDBOX": "seatbelt", "GITHUB_ACTIONS": "true"}, "agent:codex", "detected:CODEX_SANDBOX"),
    ({"GITHUB_ACTIONS": "true"}, "agent:github-actions", "detected:GITHUB_ACTIONS"),
])
def test_agent_runtimes_are_detected_in_order(person, env, expected, via):
    a = actor_mod.current(person, env)
    assert (a.id, a.via, a.is_person) == (expected, via, False)


@pytest.mark.parametrize("env", [{"CLAUDECODE": "0"}, {"GITHUB_ACTIONS": "false"}, {"AI_AGENT": ""}])
def test_a_variable_that_does_not_name_an_agent_is_not_one(person, env):
    assert actor_mod.current(person, env).is_person


@needs_git
def test_a_person_is_named_from_the_git_email(person):
    a = actor_mod.current(person, {})
    assert (a.id, a.via, a.asserted_from) == ("human:t", "git-email", None)


@needs_git
def test_a_person_is_named_from_the_git_name_when_there_is_no_email(person, tmp_path_factory):
    repo = tmp_path_factory.mktemp("named")
    git("init", "-q", cwd=repo)
    git("config", "user.name", "Jane Doe", cwd=repo)
    a = actor_mod.current(repo, {})
    assert (a.id, a.via) == ("human:jane-doe", "git-name")


def test_a_person_is_named_from_the_login_when_git_names_nobody(person, tmp_path_factory, monkeypatch):
    bare = tmp_path_factory.mktemp("nogit")
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(bare.parent))
    monkeypatch.setenv("LOGNAME", "jhet")
    a = actor_mod.current(bare, {})
    assert (a.id, a.via) == ("human:jhet", "login")


@pytest.mark.parametrize("raw", ["bob", "human:", "robot:x", "human:two words", ""])
def test_a_malformed_rb_actor_is_refused(person, raw):
    with pytest.raises(RBError) as err:
        actor_mod.current(person, {"RB_ACTOR": raw})
    assert err.value.code == "E_ACTOR_INVALID"


def test_a_malformed_rb_actor_is_refused_on_any_command(person, capsys, monkeypatch):
    start(capsys)
    monkeypatch.setenv("RB_ACTOR", "bob")
    code, env = rbj(capsys, "status")
    assert code == 2 and env.errors[0].code == "E_ACTOR_INVALID"


def test_a_person_named_inside_an_agent_session_is_marked_as_asserted(person):
    a = actor_mod.current(person, {"RB_ACTOR": "human:x", "CLAUDECODE": "1"})
    assert (a.id, a.is_person, a.asserted_from) == ("human:x", True, "claude-code")


def test_an_asserted_persons_freeze_is_stamped_on_the_object_and_in_the_log(person, capsys, monkeypatch):
    start(capsys)
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("RB_ACTOR", "human:x")
    env = ok(capsys, "freeze", "e1", "-m", "go")
    assert env.data["actor"] == {"id": "human:x", "via": "RB_ACTOR", "asserted_from": "claude-code"}
    assert env.data["object"]["frozen"]["by"] == "human:x" and env.data["object"]["frozen"]["asserted_from"] == "claude-code"
    last = Ledger(person).log_entries()[-1]
    assert (last["op"], last["actor"], last["actor_via"], last["asserted_from"]) == ("freeze", "human:x", "RB_ACTOR", "claude-code")


# ---------------------------------------------------------------- person-only calls


@pytest.fixture
def frozen_candidate(person, capsys, monkeypatch):
    """e1 with a vouchable setting e1/lr, a claim c1 on it, evidence the claim counts, and a second experiment e2 open
    for freezing. Returns the evidence id."""
    start(capsys)
    ok(capsys, "spec", "set", "e1", "lr", "0.1")
    ok(capsys, "claim", "add", "s", "-e", "e1", "--metric", "top1", "--at-least", "0.7", "--id", "c1")
    ok(capsys, "freeze", "e1", "-m", "locked")
    ev = attach(capsys, "top1=0.76")
    ok(capsys, "experiment", "add", "y", "--id", "e2")
    return ev


@pytest.mark.parametrize("argv", [
    ["freeze", "e2", "-m", "lock the spec"],
    ["decide", "c1", "accept", "-m", "looks right"],
    ["decide", "e1/lr", "accept", "-m", "I checked it"],
    ["spec", "set", "e1", "lr", "0.2", "--amend", "-m", "typo in the spec"],
    ["retract", "{ev}", "-m", "bad run"],
])
def test_an_agent_is_refused_a_persons_call_with_the_exact_command_to_hand_over(frozen_candidate, capsys, monkeypatch, argv):
    argv = [a.replace("{ev}", frozen_candidate) for a in argv]
    monkeypatch.setenv("CLAUDECODE", "1")
    code, env = rbj(capsys, *argv)
    err = env.errors[0]
    assert code == 2 and err.code == "E_HUMAN_ONLY"
    assert err.handoff == {"who": "person", "command": shlex.join(["rb", *argv])}
    assert shlex.join(["rb", *argv]) in err.fix


def test_a_refused_freeze_writes_nothing(person, capsys, monkeypatch):
    start(capsys)
    before = (person / ".rb" / "log.jsonl").read_text(encoding="utf-8")
    monkeypatch.setenv("CLAUDECODE", "1")
    rbj(capsys, "freeze", "e1", "-m", "go")
    assert Ledger(person).load("experiment", "e1").frozen is None
    assert (person / ".rb" / "log.jsonl").read_text(encoding="utf-8") == before


# ---------------------------------------------------------------- verification is strict


def test_a_comment_line_never_verifies(person, capsys):
    start(capsys)
    Path("train.py").write_text("# lr = 0.0001\n", encoding="utf-8")
    Path("cfg.yaml").write_text("# lr: 0.0001\nlr: 0.2\n", encoding="utf-8")
    assert spec_set(capsys, "e1", "lr", "0.0001", "--source", "train.py:1") == (1, "provisional")
    assert spec_set(capsys, "e1", "lr", "0.0001", "--source", "train.py:1", "--term", "lr") == (1, "provisional")
    assert spec_set(capsys, "e1", "lr", "0.0001", "--source", "cfg.yaml:1") == (1, "provisional")


def test_a_prose_line_needs_the_settings_name_or_a_term(person, capsys):
    start(capsys)
    Path("paper.txt").write_text("We train with a learning rate of 0.0001 for 90 epochs.\n", encoding="utf-8")
    assert spec_set(capsys, "e1", "lr", "1e-4", "--source", "paper.txt:1") == (1, "provisional")
    assert spec_set(capsys, "e1", "lr", "1e-4", "--source", "paper.txt:1", "--term", "learning rate") == (0, "verified")


def test_a_quote_verifies_the_value_it_contains(person, capsys):
    start(capsys)
    Path("paper.txt").write_text("We train with a learning rate of 0.0001 for 90 epochs.\n", encoding="utf-8")
    assert spec_set(capsys, "e1", "lr", "1e-4", "--source", "paper.txt", "--quote", "learning rate of 0.0001") == (0, "verified")


@pytest.mark.parametrize("file,text,source,value", [
    ("train.py", "lr = 0.0001\n", "train.py:1", "1e-4"),
    ("cfg.yaml", "lr: 0.0001\n", "cfg.yaml#lr", "1e-4"),
    ("cfg.yaml", "lr: 1e-4\n", "cfg.yaml#lr", "0.0001"),
])
def test_scientific_notation_states_the_same_number(person, capsys, file, text, source, value):
    start(capsys)
    Path(file).write_text(text, encoding="utf-8")
    assert spec_set(capsys, "e1", "lr", value, "--source", source) == (0, "verified")


@pytest.mark.parametrize("name,line,value", [
    ("backbone", "backbone = 'resnet50'", "50"),
    ("seed", "seed = 1700000001", "1700000000"),
    ("optimizer", "optimizer = 'adamw'", "adam"),
    ("betas", "betas = [0.999, 0.9]", "[0.9, 0.999]"),
    ("batch", "batch = 16", '"6"'),
])
def test_a_near_miss_does_not_verify(person, capsys, name, line, value):
    start(capsys)
    Path("train.py").write_text(line + "\n", encoding="utf-8")
    code, status = spec_set(capsys, "e1", name, value, "--source", "train.py:1")
    assert (code, status) == (1, "provisional")


@pytest.mark.parametrize("name,line,value", [
    ("seed", "seed = 1700000001", "1700000001"),
    ("optimizer", "optimizer = 'adam'", "adam"),
    ("betas", "betas = [0.9, 0.999]", "[0.9, 0.999]"),
])
def test_the_exact_value_verifies(person, capsys, name, line, value):
    start(capsys)
    Path("train.py").write_text(line + "\n", encoding="utf-8")
    assert spec_set(capsys, "e1", name, value, "--source", "train.py:1") == (0, "verified")


def test_a_yaml_line_is_recorded_as_its_key_path(person, capsys):
    start(capsys)
    Path("cfg.yaml").write_text("optim:\n  lr: 0.0001\n", encoding="utf-8")
    env = ok(capsys, "spec", "set", "e1", "lr", "0.0001", "--source", "cfg.yaml:2")
    src = env.data["object"]["source"]
    assert env.data["object"]["status"] == "verified" and (src["key"], src["line"]) == ("optim.lr", None)


def test_a_yaml_line_for_a_different_key_does_not_verify(person, capsys):
    """weight_decay: 0.0001 happens to hold lr's value; the line does not set lr."""
    start(capsys)
    Path("cfg.yaml").write_text("lr: 0.1\nweight_decay: 0.0001\n", encoding="utf-8")
    code, status = spec_set(capsys, "e1", "lr", "0.0001", "--source", "cfg.yaml:2")
    assert (code, status) == (1, "provisional")


def test_a_line_for_a_different_setting_does_not_verify(person, capsys):
    start(capsys)
    Path("train.py").write_text("weight_decay = 0.0001\n", encoding="utf-8")
    assert spec_set(capsys, "e1", "lr", "0.0001", "--source", "train.py:1") == (1, "provisional")


# ---------------------------------------------------------------- conflicts and re-checking


def test_a_source_stating_another_value_records_a_conflict(person, capsys):
    start(capsys)
    Path("cfg.yaml").write_text("lr: 0.1\n", encoding="utf-8")
    code, env = rbj(capsys, "spec", "set", "e1", "lr", "0.2", "--source", "cfg.yaml#lr")
    s = env.data["object"]
    assert code == 1 and s["status"] == "provisional" and s["conflict"] is not None
    assert env.data["outcome"]["passed"] is False


def test_reverifying_does_not_clear_a_conflict(person, capsys):
    start(capsys)
    Path("cfg.yaml").write_text("lr: 0.1\n", encoding="utf-8")
    rbj(capsys, "spec", "set", "e1", "lr", "0.2", "--source", "cfg.yaml#lr")
    code, env = rbj(capsys, "spec", "verify", "e1", "lr")
    assert code == 1 and env.data["results"][0]["after"] == "provisional"
    assert setting(capsys, "e1/lr")["conflict"] is not None
    assert ("conflict", True) in caveats(capsys, "e1/lr")


def test_a_verified_line_that_moves_is_shown_and_does_not_block(person, capsys):
    start(capsys)
    Path("train.py").write_text("lr = 0.1\n", encoding="utf-8")
    spec_set(capsys, "e1", "lr", "0.1", "--source", "train.py:1")
    Path("train.py").write_text("import os\nlr = 0.1\n", encoding="utf-8")
    assert caveats(capsys, "e1/lr") == [("source_changed", False)]


def test_a_verified_source_that_no_longer_states_the_value_is_stale_and_blocks(person, capsys):
    start(capsys)
    Path("train.py").write_text("lr = 0.1\n", encoding="utf-8")
    spec_set(capsys, "e1", "lr", "0.1", "--source", "train.py:1")
    Path("train.py").write_text("batch = 4\n", encoding="utf-8")
    assert caveats(capsys, "e1/lr") == [("stale", True)]
    assert rbj(capsys, "status", "--fail-on", "stale")[0] == 1


def test_a_verified_source_that_now_states_another_value_is_a_conflict(person, capsys):
    start(capsys)
    Path("cfg.yaml").write_text("lr: 0.1\n", encoding="utf-8")
    spec_set(capsys, "e1", "lr", "0.1", "--source", "cfg.yaml#lr")
    Path("cfg.yaml").write_text("lr: 0.3\n", encoding="utf-8")
    assert caveats(capsys, "e1/lr") == [("conflict", True)]


@needs_git
def test_a_commit_pinned_source_does_not_go_stale_when_the_working_tree_changes(person, capsys):
    Path("cfg.yaml").write_text("lr: 0.1\n", encoding="utf-8")
    git("add", "cfg.yaml")
    git("commit", "-qm", "config")
    head = git("rev-parse", "HEAD")
    start(capsys)
    assert spec_set(capsys, "e1", "lr", "0.1", "--source", "cfg.yaml#lr", "--commit", head) == (0, "verified")
    Path("cfg.yaml").write_text("lr: 0.5\n", encoding="utf-8")
    assert caveats(capsys, "e1/lr") == []
    os.remove("cfg.yaml")
    assert caveats(capsys, "e1/lr") == []


@needs_git
def test_a_hand_made_resolution_of_a_commit_pinned_source_does_not_pass_the_recheck(person):
    """The resolution says the file read 0.2, with the right hash; the file at that commit says 0.1."""
    Path("cfg.yaml").write_text("lr: 0.1\n", encoding="utf-8")
    git("add", "cfg.yaml")
    git("commit", "-qm", "config")
    head = git("rev-parse", "HEAD")
    forged = Resolution(at="2026-01-01T00:00:00+00:00", commit=head, sha256=sha256_bytes(b"lr: 0.1\n"), text="lr: 0.2", read=0.2)
    source = Source(kind="file", path="cfg.yaml", key="lr", commit=head, resolved=forged)
    state, _ = recheck(source, 0.2, person, "lr")
    assert state in ("stale", "conflict")


# ---------------------------------------------------------------- confounds


def test_an_undeclared_difference_between_variants_is_a_blocking_confound(person, capsys):
    start(capsys, "--baseline", "fp32", "--candidate", "int8", "--varies", "precision")
    ok(capsys, "spec", "set", "e1", "lr", "0.1")
    ok(capsys, "spec", "set", "e1", "int8.lr", "0.2")
    assert ("confound", True) in caveats(capsys, "e1/lr")


def test_a_difference_declared_with_varies_is_not_a_confound(person, capsys):
    start(capsys, "--baseline", "fp32", "--candidate", "int8", "--varies", "precision")
    ok(capsys, "spec", "set", "e1", "fp32.precision", "fp32")
    ok(capsys, "spec", "set", "e1", "int8.precision", "int8")
    assert not [c for c in ok(capsys, "show", "e1").data["caveats"] if c["code"].startswith("confound")]


def test_a_confound_a_person_accepts_no_longer_blocks(person, capsys):
    start(capsys, "--baseline", "fp32", "--candidate", "int8", "--varies", "precision")
    ok(capsys, "spec", "set", "e1", "lr", "0.1")
    ok(capsys, "spec", "set", "e1", "int8.lr", "0.2")
    ok(capsys, "decide", "e1/lr", "accept", "-m", "int8 needs the higher rate; it is part of the recipe")
    assert ("confound_accepted", False) in caveats(capsys, "e1/lr") and ("confound", True) not in caveats(capsys, "e1/lr")


def test_a_confound_keeps_a_claim_from_being_established(person, capsys):
    start(capsys, "--baseline", "fp32", "--candidate", "int8", "--varies", "precision")
    ok(capsys, "spec", "set", "e1", "fp32.bs", "8", "--optional")
    ok(capsys, "spec", "set", "e1", "int8.bs", "16", "--optional")
    ok(capsys, "claim", "add", "s", "-e", "e1", "--metric", "int8.epe", "--at-most", "1", "--id", "c1")
    attach(capsys, "int8.epe=0.5", "fp32.epe=0.4")
    v = verdict(capsys)
    assert v["status"] == "supported" and not v["established"] and "confound" in v["not_established_because"]


# ---------------------------------------------------------------- freezing and amending


@pytest.mark.parametrize("argv", [
    ["spec", "set", "e1", "lr", "0.2"],
    ["claim", "add", "later", "-e", "e1", "--metric", "top1", "--at-least", "0.8"],
    ["variant", "add", "e1", "v2", "--role", "candidate"],
])
def test_a_frozen_spec_refuses_changes_without_amend(person, capsys, argv):
    start(capsys)
    ok(capsys, "spec", "set", "e1", "lr", "0.1")
    ok(capsys, "freeze", "e1", "-m", "locked")
    code, env = rbj(capsys, *argv)
    assert code == 2 and env.errors[0].code == "E_FROZEN"


def test_a_persons_amendment_is_kept_with_the_hashes_before_and_after(person, capsys):
    start(capsys)
    ok(capsys, "spec", "set", "e1", "lr", "0.1")
    frozen = ok(capsys, "freeze", "e1", "-m", "locked").data["object"]["frozen"]
    ok(capsys, "spec", "set", "e1", "lr", "0.2", "--amend", "-m", "the spec had a typo")
    shown = ok(capsys, "show", "e1").data
    [am] = shown["object"]["amendments"]
    assert (am["by"], am["why"], am["before"], am["after"]) == ("human:t", "the spec had a typo", frozen["sha256"], shown["freeze_sha256"])
    assert "0.1 -> 0.2" in am["change"] and shown["spec_drift"] is None
    logged = Ledger(person).log_entries()[-1]["detail"]["amendment"]
    assert (logged["before"], logged["after"], logged["why"]) == (am["before"], am["after"], am["why"])


def test_amend_with_nothing_to_amend_is_refused(person, capsys):
    start(capsys)
    code, env = rbj(capsys, "spec", "set", "e1", "lr", "0.1", "--amend", "-m", "why not")
    assert code == 2 and env.errors[0].code == "E_OBJECT_INVALID"


def test_a_frozen_spec_edited_by_hand_is_drift_and_blocks(person, capsys):
    start(capsys)
    ok(capsys, "spec", "set", "e1", "lr", "0.1")
    ok(capsys, "decide", "e1/lr", "accept", "-m", "checked")
    ok(capsys, "claim", "add", "s", "-e", "e1", "--metric", "top1", "--at-least", "0.7", "--id", "c1")
    ok(capsys, "freeze", "e1", "-m", "locked")
    attach(capsys, "top1=0.76")
    p = person / ".rb" / "experiments" / "e1.json"
    doc = json.loads(p.read_text(encoding="utf-8"))
    doc["settings"][0]["value"] = 0.5
    p.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    assert ok(capsys, "show", "e1").data["spec_drift"]
    assert "drift" in verdict(capsys)["not_established_because"]
    assert rbj(capsys, "status", "--fail-on", "stale")[0] == 1


def test_a_claims_criterion_edited_after_the_freeze_is_drift(person, capsys):
    start(capsys)
    ok(capsys, "claim", "add", "s", "-e", "e1", "--metric", "top1", "--at-least", "0.7", "--id", "c1")
    ok(capsys, "freeze", "e1", "-m", "locked")
    edit_json(person / ".rb" / "claims" / "c1.json", target=0.5)
    assert ok(capsys, "show", "e1").data["spec_drift"]


# ---------------------------------------------------------------- observation roles


def test_evidence_attached_before_the_claim_is_exploratory(person, capsys):
    start(capsys)
    ev = attach(capsys, "top1=0.76")
    ok(capsys, "claim", "add", "s", "-e", "e1", "--metric", "top1", "--at-least", "0.7", "--id", "c1")
    v = verdict(capsys)
    assert [(o["evidence"], o["role"]) for o in v["observations"]] == [(ev, "exploratory")]
    assert v["status"] == "untested" and "exploratory_only" in v["not_established_because"]


def test_evidence_attached_before_the_freeze_is_exploratory(person, capsys):
    start(capsys)
    ok(capsys, "claim", "add", "s", "-e", "e1", "--metric", "top1", "--at-least", "0.7", "--id", "c1")
    ev = attach(capsys, "top1=0.76")
    ok(capsys, "freeze", "e1", "-m", "locked")
    v = verdict(capsys)
    assert [(o["evidence"], o["role"]) for o in v["observations"]] == [(ev, "exploratory")] and not v["established"]


def test_evidence_attached_after_the_claim_and_the_freeze_is_confirmatory(person, capsys):
    start(capsys)
    ok(capsys, "claim", "add", "s", "-e", "e1", "--metric", "top1", "--at-least", "0.7", "--id", "c1")
    ok(capsys, "freeze", "e1", "-m", "locked")
    ev = attach(capsys, "top1=0.76")
    assert [(o["evidence"], o["role"]) for o in verdict(capsys)["observations"]] == [(ev, "confirmatory")]


def test_synthetic_evidence_is_not_counted(person, capsys):
    start(capsys)
    ok(capsys, "example", "--write", "cmp.json")
    ok(capsys, "claim", "add", "s", "-e", "e1", "--metric", "candidate.mean_endpoint_error", "--at-most", "1000", "--id", "c1")
    env = ok(capsys, "evidence", "attach", "e1", "--run", "cmp.json")
    assert env.data["object"]["synthetic"] is True
    v = verdict(capsys)
    assert [o["role"] for o in v["observations"]] == ["not_counted"] and v["n"] == 0 and not v["established"]


def test_a_run_config_that_differs_from_the_spec_is_not_counted(person, capsys):
    start(capsys)
    Path("cfg.yaml").write_text("lr: 0.1\n", encoding="utf-8")
    Path("ran.yaml").write_text("lr: 0.2\n", encoding="utf-8")
    ok(capsys, "spec", "set", "e1", "lr", "--source", "cfg.yaml#lr")
    ok(capsys, "claim", "add", "s", "-e", "e1", "--metric", "top1", "--at-least", "0.7", "--id", "c1")
    attach(capsys, "top1=0.76", "--config", "ran.yaml")
    [o] = verdict(capsys)["observations"]
    assert o["role"] == "not_counted" and "lr" in o["reason"]


def test_evidence_under_a_spec_that_changed_since_is_not_counted(person, capsys):
    start(capsys)
    ok(capsys, "spec", "set", "e1", "lr", "0.1")
    ok(capsys, "claim", "add", "s", "-e", "e1", "--metric", "top1", "--at-least", "0.7", "--id", "c1")
    attach(capsys, "top1=0.76")
    ok(capsys, "spec", "set", "e1", "lr", "0.2")
    [o] = verdict(capsys)["observations"]
    assert o["role"] == "not_counted" and "0.1 -> 0.2" in o["reason"]


# ---------------------------------------------------------------- who fixed the criterion


def test_a_person_written_claim_on_confirmatory_evidence_is_established(person, capsys):
    start(capsys)
    ok(capsys, "claim", "add", "s", "-e", "e1", "--metric", "top1", "--at-least", "0.7", "--id", "c1")
    attach(capsys, "top1=0.76")
    v = verdict(capsys)
    assert (v["status"], v["established"], v["not_established_because"]) == ("supported", True, [])


def test_an_agent_written_claim_is_not_established_without_a_persons_freeze(person, capsys, monkeypatch):
    start(capsys)
    monkeypatch.setenv("CLAUDECODE", "1")
    ok(capsys, "claim", "add", "s", "-e", "e1", "--metric", "top1", "--at-least", "0.7", "--id", "c1")
    attach(capsys, "top1=0.76")
    v = verdict(capsys)
    assert v["status"] == "supported" and not v["established"]
    assert v["not_established_because"] == ["criterion_not_fixed_by_person"]


def test_an_agent_written_claim_frozen_by_a_person_afterwards_can_be_established(person, capsys, monkeypatch):
    start(capsys)
    monkeypatch.setenv("CLAUDECODE", "1")
    ok(capsys, "claim", "add", "s", "-e", "e1", "--metric", "top1", "--at-least", "0.7", "--id", "c1")
    monkeypatch.delenv("CLAUDECODE")
    ok(capsys, "freeze", "e1", "-m", "the criterion is right")
    monkeypatch.setenv("CLAUDECODE", "1")
    attach(capsys, "top1=0.76")
    v = verdict(capsys)
    assert (v["status"], v["established"]) == ("supported", True)


def test_an_agent_cannot_add_a_claim_to_a_frozen_experiment(person, capsys, monkeypatch):
    start(capsys)
    ok(capsys, "freeze", "e1", "-m", "locked")
    monkeypatch.setenv("CLAUDECODE", "1")
    code, env = rbj(capsys, "claim", "add", "s", "-e", "e1", "--metric", "top1", "--at-least", "0.7", "--id", "c1")
    assert code == 2 and env.errors[0].code == "E_FROZEN"


# ---------------------------------------------------------------- settings a claim rests on


@pytest.fixture
def one_setting(person, capsys):
    """Returns a function: record lr with the given spec-set arguments, then a claim and one confirmatory run."""
    def make(*spec_args):
        start(capsys)
        ok(capsys, "spec", "set", "e1", "lr", *spec_args)
        ok(capsys, "claim", "add", "s", "-e", "e1", "--metric", "top1", "--at-least", "0.7", "--id", "c1")
        attach(capsys, "top1=0.76")
        return verdict(capsys)
    return make


def test_a_required_unknown_setting_blocks(one_setting):
    v = one_setting("--unknown")
    assert v["status"] == "supported" and v["not_established_because"] == ["unknown"]


def test_a_required_provisional_setting_blocks(one_setting):
    v = one_setting("0.1")
    assert v["status"] == "supported" and v["not_established_because"] == ["provisional"]


def test_an_optional_unknown_setting_does_not_block(one_setting):
    assert one_setting("--unknown", "--optional")["established"]


def test_a_persons_vouch_unblocks_a_provisional_setting(one_setting, capsys):
    one_setting("0.1")
    ok(capsys, "decide", "e1/lr", "accept", "-m", "read it in the launch script")
    v = verdict(capsys)
    assert v["established"] and any(c["code"] == "vouched" and not c["blocks"] for c in v["caveats"])


def test_a_vouch_lapses_when_the_value_changes(person, capsys, monkeypatch):
    start(capsys)
    ok(capsys, "spec", "set", "e1", "lr", "0.1")
    ok(capsys, "decide", "e1/lr", "accept", "-m", "read it in the launch script")
    assert caveats(capsys, "e1/lr") == [("vouched", False)]
    monkeypatch.setenv("CLAUDECODE", "1")
    ok(capsys, "spec", "set", "e1", "lr", "0.3")
    assert caveats(capsys, "e1/lr") == [("provisional", True)]


def test_a_vouch_given_while_the_setting_was_unknown_does_not_cover_a_value_set_later(person, capsys, monkeypatch):
    """There is nothing to vouch for until the setting has a value, so the accept is refused, and a value an agent sets
    later is provisional like any other."""
    start(capsys)
    ok(capsys, "spec", "set", "e1", "seed", "--unknown")
    code, env = rbj(capsys, "decide", "e1/seed", "accept", "-m", "we will find it")
    assert code == 2 and env.errors[0].code == "E_OBJECT_INVALID" and "no value to vouch for" in env.errors[0].message
    monkeypatch.setenv("CLAUDECODE", "1")
    ok(capsys, "spec", "set", "e1", "seed", "12345")
    assert caveats(capsys, "e1/seed") == [("provisional", True)]


# ---------------------------------------------------------------- runs, noise, cited numbers


def test_fewer_confirmatory_runs_than_min_n_block(person, capsys):
    start(capsys)
    ok(capsys, "claim", "add", "s", "-e", "e1", "--metric", "top1", "--at-least", "0.7", "--min-n", "3", "--noise", "0.001", "--id", "c1")
    attach(capsys, "top1=0.76")
    attach(capsys, "top1=0.77")
    v = verdict(capsys)
    assert (v["n"], v["established"], v["not_established_because"]) == (2, False, ["too_few_runs"])
    attach(capsys, "top1=0.78")
    assert verdict(capsys)["established"]


def test_a_margin_within_the_stated_noise_is_borderline_and_blocks(person, capsys):
    start(capsys)
    ok(capsys, "claim", "add", "s", "-e", "e1", "--metric", "top1", "--at-least", "0.7", "--noise", "0.01", "--id", "c1")
    attach(capsys, "top1=0.705")
    v = verdict(capsys)
    assert v["status"] == "supported" and v["not_established_because"] == ["borderline"]


def test_the_noise_of_three_runs_makes_a_close_one_borderline(person, capsys):
    start(capsys)
    ok(capsys, "claim", "add", "s", "-e", "e1", "--metric", "top1", "--at-least", "0.7", "--id", "c1")
    for x in ("0.70", "0.75", "0.80"):
        attach(capsys, f"top1={x}")
    v = verdict(capsys)
    assert v["status"] == "supported" and v["not_established_because"] == ["borderline"]


def test_a_cited_claim_is_not_comparable_when_a_setting_differs_from_the_cited_value(person, capsys):
    start(capsys)
    Path("paper.txt").write_text("Table 3: ResNet-50 reaches 76.1 top-1 accuracy.\n", encoding="utf-8")
    Path("cfg.yaml").write_text("lr: 0.1\n", encoding="utf-8")
    ok(capsys, "spec", "set", "e1", "lr", "--source", "cfg.yaml#lr", "--cited", "0.2")
    env = ok(capsys, "claim", "add", "the paper's number", "-e", "e1", "--metric", "top1", "--equals", "76.1", "--tolerance", "0.2",
             "--source", "paper.txt", "--quote", "76.1", "--id", "c1")
    assert env.data["object"]["origin"] == "cited"
    attach(capsys, "top1=76.05")
    v = verdict(capsys)
    assert v["status"] == "not_comparable" and "not_comparable" in v["not_established_because"]


def test_a_cited_claim_whose_file_does_not_state_the_number_is_refused(person, capsys):
    start(capsys)
    Path("paper.txt").write_text("Ours: 1.43 EPE on KITTI.\n", encoding="utf-8")
    code, env = rbj(capsys, "claim", "add", "p", "-e", "e1", "--metric", "epe", "--equals", "1.5", "--tolerance", "0.1",
                    "--source", "paper.txt", "--quote", "1.43 EPE")
    assert code == 2 and env.errors[0].code == "E_SOURCE_UNRESOLVED"


# ---------------------------------------------------------------- retracting


def test_retracted_evidence_stops_counting_and_stays_on_record(person, capsys):
    start(capsys)
    ok(capsys, "claim", "add", "s", "-e", "e1", "--metric", "top1", "--at-least", "0.7", "--id", "c1")
    ev = attach(capsys, "top1=0.76")
    env = ok(capsys, "retract", ev, "-m", "the eval set leaked")
    assert env.data["rested_on"] == ["claim c1 counts it"]
    v = verdict(capsys)
    assert v["status"] == "untested" and v["observations"] == []
    shown = ok(capsys, "show", ev).data["object"]
    assert shown["retracted"]["why"] == "the eval set leaked" and shown["retracted"]["by"] == "human:t"
    assert (person / ".rb" / "evidence" / f"{ev}.json").is_file()


def test_retracting_evidence_a_claim_counts_is_a_persons_call(person, capsys, monkeypatch):
    start(capsys)
    ok(capsys, "claim", "add", "s", "-e", "e1", "--metric", "top1", "--at-least", "0.7", "--id", "c1")
    ev = attach(capsys, "top1=0.76")
    monkeypatch.setenv("CLAUDECODE", "1")
    code, env = rbj(capsys, "retract", ev, "-m", "bad run")
    assert code == 2 and env.errors[0].code == "E_HUMAN_ONLY"
    assert Ledger(person).load("evidence", ev).retracted is None


def test_an_agent_may_retract_what_nothing_rests_on(person, capsys, monkeypatch):
    start(capsys)
    monkeypatch.setenv("CLAUDECODE", "1")
    ev = attach(capsys, "top1=0.76")
    ok(capsys, "retract", ev, "-m", "typed the wrong number")
    assert Ledger(person).load("evidence", ev).retracted.by == "agent:claude-code"


# ---------------------------------------------------------------- edits outside rb


@pytest.fixture
def established(person, capsys):
    """c1 established on one confirmatory run."""
    start(capsys)
    ok(capsys, "claim", "add", "s", "-e", "e1", "--metric", "top1", "--at-least", "0.7", "--id", "c1")
    attach(capsys, "top1=0.76")
    assert verdict(capsys)["established"]
    return person


def test_a_hand_edited_claim_is_detected_and_blocks(established, capsys):
    edit_json(established / ".rb" / "claims" / "c1.json", statement="something stronger")
    v = verdict(capsys)
    assert not v["established"] and v["not_established_because"] == ["edited_outside_rb"]


def test_a_hand_edited_evidence_file_is_not_counted(established, capsys):
    [ev] = [p for p in (established / ".rb" / "evidence").glob("*.json")]
    doc = json.loads(ev.read_text(encoding="utf-8"))
    doc["metrics"]["top1"] = 0.99
    ev.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    [o] = verdict(capsys)["observations"]
    assert (o["role"], o["reason"]) == ("not_counted", "edited outside rb")


def test_a_hand_edit_fails_any_fail_on_gate(established, capsys):
    edit_json(established / ".rb" / "claims" / "c1.json", statement="something stronger")
    code, env = rbj(capsys, "status", "--fail-on", "refuted")
    assert code == 1 and env.data["outcome"]["passed"] is False


def test_doctor_reports_a_hand_edit(established, capsys):
    edit_json(established / ".rb" / "claims" / "c1.json", statement="something stronger")
    code, env = rbj(capsys, "doctor")
    assert code == 1 and [f["check"] for f in env.data["outcome"]["failures"]] == ["edited_outside_rb"]


def test_an_unparseable_object_is_state_corrupt(established, capsys):
    (established / ".rb" / "claims" / "c1.json").write_text("{not json", encoding="utf-8")
    for argv in (["status"], ["show", "c1"], ["context"]):
        code, env = rbj(capsys, *argv)
        assert code == 2 and env.errors[0].code == "E_STATE_CORRUPT", argv


def test_an_object_that_breaks_its_own_rules_is_state_corrupt(person, capsys):
    """A setting marked verified by hand, with nothing rb read behind it."""
    start(capsys)
    ok(capsys, "spec", "set", "e1", "lr", "0.1")
    p = person / ".rb" / "experiments" / "e1.json"
    doc = json.loads(p.read_text(encoding="utf-8"))
    doc["settings"][0]["status"] = "verified"
    p.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    code, env = rbj(capsys, "status")
    assert code == 2 and env.errors[0].code == "E_STATE_CORRUPT"


def test_a_setting_source_outside_the_project_is_refused(person, capsys, tmp_path_factory):
    elsewhere = tmp_path_factory.mktemp("elsewhere") / "cfg.yaml"
    elsewhere.write_text("lr: 0.1\n", encoding="utf-8")
    start(capsys)
    code, env = rbj(capsys, "spec", "set", "e1", "lr", "0.1", "--source", f"{elsewhere}#lr")
    assert code == 1 and env.data["object"]["status"] == "provisional"
    assert [f["code"] for f in env.data["outcome"]["failures"]] == ["E_SOURCE_OUTSIDE"]


def test_a_cited_claim_source_outside_the_project_is_refused(person, capsys, tmp_path_factory):
    elsewhere = tmp_path_factory.mktemp("elsewhere") / "paper.txt"
    elsewhere.write_text("We reach 76.1 top-1.\n", encoding="utf-8")
    start(capsys)
    code, env = rbj(capsys, "claim", "add", "p", "-e", "e1", "--metric", "top1", "--equals", "76.1", "--tolerance", "0.1",
                    "--source", str(elsewhere), "--quote", "76.1")
    assert code == 2 and env.errors[0].code == "E_SOURCE_OUTSIDE"


# ---------------------------------------------------------------- the log, the files, concurrent writers


def test_init_keeps_the_lock_out_of_git_and_merges_the_log_by_line(person, capsys):
    ok(capsys, "init", "integrity")
    assert (person / ".rb" / ".gitignore").read_text(encoding="utf-8").split() == [".lock", "*.tmp"]
    assert (person / ".rb" / ".gitattributes").read_text(encoding="utf-8").strip() == "log.jsonl merge=union"


def test_every_write_is_logged_with_its_actor_surface_and_hash(person, capsys, monkeypatch):
    start(capsys)
    ok(capsys, "claim", "add", "s", "-e", "e1", "--metric", "top1", "--at-least", "0.7", "--id", "c1")
    monkeypatch.setenv("CLAUDECODE", "1")
    ev = attach(capsys, "top1=0.76")
    rows = Ledger(person).log_entries()
    assert [(r["op"], r["kind"], r["id"]) for r in rows] == [
        ("init", "investigation", "integrity"), ("add", "experiment", "e1"), ("add", "claim", "c1"), ("attach", "evidence", ev)]
    for r in rows:
        assert {"at", "actor", "actor_via", "via", "op", "kind", "id", "sha256"} <= set(r)
        assert r["via"] == "cli"
    assert [(r["actor"], r["actor_via"]) for r in rows][-2:] == [("human:t", "git-email" if HAS_GIT else "login"), ("agent:claude-code", "detected:CLAUDECODE")]
    assert rows[2]["sha256"] == hashlib.sha256((person / ".rb" / "claims" / "c1.json").read_bytes()).hexdigest()


def test_ids_are_a_kind_prefix_and_four_random_characters(person, capsys):
    start(capsys)
    q = ok(capsys, "question", "add", "why").data["object"]["id"]
    e = ok(capsys, "experiment", "add", "x2").data["object"]["id"]
    c = ok(capsys, "claim", "add", "s", "-e", e, "--metric", "m", "--at-most", "1").data["object"]["id"]
    ev = attach(capsys, "m=0.5", exp=e)
    d = ok(capsys, "decide", c, "investigate", "-m", "look again").data["object"]["id"]
    for prefix, id_ in (("q", q), ("e", e), ("c", c), ("ev", ev), ("d", d)):
        assert re.fullmatch(rf"{prefix}[a-z2-7]{{4}}", id_), id_


def test_concurrent_writers_lose_nothing(person, capsys):
    ok(capsys, "init", "integrity")
    ids, errors = [], []

    def write(i):
        try:
            led = Ledger(person)
            for j in range(5):
                ids.append(led.add_question(f"question {i}.{j}").id)
        except Exception as exc:  # noqa: BLE001 - collected and asserted below
            errors.append(exc)

    threads = [threading.Thread(target=write, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    led = Ledger(person)
    assert errors == [] and len(set(ids)) == 40
    assert len(list((person / ".rb" / "questions").glob("*.json"))) == 40
    assert sum(1 for r in led.log_entries() if r["kind"] == "question") == 40
    assert all(led.edited_outside("question", q) is None for q in ids)
    assert not list((person / ".rb").rglob("*.tmp"))
