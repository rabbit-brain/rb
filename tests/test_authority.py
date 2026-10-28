"""Who made each call, as the record keeps it, and what a call rests on.

rb tracks what agents and humans do in the work; it refuses no write for who makes it. An agent freezes an experiment
and amends it, records every kind of decision (accept, reject, investigate; continue, park, stop, pivot, ship), vouches
for a setting, retracts a run a criterion counts or a criterion evidence reads on, and adopts an edit made outside rb,
and each is recorded as the agent's: on the record, on its log.jsonl line, and in what status and the finding show. A
call the agent records is proposed, not made, until the user confirms it with one command in their own terminal.
What cannot be faked is who: a human's name typed inside an agent session is ignored and the agent recorded, the
user's words an agent acted on (--words) are kept verbatim beside the reason, a decision rb did not write is nobody's
call, and an adoption never changes who wrote something.

Then the readings those calls rest on: a repeat is averaged with the run it repeats, a history reading is counted in
the same units as the current one, a production time nobody knows keeps the order unknown (and fails the late gate),
and a call on a question moves when a reading under it appears that the call was not made on.

Every number here is synthetic.
"""
import subprocess
from pathlib import Path

import pytest

from rabbit_brain.models import Envelope
from rbtest import as_the_user, main, rbyaml, state

AGENT = "agent:claude-code"


def git(*args) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def session(workdir, monkeypatch, tmp_path_factory):
    """A git repository whose human is human:t, inside a Claude Code session that names no chat session."""
    as_the_user(monkeypatch)
    cfg = tmp_path_factory.mktemp("gitconfig") / "config"
    cfg.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(cfg))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    git("init", "-q")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    git("commit", "-q", "--allow-empty", "-m", "init")
    monkeypatch.setenv("CLAUDECODE", "1")
    return workdir


class RB:
    """rb in-process with --json: a plain call is the agent's; human=True is the human's, at their own terminal in no
    chat session, whatever the test set before (as_the_user)."""

    def __init__(self, root: Path, capsys, monkeypatch) -> None:
        self.root, self.capsys, self.mp = root, capsys, monkeypatch

    def __call__(self, *argv, human: bool = False) -> tuple[int, Envelope]:
        with self.mp.context() as m:
            if human:
                as_the_user(m)
            code = main([*argv, "--json"])
        return code, Envelope.model_validate_json(self.capsys.readouterr().out)

    def ok(self, *argv, **kw) -> Envelope:
        code, env = self(*argv, **kw)
        assert code in (0, 1) and env.ok, (argv, code, env.errors)
        return env

    def human(self, *argv) -> Envelope:
        return self.ok(*argv, human=True)

    def refused(self, error: str, *argv, **kw) -> Envelope:
        code, env = self(*argv, **kw)
        assert code == 2 and not env.ok and env.errors[0].code == error, (argv, code, env.errors)
        return env

    def text(self, *argv, human: bool = False) -> str:
        """What the agent reads when it runs rb without --json; human=True, what the human reads at their terminal."""
        with self.mp.context() as m:
            if human:
                as_the_user(m)
            code = main(list(argv))
        out = self.capsys.readouterr().out
        assert code == 0, out
        return out

    def log(self) -> list[dict]:
        return state(self.root).log(None)

    def logged(self) -> tuple[str, str, str]:
        """The newest log.jsonl line: its op, the record it wrote, and who wrote it."""
        last = self.log()[-1]
        return last["op"], last["id"], last["actor"]

    def status(self) -> dict:
        return self.ok("status").data

    def items(self, code: str) -> list[dict]:
        return [i for i in self.status()["open"] if i["code"] == code]

    def finding(self, criterion: str) -> dict:
        return self.ok("show", criterion).data["finding"]

    def gate(self, *gates: str) -> int:
        return self("status", "--fail-on", ",".join(gates))[0]


@pytest.fixture
def rb(session, capsys, monkeypatch) -> RB:
    r = RB(session, capsys, monkeypatch)
    r.human("init", "authority")
    r.ok("metric", "add", "steps", "--unit", "iters", "--minimize")
    r.human("question", "add", "does a wider layer pay?", "--id", "q1")
    r.ok("experiment", "add", "width screen", "--id", "e1", "-q", "q1", "--baseline", "base", "--candidate", "wide", "--varies", "width")
    return r


def gate_missed(rb: RB, *link: str) -> None:
    """A gate criterion the evidence misses: a priority call on the line of work, open in status. It is filed under q1
    through e1, with no question link unless `link` gives one (-q <question>)."""
    rb.ok("criterion", "add", "wide saves at least 20%", "-e", "e1", "--metric", "saving_pct", "--at-least", "20", "--role", "gate", "--id", "g20", *link)
    rb.ok("run", "add", "e1", "saving_pct=18", "--command", "solve a")
    assert [i["subject"] for i in rb.items("gate_missed")] == ["g20"]


def fewer_steps(rb: RB) -> str:
    """A primary criterion c1 and a run it counts. Returns the run's id."""
    rb.ok("criterion", "add", "wide needs fewer steps", "-e", "e1", "--metric", "change.steps", "--at-most", "0", "--id", "c1")
    return rb.ok("run", "add", "e1", "change.steps=-9", "--command", "solve").data["object"]["id"]


def problems(f: dict) -> set:
    return {(p["code"], p["subject"]) for p in f["integrity"]}


def edit(path: Path, change) -> None:
    """A hand edit of a record file, outside rb."""
    doc = rbyaml.loads(path.read_text(encoding="utf-8"))
    change(doc)
    path.write_text(rbyaml.dumps(doc), encoding="utf-8")


# ---------------------------------------------------------------- an agent's calls, recorded as the agent's


def test_an_agents_freeze_and_amendments_are_recorded_as_the_agents(rb):
    """The agent freezes e1, then changes it twice: a setting's value with --amend, and a setting taken back, which on
    a frozen experiment is an amendment too. Each is the agent's on the record and on its log line. A change without
    --amend is still E_FROZEN, and the spec has not drifted from its freeze record."""
    rb.ok("spec", "set", "e1", "iters", "24", "--source", "note:the config")
    rb.ok("spec", "set", "e1", "cache", "on", "--source", "note:the runner default")
    rb.ok("criterion", "add", "wide needs fewer steps", "-e", "e1", "--metric", "change.steps", "--at-most", "0", "--id", "c1")
    frozen = rb.ok("freeze", "e1", "-m", "before the screen").data["object"]["frozen"]
    assert (frozen["by"], frozen["why"]) == (AGENT, "before the screen") and rb.logged() == ("freeze", "e1", AGENT)
    rb.refused("E_FROZEN", "spec", "set", "e1", "iters", "32", "--source", "note:the config")
    rb.ok("spec", "set", "e1", "iters", "32", "--source", "note:the config", "--amend", "--why", "the config was fixed before any run")
    assert rb.logged() == ("spec_set", "e1", AGENT)
    rb.ok("retract", "e1/cache", "--why", "not a setting of this screen")
    assert rb.logged() == ("retract", "e1", AGENT)
    exp = rb.ok("show", "e1").data
    assert [(a["by"], a["change"], a["why"]) for a in exp["object"]["amendments"]] == [
        (AGENT, "setting iters: 24 -> 32", "the config was fixed before any run"),
        (AGENT, "setting cache retracted", "not a setting of this screen")]
    assert exp["spec_drift"] is None and rb.finding("c1")["declared"]["frozen_by"] == AGENT


@pytest.mark.parametrize("subject, action, state", [
    ("g20", "accept", None), ("g20", "reject", None), ("g20", "investigate", None),
    ("e1", "continue", "open"), ("e1", "park", "parked"), ("e1", "stop", "stopped"), ("e1", "pivot", "pivoted"), ("e1", "ship", "shipped"),
    ("q1", "stop", "stopped"),
])
def test_every_kind_of_decision_an_agent_makes_is_recorded_as_the_agents(rb, subject, action, state):
    """A ruling on a finding, and a call on a line of work (the experiment, or the question it is filed under): the
    decision is the agent's, and it is proposed, not made. Status lists it apart from the calls, the finding has no
    review, the line keeps its state and the missed gate still asks for a call. One command at the user's own terminal
    confirms it: the call is then theirs, a ruling is the finding's review, and a call on the line moves its state and
    settles the missed gate that asked for one."""
    gate_missed(rb)
    rows_of = lambda st: st["experiments"] if subject == "e1" else st["questions"]
    before = next((x["state"] for x in rows_of(rb.status()) if x["id"] == subject), None)
    d = rb.ok("decide", subject, action, "--why", "the saving is below the bar").data["object"]
    assert (d["by"], d["action"]) == (AGENT, action) and rb.logged() == ("decide", d["id"], AGENT)
    st = rb.status()
    assert not [x for x in st["decisions"] if x["id"] == d["id"]]
    [waiting] = [x for x in st["pending"] if x["id"] == d["id"]]
    assert waiting["by"] == AGENT and waiting["authority"]["state"] == "proposed" and waiting["confirm"] == f"rb confirm {d['id']}"
    assert [i["do"] for i in st["open"] if i["code"] == "call_proposed"] == [f"rb confirm {d['id']}"]
    if state is None:
        assert rb.finding("g20")["review"] is None
    else:
        assert next(x for x in rows_of(st) if x["id"] == subject)["state"] == before
    assert [i["subject"] for i in st["open"] if i["code"] == "gate_missed"] == ["g20"] and st["gate"]["undecided"]
    confirmed = rb.human("confirm", d["id"]).data["confirmed"]
    c = confirmed[0]["object"]
    assert (confirmed[0]["confirms"], c["by"], c["subject"], c["action"], c["why"]) == (d["id"], "human:t", subject, action, d["why"])
    st = rb.status()
    assert [x["by"] for x in st["decisions"] if x["id"] == c["id"]] == ["human:t"] and not st["pending"]
    if state is None:
        review = rb.finding("g20")["review"]
        assert (review["decision"], review["action"], review["by"], review["current"]) == (c["id"], action, "human:t", True)
    else:
        assert next(x for x in rows_of(st) if x["id"] == subject)["state"] == state
        assert not [i for i in st["open"] if i["code"] == "gate_missed"] and not st["gate"]["undecided"]


@pytest.mark.parametrize("link", [(), ("-q", "q1")], ids=["filed", "linked"])
@pytest.mark.parametrize("elsewhere", [False, True], ids=["no-links", "a-link-elsewhere"])
def test_a_call_on_a_question_above_settles_a_missed_gate(rb, link, elsewhere):
    """A missed gate is settled by a direction call on its experiment, on a question it answers, or on one above that,
    whether the call keeps the earlier shape (no link in the record) or the scoped one."""
    rb.human("question", "add", "is the wider layer worth it?", "--id", "q0")
    rb.ok("question", "move", "q1", "--parent", "q0")
    if elsewhere:
        rb.ok("criterion", "add", "wide needs fewer steps", "-e", "e1", "--metric", "change.steps", "--at-most", "0", "--id", "c1", "-q", "q1")
    gate_missed(rb, *link)
    rb.human("decide", "q0", "stop", "--why", "the saving is below the bar")
    assert not rb.items("gate_missed") and not rb.status()["gate"]["undecided"]


def test_a_call_on_the_question_an_unlinked_gate_is_filed_under_settles_it_on_that_reading(rb):
    """With question links in the record, the call keeps the reading of the missed gate it settles; a new reading of
    the gate asks for a call again, while the call itself stays current on its question."""
    rb.ok("criterion", "add", "wide needs fewer steps", "-e", "e1", "--metric", "change.steps", "--at-most", "0", "--id", "c1", "-q", "q1")
    gate_missed(rb)
    d = rb.human("decide", "q1", "park", "--why", "the saving is below the bar").data["object"]
    assert d["inquiry_basis"] is not None and d["basis"]["g20"]["rule"] == "misses"
    assert not rb.items("gate_missed")
    rb.ok("run", "add", "e1", "saving_pct=17", "--command", "solve b")
    assert [i["subject"] for i in rb.items("gate_missed")] == ["g20"]
    assert not rb.items("decision_outdated")


def test_a_question_call_says_which_missed_gate_it_settles_apart_from_what_it_was_made_on(rb):
    """With a question link elsewhere, a call on q1 has no criterion linked to it and settles the unlinked gate filed
    under it. decide and show say the gate is what it settles, and show marks it once the gate reads otherwise."""
    rb.human("question", "add", "is it cheap enough to serve?", "--id", "q2")
    rb.ok("criterion", "add", "serving stays cheap", "-e", "e1", "--metric", "wide.cost", "--at-most", "5", "--id", "c2", "-q", "q2")
    gate_missed(rb)
    said = rb.text("decide", "q1", "park", "--why", "the saving is below the bar", human=True)
    assert "  settles the missed gate g20: " in said and "made on g20" not in said
    assert "No criterion is linked directly to this question" in said
    [decision] = [d["id"] for d in rb.status()["decisions"] if d["subject"] == "q1"]
    [line] = [x for x in rb.text("show", decision).splitlines() if "g20" in x]
    assert line.startswith("  settles the missed gate g20: ") and "moved since" not in line
    rb.ok("run", "add", "e1", "saving_pct=17", "--command", "solve b")
    [line] = [x for x in rb.text("show", decision).splitlines() if "g20" in x]
    assert line.startswith("  settles the missed gate g20: ") and line.endswith("· moved since")


def test_a_question_shows_its_call_named_with_the_settled_gate_that_has_moved_as_status_asks_again(rb):
    """The call on q1 settles the missed gate g20 on its reading. Once g20 misses on another reading, rb status asks for
    a call on it again while the call itself stays current; rb show q1 names the gate beside the call, in its text and
    in its JSON (settled_gates_moved), and stops naming it once g20 meets, when rb status asks for nothing."""
    rb.human("question", "add", "is it cheap enough to serve?", "--id", "q2")
    rb.ok("criterion", "add", "serving stays cheap", "-e", "e1", "--metric", "wide.cost", "--at-most", "5", "--id", "c2", "-q", "q2")
    gate_missed(rb)
    decision = rb.human("decide", "q1", "park", "--why", "the saving is below the bar").data["object"]["id"]

    def call():
        [line] = [x for x in rb.text("show", "q1").splitlines() if x.startswith(f"  decision {decision}:")]
        [shown] = [d for d in rb.ok("show", "q1").data["decisions"] if d["id"] == decision]
        assert shown["current"] is True and "made on readings that have moved since" not in line
        return line, shown["settled_gates_moved"]

    assert not rb.items("gate_missed")
    line, moved = call()
    assert "moved since" not in line and moved == []
    rb.ok("run", "add", "e1", "saving_pct=17", "--command", "solve b")
    assert [i["subject"] for i in rb.items("gate_missed")] == ["g20"] and not rb.items("decision_outdated")
    line, moved = call()
    assert line.endswith(" · settled the missed gate g20 on a reading that has moved since") and moved == ["g20"]
    for n, saving in enumerate((90, 95)):
        rb.ok("run", "add", "e1", f"saving_pct={saving}", "--command", f"solve {n}")
    assert rb.finding("g20")["rule_outcome"] == "meets" and not rb.items("gate_missed")
    line, moved = call()
    assert "moved since" not in line and moved == []


def test_a_linked_gate_answers_the_question_it_is_linked_to_not_the_one_its_experiment_is_filed_under(rb):
    """g20 is linked to q0, above q1, where e1 is filed. A call on q1 leaves the miss open; one on q0 settles it."""
    rb.human("question", "add", "is the wider layer worth it?", "--id", "q0")
    rb.ok("question", "move", "q1", "--parent", "q0")
    gate_missed(rb, "-q", "q0")
    rb.human("decide", "q1", "park", "--why", "the saving is below the bar")
    assert [i["subject"] for i in rb.items("gate_missed")] == ["g20"]
    rb.human("decide", "q0", "park", "--why", "the saving is below the bar")
    assert not rb.items("gate_missed") and not rb.status()["gate"]["undecided"]


def test_a_gate_linked_to_another_question_is_settled_by_a_call_on_that_question(rb):
    """Linking the gate moves which calls settle it, whichever rb recorded the earlier call."""
    gate_missed(rb)
    rb.human("decide", "q1", "park", "--why", "the saving is below the bar")
    assert not rb.items("gate_missed")
    rb.human("question", "add", "is it cheap enough to serve?", "--id", "q2")
    rb.ok("criterion", "file", "g20", "q2", "--why", "the serving budget owns this policy")
    assert [i["subject"] for i in rb.items("gate_missed")] == ["g20"]
    rb.human("decide", "q2", "park", "--why", "not within the serving budget")
    assert not rb.items("gate_missed")


def test_an_agents_vouch_for_a_setting_is_recorded_as_the_agents(rb):
    """Accepting e1/iters vouches for the value it has: the decision is the agent's, and proposed, so nothing is vouched
    for until the user confirms it; then the note on the finding that reads it says it was vouched for, and in which
    decision, naming no writer."""
    rb.ok("spec", "set", "e1", "iters", "24")
    fewer_steps(rb)
    assert ("provisional", "e1/iters") in {(c["code"], c["subject"]) for c in rb.finding("c1")["caveats"]}
    d = rb.ok("decide", "e1/iters", "accept", "--why", "read it in the launch script").data["object"]
    assert (d["by"], d["value"]) == (AGENT, 24) and rb.logged() == ("decide", d["id"], AGENT)
    assert not [c for c in rb.finding("c1")["caveats"] if c["code"] == "vouched"]
    c = rb.human("confirm", d["id"]).data["confirmed"][0]["object"]
    assert (c["by"], c["value"]) == ("human:t", 24)
    [vouched] = [x for x in rb.finding("c1")["caveats"] if x["code"] == "vouched"]
    assert vouched["subject"] == "e1/iters" and vouched["text"] == f"iters = 24 is provisional · vouched for ({c['id']})"


def test_an_agents_retraction_of_a_run_a_criterion_counts_is_recorded_as_the_agents(rb):
    """The run stays on record, retracted by the agent with its reason; rb says the criterion counted it, and the
    finding no longer does."""
    run = fewer_steps(rb)
    env = rb.ok("retract", run, "--why", "the eval read the wrong split")
    assert env.data["rested_on"] == ["criterion c1 counts it"] and rb.logged() == ("retract", run, AGENT)
    kept = rb.ok("show", run).data["object"]["retracted"]
    assert (kept["by"], kept["why"]) == (AGENT, "the eval read the wrong split")
    f = rb.finding("c1")
    assert (f["rule_outcome"], f["n"], f["observations"]) == ("untested", 0, [])


@pytest.mark.parametrize("role", ["primary", "gate"])
def test_an_agents_retraction_of_a_criterion_evidence_reads_on_is_recorded_as_the_agents(rb, role):
    """What the work claims (a primary criterion), or a gate on spending, with a run already reading on it: the agent
    takes it back, rb says evidence read on it, and the criterion stays on record, retracted by the agent."""
    rb.ok("criterion", "add", "wide saves at least 20%", "-e", "e1", "--metric", "saving_pct", "--at-least", "20", "--role", role, "--id", "c1")
    rb.ok("run", "add", "e1", "saving_pct=18", "--command", "solve a")
    env = rb.ok("retract", "c1", "--why", "the bar was set after a look at the data")
    assert env.data["rested_on"] == [f"it is a {role} criterion and evidence reads on it"] and rb.logged() == ("retract", "c1", AGENT)
    assert rb.ok("show", "c1").data["object"]["retracted"]["by"] == AGENT
    assert "c1" not in [f["criterion"] for f in rb.status()["findings"]]


def test_an_agents_adoption_is_recorded_as_the_agents_and_never_changes_who_wrote_something(rb):
    """A title edited by hand is an integrity problem for the finding that reads the experiment; the agent adopts it as
    it stands, with its reason, on a log line of its own. An edit that makes the agent's criterion a human's is
    refused whoever asks (E_ADOPT_AUTHORITY), and rb doctor --restore puts it back."""
    fewer_steps(rb)
    edit(rb.root / ".rb" / "experiments" / "e1" / "experiment.yaml", lambda d: d.update(title="width screen on both photo sets"))
    assert ("edited_outside_rb", "e1") in problems(rb.finding("c1"))
    rb.ok("doctor", "--adopt", "--why", "the title names both photo sets now")
    last = rb.log()[-1]
    assert (last["op"], last["id"], last["actor"], last["detail"]["why"]) == ("adopt", "e1", AGENT, "the title names both photo sets now")
    assert rb.finding("c1")["integrity"] == []
    edit(rb.root / ".rb" / "experiments" / "e1" / "criteria" / "c1.yaml", lambda d: d.update(created_by="human:t"))
    env = rb.refused("E_ADOPT_AUTHORITY", "doctor", "--adopt", "--why", "the user wrote it")
    assert any("c1.created_by" in p for p in env.errors[0].problems)
    rb.ok("doctor", "--restore")
    f = rb.finding("c1")
    assert f["declared"]["by"] == AGENT and f["integrity"] == []


# ---------------------------------------------------------------- what cannot be faked


def test_the_users_words_are_kept_verbatim_beside_the_reason_and_on_the_log_line(rb, monkeypatch):
    """What the user wrote in the chat, passed with --words, is kept as written: on the criterion (so on its finding)
    and on the decision, beside the reason. rb decide prints it after the reason, and each write's log line carries it
    with the chat session the runtime names. A write given no words carries none."""
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "chat-0001")
    rb.ok("criterion", "add", "wide saves at least 20%", "-e", "e1", "--metric", "saving_pct", "--at-least", "20", "--role", "gate",
          "--id", "g20", "--words", "only worth it at 20% or more")
    assert rb.finding("g20")["declared"]["words"] == "only worth it at 20% or more"
    rb.ok("run", "add", "e1", "saving_pct=18", "--command", "solve a")
    last = rb.log()[-1]
    assert last["session"] == "chat-0001" and "words" not in last
    said = 'Park it: 18% is not worth a "selection" screen.'
    out = rb.text("decide", "e1", "park", "--why", "the saving is below the bar", "--words", said)
    # given in an agent session, they are the words given with the call, never the user's, until the user confirms it
    assert f'proposed, not made: park e1 · note: the saving is below the bar · words (as given): "{said}"' in out and AGENT not in out
    assert "your words" not in out
    [d] = [d for d in state(rb.root).records("decision", live=True) if d.subject == "e1"]
    assert (d.by, d.why, d.words, d.session) == (AGENT, "the saving is below the bar", said, "chat-0001")
    last = rb.log()[-1]
    assert (last["op"], last["actor"], last["words"], last["session"]) == ("decide", AGENT, said, "chat-0001")
    out = rb.text("confirm", d.id, human=True)
    assert f'parked e1 · note: the saving is below the bar · your words: "{said}"   (confirms {d.id})' in out


def test_a_humans_name_typed_inside_an_agent_session_is_recorded_as_the_agent(rb, monkeypatch):
    """RB_ACTOR=human:t inside the session is what an agent would type: the freeze and the ruling go through as the
    agent's, the finding shows the agent's hand, and the name is kept on each log line only as ignored."""
    fewer_steps(rb)
    monkeypatch.setenv("RB_ACTOR", "human:t")
    env = rb.ok("freeze", "e1", "-m", "before the next screen")
    assert env.data["actor"] == {"id": AGENT, "via": "detected:CLAUDECODE", "ignored_rb_actor": "human:t",
                                 "agent": {"name": "claude-code", "provider": "anthropic", "provider_from": "inferred"}}
    assert env.data["object"]["frozen"]["by"] == AGENT
    d = rb.ok("decide", "c1", "accept", "--why", "the saving holds").data["object"]
    f = rb.finding("c1")
    assert d["by"] == f["declared"]["frozen_by"] == AGENT and f["review"] is None      # the agent's call waits for the user
    typed = [(e["op"], e["actor"]) for e in rb.log() if e.get("ignored_rb_actor") == "human:t"]
    assert typed == [("freeze", AGENT), ("decide", AGENT)]


def test_a_decision_rb_did_not_write_is_nobodys_call(rb):
    """A decision file written into .rb/ by hand, in a human's name: the finding it would review shows no review and
    lists the file as an integrity problem, status flags it for anyone, and it cannot be adopted into the record
    (E_ADOPT_AUTHORITY). rb doctor --restore sets it aside."""
    fewer_steps(rb)
    path = rb.root / ".rb" / "decisions" / "dhand.yaml"
    path.parent.mkdir(exist_ok=True)
    path.write_text(rbyaml.dumps({"rb": 1, "kind": "decision", "id": "dhand", "subject": "c1", "action": "accept", "why": "it holds",
                                  "by": "human:t", "at": "2026-01-01T00:00:00.000+00:00", "via": "cli"}), encoding="utf-8")
    f = rb.finding("c1")
    assert f["review"] is None and ("edited_outside_rb", "dhand") in problems(f)
    assert [(i["who"], i["subject"]) for i in rb.items("edited_outside_rb")] == [("anyone", "dhand")]
    rb.refused("E_ADOPT_AUTHORITY", "doctor", "--adopt", "--why", "the user said so")
    rb.ok("doctor", "--restore")
    assert not path.exists() and rb.finding("c1")["integrity"] == []


def test_publish_is_not_rbs_and_says_what_to_use(rb):
    env = rb.refused("E_NOT_AVAILABLE", "publish")
    assert "rb export" in env.errors[0].fix


# ---------------------------------------------------------------- readings


def test_a_repeat_is_averaged_with_the_run_it_repeats_not_counted_as_another_case(rb):
    """Two noise cases of one photo read -14 and -10: the photo's value is -12. A repeat of the first case that reads
    -14 again checks it; it does not make the photo -12.67."""
    rb.ok("spec", "set", "e1", "photo", "--per-run")
    rb.ok("spec", "set", "e1", "noise", "--per-run")
    rb.ok("spec", "set", "e1", "population.unit", "photo")
    rb.ok("criterion", "add", "wide needs fewer steps", "-e", "e1", "--metric", "change.steps", "--at-most", "0", "--id", "c1")
    first = rb.ok("run", "add", "e1", "change.steps=-14", "--set", "photo=a", "--set", "noise=0", "--command", "solve a 0").data["object"]["id"]
    rb.ok("run", "add", "e1", "change.steps=-10", "--set", "photo=a", "--set", "noise=1", "--command", "solve a 1")
    assert rb.finding("c1")["comparison"]["estimate"] == pytest.approx(-12)
    rb.ok("run", "add", "e1", "change.steps=-14", "--set", "photo=a", "--set", "noise=0", "--repeats", first,
          "--command", "solve a 0 --cycle-free")
    f = rb.finding("c1")
    assert f["comparison"]["estimate"] == pytest.approx(-12) and f["units"]["n"] == 1 and f["units"]["repeats"] == 1


def test_a_reading_under_an_earlier_spec_is_counted_in_the_same_units(rb):
    """Three seeds, two halves each: the current reading has 3 units, and so does the one it replaced."""
    rb.ok("spec", "set", "e1", "seed", "--per-run")
    rb.ok("spec", "set", "e1", "half", "--per-run")
    rb.ok("spec", "set", "e1", "population.unit", "seed")
    rb.ok("spec", "set", "e1", "iters", "24", "--source", "note:the config")
    rb.ok("criterion", "add", "wide needs fewer steps", "-e", "e1", "--metric", "change.steps", "--at-most", "0", "--id", "c1")
    for seed, (a, b) in enumerate(((-3, -5), (-4, -4), (-6, -2))):
        for half, v in ((0, a), (1, b)):
            rb.ok("run", "add", "e1", f"change.steps={v}", "--set", f"seed={seed}", "--set", f"half={half}", "--command", f"solve {seed} {half}")
    before = rb.finding("c1")["comparison"]
    rb.ok("spec", "set", "e1", "iters", "32", "--source", "note:the config")
    [old] = rb.finding("c1")["history"]
    assert old["n"] == before["n"] == 3 and old["unit"] == before["unit"] == "seed" and old["estimate"] == pytest.approx(before["estimate"])


def test_an_analysis_whose_production_time_nobody_knows_leaves_the_order_unknown(rb):
    rb.ok("criterion", "add", "wide needs fewer steps", "-e", "e1", "--metric", "steps", "--subject", "wide", "--reference", "base",
          "--better", "--id", "c1")
    rb.ok("analysis", "add", "e1", "--compare", "wide/base:steps=-9[-12,-6]", "--method", "paired bootstrap", "--unit", "photo",
          "--units", "12", "--produced-at", "unknown", "--id", "a1")
    f = rb.finding("c1")
    assert f["timing"] == "unknown" and "unknown" in f["says"]["timing"] and "production times" not in f["says"]["timing"]
    assert [i["subject"] for i in rb.items("timing_unknown")] == ["c1"]
    assert rb.gate("late") == 1 and rb.gate("unestablished") == 1


def test_data_seen_at_a_time_nobody_recorded_leaves_the_order_unknown(rb):
    rb.ok("resource", "add", "photos", "--type", "dataset", "--name", "two images")
    rb.human("resource", "expose", "photos", "inspected", "--at", "unknown", "--why", "we looked at them while debugging")
    rb.ok("criterion", "add", "wide needs fewer steps", "-e", "e1", "--metric", "change.steps", "--at-most", "0", "--id", "c1")
    rb.ok("run", "add", "e1", "change.steps=-9", "--input", "photos:eval", "--command", "solve")
    f = rb.finding("c1")
    assert f["timing"] == "unknown" and "photos" in f["says"]["timing"]


@pytest.mark.parametrize("link", [(), ("-q", "q1")], ids=["filed", "linked"])
def test_a_call_on_a_question_moves_when_a_reading_appears_under_it(rb, link):
    """A human dropped q1 on e1's reading. A new experiment under q1 gets evidence the call was not made on: filed under
    q1 in a record with no question links, or linked to q1."""
    rb.ok("criterion", "add", "wide needs fewer steps", "-e", "e1", "--metric", "change.steps", "--at-most", "-20", "--id", "c1", *link)
    rb.ok("run", "add", "e1", "change.steps=-9", "--command", "solve")
    rb.human("decide", "q1", "reject", "--why", "the saving is too small")
    assert not [i for i in rb.items("decision_outdated") if i["subject"] == "q1"]
    rb.ok("experiment", "add", "on motion blur", "--id", "e2", "-q", "q1", "--baseline", "base", "--candidate", "wide")
    rb.ok("criterion", "add", "on motion blur wide needs fewer steps", "-e", "e2", "--metric", "change.steps", "--at-most", "-5", "--id", "c2", *link)
    rb.ok("run", "add", "e2", "change.steps=-11", "--command", "solve blur")
    [it] = [i for i in rb.items("decision_outdated") if i["subject"] == "q1"]
    assert "c2" in it["what"] and "(rejected q1) was made on readings that have moved since" in it["what"]
    assert it["do"].startswith("rb decide q1 reject --why")                 # the command keeps the action as typed


def test_a_repeat_with_the_same_numbers_is_recorded_as_the_check_it_is(rb):
    """rb suggests --repeats for a second execution that checks the first; one that lands on the same numbers is
    exactly the check that holds, and it is recorded, not swallowed as a duplicate of the recording."""
    rb.ok("criterion", "add", "wide needs fewer steps", "-e", "e1", "--metric", "change.steps", "--at-most", "0", "--id", "c1")
    first = rb.ok("run", "add", "e1", "change.steps=-9", "--command", "solve").data["object"]["id"]
    again = rb.ok("run", "add", "e1", "change.steps=-9", "--command", "solve")
    assert again.data["duplicate_of"] == first
    rep = rb.ok("run", "add", "e1", "change.steps=-9", "--command", "solve", "--repeats", first).data
    assert rep.get("duplicate_of") is None and rep["object"]["repeats"] == first
    f = rb.finding("c1")
    assert f["units"]["n"] == 1 and f["units"]["repeats"] == 1
    assert any(c["code"] == "repeat" and "a check that holds" in c["text"] for c in f["caveats"])


def test_rb_help_lists_every_group_of_the_record(rb, capsys):
    main(["--help"])
    text = capsys.readouterr().out
    assert "\n  analysis " in text and "\n  decide " in text
    assert "\n  project " in text
    assert "\nLeaving:\n  remove " in text                     # the way out is listed with the way in
    assert not [gone for gone in ("grant", "approve", "publish") if f"\n  {gone} " in text]
