"""A call is the user's: whose call a decision is, worked out on every read from what the record keeps.

Promises pinned here:

- A call recorded in an agent session (rb decide, the SDK's decide, MCP's decide) is proposed, not made. It changes no
  state: questions, experiments, criteria and settings read as if it were not there. It is never listed as a call or
  as a call waiting on the user, and rb status, rb context and rb show list it apart, with its subject, action, reason
  as given and the one command that confirms it, and so does a page, never under Your call.
- One action confirms it, outside an agent session only: rb confirm <id>, or rb confirm --all after it says each call.
  The confirmation is an ordinary decision by whoever confirms it, on the same subject with the same action, reason and
  words, its log line naming what it confirms. In an agent session rb confirm is refused, and so it is when RB_ACTOR
  names an agent: a confirmation an agent passes establishes nothing.
- A call made outside an agent session needs no confirmation.
- A past call (--past, --when) recorded in an agent session is a past call mentioned, not confirmed, apart from a
  proposed call, whoever began the record and whatever document it cites: the session that reconstructs the past
  cannot attest it. It waits for one rb confirm. What git shows of its document is a fact rb says beside it: the quote
  is in that file and line, committed on a day before the record's first write; or why rb cannot tell (an untracked
  file, a quote first committed after the record began, a record that began in the session that cites the file).
  Until it is confirmed it reads in the file's voice, its words with file and line, and the session's reason as a note.
- A past call recorded outside an agent session is the user's, as any call they make.
- Words given in an agent session never read as the user's (no `your words`, nothing that says they are theirs), on any
  surface, until the user confirms the call they came with.
- The JSON is additive, and an ordinary call made outside an agent session, or a confirmation, leaves the record as an
  older rb reads it.

Every name, number and session id here is made up.
"""
import html as html_mod
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
import warnings
from pathlib import Path

import pytest

import rabbit_brain
from rabbit_brain import RBWarning
from rbtest import AGENT_VARS, Mcp, Open, command, environment

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="a past call's document is dated by git")

AGENT = {"CLAUDECODE": "1", "AI_AGENT": "claude-code_2-1-295_agent", "CLAUDE_CODE_SESSION_ID": "sess-calls-5e2a"}
OTHER = {**AGENT, "CLAUDE_CODE_SESSION_ID": "sess-calls-77b1"}         # another agent session, later
MAY = "2026-05-07T12:00:00+00:00"                                     # when the old document was committed
LATER = "2099-01-01T00:00:00+00:00"                                   # a commit made after the record began
# the keys a decision's file had before a call could be past: a confirmation is an ordinary call
ORDINARY_CALL = ("id", "subject", "action", "why", "revisit", "by", "at", "via", "session", "words", "basis", "inquiry_basis", "value", "across",
                 "retracted")
CALLS = "# Calls\n\nWe drop the width question: the sweep is done.\nWe pause INT8 for now.\n"


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
        self.git("init", "-q")
        self.git("config", "user.email", "ana@example.org")
        self.git("config", "user.name", "ana")

    def git(self, *args: str, when: str = "") -> str:
        env = {**self.env, **({"GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when} if when else {})}
        return subprocess.run(["git", *args], cwd=self.root, env=env, check=True, capture_output=True, text=True).stdout

    def commit(self, path: str, text: str, when: str = "") -> None:
        p = self.root / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        self.git("add", path)
        self.git("commit", "-qm", f"write {path}", when=when)

    def run(self, *argv: str, agent: dict | None = None, extra: dict | None = None) -> subprocess.CompletedProcess:
        return subprocess.run([*command(), *argv], cwd=self.root, env={**self.env, **(agent or {}), **(extra or {})}, capture_output=True,
                              stdin=subprocess.DEVNULL, encoding="utf-8", errors="replace")

    def rb(self, *argv: str, agent: dict | None = None, extra: dict | None = None) -> tuple[int, dict]:
        r = self.run(*argv, "--json", agent=agent, extra=extra)
        return r.returncode, json.loads(r.stdout)

    def ok(self, *argv: str, agent: dict | None = None) -> dict:
        code, env = self.rb(*argv, agent=agent)
        assert code in (0, 1) and env["ok"], (argv, code, env["errors"])
        return env

    def text(self, *argv: str, agent: dict | None = None) -> str:
        r = self.run(*argv, agent=agent)
        assert r.returncode in (0, 1), (argv, r.returncode, r.stdout, r.stderr)
        return r.stdout

    def status(self) -> dict:
        return self.ok("status")["data"]

    def log(self) -> list:
        return [json.loads(line) for line in (self.root / ".rb" / "log.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]

    def decide(self, *argv: str, agent: dict | None = AGENT) -> dict:
        """rb decide <argv>, in an agent session unless agent=None: the decision, with its authority."""
        data = self.ok("decide", *argv, agent=agent)["data"]
        return {**data["object"], "authority": data["authority"]}


def started(rec: Rec, by: dict | None = None, linked: bool = True) -> Rec:
    """The work: a document committed in May that states a call, then the record, begun by the user unless `by` says
    otherwise, with two questions, an experiment with a criterion read by one run (linked to q1 unless `linked` is
    false), and a setting nobody checked."""
    rec.commit("docs/calls.md", CALLS, when=MAY)
    rec.ok("init", "INT8 ResNet-50", "--no-agents", agent=by)
    rec.ok("question", "add", "Does INT8 cost accuracy?", "--id", "q1")
    rec.ok("question", "add", "Does width matter?", "--id", "q-width")
    rec.ok("metric", "add", "val_loss", "--unit", "nats", "--minimize")
    rec.ok("experiment", "add", "INT8 vs FP32", "--id", "int8", "-q", "q1", "--baseline", "fp32", "--candidate", "int8")
    rec.ok("criterion", "add", "INT8 adds at most 0.05 validation loss", "-e", "int8", *(["-q", "q1"] if linked else []), "--metric",
           "change.val_loss", "--at-most", "0.05", "--id", "c1")
    rec.ok("run", "add", "int8", "fp32.val_loss=5.61", "int8.val_loss=5.64", "--command", "python eval.py")
    rec.ok("spec", "set", "int8", "cuda", "12.1", "--no-verify")
    return rec


@pytest.fixture
def rec(tmp_path, tmp_path_factory) -> Rec:
    return started(Rec(tmp_path / "work", tmp_path_factory.mktemp("home")))


def at_the_users_terminal(rec: Rec, monkeypatch) -> None:
    """This process as the user at their own terminal in the record (for rb open, the SDK and the screen's views)."""
    monkeypatch.chdir(rec.root)
    for k, v in rec.env.items():
        monkeypatch.setenv(k, v)
    for k in AGENT_VARS:
        monkeypatch.delenv(k, raising=False)


def states(st: dict) -> dict:
    """What a call can change, as rb status reads it: each question's and experiment's state, each finding's review,
    each setting vouched for, and the gates."""
    return {
        "questions": {q["id"]: q["state"] for q in st["questions"]},
        "experiments": {e["id"]: e["state"] for e in st["experiments"]},
        "reviews": {f["criterion"]: f["review"] for f in st["findings"]},
        "vouched": sorted(c["subject"] for f in st["findings"] for c in f["caveats"] if c["code"] == "vouched"),
        "gate": st["gate"],
    }


# ---------------------------------------------------------------- proposed, not made


def test_a_call_recorded_in_an_agent_session_is_proposed_and_changes_nothing(rec, tmp_path):
    """Four calls from an agent session (a question dropped, an experiment parked, a ruling, a vouch) are the agent's
    and change nothing; rb status lists each apart, with its command, and none as a call; rb show says them on their
    subject; a page says none of them."""
    before = states(rec.status())
    calls = [rec.decide("q-width", "reject", "--why", "the sweep is done"), rec.decide("int8", "park", "--why", "later"),
             rec.decide("c1", "accept", "--why", "within budget"), rec.decide("int8/cuda", "accept", "--why", "read off the image")]
    assert {(d["by"], d["authority"]["state"], d["authority"]["pending"]) for d in calls} == {("agent:claude-code", "proposed", True)}
    st = rec.status()
    assert states(st) == before
    assert st["decisions"] == [] and st["counts"]["decision_count"] == 0 and st["counts"]["pending_count"] == 4
    assert [(p["id"], p["confirm"]) for p in st["pending"]] == [(d["id"], f"rb confirm {d['id']}") for d in calls]
    proposed = [i for i in st["open"] if i["code"] == "call_proposed"]
    assert [(i["who"], i["subject"], i["do"], i["decision"]) for i in proposed] == [("human", d["subject"], f"rb confirm {d['id']}", d["id"]) for d in calls]
    assert not [i for i in st["open"] if i["code"] in ("undecided", "decision_outdated") and i["subject"] in ("q-width", "int8")]
    text = rec.text("status")
    every = "rb confirm " + " ".join(d["id"] for d in calls)
    assert "Proposed, not made (4)" in text and f"Confirm all 4: {every}" in text
    assert f"  - reject q-width · note: the sweep is done · confirm: rb confirm {calls[0]['id']}" in text
    assert "call: " not in text and "Needs judgment" not in text
    assert f"  {calls[0]['id']}: proposed, not made: reject q-width · note: the sweep is done · confirm: rb confirm {calls[0]['id']}" in rec.text("show", "q-width")
    context = rec.text("context")
    assert "## Proposed, not made" in context and f"- park int8 · note: later · confirm: rb confirm {calls[1]['id']}" in context
    assert "## Decisions" not in context
    rec.ok("page", "--out", str(tmp_path / "page.html"))
    page = html_mod.unescape((tmp_path / "page.html").read_text(encoding="utf-8"))
    # the page lists them as rb status does, apart from the calls waiting on the user, each with the one command
    said = text_of(page, "")
    # the page names Your call, as its own view, holding none of them
    assert "Proposed, not made (4)" in said and f"Confirm all 4{every}" in said and "Your call" not in no_calls_said(said)
    assert all(f"confirm in your own terminal:rb confirm {d['id']}" in said for d in calls) and "reject q-width · note: the sweep is done" in said


def test_a_call_from_the_sdk_or_over_mcp_in_an_agent_session_is_proposed_too(rec, monkeypatch):
    """The SDK's decide in an agent session (or rb.open(agent=...)) and MCP's decide record a proposed call, say so (an
    RBWarning, data.warnings), and change nothing."""
    before = states(rec.status())
    at_the_users_terminal(rec, monkeypatch)
    with warnings.catch_warnings(record=True) as said:
        warnings.simplefilter("always")
        did = rabbit_brain.open(agent="notebook-agent").decide("q-width", "reject", why="the sweep is done")
    assert [str(w.message) for w in said if issubclass(w.category, RBWarning)] == [
        f"{did} is proposed, not made: it changes nothing until it is confirmed (rb confirm {did}, in your own terminal)."]
    # rb show says where to confirm it as rb open does, with no word on who wrote it
    shown = rec.text("show", did)
    assert f"  It changes nothing until it is confirmed: rb confirm {did}, in your own terminal." in shown
    assert "agent session" not in shown
    server = Mcp(root=rec.root, env={**rec.env, "RB_SESSION": "sess-calls-mcp-1"})
    try:
        server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "clientInfo": {"name": "calls-client"}}})
        answer = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                                "params": {"name": "decide", "arguments": {"subject": "int8", "action": "park", "why": "later"}}})["result"]
    finally:
        server.close()
    data = answer["structuredContent"]["data"]
    assert data["authority"]["state"] == "proposed" and "is proposed, not made" in data["warnings"][0]
    st = rec.status()
    assert states(st) == before and [p["id"] for p in st["pending"]] == [did, data["object"]["id"]]


# ---------------------------------------------------------------- one action confirms


def test_one_command_in_the_users_terminal_confirms_and_an_agent_session_cannot(rec):
    """rb confirm <id> makes the call: the same subject, action, reason and words, recorded as the user's, its log line
    naming what it confirms, and nothing new in the record's format. In an agent session, or where RB_ACTOR names an
    agent, it is refused and writes nothing."""
    d = rec.decide("q-width", "reject", "--why", "the sweep is done", "--words", "drop the width question")
    lines = len(rec.log())
    code, env = rec.rb("confirm", d["id"], agent=AGENT)
    assert code == 2 and env["errors"][0]["code"] == "E_USAGE" and "agent session" in env["errors"][0]["message"]
    code, env = rec.rb("confirm", d["id"], extra={"RB_ACTOR": "agent:bot"})
    assert code == 2 and env["errors"][0]["code"] == "E_USAGE"
    assert len(rec.log()) == lines and rec.status()["questions"][1]["state"] == "open"
    confirmed = rec.ok("confirm", d["id"])["data"]["confirmed"]
    made = confirmed[0]["object"]
    assert confirmed[0]["confirms"] == d["id"] and confirmed[0]["authority"] == {"state": "confirmed", "basis": f"confirms {d['id']}", "pending": False,
                                                                              "confirms": d["id"]}
    assert (made["by"], made["subject"], made["action"], made["why"], made["words"]) == ("human:ana", "q-width", "reject", "the sweep is done",
                                                                                         "drop the width question")
    st = rec.status()
    assert {q["id"]: q["state"] for q in st["questions"]}["q-width"] == "dropped" and st["pending"] == []
    assert [x["id"] for x in st["decisions"]] == [made["id"]]
    last = rec.log()[-1]
    assert (last["op"], last["id"], last["actor"], last["detail"]["confirms"]) == ("decide", made["id"], "human:ana", d["id"])
    kept = (rec.root / ".rb" / "decisions" / f"{made['id']}.yaml").read_text(encoding="utf-8")
    keys = {line.split(":")[0] for line in kept.splitlines() if line and not line.startswith(" ")}
    assert {"by", "words"} <= keys <= {"rb", "kind", *ORDINARY_CALL}
    code, env = rec.rb("confirm", d["id"])
    assert code == 2 and env["errors"][0]["message"] == f"{d['id']} was confirmed already, as {made['id']}."
    shown = rec.text("show", d["id"])
    assert shown.startswith(f"Decision {d['id']}: proposed: reject q-width · note: the sweep is done") and f"  confirmed as {made['id']}" in shown


def test_rb_confirm_lists_what_waits_and_all_says_each_call_then_confirms_them(rec):
    a = rec.decide("q-width", "reject", "--why", "the sweep is done")
    b = rec.decide("int8", "park", "--why", "later", "--past", "--when", "May 7")
    lines = len(rec.log())
    listed = rec.text("confirm")
    assert listed == (f"Waiting for a confirmation (2):\n  {a['id']}  proposed, not made: reject q-width · note: the sweep is done\n"
                      f"  {b['id']}  a past call mentioned, not confirmed: park int8 (May 7) · note: later\n"
                      f"rb confirm <id> confirms one; rb confirm {a['id']} {b['id']} confirms these 2 as listed.\n")
    previewed = rec.text("confirm", "--all", "--dry-run")
    assert previewed.startswith("Dry run: nothing was written.") and len(rec.log()) == lines
    said = rec.text("confirm", "--all")
    assert said.startswith("Confirming 2 calls:\n") and said.index(f"  {a['id']}  proposed") < said.index("(confirms ")
    st = rec.status()
    assert st["pending"] == [] and {q["id"]: q["state"] for q in st["questions"]}["q-width"] == "dropped"
    assert {e["id"]: e["state"] for e in st["experiments"]}["int8"] == "parked"
    assert rec.text("confirm") == "Nothing waits for a confirmation.\n"


def test_a_vouch_is_confirmed_only_for_the_value_it_was_proposed_on(rec):
    """A vouch proposed for cuda = 12.1 cannot be confirmed once cuda reads 12.2: confirming it would vouch for a
    value nobody proposed. rb says so and writes nothing; the vouch for the value it has now is a call of its own."""
    d = rec.decide("int8/cuda", "accept", "--why", "read off the image")
    rec.ok("spec", "set", "int8", "cuda", "12.2", "--no-verify")
    lines = len(rec.log())
    code, env = rec.rb("confirm", d["id"])
    assert code == 2 and env["errors"][0]["message"] == f"{d['id']} was proposed on the value int8/cuda had then, which int8/cuda no longer has."
    assert len(rec.log()) == lines and not [c for f in rec.status()["findings"] for c in f["caveats"] if c["code"] == "vouched"]


def test_a_call_made_outside_an_agent_session_needs_no_confirmation(rec):
    d = rec.decide("q-width", "reject", "--why", "the sweep is done", "--words", "drop it", agent=None)
    assert d["authority"] == {"state": "direct", "basis": "recorded outside an agent session", "pending": False}
    st = rec.status()
    assert {q["id"]: q["state"] for q in st["questions"]}["q-width"] == "dropped" and st["pending"] == []
    assert 'call: rejected: "Does width matter?" (the sweep is done) · your words: "drop it"' in rec.text("status")


def test_a_proposal_waits_no_more_once_a_call_is_made_on_its_subject(rec):
    """The user answered it another way: the proposal stays on record, answered, and is no longer listed."""
    d = rec.decide("int8", "park", "--why", "later")
    made = rec.decide("int8", "continue", "--why", "one more seed first", agent=None)
    st = rec.status()
    assert st["pending"] == [] and {e["id"]: e["state"] for e in st["experiments"]}["int8"] == "open"
    shown = rec.ok("show", d["id"])["data"]["authority"]
    assert (shown["state"], shown["pending"], shown["answered_by"]) == ("proposed", False, made["id"])


# ---------------------------------------------------------------- past calls


def test_a_past_call_mentioned_with_no_document_waits_and_confirmed_reads_retrospective(rec):
    before = states(rec.status())
    d = rec.decide("int8", "park", "--why", "too slow to iterate", "--past", "--when", "May 7")
    assert (d["past"], d["authority"]["state"], d["authority"]["basis"]) == ({"when": "May 7", "source": None}, "past_unconfirmed", "given with no document")
    st = rec.status()
    assert states(st) == before and [i["code"] for i in st["open"] if i["code"].endswith("unconfirmed")] == ["past_call_unconfirmed"]
    text = rec.text("status")
    assert f"Past calls mentioned, not confirmed (1)\n  - park int8 (May 7) · note: too slow to iterate · confirm: rb confirm {d['id']}" in text
    assert "Proposed, not made" not in text
    made = rec.ok("confirm", d["id"])["data"]["confirmed"][0]["object"]
    assert made["past"] == {"when": "May 7", "source": None}
    assert {e["id"]: e["state"] for e in rec.status()["experiments"]}["int8"] == "parked"
    assert 'call: parked May 7 (a past call, Retrospective): "INT8 vs FP32" · note: too slow to iterate' in rec.text("status")
    unknown = rec.decide("q-width", "reject", "--why", "dropped long ago", "--past")
    assert unknown["past"]["when"] == "unknown" and "reject q-width (date unknown) · note: dropped long ago" in rec.text("status")


def test_a_past_call_a_session_quotes_from_a_document_committed_before_the_record_began_waits_for_one_confirm(rec):
    """Oct 11 evening: the importing actor cannot attest its own reconstructed past. A past call a session gives from a
    document committed in May, before the user began the record, changes nothing until one rb confirm; rb says what git
    shows of the document beside it, and once confirmed the call reads as a past call from that file and line."""
    d = rec.decide("q-width", "reject", "--why", "the sweep is done", "--past", "--source", "docs/calls.md:3", "--quote", "We drop the width question")
    commit = rec.git("log", "--format=%H", "--", "docs/calls.md").strip()
    found = f"the quote is in docs/calls.md:3, committed 2026-05-07 ({commit[:12]}), before the record began"
    assert d["past"]["source"] == {"path": "docs/calls.md", "line": 3, "quote": "We drop the width question", "commit": commit}
    assert d["authority"] == {"state": "past_unconfirmed", "pending": True, "basis": found}
    st = rec.status()
    assert {q["id"]: q["state"] for q in st["questions"]}["q-width"] == "open" and st["decisions"] == []
    assert [p["id"] for p in st["pending"]] == [d["id"]]
    line = f'reject q-width (date unknown): docs/calls.md:3: "We drop the width question" · note: the sweep is done · {found}'
    assert f"Past calls mentioned, not confirmed (1)\n  - {line} · confirm: rb confirm {d['id']}" in rec.text("status")
    shown = rec.text("show", d["id"])
    assert shown.startswith(f"Decision {d['id']}: a past call mentioned, not confirmed: reject q-width (date unknown): docs/calls.md:3: ")
    assert f"  {found}\n" in shown and "It changes nothing until it is confirmed" in shown
    assert f"  {d['id']}  a past call mentioned, not confirmed: {line.replace(' · ' + found, '')} · {found}" in rec.text("confirm")
    rec.ok("confirm", d["id"])
    st = rec.status()
    assert {q["id"]: q["state"] for q in st["questions"]}["q-width"] == "dropped" and st["pending"] == []
    assert ('call: rejected (a past call from docs/calls.md:3, "We drop the width question", date unknown, Retrospective): "Does width matter?" '
            '· note: the sweep is done') in rec.text("status")
    # the same past call given at the user's own terminal is theirs, as any call they make
    mine = rec.decide("q1", "investigate", "--why", "one more look", "--past", "--source", "docs/calls.md:4", "--quote", "We pause INT8 for now", agent=None)
    assert (mine["authority"]["state"], mine["authority"]["pending"]) == ("direct", False)
    # with no line given, rb finds the line that holds the quote; a quote the file does not hold is refused
    found = rec.decide("q-width", "investigate", "--why", "the sweep is done", "--past", "--source", "docs/calls.md", "--quote", "drop the width question")
    assert found["past"]["source"]["line"] == 3 and found["authority"]["state"] == "past_unconfirmed"
    lines = len(rec.log())
    code, env = rec.rb("decide", "q1", "reject", "--why", "w", "--past", "--source", "docs/calls.md", "--quote", "We keep the width question", agent=AGENT)
    assert code == 2 and env["errors"][0]["code"] == "E_SOURCE_UNRESOLVED" and len(rec.log()) == lines
    code, env = rec.rb("decide", "q1", "reject", "--why", "w", "--past", "--source", "docs/calls.md:1", "--quote", "We drop the width question", agent=AGENT)
    assert code == 2 and env["errors"][0]["message"] == "docs/calls.md:1 does not hold the quote; line 3 does"
    code, env = rec.rb("decide", "q1", "reject", "--why", "w", "--when", "May 7", agent=AGENT)
    assert code == 2 and env["errors"][0]["code"] == "E_OBJECT_INVALID" and len(rec.log()) == lines


@pytest.mark.parametrize("how", ["untracked", "committed after", "the record began in the session"])
def test_a_document_that_cannot_date_the_call_establishes_nothing(tmp_path, tmp_path_factory, how):
    """A file git does not track, a quote first committed after the record began, or a record that began in the session
    that cites the file: rb says in one line why it cannot tell the document predates the record, beside the past call,
    which waits for the user's confirmation as every past call a session gives does. Another session citing the same
    document, committed before a record that session did not begin, reads what git shows, and waits too."""
    rec = started(Rec(tmp_path / "work", tmp_path_factory.mktemp("home")), by=AGENT if how.startswith("the record") else None)
    path, quote = "docs/calls.md", "We pause INT8 for now."
    if how == "untracked":
        path = "notes.md"
        (rec.root / path).write_text(quote + "\n", encoding="utf-8")
    elif how == "committed after":
        path = "notes.md"
        rec.commit(path, quote + "\n", when=LATER)
    before = states(rec.status())
    d = rec.decide("int8", "park", "--why", "paused", "--past", "--source", path, "--quote", quote)
    why = {
        "untracked": "notes.md is not in git, so rb cannot tell when it was written",
        "committed after": f"notes.md first held the quote in commit {rec.git('log', '--format=%H', '--', 'notes.md').strip()[:12]} (2099-01-01), made after the record began",
        "the record began in the session": (f"the quote is in docs/calls.md:4, committed 2026-05-07 ({rec.git('log', '--format=%H', '--', 'docs/calls.md').strip()[:12]}), "
                                            "before the record began; whoever began the record cites it, so the file may have been written for the record"),
    }[how]
    assert (d["authority"]["state"], d["authority"]["basis"]) == ("past_unconfirmed", why)
    assert states(rec.status()) == before
    said = rec.text("decide", "q1", "investigate", "--why", "paused", "--past", "--source", path, "--quote", quote, agent=AGENT)
    assert f"  {why}\n" in said and "It changes nothing until it is confirmed" in said
    assert f"park int8 (date unknown): {path}:{d['past']['source']['line']}: \"{quote}\" · note: paused · {why} · confirm: rb confirm {d['id']}" in rec.text("status")
    if how.startswith("the record"):
        other = rec.decide("q-width", "reject", "--why", "the sweep is done", "--past", "--source", path, "--quote", "We drop the width question",
                           agent=OTHER)
        assert (other["authority"]["state"], other["authority"]["pending"]) == ("past_unconfirmed", True)
        assert other["authority"]["basis"].endswith(", before the record began")


@pytest.mark.parametrize("quote, why", [
    ("Calls", "has 1 word, too few to state a call (4 at least)"),
    ("the sweep is done", "does not name q1 or any of its words, so it does not say the call is on it"),
])
def test_a_quote_that_cannot_state_the_call_establishes_nothing(rec, quote, why, monkeypatch):
    """A heading, a word or a clause that names nothing of the subject is in a document committed long ago, and states
    no call: the past call waits for a confirmation, from the command line, the SDK and MCP alike, and rb says why."""
    before = states(rec.status())
    line = 1 if quote == "Calls" else 3
    d = rec.decide("q1", "ship", "--why", "shipped", "--past", "--source", f"docs/calls.md:{line}", "--quote", quote)
    assert (d["authority"]["state"], d["authority"]["basis"]) == ("past_unconfirmed", f"the quote at docs/calls.md:{line} {why}")
    at_the_users_terminal(rec, monkeypatch)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RBWarning)
        did = rabbit_brain.open(agent="notebook-agent").decide("c1", "accept", why="fine", past=True, source=f"docs/calls.md:{line}", quote=quote)
    server = Mcp(root=rec.root, env={**rec.env, "RB_SESSION": "sess-calls-mcp-2"})
    try:
        server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "clientInfo": {"name": "calls-client"}}})
        answer = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "decide", "arguments": {
            "subject": "int8", "action": "stop", "why": "done", "past": True, "source": f"docs/calls.md:{line}", "quote": quote}}})["result"]
    finally:
        server.close()
    st = rec.status()
    assert states(st) == before and st["decisions"] == []
    assert [p["id"] for p in st["pending"]] == [d["id"], did, answer["structuredContent"]["data"]["object"]["id"]]


def test_a_quote_names_its_subject_by_a_setting_written_as_name_and_value(tmp_path, tmp_path_factory):
    """Review of round 6: the subject check split P=4 into P and 4 and dropped both as too short, so a quote that names
    the experiment by its settings read as naming nothing of it. A setting written as name=value is one word."""
    rec = Rec(tmp_path / "work", tmp_path_factory.mktemp("home"))
    rec.commit("docs/plan.md", "# Plan\n\nWe stop P=4 at d=1024: it costs too much.\n", when=MAY)
    started(rec)
    rec.ok("experiment", "add", "P=4 against P=6 at d=1024", "--id", "p-d1024", "--baseline", "p6", "--candidate", "p4")
    d = rec.decide("p-d1024", "stop", "--why", "it costs too much", "--past", "--when", "2026-05-07", "--source", "docs/plan.md:3",
                   "--quote", "We stop P=4 at d=1024")
    assert d["authority"]["state"] == "past_unconfirmed" and d["authority"]["basis"].startswith("the quote is in docs/plan.md:3, committed 2026-05-07")


@pytest.mark.parametrize("how", ["on the history the record began on", "an older rb's record, after a commit made since",
                                 "an older rb's record, merged in after the record was committed"])
def test_a_backdated_commit_establishes_nothing(tmp_path, tmp_path_factory, how):
    """A commit dated before the record began, made after it: rb init keeps the commit the record began on, and a
    commit not in its history came later. A record an older rb began keeps none, and then a commit that follows one
    made since the record began, or that is not in the history of the commit that first added the record, is no older
    than they are."""
    rec = started(Rec(tmp_path / "work", tmp_path_factory.mktemp("home")))
    if how.startswith("an older"):
        log = rec.root / ".rb" / "log.jsonl"
        rows = log.read_text(encoding="utf-8").splitlines()
        first = json.loads(rows[0])
        assert first["detail"]["head"] == rec.git("rev-parse", "HEAD").strip()
        del first["detail"]["head"]
        log.write_text("\n".join([json.dumps(first), *rows[1:]]) + "\n", encoding="utf-8")
        rec.git("add", "-A")
        rec.git("commit", "-qm", "the record")
    if how.endswith("committed"):
        rec.git("checkout", "-qb", "side", "HEAD~1")
    rec.commit("docs/back.md", "We reject c1 for good.\n", when="2026-04-01T00:00:00+00:00")
    if how.endswith("committed"):
        rec.git("checkout", "-q", "-")
        rec.git("merge", "-q", "--no-edit", "side")
    commit = rec.git("log", "--format=%H", "-1", "--", "docs/back.md").strip()[:12]
    d = rec.decide("c1", "reject", "--why", "for good", "--past", "--source", "docs/back.md", "--quote", "We reject c1 for good.")
    why = {
        "on the history the record began on": "which is not in the history the record began on",
        "an older rb's record, after a commit made since": "which follows a commit made after the record began",
        "an older rb's record, merged in after the record was committed": "which is not in the history of the commit that first added the record",
    }[how]
    assert (d["authority"]["state"], d["authority"]["basis"]) == ("past_unconfirmed", f"docs/back.md first held the quote in commit {commit}, {why}")
    assert rec.status()["pending"][0]["id"] == d["id"]


@pytest.mark.parametrize("how", ["put back in the copy here", "put back in a commit"])
def test_a_quote_the_document_no_longer_commits_establishes_nothing(rec, how):
    """docs/calls.md said it in May; a later commit says the opposite. Putting the old words back in the copy here, or
    in a new commit, does not make them as old as May."""
    rec.commit("docs/calls.md", CALLS.replace("We drop", "We keep"))
    (rec.root / "docs" / "calls.md").write_text(CALLS, encoding="utf-8")
    if how.endswith("commit"):
        rec.git("commit", "-qam", "put it back")
    d = rec.decide("q-width", "park", "--why", "dropped", "--past", "--source", "docs/calls.md:3", "--quote", "We drop the width question")
    why = ("docs/calls.md as last committed, with no change since, did not hold the quote when the call was recorded, so rb cannot tell when it was "
           "written") if how.endswith("here") else "first held the quote in commit"
    assert d["authority"]["state"] == "past_unconfirmed" and why in d["authority"]["basis"]
    (rec.root / "docs" / "calls.md").write_text(CALLS.replace("We drop", "We keep"), encoding="utf-8")
    assert rec.ok("show", d["id"])["data"]["authority"]["state"] == "past_unconfirmed"


def test_confirm_all_leaves_a_subject_two_calls_wait_on_to_the_user(rec):
    """Two sessions propose opposite calls on c1: --all confirms neither and names both, the user confirms the one that
    is theirs, and confirming the other after it says that it was answered."""
    a = rec.decide("c1", "reject", "--why", "too risky")
    b = rec.decide("c1", "accept", "--why", "good enough", agent=OTHER)
    c = rec.decide("q-width", "reject", "--why", "the sweep is done")
    said = rec.text("confirm", "--all")
    assert said.startswith(f"Confirming 1 call:\n  {c['id']}  proposed, not made: reject q-width · note: the sweep is done\n")
    assert f"Not confirmed: 2 calls wait on c1 ({a['id']}, {b['id']}). Confirm the one that is yours: rb confirm <id>." in said
    st = rec.status()
    assert [p["id"] for p in st["pending"]] == [a["id"], b["id"]] and [x["subject"] for x in st["decisions"]] == ["q-width"]
    made = rec.ok("confirm", b["id"])["data"]["confirmed"][0]["object"]["id"]
    assert rec.status()["pending"] == []
    again = rec.text("confirm", a["id"])
    assert f"  {a['id']} was answered by {made}, a call made on c1 after it was proposed: this call now comes after that one." in again


def test_confirm_says_what_the_call_rests_on_now_and_where_the_reading_moved(rec):
    """Proposed while c1 was untested, confirmed after a run made it miss: rb confirm says the reading moved, what
    the call is made on now and that it goes against the rule, as rb decide says them."""
    d = rec.decide("c1", "accept", "--why", "meets it")
    rec.ok("run", "add", "int8", "fp32.val_loss=5.60", "int8.val_loss=5.90", "--command", "python eval.py")
    listed = rec.text("confirm")
    moved = "c1 read criterion untested when this call was recorded; it reads criterion not met now."
    assert f"  {d['id']}  proposed, not made: accept c1 · note: meets it\n      {moved}\n" in listed
    said = rec.text("confirm", d["id"])
    made = rec.status()["decisions"][0]
    assert f"  {moved}\n" in said and "  made on c1: " in said
    assert "  This goes against the rule (criterion not met). It is recorded as made; rb status shows both." in said
    assert made["action"] == "accept" and made["authority"]["state"] == "confirmed"


def test_confirm_and_rb_ui_refuse_where_an_agent_session_left_its_id(rec):
    """An agent that unsets CLAUDECODE and AI_AGENT still runs where Claude Code left its session id and entry point:
    rb confirm and rb ui fail closed on those too, and write nothing. Review of group 9: a session that unset the
    variables rb listed confirmed its own calls and gate, with its messaging socket and other session-only variables
    still set; each of those is read the same way."""
    d = rec.decide("q-width", "reject", "--why", "the sweep is done")
    lines = len(rec.log())
    for left in ({"CLAUDE_CODE_SESSION_ID": "sess-calls-5e2a", "CLAUDE_CODE_ENTRYPOINT": "cli"}, {"CLAUDE_CODE_ENTRYPOINT": "cli"},
                 {"CODEX_THREAD_ID": "thread-calls-1"}, {"CLAUDE_CODE_BRIDGE_SESSION_ID": "session_calls_9f3"},
                 {"CLAUDE_CODE_EXECPATH": "/opt/agent/cli.js"}, {"CLAUDE_PID": "4242"}, {"CLAUDE_CODE_MESSAGING_SOCKET": "/tmp/sock-calls"},
                 {"CLAUDE_CODE_MESSAGING_TOKEN": "tok-calls"}, {"CLAUDE_CODE_SESSION_ATTENDED": "1"}):
        code, env = rec.rb("confirm", d["id"], extra=left)
        assert code == 2 and env["errors"][0]["code"] == "E_USAGE" and "agent session" in env["errors"][0]["message"], left
        r = rec.run("ui", extra=left)
        assert r.returncode == 2 and "this is an agent session" in r.stderr, left
    assert len(rec.log()) == lines and rec.status()["pending"][0]["id"] == d["id"]


def test_a_session_cannot_take_back_the_users_call(rec, monkeypatch):
    """Review of round 6's build: a session ran rb retract on the researcher's park, and the experiment read open at
    once, with nothing on rb status. A session's retract of a call that is the user's (made in their terminal, or one
    they confirmed) is refused from the command line, the SDK and MCP, and the call stands; a session proposes another
    call instead. A call it proposed and nobody confirmed it still takes back."""
    mine = rec.decide("int8", "park", "--why", "budget", agent=None)
    asked = rec.decide("q-width", "reject", "--why", "the sweep is done")
    rec.ok("confirm", asked["id"])
    [confirmation] = [d["id"] for d in rec.status()["decisions"] if d["subject"] == "q-width"]
    lines = len(rec.log())
    for d in (mine["id"], asked["id"], confirmation):
        code, env = rec.rb("retract", d, "--why", "undo", agent=AGENT)
        assert code == 2 and env["errors"][0]["code"] == "E_USAGE" and "the user's call" in env["errors"][0]["message"], (d, env["errors"])
    assert "rb decide int8 <action>" in rec.rb("retract", mine["id"], "--why", "undo", agent=AGENT)[1]["errors"][0]["fix"]
    at_the_users_terminal(rec, monkeypatch)
    with pytest.raises(rabbit_brain.RBError):
        rabbit_brain.open(rec.root, agent="notebook-agent").retract(mine["id"], why="undo")
    server = Mcp(root=rec.root, env={**rec.env, **AGENT})
    try:
        server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "clientInfo": {"name": "calls-client"}}})
        answer = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "retract", "arguments": {"subject": mine["id"], "why": "x"}}})
        assert answer["result"].get("isError"), answer
    finally:
        server.close()
    assert len(rec.log()) == lines
    st = states(rec.status())
    assert st["experiments"]["int8"] == "parked" and st["questions"]["q-width"] == "dropped"
    proposed = rec.decide("int8", "continue", "--why", "calibration fixed")
    rec.ok("retract", proposed["id"], "--why", "asked too early", agent=AGENT)
    assert not rec.status()["pending"]
    rec.ok("retract", mine["id"], "--why", "budget came through")
    assert states(rec.status())["experiments"]["int8"] != "parked"


@pytest.mark.parametrize("left", [{"CLAUDE_CODE_SESSION_ID": "sess-calls-left-1"}, {"CLAUDECODE": "0", "CLAUDE_CODE_SESSION_ID": "sess-calls-left-2"},
                                  {"RB_ACTOR": "human:ana", "CLAUDE_CODE_ENTRYPOINT": "cli"}, {"CLAUDE_CODE_BRIDGE_SESSION_ID": "session_calls_9f3"},
                                  {"CLAUDE_CODE_EXECPATH": "/opt/agent/cli.js"}, {"CLAUDE_PID": "4242"},
                                  {"CLAUDE_CODE_MESSAGING_SOCKET": "/tmp/sock-calls"}, {"CLAUDE_CODE_SESSION_ATTENDED": "1"}])
def test_a_write_where_an_agent_session_left_its_id_is_that_sessions(rec, monkeypatch, left):
    """Review of round 6: a session that unset CLAUDECODE but left its id or entry point wrote as the user, so its past
    call read as their call, its note as theirs and its gate words as their words. Every write reads a session as rb
    confirm does: the past call waits, the note is not confirmed and the gate's words are as given, from the command
    line and the SDK alike."""
    past = ("--past", "--when", "2026-05-07", "--source", "docs/calls.md:4", "--quote", "We pause INT8 for now")
    d = rec.decide("int8", "park", "--why", "fp32 first", *past, agent=left)
    assert d["by"].startswith("agent:") and d["authority"]["state"] == "past_unconfirmed" and d["authority"]["pending"]
    rec.ok("note", "add", "-e", "int8", "seed 2 looked odd", agent=left)
    rec.ok("experiment", "gate", "int8", "--if-holds", "ship INT8", "--words", "ship it if it holds", agent=left)
    text = rec.text("status")
    assert "seed 2 looked odd · not confirmed" in text and 'words (as given): "ship it if it holds"' in text and "your words" not in text
    assert "call: parked" not in text and [p["id"] for p in rec.status()["pending"]] == [d["id"]]
    at_the_users_terminal(rec, monkeypatch)
    for k, v in left.items():
        monkeypatch.setenv(k, v)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        did = rabbit_brain.open().decide("q-width", "reject", why="the sweep is done", past=True, when="2026-05-07", source="docs/calls.md:3",
                                         quote="We drop the width question")
    st = rec.status()
    assert [p["id"] for p in st["pending"]] == [d["id"], did] and {q["id"]: q["state"] for q in st["questions"]}["q-width"] == "open"


def test_a_by_edited_in_a_sessions_decision_file_makes_it_nobodys_call(rec):
    """Review of round 6: a session that rewrote by: in its past call's file to the user's name got it read as the
    user's call (status's call line, rb show, authority direct). Whose call it is rests on whom rb wrote it for: edited,
    it is listed only as edited outside rb, never said as a call made, and its words never as the user's."""
    d = rec.decide("q-width", "reject", "--why", "the sweep is done", "--past", "--when", "2026-05-07", "--source", "docs/calls.md:3",
                   "--quote", "We drop the width question", "--words", "drop it")
    path = rec.root / ".rb" / "decisions" / f"{d['id']}.yaml"
    path.write_text(path.read_text(encoding="utf-8").replace("by: agent:claude-code", "by: human:ana"), encoding="utf-8")
    code, env = rec.rb("doctor", "--adopt", "--why", "keep it", agent=AGENT)
    assert not env["ok"]                                                   # an edit to who decided is never adopted
    st = rec.status()
    assert st["decisions"] == [] and st["pending"] == [] and {q["id"]: q["state"] for q in st["questions"]}["q-width"] == "open"
    assert "edited_outside_rb" in [i["code"] for i in st["open"]]
    assert rec.ok("show", d["id"])["data"]["authority"]["state"] == "past_unconfirmed"
    for text in (rec.text("status"), rec.text("show", d["id"]), rec.text("show", "q-width"), rec.text("context")):
        assert "call: rejected" not in text and "Decision " + d["id"] + ": rejected" not in text and "your words" not in text
        assert text.count("the sweep is done") == text.count("· note: the sweep is done")



def text_of(page: str, gap: str = " ") -> str:
    """A page's text as a reader gets it: what its script and style elements hold is code, never said (the pointer
    guide's steps live in the script until it draws them), so it goes before the tags do."""
    return re.sub(r"<[^>]+>", gap, re.sub(r"(?is)<(script|style)\b[^>]*>.*?</\1\s*>", gap, page))


def no_calls_said(page: str) -> str:
    """A page's text without the places it names its Your call view by and says it holds no call ("Your call (0)",
    the nav's "Your call 0"): what is left must not say Your call at all."""
    return re.sub(r"Your call (?:\(0\)|0(?!\d))", "", page)

def screens_of(rec: Rec, monkeypatch, tmp_path) -> dict:
    """What the user's screens say: rb status, rb context, a page's text, rb open's data and rb ui's views."""
    from rbtest import home
    rec.ok("page", "--out", str(tmp_path / "page.html"))
    page = html_mod.unescape(text_of((tmp_path / "page.html").read_text(encoding="utf-8")))
    at_the_users_terminal(rec, monkeypatch)
    server = Open(rec.root)
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(server.url + "/state.json", timeout=30) as r:
            state = json.loads(r.read().decode("utf-8"))
    finally:
        server.close()
    return {"status": rec.text("status"), "context": rec.text("context"), "page": " ".join(page.split()),
            "open": json.dumps(state, ensure_ascii=False), "ui": json.dumps(home(rec.root), ensure_ascii=False)}


def test_a_past_calls_line_rests_on_the_documents_words_and_says_the_reason_given_as_a_note(rec, tmp_path, monkeypatch):
    """The Oct 11 trials showed a session's --why where the document's words belong. A past call's line gives the
    file's words verbatim with its file and line, and the reason the session gave only as a note on it, on every screen
    that lists it: rb status, rb context, rb show, a page, rb open and rb ui."""
    rec.commit("notes.md", "# Notes\n\nWe pause INT8 until the calibration set is fixed.\n", when=LATER)
    d = rec.decide("int8", "park", "--why", "calibration first", "--past", "--when", "2026-05-07", "--source", "notes.md:3", "--quote", "We pause INT8 until the calibration set is fixed")
    assert d["authority"]["state"] == "past_unconfirmed"
    line = 'park int8 (2026-05-07): notes.md:3: "We pause INT8 until the calibration set is fixed" · note: calibration first'
    shown = rec.text("show", d["id"])
    assert shown.startswith(f"Decision {d['id']}: a past call mentioned, not confirmed: {line} · recorded ")
    assert f"{line} · {d['authority']['basis']} · confirm: rb confirm {d['id']}" in rec.text("show", "int8")
    for where, said in screens_of(rec, monkeypatch, tmp_path).items():
        said = said.replace('\\"', '"')
        assert line in said, where
        assert "(2026-05-07): calibration first" not in said, where


def test_a_past_call_a_session_reconstructs_never_reads_as_the_users_call_until_it_is_confirmed(rec, tmp_path, monkeypatch):
    """A past call quoted from a document committed in May, given from the command line, the SDK and MCP in agent
    sessions: until one rb confirm, no screen says it as the user's (no call line, no Your call, no "you decided"); each
    says the file's words with file and line, the session's reason as its note, and what git shows of the file."""
    from rbtest import object_card
    rec.ok("show", "q-width", agent=OTHER)                                 # another session has looked: it is told what comes next
    d = rec.decide("q-width", "reject", "--why", "the sweep is done", "--past", "--when", "2026-05-07", "--source", "docs/calls.md:3",
                   "--quote", "We drop the width question")
    file_voice = 'reject q-width (2026-05-07): docs/calls.md:3: "We drop the width question" · note: the sweep is done'
    at_the_users_terminal(rec, monkeypatch)
    with warnings.catch_warnings(record=True) as told:
        warnings.simplefilter("always")
        did = rabbit_brain.open(agent="notebook-agent").decide("int8", "park", why="fp32 first", past=True, when="2026-05-07",
                                                               source="docs/calls.md:4", quote="We pause INT8 for now")
    assert [str(w.message) for w in told if issubclass(w.category, RBWarning)] == [
        f"{did} is a past call mentioned, not confirmed: it changes nothing until it is confirmed (rb confirm {did}, in your own terminal)."]
    server = Mcp(root=rec.root, env={**rec.env, "RB_SESSION": "sess-calls-mcp-3"})
    try:
        server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "clientInfo": {"name": "calls-client"}}})
        answer = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "decide", "arguments": {
            "subject": "c1", "action": "accept", "why": "fine", "past": True, "source": "docs/calls.md:3", "quote": "We drop the width question"}}})
    finally:
        server.close()
    assert answer["result"]["structuredContent"]["data"]["authority"]["state"] == "past_unconfirmed"
    st = rec.status()
    assert st["decisions"] == [] and [p["authority"]["state"] for p in st["pending"]] == ["past_unconfirmed"] * 3
    assert {q["id"]: q["state"] for q in st["questions"]}["q-width"] == "open"
    assert {e["id"]: e["state"] for e in st["experiments"]}["int8"] == "open"
    screens = screens_of(rec, monkeypatch, tmp_path)
    screens.update({"show the call": rec.text("show", d["id"]), "show the question": rec.text("show", "q-width"), "confirm": rec.text("confirm"),
                    "rb_delta": "\n".join(f["text"] for f in rec.ok("show", "q-width", agent=OTHER)["rb_delta"])})
    for where, said in screens.items():
        said = said.replace('\\"', '"')
        assert file_voice in said, where
        # a page names its Your call view, holding no call: that is no call said as the user's
        said = no_calls_said(said) if where == "page" else said
        for not_theirs in ("Your call", "your call", "you decided", "You decided", "rejected (a past call", "call: rejected"):
            assert not_theirs not in said, (where, not_theirs)
    # a card lists the calls made on its subject: none
    for subject in ("q-width", "int8"):
        assert "decision: " not in json.dumps(object_card(subject, rec.root), ensure_ascii=False), subject
    assert "before the record began" in screens["status"] and "before the record began" in screens["show the call"]
    assert "before the record began" in screens["confirm"]
    # what git shows of the file is beside the call wherever it waits, rb's fact apart from the session's note
    for where in ("status", "context", "show the question", "open", "ui", "confirm"):
        assert f"{file_voice} · the quote is in docs/calls.md:3, committed 2026-05-07 (" in screens[where].replace('\\"', '"'), where
    for where in ("open", "ui"):                                           # the open item's own line
        assert '"We drop the width question" · note: the sweep is done · the quote is in' in screens[where].replace('\\"', '"'), where
        assert "the sweep is done (the quote is in" not in screens[where], where
    assert 'park int8 (2026-05-07): docs/calls.md:4: "We pause INT8 for now" · note: fp32 first · the quote is in docs/calls.md:4' in rec.text("show", "int8")


def test_a_confirmed_calls_reason_stays_the_sessions_note(rec, tmp_path, monkeypatch):
    """Review of round 6: rb confirm copies the session's reason onto the user's call, and every screen then said it as
    the user's own reason, in the form of their direct calls. The user confirmed the call, not the words a session gave
    for it: once confirmed, a past call's and a proposal's reason read only as `· note:` on rb confirm, rb status,
    rb context, rb show (call and subject), a page, rb open, the cards and rb_delta. The user's own call is unchanged."""
    from rbtest import object_card
    rec.ok("show", "q-width", agent=OTHER)                                 # another session has looked: it is told what changed
    past = rec.decide("q-width", "reject", "--why", "SESSION sweep done", "--past", "--when", "2026-05-07", "--source", "docs/calls.md:3",
                      "--quote", "We drop the width question")
    rec.decide("int8", "continue", "--why", "SESSION more seeds")
    rec.ok("decide", "q1", "investigate", "--why", "MINE one more look")
    said = {"confirm": rec.text("confirm", "--all")}
    made = {x["authority"]["confirms"]: x["id"] for x in rec.status()["decisions"] if x["authority"].get("confirms")}
    assert len(made) == 2
    said.update({"show the call": rec.text("show", made[past["id"]]), "show the question": rec.text("show", "q-width"), "show int8": rec.text("show", "int8"),
                 "rb_delta": "\n".join(f["text"] for f in rec.ok("show", "q-width", agent=OTHER)["rb_delta"])})
    said.update(screens_of(rec, monkeypatch, tmp_path))
    said["cards"] = json.dumps([object_card(s, rec.root) for s in ("q-width", "int8")], ensure_ascii=False)
    for where, text in said.items():
        text = text.replace('\\"', '"')
        shown = [r for r in ("SESSION sweep done", "SESSION more seeds") if r in text]
        assert shown, where
        for reason in shown:
            assert text.count(reason) == text.count(f"· note: {reason}"), (where, reason)
    assert all(r in said[w] for w in ("status", "context", "page", "open", "cards", "confirm") for r in ("SESSION sweep done", "SESSION more seeds"))
    assert '"Does INT8 cost accuracy?" (MINE one more look)' in said["status"]


def test_a_past_park_says_which_runs_of_its_experiment_were_produced_after_the_day_it_gives(rec, tmp_path, monkeypatch):
    """A past park dated May 7 on an experiment with a run produced on May 20 says so, wherever the park shows; a run
    produced on the day itself, in any time zone, does not count, and neither does a call that steers no line of work."""
    late = rec.ok("run", "add", "int8", "fp32.val_loss=5.60", "int8.val_loss=5.66", "--command", "python eval.py",
                  "--produced-at", "2026-05-20T10:00:00Z")["data"]["object"]["id"]
    rec.ok("run", "add", "int8", "fp32.val_loss=5.62", "int8.val_loss=5.65", "--command", "python eval.py", "--produced-at", "2026-05-08T03:00:00Z")
    d = rec.decide("int8", "park", "--why", "calibration first", "--past", "--when", "2026-05-07")
    said = f"1 run of int8 was produced after 2026-05-07, the day given for this call: {late}"
    st = rec.status()
    assert st["pending"][0]["runs_after"] == [late]
    assert [i["what"] for i in st["open"] if i["code"] == "past_call_unconfirmed"] == [
        f"park int8 (2026-05-07), a past call mentioned, not confirmed · note: calibration first · {said}"]
    assert f"  {said}" in rec.text("show", d["id"]).splitlines() and rec.ok("show", d["id"])["data"]["runs_after"] == [late]
    for where, text in screens_of(rec, monkeypatch, tmp_path).items():
        assert said in text, where
    rec.ok("confirm", d["id"])
    st = rec.status()
    [made] = [x for x in st["decisions"] if x["subject"] == "int8"]
    assert made["runs_after"] == [late] and f'call: parked 2026-05-07 (a past call, Retrospective): "INT8 vs FP32" · note: calibration first · {said}' in rec.text("status")
    later = rec.decide("int8", "stop", "--why", "done", "--past", "--when", "2026-05-20")
    vouch = rec.decide("c1", "accept", "--why", "fine", "--past", "--when", "2026-05-01")
    rows = {p["id"]: p for p in rec.status()["pending"]}
    assert "runs_after" not in rows[later["id"]] and "runs_after" not in rows[vouch["id"]]


def test_rb_confirm_says_the_runs_produced_after_a_past_parks_day(rec):
    """Review of round 6's build: rb status said a run was produced after a past park's day, but rb confirm, its
    listing and rb confirm --all, which the report sends the researcher to, left it out."""
    late = rec.ok("run", "add", "int8", "fp32.val_loss=5.61", "int8.val_loss=5.63", "--command", "python eval.py",
                  "--produced-at", "2026-05-20T10:00:00Z")["data"]["object"]["id"]
    d = rec.decide("int8", "park", "--why", "calibration first", "--past", "--when", "2026-05-07")
    said = f"1 run of int8 was produced after 2026-05-07, the day given for this call: {late}"
    assert said in rec.text("confirm")
    assert said in rec.text("confirm", "--all", "--dry-run")
    done = rec.text("confirm", d["id"])
    assert f"  {said}" in done.splitlines()


def test_a_past_call_lists_no_reading_as_what_it_was_made_on(rec, tmp_path, monkeypatch):
    """Round 6: a May 7 park confirmed in October listed "made on c1: ..." under it, readings of runs produced after
    the call. A past call says its readings as what each criterion reads now, when it is confirmed, and as read when it
    was recorded, on rb show; once one moves it never says it was made on them, and asks nobody to decide again: no
    item on the experiment, and none on a criterion a past accept is on. A call made now still is."""
    d = rec.decide("int8", "park", "--why", "calibration first", "--past", "--when", "2026-05-07")
    said = rec.text("confirm", d["id"])
    assert "  c1 reads now: " in said and "made on" not in said.replace(f"a call made on int8", "")
    [made] = [x for x in rec.status()["decisions"] if x["subject"] == "int8"]
    shown = rec.text("show", made["id"])
    assert "  c1 read when it was recorded: " in shown and "made on" not in shown
    assert "c1" in made["basis"]                                         # the record and the JSON keep the reading
    rec.ok("run", "add", "int8", "fp32.val_loss=5.60", "int8.val_loss=5.90", "--command", "python eval.py")
    text, st = rec.text("status"), rec.status()
    assert " · its readings have moved since it was recorded" in text and "made on" not in text
    assert not [i for i in st["open"] if i["code"] == "decision_outdated"]
    assert " · its readings have moved since it was recorded (c1)" in rec.text("show", "int8")
    assert "made on" not in rec.text("context").split("## Decisions")[1]
    # a past accept on the criterion, confirmed, then a run that moves its reading
    rec.commit("docs/plan.md", "# Plan\n\nWe accept the INT8 validation loss as it stands.\n", when=MAY)
    vouch = rec.decide("c1", "accept", "--why", "within budget", "--past", "--when", "2026-05-07", "--source", "docs/plan.md:3",
                       "--quote", "We accept the INT8 validation loss as it stands")
    rec.ok("confirm", vouch["id"])
    rec.ok("run", "add", "int8", "fp32.val_loss=5.60", "int8.val_loss=5.95", "--command", "python eval.py")
    st = rec.status()
    assert not [i for i in st["open"] if i["code"] in ("decision_outdated", "undecided")]
    for where, said in screens_of(rec, monkeypatch, tmp_path).items():
        assert "made on" not in said.replace("a call made on int8", ""), where
    now = rec.text("decide", "c1", "reject", "--why", "too lossy")
    assert "  made on c1: " in now


# ---------------------------------------------------------------- whose words


def test_words_given_in_an_agent_session_never_read_as_the_users_until_they_confirm_the_call(rec, tmp_path, monkeypatch):
    """The words an agent gives with a write claim the user said them. rb's text, the rb_delta another session reads,
    rb open's data, rb ui's views and the cards say them as given, never as the user's; a page says none. Once the user
    confirms the call they came with, they are the user's words wherever rb says them."""
    from rbtest import home, object_card
    criterion_words, call_words = "make it 0.1, it is noisy", "park INT8 until the calibration set is fixed"
    rec.ok("show", "int8", agent=OTHER)                                   # another session has looked: it is told what comes next
    rec.ok("criterion", "add", "INT8 adds at most 0.1", "-e", "int8", "--metric", "change.val_loss", "--at-most", "0.1", "--id", "c2",
           "--words", criterion_words, agent=AGENT)
    said = rec.text("decide", "int8", "park", "--why", "the calibration set is wrong", "--words", call_words, agent=AGENT)
    d = rec.status()["pending"][0]
    texts = {
        "decide": said, "status": rec.text("status"), "show the call": rec.text("show", d["id"]), "show the experiment": rec.text("show", "int8"),
        "show the criterion": rec.text("show", "c2"), "context": rec.text("context"), "confirm": rec.text("confirm"),
        "rb_delta": "\n".join(f["text"] for f in rec.ok("show", "int8", agent=OTHER)["rb_delta"]),
    }
    for where, text in texts.items():
        for words in (criterion_words, call_words):
            assert text.count(f'"{words}"') == text.count(f'words (as given): "{words}"'), (where, words)
            assert f'your words: "{words}"' not in text and f'their words: "{words}"' not in text, (where, words)
    assert f'words (as given): "{call_words}"' in texts["status"] and f'words (as given): "{criterion_words}"' in texts["show the criterion"]
    assert f'words (as given): "{call_words}"' in texts["rb_delta"]
    at_the_users_terminal(rec, monkeypatch)
    server = Open(rec.root)
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(server.url + "/state.json", timeout=30) as r:
            state = r.read().decode("utf-8")
    finally:
        server.close()
    screens = [state, json.dumps(home(rec.root)), json.dumps(object_card("c2", rec.root)), json.dumps(object_card("int8", rec.root))]
    for screen in screens:
        for words in (criterion_words, call_words):
            assert screen.count(f'\\"{words}\\"') == screen.count(f'words (as given): \\"{words}\\"'), words
    rec.ok("page", "--out", str(tmp_path / "page.html"))
    page = (tmp_path / "page.html").read_text(encoding="utf-8")
    assert criterion_words not in page and call_words not in page
    rec.ok("confirm", d["id"])
    assert f'call: parked: "INT8 vs FP32" · note: the calibration set is wrong · your words: "{call_words}"' in rec.text("status")
    assert f'words (as given): "{criterion_words}"' in rec.text("show", "c2")       # a criterion's words have no call to confirm


def test_a_notes_words_show_beside_it_as_given_and_in_the_json(rec, tmp_path, monkeypatch):
    """The words given with a note were kept only in log.jsonl. They show beside the note on rb status, rb context,
    rb show, rb open and rb ui, as words given (the user's own when they gave them in their terminal), and under
    `words` on each note in rb status's and rb context's JSON; a page says none. Quotes stay as given, and the JSON keeps a
    line break, which a screen line says as a space so the words stay on the note's line."""
    from rbtest import object_card
    agent_words = 'If it still costs "most of a point",\nI stop.'
    rec.ok("note", "add", "-e", "int8", "No file records the stop.", "--words", agent_words, agent=AGENT)
    rec.ok("note", "add", "The validation split is the 50k images.", "--words", "use the 50k split")
    st = rec.status()
    [exp] = [e for e in st["experiments"] if e["id"] == "int8"]
    assert [(n["text"], n.get("words")) for n in exp["notes"]] == [("No file records the stop.", agent_words)]
    assert [(n["text"], n.get("words")) for n in st["project"]["notes"]] == [("The validation split is the 50k images.", "use the 50k split")]
    context = rec.ok("context")["data"]["state"]
    assert [n.get("words") for e in context["experiments"] for n in e["notes"]] == [agent_words]
    given = 'No file records the stop. · words (as given): "If it still costs "most of a point", I stop."'
    for where in ("status", "context", ("show", "int8")):
        text = rec.text(*([where] if isinstance(where, str) else where))
        assert given in text and not [line for line in text.splitlines() if line.startswith("I stop.")], where
    assert 'The validation split is the 50k images. · your words: "use the 50k split"' in rec.text("status")
    screens = screens_of(rec, monkeypatch, tmp_path)
    for where in ("open", "ui"):
        assert json.dumps(given, ensure_ascii=False)[1:-1] in screens[where], where
    assert json.dumps(given, ensure_ascii=False)[1:-1] in json.dumps(object_card("int8", rec.root), ensure_ascii=False)
    assert "most of a point" not in screens["page"] and "use the 50k split" not in screens["page"]


def test_a_gates_words_show_beside_it_as_given_and_in_the_json(rec, tmp_path, monkeypatch):
    """Round 5: the words given with rb experiment gate --words were kept only in log.jsonl, so the gate's first-person
    text showed bare. They show beside the gate on rb status, rb context, rb show, rb open and the card, as words given
    (the user's own when they gave them in their terminal), and under `gate_words` on the experiment in rb status's and
    rb context's JSON; a later gate given no words drops them. The SDK and MCP give them alike. Round 6: words that are
    the gate's own text are said once, in its place, and a session's show as given on a page too."""
    from rbtest import object_card
    said = "If INT8 still costs a point, I stop the INT8 line."
    rec.ok("experiment", "gate", "int8", "--if-holds", said, "--words", said, agent=AGENT)
    st = rec.status()
    [exp] = [e for e in st["experiments"] if e["id"] == "int8"]
    assert (exp["gate_words"], exp["gate_by"]) == (said, "agent:claude-code")
    assert [e.get("gate_words") for e in rec.ok("context")["data"]["state"]["experiments"]] == [said]
    given = f'if it holds: words (as given): "{said}"'
    assert given in rec.text("status") and f"- {given}" in rec.text("context") and f"  {given}" in rec.text("show", "int8")
    screens = screens_of(rec, monkeypatch, tmp_path)
    assert f'words (as given): \\"{said}\\"' in screens["open"] and f'words (as given): "{said}"' in screens["page"]
    assert json.dumps(given, ensure_ascii=False)[1:-1] in json.dumps(object_card("int8", rec.root), ensure_ascii=False)
    for text in (rec.text("status"), rec.text("context"), rec.text("show", "int8")):
        assert text.count(said) == text.count(given), "the gate's words show bare somewhere"
    # the same at the user's own terminal reads as theirs once the gate is: the gate a session set waits with its design
    # until the user confirms it (review of the snapshot rule, round 1); a later gate with no words drops them
    rec.ok("experiment", "gate", "int8", "--if-not", "keep FP32", "--words", "else keep FP32")
    assert 'if it holds: ' + said + ' · if not: keep FP32 · words (as given): "else keep FP32" · not confirmed' in rec.text("status")
    rec.ok(*[e for e in rec.status()["experiments"] if e["id"] == "int8"][0]["design"]["confirm"].split()[1:])
    assert 'if it holds: ' + said + ' · if not: keep FP32 · your words: "else keep FP32"' in rec.text("status")
    rec.ok("experiment", "gate", "int8", "--if-not", "keep FP32 for now")
    assert "gate_words" not in [e for e in rec.status()["experiments"] if e["id"] == "int8"][0]
    # the SDK and MCP keep them the same way
    at_the_users_terminal(rec, monkeypatch)
    rabbit_brain.open(agent="notebook-agent").experiment("int8").gate(if_holds="ship INT8", words="ship it if it holds")
    assert [e for e in rec.status()["experiments"] if e["id"] == "int8"][0]["gate_words"] == "ship it if it holds"
    server = Mcp(root=rec.root, env={**rec.env, "RB_SESSION": "sess-calls-mcp-4"})
    try:
        server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "clientInfo": {"name": "calls-client"}}})
        server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "experiment_gate", "arguments": {
            "experiment": "int8", "if_holds": "ship INT8 now", "words": "ship it now"}}})
    finally:
        server.close()
    assert 'if it holds: ship INT8 now · if not: keep FP32 for now · words (as given): "ship it now"' in rec.text("status")


def test_a_sessions_note_and_interpretation_show_as_not_confirmed_and_keep_their_source_underneath(rec, tmp_path, monkeypatch):
    """Oct 11 evening: a session's interpretation, recorded as a note or as a recorded interpretation with what it rests
    on, shows on the screens as not confirmed and names no writer; the user's own does not. Its type and session stay in
    the JSON, where the SDK and MCP read them."""
    from rbtest import object_card
    human = "The validation split is the 50k images."
    rec.ok("note", "add", "-e", "int8", human)
    session = "results.csv row 2 gives val_loss 5.64; 5.66 was a working figure."
    rec.ok("note", "add", "-e", "int8", session, agent=AGENT)
    rec.ok("criterion", "file", "c1", "q1", "--when-met", "INT8 costs nothing that matters here", "--why", "docs/calls.md:4", agent=AGENT)
    said = f"{session} · not confirmed"
    texts = {"status": rec.text("status"), "context": rec.text("context"), "show": rec.text("show", "int8"),
             "card": json.dumps(object_card("int8", rec.root), ensure_ascii=False)}
    for where, text in texts.items():
        assert said in text, where
        assert f"{human} · not confirmed" not in text and "agent" not in text, where
    assert human in texts["show"] and human in texts["card"]
    assert "declared when met, not confirmed: 'INT8 costs nothing that matters here'" in texts["status"]
    screens = screens_of(rec, monkeypatch, tmp_path)
    # a page says rb's reading of the criterion between the label and the mark; the tab draws it from its data
    # the page draws the session's interpretation as a note, not confirmed (R's design pass: "A note, not confirmed: " then
    # its words; before it, rb's reading between the label and the mark), and names no writer
    page = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", screens["page"]))
    assert ("A note, not confirmed: INT8 costs nothing that matters here" in page
            or "Conditional interpretation · Its rule has no results yet. · not confirmed" in page), page
    assert "interpretation · not confirmed" not in screens["open"]
    [group] = [g for b in json.loads(screens["open"])["home"]["briefs"] for g in b["overview"].get("interpretations", [])]
    assert (group["text"], group["confirmed"], group["author"]) == ("INT8 costs nothing that matters here", False, "")
    # underneath: who wrote each, and the session
    st = rec.status()
    [exp] = [e for e in st["experiments"] if e["id"] == "int8"]
    assert [(n["text"], n["by"]) for n in exp["notes"]] == [(human, "human:ana"), (session, "agent:claude-code")]
    links = rec.ok("show", "c1")["data"]["object"]["question_links"]
    assert [l["declared_by"] for l in links if l["retracted"] is None] == ["agent:claude-code"]
    assert [r.get("session") for r in rec.log() if r.get("op") == "note"][-1] == AGENT["CLAUDE_CODE_SESSION_ID"]


def test_a_run_note_a_session_wrote_shows_as_not_confirmed(rec, tmp_path, monkeypatch):
    """Review of round 6: a run's --note from a session read as a plain note, as the user's own does. One written from
    the command line, the SDK or MCP in a session ends `· not confirmed` on rb context, rb show, a page, rb open's data
    and the run's card on rb ui; the user's own does not. Who wrote it stays in the JSON."""
    from rbtest import object_card
    ids = {"cli": rec.ok("run", "add", "int8", "fp32.val_loss=5.61", "int8.val_loss=5.63", "--note", "seed 2 looked odd", agent=AGENT)["data"]["object"]["id"],
           "user": rec.ok("run", "add", "int8", "fp32.val_loss=5.62", "int8.val_loss=5.63", "--note", "the user's own run note")["data"]["object"]["id"]}
    server = Mcp(root=rec.root, env={**rec.env, "RB_SESSION": "sess-calls-mcp-5"})
    try:
        server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "clientInfo": {"name": "calls-client"}}})
        answer = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "run_add", "arguments": {
            "experiment": "int8", "metrics": {"fp32.val_loss": 5.60, "int8.val_loss": 5.66}, "note": "seed 3 from the mcp side"}}})["result"]
    finally:
        server.close()
    ids["mcp"] = answer["structuredContent"]["data"]["object"]["id"]
    at_the_users_terminal(rec, monkeypatch)
    ids["sdk"] = rabbit_brain.open(agent="notebook-agent").experiment("int8").record({"fp32.val_loss": 5.59, "int8.val_loss": 5.65},
                                                                                       note="seed 4 from the notebook").id
    sessions = ("seed 2 looked odd", "seed 3 from the mcp side", "seed 4 from the notebook")
    screens = screens_of(rec, monkeypatch, tmp_path)
    texts = {"context": screens["context"], "page": screens["page"], "open": screens["open"]}
    for how, rid in ids.items():
        texts[f"show {how}"] = rec.text("show", rid)
        texts[f"card {how}"] = json.dumps(object_card(rid, rec.root), ensure_ascii=False)
    for where, text in texts.items():
        for note in sessions:
            assert text.count(note) == text.count(f"{note} · not confirmed"), (where, note)
        assert "the user's own run note · not confirmed" not in text, where
    assert all(f"{n} · not confirmed" in texts["context"] for n in sessions) and "the user's own run note" in texts["context"]
    assert "seed 2 looked odd · not confirmed" in texts["page"] and "seed 2 looked odd · not confirmed" in texts["card cli"]
    assert rec.ok("show", ids["cli"])["data"]["object"]["created_by"] == "agent:claude-code"


# ---------------------------------------------------------------- the JSON, and the older rb


def test_the_json_only_grows(rec):
    """Every key rb status, rb show and rb decide gave before stays; a decision gains its authority beside them, and
    rb status what waits for a confirmation, the found runs that need a per-run value (found_needs) and how rb knows of
    what the record holds (witness)."""
    made = rec.decide("q-width", "reject", "--why", "the sweep is done", agent=None)
    rec.decide("int8", "park", "--why", "later")
    env = rec.ok("status")
    assert {"ok", "rb_version", "command", "data", "errors", "next", "rb_delta"} == set(env)
    st = env["data"]
    old = {"project", "root", "actor", "experiment_id", "counts", "tally", "results", "findings", "experiments", "runs", "analyses", "questions",
           "resources", "decisions", "retracted", "metrics", "repository", "open", "gate", "found_unrecorded", "elsewhere", "fail_on", "outcome"}
    assert old <= set(st) and set(st) - old == {"pending", "record_start", "gates_pending", "designs_pending", "scope", "uncited_changes", "found_needs",
                                                 "witness"}
    assert {"question_count", "experiment_count", "criterion_count", "run_count", "analysis_count", "resource_count", "decision_count"} <= set(st["counts"])
    [row] = st["decisions"]
    old_row = {"id", "subject", "action", "why", "revisit", "by", "at", "via", "session", "words", "basis", "inquiry_basis", "value", "across",
               "retracted", "current"}
    assert old_row <= set(row) and set(row) - old_row == {"authority", "past", "witness"} and row["past"] is None
    assert {"who", "code", "subject", "what", "do"} <= set(next(i for i in st["open"] if i["code"] == "call_proposed"))
    shown = rec.ok("show", made["id"])["data"]
    assert {"object", "current", "authority"} <= set(shown)
    assert {"object", "actor", "authority"} <= set(rec.ok("decide", "q1", "investigate", "--why", "one more look", "--dry-run")["data"])


@pytest.mark.skipif(not os.environ.get("RB_OLD_BIN"), reason="RB_OLD_BIN names an rb from before call authority")
def test_an_older_rb_reads_the_record_after_an_ordinary_call_and_a_confirmation(tmp_path, tmp_path_factory):
    """A call made outside an agent session, a call an agent proposed and the user's confirmation of it use nothing an
    older rb lacks in the record's files (a confirmation's link is on its log line): the older rb reads the record with
    no problem, and its own write leaves it whole for this rb."""
    rec = started(Rec(tmp_path / "work", tmp_path_factory.mktemp("home")), linked=False)
    rec.decide("q-width", "reject", "--why", "the sweep is done", agent=None)
    d = rec.decide("int8", "park", "--why", "later")
    rec.ok("confirm", d["id"])
    rec.git("add", "-A")
    rec.git("commit", "-qm", "the record")

    def older(*argv) -> dict:
        r = subprocess.run([os.environ["RB_OLD_BIN"], *argv, "--json"], cwd=rec.root, capture_output=True, text=True, env=rec.env)
        out = json.loads(r.stdout)
        assert out["ok"] and r.returncode in (0, 1), (argv, r.returncode, out["errors"])
        return out["data"]

    st = older("status")
    assert not [i for i in st["open"] if i["code"] in ("state_newer", "edited_outside_rb", "merged_outside_rb")]
    assert {q["id"]: q["state"] for q in st["questions"]}["q-width"] == "dropped"
    assert {e["id"]: e["state"] for e in st["experiments"]}["int8"] == "parked"
    assert not older("doctor").get("problems")
    older("note", "add", "an older rb's note", "-e", "int8")
    code, env = rec.rb("doctor")
    assert code == 0 and not env["data"].get("problems")


# ---------------------------------------------------------------- what rb 0.6.0 said wrong in a real session (0.6.1)


def test_a_run_rb_watched_write_its_file_is_not_told_to_say_when_it_ran(rec):
    """A file a command run under rb journal run wrote is that run's: rb run add --from it says nothing of a file
    written minutes ago, while a file no watched command wrote, or one changed by hand since, still gets the line."""
    (rec.root / "eval.py").write_text("import json, os, sys\nd = sys.argv[1]\nos.makedirs(d, exist_ok=True)\n"
                                      "json.dump({'fp32': {'val_loss': 5.61}, 'int8': {'val_loss': 5.6 + int(d[-1]) / 100}}, open(d + '/metrics.json', 'w'))\n",
                                      encoding="utf-8")
    assert rec.run("journal", "run", "--", sys.executable, "eval.py", "out/seed1").returncode == 0
    watched = rec.run("run", "add", "int8", "--from", "out/seed1/metrics.json")
    assert watched.returncode == 0 and "before this run was recorded" not in watched.stdout + watched.stderr, watched.stderr
    # the same file changed by hand after the command ran is no longer that run's: the line comes back
    assert rec.run("journal", "run", "--", sys.executable, "eval.py", "out/seed3").returncode == 0
    time.sleep(3)
    (rec.root / "out" / "seed3" / "metrics.json").write_text('{"fp32": {"val_loss": 5.61}, "int8": {"val_loss": 5.7}}', encoding="utf-8")
    edited = rec.run("run", "add", "int8", "--from", "out/seed3/metrics.json")
    assert "out/seed3/metrics.json was written less than a minute before this run was recorded" in edited.stdout + edited.stderr
    subprocess.run([sys.executable, "eval.py", "out/seed2"], cwd=rec.root, check=True)
    unwatched = rec.run("run", "add", "int8", "--from", "out/seed2/metrics.json")
    assert "out/seed2/metrics.json was written less than a minute before this run was recorded" in unwatched.stdout + unwatched.stderr


def test_a_freeze_in_a_session_says_the_lock_as_done(rec):
    """rb freeze prints its review after it has frozen: a rule a session declared is said as locked now, with how to
    change it, never as something to check before locking it."""
    rec.ok("criterion", "add", "INT8 adds at most 0.1 validation loss", "-e", "int8", "--metric", "change.val_loss", "--at-most", "0.1", "--id", "c2",
           agent=AGENT)
    out = rec.text("freeze", "int8", "-m", "before the held-out runs", agent=AGENT)
    assert "int8 frozen" in out and "before locking it" not in out
    assert '    look: c2 is locked as the test now. Check it is the one you mean: a change to it needs --amend --why "<reason>".' in out


def test_a_miss_with_a_proposed_call_on_it_does_not_say_no_call_is_recorded(rec):
    """A rule that misses with a call proposed on it says the call is recorded and not made yet, never that no call on
    it is recorded; with no call at all it says so."""
    rec.ok("run", "add", "int8", "fp32.val_loss=5.61", "int8.val_loss=5.80", "--command", "python eval.py --seed 2")
    [before] = [i for i in rec.status()["open"] if i["code"] == "undecided" and i["subject"] == "c1"]
    assert before["what"] == "c1 misses its rule and no call on it is recorded: INT8 adds at most 0.05 validation loss"
    rec.decide("c1", "reject", "--why", "INT8 costs too much")
    [after] = [i for i in rec.status()["open"] if i["code"] == "undecided" and i["subject"] == "c1"]
    assert after["what"] == ("c1 misses its rule and the call recorded on it is not made yet: it waits for a confirmation: "
                             "INT8 adds at most 0.05 validation loss")
    assert "no call on it is recorded" not in rec.text("status")


def test_confirming_a_design_says_the_notes_on_it_stay_not_confirmed_and_how_to_say_one(rec):
    """A session's note is not part of the design: rb confirm lists the design with a line that names the note, says
    it stays not confirmed, and how the user says it in their own name; the confirmation says it again."""
    rec.ok("criterion", "add", "INT8 adds at most 0.1 validation loss", "-e", "int8", "--metric", "change.val_loss", "--at-most", "0.1", "--id", "c2",
           agent=AGENT)
    note = rec.ok("note", "add", "the calibration draw is fixed", "-e", "int8", agent=AGENT)["data"]["object"]["id"]
    line = (f"note {note} on int8 stays not confirmed: a note is not part of the design. To say one as yours, write it in your own "
            'terminal: rb note add "<its words>" -e int8, then rb retract <note> --why "<reason>".')
    listed = rec.text("confirm", "int8")
    assert line in listed
    assert [d["notes_left"] for d in rec.ok("confirm", "int8")["data"]["designs_pending"]] == [line]
    confirm = next(w for w in listed.split() if w.startswith("int8@"))
    done = rec.text("confirm", confirm)
    assert "int8's design is confirmed." in done and line in done
    assert "the calibration draw is fixed · not confirmed" in rec.text("show", "int8")


def test_a_cited_value_that_differs_is_said_where_it_is_set_and_once_on_its_criterion(rec, monkeypatch):
    """rb spec set with --cited says at once that the cited source used another value, and so does the SDK; rb show of
    the cited criterion says the difference once, as the problem it is."""
    rec.commit("configs/eval.yaml", "data:\n  batch_size: 256\n")
    rec.commit("docs/paper.md", "# INT8 ResNet-50\n\nINT8 validation loss is 5.64 on the held-out set.\n")
    set_ = rec.run("spec", "set", "int8", "data.batch_size", "--source", "configs/eval.yaml#data.batch_size", "--cited", "128", "--cited-source",
                   "docs/paper.md")
    said = set_.stdout + set_.stderr
    assert "verified" in said and ("data.batch_size is 256 here and 128 in the cited source: while they differ, a criterion of int8 that cites a "
                                   "number is not comparable, and its rule reads undetermined.") in said
    # the SDK says it beside the setting too, as an RBWarning
    at_the_users_terminal(rec, monkeypatch)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        rabbit_brain.open().experiment("int8").spec.set("data.batch_size", source="configs/eval.yaml#data.batch_size", cited=128)
    assert any("data.batch_size is 256 here and 128 in the cited source" in str(w.message) for w in caught), [str(w.message) for w in caught]
    rec.ok("criterion", "add", "INT8 reproduces the paper's validation loss", "-e", "int8", "--metric", "int8.val_loss", "--equals", "5.64",
           "--tolerance", "0.005", "--source", "docs/paper.md", "--quote", "INT8 validation loss is 5.64", "--locator", "line 3", "--id", "c3")
    shown = rec.text("show", "c3")
    assert shown.count("data.batch_size 256 here vs 128 in the cited source") == 1, shown


def test_a_run_recorded_again_has_one_space_before_not_confirmed(rec):
    """A run a session recorded again from the same file keeps "recorded again: <why>." as its note, with no space left
    after it, so every screen says "<why>. · not confirmed"."""
    (rec.root / "out").mkdir()
    (rec.root / "out" / "m.json").write_text('{"fp32": {"val_loss": 5.61}, "int8": {"val_loss": 5.63}}', encoding="utf-8")
    rec.ok("run", "add", "int8", "--from", "out/m.json", "--produced-at", "2026-10-01T10:00:00Z", agent=AGENT)
    again = rec.ok("run", "add", "int8", "--from", "out/m.json", "--produced-at", "2026-10-02T10:00:00Z", "--again", "--why", "rerun from its receipt.",
                   agent=AGENT)["data"]["object"]
    assert again["note"] == "recorded again: rerun from its receipt."
    shown = rec.text("show", again["id"])
    assert "  note: recorded again: rerun from its receipt. · not confirmed\n" in shown and ".  ·" not in shown
