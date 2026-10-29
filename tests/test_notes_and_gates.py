"""A note shows its short id wherever notes are listed, and a note can be taken back with a reason as a run can (rb
retract <note id>, the SDK's retract, MCP's retract): it stays in the record and leaves the screens. A gate's words are
said once, as given where a session gave them, on the page too.

Every name, number and session id here is made up.
"""
import hashlib
import html as html_mod
import json
import re
import subprocess
from pathlib import Path

import pytest

import rabbit_brain
from rbtest import AGENT_VARS, Mcp, command, environment

AGENT = {"CLAUDECODE": "1", "CLAUDE_CODE_SESSION_ID": "sess-notes-3f1a"}
GATE = "If INT8 still loses less than 0.01 top-1, I ship it next week."


def confirm(exp: str, if_holds: str = "", if_not: str = "", words: str = "") -> str:
    """The command that confirms a gate as it reads: its experiment and a short hash of the gate and its words."""
    said = json.dumps([if_holds, if_not, words], separators=(",", ":"), ensure_ascii=False)
    return f"rb confirm {exp}@{hashlib.sha256(said.encode('utf-8')).hexdigest()[:7]}"


def design_confirm(rec: "Rec", exp: str) -> str:
    """The command that confirms an experiment's design as rb lists it now, which a gate a session set waits with: its
    experiment and a short hash of the design; nothing where it does not wait."""
    [e] = [e for e in rec.ok("status")["data"]["experiments"] if e["id"] == exp]
    return e["design"]["confirm"]


class Rec:
    """A record whose user is human:ana, at their own terminal; `agent=` runs rb in an agent session."""

    def __init__(self, root: Path, home: Path) -> None:
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        self.env = {k: v for k, v in environment().items() if k not in AGENT_VARS}
        self.env.update(RB_TELEMETRY="0", DO_NOT_TRACK="1", HOME=str(home), USERPROFILE=str(home), RB_ACTOR="human:ana")

    def run(self, *argv: str, agent: dict | None = None) -> subprocess.CompletedProcess:
        env = {**self.env, **(agent or {})}
        if agent:
            env.pop("RB_ACTOR", None)
        return subprocess.run([*command(), *argv], cwd=self.root, env=env, capture_output=True, stdin=subprocess.DEVNULL, encoding="utf-8")

    def ok(self, *argv: str, agent: dict | None = None) -> dict:
        r = self.run(*argv, "--json", agent=agent)
        env = json.loads(r.stdout)
        assert r.returncode in (0, 1) and env["ok"], (argv, env["errors"])
        return env

    def text(self, *argv: str) -> str:
        r = self.run(*argv)
        assert r.returncode in (0, 1), (argv, r.stdout, r.stderr)
        return r.stdout

    def page(self) -> str:
        return html_mod.unescape(re.sub(r"<[^>]+>", "\n", self.html()))

    def html(self) -> str:
        """The page's markup, its entities read, where a pin needs the element a part of a line sits in."""
        out = self.root / "page.html"
        self.ok("page", "--out", str(out))
        return html_mod.unescape(out.read_text(encoding="utf-8"))


def at_the_users_terminal(rec: Rec, monkeypatch) -> None:
    """This process as the user at their own terminal in the record (for rb open and the SDK), whatever session or CI
    job runs the tests: the record's environment, with every variable an agent or a CI run is read from cleared."""
    monkeypatch.chdir(rec.root)
    for k, v in rec.env.items():
        monkeypatch.setenv(k, v)
    for k in AGENT_VARS:
        monkeypatch.delenv(k, raising=False)


@pytest.fixture
def rec(tmp_path, tmp_path_factory) -> Rec:
    r = Rec(tmp_path / "work", tmp_path_factory.mktemp("home"))
    r.ok("init", "INT8 ResNet-50 on ImageNet", "--no-agents")
    r.ok("question", "add", "Does INT8 cost top-1 accuracy?", "--id", "q1")
    r.ok("metric", "add", "top1", "--maximize")
    r.ok("experiment", "add", "INT8 against FP32", "--id", "int8", "-q", "q1", "--baseline", "fp32", "--candidate", "int8")
    r.ok("criterion", "add", "INT8 loses at most 0.01 top-1", "-e", "int8", "-q", "q1", "--metric", "top1", "--subject", "int8",
         "--reference", "fp32", "--at-least", "-0.01", "--id", "c1")
    r.ok("run", "add", "int8", "fp32.top1=0.761", "int8.top1=0.755")
    return r


def test_a_note_shows_its_id_and_a_retracted_note_leaves_every_screen(rec, monkeypatch):
    """Round 6: notes showed no id and could not be taken back, so a mistyped line number took a second note and then
    a note correcting it."""
    first = rec.ok("note", "add", "-e", "int8", "results.csv row 12 gives top1 0.755; read from line 21.", agent=AGENT)["data"]["object"]["id"]
    later = rec.ok("note", "add", "-e", "int8", "The calibration set is the first 1000 validation images.", agent=AGENT)["data"]["object"]["id"]
    on_project = rec.ok("note", "add", "Older precisions wait for backfill.", agent=AGENT)["data"]["object"]["id"]
    assert f"note {later}: The calibration set" in rec.text("status")
    shown = rec.text("show", "int8")
    assert f"  note {first} (" in shown and f"  note {later} (" in shown
    context = rec.text("context")
    assert f"- note {first}: results.csv row 12" in context and f"- note {on_project}: Older precisions" in context
    assert f"  note {on_project} (" in rec.text("status")
    # the page names a note by what it is on, never by its id, which stays the item's own (Look 7, FIX6)
    assert f'<li class="note" data-id="{first}"><span class="nd2">note on int8:</span> <span class="au">results.csv row 12' in rec.html()
    # a plain note adds no key an older rb does not know
    assert "retracted" not in (rec.root / ".rb" / "experiments" / "int8" / "notes" / f"{first}.yaml").read_text(encoding="utf-8")

    said = rec.text("retract", first, "--why", "the row is 12, the line was mistyped")
    assert said.startswith(f"{first} (note) retracted: the row is 12, the line was mistyped.")
    for where, text in {"status": rec.text("status"), "show": rec.text("show", "int8"), "context": rec.text("context"), "page": rec.page()}.items():
        assert "results.csv row 12" not in text, where
        assert "The calibration set" in text, where
    kept = (rec.root / ".rb" / "experiments" / "int8" / "notes" / f"{first}.yaml").read_text(encoding="utf-8")
    assert "results.csv row 12" in kept and "why: the row is 12, the line was mistyped" in kept
    [row] = [n for n in rec.ok("status")["data"]["experiments"][0]["notes"] if n["id"] == first]
    assert row["retracted"]["why"] == "the row is 12, the line was mistyped"
    assert rec.ok("log")["data"]["entries"][-1]["op"] == "retract"

    # the SDK and MCP take a note back the same way
    at_the_users_terminal(rec, monkeypatch)
    rabbit_brain.open(rec.root).retract(later, why="said in the plan already")
    assert "The calibration set" not in rec.text("show", "int8")
    server = Mcp(root=rec.root, env={**rec.env, "RB_SESSION": "sess-notes-mcp"})
    try:
        server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "clientInfo": {"name": "notes-client"}}})
        answer = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                                "params": {"name": "retract", "arguments": {"subject": on_project, "why": "backfill is in the report"}}})
        assert not answer["result"].get("isError"), answer
    finally:
        server.close()
    assert "Older precisions" not in rec.text("status") and "Older precisions" not in rec.text("context")
    code = rec.run("retract", first, "--why", "again", "--json")
    assert code.returncode == 2 and "was already retracted" in json.loads(code.stdout)["errors"][0]["message"]


def test_a_gates_words_are_said_once_and_as_given_on_the_page(rec):
    """Round 6: the CLI printed a gate's words bare, then again as words (as given); the page printed them bare, so a
    session's first-person words stood on it with no mark of whose they are."""
    rec.ok("experiment", "gate", "int8", "--if-holds", GATE, "--words", GATE, agent=AGENT)
    said = f'if it holds: words (as given): "{GATE}"'
    status = rec.text("status")
    assert said in status and status.count(GATE) == 1
    assert said in rec.text("context")
    page = rec.page()
    assert f'words (as given): "{GATE}"' in page and f"if it holds: {GATE}" not in page
    # words that differ from the gate's text still follow it, as before
    rec.ok("experiment", "gate", "int8", "--if-holds", "ship INT8", "--words", GATE, agent=AGENT)
    assert f'if it holds: ship INT8 · words (as given): "{GATE}"' in rec.text("status")
    # the researcher's own gate: their words once in rb's text, and on a page neither tag nor quote. Words a session gave
    # the same read as given while the design waits: no line's time says whose they are now (review of the snapshot
    # rule, round 1); once the user confirms the design they are theirs
    rec.ok("experiment", "gate", "int8", "--if-holds", GATE, "--words", GATE)
    assert f'if it holds: words (as given): "{GATE}" · not confirmed' in rec.text("status")
    rec.ok(*design_confirm(rec, "int8").split()[1:])
    assert f'if it holds: your words: "{GATE}"' in rec.text("status")
    page, html = rec.page(), rec.html()
    # neither tag: "your words", nor the "words (as given)" a session's are said in (rb's "command (as given)" is no tag)
    assert f'<dt>if it holds</dt><dd><span class="au">{GATE}</span></dd>' in html and "your words" not in page and "words (as given)" not in page


def test_a_session_takes_back_only_a_note_a_session_wrote(rec, monkeypatch):
    """Review of round 6's build: a session ran rb retract on the researcher's own notes, and their words left every
    screen with no confirm. From the command line, the SDK and MCP alike, a session's retract of a note the user wrote
    is refused and writes nothing; its own note it takes back."""
    mine = rec.ok("note", "add", "-e", "int8", "The calibration set is the first 1000 validation images.")["data"]["object"]["id"]
    ours = rec.ok("note", "add", "Older precisions wait for backfill.")["data"]["object"]["id"]
    theirs = rec.ok("note", "add", "-e", "int8", "seed 2 looked odd", agent=AGENT)["data"]["object"]["id"]
    lines = len(rec.ok("log")["data"]["entries"])
    for note in (mine, ours):
        r = rec.run("retract", note, "--why", "not needed", "--json", agent=AGENT)
        err = json.loads(r.stdout)["errors"][0]
        assert r.returncode == 2 and err["code"] == "E_USAGE" and "a note the user wrote" in err["message"], err
    at_the_users_terminal(rec, monkeypatch)
    with pytest.raises(rabbit_brain.RBError):
        rabbit_brain.open(rec.root, agent="notebook-agent").retract(mine, why="not needed")
    server = Mcp(root=rec.root, env={**{k: v for k, v in rec.env.items() if k != "RB_ACTOR"}, **AGENT})
    try:
        server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "clientInfo": {"name": "notes-client"}}})
        answer = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "retract", "arguments": {"subject": ours, "why": "x"}}})
        assert answer["result"].get("isError"), answer
    finally:
        server.close()
    assert len(rec.ok("log")["data"]["entries"]) == lines
    assert "The calibration set" in rec.text("show", "int8") and "Older precisions" in rec.text("status")
    rec.ok("retract", theirs, "--why", "the seed was fine", agent=AGENT)
    assert "seed 2 looked odd" not in rec.text("show", "int8")


def test_a_gate_a_session_set_reads_as_not_confirmed_and_its_receipt_says_it_once(rec, monkeypatch):
    """Review of round 6's build: a session's gate given with no --words stood bare, first person, on every screen and
    the page, and rb experiment gate's receipt printed words given bare. A gate a session set ends `· not confirmed`,
    as its note does, on rb's text, a page and rb open; the receipt says the gate as rb status does, and under it the
    standing of the design it waits with, with the command that lists it to confirm (group E, recheck round 3)."""
    from rbtest import Open
    import urllib.request
    r = rec.run("experiment", "gate", "int8", "--if-holds", GATE, "--words", GATE, agent=AGENT)
    assert r.stdout.startswith(f'int8 gates: if it holds: words (as given): "{GATE}" · not confirmed\n  design not confirmed · list it to confirm: rb confirm int8\n'), r.stdout
    assert design_confirm(rec, "int8") not in r.stdout
    rec.ok("experiment", "add", "INT4 against FP32", "--id", "int4", "-q", "q1", "--baseline", "fp32", "--candidate", "int4", agent=AGENT)
    r = rec.run("experiment", "gate", "int4", "--if-holds", "I ship INT4 next week", "--if-not", "I keep FP32", agent=AGENT)
    line = "if it holds: I ship INT4 next week · if not: I keep FP32 · not confirmed"
    assert r.stdout.startswith(f"int4 gates: {line}\n  design not confirmed · list it to confirm: rb confirm int4\n"), r.stdout
    for where, text in {"status": rec.text("status"), "show": rec.text("show", "int4"), "context": rec.text("context")}.items():
        assert "I keep FP32 · not confirmed" in text, where
    assert "I keep FP32 · not confirmed" in " ".join(rec.page().split())
    at_the_users_terminal(rec, monkeypatch)
    server = Open(rec.root)
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(server.url + "/state.json", timeout=30) as resp:
            state = resp.read().decode("utf-8")
    finally:
        server.close()
    assert "I keep FP32 · not confirmed" in json.dumps(json.loads(state), ensure_ascii=False)
    # the researcher's own gate says nothing of the kind
    rec.ok("experiment", "gate", "int4", "--if-holds", "ship INT4", "--if-not", "keep FP32")
    status = rec.text("status")
    assert "if it holds: ship INT4 · if not: keep FP32" in status and "keep FP32 · not confirmed" not in status


def test_a_note_every_criterion_of_an_experiment_carries_is_said_once_for_it(rec):
    """Round 7: rb status ran to 36-39 KB, the same design-factor notes repeated on each of nine rules. A note every
    criterion of an experiment carries is said once, under `On every <exp> criterion:`, before its first, on rb status,
    rb context and rb show <exp>; a note one criterion alone carries stays on it, and --json keeps every finding's own."""
    rec.ok("criterion", "add", "INT8 reaches 0.75 top-1", "-e", "int8", "-q", "q1", "--metric", "top1", "--subject", "int8", "--at-least", "0.75",
           "--id", "c2")
    shared = "no run config was compared with the spec (record runs with --config)"
    for where in ("status", "context", "show int8"):
        lines = rec.text(*where.split()).splitlines()
        at = [i for i, line in enumerate(lines) if line.strip() == f"· {shared}"]
        head = [i for i, line in enumerate(lines) if line.strip() == "On every int8 criterion:"]
        first = min(i for i, line in enumerate(lines) if line.strip().startswith(("c1 ", "c2 ")))
        assert len(at) == 1 and len(head) == 1 and head[0] < at[0] < first, (where, at, head, first)
    findings = rec.ok("status")["data"]["findings"]
    assert all(shared in [c["text"] for c in f["caveats"]] for f in findings) and len(findings) == 2


def test_a_gate_a_session_set_is_the_users_on_one_rb_confirm(rec):
    """Round 7: a gate a session set read `· not confirmed` and no command made it the user's, so a report that asked
    for it to be confirmed pointed at nothing. Its line now gives `rb confirm <exp>`, as a call that waits does, a page
    included (round 8); that one confirmation, in the user's own terminal and refused in a session, makes the gate and its words
    theirs; rb confirm alone and --all list it after the calls. The experiment's file is unchanged. Group E: the gate
    is part of its experiment's design and waits with it: the command is the design's, its own view says the design's
    standing once, rb status, rb context and a page say the designs that wait in one line, and rb confirm lists the
    design part by part, the gate among them. Trial of group E: the gate's line gives no command of its own where its
    design waits with it, so the experiment waits in one place on each screen."""
    rec.ok("experiment", "gate", "int8", "--if-holds", GATE, "--words", GATE, agent=AGENT)
    rec.ok("decide", "c1", "accept", "--why", "INT8 holds on the first run", agent=AGENT)
    cmd = design_confirm(rec, "int8")
    gate = f'if it holds: words (as given): "{GATE}" · not confirmed'
    for where in ("status", "context"):
        text = rec.text(where)
        assert gate in text and cmd not in text and "The design of 1 experiment is not confirmed: rb confirm lists it." in text, where
    shown = rec.text("show", "int8")
    # its own view does not show every part the key names: it gives the command that lists it (recheck of group E)
    assert f"  design not confirmed · list it to confirm: rb confirm int8\n" in shown and f"  {gate}\n" in shown and cmd not in shown, shown
    # the page says it as status does: on its line, and, in the experiment's part, as the design's standing with the
    # command that lists it, once (round 8)
    page, html = rec.page(), rec.html()
    assert f'<dt>if it holds</dt><dd><span class="au">words (as given): "{GATE}" · not confirmed</span></dd>' in html, html
    assert cmd not in page and page.count("design not confirmed · list it to confirm: rb confirm int8") == 1, page
    assert "The design of 1 experiment is not confirmed: rb confirm lists it." in page and "Gates set, not confirmed" not in page, page
    assert "Confirm all" not in page, page
    listed = rec.text("confirm")
    assert listed.index("Waiting for a confirmation (1):") < listed.index("Designs waiting for a confirmation (1):\n  int8: INT8 against FP32\n")
    assert f"    gate: {gate}\n" in listed and f"    confirm: {cmd}\n" in listed and "Gates waiting" not in listed, listed
    env = rec.ok("confirm")
    assert [g["experiment"] for g in env["data"]["gates_pending"]] == ["int8"] and env["data"]["gates_pending"][0]["confirm"] == cmd
    assert [d["confirm"] for d in env["data"]["designs_pending"]] == [cmd]
    refused = rec.run("confirm", *cmd.split()[2:], "--json", agent=AGENT)
    assert refused.returncode != 0 and json.loads(refused.stdout)["errors"][0]["code"] == "E_USAGE"
    before = (rec.root / ".rb" / "experiments" / "int8" / "experiment.yaml").read_bytes()
    out = rec.text("confirm", "--all")
    assert out.index("Confirming 1 call:") < out.index("Confirming 1 design:")
    # review of group 10: the design it is confirming is listed as the calls are, with no command to run again
    assert f"Confirming 1 design:\n  int8: INT8 against FP32\n" in out and f"    gate: {gate}\n" in out and f"confirm: {cmd}" not in out, out
    assert f'int8\'s design is confirmed.\n  gate: if it holds: your words: "{GATE}"' in out
    assert (rec.root / ".rb" / "experiments" / "int8" / "experiment.yaml").read_bytes() == before
    for where in ("status", "context", "show int8"):
        text = rec.text(*where.split())
        assert f'if it holds: your words: "{GATE}"' in text and "rb confirm int8" not in text and "design not confirmed" not in text, where
    assert rec.ok("confirm")["data"]["gates_pending"] == [] and rec.ok("confirm")["data"]["designs_pending"] == []
    again = rec.run("confirm", "int8", "--json")
    assert json.loads(again.stdout)["errors"][0]["message"] == "int8's design waits for no confirmation."
    assert "design experiment int8 confirmed, with its gate" in rec.text("log")
    # a session's new gate waits again, with the design it changed
    rec.ok("experiment", "gate", "int8", "--if-not", "I keep FP32", agent=AGENT)
    status = rec.text("status")
    assert "I keep FP32 · not confirmed" in status and "The design of 1 experiment is not confirmed: rb confirm lists it." in status, status
    assert "design not confirmed · list it to confirm: rb confirm int8" in rec.text("show", "int8")
    assert '  changed since you confirmed it: the gate, was "if it holds: ' in rec.text("show", "int8")


@pytest.mark.xfail(strict=True, reason="4b gap (pins-4b.md), an old page pin that cannot pass on the shell; the fix ports it, owner R (views.rs): an experiment's notes are not drawn with their ids")
def test_a_note_is_named_by_its_id_in_its_receipt_its_log_line_rb_show_and_rb_open(rec, monkeypatch):
    """Review of round 6's build: the note receipt gave no id, rb show <note id> said nothing had it, the log said a
    note's retraction as one of its experiment, and rb open listed notes with no id."""
    from rbtest import Open
    import urllib.request
    r = rec.run("note", "add", "-e", "int8", "The calibration set is the first 1000 validation images.", agent=AGENT)
    [nid] = re.findall(r"^Note (n[a-z0-9]+) on int8: The calibration set", r.stdout, re.M)
    shown = rec.text("show", nid)
    assert shown.startswith(f"Note {nid} on int8 (") and "The calibration set is the first 1000 validation images. · not confirmed" in shown
    assert rec.ok("show", nid)["data"]["object"]["id"] == nid
    at_the_users_terminal(rec, monkeypatch)
    server = Open(rec.root)
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(server.url + "/", timeout=30) as resp:
            app = resp.read().decode("utf-8")
    finally:
        server.close()
    assert "note ${id}: ${n}" in app
    rec.ok("retract", nid, "--why", "said in the plan already", agent=AGENT)
    log = rec.text("log")
    assert f"retract note {nid} on experiment int8: note: said in the plan already" in log and "retract experiment int8" not in log
    assert f"retract note {nid} on experiment int8: note: said in the plan already" in rec.text("context")
    assert "retracted · note: said in the plan already · not confirmed" in rec.text("show", nid)


def test_a_criterions_statement_that_is_the_words_given_is_said_once_as_given(rec):
    """Review of round 6's build: a criterion whose statement was the chat's words, given again with --words, printed
    them bare and then as given on rb's text, and bare on a page. Words given that are the statement are said once,
    in its place, as given where a session gave them, on a page too; other words still follow it."""
    S = "If INT8 still costs most of a point, I stop spending GPU time on it."
    r = rec.run("criterion", "add", S, "-e", "int8", "-q", "q1", "--metric", "top1", "--subject", "int8", "--reference", "fp32",
                "--at-least", "-0.01", "--id", "c2", "--words", S, agent=AGENT)
    said = f'words (as given): "{S}"'
    assert r.stdout.startswith(f"c2: {said}\n"), r.stdout
    for where, text in {"status": rec.text("status"), "context": rec.text("context"), "show q1": rec.text("show", "q1"),
                        "show c2": rec.text("show", "c2")}.items():
        assert said in text and text.count(S) == text.count(said), where
    page = " ".join(rec.page().split())
    assert said in page and page.count(S) == page.count(said)
    rec.ok("criterion", "add", "INT8 loses at most 0.02 top-1", "-e", "int8", "--metric", "top1", "--subject", "int8", "--reference", "fp32",
           "--at-least", "-0.02", "--id", "c3", "--words", S, agent=AGENT)
    assert f"Criterion c3 on int8 (primary, own): INT8 loses at most 0.02 top-1 · {said}" in rec.text("show", "c3")


def test_an_adoption_makes_no_gate_or_note_the_users_that_they_did_not_write(rec):
    """Review of group 9: a session hand-edited the user's gate and adopted it with rb doctor --adopt, and the new gate
    read as the user's with no rb confirm; an adopted edit of the user's note read as theirs. A gate an adoption changes
    is now the adopter's, set with no words, so a session's waits for one rb confirm; an adoption that changes the words
    of another writer's note is refused (E_ADOPT_AUTHORITY). The user adopting their own edit is theirs."""
    exp = rec.root / ".rb" / "experiments" / "int8" / "experiment.yaml"
    rec.ok("experiment", "gate", "int8", "--if-holds", "ship INT8 next week", "--words", "ship it")
    exp.write_text(exp.read_text(encoding="utf-8").replace("ship INT8 next week", "ship INT4 instead"), encoding="utf-8")
    rec.ok("doctor", "--adopt", "--why", "tidy", agent=AGENT)
    cmd = design_confirm(rec, "int8")
    for where, waits in (("status", "The design of 1 experiment is not confirmed: rb confirm lists it."), ("show int8", "list it to confirm: rb confirm int8")):
        text = rec.text(*where.split())
        assert "if it holds: ship INT4 instead · not confirmed" in text and waits in text and "ship it" not in text, where
    assert [g["experiment"] for g in rec.ok("confirm")["data"]["gates_pending"]] == ["int8"]
    # an adoption that leaves the gate as it was changes nobody's gate, nor the design's standing
    rec.ok(*cmd.split()[1:])
    exp.write_text(exp.read_text(encoding="utf-8").replace("INT8 against FP32", "INT8 against the FP32 model"), encoding="utf-8")
    rec.ok("doctor", "--adopt", "--why", "tidy", agent=AGENT)
    assert rec.ok("confirm")["data"]["gates_pending"] == [] and rec.ok("confirm")["data"]["designs_pending"] == []
    nid = rec.ok("note", "add", "-e", "int8", "The calibration set is the first 1000 validation images.")["data"]["object"]["id"]
    note = exp.parent / "notes" / f"{nid}.yaml"
    note.write_text(note.read_text(encoding="utf-8").replace("first 1000", "first 5000"), encoding="utf-8")
    refused = rec.run("doctor", "--adopt", "--why", "tidy", "--json", agent=AGENT)
    err = json.loads(refused.stdout)["errors"][0]
    assert refused.returncode != 0 and err["code"] == "E_ADOPT_AUTHORITY", err
    assert f"{nid}.text: another writer's words, changed outside rb" in err["problems"], err
    rec.ok("doctor", "--adopt", "--why", "a typo in my note")
    shown = rec.text("show", "int8")
    assert "The calibration set is the first 5000 validation images." in shown
    assert "5000 validation images. · not confirmed" not in shown


def test_a_gate_key_an_earlier_rb_listed_is_refused_for_what_is_true(rec):
    """Trial of group E: group D's build listed a gate a session set as `rb confirm <exp>@<gate key>`; pasted into this
    build, it was refused as changed after it was listed, though nothing had changed. The refusal says the gate waits
    with its design now, lists the design and gives its command; nothing is confirmed, and no writer is named. A gate
    key of a gate changed since is still refused as changed."""
    rec.ok("experiment", "gate", "int8", "--if-holds", "ship INT8 next week", agent=AGENT)
    cmd = design_confirm(rec, "int8")
    r = rec.run(*confirm("int8", "ship INT8 next week").split()[1:], "--json")
    err = json.loads(r.stdout)["errors"][0]
    assert r.returncode != 0 and err["code"] == "E_OBJECT_INVALID", err
    assert err["message"] == "int8's gate waits with its design now, and is confirmed with it. It reads now:", err
    assert err["fix"] == f"To confirm it as it reads now: {cmd}" and "gate: if it holds: ship INT8 next week · not confirmed" in err["problems"], err
    text = rec.run(*confirm("int8", "ship INT8 next week").split()[1:])
    assert "changed after it was listed" not in text.stdout + text.stderr and "session" not in text.stdout + text.stderr
    assert design_confirm(rec, "int8") == cmd and rec.ok("confirm")["data"]["designs_pending"]
    rec.ok("experiment", "gate", "int8", "--if-holds", "ship INT8 in two weeks", agent=AGENT)
    old = confirm("int8", "ship INT8 next week")
    stale = json.loads(rec.run(*old.split()[1:], "--json").stdout)["errors"][0]
    assert stale["message"] == f"int8's design does not match {old.split('@')[1]}: it changed after it was listed, or rb never listed that key. It reads now:", stale


def test_a_gate_is_confirmed_only_as_it_was_listed_and_counted_with_the_calls(rec):
    """Review of group 9: rb confirm <experiment> confirmed the gate as it read when the user typed it, so a session
    that changed it after the listing had the new gate confirmed; `Confirm all 1: rb confirm --all` left out the gate
    --all also confirms; and a confirmed gate said nothing waits while a call on its experiment did. The listed
    command names the gate by a short hash (<experiment>@<hash>) and is refused once the gate changed; the gates are
    listed and counted with the calls; the refusal names the calls that still wait."""
    rec.ok("experiment", "gate", "int8", "--if-holds", "ship INT8 next week", agent=AGENT)
    cmd = design_confirm(rec, "int8")
    call = rec.ok("decide", "int8", "park", "--why", "later", agent=AGENT)["data"]["object"]["id"]
    # trial of group E: while a design waits, no line offers to confirm all, which would confirm a design no screen
    # listed; rb confirm lists it with the call, and its --all counts both (review of group 9)
    status = rec.text("status")
    assert "The design of 1 experiment is not confirmed: rb confirm lists it.\n" in status and "Confirm all" not in status, status
    assert "Confirm all" not in rec.text("context") and "Confirm all" not in rec.page()
    assert f"rb confirm {call} {cmd.split()[-1]} confirms 1 call and 1 design as listed." in rec.text("confirm")
    rec.ok("experiment", "gate", "int8", "--if-holds", "ship INT4 instead", agent=AGENT)
    now = design_confirm(rec, "int8")
    refused = rec.run(*cmd.split()[1:], "--json")
    err = json.loads(refused.stdout)["errors"][0]
    assert refused.returncode != 0 and err["message"] == f"int8's design does not match {cmd.split('@')[1]}: it changed after it was listed, or rb never listed that key. It reads now:", err
    assert "gate: if it holds: ship INT4 instead · not confirmed" in err["problems"], err
    assert err["fix"] == f"To confirm it as it reads now: {now}" and now != cmd
    # an earlier listing's command for the gate alone confirms nothing in the design's place
    gate_only = rec.run(*confirm("int8", "ship INT4 instead").split()[1:], "--json")
    assert gate_only.returncode != 0 and json.loads(gate_only.stdout)["errors"][0]["fix"] == f"To confirm it as it reads now: {now}"
    assert rec.ok("confirm")["data"]["gates_pending"][0]["if_holds"] == "ship INT4 instead"
    rec.ok(*now.split()[1:])
    again = json.loads(rec.run("confirm", "int8", "--json").stdout)["errors"][0]["message"]
    assert again == f"int8's design waits for no confirmation; the calls on it that wait: rb confirm {call}.", again


def test_a_sessions_reason_reads_as_its_note_on_rb_log_and_a_gate_confirmed_is_told(rec):
    """Review of group 9: rb log said a session's gate reason, which only the log keeps, in rb's voice, and once the
    user confirmed the gate the session was told only `gate experiment int8`. A session's reason on rb log and rb
    context reads as its note; the user's stays as given; the session is told the gate was confirmed."""
    rec.ok("experiment", "gate", "int8", "--if-holds", "ship INT8 next week", "--why", "docs/plan.md:6 gives the next step", agent=AGENT)
    rec.ok("status", agent=AGENT)
    log = rec.text("log")
    assert "gate experiment int8: note: docs/plan.md:6 gives the next step" in log, log
    assert "- " in rec.text("context") and "gate experiment int8: note: docs/plan.md:6 gives the next step" in rec.text("context")
    rec.ok("experiment", "gate", "int8", "--if-not", "keep FP32", "--why", "my own plan")
    assert "gate experiment int8: my own plan" in rec.text("log")
    rec.ok("experiment", "gate", "int8", "--if-not", "keep FP32 for now", agent=AGENT)
    rec.ok("show", "int8", agent=AGENT)
    rec.ok(*design_confirm(rec, "int8").split()[1:])
    told = [t["text"] for t in rec.ok("show", "int8", agent=AGENT)["rb_delta"]]
    assert "int8's design confirmed, with its gate: if it holds: ship INT8 next week · if not: keep FP32 for now" in told, told


def test_a_note_every_criterion_here_carries_is_said_once_on_the_questions_reading(rec):
    """Review of group 9: the question's reading on rb status, rb show <question> and rb context still said the notes
    nine Stage 1 rules share on every rule. A note every criterion of one experiment under the question carries is said
    once, before the first of them; --json keeps every sentence whole."""
    rec.ok("criterion", "add", "INT8 reaches 0.75 top-1", "-e", "int8", "-q", "q1", "--metric", "top1", "--subject", "int8", "--at-least", "0.75",
           "--id", "c2")
    shared = "no run config was compared with the spec (record runs with --config)"
    for where in ("status", "show q1", "context"):
        text = rec.text(*where.split())
        reading = re.split(r"\n(?:## )?Rules\n", text)[0] if where != "show q1" else text
        assert reading.count(shared) == 1, (where, reading)
        assert f"On every int8 criterion below: " in reading and reading.index("On every int8 criterion below: ") < reading.index("INT8 loses at most 0.01 top-1")
    q = next(x for x in rec.ok("status")["data"]["questions"] if x["id"] == "q1")
    assert sum(shared in line for line in q["inquiry"]["summary"]) == 2


def test_a_criterion_whose_shared_note_names_an_unverified_setting_never_reads_integrity_ok(rec):
    """Round 8: rb context printed 'integrity: ok' on each criterion while the block said once above them listed a
    provisional setting every one of them reads, or that no run config was compared. The criterion's line says what that
    block says applies to it, on rb context, rb status and rb show alike (rb status, which hides ok, leaves the config
    note to the block); with nothing unverified and a config compared it reads ok, as before."""
    rec.ok("criterion", "add", "INT8 reaches 0.75 top-1", "-e", "int8", "-q", "q1", "--metric", "top1", "--subject", "int8", "--at-least", "0.75",
           "--id", "c2")
    unchecked = "integrity: no run config was compared, as said above\n"
    for where in ("context", "show int8"):
        text = rec.text(*where.split())
        assert "no run config was compared with the spec (record runs with --config)" in text and text.count(unchecked) == 2, (where, text)
        assert "integrity: ok" not in text, where
    assert "integrity:" not in rec.text("status")
    rec.ok("spec", "set", "int8", "calibration", "imagenet-cal", "--no-verify")
    for where in ("context", "status", "show int8"):
        text = rec.text(*where.split())
        assert "On every int8 criterion:" in text and "calibration = imagenet-cal is provisional: no check of it is recorded" in text, where
        assert "integrity: ok" not in text, where
        assert text.count("integrity: settings it reads are not verified, as said above\n") == 2, (where, text)
    # one criterion alone: the note is its own, under it
    rec.ok("retract", "c2", "--why", "a level rule is not what the question asks")
    shown = rec.text("show", "c1")
    assert "integrity: settings it reads are not verified, as said below" in shown and "integrity: ok" not in shown, shown
    # the experiment's findings in --json keep integrity as it was: no problem, the note under caveats
    finding = next(f for f in rec.ok("status")["data"]["findings"] if f["criterion"] == "c1")
    assert finding["says"]["integrity"] == "ok" and "provisional" in [c["code"] for c in finding["caveats"]]
    # a run read again under this spec, with no config: both
    rec.ok("run", "add", "int8", "fp32.top1=0.760", "int8.top1=0.754")
    assert "integrity: settings it reads are not verified and no run config was compared, as said below\n" in rec.text("show", "c1")
    # a setting verified and a run whose config was compared with it: ok
    (rec.root / "cfg").mkdir()
    (rec.root / "cfg" / "fp16.json").write_text('{"lr": 0.1}\n', encoding="utf-8")
    rec.ok("experiment", "add", "FP16 against FP32", "--id", "fp16", "--baseline", "fp32", "--candidate", "fp16")
    rec.ok("spec", "set", "fp16", "lr", "0.1", "--source", "cfg/fp16.json#lr")
    rec.ok("criterion", "add", "FP16 loses at most 0.01 top-1", "-e", "fp16", "--metric", "top1", "--subject", "fp16", "--reference", "fp32",
           "--at-least", "-0.01", "--id", "c3")
    rec.ok("run", "add", "fp16", "fp32.top1=0.761", "fp16.top1=0.760", "--config", "cfg/fp16.json")
    assert "          integrity: ok\n" in rec.text("show", "c3")


def test_experiment_add_says_the_variants_it_made_and_a_varied_factor_left_the_same_is_said_at_once(rec, monkeypatch):
    """Round 8: rb experiment add --baseline/--candidate made the variants and said nothing a session saw, so it added
    them again; and 'varies dropout, but every variant has the same value' came only at rb status after the runs, so
    the runs were read again. The add says the variants it made, and the write that leaves a varied factor the same in
    every variant says it at once, on the command line, the SDK and MCP."""
    said = lambda e, f, what="every variant has the same value": (f"{e} declares it varies {f}, but {what}; give each variant its own: rb spec set {e} "
                                                                   f"<variant>.{f} <value> --source <file#key>")
    unset = "no variant has a value for it yet"         # review of group 10: none set yet is not one value shared
    out = rec.text("experiment", "add", "INT8 and FP16 against FP32", "--id", "prec", "--baseline", "fp32", "--candidate", "int8",
                   "--candidate", "fp16", "--varies", "precision")
    assert "  Made 3 variants: fp32 (baseline), int8 (candidate), fp16 (candidate); a setting of one is rb spec set prec fp32.<setting> <value>.\n" in out
    assert f"  {said('prec', 'precision', unset)}\n" in out and "same value" not in out, out
    assert rec.run("variant", "add", "prec", "fp32", "--role", "baseline").returncode == 2
    warned = lambda env: [w for w in env["data"].get("warnings", []) if "declares it varies" in w]
    assert warned(rec.ok("spec", "set", "prec", "precision", "int8", "--no-verify")) == [said("prec", "precision")]
    assert warned(rec.ok("spec", "set", "prec", "batch_size", "256", "--no-verify")) == []          # not the varied factor
    assert warned(rec.ok("spec", "set", "prec", "fp32.precision", "fp32", "--no-verify")) == []      # now it differs
    assert warned(rec.ok("spec", "vary", "prec", "calibration")) == [said("prec", "calibration", unset)]
    assert "Made" not in rec.text("experiment", "add", "A planned direction", "--id", "later")
    # the SDK says it as an RBWarning, MCP in data.warnings
    import warnings
    at_the_users_terminal(rec, monkeypatch)
    with warnings.catch_warnings(record=True) as told:
        warnings.simplefilter("always")
        rabbit_brain.open(rec.root).add_experiment("INT4 against FP32", id="int4", baseline="fp32", candidates=["int4"], varies=["precision"])
    assert [str(w.message) for w in told if issubclass(w.category, rabbit_brain.RBWarning)] == [said("int4", "precision", unset)]
    server = Mcp(root=rec.root, env={**rec.env, "RB_SESSION": "sess-notes-mcp-vary"})
    try:
        server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "clientInfo": {"name": "notes-client"}}})
        answer = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                                "params": {"name": "spec_set", "arguments": {"experiment": "int4", "name": "precision", "value": "int4",
                                                                             "no_verify": True}}})["result"]
    finally:
        server.close()
    assert said("int4", "precision") in answer["structuredContent"]["data"]["warnings"], answer


def test_a_gate_whose_branch_is_the_words_given_is_told_the_branch_is_the_action(rec, monkeypatch):
    """Round 9: a session passed the whole conditional sentence as --if-holds, so the gate's branch read only as words
    (as given). The write keeps the gate and says that --if-holds is the action, on rb experiment gate and rb experiment
    add, the SDK and MCP alike, each in its own names. Review: the sentence reworded, or given with no --words, is
    told too; a branch that is the action says nothing, even when the words given are that action alone."""
    import warnings
    told = "--if-holds holds a condition (\"if ...\"). --if-holds is the action, what is done when it holds; the whole sentence it was said in goes in --words."
    told_api = told.replace("--if-holds", "if_holds").replace("--words", "words")
    warned = lambda env: [w for w in env["data"].get("warnings", []) if "holds a condition" in w]
    env = rec.ok("experiment", "gate", "int8", "--if-holds", GATE, "--words", GATE, agent=AGENT)
    assert warned(env) == [told] and env["data"]["object"]["if_holds"] == GATE
    r = rec.run("experiment", "gate", "int8", "--if-holds", " " + GATE.lower(), "--words", GATE, agent=AGENT)
    assert told in r.stdout
    for branch in ("If INT8 still loses less than 0.01 top-1 then I ship it next week", "I ship it next week if INT8 still loses less than 0.01"):
        assert warned(rec.ok("experiment", "gate", "int8", "--if-holds", branch, "--words", GATE, agent=AGENT)) == [told]
    assert warned(rec.ok("experiment", "gate", "int8", "--if-holds", GATE, agent=AGENT)) == [told]
    assert warned(rec.ok("experiment", "gate", "int8", "--if-holds", "I ship INT8 next week", "--words", GATE, agent=AGENT)) == []
    assert warned(rec.ok("experiment", "gate", "int8", "--if-holds", "we ship INT8", "--words", "we ship INT8", agent=AGENT)) == []
    told_not = "--if-not holds a condition (\"if ...\"). --if-not is the action, what is done when it does not; the whole sentence it was said in goes in --words."
    assert warned(rec.ok("experiment", "gate", "int8", "--if-not", GATE, "--words", GATE, agent=AGENT)) == [told_not]
    env = rec.ok("experiment", "add", "INT8 at batch 64", "--id", "b64", "--baseline", "fp32", "--candidate", "int8", "--if-holds", GATE,
                 "--words", GATE, agent=AGENT)
    assert warned(env) == [told]
    at_the_users_terminal(rec, monkeypatch)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        rabbit_brain.open(rec.root).experiment("b64").gate(if_holds=GATE, words=GATE)
        rabbit_brain.open(rec.root).add_experiment("INT8 at batch 128", id="b128", baseline="fp32", candidates=["int8"], if_holds=GATE)
    assert [str(w.message) for w in caught if issubclass(w.category, rabbit_brain.RBWarning)] == [told_api, told_api]
    server = Mcp(root=rec.root, env={**rec.env, "RB_SESSION": "sess-notes-mcp-gate"})
    try:
        server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "clientInfo": {"name": "notes-client"}}})
        answer = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                                "params": {"name": "experiment_gate", "arguments": {"experiment": "b64", "if_holds": GATE, "words": GATE}}})["result"]
    finally:
        server.close()
    assert told_api in answer["structuredContent"]["data"]["warnings"], answer


def test_a_sessions_why_reads_as_its_note_on_every_screen_and_the_users_as_given(rec, monkeypatch):
    """Round 10: a session's experiment --why restated a file's claim as fact, and rb show, rb context and the page
    printed it as a plain `why:`. A why a session gave (an experiment's, a freeze's, an amendment's, a relation's note)
    reads as its note with what it rests on and `not confirmed`, naming no writer; the user's own stays as given; the
    record and --json keep the why as written."""
    from rbtest import home, object_card
    WHY = "results.csv row 12 gives INT8 0.755 top-1 against 0.761 for FP32"
    rec.ok("experiment", "add", "INT8 with a larger calibration set", "--id", "cal", "-q", "q1", "--baseline", "fp32", "--candidate", "int8",
           "--hypothesis", "1000 calibration images lose less top-1 than 256", "-m", WHY, agent=AGENT)
    rec.ok("experiment", "relate", "cal", "follows_from", "int8", "--note", "docs/plan.md:4 names it as the next step", agent=AGENT)
    rec.ok("freeze", "cal", "-m", "docs/plan.md:5 locks the rule before the runs", agent=AGENT)
    rec.ok("experiment", "add", "INT8 per channel", "--id", "chan", "-q", "q1", "--baseline", "fp32", "--candidate", "int8", "-m", "my own reason")
    said = f"note: {WHY} · not confirmed"
    show, context = rec.text("show", "cal"), rec.text("context")
    assert f"  {said}\n" in show and f"why: {WHY}" not in show, show
    assert "follows from int8: docs/plan.md:4 names it as the next step · not confirmed" in show
    assert " · note: docs/plan.md:5 locks the rule before the runs · not confirmed" in show
    assert f"- {said}\n" in context and f"why: {WHY}" not in context
    assert "- follows from int8: docs/plan.md:4 names it as the next step · not confirmed" in context
    assert "  why: my own reason\n" in rec.text("show", "chan") and "- why: my own reason\n" in context
    page, html = rec.page(), rec.html()
    # the session's why is drawn as its note (rb's "note" over the words, then its mark), the user's own as their why
    assert f'<dt>note</dt><dd><span class="au">{WHY} · not confirmed</span></dd>' in html
    assert f'<dt>why</dt><dd><span class="au">{WHY}' not in html and '<dt>why</dt><dd><span class="au">my own reason</span></dd>' in html
    at_the_users_terminal(rec, monkeypatch)
    # rb ui's and rb open's card, and rb open's experiment pane, which says what the brief says
    assert said in object_card("cal", rec.root).facts and "why: my own reason" in object_card("chan", rec.root).facts
    briefs = {e.id: e for b in home(rec.root).briefs for e in b.experiments}
    assert briefs["cal"].why_part == ["note", f"{WHY} · not confirmed"] and briefs["chan"].why_part == ["why", "my own reason"]
    assert 'at(x, "why_part")' in (Path(__file__).resolve().parents[1] / "rust" / "src" / "shell" / "views.rs").read_text(encoding="utf-8")
    # nothing on a screen names who wrote it, and --json keeps the why as written
    assert "claude" not in show.lower() and "agent" not in show
    assert rec.ok("show", "cal")["data"]["object"]["why"] == WHY


def test_a_sessions_supersedes_retraction_link_and_relation_reasons_read_as_its_note(rec, monkeypatch):
    """Review of round 10's fix: a session's --supersedes why and retraction reason still read in rb's computed lines
    ("superseded by an2: <why>", "retracted: <why>"), a relation's note on the card and the page had no "not
    confirmed", a question link's reason kept the plain "why:", and recent activity and the write's own echo said a
    session's reason plainly. Each reads as its note, not confirmed, naming no writer; the user's own as given."""
    from rbtest import object_card
    SUP, RET = "INT8 is better than we thought", "c2 repeats c1, so INT8 is fine"
    rec.ok("experiment", "add", "Width", "--id", "wid", "-q", "q1", "--baseline", "w1", "--candidate", "w2")
    rec.ok("criterion", "add", "INT8 loses at most 0.02 top-1", "-e", "int8", "-q", "q1", "--metric", "top1", "--subject", "int8",
           "--reference", "fp32", "--at-least", "-0.02", "--id", "c2")
    rec.ok("criterion", "add", "INT8 loses at most 0.03 top-1", "-e", "int8", "--metric", "top1", "--subject", "int8",
           "--reference", "fp32", "--at-least", "-0.03", "--id", "c3")
    said = rec.run("experiment", "relate", "int8", "extends", "wid", "--note", "docs/plan.md:3 says width settles INT8", agent=AGENT).stdout
    assert said.startswith("int8 extends wid: docs/plan.md:3 says width settles INT8 · not confirmed\n"), said
    rec.ok("analysis", "add", "int8", "--compare", "int8/fp32:top1=-0.006[-0.01,-0.002]", "--id", "an1", agent=AGENT)
    said = rec.run("analysis", "add", "int8", "--compare", "int8/fp32:top1=-0.004[-0.008,0.0]", "--id", "an2", "--supersedes", "an1",
                   "-m", SUP, agent=AGENT).stdout
    assert f"  supersedes an1 · note: {SUP} · not confirmed. Their readings stay as history." in said, said
    said = rec.run("retract", "c2", "-m", RET, agent=AGENT).stdout
    assert said.startswith(f"c2 (criterion) retracted · note: {RET} · not confirmed. It stays"), said
    rec.ok("criterion", "file", "c3", "q1", "--when-met", "INT8 is good enough for serving", "-m", "docs/calls.md:4 pauses INT8 only for now", agent=AGENT)
    said = rec.run("experiment", "add", "INT8 per channel", "--id", "chan", "-q", "q1", "--baseline", "fp32", "--candidate", "int8",
                   "--hypothesis", "per channel loses less", "-m", "docs/plan.md:6 names per channel next", agent=AGENT).stdout
    assert "  hypothesis: per channel loses less (note: docs/plan.md:6 names per channel next · not confirmed)\n" in said, said
    sup, ret = f"superseded by an2 · note: {SUP} · not confirmed", f"retracted · note: {RET} · not confirmed"
    status, context, page = rec.text("status"), rec.text("context"), rec.page()
    show_c1, show_an2, show_c2 = rec.text("show", "c1"), rec.text("show", "an2"), rec.text("show", "c2")
    for where, text in {"show c1": show_c1, "status": status, "context": context, "page": page}.items():
        assert sup in text and f"superseded by an2: {SUP}" not in text, where
    assert f"  supersedes an1 · note: {SUP} · not confirmed\n" in show_an2
    assert f"supersedes an1 · note: {SUP} · not confirmed" in context
    assert "RETRACTED " in show_c2 and f" · note: {RET} · not confirmed\n" in show_c2 and f"+00:00: {RET}" not in show_c2
    assert f"INT8 loses at most 0.02 top-1 · {ret}" in status and f"retracted: {RET}" not in status
    link = "declared when met, not confirmed: 'INT8 is good enough for serving'; note: docs/calls.md:4 pauses INT8 only for now"
    assert link in status and link in context and "; why: docs/calls.md:4" not in status + context + page
    assert f"retract criterion c2: note: {RET} · not confirmed\n" in context
    assert "extends: wid (docs/plan.md:3 says width settles INT8 · not confirmed)" in page
    at_the_users_terminal(rec, monkeypatch)
    facts = object_card("int8", rec.root).facts
    assert "extends: wid (docs/plan.md:3 says width settles INT8 · not confirmed)" in facts, facts
    assert any(f.endswith(f"supersedes an earlier one · note: {SUP} · not confirmed") for f in facts), facts
    # the user's own reasons read as given, and --json keeps every reason as written
    rec.ok("retract", "c3", "-m", "my own reason")
    assert "retracted: my own reason" in rec.text("status")
    assert rec.ok("show", "an2")["data"]["object"]["why"] == SUP
    assert "claude" not in (status + context + page).lower()
