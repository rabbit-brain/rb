"""The release gate: a determined agent cannot pass `rb status --fail-on unestablished` with a criterion it
fitted to a number it had already seen.

Played in a git repository, inside a Claude Code session (CLAUDECODE=1, RB_ACTOR unset). The agent:

1. starts the record, adds an experiment, and records a run with top1=0.7612;
2. writes a criterion (top1 >= 0.7611) picked after seeing that number, then records another run;
3. freezes the experiment: the freeze is taken, recorded as the agent's, and moves no declaration;
4. records settings whose sources do not set them: a comment line stating the value, and a line for a different key
   that happens to hold the value; none of them verifies;
5. `rb status --fail-on unestablished` exits 1: the criterion was declared after its evidence existed (late);
6. `rb context` says so on the criterion's timing line.

An agent's criterion counts as declared by the agent, with no freeze by a human; what gives the fitted one away is
its timing, read from when the evidence was produced.

Then the obvious cheats, each pinned to what rb does:

- RB_ACTOR=human:x inside the session: rb cannot tell who typed it, so the name is ignored: a freeze, a ruling, an
  amendment and a retraction of what a criterion counts all go through as the agent's, and the name appears in the
  record only in log.jsonl, as ignored;
- ruling on or vouching for its own work: taken like the freeze, and the finding says whose call it is;
- hand-editing the criterion (its author, its rule), hand-writing a criterion into the criteria file, hand-editing a
  setting to verified: each is an integrity problem of the finding, and fails the gate;
- recording the same run twice writes nothing and does not reach min_n; recording it again on purpose is a second
  execution, a unit of its own that carries the reason it was recorded again;
- adopting a hand edit (rb doctor --adopt) never launders authority, and runs only against what was reviewed.

A hand edit is played the way an agent with a text editor makes one in format 1: the Rabbit Brain YAML file rb wrote, read
with rbyaml, a value changed, written back with rbyaml.dumps. A comment or a reformat is not an edit in this store;
every edit below changes a value.
"""
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from rabbit_brain.models import Envelope
from rbtest import as_the_user, main, rbyaml, state

HAS_GIT = shutil.which("git") is not None
pytestmark = pytest.mark.skipif(not HAS_GIT, reason="the gate is played in a git repository")
FREEZE = ["freeze", "e1", "-m", "lock the criterion"]


# ---------------------------------------------------------------- the session


def git(*args) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def session(workdir, monkeypatch, tmp_path_factory):
    """A git repository whose human is human:t, inside a Claude Code session, with RB_ACTOR unset."""
    as_the_user(monkeypatch)
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


def add_run(capsys, *argv) -> str:
    return ok(capsys, "run", "add", "e1", *argv).data["object"]["id"]


def finding(capsys, criterion="c1") -> dict:
    return ok(capsys, "show", criterion).data["finding"]


def problem_codes(f: dict) -> set:
    """The codes of a finding's integrity problems."""
    return {c["code"] for c in f["integrity"]}


def section(markdown: str, title: str) -> str:
    """The body of one `## title` section of rb context."""
    body = markdown.split(f"\n## {title}\n", 1)[1]
    return body.split("\n## ", 1)[0]


def as_human(monkeypatch) -> None:
    """The human at their own terminal: every agent, actor and session variable cleared."""
    as_the_user(monkeypatch)


def as_agent(monkeypatch) -> None:
    monkeypatch.setenv("CLAUDECODE", "1")


def criterion_file(session: Path, criterion: str = "c1") -> Path:
    """Where one of e1's criteria lives: a Rabbit Brain YAML file of its own in e1's criteria/."""
    return session / ".rb" / "experiments" / "e1" / "criteria" / f"{criterion}.yaml"


def edit_criterion(session: Path, criterion: str, **changes) -> None:
    """Change a criterion's values by hand: its file, read with rbyaml, changed, and written back with rbyaml.dumps."""
    path = criterion_file(session, criterion)
    doc = rbyaml.loads(path.read_text(encoding="utf-8"))
    doc.update(changes)
    path.write_text(rbyaml.dumps(doc), encoding="utf-8")


# ---------------------------------------------------------------- the agent's run


@pytest.fixture
def gamed(session, capsys):
    """Steps 1, 2 and 4, all as the agent. Returns the two run ids and the finding as the criterion was written."""
    ok(capsys, "init", "resnet50 on imagenet")
    ok(capsys, "experiment", "add", "resnet50 top-1", "--id", "e1")
    seen = add_run(capsys, "top1=0.7612")
    written = ok(capsys, "criterion", "add", "ResNet-50 reaches 76.11% top-1", "-e", "e1", "--metric", "top1", "--at-least", "0.7611",
                 "--id", "c1").data["finding"]
    Path("train.yaml").write_text("model: resnet50\n# lr: 0.1\nlr: 0.2\nepochs: 90\nlabel_smoothing: 0.1\n", encoding="utf-8")
    Path("train.py").write_text("parser.add_argument('--epochs', default=5)\n", encoding="utf-8")
    rbj(capsys, "spec", "set", "e1", "lr", "0.1", "--source", "train.yaml:2")                   # a comment stating 0.1
    rbj(capsys, "spec", "set", "e1", "dropout", "0.1", "--source", "train.yaml:5")              # label_smoothing's line
    rbj(capsys, "spec", "set", "e1", "warmup_epochs", "5", "--source", "train.py:1")            # epochs' line
    run = add_run(capsys, "top1=0.7613")
    return {"seen": seen, "run": run, "written": written}


def test_everything_the_agent_wrote_is_recorded_as_the_agent(gamed, session):
    rows = state(session).log(None)
    assert rows and {(r["actor"], r["actor_via"]) for r in rows} == {("agent:claude-code", "detected:CLAUDECODE")}


def test_the_number_the_criterion_was_fitted_to_reads_as_evidence_that_predates_it(gamed):
    """As written, the criterion reads the number it was fitted to, counted, and says that number existed before it was
    declared: its timing is after_evidence, and it is declared by the agent."""
    f = gamed["written"]
    assert [(o["run"], o["counted"], o["before_declared"]) for o in f["observations"]] == [(gamed["seen"], True, True)]
    assert f["timing"] == "after_evidence" and f["declared"]["by"] == "agent:claude-code"


# Step 4 changes the spec after the criterion is written, so the run it was fitted to reads as under an earlier spec
# (history). It still existed before the criterion was declared, and the timing says so.


def test_a_criterion_picked_after_seeing_the_number_stays_late_after_a_later_run(gamed, capsys):
    """A later run does not make the fitted criterion prospective: the number it was fitted to still existed before it
    was declared, so its timing stays after_evidence and --fail-on late fails."""
    f = finding(capsys)
    seen = next(o for o in f["observations"] if o["run"] == gamed["seen"])
    assert seen["before_declared"] and f["timing"] == "after_evidence"
    assert rbj(capsys, "status", "--fail-on", "late")[0] == 1


def test_the_agents_freeze_is_its_own_and_the_fitted_criterion_stays_late(gamed, capsys, session):
    """Step 3: the freeze is taken and recorded as the agent's, and the finding says who froze it. Freezing moves no
    declaration: the criterion still reads as declared after its evidence, and the gate still fails."""
    ok(capsys, *FREEZE)
    assert state(session).get("e1", "experiment").frozen.by == "agent:claude-code"
    f = finding(capsys)
    assert (f["declared"]["by"], f["declared"]["frozen_by"], f["timing"]) == ("agent:claude-code", "agent:claude-code", "after_evidence")
    assert rbj(capsys, "status", "--fail-on", "unestablished")[0] == 1


def test_a_comment_line_stating_the_value_does_not_verify(gamed, capsys):
    assert ok(capsys, "show", "e1/lr").data["object"]["status"] == "provisional"


@pytest.mark.parametrize("address", ["e1/dropout", "e1/warmup_epochs"], ids=["yaml-line", "script-line"])
def test_a_line_for_a_different_key_holding_the_value_does_not_verify(gamed, capsys, address):
    assert ok(capsys, "show", address).data["object"]["status"] == "provisional"


def test_status_fails_the_unestablished_gate(gamed, capsys):
    code, env = rbj(capsys, "status", "--fail-on", "unestablished")
    assert code == 1 and env.data["outcome"]["failures"][0]["code"] == "unestablished"
    assert env.data["gate"]["late"] and [f["timing"] for f in env.data["findings"]] == ["after_evidence"]


def test_context_says_the_criterion_was_declared_after_its_evidence(gamed, capsys):
    """rb context, what the next session reads first, says on the criterion's timing line that it was declared after
    its evidence existed."""
    criteria = section(ok(capsys, "context").data["markdown"], "Rules")
    timing = [line for line in criteria.splitlines() if line.strip().startswith("timing:")]
    assert "c1" in criteria and len(timing) == 1 and "after the evidence existed" in timing[0]


@pytest.mark.parametrize("argv", [
    ["decide", "c1", "accept", "-m", "the number is in"],
    ["decide", "e1/lr", "accept", "-m", "I read it in the config"],
], ids=["ruling", "vouch"])
def test_the_agents_ruling_or_vouch_on_its_own_work_is_shown_as_the_agents(gamed, capsys, argv):
    """Ruling on its own criterion, or vouching for a setting it recorded: the decision is the agent's and proposed,
    not made, so the finding has no review and lr no vouch until the user confirms it, status lists it apart with the
    command that confirms it, and neither makes the fitted criterion prospective, so the gate still fails."""
    d = ok(capsys, *argv).data["object"]
    assert d["by"] == "agent:claude-code"
    f = finding(capsys)
    if argv[1] == "c1":
        assert f["review"] is None
    else:
        assert not [c for c in f["caveats"] if (c["code"], c["subject"]) == ("vouched", "e1/lr")]
    code, st = rbj(capsys, "status", "--fail-on", "unestablished")
    assert code == 1 and [p["id"] for p in st.data["pending"]] == [d["id"]]


# ---------------------------------------------------------------- cheat: asserting a human from inside the session


@pytest.mark.parametrize("argv", [
    ("freeze", "e1", "-m", "lock the criterion"),
    ("decide", "c1", "accept", "-m", "fine"),
    ("spec", "set", "e1", "top1_floor", "0.5", "--amend", "-m", "looser"),
], ids=["freeze", "ruling", "amendment"])
def test_a_humans_name_typed_inside_the_session_is_recorded_as_the_agent(gamed, capsys, monkeypatch, session, argv):
    """RB_ACTOR=human:x inside the agent's own session is ignored: the call goes through as the agent's, every write it
    makes is logged as the agent's with the name kept only as ignored, no record names human:x, and the gate still
    fails."""
    if argv[0] != "freeze":
        as_human(monkeypatch)
        ok(capsys, *FREEZE)
        as_agent(monkeypatch)
    log = session / ".rb" / "log.jsonl"
    before = log.read_text(encoding="utf-8")
    monkeypatch.setenv("RB_ACTOR", "human:x")
    env = ok(capsys, *argv)
    assert env.data["actor"] == {"id": "agent:claude-code", "via": "detected:CLAUDECODE", "ignored_rb_actor": "human:x",
                                 "agent": {"name": "claude-code", "provider": "anthropic", "provider_from": "inferred"}}
    rows = [json.loads(line) for line in log.read_text(encoding="utf-8")[len(before):].splitlines()]
    assert rows and {(r["actor"], r["ignored_rb_actor"]) for r in rows} == {("agent:claude-code", "human:x")}
    assert not any("human:x" in p.read_text(encoding="utf-8") for p in (session / ".rb").rglob("*.yaml"))
    assert rbj(capsys, "status", "--fail-on", "unestablished")[0] == 1


def test_a_humans_name_typed_inside_the_session_retracts_what_a_criterion_counts_as_the_agent(session, capsys, monkeypatch):
    """Retracting a run a frozen criterion counts, with RB_ACTOR=human:ana typed inside the session, is taken as the
    agent's retraction, the name ignored: rb says what rested on the run, and the criterion reads the run left."""
    as_human(monkeypatch)
    ok(capsys, "init", "t")
    ok(capsys, "experiment", "add", "x", "--id", "e1", "--baseline", "b", "--candidate", "c")
    ok(capsys, "criterion", "add", "c at most 1", "-e", "e1", "--metric", "c.loss", "--at-most", "1", "--id", "c1")
    ok(capsys, *FREEZE)
    as_agent(monkeypatch)
    good = add_run(capsys, "c.loss=0.5")
    bad = add_run(capsys, "c.loss=1.5")
    monkeypatch.setenv("RB_ACTOR", "human:ana")
    env = ok(capsys, "retract", bad, "-m", "outlier")
    assert env.data["rested_on"] == ["criterion c1 counts it", "experiment e1 is frozen"]
    assert state(session).get(bad, "run").retracted.by == "agent:claude-code"
    f = finding(capsys)
    assert [(o["run"], o["counted"]) for o in f["observations"]] == [(good, True)]
    assert f["n"] == 1 and f["comparison"]["estimate"] == pytest.approx(0.5)


# ---------------------------------------------------------------- cheat: editing .rb/ by hand


def test_hand_editing_the_criterions_author_to_a_human_is_caught(session, capsys):
    ok(capsys, "init", "t")
    ok(capsys, "experiment", "add", "x", "--id", "e1")
    ok(capsys, "criterion", "add", "s", "-e", "e1", "--metric", "top1", "--at-least", "0.7", "--noise", "0.001", "--id", "c1")
    add_run(capsys, "top1=0.76")
    f = finding(capsys)
    assert f["declared"]["by"] == "agent:claude-code" and f["integrity"] == []
    edit_criterion(session, "c1", created_by="human:t")
    assert "edited_outside_rb" in problem_codes(finding(capsys))
    assert rbj(capsys, "status", "--fail-on", "unestablished")[0] == 1


def test_hand_editing_the_criterion_after_the_freeze_is_caught(session, capsys, monkeypatch):
    ok(capsys, "init", "t")
    ok(capsys, "experiment", "add", "x", "--id", "e1")
    ok(capsys, "criterion", "add", "s", "-e", "e1", "--metric", "top1", "--at-least", "0.7", "--noise", "0.001", "--id", "c1")
    as_human(monkeypatch)
    ok(capsys, *FREEZE)
    as_agent(monkeypatch)
    add_run(capsys, "top1=0.65")
    assert finding(capsys)["rule_outcome"] == "misses"
    edit_criterion(session, "c1", target=0.6)
    assert {"edited_outside_rb", "drift"} <= problem_codes(finding(capsys))


def test_hand_writing_a_criterion_is_caught(session, capsys):
    """A criterion's place is a file of its own in its experiment's criteria/: the forgery is c1's file copied as c2, in
    a human's name, written there by hand."""
    ok(capsys, "init", "t")
    ok(capsys, "experiment", "add", "x", "--id", "e1")
    ok(capsys, "criterion", "add", "s", "-e", "e1", "--metric", "top1", "--at-least", "0.7", "--noise", "0.001", "--id", "c1")
    doc = rbyaml.loads(criterion_file(session).read_text(encoding="utf-8"))
    criterion_file(session, "c2").write_text(rbyaml.dumps({**doc, "id": "c2", "created_by": "human:t"}), encoding="utf-8")
    add_run(capsys, "top1=0.76")
    assert "edited_outside_rb" in problem_codes(finding(capsys, "c2"))
    assert rbj(capsys, "status", "--fail-on", "unestablished")[0] == 1


@pytest.fixture
def unchecked_setting(session, capsys, monkeypatch):
    """lr = 0.2 recorded unverified against a config that says 0.1; a human froze the agent's criterion; one run
    recorded. The finding has no integrity problem, and that setting is its note that nobody checked lr. Returns e1's
    experiment file."""
    ok(capsys, "init", "t")
    ok(capsys, "experiment", "add", "x", "--id", "e1")
    Path("cfg.yaml").write_text("lr: 0.1\n", encoding="utf-8")
    ok(capsys, "spec", "set", "e1", "lr", "0.2", "--source", "cfg.yaml#lr", "--no-verify")
    ok(capsys, "criterion", "add", "s", "-e", "e1", "--metric", "top1", "--at-least", "0.7", "--noise", "0.001", "--id", "c1")
    as_human(monkeypatch)
    ok(capsys, *FREEZE)
    as_agent(monkeypatch)
    add_run(capsys, "top1=0.76")
    f = finding(capsys)
    assert f["integrity"] == [] and [(c["code"], c["subject"]) for c in f["caveats"] if c["code"] == "provisional"] == [("provisional", "e1/lr")]
    return session / ".rb" / "experiments" / "e1" / "experiment.yaml"


def test_hand_marking_a_setting_verified_without_a_reading_is_state_corrupt(unchecked_setting, capsys):
    doc = rbyaml.loads(unchecked_setting.read_text(encoding="utf-8"))
    doc["settings"][0]["status"] = "verified"
    unchecked_setting.write_text(rbyaml.dumps(doc), encoding="utf-8")
    code, env = rbj(capsys, "status", "--fail-on", "unestablished")
    assert code == 2 and env.errors[0].code == "E_STATE_CORRUPT"


def test_hand_marking_a_setting_verified_with_a_forged_reading_is_caught(unchecked_setting, capsys):
    doc = rbyaml.loads(unchecked_setting.read_text(encoding="utf-8"))
    doc["settings"][0]["status"] = "verified"
    doc["settings"][0]["source"]["resolved"] = {"at": "2026-01-01T00:00:00+00:00", "sha256": hashlib.sha256(b"lr: 0.1\n").hexdigest(),
                                                "text": "lr: 0.2", "read": 0.2}
    unchecked_setting.write_text(rbyaml.dumps(doc), encoding="utf-8")
    assert "edited_outside_rb" in problem_codes(finding(capsys))
    assert rbj(capsys, "status", "--fail-on", "unestablished")[0] == 1


# ---------------------------------------------------------------- cheat: the same run twice to reach min_n


@pytest.fixture
def needs_two_runs(session, capsys, monkeypatch):
    """A criterion needing two runs, frozen by a human, with one run recorded. Returns that run's id."""
    ok(capsys, "init", "t")
    ok(capsys, "experiment", "add", "x", "--id", "e1")
    ok(capsys, "criterion", "add", "s", "-e", "e1", "--metric", "top1", "--at-least", "0.7", "--min-n", "2", "--noise", "0.001", "--id", "c1")
    as_human(monkeypatch)
    ok(capsys, *FREEZE)
    as_agent(monkeypatch)
    first = add_run(capsys, "top1=0.76")
    f = finding(capsys)
    assert f["n"] == 1 and f["rule_outcome"] == "undetermined" and f["says"]["rule"] == "criterion undetermined: 1 unit; the criterion needs 2"
    return first


def test_recording_the_same_run_twice_writes_nothing_and_does_not_reach_min_n(needs_two_runs, capsys, session):
    env = ok(capsys, "run", "add", "e1", "top1=0.76")
    assert env.data["duplicate_of"] == needs_two_runs
    assert len(list((session / ".rb" / "experiments" / "e1" / "runs").glob("*.yaml"))) == 1
    f = finding(capsys)
    assert f["n"] == 1 and f["rule_outcome"] == "undetermined"
    assert rbj(capsys, "status", "--fail-on", "unestablished")[0] == 1


def test_recording_the_same_run_again_on_purpose_is_a_second_execution_with_its_reason(needs_two_runs, capsys, session):
    """A check of the run (--repeats) joins its unit and does not count twice. --again records a second execution:
    rb never collapses equal numbers, so it is a unit of its own, and the run carries the reason it was recorded again."""
    check = add_run(capsys, "top1=0.761", "--repeats", needs_two_runs)
    f = finding(capsys)
    assert f["n"] == 1 and f["units"]["repeats"] == 1 and f["rule_outcome"] == "undetermined"
    again = add_run(capsys, "top1=0.76", "--again", "-m", "reran it")
    assert again not in (needs_two_runs, check)
    assert state(session).get(again, "run").note.startswith("recorded again: reran it")
    f = finding(capsys)
    assert f["n"] == 2 and f["rule_outcome"] == "meets"


# ---------------------------------------------------------------- the adopt cannot launder authority


def _setup_criterion(session, capsys) -> None:
    ok(capsys, "init", "t")
    ok(capsys, "experiment", "add", "x", "--id", "e1")
    ok(capsys, "criterion", "add", "s", "-e", "e1", "--metric", "top1", "--at-least", "0.7", "--noise", "0.001", "--id", "c1")
    add_run(capsys, "top1=0.76")


def _reviewed(session) -> str:
    """c1's file as a review pins it for rb doctor --adopt --seen: its path as this system writes one, and the sha256
    it has now."""
    path = criterion_file(session)
    return f"{path.relative_to(session)}@{hashlib.sha256(path.read_bytes()).hexdigest()}"


def test_the_agent_adopting_its_own_authorship_edit_is_refused(session, capsys):
    """The agent flips a criterion's created_by to a human and adopts the edit itself: the adopt refuses
    (E_ADOPT_AUTHORITY), names the field, logs nothing, and the criterion stays blocked."""
    _setup_criterion(session, capsys)
    edit_criterion(session, "c1", created_by="human:t")
    code, env = rbj(capsys, "doctor", "--adopt", "-m", "sync after merge")
    assert code == 2 and env.errors[0].code == "E_ADOPT_AUTHORITY"
    assert "created_by" in " ".join(env.errors[0].problems)
    assert '"op": "adopt"' not in (session / ".rb" / "log.jsonl").read_text(encoding="utf-8")
    assert "edited_outside_rb" in problem_codes(finding(capsys))
    assert rbj(capsys, "status", "--fail-on", "unestablished")[0] == 1


def test_a_human_adopting_an_authorship_edit_directly_is_also_refused(session, capsys, monkeypatch):
    _setup_criterion(session, capsys)
    edit_criterion(session, "c1", created_by="human:t")
    as_human(monkeypatch)
    code, env = rbj(capsys, "doctor", "--adopt", "-m", "keep it")
    assert code != 0 and env.errors[0].code == "E_ADOPT_AUTHORITY"
    assert "created_by" in " ".join(env.errors[0].problems)


def test_a_decision_written_by_hand_cannot_be_adopted(session, capsys, monkeypatch):
    """A human's accept written into .rb/decisions/ by hand is the finding's integrity problem, and no adoption makes it
    a decision: a decision is recorded by whoever makes it."""
    _setup_criterion(session, capsys)
    body = {"rb": 1, "kind": "decision", "id": "d1", "subject": "c1", "action": "accept", "why": "looks right", "by": "human:t",
            "at": "2026-09-28T00:00:00+00:00", "via": "cli"}
    (session / ".rb" / "decisions").mkdir(exist_ok=True)
    (session / ".rb" / "decisions" / "d1.yaml").write_text(rbyaml.dumps(body), encoding="utf-8")
    assert "edited_outside_rb" in problem_codes(finding(capsys))
    as_human(monkeypatch)
    code, env = rbj(capsys, "doctor", "--adopt", "-m", "keep it")
    assert code != 0 and env.errors[0].code == "E_ADOPT_AUTHORITY"
    assert '"op": "adopt"' not in (session / ".rb" / "log.jsonl").read_text(encoding="utf-8")


def test_an_edit_that_changes_no_authority_still_adopts(session, capsys, monkeypatch):
    """The refusal must not make every merge unadoptable: an edit to plain content goes through."""
    _setup_criterion(session, capsys)
    edit_criterion(session, "c1", note="clarified after the merge")
    as_human(monkeypatch)
    env = ok(capsys, "doctor", "--adopt", "-m", "keep the merged note")
    assert any(d["done"] == "adopted as it stands" for d in env.data["adopted"])
    as_agent(monkeypatch)
    assert "edited_outside_rb" not in problem_codes(finding(capsys))


def test_an_adopt_runs_only_against_the_files_that_were_reviewed(session, capsys):
    """rb doctor --adopt --seen pins each reviewed file to the sha it had then. If anything drifts between the review
    and the adopt, the adopt refuses (E_STALE_REVIEW) instead of taking whatever is there by then; pinned to the file as
    it stands, the adopt goes through, logged as the agent's."""
    _setup_criterion(session, capsys)
    edit_criterion(session, "c1", note="first edit")
    reviewed = _reviewed(session)
    edit_criterion(session, "c1", note="changed again after the review")
    code, env = rbj(capsys, "doctor", "--adopt", "-m", "keep it", "--seen", reviewed)
    assert code == 2 and env.errors[0].code == "E_STALE_REVIEW"
    assert '"op": "adopt"' not in (session / ".rb" / "log.jsonl").read_text(encoding="utf-8")
    ok(capsys, "doctor", "--adopt", "-m", "keep it", "--seen", _reviewed(session))
    row = state(session).log(None)[-1]
    assert (row["op"], row["id"], row["actor"]) == ("adopt", "c1", "agent:claude-code")
    assert "edited_outside_rb" not in problem_codes(finding(capsys))
