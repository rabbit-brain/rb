"""The 0.5.0 release gate: a determined agent cannot get an unearned "established".

Played in a git repository, inside a Claude Code session (CLAUDECODE=1, RB_ACTOR unset). The agent:

1. starts the research state, adds an experiment, and attaches top1=0.7612;
2. writes a claim whose criterion (top1 >= 0.7611) was picked after seeing that number, then attaches another run;
3. tries to freeze the experiment: refused with E_HUMAN_ONLY and the command to hand to the person;
4. records settings whose sources do not set them: a comment line stating the value, and a line for a different key
   that happens to hold the value; none of them verifies;
5. `rb status --fail-on unestablished` exits 1;
6. `rb context` lists nothing under Established, and the claim under Not established.

Then the obvious cheats, each pinned to what rb does:

- RB_ACTOR=human:x inside the session: rb cannot tell who typed it, so the freeze is accepted, and it is stamped as
  asserted from a Claude Code session on the object, in the log, and in `rb show`, `rb log` and `rb context`;
- deciding or vouching for its own work: refused, like the freeze;
- hand-editing the claim (its author, its criterion), hand-writing a claim file, hand-editing a setting to verified:
  none of them establishes the claim;
- attaching the same evidence twice, or again on purpose, does not reach min_n.
"""
import hashlib
import json
import os
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

from rabbit_brain.cli import main
from rabbit_brain.ledger import Ledger
from rabbit_brain.models import Envelope

HAS_GIT = shutil.which("git") is not None
pytestmark = pytest.mark.skipif(not HAS_GIT, reason="the gate is played in a git repository")
AGENT_VARS = ("CLAUDECODE", "AI_AGENT", "GITHUB_ACTIONS", "RB_ACTOR")
FREEZE = ["freeze", "e1", "-m", "lock the criterion"]


# ---------------------------------------------------------------- the session


def git(*args) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def session(workdir, monkeypatch, tmp_path_factory):
    """A git repository whose person is human:t, inside a Claude Code session, with RB_ACTOR unset."""
    for name in list(os.environ):
        if name in AGENT_VARS or name.startswith("CODEX_"):
            monkeypatch.delenv(name)
    cfg = tmp_path_factory.mktemp("gitconfig") / "config"
    cfg.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(cfg))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    git("init", "-q")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    monkeypatch.setenv("CLAUDECODE", "1")
    return workdir


def rbj(capsys, *argv):
    code = main([*argv, "--json"])
    return code, Envelope.model_validate_json(capsys.readouterr().out)


def ok(capsys, *argv) -> Envelope:
    code, env = rbj(capsys, *argv)
    assert code in (0, 1) and env.ok, (argv, env.errors)
    return env


def attach(capsys, *argv) -> str:
    return ok(capsys, "evidence", "attach", "e1", *argv).data["object"]["id"]


def verdict(capsys, claim="c1") -> dict:
    return ok(capsys, "show", claim).data["verdict"]


def section(markdown: str, title: str) -> str:
    """The body of one `## title` section of rb context."""
    body = markdown.split(f"\n## {title}\n", 1)[1]
    return body.split("\n## ", 1)[0]


def as_person(monkeypatch) -> None:
    monkeypatch.delenv("CLAUDECODE")


def as_agent(monkeypatch) -> None:
    monkeypatch.setenv("CLAUDECODE", "1")


def edit_json(path: Path, **changes) -> None:
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc.update(changes)
    path.write_text(json.dumps(doc, indent=2), encoding="utf-8")


# ---------------------------------------------------------------- the agent's run


@pytest.fixture
def gamed(session, capsys):
    """Steps 1, 2 and 4, all as the agent. Returns the two evidence ids."""
    ok(capsys, "init", "resnet50 on imagenet")
    ok(capsys, "experiment", "add", "resnet50 top-1", "--id", "e1")
    seen = attach(capsys, "top1=0.7612")
    claimed = ok(capsys, "claim", "add", "ResNet-50 reaches 76.11% top-1", "-e", "e1", "--metric", "top1", "--at-least", "0.7611",
                 "--id", "c1").data["verdict"]
    Path("train.yaml").write_text("model: resnet50\n# lr: 0.1\nlr: 0.2\nepochs: 90\nlabel_smoothing: 0.1\n", encoding="utf-8")
    Path("train.py").write_text("parser.add_argument('--epochs', default=5)\n", encoding="utf-8")
    rbj(capsys, "spec", "set", "e1", "lr", "0.1", "--source", "train.yaml:2")                   # a comment stating 0.1
    rbj(capsys, "spec", "set", "e1", "dropout", "0.1", "--source", "train.yaml:5")              # label_smoothing's line
    rbj(capsys, "spec", "set", "e1", "warmup_epochs", "5", "--source", "train.py:1")            # epochs' line
    run = attach(capsys, "top1=0.7613")
    return {"seen": seen, "run": run, "claimed": claimed}


def test_everything_the_agent_wrote_is_recorded_as_the_agent(gamed, session):
    rows = Ledger(session).log_entries()
    assert rows and {(r["actor"], r["actor_via"]) for r in rows} == {("agent:claude-code", "detected:CLAUDECODE")}


def test_the_number_the_criterion_was_fitted_to_is_exploratory_when_the_claim_is_written(gamed):
    v = gamed["claimed"]
    assert [(o["evidence"], o["role"]) for o in v["observations"]] == [(gamed["seen"], "exploratory")]
    assert v["status"] == "untested" and not v["established"]


def test_a_criterion_picked_after_seeing_the_number_is_not_established_by_a_later_run(gamed, capsys):
    v = verdict(capsys)
    roles = {o["evidence"]: o["role"] for o in v["observations"]}
    assert roles[gamed["run"]] == "confirmatory" and roles[gamed["seen"]] != "confirmatory"
    assert v["status"] == "supported" and v["established"] is False
    assert "criterion_not_fixed_by_person" in v["not_established_because"]


def test_the_agent_cannot_freeze_and_is_handed_the_command_for_the_person(gamed, capsys, session):
    code, env = rbj(capsys, *FREEZE)
    err = env.errors[0]
    assert code == 2 and err.code == "E_HUMAN_ONLY"
    assert err.handoff == {"who": "person", "command": shlex.join(["rb", *FREEZE])}
    assert Ledger(session).load("experiment", "e1").frozen is None


def test_a_comment_line_stating_the_value_does_not_verify(gamed, capsys):
    assert ok(capsys, "show", "e1/lr").data["object"]["status"] == "provisional"


@pytest.mark.parametrize("address", ["e1/dropout", "e1/warmup_epochs"], ids=["yaml-line", "script-line"])
def test_a_line_for_a_different_key_holding_the_value_does_not_verify(gamed, capsys, address):
    assert ok(capsys, "show", address).data["object"]["status"] == "provisional"


def test_status_fails_the_unestablished_gate(gamed, capsys):
    code, env = rbj(capsys, "status", "--fail-on", "unestablished")
    assert code == 1 and env.data["outcome"]["failures"][0]["code"] == "unestablished"
    assert [c["established"] for c in env.data["claims"]] == [False]


def test_context_lists_nothing_as_established(gamed, capsys):
    md = ok(capsys, "context").data["markdown"]
    established = section(md, "Established")
    assert "c1" not in established and "Nothing" in established
    assert "c1" in section(md, "Not established")


@pytest.mark.parametrize("argv", [
    ["decide", "c1", "accept", "-m", "the number is in"],
    ["decide", "e1/lr", "accept", "-m", "I read it in the config"],
])
def test_the_agent_cannot_decide_or_vouch_for_its_own_work(gamed, capsys, argv):
    code, env = rbj(capsys, *argv)
    assert code == 2 and env.errors[0].code == "E_HUMAN_ONLY"
    assert env.errors[0].handoff["command"] == shlex.join(["rb", *argv])


# ---------------------------------------------------------------- cheat: asserting a person from inside the session


def test_asserting_a_person_inside_the_session_freezes_and_is_stamped(gamed, capsys, monkeypatch, session):
    monkeypatch.setenv("RB_ACTOR", "human:x")
    env = ok(capsys, *FREEZE)
    frozen = env.data["object"]["frozen"]
    assert (frozen["by"], frozen["asserted_from"]) == ("human:x", "claude-code")
    last = Ledger(session).log_entries()[-1]
    assert (last["op"], last["actor"], last["actor_via"], last["asserted_from"]) == ("freeze", "human:x", "RB_ACTOR", "claude-code")


@pytest.fixture
def asserted(session, capsys, monkeypatch):
    """The agent writes a claim, freezes as RB_ACTOR=human:x from inside the session, then attaches a run."""
    ok(capsys, "init", "resnet50 on imagenet")
    ok(capsys, "experiment", "add", "resnet50 top-1", "--id", "e1")
    ok(capsys, "claim", "add", "ResNet-50 reaches 76% top-1", "-e", "e1", "--metric", "top1", "--at-least", "0.76", "--noise", "0.0001", "--id", "c1")
    monkeypatch.setenv("RB_ACTOR", "human:x")
    ok(capsys, *FREEZE)
    monkeypatch.delenv("RB_ACTOR")
    attach(capsys, "top1=0.7612")
    return session


def test_an_asserted_freeze_is_shown_as_asserted_on_the_claim(asserted, capsys):
    [cav] = [c for c in verdict(capsys)["caveats"] if c["code"] == "asserted_freeze"]
    assert "human:x" in cav["text"] and "from inside a Claude Code session" in cav["text"] and cav["blocks"]


def test_an_asserted_freeze_is_shown_as_asserted_in_the_log(asserted, capsys):
    main(["log"])
    assert "human:x (asserted from a Claude Code session) freeze experiment e1" in capsys.readouterr().out


def test_an_asserted_freeze_does_not_establish_the_claim(asserted, capsys):
    """rb cannot stop an agent giving a person's name. It records the call, marks it, and does not count it as a person's:
    a person at their own terminal is never inside a detected agent session, so only the cheat takes this path."""
    md = ok(capsys, "context").data["markdown"]
    assert "c1" not in section(md, "Established")
    assert "c1" in section(md, "Not established") and "from inside a Claude Code session" in section(md, "Not established")
    assert rbj(capsys, "status", "--fail-on", "unestablished")[0] == 1


# ---------------------------------------------------------------- cheat: editing .rb/ by hand


def test_hand_editing_the_claims_author_to_a_person_is_caught(session, capsys):
    ok(capsys, "init", "t")
    ok(capsys, "experiment", "add", "x", "--id", "e1")
    ok(capsys, "claim", "add", "s", "-e", "e1", "--metric", "top1", "--at-least", "0.7", "--noise", "0.001", "--id", "c1")
    attach(capsys, "top1=0.76")
    assert verdict(capsys)["not_established_because"] == ["criterion_not_fixed_by_person"]
    edit_json(session / ".rb" / "claims" / "c1.json", created_by="human:t")
    v = verdict(capsys)
    assert not v["established"] and "edited_outside_rb" in v["not_established_because"]
    assert rbj(capsys, "status", "--fail-on", "unestablished")[0] == 1


def test_hand_editing_the_criterion_after_the_freeze_is_caught(session, capsys, monkeypatch):
    ok(capsys, "init", "t")
    ok(capsys, "experiment", "add", "x", "--id", "e1")
    ok(capsys, "claim", "add", "s", "-e", "e1", "--metric", "top1", "--at-least", "0.7", "--noise", "0.001", "--id", "c1")
    as_person(monkeypatch)
    ok(capsys, *FREEZE)
    as_agent(monkeypatch)
    attach(capsys, "top1=0.65")
    assert verdict(capsys)["status"] == "refuted"
    edit_json(session / ".rb" / "claims" / "c1.json", target=0.6)
    v = verdict(capsys)
    assert not v["established"] and {"edited_outside_rb", "drift"} <= set(v["not_established_because"])


def test_hand_writing_a_claim_file_is_caught(session, capsys):
    ok(capsys, "init", "t")
    ok(capsys, "experiment", "add", "x", "--id", "e1")
    ok(capsys, "claim", "add", "s", "-e", "e1", "--metric", "top1", "--at-least", "0.7", "--noise", "0.001", "--id", "c1")
    forged = json.loads((session / ".rb" / "claims" / "c1.json").read_text(encoding="utf-8"))
    forged.update(id="c2", created_by="human:t")
    (session / ".rb" / "claims" / "c2.json").write_text(json.dumps(forged, indent=2), encoding="utf-8")
    attach(capsys, "top1=0.76")
    assert not verdict(capsys, "c2")["established"]
    assert rbj(capsys, "status", "--fail-on", "unestablished")[0] == 1


@pytest.fixture
def unchecked_setting(session, capsys, monkeypatch):
    """lr = 0.2 recorded unverified against a config that says 0.1; a person froze the agent's claim; one run attached.
    The only thing between the claim and established is that setting."""
    ok(capsys, "init", "t")
    ok(capsys, "experiment", "add", "x", "--id", "e1")
    Path("cfg.yaml").write_text("lr: 0.1\n", encoding="utf-8")
    ok(capsys, "spec", "set", "e1", "lr", "0.2", "--source", "cfg.yaml#lr", "--no-verify")
    ok(capsys, "claim", "add", "s", "-e", "e1", "--metric", "top1", "--at-least", "0.7", "--noise", "0.001", "--id", "c1")
    as_person(monkeypatch)
    ok(capsys, *FREEZE)
    as_agent(monkeypatch)
    attach(capsys, "top1=0.76")
    assert verdict(capsys)["not_established_because"] == ["provisional"]
    return session / ".rb" / "experiments" / "e1.json"


def test_hand_marking_a_setting_verified_without_a_reading_is_state_corrupt(unchecked_setting, capsys):
    doc = json.loads(unchecked_setting.read_text(encoding="utf-8"))
    doc["settings"][0]["status"] = "verified"
    unchecked_setting.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    code, env = rbj(capsys, "status", "--fail-on", "unestablished")
    assert code == 2 and env.errors[0].code == "E_STATE_CORRUPT"


def test_hand_marking_a_setting_verified_with_a_forged_reading_is_caught(unchecked_setting, capsys):
    doc = json.loads(unchecked_setting.read_text(encoding="utf-8"))
    doc["settings"][0]["status"] = "verified"
    doc["settings"][0]["source"]["resolved"] = {"at": "2026-01-01T00:00:00+00:00", "sha256": hashlib.sha256(b"lr: 0.1\n").hexdigest(),
                                                "text": "lr: 0.2", "read": 0.2}
    unchecked_setting.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    v = verdict(capsys)
    assert not v["established"] and "edited_outside_rb" in v["not_established_because"]
    assert rbj(capsys, "status", "--fail-on", "unestablished")[0] == 1


# ---------------------------------------------------------------- cheat: the same run twice to reach min_n


@pytest.fixture
def needs_two_runs(session, capsys, monkeypatch):
    """A claim needing two runs, frozen by a person, with one run attached. Returns that run's id."""
    ok(capsys, "init", "t")
    ok(capsys, "experiment", "add", "x", "--id", "e1")
    ok(capsys, "claim", "add", "s", "-e", "e1", "--metric", "top1", "--at-least", "0.7", "--min-n", "2", "--noise", "0.001", "--id", "c1")
    as_person(monkeypatch)
    ok(capsys, *FREEZE)
    as_agent(monkeypatch)
    first = attach(capsys, "top1=0.76")
    assert verdict(capsys)["not_established_because"] == ["too_few_runs"]
    return first


def test_attaching_the_same_evidence_twice_writes_nothing_and_does_not_reach_min_n(needs_two_runs, capsys, session):
    env = ok(capsys, "evidence", "attach", "e1", "top1=0.76")
    assert env.data["duplicate_of"] == needs_two_runs
    assert len(list((session / ".rb" / "evidence").glob("*.json"))) == 1
    v = verdict(capsys)
    assert v["n"] == 1 and not v["established"] and v["not_established_because"] == ["too_few_runs"]


def test_attaching_the_same_evidence_again_on_purpose_does_not_count_twice(needs_two_runs, capsys):
    again = attach(capsys, "top1=0.76", "--again", "-m", "reran it")
    assert again != needs_two_runs
    v = verdict(capsys)
    assert v["n"] == 1 and not v["established"] and "too_few_runs" in v["not_established_because"]
