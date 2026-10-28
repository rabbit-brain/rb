"""An experiment's design (Oct 13): what it records of how it tests its question. While a session wrote any part of it
and the user has not confirmed it, its own views say `design not confirmed` once, with the one command that confirms
it, and what it does not record; rb status, rb context and a page's summary say every such design in one line. The user
confirms it, as rb confirm listed it, in their own terminal; a session's later change reopens it, and its own views say
what changed. A design written wholly in the user's own terminal carries no mark, and no confirmation changes a reading.

Its standing is a snapshot rule (Oct 14): a design a session wrote any part of reads confirmed only while it is exactly
a design the user owns, one they confirmed or made from one they owned with their own writes, as the lines of those
writes keep it. A merge, a clone, an older rb or a clock can only make rb ask again; once a session touched a design it
waits for one confirmation, and a user's write to a design that waits does not make it theirs.

Every name, number and session id here is made up.
"""
import hashlib
import html as html_mod
import json
import re
import shutil
import subprocess
import urllib.request
from pathlib import Path

import pytest

import rabbit_brain
from rbtest import AGENT_VARS, Mcp, Open, command, environment

AGENT = {"CLAUDECODE": "1", "CLAUDE_CODE_SESSION_ID": "sess-design-7c2e"}
HYPOTHESIS = "INT8 keeps ResNet-50 top-1 within 0.5 points of FP32 on ImageNet"
# int8's own views do not show every part its key names: they give the command that lists its design to confirm
LISTING = "design not confirmed · list it to confirm: rb confirm int8"


class Rec:
    """A git repository with a record, whose user is human:ana at their own terminal; `agent=` runs rb in a session."""

    def __init__(self, root: Path, home: Path) -> None:
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        self.env = {k: v for k, v in environment().items() if k not in AGENT_VARS}
        cfg = home / "gitconfig"
        cfg.write_text("[user]\n\temail = ana@example.com\n\tname = ana\n", encoding="utf-8", newline="")
        self.env.update(RB_TELEMETRY="0", DO_NOT_TRACK="1", HOME=str(home), USERPROFILE=str(home), GIT_CONFIG_GLOBAL=str(cfg), GIT_CONFIG_NOSYSTEM="1")
        subprocess.run(["git", "init", "-q"], cwd=root, env=self.env, check=True)

    def run(self, *argv: str, agent: bool = False) -> subprocess.CompletedProcess:
        env = {**self.env, **(AGENT if agent else {})}
        return subprocess.run([*command(), *argv], cwd=self.root, env=env, capture_output=True, stdin=subprocess.DEVNULL, encoding="utf-8")

    def ok(self, *argv: str, agent: bool = False) -> dict:
        r = self.run(*argv, "--json", agent=agent)
        env = json.loads(r.stdout)
        assert r.returncode in (0, 1) and env["ok"], (argv, env["errors"])
        return env

    def text(self, *argv: str) -> str:
        r = self.run(*argv)
        assert r.returncode in (0, 1), (argv, r.stdout, r.stderr)
        return r.stdout

    def write(self, rel: str, text: str) -> None:
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8", newline="")

    def commit(self) -> None:
        subprocess.run(["git", "add", "-A"], cwd=self.root, env=self.env, check=True)
        subprocess.run(["git", "commit", "-qm", "work"], cwd=self.root, env=self.env, check=True)

    def page(self) -> str:
        out = self.root / "page.html"
        self.ok("page", "--out", str(out))
        return html_mod.unescape(re.sub(r"<[^>]+>", "\n", out.read_text(encoding="utf-8")))

    def design(self, exp: str) -> dict:
        [e] = [e for e in self.ok("status")["data"]["experiments"] if e["id"] == exp]
        return e["design"]

    def readings(self) -> str:
        """Every finding and criterion reading as --json gives them: what no confirmation may change."""
        st = self.ok("status")["data"]
        shown = {f["criterion"]: self.ok("show", f["criterion"])["data"]["finding"] for f in st["findings"]}
        return json.dumps({"findings": st["findings"], "shown": shown, "tally": st["tally"]}, sort_keys=True, ensure_ascii=False)

    def state(self, monkeypatch) -> dict:
        """rb open's /state.json, served as the user starts it."""
        monkeypatch.chdir(self.root)
        for k, v in self.env.items():
            monkeypatch.setenv(k, v)
        for k in AGENT_VARS:
            monkeypatch.delenv(k, raising=False)
        server = Open(self.root)
        try:
            with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(server.url + "/state.json", timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        finally:
            server.close()


def brief(state: dict, exp: str) -> dict:
    return next(e for b in state["home"]["briefs"] for e in b["experiments"] if e["id"] == exp)


@pytest.fixture
def rec(tmp_path, tmp_path_factory) -> Rec:
    r = Rec(tmp_path / "work", tmp_path_factory.mktemp("home"))
    r.write("configs/train.yaml", "optim:\n  lr: 0.001\n  wd: 0.0001\ndata:\n  batch_size: 256\n")
    r.write("configs/fp32.yaml", "precision: fp32\n")
    r.write("configs/int8.yaml", "precision: int8\n")
    r.write("out/seed1/config.yaml", "optim:\n  lr: 0.01\n  wd: 0.0001\ndata:\n  batch_size: 256\n")
    r.commit()
    r.ok("init", "INT8 ResNet-50", "--no-agents")
    r.ok("question", "add", "Does INT8 cost ResNet-50 top-1 on ImageNet?", "--id", "q-acc")
    r.ok("metric", "add", "top1", "--maximize")
    return r


MARKS = ("design not confirmed", "not recorded", "changed since you confirmed it", "The design of")
# On a page, a design's part not recorded reads "<part>: not recorded"; the page also says a run's command was not
# recorded ("command (as given) · not recorded"), which is no mark of a design's.
PAGE_MARKS = ("design not confirmed", ": not recorded", "changed since you confirmed it", "changed since it was confirmed", "The design of")


def test_a_design_stated_in_the_users_own_terminal_carries_no_mark(rec, monkeypatch):
    """Case (a): every part of the design written in the user's own terminal, one experiment with no why and no
    hypothesis among them: no standing line, no `not recorded`, nothing waits, on any view; the data says `yours`."""
    rec.ok("experiment", "add", "INT8 vs FP32", "--id", "int8", "-q", "q-acc", "--hypothesis", HYPOTHESIS, "-m", "the calibration set matches the eval set",
           "--baseline", "fp32", "--candidate", "int8", "--varies", "precision", "--if-holds", "ship INT8", "--if-not", "keep FP32")
    rec.ok("spec", "set", "int8", "--from", "configs/train.yaml", "--keys", "optim.*,data.batch_size")
    rec.ok("spec", "set", "int8", "fp32.precision", "--source", "configs/fp32.yaml#precision")
    rec.ok("spec", "set", "int8", "int8.precision", "--source", "configs/int8.yaml#precision")
    rec.ok("criterion", "add", "INT8 loses at most 0.5 top-1", "-e", "int8", "-q", "q-acc", "--metric", "change.top1", "--at-least", "-0.005",
           "--id", "acc-tol", "--when-met", "INT8 stays within the accuracy budget")
    rec.ok("experiment", "gate", "int8", "--if-holds", "ship INT8 next week", "--words", "I ship INT8 next week")
    rec.ok("freeze", "int8", "-m", "before the runs")
    rec.ok("run", "add", "int8", "fp32.top1=0.761", "int8.top1=0.758")
    rec.ok("experiment", "add", "Batch size probe", "--id", "bsz")
    for argv in (("status",), ("context",), ("show", "int8"), ("show", "bsz"), ("confirm",), ("log",)):
        text = rec.text(*argv)
        assert not [m for m in MARKS if m in text], (argv, text)
    assert rec.text("confirm") == "Nothing waits for a confirmation.\n"
    page = rec.page()
    assert not [m for m in PAGE_MARKS if m in page]
    st = rec.ok("status")["data"]
    assert st["designs_pending"] == [] and st["counts"]["design_pending_count"] == 0
    for e in st["experiments"]:
        d = e["design"]
        assert (d["standing"], d["pending"], d["confirm"], d["changes"], d["not_recorded"], d["confirmed"]) == ("yours", False, "", [], [], None), d
    assert [p["key"] for p in rec.design("int8")["parts"]][:3] == ["questions", "hypothesis", "why"]
    state = rec.state(monkeypatch)
    for exp in ("int8", "bsz"):
        assert brief(state, exp)["design"] == {"standing": "yours", "pending": False, "confirm": "", "lines": [], "not_recorded": []}
    assert state["home"]["pending"] == []
    refused = json.loads(rec.run("confirm", "bsz", "--json").stdout)["errors"][0]["message"]
    assert refused == "bsz's design waits for no confirmation."


def test_a_design_a_session_rebuilt_from_configs_says_its_standing_once_and_what_it_does_not_record(rec, monkeypatch):
    """Case (b): a session reads the configs with rb compile, which writes nothing, and records the design from them
    with no why: its own views say `design not confirmed` once with the command, `why: not recorded`, and each setting
    with the file it came from; rb status, rb context and a page's summary say it in one line."""
    assert rec.run("compile", "configs", agent=True).returncode == 0
    rec.ok("experiment", "add", "INT8 vs FP32", "--id", "int8", "-q", "q-acc", "--hypothesis", HYPOTHESIS, "--baseline", "fp32", "--candidate", "int8",
           "--varies", "precision", agent=True)
    rec.ok("spec", "set", "int8", "--from", "configs/train.yaml", "--keys", "optim.*,data.batch_size", agent=True)
    rec.ok("spec", "set", "int8", "fp32.precision", "--source", "configs/fp32.yaml#precision", agent=True)
    rec.ok("spec", "set", "int8", "int8.precision", "--source", "configs/int8.yaml#precision", agent=True)
    rec.ok("criterion", "add", "INT8 loses at most 0.5 top-1", "-e", "int8", "-q", "q-acc", "--metric", "change.top1", "--at-least", "-0.005",
           "--id", "acc-tol", agent=True)
    d = rec.design("int8")
    cmd = d["confirm"]
    assert (d["standing"], d["pending"], d["not_recorded"], d["changes"]) == ("not_confirmed", True, ["why"], []) and re.fullmatch(r"rb confirm int8@[0-9a-f]{64}", cmd)
    assert {"key": "source:optim.lr", "part": "setting optim.lr's source", "value": "configs/train.yaml#optim.lr"} in d["parts"]
    line = "The design of 1 experiment is not confirmed: rb confirm lists it."
    status, context = rec.text("status"), rec.text("context")
    assert status.count(line) == 1 and context.count(line) == 1 and "design not confirmed" not in status + context
    shown = rec.text("show", "int8").splitlines()
    assert shown[1] == f"  {LISTING}" and "  why: not recorded" in shown and not [x for x in shown if cmd in x], shown
    assert "hypothesis or purpose: not recorded" not in "\n".join(shown)
    assert any(x.strip().startswith("optim.lr") and x.endswith("configs/train.yaml#optim.lr") for x in shown), shown
    page = rec.page()
    assert page.count(LISTING) == 1 and "why: not recorded" in page and page.count(line) == 1
    listed = rec.text("confirm", "int8")
    assert listed.startswith("Design waiting for a confirmation (1):\n  int8: INT8 vs FP32\n") and f"    confirm: {cmd}\n" in listed
    for part in ("why: not recorded", "setting optim.lr: 0.001 from configs/train.yaml#optim.lr", "criterion acc-tol (primary): change.top1 ≥ -0.005 · for q-acc"):
        assert f"    {part}\n" in listed, (part, listed)
    assert rec.design("int8")["standing"] == "not_confirmed"      # listing it confirms nothing
    state = rec.state(monkeypatch)
    b = brief(state, "int8")["design"]
    assert (b["standing"], b["confirm"], b["lines"], b["not_recorded"]) == ("not_confirmed", cmd, [LISTING], ["why"])
    [row] = state["home"]["pending"]
    assert (row["kind"], row["confirm"]) == ("design", cmd) and "setting optim.lr: 0.001 from configs/train.yaml#optim.lr" in row["lines"]


def test_a_design_a_session_ran_on_another_config_is_confirmed_once_and_reopened_by_its_changes(rec, monkeypatch):
    """Case (c): a session makes the design and runs it on a config that disagrees with the spec. The run is not
    counted, as before, and the design is not confirmed. The user confirms it as listed, never a session. A session's
    later change of a criterion's rule and of a setting's value reopens it, said with what each was and is; the user's
    change of the gate does not, and a key listed before it is refused. A session's change taken back closes it again.
    No confirmation changes a finding or a reading."""
    rec.ok("experiment", "add", "INT8 vs FP32", "--id", "int8", "-q", "q-acc", "--hypothesis", HYPOTHESIS, "-m", "docs/plan.md:3 names INT8",
           "--baseline", "fp32", "--candidate", "int8", "--if-holds", "ship INT8", "--if-not", "keep FP32", agent=True)
    rec.ok("spec", "set", "int8", "optim.lr", "--source", "configs/train.yaml#optim.lr", agent=True)
    rec.ok("criterion", "add", "INT8 loses at most 0.5 top-1", "-e", "int8", "-q", "q-acc", "--metric", "change.top1", "--at-least", "-0.005",
           "--id", "acc-tol", agent=True)
    run = rec.ok("run", "add", "int8", "fp32.top1=0.761", "int8.top1=0.758", "--config", "out/seed1/config.yaml", agent=True)["data"]["object"]["id"]
    rec.ok("run", "add", "int8", "fp32.top1=0.760", "int8.top1=0.757", agent=True)
    shown = rec.text("show", "int8")
    first = rec.design("int8")["confirm"]
    assert f"  {LISTING}\n" in shown and f"{run} not counted: ran with optim.lr 0.01 (the spec says 0.001)" in shown, shown
    assert "  note: docs/plan.md:3 names INT8 · not confirmed\n" in shown and "if not: keep FP32 · not confirmed" in shown
    readings = rec.readings()
    refused = rec.run(*first.split()[1:], "--json", agent=True)
    assert refused.returncode != 0 and json.loads(refused.stdout)["errors"][0]["code"] == "E_USAGE"
    assert json.loads(refused.stdout)["errors"][0]["message"].startswith("rb confirm makes a call or a design the user's own, and this is an agent session")
    out = rec.text(*first.split()[1:])
    assert out.startswith("int8's design is confirmed.\n  gate: if it holds: ship INT8 · if not: keep FP32\n"), out
    d = rec.design("int8")
    assert (d["standing"], d["pending"], d["confirm"], d["changes"]) == ("confirmed", False, "", []) and d["confirmed"]["by"] == "human:ana"
    assert rec.readings() == readings
    shown = rec.text("show", "int8")
    assert not [m for m in ("design not confirmed", "· not confirmed", "rb confirm") if m in shown], shown
    assert "  note: docs/plan.md:3 names INT8\n" in shown and "  why: docs/plan.md:3" not in shown      # still the session's note
    assert "The design of" not in rec.text("status") and "design experiment int8 confirmed, with its gate" in rec.text("log")
    # a session changes a criterion's rule (an edit it adopts) and a setting's value: both reopen it, with was and now
    crit = rec.root / ".rb" / "experiments" / "int8" / "criteria" / "acc-tol.yaml"
    crit.write_text(crit.read_text(encoding="utf-8").replace("target: -0.005", "target: -0.003"), encoding="utf-8", newline="")
    rec.ok("doctor", "--adopt", "--why", "the plan says 0.3", agent=True)
    rec.write("configs/train.yaml", "optim:\n  lr: 0.002\n  wd: 0.0001\ndata:\n  batch_size: 256\n")
    rec.ok("spec", "set", "int8", "optim.lr", "--source", "configs/train.yaml#optim.lr", agent=True)
    changes = ["changed since you confirmed it: criterion acc-tol's rule, was change.top1 ≥ -0.005, now change.top1 ≥ -0.003",
               "changed since you confirmed it: setting optim.lr, was 0.001, now 0.002"]
    d = rec.design("int8")
    assert d["standing"] == "changed" and sorted((c["part"], c["change"], c["was"], c["now"]) for c in d["changes"]) == [
        ("criterion acc-tol's rule", "changed", "change.top1 ≥ -0.005", "change.top1 ≥ -0.003"), ("setting optim.lr", "changed", "0.001", "0.002")]
    second = d["confirm"]
    shown = rec.text("show", "int8")
    assert f"  {LISTING}\n" in shown and all(f"  {c}\n" in shown for c in changes), shown
    page = rec.page()
    # a page anyone may be sent says them naming no reader
    assert all(c.replace("since you confirmed it", "since it was confirmed") in page for c in changes)
    assert page.count("The design of 1 experiment is not confirmed: rb confirm lists it.") == 1
    assert rec.text("status").count("The design of 1 experiment is not confirmed: rb confirm lists it.") == 1
    lines = brief(rec.state(monkeypatch), "int8")["design"]["lines"]
    assert lines[0] == LISTING and sorted(lines[1:]) == sorted(changes)
    # the user's own change of the gate while the design waits makes no design theirs: it is said with the rest, against
    # what they confirmed, and changes what a confirmation names (snapshot rule, Oct 14); the gate a session set, no longer
    # as the user owns it, waits with its design (review of the snapshot rule, round 1)
    rec.ok("experiment", "gate", "int8", "--if-not", "keep FP32 for now")
    d = rec.design("int8")
    assert [c["part"] for c in d["changes"]] == ["setting optim.lr", "criterion acc-tol's rule", "the gate"] and d["confirm"] != second, d["changes"]
    stale = rec.run(*second.split()[1:], "--json")
    err = json.loads(stale.stdout)["errors"][0]
    assert stale.returncode != 0 and err["code"] == "E_OBJECT_INVALID" and err["message"] == f"int8's design does not match {second.split('@')[1]}: it changed after it was listed, or rb never listed that key. It reads now:"
    assert err["fix"] == f"To confirm it as it reads now: {d['confirm']}" and "gate: if it holds: ship INT8 · if not: keep FP32 for now · not confirmed" in err["problems"]
    readings = rec.readings()
    # rb confirm --json says the design it confirmed as it reads once confirmed (review of group E)
    [done] = rec.ok(*d["confirm"].split()[1:])["data"]["designs_confirmed"]
    assert (done["standing"], done["confirm"], done["key"]) == ("confirmed", "", d["key"]), done
    assert rec.design("int8")["standing"] == "confirmed" and rec.readings() == readings
    # a session's criterion added reopens it; the same session taking it back leaves the design as confirmed
    rec.ok("criterion", "add", "INT8 keeps 0.75 top-1", "-e", "int8", "--metric", "int8.top1", "--at-least", "0.75", "--id", "c9", agent=True)
    assert [(c["part"], c["change"], c["now"]) for c in rec.design("int8")["changes"]] == [
        ("criterion c9's rule", "added", "int8.top1 ≥ 0.75"), ("criterion c9's role", "added", "primary")]
    assert "  changed since you confirmed it: criterion c9 added: int8.top1 ≥ 0.75 (primary)\n" in rec.text("show", "int8")
    rec.ok("retract", "c9", "--why", "a duplicate of acc-tol", agent=True)
    assert rec.design("int8")["standing"] == "confirmed" and "design not confirmed" not in rec.text("show", "int8")


def test_eleven_experiments_a_session_imported_wait_in_one_line(rec):
    """A record built by an import with many experiments: rb status, rb context and a page's summary say the designs
    that wait in one line, never one per experiment; rb confirm lists each, and ends with the one command that
    confirms them all as listed; --all confirms each as it reads when it runs."""
    for n in range(11):
        rec.ok("experiment", "add", f"Calibration size {n}", "--id", f"cal{n}", "-q", "q-acc", "--purpose", f"top-1 at calibration size {n}", agent=True)
    line = "The design of 11 experiments is not confirmed: rb confirm lists them."
    status, context, page = rec.text("status"), rec.text("context"), rec.page()
    for where, text in (("status", status), ("context", context)):
        assert text.count(line) == 1 and "design not confirmed" not in text, where
        # one line, and no second one counting them again for rb confirm --all (review of group E)
        assert "Confirm all" not in text, (where, text)
    assert "Confirm all" not in page
    # a page's summary says the line once; each experiment's own part of it says its standing once
    assert page.count(line) == 1 and page.count("design not confirmed · list it to confirm: rb confirm cal") == 11
    assert rec.ok("status")["data"]["counts"]["design_pending_count"] == 11
    listed = rec.text("confirm")
    assert listed.startswith("Designs waiting for a confirmation (11):\n") and listed.count("    why: not recorded\n") == 11
    every = " ".join(rec.design(f'cal{n}')['confirm'].split()[-1] for n in range(11))
    assert listed.rstrip("\n").endswith(f"rb confirm <experiment>@<hash> confirms one; rb confirm {every} confirms these 11 as listed.")
    out = rec.text("confirm", "--all")
    assert out.startswith("Confirming 11 designs:\n") and out.count("'s design is confirmed.") == 11
    assert "The design of" not in rec.text("status") and rec.ok("status")["data"]["designs_pending"] == []


def test_once_the_user_confirms_a_design_a_sessions_reasons_on_it_read_as_its_notes_and_its_gate_as_theirs(rec):
    """After the confirmation, a session's why on the experiment, on its freeze and on an amendment reads as its note,
    with no `not confirmed`, as a confirmed call's reason does; its gate is the user's. A session that changes the gate
    and then sets it back as it was confirmed leaves the design confirmed, and the gate waits nowhere on its own."""
    rec.ok("experiment", "add", "INT8 vs FP32", "--id", "int8", "-q", "q-acc", "--hypothesis", HYPOTHESIS, "-m", "docs/plan.md:3 names INT8",
           "--baseline", "fp32", "--candidate", "int8", "--varies", "precision", agent=True)
    rec.ok("experiment", "gate", "int8", "--if-holds", "ship INT8", "--words", "I ship INT8 if it holds", agent=True)
    rec.ok("spec", "set", "int8", "fp32.precision", "--source", "configs/fp32.yaml#precision", agent=True)
    rec.ok("freeze", "int8", "-m", "docs/plan.md:5 locks the rule", agent=True)
    rec.ok("spec", "set", "int8", "int8.precision", "--source", "configs/int8.yaml#precision", "--amend", "--why", "docs/plan.md:6 adds it", agent=True)
    shown = rec.text("show", "int8")
    for said in ("  note: docs/plan.md:3 names INT8 · not confirmed\n", " · note: docs/plan.md:5 locks the rule · not confirmed",
                 " · note: docs/plan.md:6 adds it · not confirmed", 'words (as given): "I ship INT8 if it holds" · not confirmed'):
        assert said in shown, (said, shown)
    # what the confirmation covers is what rb confirm lists: the freeze's and the amendment's reasons among it (review
    # of group E: it listed only `frozen <day> · 1 amendment`)
    listed = rec.text("confirm", "int8")
    assert re.search(r"\n    frozen \d{4}-\d\d-\d\dT[^\s]+ · note: docs/plan\.md:5 locks the rule · not confirmed\n", listed), listed
    assert re.search(r"\n    amended \d{4}-\d\d-\d\d: setting int8\.precision: new, int8 · note: docs/plan\.md:6 adds it · not confirmed\n", listed), listed
    rec.ok(*rec.design("int8")["confirm"].split()[1:])
    shown = rec.text("show", "int8")
    assert "not confirmed" not in shown and "rb confirm" not in shown, shown
    for said in ("  note: docs/plan.md:3 names INT8\n", " · note: docs/plan.md:5 locks the rule", " · note: docs/plan.md:6 adds it",
                 'if it holds: ship INT8 · your words: "I ship INT8 if it holds"'):
        assert said in shown, (said, shown)
    assert "  why: docs/plan.md:3" not in shown and "because: docs/plan.md:6" not in shown
    rec.ok("experiment", "gate", "int8", "--if-holds", "ship INT8 next week", "--words", "I ship INT8 if it holds", agent=True)
    assert [c["part"] for c in rec.design("int8")["changes"]] == ["the gate"] and len(rec.ok("status")["data"]["gates_pending"]) == 1
    rec.ok("experiment", "gate", "int8", "--if-holds", "ship INT8", "--words", "I ship INT8 if it holds", agent=True)
    st = rec.ok("status")["data"]
    assert [e["design"]["standing"] for e in st["experiments"]] == ["confirmed"] and st["gates_pending"] == [] and st["designs_pending"] == []
    shown = rec.text("show", "int8")
    assert "not confirmed" not in shown and 'if it holds: ship INT8 · your words: "I ship INT8 if it holds"' in shown, shown
    assert rec.text("confirm") == "Nothing waits for a confirmation.\n"


def test_mcp_and_the_sdk_carry_a_designs_standing_and_its_command(rec):
    """An experiment a session made over MCP: the status tool and the SDK give its design's standing, the command that
    confirms it and the parts it is made of; the count is in rb status --json; nothing confirms one but rb confirm."""
    server = Mcp(root=rec.root, env={**rec.env, **AGENT})
    try:
        server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "clientInfo": {"name": "design-client"}}})
        made = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "experiment_add", "arguments": {
            "title": "INT8 vs FP32", "id": "int8", "question": ["q-acc"], "hypothesis": HYPOTHESIS, "baseline": "fp32", "candidate": ["int8"]}}})
        assert not made["result"].get("isError"), made
        tools = server.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/list"})["result"]["tools"]
        assert not [t["name"] for t in tools if "confirm" in t["name"]]
        answer = server.handle({"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "status", "arguments": {}}})["result"]
    finally:
        server.close()
    data = answer["structuredContent"]["data"]
    [e] = data["experiments"]
    assert (e["design"]["standing"], e["design"]["pending"], e["design"]["not_recorded"]) == ("not_confirmed", True, ["why"])
    assert data["counts"]["design_pending_count"] == 1 and [d["confirm"] for d in data["designs_pending"]] == [e["design"]["confirm"]]
    st = rabbit_brain.open(rec.root).status()
    assert st["experiments"][0]["design"]["confirm"] == e["design"]["confirm"]
    assert {"key": "hypothesis", "part": "hypothesis", "value": HYPOTHESIS} in st["experiments"][0]["design"]["parts"]


# Review of group E: what a design's standing says must be true of the record. Each case below said something untrue.

def session_design(rec: Rec) -> None:
    """int8, made in a session: a hypothesis, a why, a gate, one setting from a config, one criterion filed under q-acc
    with what meeting it would mean."""
    rec.ok("experiment", "add", "INT8 vs FP32", "--id", "int8", "-q", "q-acc", "--hypothesis", HYPOTHESIS, "-m", "docs/plan.md:3 names INT8",
           "--baseline", "fp32", "--candidate", "int8", "--if-holds", "ship INT8", "--if-not", "keep FP32", agent=True)
    rec.ok("spec", "set", "int8", "optim.lr", "--source", "configs/train.yaml#optim.lr", agent=True)
    rec.ok("criterion", "add", "INT8 loses at most 0.5 top-1", "-e", "int8", "-q", "q-acc", "--metric", "change.top1", "--at-least", "-0.005",
           "--id", "acc-tol", "--when-met", "INT8 stays within the accuracy budget", agent=True)


def confirm(rec: Rec) -> dict:
    """The user confirms int8's design as rb confirm lists it."""
    rec.ok(*rec.design("int8")["confirm"].split()[1:])
    d = rec.design("int8")
    assert (d["standing"], d["changes"]) == ("confirmed", []), d
    return d


def edit(rec: Rec, rel: str, old: str, new: str) -> None:
    """A hand edit of a record file, for rb doctor --adopt to keep."""
    p = rec.root / ".rb" / rel
    text = p.read_text(encoding="utf-8")
    assert old in text, (rel, old, text)
    p.write_text(text.replace(old, new), encoding="utf-8", newline="")


def changes(rec: Rec) -> list:
    return [(c["part"], c["change"], c["was"], c["now"]) for c in rec.design("int8")["changes"]]


def test_an_adoption_by_the_user_makes_no_part_theirs_that_it_left_as_it_was(rec):
    """A session wrote lat's design. The user fixes the experiment's title by hand and adopts it: the title is not a
    part of the design, so the design still waits, with the same command; the session's hypothesis and setting never
    read as the user's without rb confirm. So with a criterion a session added to the user's own experiment, whose
    statement the user fixes and adopts. A design a session wrote a part of waits for one confirmation even once the
    user rewrites that part (snapshot rule, Oct 14)."""
    rec.ok("experiment", "add", "INT8 latency", "--id", "lat", "-q", "q-acc", "--hypothesis", "INT8 halves CPU latency", "-m", "docs/plan.md:3 names it",
           "--if-holds", "ship INT8", "--if-not", "keep FP32", agent=True)
    rec.ok("spec", "set", "lat", "optim.lr", "--source", "configs/train.yaml#optim.lr", agent=True)
    waiting = rec.design("lat")["confirm"]
    edit(rec, "experiments/lat/experiment.yaml", "title: INT8 latency", "title: INT8 latency on CPU")
    rec.ok("doctor", "--adopt", "--why", "fix the title")
    d = rec.design("lat")
    assert (d["standing"], d["confirm"]) == ("not_confirmed", waiting), d
    assert "design not confirmed · list it to confirm: rb confirm lat" in rec.text("show", "lat") and rec.text("confirm") != "Nothing waits for a confirmation.\n"
    rec.ok("experiment", "add", "INT8 vs FP32", "--id", "int8", "-q", "q-acc", "--baseline", "fp32", "--candidate", "int8")
    rec.ok("criterion", "add", "INT8 loses at most 0.5 top-1", "-e", "int8", "-q", "q-acc", "--metric", "change.top1", "--at-least", "-0.005",
           "--id", "acc-tol", agent=True)
    edit(rec, "experiments/int8/criteria/acc-tol.yaml", "statement: INT8 loses at most 0.5 top-1", "statement: INT8 loses at most half a point of top-1")
    rec.ok("doctor", "--adopt", "--why", "fix a typo")
    assert rec.design("int8")["standing"] == "not_confirmed"
    # a session's purpose on the user's experiment waits; once the user rewrites it and adopts that, it still waits for
    # the one confirmation, and that makes it theirs
    rec.ok("experiment", "add", "Batch size probe", "--id", "bsz", "-q", "q-acc", "--purpose", "top-1 at batch 128")
    edit(rec, "experiments/bsz/experiment.yaml", "purpose: top-1 at batch 128", "purpose: top-1 at batch 64")
    rec.ok("doctor", "--adopt", "--why", "the plan says 64", agent=True)
    assert rec.design("bsz")["standing"] == "not_confirmed"
    edit(rec, "experiments/bsz/experiment.yaml", "purpose: top-1 at batch 64", "purpose: top-1 at batch 96")
    rec.ok("doctor", "--adopt", "--why", "my own number")
    assert rec.design("bsz")["standing"] == "not_confirmed" and rec.design("lat")["standing"] == "not_confirmed"
    rec.ok(*rec.design("bsz")["confirm"].split()[1:])
    assert rec.design("bsz")["standing"] == "confirmed" and "design not confirmed" not in rec.text("show", "bsz")


def test_the_users_own_writes_after_a_confirmation_never_read_as_a_sessions_change(rec):
    """After the user confirms int8's design, their own writes never reopen it, and a session's later write is read
    against what those writes left: one that restates the user's value, or changes no part, changes nothing; one that
    changes a part says what the user had there (`was`), never what the confirmation listed."""
    session_design(rec)
    confirm(rec)
    # the user changes the gate; a session then edits only the why and adopts it: one change, the why's
    rec.ok("experiment", "gate", "int8", "--if-not", "keep FP32 for now")
    assert rec.design("int8")["standing"] == "confirmed"
    edit(rec, "experiments/int8/experiment.yaml", "why: docs/plan.md:3 names INT8", "why: docs/plan.md:4 names INT8")
    rec.ok("doctor", "--adopt", "--why", "the plan moved", agent=True)
    assert changes(rec) == [("why", "changed", "docs/plan.md:3 names INT8", "docs/plan.md:4 names INT8")]
    shown = rec.text("show", "int8")
    assert '  changed since you confirmed it: why, was "docs/plan.md:3 names INT8", now "docs/plan.md:4 names INT8"\n' in shown, shown
    assert "changed since you confirmed it: gate" not in shown and "if not: keep FP32 for now\n" in shown
    confirm(rec)
    # the user sets a value; a session that sets the same one changes nothing, nor does one that edits the title
    rec.ok("spec", "set", "int8", "optim.lr", "0.002")
    rec.ok("spec", "set", "int8", "optim.lr", "0.002", agent=True)
    assert rec.design("int8")["standing"] == "confirmed"
    edit(rec, "experiments/int8/experiment.yaml", "title: INT8 vs FP32", "title: INT8 against FP32")
    rec.ok("doctor", "--adopt", "--why", "tidy the title", agent=True)
    assert rec.design("int8")["standing"] == "confirmed" and "changed since" not in rec.text("show", "int8")
    # a session's new value is read against the user's: it was 0.002, and its source the user took away stays theirs
    rec.ok("spec", "set", "int8", "optim.lr", "0.004", agent=True)
    assert changes(rec) == [("setting optim.lr", "changed", "0.002", "0.004")]
    confirm(rec)
    # a purpose the user adds is theirs; a session clearing it changed that (review of group E: the line listed seven
    # parts that never changed, and not the purpose)
    edit(rec, "experiments/int8/experiment.yaml", "title: INT8 against FP32", "title: INT8 against FP32\npurpose: price the INT8 path")
    rec.ok("doctor", "--adopt", "--why", "a purpose")
    rec.ok("experiment", "gate", "int8", "--if-not", "keep FP32 for good")
    assert rec.design("int8")["standing"] == "confirmed"
    edit(rec, "experiments/int8/experiment.yaml", "purpose: price the INT8 path\n", "")
    rec.ok("doctor", "--adopt", "--why", "no purpose", agent=True)
    assert changes(rec) == [("purpose", "removed", "price the INT8 path", None)], changes(rec)
    confirm(rec)
    # a criterion the user added after confirming is theirs as they added it: a session changing its rule changed that
    rec.ok("criterion", "add", "INT8 keeps 0.75 top-1", "-e", "int8", "--metric", "int8.top1", "--at-least", "0.75", "--id", "c9")
    assert rec.design("int8")["standing"] == "confirmed"
    edit(rec, "experiments/int8/criteria/c9.yaml", "target: 0.75", "target: 0.7")
    rec.ok("doctor", "--adopt", "--why", "the plan says 0.70", agent=True)
    assert changes(rec) == [("criterion c9's rule", "changed", "int8.top1 ≥ 0.75", "int8.top1 ≥ 0.7")]


TAKEN_BACK = {
    "criterion": (("criterion", "add", "INT8 keeps 0.75 top-1", "-e", "int8", "--metric", "int8.top1", "--at-least", "0.75", "--id", "c9"),
                  ("retract", "c9", "--why", "a duplicate"), "criterion c9 removed, was int8.top1 ≥ 0.75 (primary)"),
    "setting": (("spec", "set", "int8", "optim.wd", "0.0001"), ("retract", "int8/optim.wd", "--why", "unused"), "setting optim.wd removed, was 0.0001"),
    "variant": (("variant", "add", "int8", "int4", "--role", "ablation"), ("retract", "int8/int4", "--why", "dropped"), "variant int4 (ablation) removed"),
    "filing": (("criterion", "file", "acc-tol", "q-lat", "--why", "it bears on speed too"), ("criterion", "file", "acc-tol", "q-lat", "--remove", "--why", "not about speed"),
               "criterion acc-tol no longer filed under q-lat"),
}


@pytest.mark.parametrize("part", sorted(TAKEN_BACK))
def test_a_session_taking_back_a_part_the_user_added_after_confirming_reopens_it(rec, part):
    """Parts the user added after confirming are theirs; a session that takes one back changed the design as the user
    has it, and the removal says what was there. Where the design is then exactly one the user confirmed, it is theirs
    and reads confirmed (snapshot rule, Oct 14)."""
    add, take_back, line = TAKEN_BACK[part]
    rec.ok("question", "add", "Is INT8 fast enough?", "--id", "q-lat")
    session_design(rec)
    confirm(rec)
    rec.ok(*add)
    rec.ok("experiment", "gate", "int8", "--if-not", "keep FP32 for now")
    assert rec.design("int8")["standing"] == "confirmed"
    rec.ok(*take_back, agent=True)
    assert rec.design("int8")["standing"] == "changed"
    assert f"  changed since you confirmed it: {line}\n" in rec.text("show", "int8"), rec.text("show", "int8")
    # the user sets the gate back as they confirmed it: the design is exactly the one they confirmed
    rec.ok("experiment", "gate", "int8", "--if-not", "keep FP32")
    assert rec.design("int8")["standing"] == "confirmed" and "design not confirmed" not in rec.text("show", "int8")


def test_a_sessions_amendment_design_factor_or_cited_value_after_confirming_reopens_it(rec):
    """A fixed design factor, a cited source's value and an amendment are parts of the design: a session's after the
    confirmation reopens it, and rb confirm lists each before it is confirmed. They change readings, so a design
    confirmed before them is not confirmed with them."""
    session_design(rec)
    rec.ok("spec", "set", "int8", "seed", "--per-run", agent=True)
    rec.ok("freeze", "int8", "-m", "before the runs", agent=True)
    confirm(rec)
    rec.ok("spec", "set", "int8", "seed", "--design-factor", "--amend", "-m", "seeds are fixed cells", agent=True)
    shown = rec.text("show", "int8")
    for line in ("setting seed, was per run, now per run (a fixed design factor)", "amended: setting seed: now a fixed design factor · note: seeds are fixed cells"):
        assert f"  changed since you confirmed it: {line}\n" in shown, (line, shown)
    assert "setting seed: per run (a fixed design factor)" in rec.text("confirm", "int8")
    confirm(rec)
    rec.ok("spec", "set", "int8", "optim.lr", "0.001", "--cited", "0.01", "--amend", "-m", "the paper used 0.01", agent=True)
    d = rec.design("int8")
    assert d["standing"] == "changed" and ("setting optim.lr", "changed", "0.001", "0.001 (the cited source used 0.01)") in changes(rec), d["changes"]


def test_a_freeze_or_an_amendment_since_the_confirmation_says_its_reason_on_its_line(rec):
    """Trial of group E: after the confirmation a session froze the experiment with a reason, then amended a setting's
    value and source with another; the lines that said what changed dropped both reasons, and the amended setting took
    three lines (its value, its source, the amendment saying both again). The freeze's and the amendment's lines say
    the reason given, as the session's note; a value and a source the amendment says as they changed are said by it."""
    rec.write("configs/other.yaml", "optim:\n  lr: 0.001\n")
    session_design(rec)
    rec.ok("spec", "set", "int8", "optim.lr", "--source", "configs/other.yaml#optim.lr", agent=True)
    confirm(rec)
    rec.ok("freeze", "int8", "-m", "locks the plan", agent=True)
    rec.write("configs/train.yaml", "optim:\n  lr: 0.003\n  wd: 0.0001\ndata:\n  batch_size: 256\n")
    rec.ok("spec", "set", "int8", "optim.lr", "--source", "configs/train.yaml#optim.lr", "--amend", "--why", "the warmup was too short", agent=True)
    shown = rec.text("show", "int8")
    lines = [l.strip() for l in shown.splitlines() if l.strip().startswith("changed since you confirmed it:")]
    assert len(lines) == 2 and re.fullmatch(r"changed since you confirmed it: frozen \d{4}-\d\d-\d\d · note: locks the plan", lines[0]), lines
    assert lines[1] == ("changed since you confirmed it: amended: setting optim.lr: 0.001 -> 0.003; source configs/other.yaml#optim.lr -> "
                        "configs/train.yaml#optim.lr · note: the warmup was too short"), lines
    # --json keeps every part's change, with was and now
    assert {("setting optim.lr", "0.001", "0.003"), ("setting optim.lr's source", "configs/other.yaml#optim.lr", "configs/train.yaml#optim.lr")} <= {
        (c[0], c[2], c[3]) for c in changes(rec)}, changes(rec)


def test_a_sessions_change_merged_in_after_the_confirmation_reopens_it(rec):
    """A session changed a setting on a branch before the user confirmed the design on theirs; once the branch is
    merged, the design reads the session's value, which the user never confirmed: it waits again, with what the user
    confirmed and what it reads now, whatever the times of the writes."""
    session_design(rec)
    rec.commit()
    git = lambda *a: subprocess.run(["git", *a], cwd=rec.root, env=rec.env, check=True, capture_output=True)
    git("checkout", "-q", "-b", "side")
    rec.ok("spec", "set", "int8", "optim.lr", "0.002", agent=True)
    rec.commit()
    git("checkout", "-q", "-")
    confirm(rec)
    rec.commit()
    git("merge", "-q", "--no-edit", "side")
    d = rec.design("int8")
    assert d["standing"] == "changed" and ("setting optim.lr", "changed", "0.001", "0.002") in changes(rec), d
    assert "  changed since you confirmed it: setting optim.lr, was 0.001, now 0.002\n" in rec.text("show", "int8")


def test_an_interpretation_the_user_confirmed_with_its_design_reads_as_confirmed(rec, monkeypatch):
    """rb confirm lists what meeting acc-tol would mean for q-acc, so confirming the design confirms it: rb status,
    rb context, rb show of the question, a page and rb open stop saying it is not confirmed, and a reason given with a
    link reads as its note, as a confirmed call's reason does. The readings stay as they were."""
    session_design(rec)
    rec.ok("question", "add", "Is INT8 fast enough?", "--id", "q-lat")
    rec.ok("criterion", "file", "acc-tol", "q-lat", "--when-met", "INT8 is fast enough", "--why", "docs/plan.md:7 ties them", agent=True)
    unconfirmed = "declared when met, not confirmed: 'INT8 stays within the accuracy budget'"
    assert all(unconfirmed in rec.text(*w) for w in (("status",), ("context",), ("show", "q-acc")))
    readings = rec.readings()
    confirm(rec)
    assert rec.readings() == readings
    for where in (("status",), ("context",), ("show", "q-acc"), ("show", "q-lat")):
        text = rec.text(*where)
        assert "when met, not confirmed" not in text, (where, text)
    assert "declared when met: 'INT8 stays within the accuracy budget'" in rec.text("show", "q-acc")
    assert "declared when met: 'INT8 is fast enough'; note: docs/plan.md:7 ties them." in rec.text("show", "q-lat")
    page = rec.page().replace("\n", "")
    assert not re.search(r"(Recorded|Conditional) interpretation( · its criterion: [^·]*)? · not confirmed", page), page
    state = rec.state(monkeypatch)
    groups = [g for b in state["home"]["briefs"] for g in (b.get("overview") or {}).get("interpretations", [])]
    assert groups and all(g["confirmed"] for g in groups), groups


def test_an_interpretation_confirmed_with_a_design_waits_again_once_a_session_changes_it(rec, monkeypatch):
    """Trial of group E: the user confirmed acc-tol's interpretation with int8's design at a rule the runs miss; a
    session then loosened the rule so they meet it, and the question's reading said the interpretation, unmarked and
    active, on a rule the user never confirmed. What meeting a criterion would mean hangs on the whole design (its
    rules, its variants' roles, the settings that decide which runs count): once a session changes the design after
    the confirmation, it reads as not confirmed again, until the user confirms it as it reads now. The gate the user
    confirmed stays theirs, as a gate confirmation leaves it (recheck of group E: it read as a session's again, in
    gate_by and gates_pending too). The user's own change never reopens either, and no reading moves."""
    session_design(rec)
    rec.ok("run", "add", "int8", "fp32.top1=0.761", "int8.top1=0.758", agent=True)
    rec.ok("run", "add", "int8", "fp32.top1=0.760", "int8.top1=0.758", agent=True)
    edit(rec, "experiments/int8/criteria/acc-tol.yaml", "target: -0.005", "target: -0.001")
    rec.ok("doctor", "--adopt", "--why", "docs/plan.md:4 says 0.1", agent=True)
    confirm(rec)
    said = "declared when met: 'INT8 stays within the accuracy budget'"
    unconfirmed = "declared when met, not confirmed: 'INT8 stays within the accuracy budget'"
    assert said in rec.text("status") and unconfirmed not in rec.text("status")
    assert "if not: keep FP32\n" in rec.text("show", "int8")
    # a session loosens the rule: the runs now meet it, and the interpretation is active on a rule nobody confirmed
    edit(rec, "experiments/int8/criteria/acc-tol.yaml", "target: -0.001", "target: -0.005")
    rec.ok("doctor", "--adopt", "--why", "docs/plan.md:4 says 0.5", agent=True)
    assert changes(rec) == [("criterion acc-tol's rule", "changed", "change.top1 ≥ -0.001", "change.top1 ≥ -0.005")]
    for where in (("status",), ("context",), ("show", "q-acc")):
        text = rec.text(*where)
        assert unconfirmed in text and said not in text, (where, text)
    page = rec.page()
    # the shell says the interpretation as a note not confirmed, in its own line (plain words pass 3)
    assert unconfirmed in page.replace("\n", "") or "A note, not confirmed: " in page, page
    [row] = [r for q in rec.ok("status")["data"]["questions"] for r in q["inquiry"]["direct"] if r.get("interpretation")]
    assert row["interpretation_confirmed"] is False and row["interpretation_active"] is True, row
    groups = [g for b in rec.state(monkeypatch)["home"]["briefs"] for g in (b.get("overview") or {}).get("interpretations", [])]
    assert groups and not any(g["confirmed"] for g in groups), groups
    # the gate the user confirmed is theirs still: nobody changed it
    shown = rec.text("show", "int8")
    assert "if not: keep FP32\n" in shown and "keep FP32 · not confirmed" not in shown, shown
    st = rec.ok("status")["data"]
    assert st["gates_pending"] == [] and [e["gate_by"] for e in st["experiments"]] == ["human:ana"], st["gates_pending"]
    # a session's change to a setting reopens them too; confirming the design as it reads now makes them the user's,
    # and no confirmation moves a reading
    readings = rec.readings()
    rec.ok(*rec.design("int8")["confirm"].split()[1:])
    assert rec.readings() == readings
    rec.ok("spec", "set", "int8", "optim.lr", "0.002", agent=True)
    assert unconfirmed in rec.text("status") and "if not: keep FP32\n" in rec.text("show", "int8")
    readings = rec.readings()
    rec.ok(*rec.design("int8")["confirm"].split()[1:])
    assert rec.readings() == readings
    assert said in rec.text("status") and "if not: keep FP32\n" in rec.text("show", "int8")
    # the user's own change of the rule leaves them confirmed
    edit(rec, "experiments/int8/criteria/acc-tol.yaml", "target: -0.005", "target: -0.004")
    rec.ok("doctor", "--adopt", "--why", "my own margin")
    assert rec.design("int8")["standing"] == "confirmed" and said in rec.text("status") and "if not: keep FP32\n" in rec.text("show", "int8")


def test_a_waiting_designs_command_is_said_once_on_each_screen(rec, monkeypatch, tmp_path):
    """Trial of group E: rb status, rb context, a page and rb export printed the design's command beside the gate line
    too, so one experiment waited in two places on one screen, and that command, beside a gate, confirms a whole design
    the screen never listed. The gate line ends `· not confirmed`, with no command: rb status and rb context say the one
    line for the designs that wait; rb show, a page and rb open's pane say the design's standing line, with its command,
    once."""
    session_design(rec)
    cmd = rec.design("int8")["confirm"]
    line = "The design of 1 experiment is not confirmed: rb confirm lists it."
    for argv in (("status",), ("context",)):
        text = rec.text(*argv)
        assert cmd not in text and text.count(line) == 1 and "keep FP32 · not confirmed" in text, (argv, text)
    # recheck of group E, round 3: none of these views shows every part the key names, so each gives the command that
    # lists the design, which ends with the one that confirms it as listed
    shown = rec.text("show", "int8")
    assert cmd not in shown and shown.count(LISTING) == 1 and "if not: keep FP32 · not confirmed\n" in shown, shown
    page = rec.page()
    assert cmd not in page and page.count(LISTING) == 1 and page.count(line) == 1 and "keep FP32 · not confirmed" in page, page
    rec.ok("export", "--out", str(tmp_path / "export"))
    exported = html_mod.unescape(re.sub(r"<[^>]+>", "\n", (tmp_path / "export" / "page.html").read_text(encoding="utf-8")))
    assert cmd not in exported and exported.count(LISTING) == 1 and "keep FP32 · not confirmed" in exported, exported
    pane = brief(rec.state(monkeypatch), "int8")
    assert pane["design"]["lines"][0] == LISTING and not [t for _, t in pane["gate_parts"] if "rb confirm" in t], pane
    assert rec.text("confirm", "int8").endswith(f"    confirm: {cmd}\n")


def test_no_line_offers_to_confirm_all_while_a_design_waits(rec):
    """Trial of group E: with one call and one design waiting, rb status, rb context and a page said the design line,
    then `Confirm all 2 (1 call and 1 design): rb confirm --all`, a second line about the design that would confirm it
    unread. While a design waits, its one line is the only one: rb confirm lists it with the calls and ends with the
    command that confirms them all as listed. Once no design waits, the line is back, naming each call it confirms."""
    session_design(rec)
    rec.ok("decide", "int8", "park", "--why", "later", agent=True)
    rec.ok("decide", "q-acc", "investigate", "--why", "one more seed", agent=True)
    line = "The design of 1 experiment is not confirmed: rb confirm lists it."
    for where, text in (("status", rec.text("status")), ("context", rec.text("context")), ("page", rec.page())):
        assert text.count(line) == 1 and "Confirm all" not in text and "Proposed, not made" in text, (where, text)
    calls = [p["id"] for p in rec.ok("status")["data"]["pending"]]
    assert rec.text("confirm").rstrip("\n").endswith(f"rb confirm {' '.join(calls)} {rec.design('int8')['confirm'].split()[-1]} confirms 2 calls and 1 design as listed.")
    confirm(rec)
    for where, text in (("status", rec.text("status")), ("context", rec.text("context"))):
        assert f"Confirm all 2: rb confirm {' '.join(calls)}" in text and line not in text, (where, text)
    assert f"Confirm all 2\n\n\nrb confirm {' '.join(calls)}\n" in rec.page()


def test_a_design_a_session_touched_waits_once_even_when_it_is_wholly_the_users_again(rec):
    """A session adds a criterion to the user's own design and takes it back; or the user takes it back. Review of
    group E read that as the user's design again, worked out from who wrote what last. By the snapshot rule (Oct 14) a
    design a session's line wrote any part of waits for one confirmation, whatever is written after; once the user
    confirms it, nothing waits and no line says it changed."""
    rec.ok("experiment", "add", "INT8 vs FP32", "--id", "int8", "-q", "q-acc", "--baseline", "fp32", "--candidate", "int8")
    rec.ok("spec", "set", "int8", "optim.lr", "--source", "configs/train.yaml#optim.lr")
    mine = rec.design("int8")
    assert mine["standing"] == "yours"
    for who in (True, False):
        rec.ok("criterion", "add", "INT8 keeps 0.75 top-1", "-e", "int8", "--metric", "int8.top1", "--at-least", "0.75", "--id", f"c{int(who)}", agent=True)
        assert rec.design("int8")["standing"] == "not_confirmed"
        rec.ok("retract", f"c{int(who)}", "--why", "not needed", agent=who)
        d = rec.design("int8")
        assert (d["standing"], d["key"], d["changes"]) == ("not_confirmed", mine["key"], []), d
        assert LISTING in rec.text("show", "int8") and rec.text("confirm") != "Nothing waits for a confirmation.\n"
    rec.ok(*rec.design("int8")["confirm"].split()[1:])
    assert rec.design("int8")["standing"] == "confirmed"
    for w in (("status",), ("show", "int8"), ("confirm",)):
        text = rec.text(*w)
        assert not [m for m in ("design not confirmed", "changed since you confirmed it", "The design of") if m in text], (w, text)


def test_a_confirmation_whose_parts_are_not_what_its_key_names_confirms_nothing(rec):
    """The confirmation is one log line that keeps the design as listed, under its key. A line appended by hand with
    the key and no parts is no confirmation rb wrote: the design still waits."""
    session_design(rec)
    key = rec.design("int8")["key"]
    line = {"at": "2099-01-01T00:00:00.000+00:00", "actor": "human:ana", "actor_via": "git-email", "via": "cli", "op": "gate", "kind": "experiment",
            "id": "int8", "detail": {"confirms": "gate", "design": key, "parts": {}, "if_holds": "ship INT8", "if_not": "keep FP32"}}
    with (rec.root / ".rb" / "log.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(line) + "\n")
    assert rec.design("int8")["standing"] == "not_confirmed"
    # nor is one that keeps the design under its key with no parts in rb's structured form
    line = {**line, "at": "2099-01-02T00:00:00.000+00:00", "detail": {"confirms": "gate", "owned": {"int8": {"key": key, "parts": {}}}}}
    with (rec.root / ".rb" / "log.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(line) + "\n")
    assert rec.design("int8")["standing"] == "not_confirmed"


def test_a_variant_file_edited_outside_rb_is_not_confirmed(rec):
    """rb confirm refuses a design whose experiment or criterion file was edited outside rb; a variant's file is part
    of the design too."""
    session_design(rec)
    edit(rec, "experiments/int8/variants/int8.yaml", "role: candidate", "role: ablation")
    cmd = rec.design("int8")["confirm"]          # as rb confirm lists it now
    r = rec.run(*cmd.split()[1:], "--json")
    assert r.returncode != 0 and json.loads(r.stdout)["errors"][0]["code"] == "E_STATE_EDITED", r.stdout


def test_rb_confirm_help_says_it_confirms_a_design():
    """rb confirm's help names designs, and that an experiment's id alone lists its design (review of group E)."""
    r = subprocess.run([*command(), "confirm", "--help"], capture_output=True, text=True, stdin=subprocess.DEVNULL,
                       env={k: v for k, v in environment().items() if k not in AGENT_VARS})
    text = " ".join(r.stdout.split())
    assert "<experiment>@<hash> for an experiment's design with its gate, as rb confirm lists it" in text, text
    assert "an experiment's id alone lists its design" in text and "every call, gate and design that waits as it reads now" in text, text
    assert "the command rb confirm ends with confirms them as listed" in text, text


def test_a_variants_own_setting_named_like_a_shared_one_is_its_own_part(rec):
    """Recheck of group E: variant optim's own lr and the shared optim.lr were one part to a confirmation, so it kept
    one part fewer than its key named: rb confirm said the design was confirmed, and it still waited, however often it
    was confirmed. Every part has a key of its own, kept as listed, in order, and the design is confirmed."""
    session_design(rec)
    rec.ok("variant", "add", "int8", "optim", "--role", "ablation", agent=True)
    rec.ok("spec", "set", "int8", "lr", "0.5", "--variant", "optim", agent=True)
    assert rec.text("confirm", "int8").count("    setting optim.lr: ") == 2
    out = rec.text(*rec.design("int8")["confirm"].split()[1:])
    assert out.startswith("int8's design is confirmed.\n"), out
    assert rec.design("int8")["standing"] == "confirmed" and "The design of" not in rec.text("status")
    [kept] = [line["detail"]["parts"] for line in map(json.loads, (rec.root / ".rb" / "log.jsonl").read_text(encoding="utf-8").splitlines())
              if line["op"] == "gate" and "design" in line.get("detail", {})]
    keys = [k for k, _ in kept]
    assert len(set(keys)) == len(keys) and {"setting:optim.lr", "setting:optim/lr"} <= set(keys), keys


def user_design(rec: Rec) -> None:
    """int8's design, written wholly in the user's own terminal."""
    rec.ok("experiment", "add", "INT8 vs FP32", "--id", "int8", "-q", "q-acc", "--hypothesis", HYPOTHESIS, "-m", "docs/plan.md:3 names INT8",
           "--baseline", "fp32", "--candidate", "int8", "--varies", "precision", "--if-holds", "ship INT8", "--if-not", "keep FP32")
    rec.ok("spec", "set", "int8", "optim.lr", "--source", "configs/train.yaml#optim.lr")


@pytest.mark.parametrize("confirmed", [True, False], ids=["after-a-confirmation", "on-the-users-design"])
def test_the_users_write_of_one_element_never_makes_a_sessions_write_of_another_theirs(rec, confirmed):
    """Recheck of group E: a session filed int8 under q-lat, varied optim.lr, set the gate's if-not branch, or set
    optim.lr from a note; the user then filed it under q-cal, varied optim.wd, set the if-holds branch, or gave
    optim.lr a note, marked it optional or cited a value. Each write counted for the whole part, so the design read as
    confirmed, or wholly the user's, with the session's element in it. A user's write to a design that waits makes no
    design theirs (snapshot rule, Oct 14): each difference is said against what they confirmed, theirs and the
    session's alike, part by part."""
    for q, text in (("q-lat", "Is INT8 fast enough?"), ("q-cal", "Does calibration size matter?")):
        rec.ok("question", "add", text, "--id", q)
    steps = [
        (("experiment", "file", "int8", "q-lat"), ("experiment", "file", "int8", "q-cal"),
         'questions, was q-acc, now "q-acc, q-lat, q-cal"'),
        (("spec", "vary", "int8", "optim.lr"), ("spec", "vary", "int8", "optim.wd"),
         'what it varies, was precision, now "precision, optim.lr, optim.wd"'),
        (("experiment", "gate", "int8", "--if-not", "drop INT8 for good"), ("experiment", "gate", "int8", "--if-holds", "ship INT8 next week"),
         'the gate, was "if it holds: ship INT8 · if not: keep FP32", now "if it holds: ship INT8 next week · if not: drop INT8 for good"'),
        (("spec", "set", "int8", "optim.lr", "0.002", "--source", "note:the paper says 0.002"), ("spec", "set", "int8", "optim.lr", "--note", "the paper's value"),
         "setting optim.lr, was 0.001, now 0.002"),
        (None, ("spec", "set", "int8", "optim.lr", "--optional"), "setting optim.lr, was 0.001, now 0.002 (optional)"),
        (None, ("spec", "set", "int8", "optim.lr", "--cited", "0.01"), "setting optim.lr, was 0.001, now 0.002 (optional) (the cited source used 0.01)"),
        # a session takes a question of the user's out, and the user another: the questions are said as they differ
        (("experiment", "file", "int8", "q-cal", "--remove"), ("experiment", "file", "int8", "q-acc", "--remove"), "questions, was q-acc, now q-lat"),
    ]
    if confirmed:
        session_design(rec)
        rec.ok("spec", "vary", "int8", "precision", agent=True)
        confirm(rec)
    else:
        user_design(rec)
    for session, user, line in steps:
        if session:
            rec.ok(*session, agent=True)
        rec.ok(*user)
        d = rec.design("int8")
        assert d["standing"] == ("changed" if confirmed else "not_confirmed"), (user, d)
        shown = rec.text("show", "int8")
        assert f"{LISTING}\n" in shown and rec.text("confirm") != "Nothing waits for a confirmation.\n", (user, shown)
        if confirmed:
            assert f"  changed since you confirmed it: {line}\n" in shown, (user, shown)


def three_records(rec: Rec) -> None:
    """The recheck's three cases, each in one commit after its writes. a: the user's own design; the user adopts a
    hypothesis of their own, then a session adopts a title edit. b: a session's design the user confirmed; the user
    sets optim.lr, then a session adopts a title edit. c: the same; a session adds optim.wd, then the user adopts a
    hypothesis of their own."""
    exp = lambda e: f"experiments/{e}/experiment.yaml"
    rec.ok("experiment", "add", "INT8 vs FP32", "--id", "a", "-q", "q-acc", "--hypothesis", "INT8 keeps top-1 within 0.5 points", "--baseline", "fp32")
    for e in ("b", "c"):
        rec.ok("experiment", "add", "INT8 vs FP32", "--id", e, "-q", "q-acc", "--hypothesis", "INT8 keeps top-1 within 0.5 points", "--baseline", "fp32", agent=True)
        rec.ok("spec", "set", e, "optim.lr", "--source", "configs/train.yaml#optim.lr", agent=True)
    rec.commit()
    for e in ("b", "c"):
        rec.ok(*rec.design(e)["confirm"].split()[1:])
    edit(rec, exp("a"), "within 0.5 points", "within 1 point, as I decided")
    rec.ok("doctor", "--adopt", "--why", "my change")
    edit(rec, exp("a"), "title: INT8 vs FP32", "title: INT8 against FP32")
    rec.ok("doctor", "--adopt", "--why", "tidy the title", agent=True)
    rec.ok("spec", "set", "b", "optim.lr", "0.003")
    edit(rec, exp("b"), "title: INT8 vs FP32", "title: INT8 against FP32")
    rec.ok("doctor", "--adopt", "--why", "tidy the title", agent=True)
    rec.ok("spec", "set", "c", "optim.wd", "--source", "configs/train.yaml#optim.wd", agent=True)
    edit(rec, exp("c"), "within 0.5 points", "within 1 point, as I decided")
    rec.ok("doctor", "--adopt", "--why", "my own hypothesis")
    rec.commit()


def standings(rec: Rec) -> dict:
    return {e["id"]: e["design"] for e in rec.ok("status")["data"]["experiments"]}


def clone_of(rec: Rec, tmp_path) -> Rec:
    other = Rec.__new__(Rec)
    other.root, other.env = tmp_path / "clone", rec.env
    subprocess.run(["git", "clone", "-q", str(rec.root), str(other.root)], env=rec.env, check=True, capture_output=True)
    return other


def test_a_designs_standing_reads_the_same_in_a_clone_and_without_rbs_copies(rec, tmp_path):
    """Recheck of group E: the standing rested on rb's copies of what it wrote between commits (.rb/cache, which git
    does not keep). In a clone, or once they were gone, the user's own design read `design not confirmed`, and the
    user's own changes read as a session's. The standing is read from the log alone. In c the user's hypothesis came
    after a session's change, while the design waited: it is said with the session's (snapshot rule, Oct 14)."""
    three_records(rec)
    here = standings(rec)
    assert [here[e]["standing"] for e in "abc"] == ["yours", "confirmed", "changed"], here
    assert [(c["part"], c["change"]) for c in here["c"]["changes"]] == [
        ("hypothesis", "changed"), ("setting optim.wd", "added"), ("setting optim.wd's source", "added")], here["c"]
    there = standings(clone_of(rec, tmp_path))
    shutil.rmtree(rec.root / ".rb" / "cache")
    assert there == here and standings(rec) == here


def as_an_older_rb_wrote_it(rec: Rec) -> None:
    """The record's log lines as an older rb wrote them, with no changed_parts and no design kept, committed."""
    log = rec.root / ".rb" / "log.jsonl"
    lines = [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines()]
    for line in lines:
        for key in ("changed_parts", "parts_compared", "owned"):
            (line.get("detail") or {}).pop(key, None)
    log.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in lines), encoding="utf-8", newline="")
    subprocess.run(["git", "commit", "-qam", "as an older rb wrote it", "--allow-empty"], cwd=rec.root, env=rec.env, check=True)


def test_lines_an_older_rb_wrote_read_on_the_safe_side_without_rbs_copies(rec, tmp_path):
    """The same three records, their lines as an older rb wrote them (no changed_parts, no design kept), in a clone: a
    session's adoption that does not say what it changed is a write to the design, so a waits; the user's own write
    from an older rb keeps no design, so b asks again (snapshot rule, Oct 14: an older rb can only make rb ask again,
    never make a session's change read as the user's). The record and its clone read the same. Recheck of the snapshot
    rule, round 3: b and c, confirmed by a line that keeps no design and names no line it saw, as group E's first build
    wrote one, said only the changes rb's words show, though a session's line that may be after it can change what they
    do not; such a confirmation says no change since it."""
    three_records(rec)
    as_an_older_rb_wrote_it(rec)
    got = standings(rec)
    assert [got[e]["standing"] for e in "abc"] == ["not_confirmed", "not_confirmed", "not_confirmed"], got
    assert standings(clone_of(rec, tmp_path)) == got
    assert [got[e]["changes"] for e in "bc"] == [[], []], got


@pytest.mark.parametrize("order", ["the-users-first", "the-sessions-first"])
def test_a_sessions_adoption_an_older_rb_wrote_after_a_confirmation_waits_in_a_clone(rec, tmp_path, order):
    """Recheck of group E: after the user confirmed int8's design, the user adopted an edit of the why and a session one
    of the hypothesis, in one commit, as an older rb writes adoptions (no changed_parts). In a clone, with no copy of
    what either wrote, the design read as confirmed with the session's hypothesis in it, and rb confirm said nothing
    waited. A part a session's line may have changed is the session's, whichever came first. Recheck of the snapshot
    rule, round 3: the confirmation, as an older rb's line keeps no design and names no line it saw, says no change
    since it."""
    session_design(rec)
    confirm(rec)
    rel = "experiments/int8/experiment.yaml"
    steps = [(("why: docs/plan.md:3 names INT8", "why: my own reason"), False), ((f"hypothesis: {HYPOTHESIS}", "hypothesis: INT8 keeps top-1 within 3 points"), True)]
    for (old, new), agent in steps if order == "the-users-first" else steps[::-1]:
        edit(rec, rel, old, new)
        rec.ok("doctor", "--adopt", "--why", "an edit", agent=agent)
    rec.commit()
    as_an_older_rb_wrote_it(rec)
    there = clone_of(rec, tmp_path)
    d = there.design("int8")
    assert (d["standing"], d["changes"]) == ("not_confirmed", []), d
    assert LISTING in there.text("show", "int8") and there.text("confirm") != "Nothing waits for a confirmation.\n"


def test_the_users_write_merged_in_after_a_confirmation_is_what_a_sessions_change_is_read_against(rec):
    """Recheck of group E: the user set optim.lr to 0.003 on a branch and confirmed the design on main; after the merge
    a session set 0.004, and rb said `was 0.001`, the value the confirmation listed, though the record holds the user's
    0.003; a session setting it back to 0.001 read as confirmed. By the snapshot rule (Oct 14) the user's write on the
    branch, made while the design waited, is no design they own: after the merge rb asks again, whatever the times of the
    writes, and once the user confirms it as merged a session's change is read against that."""
    session_design(rec)
    rec.commit()
    git = lambda *a: subprocess.run(["git", *a], cwd=rec.root, env=rec.env, check=True, capture_output=True)
    git("checkout", "-q", "-b", "side")
    rec.ok("spec", "set", "int8", "optim.lr", "0.003")
    rec.commit()
    git("checkout", "-q", "-")
    confirm(rec)
    rec.commit()
    git("merge", "-q", "--no-edit", "side")
    assert rec.design("int8")["standing"] == "changed" and ("setting optim.lr", "changed", "0.001", "0.003") in changes(rec), changes(rec)
    confirm(rec)
    rec.ok("spec", "set", "int8", "optim.lr", "0.004", agent=True)
    assert ("setting optim.lr", "changed", "0.003", "0.004") in changes(rec), changes(rec)
    assert "  changed since you confirmed it: setting optim.lr, was 0.003, now 0.004\n" in rec.text("show", "int8")
    rec.ok("spec", "set", "int8", "optim.lr", "0.003", agent=True)
    assert rec.design("int8")["standing"] == "confirmed"
    rec.ok("spec", "set", "int8", "optim.lr", "0.001", agent=True)
    assert ("setting optim.lr", "changed", "0.003", "0.001") in changes(rec), changes(rec)


def test_an_experiments_why_reads_in_the_voice_of_whoever_wrote_it(rec):
    """Recheck of group E: the why was labelled by whoever made the experiment. A session's why adopted into the user's
    own experiment read as the user's reason, and the user's own why adopted into a session's read as a session's note,
    not confirmed. Review of the snapshot rule, round 1: the voice is never the latest line's by its time (a clock, a
    merge). A why reads as a session's note while a session's line wrote it as it reads now; one only the user wrote is
    theirs, while the design waits too."""
    user_design(rec)
    edit(rec, "experiments/int8/experiment.yaml", "why: docs/plan.md:3 names INT8", "why: the session thinks INT8 is enough")
    rec.ok("doctor", "--adopt", "--why", "a better reason", agent=True)
    shown = rec.text("show", "int8")
    assert "  note: the session thinks INT8 is enough · not confirmed\n" in shown and rec.design("int8")["standing"] == "not_confirmed", shown
    rec.ok("experiment", "add", "INT8 latency", "--id", "lat", "-q", "q-acc", "--hypothesis", "INT8 halves CPU latency", "-m", "docs/plan.md:9 says so", agent=True)
    edit(rec, "experiments/lat/experiment.yaml", "why: docs/plan.md:9 says so", "why: my own reason, typed by me")
    rec.ok("doctor", "--adopt", "--why", "my why")
    shown = rec.text("show", "lat")
    assert rec.design("lat")["standing"] == "not_confirmed", shown
    assert "  why: my own reason, typed by me\n" in shown and "my own reason, typed by me · not confirmed" not in shown, shown


def test_a_gate_the_user_confirmed_stays_theirs_until_a_session_changes_it(rec):
    """Recheck of group E: after the user confirmed int8's design, a session changed only settings and added a variant;
    gate_by then named the session, gates_pending listed the gate again, and every screen put `· not confirmed` back on
    a gate the user had confirmed word for word. The gate stays the user's, as a gate confirmation leaves it, while the
    design's one standing line says what changed; a session's change of a branch of it waits with the design, and the
    user's own gate write after it, while the design waits, is said with it against what they confirmed."""
    session_design(rec)
    confirm(rec)
    rec.ok("spec", "set", "int8", "optim.lr", "0.002", agent=True)
    rec.ok("variant", "add", "int8", "int4", "--role", "ablation", agent=True)
    st = rec.ok("status")["data"]
    [e] = st["experiments"]
    assert (e["gate_by"], st["gates_pending"], e["design"]["standing"]) == ("human:ana", [], "changed"), (e["gate_by"], st["gates_pending"])
    for argv in (("status",), ("context",), ("show", "int8")):
        text = rec.text(*argv)
        assert "keep FP32 · not confirmed" not in text, (argv, text)
    assert rec.text("show", "int8").count("not confirmed") == 1
    rec.ok("experiment", "gate", "int8", "--if-not", "drop INT8 for good", agent=True)
    st = rec.ok("status")["data"]
    assert st["experiments"][0]["gate_by"] == "agent:claude-code" and [g["confirm"] for g in st["gates_pending"]] == [rec.design("int8")["confirm"]]
    assert "if not: drop INT8 for good · not confirmed\n" in rec.text("show", "int8")
    # the user setting the other branch leaves the session's branch waiting with the design
    rec.ok("experiment", "gate", "int8", "--if-holds", "ship INT8 next week")
    line = 'the gate, was "if it holds: ship INT8 · if not: keep FP32", now "if it holds: ship INT8 next week · if not: drop INT8 for good"'
    assert rec.design("int8")["standing"] == "changed" and f"  changed since you confirmed it: {line}\n" in rec.text("show", "int8")


def test_words_the_user_gives_with_a_gate_are_theirs_once_the_gate_is(rec):
    """A session set int8's gate with words; the user then set it in their own terminal, giving the same words. The
    design a session touched waits for one confirmation all the same (snapshot rule, Oct 14), and so does its gate, in
    the standing's voice (review of the snapshot rule, round 1): the words read as given until the user confirms the
    design, then as theirs."""
    user_design(rec)
    rec.ok("experiment", "gate", "int8", "--if-holds", "ship INT8 next week", "--words", "I ship INT8 if it holds", agent=True)
    assert rec.design("int8")["standing"] == "not_confirmed"
    rec.ok("experiment", "gate", "int8", "--if-holds", "ship INT8 this week", "--words", "I ship INT8 if it holds")
    assert rec.design("int8")["standing"] == "not_confirmed" and 'words (as given): "I ship INT8 if it holds"' in rec.text("show", "int8")
    assert [g["experiment"] for g in rec.ok("status")["data"]["gates_pending"]] == ["int8"]
    rec.ok(*rec.design("int8")["confirm"].split()[1:])
    assert 'your words: "I ship INT8 if it holds"' in rec.text("show", "int8") and rec.ok("status")["data"]["gates_pending"] == []


def test_rb_confirm_and_rb_log_say_what_is_true_of_a_design(rec):
    """Recheck of group E: a key rb never listed was refused as a design changed after it was listed; a design
    already confirmed was refused as its gate being the user's; rb log said a gate line for an experiment with no gate;
    and rb confirm counted `these 1 call and 1 design`."""
    session_design(rec)
    rec.ok("experiment", "add", "Calibration size", "--id", "cal", "-q", "q-acc", "--purpose", "top-1 at calibration size 512", agent=True)
    rec.ok("decide", "int8", "park", "--why", "later", agent=True)
    call = rec.ok("status")["data"]["pending"][0]["id"]
    every = f"{call} {rec.design('int8')['confirm'].split()[-1]} {rec.design('cal')['confirm'].split()[-1]}"
    assert rec.text("confirm").rstrip("\n").endswith(f"rb confirm {every} confirms 1 call and 2 designs as listed.")
    never = json.loads(rec.run("confirm", "int8@0000000", "--json").stdout)["errors"][0]
    assert never["message"] == "int8's design does not match 0000000: it changed after it was listed, or rb never listed that key. It reads now:", never
    key = rec.design("cal")["confirm"].split("@")[1]
    rec.ok("confirm", f"cal@{key}")
    log = rec.text("log")
    assert "design experiment cal confirmed\n" in log and "gate experiment cal" not in log, log
    for argv in (("confirm", f"cal@{key}"), ("confirm", "cal"), ("confirm", "cal@zzz")):
        again = json.loads(rec.run(*argv, "--json").stdout)["errors"][0]
        assert (again["message"], again["fix"]) == ("cal's design waits for no confirmation.", "rb confirm lists what waits."), (argv, again)


def test_the_same_key_given_twice_confirms_the_design_once(rec):
    """Recheck of group E: `rb confirm cal@<key> cal@<key>` confirmed the design, then failed on the second id, exit
    2, saying both that it was confirmed and that nothing waited. Each id given counts once."""
    rec.ok("experiment", "add", "Calibration size", "--id", "cal", "-q", "q-acc", "--purpose", "top-1 at calibration size 512", agent=True)
    cmd = rec.design("cal")["confirm"]
    twice = rec.run(*cmd.split()[1:], cmd.split()[2], "--json")
    assert twice.returncode == 0 and [d["experiment"] for d in json.loads(twice.stdout)["data"]["designs_confirmed"]] == ["cal"], twice.stdout
    assert rec.design("cal")["standing"] == "confirmed"


def test_each_change_since_a_confirmation_is_said_where_its_part_is_listed(rec):
    """A session set optim.lr with no source after the user confirmed int8's design, and changed acc-tol's rule: the
    source taken away was said last, after the criterion, apart from its setting. Each change is said where its part
    is listed: a setting's value and its source together, before the criteria."""
    session_design(rec)
    confirm(rec)
    rec.ok("spec", "set", "int8", "optim.lr", "0.002", agent=True)
    edit(rec, "experiments/int8/criteria/acc-tol.yaml", "target: -0.005", "target: -0.003")
    rec.ok("doctor", "--adopt", "--why", "the plan says 0.3", agent=True)
    lines = [l.strip() for l in rec.text("show", "int8").splitlines() if l.strip().startswith("changed since you confirmed it:")]
    assert lines == ["changed since you confirmed it: setting optim.lr, was 0.001, now 0.002",
                     "changed since you confirmed it: setting optim.lr's source removed, was configs/train.yaml#optim.lr",
                     "changed since you confirmed it: criterion acc-tol's rule, was change.top1 ≥ -0.005, now change.top1 ≥ -0.003"], lines


def test_a_variant_file_edited_outside_rb_after_a_confirmation_reads_changed_until_it_is_put_back(rec):
    """A session made int8 with its variants in one line, and the user confirmed it; then the variant file was edited
    by hand and not adopted. The design as it reads is not the one the user confirmed, so it reads changed (snapshot
    rule, Oct 14), said as what changed and never as whose; rb doctor lists the edit, rb confirm refuses a design rb
    did not write, and once rb doctor --restore puts back what rb wrote it reads confirmed again."""
    session_design(rec)
    confirm(rec)
    edit(rec, "experiments/int8/variants/int8.yaml", "role: candidate", "role: ablation")
    assert changes(rec) == [("variant int8", "changed", "candidate", "ablation")], changes(rec)
    assert "edited_outside_rb" in rec.text("doctor")
    r = rec.run(*rec.design("int8")["confirm"].split()[1:], "--json")
    assert r.returncode != 0 and json.loads(r.stdout)["errors"][0]["code"] == "E_STATE_EDITED", r.stdout
    rec.ok("doctor", "--restore")
    assert rec.design("int8")["standing"] == "confirmed" and "changed since you confirmed it" not in rec.text("show", "int8")


# Recheck of group E, round 2.

def git(rec: Rec, *argv: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *argv], cwd=rec.root, env=rec.env, check=True, capture_output=True, encoding="utf-8")


@pytest.mark.parametrize("direction", ["into-main", "into-side"])
def test_the_users_write_of_what_a_session_wrote_the_same_on_a_branch_waits_for_one_confirmation(rec, direction):
    """Recheck of group E: the user's own design; on a branch a session filed int8 under q-lat, varied data.batch_size
    and set the gate's if-holds branch, and on main the user did the same in their own terminal. After a clean merge
    the design read `design not confirmed`, though every part it listed was one the user wrote. By the snapshot rule
    (Oct 14) a session's lines touched it, so it waits for one confirmation, which a merge needs no order to read; once
    the user confirms it, nothing waits."""
    rec.ok("question", "add", "Is INT8 fast enough?", "--id", "q-lat")
    user_design(rec)
    rec.commit()
    git(rec, "checkout", "-q", "-b", "side")
    steps = (("experiment", "file", "int8", "q-lat"), ("spec", "vary", "int8", "data.batch_size"), ("experiment", "gate", "int8", "--if-holds", "ship INT8 soon"))
    for argv in steps:
        rec.ok(*argv, agent=True)
    rec.commit()
    git(rec, "checkout", "-q", "-")
    for argv in steps:
        rec.ok(*argv)
    assert rec.design("int8")["standing"] == "yours"
    rec.commit()
    if direction == "into-main":
        git(rec, "merge", "-q", "--no-edit", "side")
    else:
        git(rec, "checkout", "-q", "side")
        git(rec, "merge", "-q", "--no-edit", "-")
    d = rec.design("int8")
    assert (d["standing"], d["changes"]) == ("not_confirmed", []), d
    assert LISTING in rec.text("show", "int8") and rec.text("confirm") != "Nothing waits for a confirmation.\n"
    rec.ok(*d["confirm"].split()[1:])
    assert rec.design("int8")["standing"] == "confirmed" and rec.text("confirm") == "Nothing waits for a confirmation.\n"


@pytest.mark.parametrize("confirmed", [True, False], ids=["after-a-confirmation", "on-the-users-design"])
def test_a_setting_the_user_states_as_a_session_left_it_waits_for_one_confirmation(rec, confirmed):
    """Recheck of group E: a session set optim.wd to 0.0005, gave optim.lr a source and marked it optional; the user
    then typed the same in their own terminal. By the snapshot rule (Oct 14) a user's write to a design that waits
    makes no design theirs: it still waits, said against what they confirmed, until they confirm it once."""
    if confirmed:
        session_design(rec)
        confirm(rec)
    else:
        user_design(rec)
    for argv in (("spec", "set", "int8", "optim.wd", "0.0005"), ("spec", "set", "int8", "optim.lr", "--source", "configs/train.yaml#optim.lr", "--optional")):
        rec.ok(*argv, agent=True)
        assert rec.design("int8")["standing"] == ("changed" if confirmed else "not_confirmed"), argv
        rec.ok(*argv)
        assert rec.design("int8")["standing"] == ("changed" if confirmed else "not_confirmed"), argv
    if confirmed:
        assert changes(rec) == [("setting optim.lr", "changed", "0.001", "0.001 (optional)"), ("setting optim.wd", "added", None, "0.0005")], changes(rec)
    rec.ok(*rec.design("int8")["confirm"].split()[1:])
    d = rec.design("int8")
    assert (d["standing"], d["changes"]) == ("confirmed", []), d
    assert "design not confirmed" not in rec.text("show", "int8")


def test_filing_a_criterion_in_the_words_it_has_says_nothing_changed(rec):
    """The user filed acc-tol under q-acc in the words a session had just declared there. rb kept the session's
    declaration, as the same words do, and wrote nothing, yet said `acc-tol: declared for question q-acc.`: the user
    read their filing as made while the design still waited on it. It says nothing changed, and that the design waits."""
    user_design(rec)
    rec.ok("criterion", "add", "INT8 loses at most 0.5 top-1", "-e", "int8", "-q", "q-acc", "--metric", "change.top1", "--at-least", "-0.005", "--id", "acc-tol")
    argv = ("criterion", "file", "acc-tol", "q-acc", "--when-met", "INT8 is good enough", "--why", "it bears on it")
    rec.ok(*argv, agent=True)
    cmd = rec.design("int8")["confirm"]
    said = rec.text(*argv)
    assert said.startswith(f"acc-tol: already declared for question q-acc in these words; nothing changed.\n  {LISTING}\n"), said
    assert rec.design("int8")["confirm"] == cmd


def test_a_sessions_why_the_design_waits_on_again_reads_as_not_confirmed(rec):
    """Recheck of group E: the user confirmed a why a session gave, then wrote their own, and a session put a why of
    its own there. rb show said the why changed since the confirmation, yet read it as a confirmed note, without `·
    not confirmed`. A why that is in no design the user owns reads as not confirmed; one that puts back exactly the
    design they confirmed is theirs again, a note with no mark (snapshot rule, Oct 14)."""
    user_design(rec)
    why = "experiments/int8/experiment.yaml"
    edit(rec, why, "why: docs/plan.md:3 names INT8", "why: docs/plan.md:4 names INT8")
    rec.ok("doctor", "--adopt", "--why", "the plan moved", agent=True)
    confirm(rec)
    edit(rec, why, "why: docs/plan.md:4 names INT8", "why: the plan says so")
    rec.ok("doctor", "--adopt", "--why", "my own reason")
    assert rec.design("int8")["standing"] == "confirmed" and "  why: the plan says so\n" in rec.text("show", "int8")
    edit(rec, why, "why: the plan says so", "why: docs/plan.md:5 names INT8")
    rec.ok("doctor", "--adopt", "--why", "the plan moved again", agent=True)
    shown = rec.text("show", "int8")
    assert '  changed since you confirmed it: why, was "the plan says so", now "docs/plan.md:5 names INT8"\n' in shown, shown
    assert "  note: docs/plan.md:5 names INT8 · not confirmed\n" in shown, shown
    edit(rec, why, "why: docs/plan.md:5 names INT8", "why: docs/plan.md:4 names INT8")
    rec.ok("doctor", "--adopt", "--why", "the plan as confirmed", agent=True)
    shown = rec.text("show", "int8")
    assert rec.design("int8")["standing"] == "confirmed" and "  note: docs/plan.md:4 names INT8\n" in shown, shown


@pytest.mark.parametrize("into", ["main", "side"])
def test_a_design_the_user_confirmed_on_a_branch_reads_confirmed_after_a_merge(rec, into):
    """Recheck of group E: on a branch a session set optim.lr and the user confirmed the design with it; on main the user
    confirmed the design as it was. After a clean merge the design read exactly as the branch's confirmation listed it,
    yet `changed since you confirmed it: setting optim.lr`, and rb asked again: only the latest confirmation counted. A
    session's write a confirmation saw and listed as it reads now is the user's; one neither saw still reopens it."""
    session_design(rec)
    rec.commit()
    git(rec, "checkout", "-q", "-b", "side")
    rec.ok("spec", "set", "int8", "optim.lr", "0.002", agent=True)
    rec.ok(*rec.design("int8")["confirm"].split()[1:])
    rec.commit()
    git(rec, "checkout", "-q", "-")
    confirm(rec)
    rec.commit()
    if into == "main":
        git(rec, "merge", "-q", "--no-edit", "side")
    else:
        git(rec, "checkout", "-q", "side")
        git(rec, "merge", "-q", "--no-edit", "-")
    d = rec.design("int8")
    assert (d["standing"], d["changes"]) == ("confirmed", []), d
    assert "design not confirmed" not in rec.text("show", "int8")
    rec.ok("spec", "set", "int8", "optim.wd", "0.0005", agent=True)
    assert changes(rec) == [("setting optim.wd", "added", None, "0.0005")], changes(rec)


def test_a_design_that_waits_waits_in_a_clone_of_its_bundle(rec, tmp_path):
    """Recheck of group E: rb clone wrote every experiment as the cloner's, so a design a session wrote, which waited in
    the bundle's record, arrived as the user's: no standing line, the session's why in the researcher's voice, and rb
    confirm said nothing waited. A clone keeps who wrote each part that waits; one the user confirmed arrives as theirs."""
    session_design(rec)
    rec.ok("experiment", "add", "Calibration size", "--id", "cal", "-q", "q-acc", "--purpose", "top-1 at calibration size 512", agent=True)
    rec.ok(*rec.design("cal")["confirm"].split()[1:])
    rec.ok("export", "--with-record", "--out", str(tmp_path / "bundle"))
    home = tmp_path / "cloner"
    home.mkdir()
    rec.ok("clone", str(tmp_path / "bundle"), str(home / "int8"))
    other = Rec.__new__(Rec)
    other.root, other.env = home / "int8", rec.env
    d = other.design("int8")
    assert (d["standing"], d["not_recorded"]) == ("not_confirmed", []), d
    shown = other.text("show", "int8")
    assert f"  {LISTING}\n" in shown and "  note: docs/plan.md:3 names INT8 · not confirmed\n" in shown, shown
    assert other.design("cal")["standing"] == "yours" and "design not confirmed" not in other.text("show", "cal")
    other.ok(*d["confirm"].split()[1:])
    assert other.design("int8")["standing"] == "confirmed"


def test_a_reason_given_with_an_interpretation_the_user_confirmed_reads_as_its_note(rec):
    """Recheck of group E: a session filed acc-tol under q-acc with what meeting it would mean and a reason; once the user
    confirmed the design, rb confirm said nothing waited, yet the reason still ended `· not confirmed`, a mark no
    command could clear. It reads as the session's note, as a confirmed call's reason does."""
    user_design(rec)
    rec.ok("criterion", "add", "INT8 loses at most 0.5 top-1", "-e", "int8", "--metric", "change.top1", "--at-least", "-0.005", "--id", "acc-tol")
    rec.ok("criterion", "file", "acc-tol", "q-acc", "--when-met", "INT8 is good enough", "--why", "it bears on it", agent=True)
    assert "declared when met, not confirmed: 'INT8 is good enough'; note: it bears on it." in rec.text("status")
    confirm(rec)
    status = rec.text("status")
    assert "declared when met: 'INT8 is good enough'; note: it bears on it." in status and "it bears on it · not confirmed" not in status, status
    assert rec.text("confirm") == "Nothing waits for a confirmation.\n"


def test_the_command_rb_confirm_ends_with_confirms_only_what_it_listed(rec):
    """Recheck of group E: rb confirm listed int8's design, bsz's and a call, and ended with `rb confirm --all`. A
    session then changed int8's gate, with words, and a setting, and added an experiment; the user typed the line they
    were given, and --all confirmed all of it as it read then, the session's words as the user's. The listing ends with
    the one command that names what it listed, which refuses a design changed since."""
    session_design(rec)
    rec.ok("experiment", "add", "Batch size probe", "--id", "bsz", "-q", "q-acc", "--purpose", "top-1 at batch 128", agent=True)
    rec.ok("decide", "int8", "park", "--why", "later", agent=True)
    last = rec.text("confirm").rstrip("\n").splitlines()[-1]
    assert "--all" not in last, last
    cmd = last.split("; ")[1].split(" confirms ")[0]
    rec.ok("experiment", "gate", "int8", "--if-holds", "ship INT8 to all customers", "--words", "I promised INT8 to all customers", agent=True)
    rec.ok("spec", "set", "int8", "optim.lr", "0.5", agent=True)
    rec.ok("experiment", "add", "Sink", "--id", "snk", "-q", "q-acc", "--purpose", "top-1 with a sink token", agent=True)
    r = rec.run(*cmd.split()[1:], "--json")
    err = json.loads(r.stdout)["errors"][0]
    assert r.returncode != 0 and err["message"].startswith("int8's design does not match "), err
    assert rec.design("int8")["pending"] and rec.design("snk")["pending"]
    assert "your words" not in rec.text("show", "int8")


def test_an_experiment_a_session_imported_says_what_it_does_not_record(rec):
    """Recheck of group E: a session ran rb reconcile --apply on results folders; each experiment it made recorded
    neither a why nor a hypothesis or purpose, yet rb show said neither, and --json gave not_recorded []: a design with
    no parts read as the user's. An experiment a session made says what it does not record, as one with a part does;
    the user's own import says nothing new."""
    for n in range(2):
        rec.write(f"results/sweep{n}/README.md", f"# Sweep {n}\n\nBatch size sweep.\n")
        rec.write(f"results/sweep{n}/config.yaml", f"optim:\n  lr: 0.00{n + 1}\n")
        rec.write(f"results/sweep{n}/metrics.json", f'{{"top1": 0.7{n}}}\n')
    rec.commit()
    rec.ok("reconcile", "--apply", agent=True)
    d = rec.design("sweep0")
    assert (d["standing"], d["not_recorded"]) == ("yours", ["hypothesis or purpose", "why"]), d
    shown = rec.text("show", "sweep0")
    assert "  hypothesis or purpose: not recorded\n" in shown and "  why: not recorded\n" in shown, shown
    assert "not recorded" not in rec.text("status")


def test_a_page_says_a_change_since_the_confirmation_to_whoever_reads_it(rec, tmp_path):
    """Recheck of group E: a page anyone may be sent said `changed since you confirmed it`, naming its reader as the
    one who confirmed. A page and an export say `changed since it was confirmed`; the user's own screens keep `you`."""
    session_design(rec)
    confirm(rec)
    rec.ok("spec", "set", "int8", "optim.lr", "0.002", agent=True)
    line = "setting optim.lr, was 0.001, now 0.002"
    rec.ok("export", "--out", str(tmp_path / "export"))
    exported = html_mod.unescape(re.sub(r"<[^>]+>", "\n", (tmp_path / "export" / "page.html").read_text(encoding="utf-8")))
    for page in (rec.page(), exported):
        assert f"changed since it was confirmed: {line}" in page and "you confirmed" not in page, page
    assert f"  changed since you confirmed it: {line}\n" in rec.text("show", "int8")


def test_an_empty_key_is_refused_as_naming_none(rec):
    """Recheck of group E: `rb confirm int8@` said int8's design did not match `` and that it had changed since it was
    listed. A key left empty after the @ is a usage error that says how to get the key."""
    session_design(rec)
    err = json.loads(rec.run("confirm", "int8@", "--json").stdout)["errors"][0]
    assert (err["code"], err["message"]) == ("E_USAGE", "int8@ names no key after the @."), err
    assert err["fix"] == "rb confirm int8 lists its design with its key: rb confirm int8@<key>.", err
    assert rec.design("int8")["pending"]


def merged(rec: Rec, into: str) -> None:
    """Merge the branch side and the line it left, into either, as git merges two lines of work."""
    if into == "main":
        git(rec, "merge", "-q", "--no-edit", "side")
    else:
        git(rec, "checkout", "-q", "side")
        git(rec, "merge", "-q", "--no-edit", "-")
    assert rec.run("doctor").returncode == 0, rec.text("doctor")


ACC_TOL = "experiments/int8/criteria/acc-tol.yaml"


@pytest.mark.parametrize("into", ["main", "side"])
@pytest.mark.parametrize("confirmed", ["before-both", "after-the-users-change"])
def test_a_sessions_change_git_merged_with_the_users_in_one_part_waits(rec, tmp_path, into, confirmed):
    """Recheck of group E, round 3: on a branch a session set acc-tol's min_n to 3; on main the user then tightened its
    target (and in one case confirmed the design). After a clean merge the rule reads both changes, yet the design read
    as confirmed, with the session's change in it: the user's write was the latest in time, so the part read as theirs.
    A part that reads what no one write left waits while a session's write in it is one no confirmation saw."""
    session_design(rec)
    if confirmed == "before-both":
        confirm(rec)
    rec.commit()
    git(rec, "checkout", "-q", "-b", "side")
    edit(rec, ACC_TOL, "min_n: 1", "min_n: 3")
    rec.ok("doctor", "--adopt", "--why", "three seeds at least", agent=True)
    rec.commit()
    git(rec, "checkout", "-q", "-")
    edit(rec, ACC_TOL, "target: -0.005", "target: -0.003")
    rec.ok("doctor", "--adopt", "--why", "a tighter budget")
    if confirmed == "after-the-users-change":
        confirm(rec)
    rec.commit()
    merged(rec, into)
    for there in (rec, clone_of(rec, tmp_path)):
        assert changes(there) == [("criterion acc-tol's rule", "changed", "change.top1 ≥ -0.003", "change.top1 ≥ -0.003 (n ≥ 3)")], changes(there)
        assert "  changed since you confirmed it: criterion acc-tol's rule, was change.top1 ≥ -0.003, now change.top1 ≥ -0.003 (n ≥ 3)\n" in there.text("show", "int8")
        assert there.text("confirm") != "Nothing waits for a confirmation.\n"


@pytest.mark.parametrize("into", ["main", "side"])
def test_the_users_change_merged_beside_a_confirmed_session_change_asks_again(rec, tmp_path, into):
    """Recheck of group E, round 3: on a branch the user tightened acc-tol's target; on main a session then set its
    min_n to 3, and the user confirmed that. After a clean merge the rule reads both, a design the user owns on neither
    line: by the snapshot rule (Oct 14) a merge can only make rb ask again. It says how the rule differs from the
    design the user owns closest to it (each is one part away; the latest in the log), the same in a clone, and once
    the user confirms it nothing waits."""
    session_design(rec)
    confirm(rec)
    rec.commit()
    git(rec, "checkout", "-q", "-b", "side")
    edit(rec, ACC_TOL, "target: -0.005", "target: -0.003")
    rec.ok("doctor", "--adopt", "--why", "a tighter budget")
    rec.commit()
    git(rec, "checkout", "-q", "-")
    edit(rec, ACC_TOL, "min_n: 1", "min_n: 3")
    rec.ok("doctor", "--adopt", "--why", "three seeds at least", agent=True)
    confirm(rec)
    rec.commit()
    merged(rec, into)
    for there in (rec, clone_of(rec, tmp_path)):
        assert changes(there) == [("criterion acc-tol's rule", "changed", "change.top1 ≥ -0.005 (n ≥ 3)", "change.top1 ≥ -0.003 (n ≥ 3)")], changes(there)
        assert there.text("confirm") != "Nothing waits for a confirmation.\n"
    confirm(rec)
    assert rec.text("confirm") == "Nothing waits for a confirmation.\n"


def test_a_merge_resolved_to_a_sessions_side_waits_after_the_user_adopts_it(rec):
    """Recheck of group E, round 3: a session set optim.lr to 0.002 on a branch and the user set 0.003 on main; the merge
    was resolved by keeping the branch's file and the user adopted it as it stands, changing no part. The setting read
    the session's 0.002 as confirmed: the user's write was the latest in time. The part reads as the write it holds."""
    session_design(rec)
    confirm(rec)
    rec.commit()
    git(rec, "checkout", "-q", "-b", "side")
    rec.ok("spec", "set", "int8", "optim.lr", "0.002", agent=True)
    rec.commit()
    git(rec, "checkout", "-q", "-")
    rec.ok("spec", "set", "int8", "optim.lr", "0.003")
    rec.commit()
    assert subprocess.run(["git", "merge", "-q", "--no-edit", "side"], cwd=rec.root, env=rec.env, capture_output=True).returncode != 0
    git(rec, "checkout", "--theirs", ".rb/experiments/int8/experiment.yaml")
    rec.commit()
    rec.ok("doctor", "--adopt", "--why", "kept the branch's value")
    assert ("setting optim.lr", "changed", "0.003", "0.002") in changes(rec), changes(rec)
    assert "  changed since you confirmed it: setting optim.lr, was 0.003, now 0.002\n" in rec.text("show", "int8")


@pytest.mark.parametrize("into", ["main", "side"])
def test_a_sessions_retraction_merged_with_the_users_change_says_what_was_removed(rec, into):
    """Recheck of group E, round 3: on a branch a session took back acc-tol, and the variant int8pc; on main the user
    then tightened acc-tol's target and made int8pc a control. After a clean merge rb said the criterion's role was
    removed and its filing under q-acc, but not that the criterion or the variant was: the user's write was the latest
    to the rule and the variant's role. A removal is said against the user's value."""
    session_design(rec)
    rec.ok("variant", "add", "int8", "int8pc", "--role", "ablation", agent=True)
    confirm(rec)
    rec.commit()
    git(rec, "checkout", "-q", "-b", "side")
    rec.ok("retract", "acc-tol", "-m", "drop it", agent=True)
    rec.ok("retract", "int8/int8pc", "-m", "drop it", agent=True)
    rec.commit()
    git(rec, "checkout", "-q", "-")
    edit(rec, ACC_TOL, "target: -0.005", "target: -0.003")
    edit(rec, "experiments/int8/variants/int8pc.yaml", "role: ablation", "role: control")
    rec.ok("doctor", "--adopt", "--why", "my changes")
    rec.commit()
    merged(rec, into)
    shown = rec.text("show", "int8")
    for line in ("criterion acc-tol removed, was change.top1 ≥ -0.003 (primary)", "variant int8pc (control) removed"):
        assert f"  changed since you confirmed it: {line}\n" in shown, shown
    assert "acc-tol's role removed" not in shown and "no longer filed under" not in shown, shown


def test_an_older_rbs_adoption_by_the_user_keeps_no_design_and_reads_the_same_in_a_clone(rec, tmp_path):
    """Recheck of group E, round 3: after the user confirmed int8's design, the user made acc-tol secondary by an
    adoption an older rb wrote (no changed_parts), then a session changed the role again. In a clone the design read as
    confirmed. By the snapshot rule (Oct 14) an older rb's write keeps no design the user owns, so it can only make rb
    ask again: the design waits, said against what the user confirmed, in the record and in its clone alike."""
    session_design(rec)
    confirm(rec)
    edit(rec, ACC_TOL, "role: primary", "role: secondary")
    rec.ok("doctor", "--adopt", "--why", "it is secondary")
    log = rec.root / ".rb" / "log.jsonl"
    lines = [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines()]
    [users] = [x for x in lines if x["op"] == "adopt" and x["actor"].startswith("human:")]
    users["detail"].pop("changed_parts")
    users["detail"].pop("owned")
    log.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in lines), encoding="utf-8", newline="")
    rec.commit()
    assert changes(rec) == [("criterion acc-tol's role", "changed", "primary", "secondary")], changes(rec)
    edit(rec, ACC_TOL, "role: secondary", "role: exploratory")
    rec.ok("doctor", "--adopt", "--why", "it is exploratory", agent=True)
    rec.commit()
    there = clone_of(rec, tmp_path)
    for d in (rec, there):
        assert changes(d) == [("criterion acc-tol's role", "changed", "primary", "exploratory")], changes(d)
        assert "  changed since you confirmed it: criterion acc-tol's role, was primary, now exploratory\n" in d.text("show", "int8")


def test_a_gate_a_session_put_back_as_the_user_had_it_waits_once_with_its_design(rec):
    """Recheck of group E, round 3: the user wrote int8's design with its gate; a session changed the gate's if-holds
    branch and then put it back. The gate waited on its own, with a key of its own. By the snapshot rule (Oct 14) the
    session's lines touched the design, so the gate waits with its design under the design's one command, never its
    own, until the user confirms the design once; then nothing waits and the gate is theirs."""
    user_design(rec)
    rec.ok("experiment", "gate", "int8", "--if-holds", "ship INT8 now", agent=True)
    assert rec.ok("status")["data"]["gates_pending"], "a session's gate waits with its design"
    rec.ok("experiment", "gate", "int8", "--if-holds", "ship INT8", agent=True)
    st = rec.ok("status")["data"]
    [e] = st["experiments"]
    cmd = e["design"]["confirm"]
    assert (e["design"]["standing"], [g["confirm"] for g in st["gates_pending"]]) == ("not_confirmed", [cmd]), (e, st["gates_pending"])
    assert "if it holds: ship INT8 · not confirmed\n" in rec.text("show", "int8") and "Gates set, not confirmed" not in rec.text("status")
    rec.ok(*cmd.split()[1:])
    st = rec.ok("status")["data"]
    assert (st["experiments"][0]["design"]["standing"], st["gates_pending"], st["experiments"][0]["gate_by"]) == ("confirmed", [], "human:ana"), st["experiments"][0]
    for argv in (("status",), ("show", "int8"), ("context",)):
        text = rec.text(*argv)
        assert "not confirmed" not in text and "rb confirm int8" not in text, (argv, text)
    assert rec.text("confirm") == "Nothing waits for a confirmation.\n"


def test_rb_confirm_naming_several_designs_ends_with_the_command_for_all_and_a_status_of_one_names_its_own(rec):
    """Recheck of group E, round 3: `rb confirm int8 bsz` listed both designs, each with its command, but not the one
    command that confirms both, as rb confirm alone ends; and `rb status bsz` said rb confirm lists bsz's design,
    though rb confirm lists every design and call that waits."""
    session_design(rec)
    rec.ok("experiment", "add", "Batch size probe", "--id", "bsz", "-q", "q-acc", "--purpose", "top-1 at batch 128", agent=True)
    rec.ok("decide", "int8", "park", "--why", "later", agent=True)
    keys = f"{rec.design('int8')['confirm'].split()[-1]} {rec.design('bsz')['confirm'].split()[-1]}"
    end = f"rb confirm <experiment>@<hash> confirms one; rb confirm {keys} confirms these 2 as listed.\n"
    assert rec.text("confirm", "int8", "bsz").endswith(end), rec.text("confirm", "int8", "bsz")
    assert "\nThe design of 1 experiment is not confirmed: rb confirm bsz lists it.\n" in rec.text("status", "bsz"), rec.text("status", "bsz")
    assert "\nThe design of 2 experiments is not confirmed: rb confirm lists them.\n" in rec.text("status")


# The snapshot rule (Oct 14): no order, no clock, no copy of what rb wrote.

def test_a_sessions_change_cherry_picked_beside_the_users_waits(rec):
    """Recheck of group E, round 3 (MX3, MX4): a session's change on a branch, brought in by a cherry-pick beside the
    user's own change, read as confirmed. The design as it reads is no design the user owns: it waits, said against
    the closest one they do."""
    session_design(rec)
    confirm(rec)
    rec.commit()
    git(rec, "checkout", "-q", "-b", "side")
    edit(rec, ACC_TOL, "min_n: 1", "min_n: 3")
    rec.ok("doctor", "--adopt", "--why", "three seeds at least", agent=True)
    rec.commit()
    picked = git(rec, "rev-parse", "HEAD").stdout.strip()
    git(rec, "checkout", "-q", "-")
    edit(rec, ACC_TOL, "target: -0.005", "target: -0.003")
    rec.ok("doctor", "--adopt", "--why", "a tighter budget")
    rec.commit()
    git(rec, "cherry-pick", picked)
    assert changes(rec) == [("criterion acc-tol's rule", "changed", "change.top1 ≥ -0.003", "change.top1 ≥ -0.003 (n ≥ 3)")], changes(rec)
    assert rec.text("confirm") != "Nothing waits for a confirmation.\n"


def test_a_clock_says_nothing_of_a_designs_standing(rec, monkeypatch):
    """Recheck of group E, round 2: with the user's clock ahead of the session's, a session's change made after the
    confirmation was dated before it and read as confirmed. The standing asks only whether the design is exactly one
    the user owns, whatever the times of the lines."""
    session_design(rec)
    ahead = {**rec.env, "RB_TEST_NOW": "2099-01-01T00:00:00+00:00"}
    for argv in (("spec", "set", "int8", "optim.wd", "0.0005"), None):
        argv = argv or rec.design("int8")["confirm"].split()[1:]
        r = subprocess.run([*command(), *argv], cwd=rec.root, env=ahead, capture_output=True, stdin=subprocess.DEVNULL, encoding="utf-8")
        assert r.returncode == 0, (argv, r.stdout, r.stderr)
    assert rec.design("int8")["standing"] == "confirmed"
    rec.ok("spec", "set", "int8", "optim.wd", "0.001", agent=True)
    assert ("setting optim.wd", "changed", "0.0005", "0.001") in changes(rec), changes(rec)


def test_only_the_users_writes_to_a_design_a_session_touched_keep_a_design(rec):
    """Rule 2 of the snapshot rule: a line keeps a design (`detail.owned`) only where the user's own write made it from
    one they owned, worked out as it is written: no line of a design no session touched keeps one, nor a session's
    line, nor the user's write to a design that waits, nor a write that changes none of its parts; the confirmation
    keeps the design it confirms, and the user's next write the design it makes. Each keeps one design's parts."""
    def kept() -> list:
        lines = [json.loads(x) for x in (rec.root / ".rb" / "log.jsonl").read_text(encoding="utf-8").splitlines()]
        return [(x["op"], x["actor"], sorted((x.get("detail") or {}).get("owned", {}))) for x in lines if "owned" in (x.get("detail") or {})]
    user_design(rec)
    rec.ok("spec", "set", "int8", "optim.wd", "0.0001")
    assert kept() == []
    rec.ok("spec", "set", "int8", "data.batch_size", "512", agent=True)
    rec.ok("spec", "set", "int8", "data.batch_size", "256")
    assert kept() == [] and rec.design("int8")["standing"] == "not_confirmed"
    confirmed = rec.design("int8")
    rec.ok(*confirmed["confirm"].split()[1:])
    assert kept() == [("gate", "human:ana", ["int8"])]
    rec.ok("note", "add", "the eval set is fixed", "-e", "int8")
    rec.ok("spec", "set", "int8", "optim.wd", "0.0001")
    assert len(kept()) == 1
    rec.ok("spec", "set", "int8", "optim.wd", "0.0002")
    assert kept() == [("gate", "human:ana", ["int8"]), ("spec_set", "human:ana", ["int8"])] and rec.design("int8")["standing"] == "confirmed"
    [confirmation, line] = [json.loads(x) for x in (rec.root / ".rb" / "log.jsonl").read_text(encoding="utf-8").splitlines() if "owned" in x]
    entry = line["detail"]["owned"]["int8"]
    assert entry["key"] == rec.design("int8")["key"] and entry["key"] != confirmed["key"]
    base = confirmation["detail"]["owned"]["int8"]
    assert entry["from"] == base["key"] and entry["parts"] == {"setting:optim.wd": {"value": 0.0002}}
    assert base["parts"]["source:optim.lr"] == {"kind": "file", "path": "configs/train.yaml", "key": "optim.lr"}



# Review of the snapshot rule, round 1.

def users_design_with_runs(rec: Rec) -> None:
    """int8's design written wholly in the user's own terminal, from the training config, with a criterion and three
    runs on a config that states the same values."""
    rec.ok("experiment", "add", "INT8 vs FP32", "--id", "int8", "-q", "q-acc", "--hypothesis", HYPOTHESIS, "-m", "docs/plan.md:3 names INT8",
           "--baseline", "fp32", "--candidate", "int8")
    rec.ok("spec", "set", "int8", "--from", "configs/train.yaml", "--keys", "optim.*,data.batch_size")
    rec.ok("criterion", "add", "INT8 loses at most 0.5 top-1", "-e", "int8", "-q", "q-acc", "--metric", "change.top1", "--at-least", "-0.005", "--id", "acc-tol")
    rec.write("out/seed2/config.yaml", "optim:\n  lr: 0.001\n  wd: 0.0001\ndata:\n  batch_size: 256\n")
    for s in range(1, 4):
        rec.ok("run", "add", "int8", f"fp32.top1=0.76{s}", f"int8.top1=0.75{s + 5}", "--config", "out/seed2/config.yaml")
    assert rec.design("int8")["standing"] == "yours"


def test_a_sessions_change_rb_says_alike_is_a_write_to_the_design(rec):
    """Review of the snapshot rule, round 1: a session's adoption that only turned data.batch_size's 256 into the text
    '256', and a session's source given another --term, changed the design's key and its criterion's reading, yet the
    user's own design still read as theirs: the line's changed_parts compared parts as rb says them. A part is compared
    in rb's structured form, so each is a write to the design, which waits."""
    users_design_with_runs(rec)
    before = rec.readings()
    edit(rec, "experiments/int8/experiment.yaml", "value: 256", "value: '256'")
    rec.ok("doctor", "--adopt", "--why", "tidy", agent=True)
    [line] = [json.loads(x) for x in (rec.root / ".rb" / "log.jsonl").read_text(encoding="utf-8").splitlines()[-1:]]
    assert "setting:data.batch_size" in line["detail"]["changed_parts"]["int8"], line
    assert rec.readings() != before and rec.design("int8")["standing"] == "not_confirmed"
    assert LISTING in rec.text("show", "int8")


def test_a_sessions_new_term_for_a_source_is_a_write_to_the_design(rec):
    """Review of the snapshot rule, round 1: the term a source is read by changes what rb checks, though the source reads
    as before (docs/train.md:3). A session's adoption of another term in the user's own design waits, and a session's
    write of one reopens a design the user confirmed."""
    rec.write("docs/train.md", "# Training\n\nWe train with a learning rate of 0.001 and a weight decay of 0.0001 for every run.\n")
    users_design_with_runs(rec)
    rec.ok("spec", "set", "int8", "optim.lr", "0.001", "--source", "docs/train.md:3", "--term", "learning rate")
    assert rec.design("int8")["standing"] == "yours"
    edit(rec, "experiments/int8/experiment.yaml", "term: learning rate", "term: weight decay")
    rec.ok("doctor", "--adopt", "--why", "the other term", agent=True)
    assert rec.design("int8")["standing"] == "not_confirmed"
    edit(rec, "experiments/int8/experiment.yaml", "term: weight decay", "term: learning rate")
    rec.ok("doctor", "--adopt", "--why", "the first term")
    rec.ok(*rec.design("int8")["confirm"].split()[1:])
    rec.ok("spec", "set", "int8", "optim.lr", "0.001", "--source", "docs/train.md:3", "--term", "weight decay", agent=True)
    assert rec.design("int8")["standing"] == "changed"


def test_a_criterion_a_session_moved_to_another_experiment_is_a_write_to_both_designs(rec):
    """Review of the snapshot rule, round 1: a session moved criterion c9 from int8 to bsz by hand and adopted it. The
    line named bsz with no part changed, and int8 not at all, so both of the user's designs still read as theirs. A file
    that moves takes its parts from one design and gives them to the other: both wait."""
    user_design(rec)
    rec.ok("criterion", "add", "INT8 keeps 0.75 top-1", "-e", "int8", "--metric", "int8.top1", "--at-least", "0.75", "--id", "c9")
    rec.ok("experiment", "add", "Batch size probe", "--id", "bsz", "--purpose", "the largest batch that fits", "--baseline", "b256", "--candidate", "b512")
    assert [rec.design(e)["standing"] for e in ("int8", "bsz")] == ["yours", "yours"]
    old = rec.root / ".rb" / "experiments" / "int8" / "criteria" / "c9.yaml"
    rec.write(".rb/experiments/bsz/criteria/c9.yaml", old.read_text(encoding="utf-8").replace("experiment: int8", "experiment: bsz"))
    old.unlink()
    rec.ok("doctor", "--adopt", "--why", "it belongs to bsz", agent=True)
    [line] = [json.loads(x) for x in (rec.root / ".rb" / "log.jsonl").read_text(encoding="utf-8").splitlines()[-1:]]
    assert sorted(line["detail"]["changed_parts"]) == ["bsz", "int8"] and all(line["detail"]["changed_parts"].values()), line
    assert [rec.design(e)["standing"] for e in ("int8", "bsz")] == ["not_confirmed", "not_confirmed"]


def test_a_sessions_write_that_changes_no_part_leaves_the_users_design_as_it_was(rec):
    """Review of the snapshot rule, round 1: a session restated the user's optim.lr with the same value and source, and
    its line named both parts as stated, so the user's own design waited. A session's line names only what it changed
    (group F read the same); the user's own line still names what it states, as an earlier rb reads it."""
    user_design(rec)
    def last_set() -> dict:
        return [x for x in map(json.loads, (rec.root / ".rb" / "log.jsonl").read_text(encoding="utf-8").splitlines()) if x["op"] == "spec_set"][-1]
    rec.ok("spec", "set", "int8", "optim.lr", "--source", "configs/train.yaml#optim.lr", agent=True)
    assert last_set()["actor"].startswith("agent:") and last_set()["detail"]["changed_parts"] == {"int8": {}}, last_set()
    assert rec.design("int8")["standing"] == "yours" and "design not confirmed" not in rec.text("show", "int8")
    rec.ok("spec", "set", "int8", "optim.lr", "--source", "configs/train.yaml#optim.lr")
    assert sorted(last_set()["detail"]["changed_parts"]["int8"]) == ["setting:optim.lr", "source:optim.lr"], last_set()


def test_the_users_gate_write_after_a_confirmation_is_no_confirmation(rec):
    """Review of the snapshot rule, round 1: after the user confirmed int8's design, their own gate write kept the design
    it made, and `design.confirmed` named that write as the latest confirmation. It names when, and by whom, the design
    was confirmed."""
    session_design(rec)
    confirm(rec)
    lines = [json.loads(x) for x in (rec.root / ".rb" / "log.jsonl").read_text(encoding="utf-8").splitlines()]
    [confirmed] = [x for x in lines if x["op"] == "gate" and "design" in x.get("detail", {})]
    rec.ok("experiment", "gate", "int8", "--if-not", "drop INT8 for good")
    d = rec.design("int8")
    assert d["standing"] == "confirmed" and d["confirmed"] == {"at": confirmed["at"], "by": "human:ana"}, d["confirmed"]


def test_a_design_the_user_owns_already_is_kept_once(rec):
    """Review of the snapshot rule, round 1: after the user confirmed int8's design, each of their writes kept the
    design it made again, though a line already kept it (0.002, 0.001, 0.002, 0.001: four designs of a thousand bytes
    each, where two are new). Owning is a set test, so a design a line keeps is kept once."""
    session_design(rec)
    confirm(rec)
    for lr in ("0.002", "0.001", "0.002", "0.001"):
        rec.ok("spec", "set", "int8", "optim.lr", lr)
        assert rec.design("int8")["standing"] == "confirmed"
    lines = [json.loads(x) for x in (rec.root / ".rb" / "log.jsonl").read_text(encoding="utf-8").splitlines()]
    kept = [x["detail"]["owned"]["int8"]["key"] for x in lines if "owned" in (x.get("detail") or {})]
    assert len(kept) == len(set(kept)) == 3, kept
    rec.ok("spec", "set", "int8", "optim.lr", "0.004", agent=True)
    assert changes(rec) == [("setting optim.lr", "changed", "0.001", "0.004")], changes(rec)


def as_an_earlier_build_confirmed_it(rec: Rec, seen: bool) -> None:
    """The user's last confirmation as group E's and F's builds wrote it: the design as rb says it, under the hash they
    name it by, with the lines it saw (F's, by their short hash) or none (E's first), and no design in rb's structured
    form."""
    log = rec.root / ".rb" / "log.jsonl"
    lines = [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines()]
    n = max(i for i, x in enumerate(lines) if x["op"] == "gate" and "design" in x.get("detail", {}))
    said = lambda x: json.dumps([x.get(k) if isinstance(x.get(k), str) else "" for k in ("at", "op", "kind", "id", "sha256")], separators=(",", ":"), ensure_ascii=False)
    lines[n]["detail"].pop("owned")
    if seen:
        lines[n]["detail"]["seen"] = [hashlib.sha256(said(x).encode("utf-8")).hexdigest()[:12] for x in lines[:n] if x["op"] != "session"]
    log.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in lines), encoding="utf-8", newline="")


@pytest.mark.parametrize("seen", [True], ids=["as-group-f-wrote-it"])
def test_a_sessions_change_rb_says_alike_reopens_a_design_an_earlier_build_confirmed(rec, seen):
    """Review of the snapshot rule, round 1: a confirmation group E's or F's build wrote keeps the design as rb says it.
    A session's change that rb says alike (a source's term, a value's type) left the design reading as confirmed, though
    it changed what rb checks. Such a confirmation owns the design only while it saw every session's line that wrote a
    part of it; past that it is what the change is said against: was as it was listed, now with what rb's words leave
    out (round 2: it was said bare, with neither). One that names no line it saw (group E's first build) asks again at
    once (round 3), as the test after `stamped` says."""
    rec.write("docs/train.md", "# Training\n\nWe train with a learning rate of 0.001 and a weight decay of 0.0001 for every run.\n")
    session_design(rec)
    rec.ok("spec", "set", "int8", "optim.lr", "0.001", "--source", "docs/train.md:3", "--term", "learning rate", agent=True)
    confirm(rec)
    as_an_earlier_build_confirmed_it(rec, seen)
    assert rec.design("int8")["standing"] == "confirmed"
    rec.ok("spec", "set", "int8", "optim.lr", "0.001", "--source", "docs/train.md:3", "--term", "weight decay", agent=True)
    d = rec.design("int8")
    source = ("setting optim.lr's source", "changed", "docs/train.md:3", 'docs/train.md:3 (term "weight decay")')
    assert (d["standing"], [(c["part"], c["change"], c["was"], c["now"]) for c in d["changes"]]) == ("changed", [source]), d
    shown = rec.text("show", "int8")
    assert f"{LISTING}\n" in shown and '  changed since you confirmed it: setting optim.lr\'s source, was docs/train.md:3, now docs/train.md:3 (term "weight decay")\n' in shown, shown
    assert rec.text("confirm") != "Nothing waits for a confirmation.\n"
    rec.ok(*d["confirm"].split()[1:])
    assert rec.design("int8")["standing"] == "confirmed"
    # a value's type: its source, named with it, did not change
    as_an_earlier_build_confirmed_it(rec, seen)
    edit(rec, "experiments/int8/experiment.yaml", "value: 0.001", "value: '0.001'")
    rec.ok("doctor", "--adopt", "--why", "tidy", agent=True)
    assert changes(rec) == [("setting optim.lr", "changed", "0.001", "0.001 (text)")], changes(rec)


def checkout_of(rec: Rec, root: Path) -> Rec:
    other = Rec.__new__(Rec)
    other.root, other.env = root, rec.env
    return other


def waits_after_the_users_adoption(rec: Rec, change: tuple) -> None:
    """rb doctor lists an edit outside rb; the user adopts it in their own terminal; the design still waits, said as
    the change that came in, and nothing reads as theirs that they never owned."""
    assert "edited_outside_rb" in rec.text("doctor")
    rec.ok("doctor", "--adopt", "--why", "tidy by hand")
    [line] = [json.loads(x) for x in (rec.root / ".rb" / "log.jsonl").read_text(encoding="utf-8").splitlines()[-1:]]
    assert line["op"] == "adopt" and "owned" not in line["detail"], line
    d = rec.design("int8")
    assert d["standing"] == "changed" and change in [(c["part"], c["change"], c["was"], c["now"]) for c in d["changes"]], d
    assert LISTING in rec.text("show", "int8") and rec.text("confirm") != "Nothing waits for a confirmation.\n"


def test_the_users_adoption_after_a_worktree_merge_keeps_no_design_rb_cannot_read_before_it(rec, tmp_path):
    """Review of the snapshot rule, round 1: in a worktree a session added the variant int4 to the design the user
    confirmed and edited its file by hand; after a clean merge the user adopted the edit. rb had no copy of the form it
    wrote there, read that as no file, and kept the session's variant as the user's design. A form rb cannot find
    leaves the design before unknown: the adoption keeps nothing."""
    session_design(rec)
    confirm(rec)
    rec.commit()
    git(rec, "worktree", "add", "-q", str(tmp_path / "wt"), "-b", "agent")
    wt = checkout_of(rec, tmp_path / "wt")
    wt.ok("variant", "add", "int8", "int4", "--role", "ablation", agent=True)
    arm = wt.root / ".rb" / "experiments" / "int8" / "variants" / "int4.yaml"
    arm.write_text(arm.read_text(encoding="utf-8") + "note: the four-bit arm\n", encoding="utf-8", newline="")
    wt.commit()
    git(rec, "merge", "-q", "--no-edit", "agent")
    waits_after_the_users_adoption(rec, ("variant int4", "added", None, "ablation"))


@pytest.mark.parametrize("where", ["a-clone", "rbs-copies-gone"])
def test_the_users_adoption_in_a_clone_keeps_no_design_rb_cannot_read_before_it(rec, tmp_path, where):
    """Review of the snapshot rule, round 1: a session added the variant int4 after the confirmation and edited its file
    in the same commit; in a clone, or in place once .rb/cache was gone, with no copy of what rb wrote, the user's
    adoption kept the session's variant as theirs. The design waits."""
    session_design(rec)
    confirm(rec)
    rec.ok("variant", "add", "int8", "int4", "--role", "ablation", agent=True)
    edit(rec, "experiments/int8/variants/int4.yaml", "role: ablation", "role: ablation\nnote: the four-bit arm")
    rec.commit()
    if where == "rbs-copies-gone":
        shutil.rmtree(rec.root / ".rb" / "cache")
    waits_after_the_users_adoption(clone_of(rec, tmp_path) if where == "a-clone" else rec, ("variant int4", "added", None, "ablation"))


def test_the_users_adoption_after_a_clean_merge_keeps_no_design_git_merged(rec):
    """Review of the snapshot rule, round 1: a session set the gate on a branch, the user set optim.lr on main, and git
    merged the two cleanly; the user then fixed the title by hand and adopted it. rb read the design before as the
    user's write, the latest in time, and kept the session's gate as theirs. A file git merged leaves the design before
    unknown: the adoption keeps nothing, and the gate waits with its design."""
    session_design(rec)
    confirm(rec)
    rec.commit()
    git(rec, "checkout", "-q", "-b", "side")
    rec.ok("experiment", "gate", "int8", "--if-holds", "ship INT8 to every customer today", agent=True)
    rec.commit()
    git(rec, "checkout", "-q", "-")
    rec.ok("spec", "set", "int8", "optim.lr", "0.003")
    rec.commit()
    merged(rec, "main")
    edit(rec, "experiments/int8/experiment.yaml", "title: INT8 vs FP32", "title: INT8 against FP32")
    gate = ("the gate", "changed", "if it holds: ship INT8 · if not: keep FP32", "if it holds: ship INT8 to every customer today · if not: keep FP32")
    waits_after_the_users_adoption(rec, gate)


def test_the_users_adoption_keeps_no_design_a_clock_put_before_a_sessions(rec, tmp_path):
    """Review of the snapshot rule, round 1: the user's machine runs an hour ahead. There they set optim.lr; a session
    on another machine pulled it, edited the hypothesis and adopted that; back on the user's machine rb took the user's
    write as the last, by its time, and their adoption kept the session's hypothesis as theirs. The file holds a form
    rb wrote that is not the one the times put last: the adoption keeps nothing."""
    session_design(rec)
    confirm(rec)
    rec.commit()
    other = clone_of(rec, tmp_path)
    ahead = {**rec.env, "RB_TEST_NOW": "2099-01-01T00:00:00+00:00"}
    r = subprocess.run([*command(), "spec", "set", "int8", "optim.lr", "0.003"], cwd=rec.root, env=ahead, capture_output=True, stdin=subprocess.DEVNULL, encoding="utf-8")
    assert r.returncode == 0, r.stdout + r.stderr
    rec.commit()
    git(other, "pull", "-q", "--no-rebase", "origin", "HEAD")
    edit(other, "experiments/int8/experiment.yaml", "within 0.5 points", "within 3 points")
    other.ok("doctor", "--adopt", "--why", "the plan says 3", agent=True)
    other.commit()
    git(rec, "pull", "-q", "--no-rebase", str(other.root), "HEAD")
    assert "edited_outside_rb" not in rec.text("doctor")
    d = rec.design("int8")
    change = ("hypothesis", "changed", HYPOTHESIS, HYPOTHESIS.replace("within 0.5 points", "within 3 points"))
    assert d["standing"] == "changed" and change in [(c["part"], c["change"], c["was"], c["now"]) for c in d["changes"]], d
    assert LISTING in rec.text("show", "int8") and rec.text("confirm") != "Nothing waits for a confirmation.\n"


def test_the_users_own_edit_committed_and_adopted_keeps_a_confirmed_design_theirs(rec):
    """The user's own edit outside rb, committed on top of what rb wrote and then adopted, is their write: the design
    stays confirmed. Where rb is sure of the design before an adoption, the adoption keeps the one it makes."""
    session_design(rec)
    confirm(rec)
    rec.ok("spec", "set", "int8", "optim.lr", "0.002")
    rec.commit()
    edit(rec, "experiments/int8/experiment.yaml", "why: docs/plan.md:3 names INT8", "why: my own reason")
    rec.commit()
    rec.ok("doctor", "--adopt", "--why", "my reason")
    assert rec.design("int8")["standing"] == "confirmed" and rec.text("confirm") == "Nothing waits for a confirmation.\n"


@pytest.mark.parametrize("confirmed", [True, False], ids=["after-a-confirmation", "before-any"])
def test_a_sessions_gate_branch_reads_as_not_confirmed_beside_the_users_later_branch(rec, confirmed):
    """Review of the snapshot rule, round 1: a session set the gate's if-not branch, then the user set its if-holds
    branch. The gate read in the voice of its latest line, the user's, so the session's branch showed with no `· not
    confirmed` on rb show, rb context and the listing, and no gate waited. The voice is the standing's: a gate a
    session's line wrote that is not as the user owns it waits with its design."""
    session_design(rec)
    if confirmed:
        confirm(rec)
    rec.ok("experiment", "gate", "int8", "--if-not", "drop INT8 for good", agent=True)
    rec.ok("experiment", "gate", "int8", "--if-holds", "ship INT8 next week")
    st = rec.ok("status")["data"]
    [e] = st["experiments"]
    assert [g["experiment"] for g in st["gates_pending"]] == ["int8"] and not e["gate_by"].startswith("human:"), (st["gates_pending"], e["gate_by"])
    assert "if not: drop INT8 for good · not confirmed\n" in rec.text("show", "int8")
    assert "- if not: drop INT8 for good · not confirmed\n" in rec.text("context")
    assert "  gate: if it holds: ship INT8 next week · if not: drop INT8 for good · not confirmed\n" in rec.text("confirm", "int8")


def test_a_clock_never_puts_the_users_voice_on_a_sessions_gate_or_why(rec):
    """Review of the snapshot rule, round 1: the user's gate with their words, and their why, were dated an hour ahead;
    a session's later gate and why, dated before them, read in the user's voice (`your words`, `why:`). The voice is the
    standing's, never the times of the lines."""
    session_design(rec)
    confirm(rec)
    ahead = {**rec.env, "RB_TEST_NOW": "2099-01-01T00:00:00+00:00"}
    for argv in (("experiment", "gate", "int8", "--if-holds", "ship INT8 next week", "--words", "we ship next week if it holds"), ("doctor", "--adopt", "--why", "my reason")):
        if argv[0] == "doctor":
            edit(rec, "experiments/int8/experiment.yaml", "why: docs/plan.md:3 names INT8", "why: my own reason")
        r = subprocess.run([*command(), *argv], cwd=rec.root, env=ahead, capture_output=True, stdin=subprocess.DEVNULL, encoding="utf-8")
        assert r.returncode == 0, (argv, r.stdout, r.stderr)
    assert rec.design("int8")["standing"] == "confirmed"
    rec.ok("experiment", "gate", "int8", "--if-holds", "ship INT8 to every customer today", agent=True)
    edit(rec, "experiments/int8/experiment.yaml", "why: my own reason", "why: the session reason, it is cheaper")
    rec.ok("doctor", "--adopt", "--why", "cheaper", agent=True)
    shown = rec.text("show", "int8")
    assert "  note: the session reason, it is cheaper · not confirmed\n" in shown and "why: the session reason" not in shown, shown
    assert "  if it holds: ship INT8 to every customer today · not confirmed\n" in shown, shown
    assert "your words" not in shown and "note: the session reason, it is cheaper · not confirmed" in rec.text("confirm", "int8")


def test_a_change_rb_says_alike_is_told_apart_on_its_line(rec):
    """Review of the snapshot rule, round 1: a session turned seeds' 3 into the text "3", and gave optim.lr's source
    another term; rb said `setting seeds, was 3, now 3` and `was docs/train.md:3, now docs/train.md:3`. Where rb says
    the two alike, its line says what tells them apart: the value's kind, or the field and its value."""
    rec.write("docs/train.md", "# Training\n\nWe train with a learning rate of 0.001 and a weight decay of 0.0001 for every run.\n")
    session_design(rec)
    rec.ok("spec", "set", "int8", "seeds", "3", agent=True)
    rec.ok("spec", "set", "int8", "optim.lr", "0.001", "--source", "docs/train.md:3", "--term", "learning rate", agent=True)
    confirm(rec)
    rec.ok("spec", "set", "int8", "seeds", '"3"', agent=True)
    rec.ok("spec", "set", "int8", "optim.lr", "0.001", "--source", "docs/train.md:3", "--term", "weight decay", agent=True)
    assert changes(rec) == [
        ("setting optim.lr's source", "changed", 'docs/train.md:3 (term "learning rate")', 'docs/train.md:3 (term "weight decay")'),
        ("setting seeds", "changed", "3 (a number)", "3 (text)")], changes(rec)
    shown = rec.text("show", "int8")
    for line in ("setting seeds, was 3 (a number), now 3 (text)", "setting optim.lr's source, was docs/train.md:3 (term \"learning rate\"), now docs/train.md:3 (term \"weight decay\")"):
        assert f"  changed since you confirmed it: {line}\n" in shown, shown


def test_a_key_an_earlier_rb_listed_is_refused_as_the_name_it_gave(rec):
    """Review of the snapshot rule, round 1: group E's and F's builds listed a design by a hash over how rb says it. With
    no change since, the key they listed was refused as a design that changed after it was listed, which is not true.
    Round 2: nor is it sure to name the same design, as a change rb words alike leaves that hash as it was. rb says it
    cannot tell, with the design as it reads now and the command that confirms it."""
    session_design(rec)
    lines = [json.loads(x) for x in (rec.root / ".rb" / "log.jsonl").read_text(encoding="utf-8").splitlines()]
    now = rec.design("int8")["confirm"].split("@")[1]
    log = rec.root / ".rb" / "log.jsonl"
    rec.ok("confirm", f"int8@{now}")
    [confirmation] = [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines() if '"design"' in x]
    earlier = confirmation["detail"]["design"]
    log.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in lines), encoding="utf-8", newline="")
    assert earlier != now and rec.design("int8")["standing"] == "not_confirmed"
    err = json.loads(rec.run("confirm", f"int8@{earlier}", "--json").stdout)["errors"][0]
    assert err["message"] == f"{earlier} is a key an earlier rb listed, and rb cannot tell from it whether int8's design changed since. It reads now:", err
    assert err["fix"] == f"To confirm it as it reads now: rb confirm int8@{now}" and rec.design("int8")["standing"] == "not_confirmed"


def test_the_users_adoption_keeps_no_design_a_line_already_keeps(rec):
    """Review of the snapshot rule, round 1 (with the user's writes keeping a design once): the user's adoption that put
    back a design they confirmed kept it again. A design a line keeps is kept once."""
    session_design(rec)
    confirm(rec)
    edit(rec, "experiments/int8/experiment.yaml", "why: docs/plan.md:3 names INT8", "why: my own reason")
    rec.ok("doctor", "--adopt", "--why", "my reason")
    edit(rec, "experiments/int8/experiment.yaml", "why: my own reason", "why: docs/plan.md:3 names INT8")
    rec.ok("doctor", "--adopt", "--why", "the plan's reason after all")
    lines = [json.loads(x) for x in (rec.root / ".rb" / "log.jsonl").read_text(encoding="utf-8").splitlines()]
    kept = [x["op"] for x in lines if "owned" in (x.get("detail") or {})]
    assert kept == ["gate", "adopt"] and rec.design("int8")["standing"] == "confirmed", kept


@pytest.mark.parametrize("bring", ["cherry-pick", "cherry-pick-no-commit", "squash", "stash"])
def test_the_users_adoption_after_git_brings_in_a_sessions_line_keeps_no_design(rec, bring):
    """Review of the snapshot rule, round 1 (with the user's adoption certain of what came before): a session set
    acc-tol's min_n to 3, on a branch or in changes set aside with git stash; the user tightened its target on main; a
    cherry-pick, a squash merge or git stash pop brought the session's change and its line in, committed or not, and the
    user adopted the file as it stands. rb read the design before as the user's last write. A session's line that came
    in beside it, whose value the file now holds, leaves the edit unknown: the adoption keeps nothing, and it waits."""
    session_design(rec)
    confirm(rec)
    rec.commit()
    if bring != "stash":
        git(rec, "checkout", "-q", "-b", "side")
    edit(rec, ACC_TOL, "min_n: 1", "min_n: 3")
    rec.ok("doctor", "--adopt", "--why", "three seeds at least", agent=True)
    if bring != "stash":
        rec.commit()
        git(rec, "checkout", "-q", "-")
    else:
        git(rec, "stash", "-q")
    edit(rec, ACC_TOL, "target: -0.005", "target: -0.003")
    rec.ok("doctor", "--adopt", "--why", "a tighter budget")
    rec.commit()
    if bring == "cherry-pick":
        git(rec, "cherry-pick", "side")
    elif bring == "cherry-pick-no-commit":
        git(rec, "cherry-pick", "-n", "side")
    elif bring == "squash":
        git(rec, "merge", "-q", "--squash", "side")
        rec.commit()
    else:
        git(rec, "stash", "pop", "-q")
    rule = ("criterion acc-tol's rule", "changed", "change.top1 ≥ -0.003", "change.top1 ≥ -0.003 (n ≥ 3)")
    waits_after_the_users_adoption(rec, rule)


def test_the_users_own_edit_beside_their_own_uncommitted_writes_keeps_a_confirmed_design_theirs(rec):
    """Every write since the last commit made here, the session's among them, then the user's own edit outside rb and
    its adoption: no line came in beside rb's last write, so the adoption keeps the design it makes."""
    session_design(rec)
    rec.ok("spec", "set", "int8", "optim.lr", "0.002", agent=True)
    confirm(rec)
    rec.ok("spec", "set", "int8", "optim.lr", "0.003")
    edit(rec, "experiments/int8/experiment.yaml", "why: docs/plan.md:3 names INT8", "why: my own reason")
    rec.ok("doctor", "--adopt", "--why", "my reason")
    assert rec.design("int8")["standing"] == "confirmed" and rec.text("confirm") == "Nothing waits for a confirmation.\n"


# Review of the snapshot rule, round 2.


def last_line_as_an_earlier_build_wrote_it(rec: Rec, changed) -> dict:
    """The log's last line as group E's or F's build wrote it (`changed`, its changed_parts, compared as rb words the
    parts), or as an older one did (None: no changed_parts): no word that it compared them in rb's structured form."""
    log = rec.root / ".rb" / "log.jsonl"
    lines = [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines()]
    detail = lines[-1]["detail"]
    detail.pop("parts_compared", None)
    detail.pop("changed_parts", None)
    if changed is not None:
        detail["changed_parts"] = changed
    log.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in lines), encoding="utf-8", newline="")
    return lines[-1]


@pytest.mark.parametrize("change", [("term: learning rate", "term: rate"), ("value: 3", "value: '3'")], ids=["a-term", "a-type"])
@pytest.mark.parametrize("start", ["the-users-own", "confirmed-by-an-earlier-build"])
def test_a_sessions_change_an_earlier_build_adopted_as_changing_no_part_waits(rec, start, change):
    """Review of the snapshot rule, round 2: group E's and F's adoptions compared parts as rb words them, so a session's
    edit of a source's term or a value's type came out as changed_parts {"int8": {}}, read here as no part written:
    the user's own design read as theirs, and one an earlier build confirmed read as confirmed. Only a line that says
    it compared the parts in rb's structured form says what it changed; an earlier one wrote a part where it wrote one
    of the design's files."""
    rec.write("docs/train.md", "# Training\n\nWe train with a learning rate of 0.001 and a weight decay of 0.0001 for every run.\n")
    if start == "the-users-own":
        users_design_with_runs(rec)
        rec.ok("spec", "set", "int8", "optim.lr", "0.001", "--source", "docs/train.md:3", "--term", "learning rate")
        rec.ok("spec", "set", "int8", "seeds", "3")
        assert rec.design("int8")["standing"] == "yours"
    else:
        session_design(rec)
        rec.ok("spec", "set", "int8", "optim.lr", "0.001", "--source", "docs/train.md:3", "--term", "learning rate", agent=True)
        rec.ok("spec", "set", "int8", "seeds", "3", agent=True)
        confirm(rec)
        as_an_earlier_build_confirmed_it(rec, True)
        assert rec.design("int8")["standing"] == "confirmed"
    edit(rec, "experiments/int8/experiment.yaml", *change)
    rec.ok("doctor", "--adopt", "--why", "tidy", agent=True)
    line = last_line_as_an_earlier_build_wrote_it(rec, {"int8": {}})
    assert line["op"] == "adopt" and line["actor"].startswith("agent:"), line
    d = rec.design("int8")
    assert d["standing"] == ("not_confirmed" if start == "the-users-own" else "changed"), d
    assert LISTING in rec.text("show", "int8") and rec.text("confirm") != "Nothing waits for a confirmation.\n"
    readings = rec.readings()
    rec.ok(*d["confirm"].split()[1:])
    assert rec.design("int8")["standing"] == "confirmed" and rec.readings() == readings


@pytest.mark.parametrize("older", ["no-changed-parts", "names-the-new-experiment-only"])
def test_a_criterion_an_earlier_build_moved_out_of_the_users_design_makes_both_wait(rec, older):
    """Review of the snapshot rule, round 2: a session moved criterion c9 from the user's int8 to bsz by hand and adopted
    it with an older rb. Group D's line named no changed parts and group E's and F's only bsz, and a criterion's line
    was read as of the experiment it is of now, so int8 still read as the user's, though a session took its criterion.
    Such a line is read as of every experiment a line says the criterion was of."""
    user_design(rec)
    rec.ok("criterion", "add", "INT8 keeps 0.75 top-1", "-e", "int8", "--metric", "int8.top1", "--at-least", "0.75", "--id", "c9")
    rec.ok("experiment", "add", "Batch size probe", "--id", "bsz", "--purpose", "the largest batch that fits", "--baseline", "b256", "--candidate", "b512")
    old = rec.root / ".rb" / "experiments" / "int8" / "criteria" / "c9.yaml"
    rec.write(".rb/experiments/bsz/criteria/c9.yaml", old.read_text(encoding="utf-8").replace("experiment: int8", "experiment: bsz"))
    old.unlink()
    rec.ok("doctor", "--adopt", "--why", "it belongs to bsz", agent=True)
    line = last_line_as_an_earlier_build_wrote_it(rec, None if older == "no-changed-parts" else {"bsz": {}})
    assert (line["kind"], line["id"], line["path"]) == ("criterion", "c9", ".rb/experiments/bsz/criteria/c9.yaml"), line
    assert [rec.design(e)["standing"] for e in ("int8", "bsz")] == ["not_confirmed", "not_confirmed"]
    assert LISTING in rec.text("show", "int8")


@pytest.mark.parametrize("change", [("term: learning rate", "term: rate"), ("value: 3", "value: '3'")], ids=["a-term", "a-type"])
def test_a_design_an_earlier_build_confirmed_waits_while_a_file_of_it_is_edited_outside_rb(rec, change):
    """Review of the snapshot rule, round 2: a confirmation group E's or F's build wrote keeps the design as rb words it.
    An edit outside rb, not adopted, that rb words the same (a source's term, a value's type) left the design reading
    confirmed, though rb doctor listed the file and a reading could move. Such a confirmation owns the design only
    while rb wrote every file of it as it stands; once rb doctor puts back what rb wrote, it reads confirmed again."""
    rec.write("docs/train.md", "# Training\n\nWe train with a learning rate of 0.001 and a weight decay of 0.0001 for every run.\n")
    session_design(rec)
    rec.ok("spec", "set", "int8", "optim.lr", "0.001", "--source", "docs/train.md:3", "--term", "learning rate", agent=True)
    rec.ok("spec", "set", "int8", "seeds", "3", agent=True)
    confirm(rec)
    as_an_earlier_build_confirmed_it(rec, True)
    assert rec.design("int8")["standing"] == "confirmed"
    edit(rec, "experiments/int8/experiment.yaml", *change)
    assert "edited_outside_rb" in rec.text("doctor")
    assert rec.design("int8")["standing"] == "changed" and LISTING in rec.text("show", "int8")
    r = rec.run(*rec.design("int8")["confirm"].split()[1:], "--json")
    assert r.returncode != 0 and json.loads(r.stdout)["errors"][0]["code"] == "E_STATE_EDITED", r.stdout
    rec.ok("doctor", "--restore")
    assert rec.design("int8")["standing"] == "confirmed" and rec.text("confirm") == "Nothing waits for a confirmation.\n"


def test_rb_confirm_lists_every_field_the_key_names(rec):
    """Review of the snapshot rule, round 2: the key names a source's term and a value's kind, which rb confirm's listing
    did not show: a session's change of only the term, or only the kind, between the listing and the confirmation was
    refused with the very text the user had listed, and the user confirmed a term they never saw. The listing, and the
    refusal's `It reads now`, say each: every source field in full, and text quoted and escaped in values and rules.
    A source reads in rb's short form with what it leaves out after it, as group E listed it (round 3)."""
    rec.write("docs/train.md", "# Training\n\nWe train with a learning rate of 0.001 and a weight decay of 0.0001 for every run.\n")
    session_design(rec)
    rec.ok("spec", "set", "int8", "optim.wd", "0.0001", "--source", "docs/train.md:3", "--term", "weight decay", agent=True)
    rec.ok("spec", "set", "int8", "seeds", '"3"', agent=True)
    rec.ok("spec", "set", "int8", "seed", "--per-run", agent=True)
    rec.ok("criterion", "add", "INT8 keeps 0.75 top-1 on seed 1", "-e", "int8", "--metric", "int8.top1", "--at-least", "0.75", "--where", 'seed="1"', "--id", "c-s1", agent=True)
    listed = rec.text("confirm", "int8")
    for line in ('setting optim.wd: 0.0001 from docs/train.md:3 (term "weight decay")', 'setting seeds: \"3\"'):
        assert f"    {line}\n" in listed, listed
    assert [x for x in listed.splitlines() if x.startswith("    criterion c-s1 (") and ': int8.top1 [where seed="1"]' in x], listed
    first = rec.design("int8")["confirm"].split("@")[1]
    rec.ok("spec", "set", "int8", "optim.wd", "0.0001", "--source", "docs/train.md:3", "--term", "decay", agent=True)
    err = json.loads(rec.run("confirm", f"int8@{first}", "--json").stdout)["errors"][0]
    assert err["message"].startswith(f"int8's design does not match {first}: it changed after it was listed"), err
    assert 'setting optim.wd: 0.0001 from docs/train.md:3 (term "decay")' in err["problems"], err
    # a key an earlier rb listed names the design as rb words it, which a change of the term leaves as it was
    lines = [json.loads(x) for x in (rec.root / ".rb" / "log.jsonl").read_text(encoding="utf-8").splitlines()]
    now = rec.design("int8")["confirm"].split("@")[1]
    rec.ok("confirm", f"int8@{now}")
    earlier = [json.loads(x) for x in (rec.root / ".rb" / "log.jsonl").read_text(encoding="utf-8").splitlines()][-1]["detail"]["design"]
    (rec.root / ".rb" / "log.jsonl").write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in lines), encoding="utf-8", newline="")
    rec.ok("spec", "set", "int8", "optim.wd", "0.0001", "--source", "docs/train.md:3", "--term", "weight decay", agent=True)
    err = json.loads(rec.run("confirm", f"int8@{earlier}", "--json").stdout)["errors"][0]
    assert err["message"] == f"{earlier} is a key an earlier rb listed, and rb cannot tell from it whether int8's design changed since. It reads now:", err
    assert 'setting optim.wd: 0.0001 from docs/train.md:3 (term "weight decay")' in err["problems"] and rec.design("int8")["standing"] == "not_confirmed", err


def test_a_sessions_delta_says_the_gate_the_user_confirmed(rec):
    """Review of the snapshot rule, round 2: once the user confirmed int8's design with its gate, a session changed the
    gate, and was told the design was confirmed with the gate as it read after its own change. A confirmation is told
    with the gate its line keeps."""
    session_design(rec)
    confirm(rec)
    told = [t["text"] for t in rec.ok("experiment", "gate", "int8", "--if-holds", "ship INT8 now", agent=True)["rb_delta"]]
    assert "int8's design confirmed, with its gate: if it holds: ship INT8 · if not: keep FP32" in told, told
    assert not [t for t in told if "ship INT8 now" in t and "confirmed" in t], told


def test_a_gate_on_two_lines_says_not_confirmed_on_the_branch_a_session_wrote(rec):
    """Review of the snapshot rule, round 2: after the user confirmed int8's design, a session set the gate's if-holds
    branch on a branch of the repository and the user its if-not branch on main; after the merge, the views that say
    the gate on two lines put `· not confirmed` on the user's branch. It goes on the branch a session wrote."""
    session_design(rec)
    confirm(rec)
    rec.commit()
    git(rec, "checkout", "-q", "-b", "side")
    rec.ok("experiment", "gate", "int8", "--if-holds", "ship INT8 next week", agent=True)
    rec.commit()
    git(rec, "checkout", "-q", "-")
    rec.ok("experiment", "gate", "int8", "--if-not", "drop INT8 for good")
    rec.commit()
    merged = subprocess.run(["git", "merge", "-q", "--no-edit", "side"], cwd=rec.root, env=rec.env, capture_output=True, encoding="utf-8")
    if merged.returncode != 0:
        exp = rec.root / ".rb" / "experiments" / "int8" / "experiment.yaml"
        text = re.sub(r"<<<<<<< [^\n]*\n(.*?)=======\n(.*?)>>>>>>> [^\n]*\n", lambda m: m.group(2) if "next week" in m.group(2) else m.group(1), exp.read_text(encoding="utf-8"), flags=re.S)
        exp.write_text(text.replace("if_not: keep FP32", "if_not: drop INT8 for good"), encoding="utf-8", newline="")
        rec.commit()
        rec.ok("doctor", "--adopt", "--why", "both changes")
    shown = rec.text("show", "int8")
    assert "  if it holds: ship INT8 next week · not confirmed\n" in shown and "  if not: drop INT8 for good\n" in shown, shown
    assert "- if it holds: ship INT8 next week · not confirmed\n" in rec.text("context")


def test_the_users_write_keeps_only_what_it_changed_of_a_design_they_own(rec, tmp_path):
    """Review of the snapshot rule, round 2: each of the user's writes to a design they owned kept the whole design on
    its line, 4 KB a line on a design of a hundred settings, and every read parses the log: reads ran up to 1.7 times
    group F's. A write keeps the key of the design it made and of the one it made it from, with only the parts that
    differ; every sixteenth keeps the design whole again. A line whose earlier ones are not in the log keeps none."""
    session_design(rec)
    rec.write("configs/wide.yaml", "".join(f"k{n:03d}: {n}\n" for n in range(100)))
    rec.ok("spec", "set", "int8", "--from", "configs/wide.yaml", "--keys", "k*", agent=True)
    rec.ok("spec", "set", "int8", "--from", "configs/train.yaml", "--keys", "optim.*,data.batch_size", agent=True)
    confirm(rec)
    log = rec.root / ".rb" / "log.jsonl"
    for n in range(20):
        rec.ok("spec", "set", "int8", "data.batch_size", str(300 + n))
        assert rec.design("int8")["standing"] == "confirmed", n
    lines = [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines()]
    [confirmation] = [x for x in lines if x["op"] == "gate" and "design" in x["detail"]]
    kept = [x for x in lines if x["op"] == "spec_set" and "owned" in x["detail"]]
    entries = [x["detail"]["owned"]["int8"] for x in kept]
    assert ["from" in e for e in entries] == [True] * 15 + [False] + [True] * 4, entries
    assert all(sorted(e["parts"]) == ["setting:data.batch_size"] for e in entries if "from" in e), entries
    assert entries[0]["from"] == confirmation["detail"]["owned"]["int8"]["key"] and entries[1]["from"] == entries[0]["key"]
    assert max(len(json.dumps(x)) for x in kept if "from" in x["detail"]["owned"]["int8"]) * 3 < len(json.dumps(confirmation))
    rec.commit()
    assert clone_of(rec, tmp_path).design("int8")["standing"] == "confirmed"
    rec.write("configs/train.yaml", "optim:\n  lr: 0.001\n  wd: 0.5\ndata:\n  batch_size: 256\n")
    rec.ok("spec", "set", "int8", "optim.wd", "--source", "configs/train.yaml#optim.wd", agent=True)
    assert changes(rec) == [("setting optim.wd", "changed", "0.0001", "0.5")], changes(rec)
    rec.write("configs/train.yaml", "optim:\n  lr: 0.001\n  wd: 0.0001\ndata:\n  batch_size: 256\n")
    rec.ok("spec", "set", "int8", "optim.wd", "--source", "configs/train.yaml#optim.wd", agent=True)
    assert rec.design("int8")["standing"] == "confirmed"
    # Keep the current file's write, but only the latest owned snapshot and none of its parents.
    current = [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines()]
    gone = [x for x in current if "owned" not in x.get("detail", {})] + kept[-1:]
    log.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in gone), encoding="utf-8", newline="")
    assert rec.design("int8")["standing"] == "not_confirmed"


def test_delta_owned_snapshots_survive_deletions_duplicates_and_log_order(rec, tmp_path):
    session_design(rec)
    rec.ok("spec", "set", "int8", "optim.wd", "0.0001", agent=True)
    rec.ok("variant", "add", "int8", "int4", "--role", "ablation", agent=True)
    confirm(rec)
    log = rec.root / ".rb" / "log.jsonl"
    removed = []
    for target in ("int8/optim.wd", "int8/int4", "acc-tol"):
        rec.ok("retract", target, "--why", "not needed")
        assert rec.design("int8")["standing"] == "confirmed"
        rows = [json.loads(x) for x in log.read_text().splitlines()]
        entry = rows[-1]["detail"]["owned"]["int8"]
        assert "from" in entry and entry["removed"], entry
        removed.extend(entry["removed"])
    assert {"setting:optim.wd", "variant:int4", "rule:acc-tol", "role:acc-tol"} <= set(removed)
    rows = [json.loads(x) for x in log.read_text().splitlines()]
    kept_rows = [r for r in rows if r.get("detail", {}).get("owned", {}).get("int8")]
    plain_rows = [r for r in rows if r not in kept_rows]
    # Ownership membership must not depend on log order. Include the same whole
    # and delta snapshots twice, then put the dependent snapshots before bases.
    log.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in plain_rows + kept_rows[::-1] + kept_rows[::-1]))
    assert rec.design("int8")["standing"] == "confirmed"
    rec.commit()
    other = clone_of(rec, tmp_path)
    assert other.design("int8")["standing"] == "confirmed"
    cache = rec.root / ".rb" / "cache"
    if cache.exists():
        shutil.rmtree(cache)
    assert rec.design("int8")["standing"] == "confirmed"
    rec.write("configs/train.yaml", "optim:\n  lr: 0.002\n  wd: 0.0001\ndata:\n  batch_size: 256\n")
    rec.ok("spec", "set", "int8", "optim.lr", "--source", "configs/train.yaml#optim.lr", agent=True)
    assert changes(rec) == [("setting optim.lr", "changed", "0.001", "0.002")], changes(rec)


def test_two_owned_delta_branches_do_not_own_their_combined_design(rec):
    session_design(rec)
    rec.ok("spec", "set", "int8", "fp32.threads", "1", agent=True)
    rec.ok("spec", "set", "int8", "int8.threads", "1", agent=True)
    confirm(rec)
    rec.commit()
    git(rec, "checkout", "-q", "-b", "left")
    rec.ok("spec", "set", "int8", "fp32.threads", "2")
    assert rec.design("int8")["standing"] == "confirmed"
    rec.commit()
    assert git(rec, "checkout", "-q", "-").returncode == 0
    rec.ok("spec", "set", "int8", "int8.threads", "3")
    assert rec.design("int8")["standing"] == "confirmed"
    rec.commit()
    merged = git(rec, "merge", "-q", "--no-edit", "left")
    assert merged.returncode == 0, (merged.stdout, merged.stderr)
    d = rec.design("int8")
    assert d["standing"] == "changed" and len(d["changes"]) == 1, d
    assert d["changes"][0]["part"] in ("setting fp32.threads", "setting int8.threads")
    confirm(rec)
    rec.ok("spec", "set", "int8", "fp32.threads", "4")
    assert rec.design("int8")["standing"] == "confirmed"


def test_two_full_designs_with_the_same_short_key_keep_the_users_write(rec):
    """A short listing key is not the identity of an owned design. Find a birthday
    collision locally, then make just two real writes, without modifying the log.
    This probes owned_after's short-prefix shortcut independently of compression.
    The search is bounded to 200000 small SHA256 operations and uses no CLI loop.
    """
    session_design(rec)
    rec.ok("spec", "set", "int8", "collision", "0", agent=True)
    confirm(rec)
    rows = [json.loads(x) for x in (rec.root / ".rb" / "log.jsonl").read_text().splitlines()]
    entry = rows[-1]["detail"]["owned"]["int8"]
    parts = entry["parts"]
    def full_hash():
        return hashlib.sha256(json.dumps(parts, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    assert full_hash().startswith(entry["key"]), (full_hash(), entry["key"])
    seen = {}
    for n in range(1, 200001):
        parts["setting:collision"] = {"value": n}
        full = full_hash()
        short = full[:7]
        if short in seen:
            first, second = seen[short], n
            break
        seen[short] = n
    else:
        raise AssertionError("No short-key collision found in 200000 candidates")
    rec.ok("spec", "set", "int8", "collision", str(first))
    assert rec.design("int8")["standing"] == "confirmed"
    before = rec.design("int8")
    rec.ok("spec", "set", "int8", "collision", str(second))
    after = rec.design("int8")
    assert before["key"] == after["key"], (first, second, before, after)
    assert after["standing"] == "confirmed", (first, second, after)


@pytest.mark.parametrize("first,second", [('3', '3 (text)'), ('1', '"1"'), ('unknown', 'unknown (text)')])
def test_confirmation_lists_distinct_text_values_distinctly(rec, first, second):
    session_design(rec)
    shown = []
    for value in (first, second):
        rec.ok("spec", "set", "int8", "label", json.dumps(value), agent=True)
        shown.append(next(line for line in rec.text("confirm", "int8").splitlines() if line.startswith("    setting label:")))
    assert shown[0] != shown[1], shown


def test_confirmation_lists_distinct_selector_text_distinctly(rec):
    session_design(rec)
    rec.ok("spec", "set", "int8", "seed", "--per-run", agent=True)
    rec.ok("criterion", "add", "INT8 reaches 0.75 top-1", "-e", "int8", "--metric", "int8.top1", "--at-least", "0.75", "--where", "seed=1", "--id", "selector", agent=True)
    shown = []
    for old, new in (("seed: 1", "seed: '1'"), ("seed: '1'", "seed: '\"1\"'")):
        edit(rec, "experiments/int8/criteria/selector.yaml", old, new)
        shown.append(next(line for line in rec.text("confirm", "int8").splitlines() if line.startswith("    criterion selector (")))
    assert shown[0] != shown[1], shown


def test_confirmation_lists_the_full_source_commit(rec):
    session_design(rec)
    shown = []
    for commit in ("abcdef0" + "1" * 33, "abcdef0" + "2" * 33):
        rec.ok("spec", "set", "int8", "optim.lr", "0.001", "--source", "configs/train.yaml#optim.lr", "--commit", commit, "--no-verify", agent=True)
        line = next(line for line in rec.text("confirm", "int8").splitlines() if line.startswith("    setting optim.lr:"))
        assert commit in line, line
        shown.append(line)
    assert shown[0] != shown[1], shown


def test_confirmation_lists_the_full_freeze_time(rec):
    session_design(rec)
    obj = rec.ok("freeze", "int8", "-m", "before the runs", agent=True)["data"]["object"]
    assert obj["frozen"]["at"] in rec.text("confirm", "int8")


def test_a_short_design_key_collision_cannot_confirm_a_changed_listing(rec):
    session_design(rec)
    rec.ok("spec", "set", "int8", "collision", "0", agent=True)
    confirm(rec)
    rows = [json.loads(x) for x in (rec.root / ".rb" / "log.jsonl").read_text().splitlines()]
    entry = rows[-1]["detail"]["owned"]["int8"]
    parts = entry["parts"]
    def full_hash():
        return hashlib.sha256(json.dumps(parts, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    assert full_hash().startswith(entry["key"]), (full_hash(), entry["key"])
    seen = {}
    for n in range(1, 200001):
        parts["setting:collision"] = {"value": n}
        short = full_hash()[:7]
        if short in seen:
            first, second = seen[short], n
            break
        seen[short] = n
    else:
        raise AssertionError("No short-key collision found in 200000 candidates")
    rec.ok("spec", "set", "int8", "collision", str(first), agent=True)
    listed = rec.design("int8")
    assert listed["pending"]
    shown = rec.text("confirm", "int8")
    command_as_listed = listed["confirm"]
    assert command_as_listed in shown, shown
    rec.ok("spec", "set", "int8", "collision", str(second), agent=True)
    current = rec.design("int8")
    assert current["key"] == listed["key"], (first, second, listed, current)
    assert current["parts"] != listed["parts"]
    response = rec.run(*command_as_listed.split()[1:], "--json")
    envelope = json.loads(response.stdout)
    assert response.returncode != 0 and not envelope["ok"], (first, second, envelope)
    assert envelope["errors"][0]["code"] == "E_OBJECT_INVALID", envelope
    assert rec.design("int8")["pending"]


def test_full_parent_identity_can_read_an_older_whole_snapshot(rec):
    session_design(rec)
    confirm(rec)
    log = rec.root / ".rb" / "log.jsonl"
    rows = [json.loads(x) for x in log.read_text().splitlines()]
    kept = rows[-1]["detail"]["owned"]["int8"]
    assert "from" not in kept
    kept.pop("full", None)
    log.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows))
    assert rec.design("int8")["standing"] == "confirmed"
    rec.ok("spec", "set", "int8", "optim.lr", "0.002")
    assert rec.design("int8")["standing"] == "confirmed"
    rows = [json.loads(x) for x in log.read_text().splitlines()]
    changed = rows[-1]["detail"]["owned"]["int8"]
    before_full = hashlib.sha256(json.dumps(kept["parts"], sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    assert changed["from"] == kept["key"] and changed["from_full"] == before_full
    assert len(changed["full"]) == 64 and changed["full"].startswith(changed["key"])


def test_confirmation_distinguishes_a_gate_branch_from_delimiters_in_its_words(rec):
    session_design(rec)
    listings, parts = [], []
    for holds, otherwise in (("ship INT8 · if not: keep FP32", ""), ("ship INT8", "keep FP32")):
        rec.ok("experiment", "gate", "int8", "--if-holds", holds, "--if-not", otherwise, agent=True)
        parts.append(rec.design("int8")["key"])
        listings.append(next(line for line in rec.text("confirm", "int8").splitlines() if line.startswith("    gate:")))
    assert parts[0] != parts[1]
    assert listings[0] != listings[1], listings


def test_confirmation_distinguishes_question_links_from_delimiters_in_interpretations(rec):
    session_design(rec)
    rec.ok("question", "add", "Does INT8 fit the latency budget?", "--id", "q-extra")
    rec.ok("criterion", "file", "acc-tol", "q-acc", "--when-met", 'x" · for q-extra, when met, "y', "--why", "one interpretation", agent=True)
    before = next(line for line in rec.text("confirm", "int8").splitlines() if line.startswith("    criterion acc-tol ("))
    before_parts = rec.design("int8")["parts"]
    rec.ok("criterion", "file", "acc-tol", "q-acc", "--when-met", "x", "--why", "first interpretation", agent=True)
    rec.ok("criterion", "file", "acc-tol", "q-extra", "--when-met", "y", "--why", "second interpretation", agent=True)
    after = next(line for line in rec.text("confirm", "int8").splitlines() if line.startswith("    criterion acc-tol ("))
    assert before_parts != rec.design("int8")["parts"]
    assert before != after, (before, after)


# Review of the snapshot rule, round 3.


def stamped(rec: Rec, when: str, *argv: str, agent: bool = False) -> dict:
    """A write whose line is stamped `when`, as on a machine whose clock says so."""
    rec.env["RB_TEST_NOW"] = when
    try:
        return rec.ok(*argv, agent=agent)
    finally:
        del rec.env["RB_TEST_NOW"]


def a_type_change_adopted_in_a_session(rec: Rec, when: str) -> None:
    """A session's hand edit of optim.lr's type, which rb says alike, adopted with its line stamped `when`."""
    edit(rec, "experiments/int8/experiment.yaml", "value: 0.001", "value: '0.001'")
    stamped(rec, when, "doctor", "--adopt", "--why", "tidy", agent=True)


@pytest.mark.parametrize("where", ["merged-from-a-branch", "a-session-clock-behind", "the-users-clock-behind", "a-session-change-after-it"])
def test_a_confirmation_that_names_no_line_it_saw_asks_again_wherever_its_line_falls(rec, where):
    """Review of the snapshot rule, round 3: a confirmation group E's first build wrote keeps the design as rb says it and
    names no line it saw. It was read as having seen every line before it in the log, which sorts by time: a session's
    change rb says alike, made on a branch before the user confirmed on main and then merged, or made where the clock is
    behind, read as confirmed, and the user's next write kept that change as theirs. With the user's clock behind, the
    design exactly as confirmed read changed, with a line for each part. Such a confirmation rests on no line: rb asks
    again. Recheck of round 3: after the user's next write the design read changed, said against it in rb's words, so
    the lines named the user's own change and left out the session's change rb says alike, on a straight line with one
    clock too; it says no part as changed since it."""
    session_design(rec)
    rec.commit()
    if where == "merged-from-a-branch":
        git(rec, "checkout", "-q", "-b", "side")
        a_type_change_adopted_in_a_session(rec, "2099-01-01T00:05:00+00:00")
        rec.commit()
        git(rec, "checkout", "-q", "-")
        stamped(rec, "2099-01-01T00:10:00+00:00", *rec.design("int8")["confirm"].split()[1:])
        as_an_earlier_build_confirmed_it(rec, False)
        rec.commit()
        git(rec, "merge", "-q", "--no-edit", "side")
    elif where == "a-session-clock-behind":
        stamped(rec, "2099-01-01T00:10:00+00:00", *rec.design("int8")["confirm"].split()[1:])
        as_an_earlier_build_confirmed_it(rec, False)
        a_type_change_adopted_in_a_session(rec, "2099-01-01T00:05:00+00:00")
    elif where == "the-users-clock-behind":
        stamped(rec, "2000-01-01T00:00:00+00:00", *rec.design("int8")["confirm"].split()[1:])
        as_an_earlier_build_confirmed_it(rec, False)
    else:
        stamped(rec, "2099-01-01T00:05:00+00:00", *rec.design("int8")["confirm"].split()[1:])
        as_an_earlier_build_confirmed_it(rec, False)
        a_type_change_adopted_in_a_session(rec, "2099-01-01T00:10:00+00:00")
    d = rec.design("int8")
    assert (d["standing"], d["changes"]) == ("not_confirmed", []), d
    shown = rec.text("show", "int8")
    assert LISTING in shown and "changed since" not in shown and "changed since" not in rec.text("confirm"), shown
    # the user's next write keeps no design, as it was made from one that waits; nothing is said against the
    # confirmation, as any session's line may be after it
    stamped(rec, "2099-01-01T01:00:00+00:00", "spec", "set", "int8", "optim.wd", "0.0002")
    assert "owned" not in json.loads((rec.root / ".rb" / "log.jsonl").read_text(encoding="utf-8").splitlines()[-1]).get("detail", {})
    assert (rec.design("int8")["standing"], changes(rec)) == ("not_confirmed", []), changes(rec)
    stamped(rec, "2099-01-01T02:00:00+00:00", "spec", "set", "int8", "optim.mom", "0.9", agent=True)
    assert (rec.design("int8")["standing"], changes(rec)) == ("not_confirmed", []), changes(rec)
    assert "changed since" not in rec.text("show", "int8") and "changed since" not in rec.text("confirm", "int8")
    readings = rec.readings()
    stamped(rec, "2099-01-01T03:00:00+00:00", *rec.design("int8")["confirm"].split()[1:])
    assert rec.design("int8")["standing"] == "confirmed" and rec.readings() == readings


@pytest.mark.parametrize("how", ["edited-outside-rb", "set-by-an-earlier-build", "adopted-by-an-earlier-build", "by-a-line-that-names-nothing"])
def test_a_change_rb_says_alike_after_an_earlier_builds_confirmation_says_what_it_can(rec, how):
    """Review of the snapshot rule, round 3: after a confirmation group E's or F's build wrote, a change rb says alike that
    no line names in rb's structured form (an edit outside rb not adopted, an earlier rb's write or adoption) read
    `changed` with no line at all: the user was asked again and not told what changed. rb names the part a line names,
    with what tells it apart, and else the file written or edited since."""
    session_design(rec)
    rec.ok("spec", "set", "int8", "seeds", "3", agent=True)
    rec.ok("spec", "set", "int8", "seed", "--per-run", agent=True)
    rec.ok("criterion", "add", "INT8 keeps 0.75 top-1 on seed 1", "-e", "int8", "--metric", "int8.top1", "--at-least", "0.75", "--where", "seed=1",
           "--id", "c-s1", agent=True)
    confirm(rec)
    as_an_earlier_build_confirmed_it(rec, True)
    assert rec.design("int8")["standing"] == "confirmed"
    if how == "edited-outside-rb":
        edit(rec, "experiments/int8/criteria/c-s1.yaml", "seed: 1", "seed: '1'")
        said, expected = ".rb/experiments/int8/criteria/c-s1.yaml was edited outside rb", (".rb/experiments/int8/criteria/c-s1.yaml", "changed", None, None)
    elif how == "set-by-an-earlier-build":
        # the closing pass: the listing an earlier rb kept cannot show a value's type, so the line's file is named
        rec.ok("spec", "set", "int8", "seeds", '"3"', agent=True)
        last_line_as_an_earlier_build_wrote_it(rec, {"int8": {"setting:seeds": "3", "source:seeds": None}})
        said, expected = ".rb/experiments/int8/experiment.yaml was written again", (".rb/experiments/int8/experiment.yaml", "changed", None, None)
    else:
        edit(rec, "experiments/int8/experiment.yaml", "value: 3", "value: '3'")
        rec.ok("doctor", "--adopt", "--why", "tidy", agent=True)
        last_line_as_an_earlier_build_wrote_it(rec, {"int8": {}} if how == "adopted-by-an-earlier-build" else None)
        said = ".rb/experiments/int8/experiment.yaml was written again"
        expected = (".rb/experiments/int8/experiment.yaml", "changed", None, None)
    assert (rec.design("int8")["standing"], changes(rec)) == ("changed", [expected]), changes(rec)
    assert f"{LISTING}\n  changed since you confirmed it: {said}\n" in rec.text("show", "int8"), rec.text("show", "int8")
    assert f"    changed since you confirmed it: {said}\n" in rec.text("confirm", "int8")


def test_rb_confirm_shows_what_a_terminal_would_hide_or_act_on_as_its_escape(rec):
    """Review of the snapshot rule, round 3 (group F did the same): rb confirm printed a session's words raw. A
    hypothesis holding an erase-line and a cursor move showed, on a terminal, only the words after them, and the user
    confirmed the hidden hypothesis with the command listed under it; a direction mark in a gate branch turned its
    words around. The listing, and the refusal's `It reads now`, write each such character as its escape."""
    hidden = "INT8 loses up to 5 points of top-1\x1b[2K\x1b[5Ghypothesis: " + HYPOTHESIS
    rec.ok("experiment", "add", "INT8 vs FP32", "--id", "int8", "-q", "q-acc", "--hypothesis", hidden, "-m", "docs/plan.md:3 names INT8",
           "--baseline", "fp32", "--candidate", "int8", "--if-holds", "ship ‮INT8", "--if-not", "keep FP32", agent=True)
    rec.ok("criterion", "add", "INT8 loses at most 0.5 top-1", "-e", "int8", "-q", "q-acc", "--metric", "change.top1", "--at-least", "-0.005",
           "--id", "acc-tol", "--when-met", "INT8 stays⁦ within budget", agent=True)
    rec.ok("spec", "set", "int8", "label", '"fp\\u009bint8"', agent=True)
    listed = rec.text("confirm", "int8")
    assert not [c for c in listed if c not in "\n" and (ord(c) < 0x20 or 0x7f <= ord(c) <= 0x9f or 0x2066 <= ord(c) <= 0x2069 or 0x202a <= ord(c) <= 0x202e)], repr(listed)
    for line in ('hypothesis: "INT8 loses up to 5 points of top-1\\u001b[2K\\u001b[5Ghypothesis: ' + HYPOTHESIS + '"',
                 'gate: if it holds: "ship \\u202eINT8" · if not: keep FP32 · not confirmed', 'setting label: "fp\\u009bint8"'):
        assert f"    {line}\n" in listed, listed
    assert 'when met, "INT8 stays\\u2066 within budget"' in listed, listed
    first = rec.design("int8")["confirm"].split("@")[1]
    rec.ok("spec", "set", "int8", "optim.lr", "0.001", agent=True)
    err = json.loads(rec.run("confirm", f"int8@{first}", "--json").stdout)["errors"][0]
    assert 'hypothesis: "INT8 loses up to 5 points of top-1\\u001b[2K\\u001b[5Ghypothesis: ' + HYPOTHESIS + '"' in err["problems"], err


@pytest.mark.parametrize("field,first,second", [
    pytest.param("hypothesis", "INT8 keeps top-1", "INT8 keeps\ntop-1", id="hypothesis-line-break"),
    pytest.param("hypothesis", "INT8 keeps top-1", "INT8 keeps top-1 ", id="hypothesis-trailing-space"),
    pytest.param("why", "the latency budget is tight", "the latency\nbudget is tight", id="why-line-break"),
    pytest.param("freeze", "before the runs", "before\nthe runs", id="freeze-reason-line-break"),
    pytest.param("amendment", "the plan says so", "the plan\nsays so", id="amendment-reason-line-break"),
    pytest.param("target", "0.123456789012345", "0.1234567890123451", id="rule-target-16th-digit"),
    pytest.param("noise", "0.123456789012345", "0.1234567890123451", id="rule-noise-16th-digit"),
])
def test_rb_confirm_never_lists_two_designs_alike_on_screen(tmp_path, tmp_path_factory, field, first, second):
    """Review of the snapshot rule, round 3: rb confirm folded a line break in a hypothesis, a why or a reason into a
    space, showed a trailing space as nothing, and cut a rule's number at 15 digits, so two designs with different keys
    listed alike, and a change between the listing and the confirmation was refused with `It reads now` showing the very
    lines the user had read. Each lists apart."""
    shown, keys = [], []
    for n, value in enumerate((first, second)):
        r = Rec(tmp_path / f"r{n}", tmp_path_factory.mktemp("home"))
        r.env["RB_TEST_NOW"] = "2026-10-14T09:00:00+00:00"
        r.ok("init", "INT8 ResNet-50", "--no-agents")
        r.ok("question", "add", "Does INT8 cost ResNet-50 top-1 on ImageNet?", "--id", "q-acc")
        r.ok("metric", "add", "top1", "--maximize")
        r.ok("experiment", "add", "INT8 vs FP32", "--id", "int8", "-q", "q-acc", "--hypothesis", value if field == "hypothesis" else HYPOTHESIS,
             "-m", value if field == "why" else "docs/plan.md:3 names INT8", "--baseline", "fp32", "--candidate", "int8", agent=True)
        r.ok("criterion", "add", "INT8 keeps top-1", "-e", "int8", "-q", "q-acc", "--metric", "change.top1", "--at-least", value if field == "target" else "-0.005",
             "--noise", value if field == "noise" else "0.001", "--id", "acc-tol", agent=True)
        r.ok("spec", "set", "int8", "optim.lr", "0.001", agent=True)
        if field in ("freeze", "amendment"):
            r.ok("freeze", "int8", "-m", value if field == "freeze" else "before the runs", agent=True)
        if field == "amendment":
            r.ok("spec", "set", "int8", "optim.lr", "0.002", "--amend", "--why", value, agent=True)
        # what a terminal shows: no line's trailing spaces, and no command, which names the key
        shown.append([line.rstrip() for line in r.text("confirm", "int8").splitlines() if not line.startswith("    confirm: ")])
        keys.append(r.design("int8")["confirm"])
    assert keys[0] != keys[1] and shown[0] != shown[1], shown


@pytest.mark.parametrize("change", ["a-branch-holding-the-gates-delimiter", "a-line-break-in-a-branch", "a-line-break-in-the-hypothesis", "an-escape-in-the-hypothesis"])
def test_a_changed_since_line_tells_apart_what_reads_alike_on_screen(rec, change):
    """Review of the snapshot rule, round 3: after a confirmation, a session's change to a gate branch that holds
    `· if not:`, or a line break in a branch, the hypothesis, the purpose or the why, was said with the same was and now
    (`the gate, was "if it holds: ship INT8 · if not: keep FP32", now "if it holds: ship INT8 · if not: keep FP32"`), and
    an escape in the words went to the terminal raw. A branch is said as rb confirm lists it, words are quoted and
    escaped, and nothing a screen would hide or act on is printed raw."""
    session_design(rec)
    confirm(rec)
    if change == "a-branch-holding-the-gates-delimiter":
        rec.ok("experiment", "gate", "int8", "--if-holds", "ship INT8 · if not: keep FP32", "--if-not", "", agent=True)
        part = "the gate"
    elif change == "a-line-break-in-a-branch":
        rec.ok("experiment", "gate", "int8", "--if-holds", "ship\nINT8", "--if-not", "keep FP32", agent=True)
        part = "the gate"
    else:
        hypothesis = HYPOTHESIS.replace(" of FP32", "\nof FP32") if change == "a-line-break-in-the-hypothesis" else HYPOTHESIS + "\x1b[2K"
        edit(rec, "experiments/int8/experiment.yaml", f"hypothesis: {HYPOTHESIS}", "hypothesis: " + json.dumps(hypothesis))
        rec.ok("doctor", "--adopt", "--why", "tidy", agent=True)
        part = "hypothesis"
    [c] = [c for c in rec.design("int8")["changes"] if c["part"] == part]
    assert c["change"] == "changed" and c["was"] != c["now"], c
    [line] = [x for x in rec.text("show", "int8").splitlines() if x.startswith(f"  changed since you confirmed it: {part}, was ")]
    was, now = line.split(", was ", 1)[1].split(", now ", 1)
    assert was != now and not [x for x in line if ord(x) < 0x20 or 0x7f <= ord(x) <= 0x9f], line


@pytest.mark.parametrize("char", [" ", " ", "\u0085"], ids=["line-separator", "paragraph-separator", "next-line"])
def test_a_value_holding_a_line_separator_keeps_the_log_and_the_journal_whole(rec, char):
    """Review of the snapshot rule, round 3, beside rb confirm's raw words (group F and every earlier rb did the same):
    a value holding U+2028, U+2029 or U+0085 was written into the log as it is, and the log's reader, which splits
    lines as Python does, read that line as two, so every later command failed with `.rb/log.jsonl line N is not a log
    entry`; a command run through rb journal run that held one was dropped from the journal. rb writes the character
    escaped, and reads each back as it was given."""
    session_design(rec)
    value = f"fp{char}int8"
    rec.ok("spec", "set", "int8", "label", json.dumps(value), agent=True)
    assert rec.run("status").returncode == 0, rec.run("status").stderr
    [setting] = [s for s in rec.ok("show", "int8")["data"]["object"]["settings"] if s["name"] == "label"]
    assert setting["value"] == value
    log = (rec.root / ".rb" / "log.jsonl").read_text(encoding="utf-8")
    assert char not in log and value in [json.loads(x).get("detail", {}).get("value") for x in log.split("\n") if x]
    assert rec.run("journal", "run", "--", "echo", value).returncode == 0
    assert [e["command"] for e in rec.ok("journal", "-n", "50")["data"]["entries"] if e.get("op") == "run"] == [f"echo '{value}'"]


# Recheck of the snapshot rule, round 3.


TAGS = "".join(chr(0xE0000 + ord(c)) for c in "and INT8 ships")


@pytest.mark.parametrize("hidden,escaped", [
    pytest.param(TAGS, "".join(f"\\udb40\\udc{ord(c):02x}" for c in "and INT8 ships"), id="tag-characters"),
    pytest.param("\u00ad", "\\u00ad", id="soft-hyphen"),
    pytest.param("\ufe0f", "\\ufe0f", id="variation-selector"),
    pytest.param("\u180e", "\\u180e", id="mongolian-vowel-separator"),
    pytest.param("\ufff9x\ufffa\ufffb", "\\ufff9x\\ufffa\\ufffb", id="interlinear-annotation"),
    pytest.param("\u034f", "\\u034f", id="combining-grapheme-joiner"),
    pytest.param("\u3164", "\\u3164", id="hangul-filler"),
])
def test_rb_confirm_shows_characters_a_terminal_draws_as_nothing_as_escapes(rec, hidden, escaped):
    """Recheck of the snapshot rule, round 3: rb confirm's listing printed Unicode tag characters raw, so a terminal drew
    `hypothesis: INT8 keeps ...` and the user confirmed a hypothesis that went on, invisibly, `and INT8 ships`; so did a
    soft hyphen, a variation selector, a joiner or a filler, each a different key listed alike. The listing, the
    refusal's `It reads now` and a changed-since line write each as its escape, as in JSON; the record keeps it."""
    rec.ok("experiment", "add", "INT8 vs FP32", "--id", "int8", "-q", "q-acc", "--hypothesis", HYPOTHESIS + hidden, "-m", "docs/plan.md:3 names INT8",
           "--baseline", "fp32", "--candidate", "int8", "--if-holds", "ship INT8", "--if-not", "keep FP32", agent=True)
    line = f'    hypothesis: "{HYPOTHESIS}{escaped}"\n'
    listed = rec.text("confirm", "int8")
    assert line in listed and hidden[0] not in listed, repr(listed)
    first = rec.design("int8")["confirm"].split("@")[1]
    rec.ok("spec", "set", "int8", "optim.lr", "0.001", agent=True)
    err = json.loads(rec.run("confirm", f"int8@{first}", "--json").stdout)["errors"][0]
    assert line.strip() in err["problems"] and not [p for p in err["problems"] if hidden[0] in p], err
    confirm(rec)
    assert HYPOTHESIS + hidden in (rec.root / ".rb" / "experiments" / "int8" / "experiment.yaml").read_text(encoding="utf-8")
    rec.ok("experiment", "gate", "int8", "--if-holds", "ship INT8", "--if-not", "keep FP32" + hidden, agent=True)
    [said] = [x for x in rec.text("show", "int8").splitlines() if x.startswith("  changed since you confirmed it: the gate")]
    assert f"keep FP32{escaped}" in said and hidden[0] not in said, repr(said)


def test_rb_confirm_shows_a_waiting_calls_hidden_characters_as_escapes(rec):
    """Recheck of the snapshot rule, round 3, beside the design's listing: a call a session recorded waits in the same
    listing, and its reason was printed raw, so words a terminal draws as nothing went with the call the user confirmed."""
    session_design(rec)
    rec.ok("decide", "acc-tol", "accept", "--why", "the saving holds" + TAGS, agent=True)
    [line] = [x for x in rec.text("confirm").splitlines() if "the saving holds" in x]
    assert TAGS[0] not in line and "the saving holds\\udb40\\udc61" in line, repr(line)


@pytest.mark.parametrize("how", ["a-term-beside-a-value-it-states", "a-type-and-an-edit-outside-rb", "a-line-that-names-nothing-beside-a-part"])
def test_after_an_earlier_builds_confirmation_each_change_is_said_by_the_part_it_changed(rec, how):
    """Recheck of the snapshot rule, round 3: after a confirmation group E's or F's build wrote, the changed-since lines
    named the wrong part, or left a change out. That build's rb spec set names a setting with its source whenever it
    states both: a new term read `setting optim.wd, was 0.0001, now 0.0001 (a number)`, though the value had not changed,
    and nothing said the term. A file edited outside rb, or written by a line that names no part, was said only where
    nothing else was. Each part such a line names is said where rb's words do not say it as it reads now, a source by
    what tells it apart; a line that names none it can say is said by the file it wrote; every file edited outside rb is
    said."""
    rec.write("docs/train.md", "# Training\n\nWe train with a learning rate of 0.001 and a weight decay of 0.0001 for every run.\n")
    session_design(rec)
    rec.ok("spec", "set", "int8", "seeds", "3", agent=True)
    rec.ok("spec", "set", "int8", "optim.wd", "0.0001", "--source", "docs/train.md:3", agent=True)
    confirm(rec)
    as_an_earlier_build_confirmed_it(rec, True)
    assert rec.design("int8")["standing"] == "confirmed"
    if how == "a-term-beside-a-value-it-states":
        rec.ok("spec", "set", "int8", "optim.wd", "0.0001", "--source", "docs/train.md:3", "--term", "weight decay", agent=True)
        # its line, before the check of the source that follows it
        log = rec.root / ".rb" / "log.jsonl"
        lines = [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines()]
        assert [x["op"] for x in lines[-2:]] == ["spec_set", "spec_verify"], lines[-2:]
        detail = lines[-2]["detail"]
        detail.pop("parts_compared")
        detail["changed_parts"] = {"int8": {"setting:optim.wd": "0.0001", "source:optim.wd": "docs/train.md:3"}}
        log.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in lines), encoding="utf-8", newline="")
        # the closing pass: a term the listing could not show may have been there already, so the line's file is named
        expected = [(".rb/experiments/int8/experiment.yaml", "changed", None, None)]
        said = [".rb/experiments/int8/experiment.yaml was written again"]
    elif how == "a-type-and-an-edit-outside-rb":
        rec.ok("spec", "set", "int8", "seeds", '"3"', agent=True)
        last_line_as_an_earlier_build_wrote_it(rec, {"int8": {"setting:seeds": "3"}})
        edit(rec, "experiments/int8/experiment.yaml", "value: 0.001", "value: '0.001'")
        # the closing pass: nor a type, and the file the line wrote is the one edited outside rb, said once
        expected = [(".rb/experiments/int8/experiment.yaml", "changed", None, None)]
        said = [".rb/experiments/int8/experiment.yaml was edited outside rb"]
    else:
        rec.ok("spec", "set", "int8", "optim.lr", '"0.001"', agent=True)
        last_line_as_an_earlier_build_wrote_it(rec, None)
        expected = [("setting optim.lr's source", "removed", "configs/train.yaml#optim.lr", None), (".rb/experiments/int8/experiment.yaml", "changed", None, None)]
        said = ["setting optim.lr's source removed, was configs/train.yaml#optim.lr", ".rb/experiments/int8/experiment.yaml was written again"]
    assert (rec.design("int8")["standing"], changes(rec)) == ("changed", expected), changes(rec)
    shown = rec.text("show", "int8")
    assert f"{LISTING}\n" + "".join(f"  changed since you confirmed it: {x}\n" for x in said) in shown, shown
    assert "".join(f"    changed since you confirmed it: {x}\n" for x in said) in rec.text("confirm", "int8")


def test_a_page_keeps_the_spaces_of_a_designs_changed_since_lines(rec):
    """Recheck of the snapshot rule, round 3: after a session put two spaces in a confirmed gate's branch, a page said
    `changed since it was confirmed: the gate, was "...ship INT8...", now "...ship INT8..."`, the same was and now, as it
    collapsed the run of spaces. A page keeps the spaces of a design's lines, and only of those."""
    session_design(rec)
    confirm(rec)
    rec.ok("experiment", "gate", "int8", "--if-holds", "ship  INT8", "--if-not", "keep FP32", agent=True)
    out = rec.root / "page.html"
    rec.ok("page", "--out", str(out))
    page = out.read_text(encoding="utf-8")
    assert ".dline{white-space:pre-wrap}" in page
    [line] = [html_mod.unescape(x) for x in re.findall(r'<p class="dline">([^<]*changed since it was confirmed[^<]*)</p>', page)]
    assert "if it holds: ship INT8 · if not: keep FP32" in line and "if it holds: ship  INT8 · if not: keep FP32" in line, line
    assert re.findall(r'<p class="dline">([^<]*)', page)[0].startswith("design not confirmed")
    assert not [x for x in re.findall(r'<p class="dline">([^<]*)</p>', page) if x.startswith(("hypothesis", "why"))]


def test_a_page_has_the_rule_for_a_designs_lines_only_where_it_shows_one(rec):
    """Trials of the snapshot rule: a page of a record no session wrote to carried one style rule more than group F's,
    for design lines it has none of, so it no longer read byte for byte as before. The rule comes with a design's line,
    and it is the only difference between the two pages' styles."""
    style = lambda page: re.search(r"<style>(.*?)</style>", page, re.S).group(1)
    user_design(rec)
    out = rec.root / "page.html"
    rec.ok("page", "--out", str(out))
    yours = out.read_text(encoding="utf-8")
    assert 'class="dline' not in yours and 'class="dstd' not in yours
    rec.ok("experiment", "gate", "int8", "--if-holds", "ship  INT8", "--if-not", "keep FP32", agent=True)
    rec.ok("page", "--out", str(out))
    waits = out.read_text(encoding="utf-8")
    assert '<p class="dline">design not confirmed' in waits
    # the shell's one stylesheet holds the rule whatever the record: a page with no design line reads as it did
    assert style(waits) == style(yours) and ".dline{white-space:pre-wrap}" in style(yours)


def test_a_gate_and_its_words_weigh_as_one_part_of_the_design_said_against(rec):
    """Trials of the snapshot rule: the user confirmed int8; a session changed the gate's first branch with words, and
    the user confirmed that too; a session then changed the second branch, which took the words away. The change was
    said against the first confirmation, one part away (the gate), not the one just made, two away (the gate and its
    words): the branch the user had just confirmed read as new, and nothing said the words went. A gate and the words
    given with it weigh as one part, as the freeze and its reason do, and the latest of the closest is said."""
    session_design(rec)
    confirm(rec)
    rec.ok("experiment", "gate", "int8", "--if-holds", "ship INT8 next month", "--words", "we ship INT8 next month", agent=True)
    confirm(rec)
    rec.ok("experiment", "gate", "int8", "--if-not", "rerun with calibration 2", agent=True)
    was, now = "if it holds: ship INT8 next month · if not: keep FP32", "if it holds: ship INT8 next month · if not: rerun with calibration 2"
    assert changes(rec) == [("the gate", "changed", was, now), ("the gate's words (as given)", "removed", "we ship INT8 next month", None)], changes(rec)
    said = [f'the gate, was "{was}", now "{now}"', 'the gate\'s words (as given) removed, was "we ship INT8 next month"']
    assert f"{LISTING}\n" + "".join(f"  changed since you confirmed it: {x}\n" for x in said) in rec.text("show", "int8"), rec.text("show", "int8")


# The closing pass after the snapshot rule's last recheck.


ESCAPED = "".join(f"\\udb40\\udc{ord(c):02x}" for c in "and INT8 ships")


def test_every_screen_draws_what_the_user_confirms_with_the_characters_a_terminal_hides_as_escapes(rec, monkeypatch):
    """Last recheck of the snapshot rule: rb confirm listed a waiting call's reason and words with their escapes, but rb
    open's group, rb status and rb show drew the same text raw, and the command beside it made the user's a call whose
    reason went on, invisibly, `and INT8 ships`; a design's title, hypothesis and gate were raw on its own views, and so
    was the gate on rb confirm's receipt. Every screen of a record where something a session wrote can wait draws each
    such character as its escape, as rb confirm lists it; --json and the record keep the text."""
    user_design(rec)
    rec.ok("decide", "int8", "ship", "-m", "it holds" + TAGS, "--words", "ship it" + TAGS, agent=True)
    rec.ok("experiment", "add", "INT4 vs FP32" + TAGS, "--id", "int4", "-q", "q-acc", "--hypothesis", "INT4 cuts latency" + TAGS, "-m", "the budget" + TAGS,
           "--baseline", "fp32", "--candidate", "int4", "--if-holds", "ship INT4" + TAGS, "--if-not", "keep FP32", agent=True)
    rec.ok("spec", "set", "int4", "opt.name", json.dumps("alpha" + TAGS), agent=True)
    [call] = [d["id"] for d in rec.ok("status")["data"]["pending"]]
    screens = {argv: rec.text(*argv) for argv in (("status",), ("context",), ("show", "int4"), ("show", "int8"), ("show", call), ("log",), ("confirm",))}
    for argv, text in screens.items():
        assert TAGS[0] not in text, (argv, text)
    for argv in (("status",), ("context",), ("show", "int8"), ("show", call), ("confirm",)):
        assert f"note: it holds{ESCAPED} · words (as given): \"ship it{ESCAPED}\"" in screens[argv], (argv, screens[argv])
    for line in (f"Experiment int4: INT4 vs FP32{ESCAPED}", f"  hypothesis: INT4 cuts latency{ESCAPED}", f"  if it holds: ship INT4{ESCAPED}", f"alpha{ESCAPED}"):
        assert line in screens[("show", "int4")], (line, screens[("show", "int4")])
    assert TAGS[0] not in rec.page()
    state = rec.state(monkeypatch)
    assert TAGS[0] not in json.dumps(state, ensure_ascii=False)
    rows = {p["kind"]: p for p in state["home"]["pending"]}
    assert f"note: it holds{ESCAPED} · words (as given): \"ship it{ESCAPED}\"" in rows["proposed"]["text"], rows
    assert rows["design"]["text"] == f"int4's design: INT4 vs FP32{ESCAPED}" and brief(state, "int4")["hypothesis"] == f"INT4 cuts latency{ESCAPED}"
    # --json and the record keep the text as it was given
    data = rec.ok("status")["data"]
    assert [d["why"] for d in data["pending"]] == ["it holds" + TAGS] and "INT4 cuts latency" + TAGS in [e["hypothesis"] for e in data["experiments"]]
    # and so does what confirming each says
    receipt = rec.text("confirm", call)
    assert TAGS[0] not in receipt and ESCAPED in receipt, receipt
    receipt = rec.text(*rec.design("int4")["confirm"].split()[1:])
    assert TAGS[0] not in receipt and f"  gate: if it holds: ship INT4{ESCAPED} · if not: keep FP32\n" in receipt, receipt


def test_a_record_no_session_wrote_in_draws_its_text_as_it_always_has(rec, monkeypatch):
    """Beside the last: a record whose every write the user made in their own terminal holds nothing that waits for
    them, and its screens draw its text as group F's did, a character a terminal hides included."""
    user_design(rec)
    rec.ok("experiment", "add", "INT4 vs FP32", "--id", "int4", "-q", "q-acc", "--hypothesis", "INT4 cuts latency" + TAGS, "--baseline", "fp32", "--candidate", "int4")
    assert f"  hypothesis: INT4 cuts latency{TAGS}\n" in rec.text("show", "int4") and f"INT4 cuts latency{TAGS}" in rec.text("status")
    assert f"INT4 cuts latency{TAGS}" in rec.page()
    assert brief(rec.state(monkeypatch), "int4")["hypothesis"] == "INT4 cuts latency" + TAGS


@pytest.mark.parametrize("adopted", ["as-an-earlier-build-adopted-it", "with-no-parts-named"])
def test_an_earlier_builds_line_that_says_one_part_also_names_the_file_it_wrote(rec, adopted):
    """Last recheck of the snapshot rule: after group F's or E's confirmation, a session edited int8's file, optim.lr from
    0.001 to 0.002 and seeds from 3 to '3', and that build's rb doctor --adopt named optim.lr alone, as its words say
    seeds alike. rb said optim.lr's change and nothing else, so the type change hid behind the part it said. A line an
    earlier rb wrote cannot show every change: beside what it says, the file it wrote is named."""
    session_design(rec)
    rec.ok("spec", "set", "int8", "seeds", "3", agent=True)
    confirm(rec)
    as_an_earlier_build_confirmed_it(rec, True)
    edit(rec, "experiments/int8/experiment.yaml", "value: 3", "value: '3'")
    edit(rec, "experiments/int8/experiment.yaml", "value: 0.001", "value: 0.002")
    rec.ok("doctor", "--adopt", "--why", "tidy", agent=True)
    named = {"int8": {"setting:optim.lr": "0.002", "source:optim.lr": "configs/train.yaml#optim.lr"}}
    last_line_as_an_earlier_build_wrote_it(rec, named if adopted == "as-an-earlier-build-adopted-it" else None)
    expected = [("setting optim.lr", "changed", "0.001", "0.002"), (".rb/experiments/int8/experiment.yaml", "changed", None, None)]
    assert (rec.design("int8")["standing"], changes(rec)) == ("changed", expected), changes(rec)
    said = ["setting optim.lr, was 0.001, now 0.002", ".rb/experiments/int8/experiment.yaml was written again"]
    assert f"{LISTING}\n" + "".join(f"  changed since you confirmed it: {x}\n" for x in said) in rec.text("show", "int8"), rec.text("show", "int8")
    assert "".join(f"    changed since you confirmed it: {x}\n" for x in said) in rec.text("confirm", "int8")


@pytest.mark.parametrize("value,source,said", [
    ('"0.0001"', "docs/train.md:3", ["setting optim.wd, was 0.0001, now 0.0001 (text)", 'setting optim.wd\'s source, was docs/train.md:3, now docs/train.md:3 (term "weight decay")']),
    ("0.0002", "docs/train.md:3", ["setting optim.wd, was 0.0001, now 0.0002", 'setting optim.wd\'s source, was docs/train.md:3, now docs/train.md:3 (term "weight decay")']),
    ("0.0002", "docs/train2.md:3", ["setting optim.wd, was 0.0001, now 0.0002", 'setting optim.wd\'s source, was docs/train.md:3, now docs/train2.md:3 (term "weight decay")']),
], ids=["a-type-and-a-term", "a-value-and-a-term", "a-value-a-path-and-a-term"])
def test_after_an_earlier_builds_confirmation_a_sessions_value_and_term_are_both_said(rec, value, source, said):
    """Last recheck of the snapshot rule: after group F's or E's confirmation, one rb spec set by a session changed
    optim.wd's value or type and gave its source a term; rb said the setting alone, since this build's line named a
    setting's source with every value it wrote, changed or not, and rb left every such source out. A session's line names
    a source only where it changed, and rb says the source with the term its short form leaves out."""
    rec.write("docs/train.md", "# Training\n\nWe train with a learning rate of 0.001 and a weight decay of 0.0001 for every run.\n")
    rec.write("docs/train2.md", "# Training\n\nWe use a weight decay of 0.0002 here.\n")
    session_design(rec)
    rec.ok("spec", "set", "int8", "optim.wd", "0.0001", "--source", "docs/train.md:3", agent=True)
    confirm(rec)
    as_an_earlier_build_confirmed_it(rec, True)
    assert rec.design("int8")["standing"] == "confirmed"
    rec.ok("spec", "set", "int8", "optim.wd", value, "--source", source, "--term", "weight decay", "--no-verify", agent=True)
    shown = rec.text("show", "int8")
    assert f"{LISTING}\n" + "".join(f"  changed since you confirmed it: {x}\n" for x in said) in shown, shown
    # a session's write of the value alone names the setting, not the source it left as it was
    rec.ok("spec", "set", "int8", "optim.wd", "0.0003", "--source", source, "--term", "weight decay", "--no-verify", agent=True)
    [line] = [json.loads(x) for x in (rec.root / ".rb" / "log.jsonl").read_text(encoding="utf-8").splitlines() if '"spec_set"' in x][-1:]
    assert line["detail"]["changed_parts"] == {"int8": {"setting:optim.wd": "0.0003"}}, line["detail"]


@pytest.mark.parametrize("first,second,said", [
    ('"alpha beta"', '"alpha beta "', 'setting opt.name, was "alpha beta", now "alpha beta "'),
    (json.dumps('"alpha beta\\u00ad"'), json.dumps("alpha beta­"), 'setting opt.name, was "\\"alpha beta\\\\u00ad\\"", now "alpha beta\\u00ad"'),
    ('"alpha beta"', json.dumps("alpha beta"), 'setting opt.name, was "alpha beta", now "alpha\\u00a0beta"'),
], ids=["a-trailing-space", "an-escape-written-out-and-the-character", "a-no-break-space"])
def test_a_was_and_a_now_a_screen_would_draw_alike_are_quoted_and_escaped(rec, first, second, said):
    """Last recheck of the snapshot rule: a session's change of a confirmed setting's text read `was alpha beta, now alpha
    beta `, the trailing space drawn as nothing; one from the text `"alpha beta\\u00ad"`, written out, to the soft
    hyphen itself read the same was and now byte for byte, and so did a no-break space for a space. A was and a now a
    screen would draw alike are each quoted and escaped in full."""
    session_design(rec)
    rec.ok("spec", "set", "int8", "opt.name", first, agent=True)
    confirm(rec)
    rec.ok("spec", "set", "int8", "opt.name", second, agent=True)
    shown = rec.text("show", "int8")
    assert f"{LISTING}\n  changed since you confirmed it: {said}\n" in shown, shown


def test_what_meeting_a_criterion_would_mean_is_said_escaped_where_it_changed(rec):
    """Beside the last: what meeting acc-tol would mean moved from the words `stays\\u00ad`, written out, to the soft hyphen
    itself, and the changed-since line said `was "...stays\\u00ad", now "...stays\\u00ad"`: the words were quoted as the
    link holds them, unescaped. They are quoted and escaped, as rb confirm lists them."""
    session_design(rec)
    rec.ok("criterion", "file", "acc-tol", "q-acc", "--when-met", "INT8 stays\\u00ad", "-m", "reworded", agent=True)
    confirm(rec)
    rec.ok("criterion", "file", "acc-tol", "q-acc", "--when-met", "INT8 stays­", "-m", "reworded", agent=True)
    said = 'what meeting acc-tol would mean for q-acc, was "INT8 stays\\\\u00ad", now "INT8 stays\\u00ad"'
    shown = rec.text("show", "int8")
    assert f"  changed since you confirmed it: {said}\n" in shown, shown


def test_a_page_that_draws_escapes_wraps_them_and_one_that_draws_none_keeps_its_style(rec):
    """Beside the first of the closing pass: a waiting call's reason drawn with its escapes is one word as long as its
    hidden characters make it, and it ran past the page's edge, out of sight, at 1280 pixels and on a phone. A page that
    draws an escape wraps every word, however long; one that draws none keeps the style it had."""
    style = lambda page: re.search(r"<style>(.*?)</style>", page, re.S).group(1)
    user_design(rec)
    rec.ok("decide", "int8", "ship", "-m", "it holds", agent=True)
    out = rec.root / "page.html"
    rec.ok("page", "--out", str(out))
    plain = out.read_text(encoding="utf-8")
    rec.ok("decide", "int8", "park", "-m", "later" + TAGS, agent=True)
    rec.ok("page", "--out", str(out), "--replace")
    drawn = out.read_text(encoding="utf-8")
    assert TAGS[0] not in drawn and ESCAPED in drawn
    # the shell's one stylesheet wraps every word, however long, whatever the record draws
    assert f'<p class="wt">park int8 · note: later{ESCAPED}' in drawn
    assert style(drawn) == style(plain) and ".wt,.wp{margin:0;overflow-wrap:break-word}" in style(plain) and "overflow-wrap:break-word}" in style(plain)


# The recheck of the closing pass.


def test_rb_contexts_data_keeps_a_waiting_calls_text_as_written(rec):
    """Recheck of the closing pass: the pass drew a waiting call's reason and words with their escapes where the rows
    are made, so rb context --json's markdown, MCP's context and the SDK's context() carried the escapes as text, where
    group F gave the text as written, as every --json does. A screen draws the escapes; the briefing's data does not."""
    user_design(rec)
    rec.ok("decide", "int8", "ship", "-m", "it holds" + TAGS, "--words", "ship it" + TAGS, agent=True)
    server = Mcp(root=rec.root, env=rec.env)
    try:
        server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "clientInfo": {"name": "briefing-client"}}})
        tool = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "context", "arguments": {}}})["result"]
    finally:
        server.close()
    briefings = {"rb context --json": rec.ok("context")["data"]["markdown"], "MCP": tool["content"][0]["text"], "SDK": rabbit_brain.open(rec.root).context()}
    for where, markdown in briefings.items():
        assert f'note: it holds{TAGS} · words (as given): "ship it{TAGS}"' in markdown and "\\udb40" not in markdown, (where, markdown)
    for argv in (("context",), ("status",)):
        text = rec.text(*argv)
        assert f'note: it holds{ESCAPED} · words (as given): "ship it{ESCAPED}"' in text and TAGS[0] not in text, (argv, text)


def test_rb_shows_warning_of_a_hand_edit_draws_what_a_terminal_hides_as_escapes(rec):
    """Recheck of the closing pass: in a record where a session's design was confirmed and a call waits, a hand edit put
    tag characters in int8's hypothesis, in what meeting acc-tol would mean and in the call's reason. rb show drew them
    as escapes, but its warning that the file was edited outside rb printed them raw on stderr. The warning draws them as
    the rest of the screen does; --json keeps them as written among its warnings. In a record no session wrote in, the
    warning says them as group F did."""
    rec.ok("experiment", "add", "INT4 vs FP32", "--id", "int4", "-q", "q-acc", "--hypothesis", "INT4 cuts latency", "--baseline", "fp32", "--candidate", "int4")
    edit(rec, "experiments/int4/experiment.yaml", "hypothesis: INT4 cuts latency", "hypothesis: " + json.dumps("INT4 cuts latency" + TAGS, ensure_ascii=False))
    r = rec.run("show", "int4")
    assert "edited outside rb" in r.stderr and TAGS in r.stderr and ESCAPED not in r.stderr, r.stderr
    session_design(rec)
    confirm(rec)
    rec.ok("decide", "int8", "park", "-m", "later", agent=True)
    [call] = [d["id"] for d in rec.ok("status")["data"]["pending"]]
    edit(rec, "experiments/int8/experiment.yaml", f"hypothesis: {HYPOTHESIS}", "hypothesis: " + json.dumps(HYPOTHESIS + TAGS, ensure_ascii=False))
    edit(rec, "experiments/int8/criteria/acc-tol.yaml", "INT8 stays within the accuracy budget", "INT8 stays within the accuracy budget" + TAGS)
    edit(rec, f"decisions/{call}.yaml", "later", "later" + TAGS)
    for subject in ("int4", "int8", "acc-tol", call):
        r = rec.run("show", subject)
        assert "edited outside rb" in r.stderr and ESCAPED in r.stderr and TAGS[0] not in r.stdout + r.stderr, (subject, r.stdout, r.stderr)
        r = rec.run("show", subject, "--json")
        [warning] = json.loads(r.stdout)["data"]["warnings"]
        assert TAGS in warning and ESCAPED in r.stderr and TAGS[0] not in r.stderr, (subject, warning, r.stderr)


# The trials of the closing pass.


def test_a_designs_data_gives_its_gate_as_written(rec):
    """Trials of the closing pass: in a record the user alone wrote, rb status --json gave int4's gate under design.parts
    quoted and escaped (`if it holds: "ship\\u202e INT4"`), where group F gave it as written, as it gives the hypothesis
    and every other part. design.parts gives the gate as written, wherever a session wrote too; rb confirm lists it
    escaped, and a change's was and now say each branch as rb confirm lists it, as before."""
    rlo = "\u202e"
    rec.ok("experiment", "add", "INT4 vs FP32", "--id", "int4", "-q", "q-acc", "--hypothesis", "INT4 cuts latency" + rlo,
           "--baseline", "fp32", "--candidate", "int4", "--if-holds", f"ship{rlo} INT4", "--if-not", "keep FP32 · for now")
    gate = f"if it holds: ship{rlo} INT4 · if not: keep FP32 · for now"

    def parts(exp: str, *argv: str) -> dict:
        data = rec.ok(*argv)["data"]
        [e] = [e for e in data["experiments"] if e["id"] == exp] if "experiments" in data else [data]
        return {p["key"]: p["value"] for p in e["design"]["parts"]}

    for argv in (("status",), ("show", "int4")):
        said = parts("int4", *argv)
        assert (said["gate"], said["hypothesis"]) == (gate, "INT4 cuts latency" + rlo), (argv, said)
    # a design a session wrote: its data as written, its listing escaped
    session_design(rec)
    rec.ok("experiment", "gate", "int8", "--if-holds", f"ship{rlo} INT8", agent=True)
    assert parts("int8", "status")["gate"] == f"if it holds: ship{rlo} INT8 · if not: keep FP32"
    listing = rec.text("confirm", "int8")
    assert rlo not in listing and '    gate: if it holds: "ship\\u202e INT8" · if not: keep FP32' in listing, listing


def test_a_pages_overview_wraps_a_question_drawn_with_escapes_in_its_column(rec):
    """Trials of the closing pass: a question a session added with characters a terminal hides was drawn on a page's
    overview with its escapes, one word wider than a 390-pixel screen, and the overview, whose one column took the
    width of that word, cut it before its end. A page that draws an escape lets each of the overview's columns shrink
    to the screen, so the word wraps inside it; one that draws none keeps its style."""
    style = lambda page: re.search(r"<style>(.*?)</style>", page, re.S).group(1)
    user_design(rec)
    rec.ok("decide", "int8", "ship", "-m", "it holds", agent=True)
    out = rec.root / "page.html"
    rec.ok("page", "--out", str(out))
    plain = out.read_text(encoding="utf-8")
    rec.ok("question", "add", "Does INT4 hold" + TAGS + "?", "--id", "q-int4", agent=True)
    rec.ok("page", "--out", str(out), "--replace")
    drawn = out.read_text(encoding="utf-8")
    assert f'<a class="sq" href="#q-q-int4"><span class="au">Does INT4 hold{ESCAPED}?</span></a>' in drawn
    # the overview's row lets its question shrink to the screen, in the shell's one stylesheet, whatever the record
    assert style(drawn) == style(plain) and style(plain).split(".srow .sq{", 1)[1].split("}", 1)[0].endswith("min-width:0")
