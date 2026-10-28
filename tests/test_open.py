"""rb open is the user's live view: loopback only, read-only, and every refresh is a fresh
computation. If it ever binds a public interface, accepts a write, ships an external resource, or
shows a standing the ledger does not compute, this fails."""
import hashlib
import html as html_mod
import json
import os
import queue
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from app.test_app import two_places

from rabbit_brain.models import Envelope
from rbtest import as_the_user, at_home, confirm_designs, main, object_card, Open, places_record, state

HAS_GIT = shutil.which("git") is not None
pytestmark = pytest.mark.skipif(not HAS_GIT, reason="the record is played in a git repository")

_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # never let a proxy near loopback


def _get(url: str):
    with _OPENER.open(url, timeout=10) as r:
        return r.status, r.read().decode("utf-8")


def _raw(port: int, request: str) -> tuple:
    """The status line and the body rb open answers these bytes with, every header as written (or left out)."""
    with socket.create_connection(("127.0.0.1", port), timeout=10) as s:
        s.sendall(request.encode("latin-1"))
        s.shutdown(socket.SHUT_WR)
        got = b""
        while chunk := s.recv(65536):
            got += chunk
    head, _, body = got.partition(b"\r\n\r\n")
    return head.split(b"\r\n")[0].decode("latin-1"), body.decode("utf-8")


@pytest.fixture
def served(workdir, monkeypatch, tmp_path_factory, capsys):
    as_the_user(monkeypatch)
    cfg = tmp_path_factory.mktemp("gitconfig") / "config"
    cfg.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(cfg))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    subprocess.run(["git", "init", "-q"], check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], check=True)
    subprocess.run(["git", "config", "user.name", "t"], check=True)

    def rb(*argv):
        code = main([*argv, "--json"])
        return code, Envelope.model_validate_json(capsys.readouterr().out)

    assert rb("init", "the live project")[0] == 0
    monkeypatch.setenv("CLAUDECODE", "1")
    assert rb("question", "add", "does the view stay live", "--id", "q1")[0] == 0
    assert rb("experiment", "add", "the experiment", "--id", "e1", "--hypothesis", "the view is computed fresh", "--question", "q1",
              "--baseline", "old", "--candidate", "new", "--varies", "renderer")[0] == 0
    assert rb("criterion", "add", "top1 holds", "-e", "e1", "--metric", "top1", "--at-least", "0.7", "--id", "c1")[0] == 0
    monkeypatch.delenv("CLAUDECODE")

    server = Open()                 # rb open, started the way the user starts it, in its own process
    yield rb, server.url, server
    server.close()


def _outward_addresses() -> set:
    """This machine's IPv4 addresses other than loopback: what its host name resolves to, where it resolves, and the
    address it would send from, which a UDP socket learns by connecting, without sending anything."""
    found = set()
    try:
        found |= {addr[0] for *_, addr in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET, socket.SOCK_STREAM)}
    except socket.gaierror:            # a host name nothing resolves, as a Mac's .local name can be
        pass
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("192.0.2.1", 9))          # TEST-NET-1 (RFC 5737): routed nowhere, and nothing is sent
            found.add(probe.getsockname()[0])
    except OSError:                    # no route out: no outward address
        pass
    return {a for a in found if not a.startswith("127.") and a != "0.0.0.0"}


def test_loopback_only_and_read_only(served):
    rb, url, server = served
    assert server.host == "127.0.0.1" and "this machine only." in server.printed()     # the road in is the user's ssh tunnel
    for address in _outward_addresses():                  # this machine's other addresses do not answer
        with pytest.raises(OSError):
            socket.create_connection((address, server.port), timeout=2).close()
    req = urllib.request.Request(url + "/state.json", data=b"{}", method="POST")
    try:
        _OPENER.open(req, timeout=10)
        assert False, "a write was accepted"
    except urllib.error.HTTPError as e:
        assert e.code == 405
        assert "your terminal" in e.read().decode("utf-8")


def test_a_name_rebound_to_this_machine_is_refused(served):
    """A page whose own name is made to resolve to 127.0.0.1 is same-origin with the tab and could read the whole
    record; the Host it sends gives it away, so every route refuses it, whatever it asks with. This machine's loopback
    names pass at any port, as an ssh tunnel brings them, and a request that names no host is answered as it was."""
    rb, url, server = served
    port, other = server.port, (8787 if server.port != 8787 else 8788)
    refused = {"error": "rb open answers only at 127.0.0.1, localhost or [::1]. Open the link it printed."}
    for ask in ("GET /", "GET /state.json", "GET /card.json?ref=o:e1", "GET /page.html", "GET /telemetry/inspected",
                "OPTIONS /state.json", "POST /state.json"):
        status, body = _raw(port, f"{ask} HTTP/1.1\r\nHost: attacker.example:{port}\r\nX-RB-Usage: 1\r\n\r\n")
        assert status == "HTTP/1.0 403 Forbidden" and json.loads(body) == refused, (ask, status)
    for host in ("attacker.example", f"127.0.0.1.attacker.example:{port}", f"localhost.attacker.example:{port}"):
        assert _raw(port, f"GET /state.json HTTP/1.1\r\nHost: {host}\r\n\r\n")[0] == "HTTP/1.0 403 Forbidden", host
    for host in (f"127.0.0.1:{port}", f"localhost:{other}", "localhost", f"[::1]:{port}"):
        status, body = _raw(port, f"GET /state.json HTTP/1.1\r\nHost: {host}\r\n\r\n")
        assert status == "HTTP/1.0 200 OK" and json.loads(body)["home"]["title"] == "the live project", host
    status, page = _raw(port, "GET / HTTP/1.0\r\n\r\n")
    assert status == "HTTP/1.0 200 OK" and page.startswith("<!doctype html>")
    # what this machine's own names ask is answered as before
    assert _raw(port, f"OPTIONS /state.json HTTP/1.1\r\nHost: localhost:{port}\r\n\r\n")[0] == "HTTP/1.0 501 Unsupported method ('OPTIONS')"
    assert _raw(port, f"POST /state.json HTTP/1.1\r\nHost: localhost:{port}\r\n\r\n")[0] == "HTTP/1.0 405 Method Not Allowed"


def test_every_post_is_refused_and_changes_nothing(served):
    """The tab only reads: a POST to any path, with any body, is refused with 405 before anything runs,
    and the record is exactly what it was. No write route is left, so there is no key to present."""
    rb, url, _ = served
    _, before = _get(url + "/state.json")
    ruling = json.dumps({"k": "any", "request_id": "r1", "decision": "approve", "why": "x"}).encode("utf-8")
    for path in ("/", "/state.json", "/card.json?ref=o:e1", "/page.html", "/act.json", "/nowhere"):
        for body in (b"", b"{}", ruling):
            req = urllib.request.Request(url + path, data=body, headers={"Content-Type": "application/json"}, method="POST")
            try:
                _OPENER.open(req, timeout=10)
                assert False, f"a POST to {path} was accepted"
            except urllib.error.HTTPError as e:
                assert e.code == 405, path
                assert json.loads(e.read().decode("utf-8")) == {"error": "rb open only reads. Decisions run in your terminal."}
    _, after = _get(url + "/state.json")
    assert json.loads(after)["digest"] == json.loads(before)["digest"]
    assert state().records("decision") == []
    _, html = _get(url + "/")
    assert "act.json" not in html and "POST" not in html and "launch key" not in html and "approve" not in html


def test_the_printed_link_carries_no_key(served):
    """Nothing to authorise, so nothing to mint: the link rb open prints is the bare loopback address."""
    rb, url, server = served
    err = server.printed()
    assert err.splitlines()[0] == f"rb open · http://127.0.0.1:{server.port}/"
    assert "?k=" not in err and "key" not in err and "approve" not in err and "ruling" not in err


@pytest.mark.xfail(strict=True, reason="4b gap (pins-4b.md), an old page pin that cannot pass on the shell; the fix ports it, owner A (script.js) and R (views.rs): the shell has no run selection, side-by-side comparison or run chart")
def test_the_app_is_one_file_with_nothing_external(served):
    rb, url, _ = served
    status, html = _get(url + "/")
    assert status == 200 and html.startswith("<!doctype html>")
    low = html.lower()
    assert "http://" not in low and "https://" not in low and "cdn" not in low
    # Inquiry columns distinguish the surrounding experiment from the evidence explicitly linked here.
    assert "Where the work stands" in html and "Evidence by inquiry" in html and "Complete inquiry reading" in html
    # The drawers: the same record by kind, the same shape in every project.
    assert 'const TABS=["overview","experiments","findings","runs","history","resources"];' in html
    assert 'href="#${t}"' in html
    assert "recorded by</th>" not in html and "standing</th>" in html     # no column for who recorded a run
    assert "eb.hypothesis" in html                       # the directions are on the surface, not lost: each experiment's hypothesis
    assert "data-eid" in html and "runs selected" in html   # detail pane and run-by-run comparison
    assert "filter rows" in html and "polyline" in html     # the boring tracker gifts: filter, charts
    assert "b.ruled" in html                                # rulings render, not just live in the payload
    assert "Your call" in html and "Review the calls waiting on you" in html
    assert "rb run add" not in html and "rb freeze" not in html   # no agent commands, ever
    assert "rb approve" not in html and "doAct" not in html       # nothing is settled from the tab: it only reads
    assert "\u2014" not in html                               # no em dashes, anywhere
    status, page = _get(url + "/page.html")              # the sendable receipt, one click away
    assert status == 200 and page.startswith("<!doctype html>")


def test_state_agrees_with_the_ledger_and_refreshing_recomputes(served, capsys):
    rb, url, _ = served
    _, body = _get(url + "/state.json")
    s = json.loads(body)
    st = state().status()
    tree = {r["id"]: r for r in s["home"]["tree"]}
    assert set(tree) == {"q1", "e1"} and tree["e1"]["standing"] == "planned · 1 criterion untested · not locked"
    # the payload carries the brief: the state, question by question, for any renderer to lead with
    briefs = s["home"]["briefs"]
    assert [b["id"] for b in briefs] == ["q1"]           # e1 is filed under q1
    assert any(f["criterion_id"] == "c1" for b in briefs for e in b["experiments"] for f in e["findings"])
    # the direction rides on the experiment under its question: the hypothesis verbatim, and the state it is in
    eb = briefs[0]["experiments"][0]
    assert eb["title"] == "the experiment" and eb["hypothesis"] == "the view is computed fresh" and eb["state"] == "planned"
    # the experiment carries its shape before any run: arms in role order, the declared differences
    assert eb["arms"] == "old vs new" and eb["varies"] == ["renderer"]
    assert s["home"]["runs"] == []                       # the runs drawer starts empty
    # per-criterion words travel on the card, verbatim from the ledger
    _, cbody = _get(url + "/card.json?ref=o:e1")
    bars = {b["criterion_id"]: b["standing"] for b in json.loads(cbody)["card"]["pass_bars"]}
    assert bars == {f["criterion"]: f["says"]["headline"] for f in st["findings"]}
    # the live part: a new run lands, the next request computes it, no restart, no cache
    os.environ["RB_SESSION"] = "chat-42"          # the fixture's monkeypatch is out of scope here: set and clean up by hand
    try:
        assert rb("run", "add", "e1", "top1=0.76")[0] == 0
    finally:
        os.environ.pop("RB_SESSION", None)
    _, body2 = _get(url + "/state.json")
    s2 = json.loads(body2)
    tree2 = {r["id"]: r for r in s2["home"]["tree"]}
    assert tree2["e1"]["standing"] == "open · 1 criterion met · not locked"
    # the run is a drawer row: the day, the experiment's title, the pairs verbatim, and no writer or session
    row = s2["home"]["runs"][0]
    assert row["experiment"] == "the experiment" and row["results"] == "top1=0.76"
    assert row["by"] == "" and row["note"] == ""
    assert row["experiment_id"] == "e1" and row["metrics"] == {"top1": "0.76"} and row["per_run"] == {}
    assert row["session"] == ""                          # the chat session RB_SESSION named stays in the record
    assert state().status()["runs"][0]["session"] == "chat-42"
    # the receipt rides the row for the on-demand card: stored fields verbatim, no narration, and none that says who
    rc = row["receipt"]
    assert not {"actor", "via", "session"} & set(rc)
    assert "python" in rc["environment"] and "commit" in rc["attached"]   # None before a first commit is itself the fact
    _, cbody2 = _get(url + "/card.json?ref=o:e1")
    bars2 = {b["criterion_id"]: b["standing"] for b in json.loads(cbody2)["card"]["pass_bars"]}
    assert bars2 == {f["criterion"]: f["says"]["headline"] for f in state().status()["findings"]} == {"c1": "estimate meets criterion · point only"}
    assert s2["digest"] != s["digest"]


# The tab's own script, run as a browser runs it against rb open, the document stubbed. Each line on stdin is a step:
# load, refresh, or a click where the page listens (experiment <id>, tick <run>, compare, receipt <run>); after each,
# what the pane holds is one line on stdout (after view, what the page draws: the tally, the quiet line's class and
# words, the catalogue).
TAB = r"""
const [base, page] = process.argv.slice(2);
const fs = require("fs"), vm = require("vm"), readline = require("readline");
let inflight = 0, tick = null;
const els = {}, docOn = {};
const element = id => els[id] ??= (classes => ({id, innerHTML: "", textContent: "", hidden: false, value: "", on: {}, classes, open: false,
  // what the markup drawn so far says the element is (<details id=...>), so a link to it can open it
  get tagName() { const m = Object.values(els).map(e => e.innerHTML || "").join("").match(new RegExp(`<(\\w+)[^>]*\\sid="${id}"`)); return m ? m[1].toUpperCase() : "DIV"; },
  parentElement: null, matches(s) { return s.toUpperCase() === this.tagName; }, scrollIntoView() {}, setAttribute() {}, focus() {},
  querySelector() { return {setAttribute() {}, focus() {}}; },
  classList: {add: c => classes.add(c), remove: c => classes.delete(c), toggle: (c, on = !classes.has(c)) => on ? classes.add(c) : classes.delete(c)},
  addEventListener(type, fn) { this.on[type] = fn; }}))(new Set());
const tab = vm.createContext({
  location: {hash: ""}, window: {addEventListener() {}},
  document: {title: "", getElementById: element, addEventListener(type, fn) { docOn[type] = fn; }},
  setTimeout() {}, setInterval(fn) { tick = fn; return 1; },
  fetch(path, init = {}) {
    inflight++;
    return fetch(new URL(path, base), {headers: init.headers || {}}).then(async r => {
      const text = await r.text();
      return {status: r.status, json: async () => JSON.parse(text)};
    }).finally(() => { inflight--; });
  },
});
const click = (on, attr, value) =>
  els[on].on.click({target: {closest: s => s === `[${attr}]` ? {checked: true, getAttribute: () => value} : null}});
const steps = {
  load: () => vm.runInContext(fs.readFileSync(page, "utf8"), tab),
  refresh: () => tick(),
  experiment: id => click("cat", "data-eid", id),
  design: id => click("call", "data-eid", id),
  tick: run => click("cat", "data-runsel", run),
  compare: () => click("cat", "data-cmp", "1"),
  receipt: run => click("pane", "data-rcpt", run),
  findings: () => vm.runInContext('tab="findings";draw()', tab),
  runs: () => vm.runInContext('tab="runs";draw()', tab),
  resources: () => vm.runInContext('tab="resources";draw()', tab),
  filter: () => vm.runInContext('filt="no-such-result";tab="experiments";draw()', tab),
  back: () => vm.runInContext('location.hash="";route()', tab),
  where: id => els.where.on.change({target: {id: "where-place", value: id}}),
  version: sha => els.where.on.change({target: {id: "where-at", value: sha}}),
  goback: () => els["scope-back"].on.click({}),
  follow: id => docOn.click({target: {closest: s => s === "[data-place]" ? {getAttribute: () => id} : null}, preventDefault() {}}),
  at: hash => { tab.location.hash = hash; },
  nav: hash => vm.runInContext(`location.hash=${JSON.stringify(hash)}; route()`, tab),
  view: () => {},
  classes: () => {},
  opened: () => {},
};
const scope = () => els.scope?.hidden !== false ? "" : `${els["scope-said"].textContent} | ${els["scope-back"].textContent}`;   // none drawn on a failure
const drawn = step => step === "opened" ? {opened: Object.keys(els).filter(k => els[k].open).sort()} : step === "classes" ? {cat: [...els.cat.classes].sort().join(" ")} : step === "view" ? {tally: els.tally.innerHTML, quiet: els.quiet.className || "", said: els.quiet.textContent, looked: els.looked?.innerHTML ?? "",
  cat: els.cat.innerHTML, scope: scope(), elsewhere: els.elsewhere?.hidden === false ? els.elsewhere.innerHTML : "", call: els.call?.hidden === false ? els.call.innerHTML : ""} : ["where", "version", "goback", "nav", "load", "follow", "runs", "resources"].includes(step) ? {where: els.where.innerHTML,
  failed: els.failed.hidden ? "" : els.failed.textContent, cat: els.cat.innerHTML, scope: scope(), hash: tab.location.hash,
  elsewhere: els.elsewhere?.hidden === false ? els.elsewhere.innerHTML : "", title: tab.document.title, whereClass: [...els.where.classes].sort().join(" "),
  failedClass: [...els.failed.classes].sort().join(" "), shown: vm.runInContext("[S&&S.place.id, ver, tab]", tab)} : els.pane?.innerHTML ?? "";
const lines = readline.createInterface({input: process.stdin});
let done = Promise.resolve();                     // a step at a time, the last one done before node ends
lines.on("line", line => { done = done.then(async () => {
  const [step, arg] = line.trim().split(" ");
  await steps[step](arg);
  do { await new Promise(r => setTimeout(r, 20)); } while (inflight);   // a render's own requests start in its turn
  process.stdout.write(JSON.stringify(drawn(step)) + "\n");
}).catch(e => { process.stderr.write(String(e && e.stack || e) + "\n"); process.exitCode = 1; lines.close(); }); });
// node ends on its own once stdin closes: process.exit while its pipes close aborts node 24 on Windows
lines.on("close", () => done.then(() => { process.exitCode ??= 0; }));
"""


@pytest.mark.xfail(strict=True, reason="4b gap (pins-4b.md), an old page pin that cannot pass on the shell; the fix ports it, owner A (script.js) and L (webui.rs): no ticked-run comparison, no old receipt-missing line")
def test_a_receipt_is_the_run_clicked_whatever_landed_since(served, tmp_path_factory):
    """A receipt link names its run, found when it is clicked: an experiment's pane drawn before another run landed
    still opens the run it showed, never the one now in its place in the list; the runs ticked side by side are the
    runs ticked; and a run the page no longer holds says so instead of showing another."""
    rb, url, _ = served
    subprocess.run(["git", "add", "-A"], check=True)
    subprocess.run(["git", "commit", "-qm", "before the runs"], check=True)
    assert rb("run", "add", "e1", "top1=0.76")[0] == 0
    _, html = _get(url + "/")
    keys = re.findall(r'data-(?:rcpt|runsel)="([^"]*)"', html)
    assert keys and all(re.fullmatch(r"\$\{esc\(\w+\.run_id\)\}", k) for k in keys)   # a run's id, never its place
    node = shutil.which("node")
    if not node or subprocess.run([node, "-e", "fetch"], capture_output=True, timeout=30).returncode:
        pytest.skip("the tab's script runs in node 18 or later")
    here = tmp_path_factory.mktemp("tab")                 # outside the repository: its commits and checkouts leave it be
    (here / "page.js").write_text(html.split("<script>", 1)[1].split("</script>", 1)[0], encoding="utf-8")
    (here / "tab.js").write_text(TAB, encoding="utf-8")
    said = here / "tab.stderr"
    with said.open("wb") as stderr:
        tab = subprocess.Popen([node, str(here / "tab.js"), url, str(here / "page.js")],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=stderr, text=True, encoding="utf-8")
        panes = queue.Queue()

        def read():
            for line in tab.stdout:
                panes.put(line)
            panes.put(None)                                 # node has ended

        threading.Thread(target=read, daemon=True).start()

        def step(line):
            tab.stdin.write(line + "\n")
            tab.stdin.flush()
            try:
                pane = panes.get(timeout=30)
            except queue.Empty:
                pane = None
            assert pane is not None, said.read_text(encoding="utf-8")
            return json.loads(pane)

        try:
            step("load")
            before = step("experiment e1")                  # the experiment's pane, its one run linked
            [a] = re.findall(r'data-rcpt="([^"]*)"', before)
            assert rb("run", "add", "e1", "top1=0.81")[0] == 0
            new, old = json.loads(_get(url + "/state.json")[1])["home"]["runs"]
            assert (new["results"], old["run_id"]) == ("top1=0.81", a)     # the new run is first in the list now
            b = new["run_id"]
            assert step("refresh") == before                # the record moved; the open pane is as it was drawn
            receipt = step(f"receipt {a}")
            assert "top1=0.76" in receipt and "top1=0.81" not in receipt
            step(f"tick {b}")
            step(f"tick {a}")
            side = step("compare")
            assert "2 runs, side by side" in side and ">0.81<" in side and ">0.76<" in side
            both = step("experiment e1")
            assert re.findall(r'data-rcpt="([^"]*)"', both) == [b, a]
            subprocess.run(["git", "add", "-A"], check=True)
            subprocess.run(["git", "commit", "-qm", "the runs"], check=True)
            subprocess.run(["git", "checkout", "-q", "HEAD~1"], check=True)   # the record as it was before them
            assert step("refresh") == both
            gone = step(f"receipt {a}")
            assert "no longer among them" in gone and f"rb show {a}" in gone and "top1=" not in gone
        finally:
            tab.stdin.close()
            try:
                tab.wait(timeout=10)
            except subprocess.TimeoutExpired:
                tab.kill()
                tab.wait(timeout=5)


@pytest.mark.xfail(strict=True, reason="4b gap (pins-4b.md), an old page pin that cannot pass on the shell; the fix ports it, owner C (nodes.rs) and R (views.rs): an estimate-only standing is neutral, not muted")
def test_a_met_word_is_dim_wherever_a_rule_it_counts_is_met_only_on_its_estimate(served, tmp_path_factory):
    """A met rule is green only while nothing is wrong with it. Met on its estimate alone (one run, with no interval
    to read, or an interval that crosses the threshold), rb open serves the met word muted and the criterion's row not
    good, and the tab draws the word dim in the tally, the line saying nothing waits on you and the criterion's row,
    where it draws a sound one green."""
    rb, url, _ = served
    assert confirm_designs() == ["e1"]                       # the session set it up: the user confirms its design
    node = shutil.which("node")
    node = node if node and not subprocess.run([node, "-e", "fetch"], capture_output=True, timeout=30).returncode else None
    here = tmp_path_factory.mktemp("tab")
    (here / "page.js").write_text(_get(url + "/")[1].split("<script>", 1)[1].split("</script>", 1)[0], encoding="utf-8")
    (here / "tab.js").write_text(TAB, encoding="utf-8")
    homes, drawn = [], []
    # one run meets 0.7 with no interval; three read 0.78 [0.73, 0.83], clear of it; five 0.782 [0.64, 0.93], across it
    for runs in (["0.76"], ["0.78", "0.8"], ["0.62", "0.95"]):
        for top1 in runs:
            assert rb("run", "add", "e1", f"top1={top1}")[0] == 0
        homes.append(json.loads(_get(url + "/state.json")[1])["home"])
        if node:
            tab = subprocess.run([node, str(here / "tab.js"), url, str(here / "page.js")], input="load\nfindings\nview\n",
                                 capture_output=True, text=True, encoding="utf-8", timeout=60)
            assert tab.returncode == 0, tab.stderr
            drawn.append(json.loads(tab.stdout.splitlines()[-1]))
    rows = [[f["row"] for b in h["briefs"] for e in b["experiments"] for f in e["findings"]] for h in homes]
    assert [h["muted"] for h in homes] == [["meets"], [], ["meets"]]
    assert [[r["good"] for r in rs] for rs in rows] == [[False], [True], [False]]
    assert [rs[0]["standing"].split(" · ")[0] for rs in rows] == ["estimate meets criterion", "criterion met", "estimate meets criterion"]
    if not node:
        pytest.skip("rb open serves the met word muted; drawing it takes the tab's script, which runs in node 18 or later")
    for view, cls, label in zip(drawn, ("dim", "good", "dim"), ("estimate meets criterion", "criterion met", "estimate meets criterion")):
        assert f'<span class="{cls}">criterion met</span>' in view["tally"] and f'<span class="{cls}">{label}</span>' in view["cat"], view
        assert '<span class="good">estimate meets criterion</span>' not in view["cat"]
    assert [view["quiet"] for view in drawn] == ["dim", "", "dim"]


def test_a_receipt_drills_to_the_row_its_numbers_were_read_from(served, tmp_path_factory):
    """A run read from a row of a SQLite database names, wherever its receipt is shown, the database, the table, the row
    and the column of each value: rb open's card, the page and rb ui's card."""
    rb, url, _ = served
    con = sqlite3.connect("results.db")
    con.execute("create table evals (run_name text, seed integer, top1 real, ended text)")
    con.execute("insert into evals values ('r1', 7, 0.76, '2026-05-01 10:00:00')")
    con.commit()
    con.close()
    assert rb("spec", "set", "e1", "seed", "--per-run")[0] == 0
    code, env = rb("run", "add", "e1", "--variant", "new", "--from", "results.db", "--table", "evals", "--where", "run_name=r1",
                   "--set", "seed=@seed", "--produced-at", "@ended")
    assert code == 0, env.errors
    run = env.data["object"]["id"]
    [row] = json.loads(_get(url + "/state.json")[1])["home"]["runs"]
    assert {k: row["receipt"]["row"][k] for k in ("path", "table", "where", "rowid", "columns", "per_run", "produced_at")} == {
        "path": "results.db", "table": "evals", "where": {"run_name": "r1"}, "rowid": 1, "columns": {"new.top1": "top1"},
        "per_run": {"seed": "seed"}, "produced_at": "ended"}
    facts = object_card(run).facts
    assert "row: read from results.db, table evals, rowid 1 (run_name=r1)" in facts
    assert "columns: new.top1 from column top1; seed from column seed; produced at from column ended" in facts
    _, page = _get(url + "/page.html")
    # the page's receipt says the row in lines a reader follows, never as the stored dict
    receipt = page.split(f'id="run-{run}"', 1)[1].split('<div class="card', 1)[0]
    assert '<dt>row</dt><dd>read from <span class="mono">results.db</span>, table evals, rowid 1 (run_name=r1)</dd>' in receipt
    assert "<dt>row</dt><dd>columns: new.top1 from column top1; seed from column seed; produced at from column ended</dd>" in receipt
    assert "&#x27;table&#x27;" not in page and "{&#x27;" not in page


@pytest.mark.xfail(strict=True, reason="4b gap (pins-4b.md), an old page pin that cannot pass on the shell; the fix ports it, owner L (webui.rs, live.js): an invalid record at / answers JSON, not a page with its fix")
def test_a_hand_edited_field_says_its_fix_on_the_tab(served, tmp_path_factory):
    """A field rb does not know, added to a question by hand: rb open answered with the message alone, and the tab drew
    it with no fix, where every rb command offers rb doctor --restore. The answer carries the fix (`fix`), and the tab
    draws it under the message."""
    rb, url, _ = served
    q1 = Path(".rb/questions/q1.yaml")
    q1.write_text(q1.read_text(encoding="utf-8") + "future_field: something\n", encoding="utf-8")
    status, s = _answer(url + "/state.json")
    assert status == 404 and s["code"] == "E_STATE_NEWER" and s["error"].startswith(".rb/questions/q1.yaml uses a shape this rb does not know")
    assert s["fix"].startswith("If it was edited by hand, `rb doctor --restore` puts back what rb last wrote")
    node = shutil.which("node")
    if not node or subprocess.run([node, "-e", "fetch"], capture_output=True, timeout=30).returncode:
        pytest.skip("rb open serves the fix; drawing it takes the tab's script, which runs in node 18 or later")
    here = tmp_path_factory.mktemp("tab")
    (here / "page.js").write_text(_get(url + "/")[1].split("<script>", 1)[1].split("</script>", 1)[0], encoding="utf-8")
    (here / "tab.js").write_text(TAB, encoding="utf-8")
    tab = subprocess.run([node, str(here / "tab.js"), url, str(here / "page.js")], input="load\n", capture_output=True, text=True,
                         encoding="utf-8", timeout=60)
    assert tab.returncode == 0, tab.stderr
    assert json.loads(tab.stdout.splitlines()[-1])["failed"] == f"{s['error']}\n→ {s['fix']}"


@pytest.mark.xfail(strict=True, reason="4b gap (pins-4b.md), an old page pin that cannot pass on the shell; the fix ports it, owner R (views.rs): the scope line's time has no nowrap element")
def test_the_tab_says_what_rb_looks_at(served, tmp_path_factory):
    """rb open serves the scope line as rb status says it; the tab draws it under the line about calls, on the overview
    alone, with the time it last looked on one line."""
    rb, url, _ = served
    st = rb("status")[1].data
    home = json.loads(_get(url + "/state.json")[1])["home"]
    assert home["scope"].startswith("rb looks in out/, outputs/, results/, eval-out/ and metrics/, at the files in the repository's top folder") and home["scope"].endswith(st["scope"]["said"].split(" UTC. ", 1)[1])
    node = shutil.which("node")
    if not node or subprocess.run([node, "-e", "fetch"], capture_output=True, timeout=30).returncode:
        pytest.skip("rb open serves the line; drawing it takes the tab's script, which runs in node 18 or later")
    here = tmp_path_factory.mktemp("tab")
    (here / "page.js").write_text(_get(url + "/")[1].split("<script>", 1)[1].split("</script>", 1)[0], encoding="utf-8")
    (here / "tab.js").write_text(TAB, encoding="utf-8")
    tab = subprocess.run([node, str(here / "tab.js"), url, str(here / "page.js")], input="load\nview\nfindings\nview\n",
                         capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert tab.returncode == 0, tab.stderr
    overview, findings = [json.loads(line) for line in tab.stdout.splitlines() if '"looked"' in line]
    assert overview["looked"].startswith("rb looks in out/, outputs/, results/, eval-out/ and metrics/, at the files in the repository's top folder")
    assert re.search(r'<span class="nowrap">\d{4}-\d\d-\d\d \d\d:\d\d UTC</span>', overview["looked"])     # the time, on one line
    assert findings["looked"] == ""


@pytest.mark.xfail(strict=True, reason="4b gap (pins-4b.md), an old page pin that cannot pass on the shell; the fix ports it, owner M (model.rs) and R (views.rs): the experiment's comparison omits the primary rule's digits")
def test_the_tab_says_the_change_a_primary_criterion_reads_with_its_rule_s_digits(served, tmp_path_factory):
    """The experiment's table says a change to 3 significant digits, as rb compare does, which would put +0.05004 on a
    rule of at most 0.05 as +0.05. The change a primary criterion reads is served and drawn as its rule says it."""
    rb, url, _ = served
    assert rb("criterion", "add", "new costs at most 0.05", "-e", "e1", "--metric", "change.loss", "--at-most", "0.05", "--id", "c2")[0] == 0
    for old, new in (("1.001", "1.05104"), ("1.002", "1.05204"), ("1.003", "1.05304")):
        assert rb("run", "add", "e1", f"old.loss={old}", f"new.loss={new}")[0] == 0
    home = json.loads(_get(url + "/state.json")[1])["home"]
    [table] = [e["table"] for b in home["briefs"] for e in b["experiments"] if e.get("table")]
    [cell] = [r["metrics"]["loss"] for r in table["rows"] if r["variant"] == "new"]
    assert (cell["primary"], cell["said"]) == (True, "+0.05004")
    node = shutil.which("node")
    if not node or subprocess.run([node, "-e", "fetch"], capture_output=True, timeout=30).returncode:
        pytest.skip("rb open serves the change as its rule says it; drawing it takes the tab's script, which runs in node 18 or later")
    here = tmp_path_factory.mktemp("tab")
    (here / "page.js").write_text(_get(url + "/")[1].split("<script>", 1)[1].split("</script>", 1)[0], encoding="utf-8")
    (here / "tab.js").write_text(TAB, encoding="utf-8")
    tab = subprocess.run([node, str(here / "tab.js"), url, str(here / "page.js")], input="load\nexperiment e1\n",
                         capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert tab.returncode == 0, tab.stderr
    pane = json.loads(tab.stdout.splitlines()[-1])
    assert "+0.05004 vs old" in pane and "· primary" in pane and "+0.05 vs" not in pane


def test_cards_are_served_fresh_by_reference(served):
    rb, url, _ = served
    _, body = _get(url + "/card.json?ref=o:e1")
    c = json.loads(body)["card"]
    assert c["title"] == "the experiment"
    try:
        _get(url + "/card.json?ref=o:nope")
        assert False, "a missing object was served"
    except urllib.error.HTTPError as e:
        assert e.code == 404 and "error" in json.loads(e.read().decode("utf-8"))
    # a retracted experiment is off the catalogue: its card is not served either, said the same way
    assert rb("experiment", "add", "gone soon", "--id", "e9")[0] == 0
    assert rb("retract", "e9", "-m", "not this one")[0] == 0
    try:
        _get(url + "/card.json?ref=o:e9")
        assert False, "a retracted experiment was served"
    except urllib.error.HTTPError as e:
        assert e.code == 404 and json.loads(e.read().decode("utf-8")) == {"error": "nothing here is called 'e9'"}


def test_the_resources_drawer_says_where_each_resource_stands_now(served):
    """A resource is what it is, where its bytes are, and how to get them again; whether it is here is computed on
    every refresh, never stored, so deleting the bytes leaves the resource and changes only where it stands."""
    rb, url, _ = served
    Path("data").mkdir()
    Path("data/imagenet.bin").write_bytes(b"the benchmark's bytes")
    assert rb("resource", "add", "imagenet", "--type", "dataset", "--name", "ImageNet-1k", "--version", "validation",
              "--file", "data/imagenet.bin", "--url", "https://example.org/imagenet")[0] == 0
    assert rb("run", "add", "e1", "top1=0.76", "--input", "imagenet:eval")[0] == 0
    _, body = _get(url + "/state.json")
    home = json.loads(body)["home"]
    [row] = home["resources"]
    assert (row["resource_id"], row["name"], row["type"], row["availability"]) == ("imagenet", "ImageNet-1k validation", "dataset", "present")
    assert row["exact"] and row["identity"].startswith("sha256 ") and row["used_by"] == 1
    assert row["where"] == ["local:data/imagenet.bin · here"] and row["how"] == ""
    assert {r["id"]: (r["kind"], r["standing"]) for r in home["tree"]}["imagenet"] == ("resource", "present")
    Path("data/imagenet.bin").unlink()
    _, body = _get(url + "/state.json")
    [row] = json.loads(body)["home"]["resources"]
    assert row["availability"] == "reconstructible" and row["how"] == "https://example.org/imagenet"
    assert row["where"] == ["local:data/imagenet.bin · gone"] and row["used_by"] == 1
    # The shell row and card are pinned by
    # test_shell_resource_availability_is_recomputed_in_its_row_and_card.
    _, cbody = _get(url + "/card.json?ref=o:imagenet")
    card = json.loads(cbody)["card"]
    assert card["title"] == "ImageNet-1k validation" and card["lead"] == "dataset · reconstructible"
    assert "how to get it again: https://example.org/imagenet" in card["facts"] and len(card["runs"]) == 1


@pytest.mark.xfail(strict=True, reason="4b gap (pins-4b.md), an old page pin that cannot pass on the shell; the fix ports it, owner C (nodes.rs): the criterion card omits its declaration's words")
def test_the_tab_says_the_call_and_your_words(served, monkeypatch):
    """An agent's call is proposed, not made: the tab lists it in a quiet group of its own, with the words given with
    it, never as the user's, and the command that confirms it, and says no call under its question. Once the user
    confirms it, the tab says the call with no writer, and the words, now the user's, whole, right after the reason and
    with no tag. Words an agent gave with a criterion are said as given after the criterion's declaration, where the
    criterion lines draw them."""
    rb, url, _ = served
    monkeypatch.setenv("CLAUDECODE", "1")
    assert rb("criterion", "add", "score holds", "-e", "e1", "--metric", "score", "--at-least", "0.9", "--id", "c2",
              "--words", "score is what the team reads; hold it at 0.9")[0] == 0
    code, env = rb("decide", "e1", "accept", "-m", "top1 clears the bar", "--words", "if new holds on top1, call it")
    monkeypatch.delenv("CLAUDECODE")
    d = env.data["object"]
    assert code == 0 and (d["by"], d["words"]) == ("agent:claude-code", "if new holds on top1, call it")
    _, body = _get(url + "/state.json")
    home = json.loads(body)["home"]
    assert home["found"] == [] and "your_call" not in home and home["waiting"] == 0
    # the call, then the design the session wrote, which waits on its own
    assert [p for p in home["pending"] if p["kind"] != "design"] == [{"id": d["id"], "kind": "proposed", "confirm": f"rb confirm {d['id']}",
                                "text": 'accept e1 · note: top1 clears the bar · words (as given): "if new holds on top1, call it"'}]
    assert [p["text"] for p in home["pending"] if p["kind"] == "design"] == ["e1's design: the experiment"]
    assert home["briefs"][0]["ruled"] == []
    assert rb("confirm", d["id"])[0] == 0
    _, body = _get(url + "/state.json")
    home = json.loads(body)["home"]
    assert [p["kind"] for p in home["pending"]] == ["design"]
    b = home["briefs"][0]
    assert 'accepted: "the experiment" · note: top1 clears the bar · "if new holds on top1, call it"' in b["ruled"]
    assert "your words" not in body
    lines = {f["criterion_id"]: f for e in b["experiments"] for f in e["findings"]}
    assert lines["c2"]["your_words"] == 'words (as given): "score is what the team reads; hold it at 0.9"'
    timing = lines["c2"]["says"]["timing"]
    assert timing.startswith("declared") and "claude-code" not in timing
    assert lines["c1"]["your_words"] == ""                         # declared without them: nothing is added
    _, html = _get(url + "/")
    assert "f.your_words" in html                                  # drawn right after the declaration


def test_an_agent_session_is_refused_before_anything_binds(served, monkeypatch, capsys):
    rb, url, _ = served
    monkeypatch.setenv("CLAUDECODE", "1")
    code = main(["open", "--json"])
    env = Envelope.model_validate_json(capsys.readouterr().out)
    assert code != 0 and env.errors[0].code == "E_USAGE"
    assert "user's view" in env.errors[0].message


@pytest.mark.xfail(strict=True, reason="4b gap (pins-4b.md), an old page pin that cannot pass on the shell; the fix ports it, owner R (views.rs) and A (script.js): Experiments has no filter control")
def test_findings_keep_canonical_rows_after_a_hidden_filter_and_back_restores_overview(served, tmp_path_factory):
    rb, url, _ = served
    node = shutil.which("node")
    if not node or subprocess.run([node, "-e", "fetch"], capture_output=True, timeout=30).returncode:
        pytest.skip("the tab script runs in node 18 or later")
    here = tmp_path_factory.mktemp("tab-routing")
    (here / "page.js").write_text(_get(url + "/")[1].split("<script>", 1)[1].split("</script>", 1)[0], encoding="utf-8")
    (here / "tab.js").write_text(TAB, encoding="utf-8")
    tab = subprocess.run([node, str(here / "tab.js"), url, str(here / "page.js")],
                         input="load\nfilter\nfindings\nview\nback\nview\n", capture_output=True, text=True, timeout=60)
    assert tab.returncode == 0, tab.stderr
    frames = [json.loads(line) for line in tab.stdout.splitlines()]
    findings, overview = frames[3], frames[5]
    assert 'id="all-c-c1"' in findings["cat"] and "top1 holds" in findings["cat"]
    assert "Where the work stands" in overview["cat"] and "Evidence by inquiry" not in overview["cat"]
    assert "No interpretation has been recorded for these findings." not in overview["cat"]  # c1 has no question link


# ---------------------------------------------------------------- the place shown, and other places and versions


def _answer(url: str, headers: dict = None) -> tuple:
    """The status and the JSON rb open answers, error answers included. A past version is checked out on its first
    read, which a slow disk can take more than a minute over."""
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with _OPENER.open(req, timeout=180) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


def _files(top: Path, skip: tuple = ()) -> dict:
    """Every file under `top` (but the folders `skip` names there), with its size, its time and a hash of its bytes."""
    out = {}
    for path in sorted(top.rglob("*")):
        rel = path.relative_to(top).as_posix()
        if any(rel == s or rel.startswith(s + "/") for s in skip) or not path.is_file():
            continue
        st = path.stat()
        out[rel] = (st.st_size, st.st_mtime_ns, hashlib.sha256(path.read_bytes()).hexdigest())
    return out


@pytest.fixture
def placed(tmp_path, monkeypatch):
    """rb open in the main checkout of a clone with a place of every kind (tests/rbtest.py: places_record)."""
    as_the_user(monkeypatch)
    rec = places_record(tmp_path / "clone")
    for k in ("GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM", "RB_TELEMETRY", "DO_NOT_TRACK", "RB_ACTOR"):
        monkeypatch.setenv(k, rec.env[k])
    server = Open(rec.root)
    yield rec, server
    server.close()


def test_a_single_checkout_shows_its_own_place_and_says_it_as_before(served):
    """In a single checkout, /state.json says which place it shows (place: this one, live, with the record's committed
    versions) and lists it (places), and everything else it said before stays: each run gains only where it was
    recorded (null here) and each finding its note on that (empty). The digest is the record's own, as before, and the
    same when the place is chosen by its id."""
    rb, url, _ = served
    status, s = _answer(url + "/state.json")
    assert status == 200 and set(s) == {"home", "digest", "rb_version", "at", "place", "places"}
    p = s["place"]
    assert (p["record"], p["readable"], p["version"], p["versions"], p["gone"]) == ("this", True, None, [], False)
    assert [x["id"] for x in s["places"]] == [p["id"]] and p["path"] == Path.cwd().resolve().as_posix() and p["label"].endswith(" · " + p["path"])
    assert rb("run", "add", "e1", "top1=0.76")[0] == 0
    status, s = _answer(url + "/state.json")
    [row] = s["home"]["runs"]
    assert row["observed_in"] is None and set(row) == {"when", "experiment", "results", "by", "note", "experiment_id", "run_id", "metrics",
                                                         "per_run", "session", "receipt", "produced", "facts", "observed_in"}
    findings = [f for b in s["home"]["briefs"] for e in b["experiments"] for f in e["findings"]]
    assert findings and all(f["observed"] == "" for f in findings)
    _, card = _answer(url + "/card.json?ref=o:e1")
    assert card["digest"] == s["digest"] == _answer(url + "/state.json?place=" + p["id"])[1]["digest"]


def test_rb_open_names_each_place_and_says_where_a_run_was_recorded(placed):
    rec, server = placed
    status, s = _answer(server.url + "/state.json")
    assert status == 200
    p = {x["branch"]: x for x in s["places"]}
    assert s["place"]["id"] == p["main"]["id"] and s["place"]["label"] == p["main"]["label"] == f"main · {rec.root.resolve().as_posix()}"
    assert {b: x["record"] for b, x in p.items()} == {"main": "this", "exp-b": "shared", "exp-c": "own", "exp-s": "separate", "exp-n": "none", "exp-g": "none"}
    assert [v["sha"] for v in s["place"]["versions"]] == [rec.git("rev-parse", "HEAD")]
    runs = {r["run_id"]: r for r in s["home"]["runs"]}
    head = rec.git("rev-parse", "HEAD", cwd=rec.tree["exp-b"])
    assert runs[rec.run_nested]["observed_in"] == {
        "label": f"recorded in exp-b · {p['exp-b']['path']} at {head[:12]}, with uncommitted changes", "commit": head, "dirty": True, "gone": False,
        "label_short": f"recorded in exp-b · .worktrees/b at {head[:12]}, with uncommitted changes", "place": None}
    assert runs[rec.run_main]["observed_in"] is None
    [c1] = [f for b in s["home"]["briefs"] for e in b["experiments"] for f in e["findings"] if f["criterion_id"] == "c1"]
    assert c1["observed"] == f"1 of 2 runs was recorded in exp-b · {p['exp-b']['path']}"
    # its card says so too, and the run's
    _, card = _answer(f"{server.url}/card.json?ref=o:{rec.run_nested}")
    assert card["card"]["facts"][-1] == runs[rec.run_nested]["observed_in"]["label_short"]
    _, card = _answer(server.url + "/card.json?ref=o:c1")
    assert c1["observed_short"] in card["card"]["facts"]


@pytest.fixture
def at_home_placed(tmp_path, monkeypatch):
    """The clone of `placed` with the home folder above it, a worktree outside the home folder (exp-far), and two
    detached worktrees at one commit whose paths in short could be taken for each other: ~/wt from the home folder, and
    a folder named ~ in the main checkout, which holds wt too."""
    as_the_user(monkeypatch)
    rec = places_record(tmp_path / "home" / "clone")
    for k in ("GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM", "RB_TELEMETRY", "DO_NOT_TRACK", "RB_ACTOR"):
        monkeypatch.setenv(k, rec.env[k])
    rec.far = tmp_path / "far" / "wt"
    rec.git("worktree", "add", "-q", "-b", "exp-far", str(rec.far), rec.start)
    rec.twins = (tmp_path / "home" / "wt", rec.root / "~" / "wt")
    for t in rec.twins:
        rec.git("worktree", "add", "-q", "--detach", str(t), rec.start)
    at_home(monkeypatch, tmp_path / "home")
    server = Open(rec.root)
    yield rec, server
    server.close()


def test_rb_open_names_each_place_in_short_and_keeps_the_whole_path(at_home_placed):
    """Each place, as rb open names it, is its branch, then its path from the main checkout when it is inside it, from ~
    under the home folder, else as git lists it (`label_short`, beside `label`, which keeps the whole path); a folder
    named ~ in the main checkout is said from ./, never as the home folder; where a run was recorded, the note on a
    finding and a past version are said in short too, and a command keeps the whole path."""
    rec, server = at_home_placed
    _, s = _answer(server.url + "/state.json")
    p = {x["branch"] or x["path"]: x for x in s["places"]}
    twins = [x for x in s["places"] if x["branch"] is None]
    assert p["main"]["label_short"] == "main · ~/clone/p" and p["main"]["label"] == f"main · {p['main']['path']}"
    assert p["exp-b"]["label_short"] == "exp-b · .worktrees/b" and p["exp-c"]["label_short"] == "exp-c · ~/clone/wt-c"
    assert p["exp-far"]["label_short"] == f"exp-far · {p['exp-far']['path']}" and p["exp-far"]["path"].endswith("/far/wt")
    detached = f"detached at {rec.start[:12]}"
    assert sorted(t["label_short"] for t in twins) == [f"{detached} · ./~/wt", f"{detached} · ~/wt"]
    assert p["exp-b"]["why_not"] == "exp-b · .worktrees/b has no record of its own to show: it writes to this record."
    assert p["exp-c"]["why_not"] == "" and p["exp-g"]["why_not"] == "exp-g · ~/clone/wt-g is removed since: there is nothing there to show."
    runs = {r["run_id"]: r for r in s["home"]["runs"]}
    o = runs[rec.run_nested]["observed_in"]
    assert o["label_short"] == "recorded in exp-b · .worktrees/b" + o["label"].split(p["exp-b"]["path"], 1)[1] and o["label"].startswith(f"recorded in exp-b · {p['exp-b']['path']} at ")
    [c1] = [f for b in s["home"]["briefs"] for e in b["experiments"] for f in e["findings"] if f["criterion_id"] == "c1"]
    assert (c1["observed"], c1["observed_short"]) == (f"1 of 2 runs was recorded in exp-b · {p['exp-b']['path']}", "1 of 2 runs was recorded in exp-b · .worktrees/b")
    _, card = _answer(f"{server.url}/card.json?ref=o:c1")
    assert c1["observed_short"] in card["card"]["facts"] and c1["observed"] not in card["card"]["facts"]
    [v] = s["place"]["versions"]
    assert v["shown_short"] == f"main · ~/clone/p as of {v['sha'][:12]} · {v['label'].split(' · ', 1)[1]} · read only"
    _, c = _answer(f"{server.url}/state.json?place={p['exp-c']['id']}")
    rec_c = rec.rb("decide", "e1", "accept", "--why", "it holds there", cwd=rec.tree["exp-c"], who={"CLAUDECODE": "1", "CLAUDE_CODE_SESSION_ID": "sess-short-1"})
    _, c = _answer(f"{server.url}/state.json?place={p['exp-c']['id']}")
    assert [x["confirm"] for x in c["home"]["pending"]] == [f"cd {p['exp-c']['record_dir']} && rb confirm {rec_c['object']['id']}"]


def test_a_place_taken_away_since_it_was_listed_is_said_gone_in_short(at_home_placed):
    """A worktree removed while rb open still lists it (its list is a few seconds old) is said gone in rb's words, by
    its label in short, for its records and its cards alike; so is a place listed that cannot be shown."""
    rec, server = at_home_placed
    _, s = _answer(server.url + "/state.json")
    p = {x["branch"]: x for x in s["places"]}
    c = p["exp-c"]
    assert c["readable"] is True and c["label_short"] == "exp-c · ~/clone/wt-c"
    shutil.rmtree(rec.tree["exp-c"])
    gone = f"exp-c · ~/clone/wt-c is removed since: there is nothing there to show."
    assert _answer(f"{server.url}/state.json?place={c['id']}") == (404, {"error": gone})
    assert _answer(f"{server.url}/card.json?ref=o:c1&place={c['id']}") == (404, {"error": gone})
    assert _answer(f"{server.url}/state.json?place={p['exp-b']['id']}") == (404, {"error": p["exp-b"]["why_not"]})


def test_a_worktree_made_since_the_places_were_listed_is_not_said_to_be_removed(placed):
    """A run recorded in a worktree made after rb open last listed the places says where, and never that the
    worktree is removed since: only one git lists as gone, or with no checkout there, is."""
    rec, server = placed
    assert _answer(server.url + "/state.json")[0] == 200            # the places are listed now
    e = rec.worktree("exp-e", rec.root / ".worktrees" / "e", rec.start)
    run_e = rec.rb("run", "add", "e1", "fp32.top1=0.763", "int8.top1=0.757", cwd=e)["object"]["id"]
    _, s = _answer(server.url + "/state.json")
    [row] = [r for r in s["home"]["runs"] if r["run_id"] == run_e]
    assert row["observed_in"]["gone"] is False and row["observed_in"]["label"].startswith(f"recorded in exp-e · {e.resolve().as_posix()} at ")
    assert "removed since" not in row["observed_in"]["label"]


def test_a_listed_place_shows_its_record_and_nothing_else_is_shown(placed):
    rec, server = placed
    _, s = _answer(server.url + "/state.json")
    p = {x["branch"]: x for x in s["places"]}
    status, c = _answer(f"{server.url}/state.json?place={p['exp-c']['id']}")
    assert status == 200 and c["place"]["id"] == p["exp-c"]["id"] and c["place"]["record"] == "own"
    assert rec.run_sibling in {r["run_id"] for r in c["home"]["runs"]} and rec.run_sibling not in {r["run_id"] for r in s["home"]["runs"]}
    assert _answer(f"{server.url}/card.json?ref=o:{rec.run_sibling}&place={p['exp-c']['id']}")[0] == 200
    assert _answer(f"{server.url}/card.json?ref=o:{rec.run_sibling}")[0] == 404
    status, sep = _answer(f"{server.url}/state.json?place={p['exp-s']['id']}")
    assert status == 200 and sep["home"]["title"] == "another study"
    status, html = _get(f"{server.url}/page.html?place={p['exp-c']['id']}")
    assert status == 200 and html.startswith("<!doctype html>")
    # only what the server listed, and what it can show
    for ask, said in ((f"place=p_0000000000", "p_0000000000 is not a place rb listed here."),
                      (f"place={urllib.request.quote(str(rec.tree['exp-c']))}", "is not a place rb listed here."),
                      (f"place={p['exp-b']['id']}", f"{p['exp-b']['label_short']} has no record of its own to show: it writes to this record."),
                      (f"place={p['exp-g']['id']}", f"{p['exp-g']['label_short']} is removed since: there is nothing there to show."),
                      (f"place={p['exp-n']['id']}", f"{p['exp-n']['label_short']} has no record to show."),
                      ("at=" + "0" * 40, "is not a version rb listed for main · ")):
        for route in ("/state.json?", "/card.json?ref=o:e1&", "/page.html?"):
            status, answer = _answer(server.url + route + ask)
            assert status == 404 and said in answer["error"], (route, ask, answer)
    # a choice made from another site's page is refused before anything is read
    chosen = f"place={p['exp-c']['id']}"
    for site in ("cross-site", "same-site", "none"):
        for route in ("/state.json?", "/card.json?ref=o:e1&", "/page.html?"):
            status, answer = _answer(server.url + route + chosen, {"Sec-Fetch-Site": site})
            assert status == 403 and answer == {"error": "A place or a version is chosen on rb open's own page."}, (site, route)
    assert _answer(f"{server.url}/state.json?{chosen}", {"Sec-Fetch-Site": "same-origin"})[0] == 200
    assert _answer(server.url + "/state.json", {"Sec-Fetch-Site": "cross-site"})[0] == 200        # the record shown first, as before


def test_a_place_is_shown_as_it_is_now_not_as_it_was_listed(placed):
    """A place whose record went, or which was removed, since rb open listed the places is said as it is now: never
    shown with the record above it, under its name."""
    rec, server = placed
    x = rec.worktree("exp-x", rec.root / ".worktrees" / "x", "main")         # its own record, as main committed it
    y = rec.worktree("exp-y", rec.base / "wt-y", "main")
    _, s = _answer(server.url + "/state.json")                                # the places are listed now
    p = {b["branch"]: b for b in s["places"]}
    assert (p["exp-x"]["record"], p["exp-y"]["record"]) == ("own", "own")
    (x / ".rb").rename(x / ".rb-away")                                        # as a checkout of a branch with none
    shutil.rmtree(y)
    status, answer = _answer(f"{server.url}/state.json?place={p['exp-x']['id']}")
    assert status == 404 and answer["error"] == f"{p['exp-x']['label_short']} has no record of its own to show: it writes to this record.", answer
    status, answer = _answer(f"{server.url}/state.json?place={p['exp-y']['id']}")
    assert status == 404 and answer["error"] == f"{p['exp-y']['label_short']} is removed since: there is nothing there to show.", answer


def test_the_manual_and_the_changelog_say_what_rb_open_refuses_and_a_page_carries():
    """AGENTS.md says a choice typed as an address is refused as one from another site is (above: Sec-Fetch-Site none
    gets 403), and the changelog promises of a page only what rb page and rb export keep: none of the place labels."""
    from rbtest import ROOT
    doc, log = (ROOT / "AGENTS.md").read_text(encoding="utf-8"), (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "a choice not made on the page itself (sent from another site, or typed as an address) 403" in doc
    assert "`rb page` and `rb export` carry none of these labels" in log and "a page you send names no place" not in log


def test_another_place_is_read_and_nothing_is_written_there(placed):
    """Ten reads of another place's record, its cards and its page change nothing there, in a worktree rb has never
    written in (exp-d, with the record as main committed it) as in one it has (exp-c): no file, no time of a file, no
    lock or cache in its .rb/, no journal of its own, and nothing in the clone's journals or the mark of what the user
    has seen."""
    rec, server = placed
    rec.worktree("exp-d", rec.base / "wt-d", "main")                # before the server first lists the places
    _, s = _answer(server.url + "/state.json")                      # the record shown first is watched, as before
    common = Path(rec.git("rev-parse", "--path-format=absolute", "--git-common-dir"))
    for branch in ("exp-d", "exp-c"):
        p = next(x for x in s["places"] if x["branch"] == branch)
        there, rb_dir = _files(rec.tree[branch]), _files(common / "rb")
        seen = (common / "rb-last-seen.json").read_bytes() if (common / "rb-last-seen.json").exists() else None
        for _ in range(10):
            assert _answer(f"{server.url}/state.json?place={p['id']}")[0] == 200
            assert _answer(f"{server.url}/card.json?ref=o:e1&place={p['id']}")[0] == 200
            assert _get(f"{server.url}/page.html?place={p['id']}")[0] == 200
        assert _files(rec.tree[branch]) == there and _files(common / "rb") == rb_dir, branch
        assert ((common / "rb-last-seen.json").read_bytes() if (common / "rb-last-seen.json").exists() else None) == seen
    assert not (rec.tree["exp-d"] / ".rb" / ".lock").exists() and not (rec.tree["exp-d"] / ".rb" / "cache").exists()


def test_two_places_that_hold_the_same_record_read_apart(placed):
    """A worktree that holds the same record as the main checkout reads the same, under a digest of its own, so the
    page redraws on a switch between them; and back, the record shown first has its own digest again. There, the run
    recorded in the main checkout says so, by the branch and commit its receipt keeps."""
    rec, server = placed
    rec.worktree("exp-d", rec.base / "wt-d", "main")                # before the server first lists the places
    _, s = _answer(server.url + "/state.json")
    d = next(x for x in s["places"] if x["branch"] == "exp-d")
    _, there = _answer(f"{server.url}/state.json?place={d['id']}")
    def same(v):            # but where each run was recorded and what code it ran, which are said against the place shown
        if isinstance(v, dict):
            return {k: same(x) for k, x in v.items() if k not in ("live", "found", "unplaced", "observed_in", "observed", "observed_short", "other_code", "scope", "scope_short")}
        return [same(x) for x in v] if isinstance(v, list) else v
    assert same(there["home"]) == same(s["home"]) and there["digest"] != s["digest"]
    main_at = rec.start                                              # where run_main was recorded, before the record's commit
    [row] = [r for r in there["home"]["runs"] if r["run_id"] == rec.run_main]
    assert row["observed_in"] == {"label": f"recorded in main at {main_at[:12]}", "commit": main_at, "dirty": False, "gone": False,
                                  "label_short": f"recorded in main at {main_at[:12]}", "place": None}
    assert _answer(server.url + "/state.json")[1]["digest"] == s["digest"]


def test_a_linked_worktree_s_record_says_which_runs_were_recorded_in_the_main_checkout(placed):
    """At a linked worktree's own record, a run recorded in the main checkout (its receipt keeps no worktree, as no
    write from the main checkout does) says where, by the branch and commit it keeps, and the finding that counts it
    counts it with the others recorded elsewhere; the run recorded there says nothing."""
    rec, server = placed
    _, s = _answer(server.url + "/state.json")
    p = {x["branch"]: x for x in s["places"]}
    _, c = _answer(f"{server.url}/state.json?place={p['exp-c']['id']}")
    runs = {r["run_id"]: r for r in c["home"]["runs"]}
    main_at = rec.start
    assert runs[rec.run_main]["observed_in"]["label"] == f"recorded in main at {main_at[:12]}"
    assert runs[rec.run_nested]["observed_in"]["label"].startswith(f"recorded in exp-b · {p['exp-b']['path']} at ")
    assert runs[rec.run_sibling]["observed_in"] is None
    [c1] = [f for b in c["home"]["briefs"] for e in b["experiments"] for f in e["findings"] if f["criterion_id"] == "c1"]
    assert c1["observed"].startswith("2 of 3 runs were recorded elsewhere: ")
    assert "1 in main" in c1["observed"] and f"1 in exp-b · {p['exp-b']['path']}" in c1["observed"]
    _, card = _answer(f"{server.url}/card.json?ref=o:{rec.run_main}&place={p['exp-c']['id']}")
    assert card["card"]["facts"][-1] == f"recorded in main at {main_at[:12]}"
    # said by its branch alone, it links to no place, though main is listed with a record and is on that branch; the
    # manual says so where it says what links
    from rbtest import ROOT
    assert runs[rec.run_main]["observed_in"]["place"] is None and p["main"]["readable"] is True and p["main"]["branch"] == "main"
    assert "and so does a run said by its branch alone: its receipt names no worktree" in (ROOT / "AGENTS.md").read_text(encoding="utf-8")


def test_recorded_in_a_worktree_on_another_branch_now_stays_plain_words(placed):
    """Where the worktree a run was recorded in has moved to another branch since, it shows other work: the run and the
    finding's note still name the branch the run was recorded on, and link to no place."""
    rec, server = placed
    c_tree = rec.tree["exp-c"]
    rec.git("add", ".rb", cwd=c_tree)
    rec.git("commit", "-qm", "the sibling's run", cwd=c_tree)
    rec.git("merge", "-q", "--ff-only", "exp-c")
    rec.git("checkout", "-q", "-b", "exp-other", cwd=c_tree)
    _, s = _answer(server.url + "/state.json")
    p = {x["branch"]: x for x in s["places"]}
    assert p["exp-other"]["readable"] is True and "exp-c" not in p
    o = {r["run_id"]: r for r in s["home"]["runs"]}[rec.run_sibling]["observed_in"]
    assert o["label_short"].startswith("recorded in exp-c · ") and o["place"] is None
    [c1] = [f for b in s["home"]["briefs"] for e in b["experiments"] for f in e["findings"] if f["criterion_id"] == "c1"]
    assert c1["observed_places"] == [] and "1 in exp-c · " in c1["observed_short"]


def test_a_file_to_run_again_from_a_worktree_with_its_own_record_says_where_it_is(placed):
    """A run recorded in a worktree with a record of its own names its files from there: merged here, a file it takes
    to run the experiment again that is not here is said with that worktree beside it, in short on rb open, as another
    worktree on a page, and as recorded in rb's text."""
    rec, server = placed
    c = rec.tree["exp-c"]
    (c / "out").mkdir()
    (c / "out" / "metrics.json").write_text(json.dumps({"fp32": {"top1": 0.763}, "int8": {"top1": 0.760}}), encoding="utf-8")
    rec.rb("run", "add", "e1", "--from", "out/metrics.json", cwd=c)
    rec.git("add", ".rb", cwd=c)
    rec.git("commit", "-qm", "a run read from its file", cwd=c)
    rec.git("merge", "-q", "--ff-only", "exp-c")
    _, s = _answer(server.url + "/state.json")
    p = {x["branch"]: x for x in s["places"]}
    [eb] = [e for b in s["home"]["briefs"] for e in b["experiments"] if e["id"] == "e1"]
    [files] = [r for r in eb["rerun"] if r["what"] == "files"]
    assert files["detail"].endswith(f"not here: out/metrics.json (in {p['exp-c']['label_short']})"), files
    page = _get(server.url + "/page.html")[1]
    assert "not here: out/metrics.json (in another worktree)" in page and str(c) not in page and "exp-c" not in page
    said = "\n".join(r["detail"] for r in rec.rb("show", "e1")["rerunnability"] if r["what"] == "files")
    assert said.endswith("not here: out/metrics.json"), said


def test_elsewhere_leaves_out_a_call_or_gate_merged_here(placed):
    """A call and a gate a session made in another place, merged here, wait here: Elsewhere does not list them as
    waiting there too, with a second command, and once they are confirmed here it still does not."""
    from rbtest import Open
    rec, server = placed
    c = rec.tree["exp-c"]
    agent = {"CLAUDECODE": "1", "CLAUDE_CODE_SESSION_ID": "sess-merged-3"}
    proposed = rec.rb("decide", "e1", "accept", "--why", "the sibling's runs hold", cwd=c, who=agent)["object"]["id"]
    rec.rb("experiment", "gate", "e1", "--if-holds", "ship int8", "--if-not", "keep fp32", cwd=c, who=agent)
    there = rec.rb("status", cwd=c)
    assert [d["id"] for d in there["pending"]] == [proposed] and len(there["gates_pending"]) == 1
    rec.git("add", ".rb", cwd=c)
    rec.git("commit", "-qm", "the call and the gate", cwd=c)
    rec.git("merge", "-q", "--no-edit", "exp-c")
    here = rec.rb("status")
    assert [d["id"] for d in here["pending"]] == [proposed] and here["gates_pending"] == there["gates_pending"]
    _, s = _answer(server.url + "/state.json")
    [row] = [r for r in s["elsewhere"] if r["branch"] == "exp-c"]
    assert row["waiting"] == []
    assert re.fullmatch(r"rb confirm e1@[0-9a-f]{64}", here["gates_pending"][0]["confirm"])
    rec.rb("confirm", proposed, here["gates_pending"][0]["confirm"].split(" ")[-1])
    assert rec.rb("status")["pending"] == []
    again = Open(rec.root)                          # the list as worked out now, not ten seconds ago
    try:
        _, s = _answer(again.url + "/state.json")
    finally:
        again.close()
    [row] = [r for r in s["elsewhere"] if r["branch"] == "exp-c"]
    assert row["waiting"] == []


@pytest.mark.xfail(strict=True, reason="4b gap (pins-4b.md), an old page pin that cannot pass on the shell; the fix ports it, owner C (nodes.rs) and R (views.rs): the resource card and row lose the whole recorded path")
def test_a_resource_s_file_in_a_worktree_keeps_its_whole_path_on_hover(placed, tmp_path_factory):
    """A resource's local file inside a nested worktree is named on rb open by its path inside it, with the worktree in
    short, and its whole path as recorded is on hover; a file of the main checkout reads as it did."""
    rec, server = placed
    node = shutil.which("node")
    if not node or subprocess.run([node, "-e", "fetch"], capture_output=True, timeout=30).returncode:
        pytest.skip("the tab's script runs in node 18 or later")
    b = rec.tree["exp-b"]
    (b / "out").mkdir()
    (b / "out" / "config.yaml").write_text("lr: 0.25\n", encoding="utf-8")
    rec.rb("resource", "add", "int8-ckpt", "--name", "int8 checkpoint", "--type", "checkpoint", "--file", "out/config.yaml", cwd=b)
    (rec.root / "labels.txt").write_text("tench\n", encoding="utf-8")
    rec.rb("resource", "add", "labels", "--name", "class labels", "--type", "dataset", "--file", "labels.txt")
    here = tmp_path_factory.mktemp("tab-resources")
    (here / "page.js").write_text(_get(server.url + "/")[1].split("<script>", 1)[1].split("</script>", 1)[0], encoding="utf-8")
    (here / "tab.js").write_text(TAB, encoding="utf-8")
    tab = subprocess.run([node, str(here / "tab.js"), server.url, str(here / "page.js")], input="load\nresources\n",
                         capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert tab.returncode == 0, tab.stderr
    _, drawn = [json.loads(line) for line in tab.stdout.splitlines()]
    cat = html_unescape(drawn["cat"])
    assert '<div class="mono" title="local:.worktrees/b/out/config.yaml · here">local:out/config.yaml (in exp-b · .worktrees/b) · here</div>' in cat
    assert '<div class="mono">local:labels.txt · here</div>' in cat


def html_unescape(text: str) -> str:
    import html
    return html.unescape(text)


@pytest.fixture
def versioned(workdir, monkeypatch, tmp_path_factory):
    """A record with four committed versions (v1 to v4, each one more run) and a note not committed since."""
    as_the_user(monkeypatch)
    monkeypatch.setenv("RB_ACTOR", "human:ana")
    cfg = tmp_path_factory.mktemp("gitconfig") / "config"
    cfg.write_text("[user]\n\temail = ana@example.org\n\tname = ana\n[init]\n\tdefaultBranch = main\n", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(cfg))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    git = lambda *a: subprocess.run(["git", *a], check=True, capture_output=True, text=True).stdout.strip()
    git("init", "-q")
    Path("train.py").write_text("lr = 0.1\n", encoding="utf-8")
    git("add", "train.py")
    git("commit", "-qm", "start")
    assert main(["init", "INT8 ResNet-50 on ImageNet", "--no-agents"]) == 0
    assert main(["experiment", "add", "int8 against fp32", "--id", "e1", "--baseline", "fp32", "--candidate", "int8"]) == 0
    assert main(["criterion", "add", "int8 holds top1", "-e", "e1", "--metric", "change.top1", "--at-least", "-0.01", "--id", "c1"]) == 0
    assert main(["spec", "set", "e1", "seed", "--per-run"]) == 0
    shas = []
    for i in range(4):
        assert main(["run", "add", "e1", "fp32.top1=0.761", f"int8.top1=0.75{i}", "--set", f"seed={i}"]) == 0
        git("add", ".rb")
        git("commit", "-qm", f"v{i + 1}")
        shas.append(git("rev-parse", "HEAD"))
    assert main(["note", "add", "a note not committed"]) == 0
    return git, shas


def test_a_past_version_reads_as_a_checkout_of_its_commit_and_leaves_nothing(versioned, tmp_path_factory):
    git, shas = versioned
    at_v1 = tmp_path_factory.mktemp("at") / "v1"
    git("worktree", "add", "-q", "--detach", str(at_v1), shas[0])    # a live checkout at v1, to read it against
    common = Path(git("rev-parse", "--path-format=absolute", "--git-common-dir"))
    server = Open()
    try:
        _, s = _answer(server.url + "/state.json")
        assert [v["sha"] for v in s["place"]["versions"]] == shas[::-1] and len(s["home"]["runs"]) == 4
        before = _files(common, skip=("rb",))
        status, v1 = _answer(server.url + "/state.json?at=" + shas[0])
        assert status == 200 and v1["place"]["version"]["sha"] == shas[0] and len(v1["home"]["runs"]) == 1
        assert v1["place"]["version"]["shown"] == f"main · {Path.cwd().resolve().as_posix()} at {shas[0][:12]}, {v1['place']['version']['label'][15:]}: a past version, read only"
        assert v1["digest"] != s["digest"] and v1["place"]["id"] == s["place"]["id"]
        live = Open(at_v1)
        try:
            _, there = _answer(live.url + "/state.json")
        finally:
            live.close()
        def same(v):        # but its live panel, and where runs were recorded, said against the place each shows
            if isinstance(v, dict):
                return {k: same(x) for k, x in v.items() if k not in ("live", "observed_in", "observed", "observed_short", "scope", "scope_short")}
            return [same(x) for x in v] if isinstance(v, list) else v
        assert same(v1["home"]) == same(there["home"])
        # a card and the page of the version, and what was not listed
        assert _answer(f"{server.url}/card.json?ref=o:e1&at={shas[0]}")[0] == 200
        assert _get(f"{server.url}/page.html?at={shas[0]}")[0] == 200
        start = git("rev-parse", "HEAD~4")
        for sha in ("0" * 40, start, shas[0][:12]):
            status, answer = _answer(f"{server.url}/state.json?at={sha}")
            assert status == 404 and "is not a version rb listed" in answer["error"], (sha, answer)
        # at most three at a time, in rb's own folder of the clone, and the repository as it was outside it
        for sha in shas:
            assert _answer(f"{server.url}/state.json?at={sha}")[0] == 200
        [folder] = [d for d in (common / "rb" / "views").iterdir()]
        assert sorted(d.name for d in folder.iterdir() if not d.name.startswith(".")) == sorted(sha[:12] for sha in shas[1:])
        assert _files(common, skip=("rb",)) == before
    finally:
        server.close()
    assert not (common / "rb" / "views").exists()                     # taken away when rb open stops


@pytest.mark.skipif(sys.platform == "win32", reason="the hooks here are shell scripts")
def test_a_past_version_is_checked_out_with_none_of_the_user_s_hooks(versioned, tmp_path_factory):
    """The user's hooks (core.hooksPath in their git config) never run while rb checks a past version out: none writes
    in their checkout, where rb open runs, and none runs in rb's own."""
    git, shas = versioned
    hooks = tmp_path_factory.mktemp("hooks")
    ran = hooks / "ran.log"
    for name in ("reference-transaction", "post-checkout", "post-index-change"):
        (hooks / name).write_text(f'#!/bin/sh\necho "{name} $PWD" >> "{ran}"\necho hook > ./hook-wrote-here.txt\n', encoding="utf-8")
        (hooks / name).chmod(0o755)
    cfg = Path(os.environ["GIT_CONFIG_GLOBAL"])
    cfg.write_text(cfg.read_text(encoding="utf-8") + f"[core]\n\thooksPath = {hooks.as_posix()}\n", encoding="utf-8")
    before = git("status", "--porcelain")
    ran.unlink(missing_ok=True)                                       # what the user's own git status ran
    Path("hook-wrote-here.txt").unlink(missing_ok=True)
    server = Open()
    try:
        assert _answer(server.url + "/state.json?at=" + shas[0])[0] == 200
    finally:
        server.close()
    assert not ran.exists() and not Path("hook-wrote-here.txt").exists() and git("status", "--porcelain") == before


@pytest.mark.skipif(sys.platform == "win32", reason="SIGTERM and SIGHUP are POSIX signals")
@pytest.mark.parametrize("name", ["SIGTERM", "SIGHUP"])
def test_a_request_to_end_or_a_closed_terminal_takes_the_past_versions_away(versioned, name):
    """rb open ended by a request to end (SIGTERM) or by the terminal it ran in closing (SIGHUP), as by Ctrl-C, takes
    away the past versions it checked out, then ends as that signal ends a program."""
    import signal
    git, shas = versioned
    views = Path(git("rev-parse", "--path-format=absolute", "--git-common-dir")) / "rb" / "views"
    number = getattr(signal, name)
    was = signal.signal(number, signal.SIG_DFL)         # a signal rb is started to ignore (under nohup) stays ignored
    try:
        server = Open()
    finally:
        signal.signal(number, was)
    try:
        assert _answer(server.url + "/state.json?at=" + shas[0])[0] == 200 and any(views.iterdir())
        server.proc.send_signal(number)
        assert server.proc.wait(timeout=30) == -number
    finally:
        server.close()
    assert not views.exists()


def test_a_version_too_large_to_check_out_is_listed_with_why(versioned, monkeypatch):
    git, shas = versioned
    monkeypatch.setenv("RB_TEST_VIEW_BYTES", "4")                     # past versions of more than 4 bytes are only listed
    server = Open()
    try:
        status, answer = _answer(server.url + "/state.json?at=" + shas[0])
        assert status == 404 and answer["error"].startswith(f"{shas[0][:12]} tracks ") and answer["error"].endswith(
            "of files, more than rb checks out to read a past version (4 bytes).")
        _, s = _answer(server.url + "/state.json")
        notes = {v["sha"]: v["note"] for v in s["place"]["versions"]}
        assert notes[shas[0]] == answer["error"] and notes[shas[1]] == ""
    finally:
        server.close()


@pytest.mark.skipif(sys.platform == "win32", reason="the lock a test holds is a POSIX lock")
def test_a_views_folder_no_screen_holds_is_taken_away_at_start(versioned):
    """A server or a screen stopped with no chance to take its past versions away leaves its folder: the next rb open
    takes away every folder no open screen holds, and leaves the one a screen holds."""
    import fcntl
    git, shas = versioned
    common = Path(git("rev-parse", "--path-format=absolute", "--git-common-dir"))
    left, held = common / "rb" / "views" / "999-0badf00d", common / "rb" / "views" / "998-0c0ffee0"
    for d in (left, held):
        (d / shas[0][:12] / ".rb").mkdir(parents=True)
        (d / ".lock").write_text("", encoding="utf-8")
    with open(held / ".lock", "r+") as f:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        server = Open()
        server.close()
        assert not left.exists() and held.is_dir()
    import shutil as _shutil
    _shutil.rmtree(held)


@pytest.mark.xfail(strict=True, reason="4b gap (pins-4b.md), an old page pin that cannot pass on the shell; the fix ports it, owner R (views.rs): the experiment row omits the list-to-confirm command")
def test_the_tab_says_an_experiments_design_standing_in_its_pane_and_its_row(served, tmp_path_factory, monkeypatch):
    """Group E: the session that set e1 up wrote its design. rb open's experiment pane and its row say the design's
    standing once, with the command that confirms it while it waits, and the pane what such a design does not record;
    the overview lists it apart with that command. Once the user confirms it, neither says anything; a session's later
    change reopens it, and the pane says what changed."""
    rb, url, _ = served
    node = shutil.which("node")
    if not node or subprocess.run([node, "-e", "fetch"], capture_output=True, timeout=30).returncode:
        pytest.skip("drawing the pane takes the tab's script, which runs in node 18 or later")
    here = tmp_path_factory.mktemp("tab")
    (here / "page.js").write_text(_get(url + "/")[1].split("<script>", 1)[1].split("</script>", 1)[0], encoding="utf-8")
    (here / "tab.js").write_text(TAB, encoding="utf-8")

    def drawn() -> tuple:
        tab = subprocess.run([node, str(here / "tab.js"), url, str(here / "page.js")], input="load\nexperiment e1\nnav #experiments\n",
                             capture_output=True, text=True, encoding="utf-8", timeout=60)
        assert tab.returncode == 0, tab.stderr
        _, pane, nav = [json.loads(x) for x in tab.stdout.splitlines()]
        # what a reader sees: the command is kept whole on a narrow screen, in a span of its own
        assert all(f'<span class="cmd">{c}</span>' in h for h in (pane, nav["cat"]) for c in re.findall(r"rb confirm e1@[0-9a-f]{64}", html_mod.unescape(h)))
        return (re.sub(r'</?span[^>]*>', "", html_mod.unescape(x)) for x in (pane, nav["cat"]))

    def overview() -> str:
        tab = subprocess.run([node, str(here / "tab.js"), url, str(here / "page.js")], input="load\nview\n",
                             capture_output=True, text=True, encoding="utf-8", timeout=60)
        assert tab.returncode == 0, tab.stderr
        return html_mod.unescape(json.loads(tab.stdout.splitlines()[-1])["call"])

    home = json.loads(_get(url + "/state.json")[1])["home"]
    [e1] = [e for b in home["briefs"] for e in b["experiments"] if e["id"] == "e1"]
    cmd = e1["design"]["confirm"]
    assert re.fullmatch(r"rb confirm e1@[0-9a-f]{64}", cmd) and [(p["kind"], p["confirm"]) for p in home["pending"]] == [("design", cmd)]
    pane, row = drawn()
    # neither shows every part the key names: each gives the command that lists the design (recheck of group E)
    listing = "design not confirmed · list it to confirm: rb confirm e1"
    assert pane.count(listing) == 1 and row.count(listing) == 1 and cmd not in pane + row, (pane, row)
    assert '<div class="k">why</div><div>not recorded</div>' in pane and "hypothesis or purpose" not in pane
    # the overview lists it in a quiet group of its own, opening its experiment, with the command
    call = overview()
    assert "Experiment designs not confirmed (1)" in call, call
    # (review of group E) the row is a button a keyboard reaches, it opens its experiment, and the design reads part by
    # part above the command that confirms it as listed
    assert '<button type="button" class="lnk" data-eid="e1">e1\'s design: the experiment</button>' in call, call
    listed = [p for p in home["pending"] if p["kind"] == "design"][0]["lines"]
    assert listed and all(f'<div class="design-parts">{line}</div>' in call for line in listed), (listed, call)
    assert call.index('<div class="design-parts">') < call.index(f'confirm in your terminal: <span class="cmd">{cmd}</span>')
    opened = subprocess.run([node, str(here / "tab.js"), url, str(here / "page.js")], input="load\nview\ndesign e1\n",
                            capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert opened.returncode == 0, opened.stderr
    assert "<h2>the experiment</h2>" in html_mod.unescape(json.loads(opened.stdout.splitlines()[-1]))
    assert confirm_designs() == ["e1"]
    assert "designs not confirmed" not in overview()
    pane, row = drawn()
    assert "design not confirmed" not in pane + row and "changed since" not in pane and '<div class="k">why</div><div>not recorded</div>' in pane
    monkeypatch.setenv("CLAUDECODE", "1")
    assert rb("criterion", "add", "top5 holds", "-e", "e1", "--metric", "top5", "--at-least", "0.9", "--id", "c2")[0] == 0
    monkeypatch.delenv("CLAUDECODE")
    pane, row = drawn()
    now = json.loads(_get(url + "/state.json")[1])["home"]["pending"][0]["confirm"]
    assert now != cmd and listing in pane and listing in row and now not in pane + row
    assert "changed since you confirmed it: criterion c2 added: top5 ≥ 0.9 (primary)" in pane, pane
    assert "changed since you confirmed it" not in row, row          # the row stays one quiet line; its pane says more


@pytest.mark.xfail(strict=True, reason="4b gap (pins-4b.md), an old page pin that cannot pass on the shell; the fix ports it, owner R (shell.css, views.rs) and C (nodes.rs): escaped criterion, title and history run past 390")
def test_a_word_of_escapes_wraps_inside_the_screen_on_every_tab(served, tmp_path_factory, monkeypatch):
    """Trials of the closing pass: a question a session added with characters a terminal hides was drawn on the
    overview with its escapes, one word wider than a 390-pixel screen, and the overview, whose one column took the width
    of that word, cut it before its end; on the Findings and History tabs such a word, in a criterion's statement, an
    experiment's title or a line of history, ran past the screen's edge. Where the home draws an escape (`escaped` in its
    data), each tab wraps a word however long, as a page that draws one does; where it draws none, each tab is drawn as it
    was."""
    rb, url, _ = served
    node = shutil.which("node")
    if not node or subprocess.run([node, "-e", "fetch"], capture_output=True, timeout=30).returncode:
        pytest.skip("drawing the tabs takes the tab's script, which runs in node 18 or later")
    app = (Path(__file__).resolve().parents[1] / "rust" / "src" / "assets" / "open_app.html").read_text(encoding="utf-8")
    assert "#cat.escaped{overflow-wrap:anywhere}" in app
    here = tmp_path_factory.mktemp("tab")
    (here / "page.js").write_text(_get(url + "/")[1].split("<script>", 1)[1].split("</script>", 1)[0], encoding="utf-8")
    (here / "tab.js").write_text(TAB, encoding="utf-8")
    tabs = {"overview": "load\n", "experiments": "load\nnav #experiments\n", "findings": "load\nfindings\n", "runs": "load\nruns\n",
            "history": "load\nnav #history\n"}

    def drawn() -> dict:
        out = {}
        for k, given in tabs.items():
            tab = subprocess.run([node, str(here / "tab.js"), url, str(here / "page.js")], input=given + "view\nclasses\n",
                                 capture_output=True, text=True, encoding="utf-8", timeout=60)
            assert tab.returncode == 0, tab.stderr
            lines = [json.loads(x) for x in tab.stdout.splitlines()]
            out[k] = (lines[-1]["cat"], html_mod.unescape(lines[-2]["cat"]))
        return out

    plain = drawn()
    assert {k: c for k, (c, _) in plain.items()} == dict.fromkeys(tabs, "") and "escaped" not in _answer(url + "/state.json")[1]["home"]
    tags = "".join(chr(0xE0000 + ord(c)) for c in " in fact")
    monkeypatch.setenv("CLAUDECODE", "1")
    assert rb("question", "add", "does it hold" + tags + "?", "--id", "q2")[0] == 0
    monkeypatch.delenv("CLAUDECODE")
    escaped = drawn()
    assert {k: c for k, (c, _) in escaped.items()} == dict.fromkeys(tabs, "escaped")
    overview = escaped["overview"][1]
    assert "does it hold\\udb40\\udc20" in overview and tags[0] not in overview, overview
    assert _answer(url + "/state.json")[1]["home"]["escaped"] is True


@pytest.mark.xfail(strict=True, reason="4b gap (pins-4b.md), an old page pin that cannot pass on the shell; the fix ports it, owner R (shell.css, views.rs): a design's lines do not keep runs of spaces")
def test_an_experiments_design_lines_keep_their_spaces_in_its_pane_and_its_row():
    """Recheck of the snapshot rule, round 3: after a session put two spaces in a confirmed gate's branch, the
    experiment's pane said `changed since you confirmed it: the gate, was "...ship INT8...", now "...ship INT8..."`, the
    same was and now, as the page collapsed the run of spaces rb show prints. The pane's and the row's design lines keep
    their spaces, as the overview's group of designs that wait does."""
    app = (Path(__file__).resolve().parents[1] / "rust" / "src" / "assets" / "open_app.html").read_text(encoding="utf-8")
    assert ".design-standing{color:var(--dim);overflow-wrap:anywhere;white-space:pre-wrap}" in app
    assert app.count('class="said design-standing"') == 1 and app.count('class="meta design-standing"') == 1


@pytest.mark.xfail(strict=True, reason="4b gap (pins-4b.md), an old page pin that cannot pass on the shell; the fix ports it, owner R (views.rs): the experiment row omits the listing command")
def test_a_design_waiting_in_another_place_gives_its_command_with_the_cd_there(placed, tmp_path_factory):
    """Recheck of group E: rb open showing exp-c, where a session wrote lat's design, gave the overview's command with
    the cd to exp-c, but lat's row, its pane and its card said `rb confirm lat@<hash>` alone, which typed in the
    terminal rb open started in answers for the record there. Every command the screen gives for the place it shows
    starts with the cd to it."""
    rec, server = placed
    c = rec.tree["exp-c"]
    rec.rb("experiment", "add", "INT8 latency", "--id", "lat", "--hypothesis", "INT8 halves CPU latency", "--if-holds", "ship INT8", "--if-not", "keep FP32",
           cwd=c, who={"CLAUDECODE": "1", "CLAUDE_CODE_SESSION_ID": "sess-there-3"})
    _, s = _answer(server.url + "/state.json")
    p = {x["branch"]: x for x in s["places"]}
    there = p["exp-c"]
    _, c_state = _answer(f"{server.url}/state.json?place={there['id']}")
    [lat] = [e for b in c_state["home"]["briefs"] for e in b["experiments"] if e["id"] == "lat"]
    cmd = lat["design"]["confirm"]
    assert cmd.startswith(f"cd {there['record_dir']} && rb confirm lat@") and cmd.count("cd ") == 1, cmd
    # its row, pane and card do not show every part the key names: they give the command that lists it there
    listing = f"design not confirmed · list it to confirm: cd {there['record_dir']} && rb confirm lat"
    assert lat["design"]["lines"][0] == listing, lat["design"]
    assert [r["confirm"] for r in c_state["home"]["pending"] if r["kind"] == "design"] == [cmd]
    _, card = _answer(f"{server.url}/card.json?ref=o:lat&place={there['id']}")
    assert listing in card["card"]["facts"], card["card"]["facts"]
    # the home where rb open started lists it under exp-c in one line, so it gives the command that lists it there
    # (`list`), said as one that lists it; `confirm` stays the one that confirms it as listed
    _, home = _answer(server.url + "/state.json")
    [row] = [r for r in {x["branch"]: x for x in home["elsewhere"]}["exp-c"]["waiting"] if r["kind"] == "design"]
    assert (row["list"], row["confirm"]) == (f"cd {there['record_dir']} && rb confirm lat", cmd), row
    node = shutil.which("node")
    if node and not subprocess.run([node, "-e", "fetch"], capture_output=True, timeout=30).returncode:
        here = tmp_path_factory.mktemp("tab-design-elsewhere")
        (here / "page.js").write_text(_get(server.url + "/")[1].split("<script>", 1)[1].split("</script>", 1)[0], encoding="utf-8")
        (here / "tab.js").write_text(TAB, encoding="utf-8")
        tab = subprocess.run([node, str(here / "tab.js"), server.url, str(here / "page.js")], input="load\nview\n",
                             capture_output=True, text=True, encoding="utf-8", timeout=120)
        assert tab.returncode == 0, tab.stderr
        listed = html_unescape(json.loads(tab.stdout.splitlines()[-1])["elsewhere"])
        assert f"list it to confirm, in your terminal: {row['list']}" in listed and f"confirm in your terminal: {row['list']}" not in listed, listed
    # rb ui's card of it, shown from the same terminal, says the same
    from rabbit_brain import tui
    assert listing in tui.object_card(rec.root, "lat", there["id"]).facts
    # where the screen started, the same experiment's command has no cd
    _, here = _answer(server.url + "/card.json?ref=o:e1")
    # (the command, never the two letters: a commit hash may end in "cd")
    assert not [f for f in here["card"]["facts"] if re.search(r"\bcd \S.* && ", f)]


def test_open_app_html_is_gone():
    """rb open's page is the shell: open_app.html is gone, and nothing serves or reads it (BP 4b)."""
    root = Path(__file__).resolve().parents[1]
    assert not (root / "rust" / "src" / "assets" / "open_app.html").exists()
    assert "open_app.html" not in (root / "rust" / "src" / "webui.rs").read_text(encoding="utf-8")


# The shell draws its document on the server; these pins need no simulated DOM.
class _ShellElement:
    def __init__(self, tag="", attrs=()):
        self.tag, self.attrs, self.children = tag, dict(attrs), []

    @property
    def text(self):
        return "".join(c if isinstance(c, str) else c.text for c in self.children)

    def all(self, tag=None, **attrs):
        return [c for c in self.walk() if (tag is None or c.tag == tag) and all(c.attrs.get(k) == v for k, v in attrs.items())]

    def walk(self):
        for child in self.children:
            if isinstance(child, _ShellElement):
                yield child
                yield from child.walk()

    def at(self, id):
        [element] = self.all(id=id)
        return element


def _shell_document(html):
    from html.parser import HTMLParser

    class Parser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.root = _ShellElement()
            self.stack = [self.root]

        def handle_starttag(self, tag, attrs):
            element = _ShellElement(tag, attrs)
            self.stack[-1].children.append(element)
            if tag not in {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}:
                self.stack.append(element)

        def handle_endtag(self, tag):
            for i in range(len(self.stack) - 1, 0, -1):
                if self.stack[i].tag == tag:
                    del self.stack[i:]
                    break

        def handle_data(self, text):
            self.stack[-1].children.append(text)

    parser = Parser()
    parser.feed(html)
    return parser.root


@pytest.fixture
def shell_served(monkeypatch, request):
    monkeypatch.setenv("RB_TEST_SHELL", "1")
    served = request.getfixturevalue("served")
    monkeypatch.setenv("RB_ACTOR", "human:ana")
    return served


@pytest.fixture
def shell_placed(monkeypatch, request):
    monkeypatch.setenv("RB_TEST_SHELL", "1")
    return request.getfixturevalue("placed")


def _shell(url, query=""):
    status, html = _get(url + "/" + query)
    assert status == 200 and html.startswith("<!doctype html>")
    return _shell_document(html)


@pytest.mark.parametrize("route", ["/", "/index.html", "/o/c-c1.html", "/digest.json"])
@pytest.mark.parametrize("query", ["place=p_0000000000", "at=" + "0" * 40])
def test_shell_place_choices_require_its_own_page(shell_placed, route, query):
    """A typed or cross-site place or version choice is refused on every shell route, before it reads another place."""
    _, server = shell_placed
    for site in ("none", "cross-site", "same-site"):
        status, body = _answer(server.url + route + "?" + query, {"Sec-Fetch-Site": site})
        assert status == 403 and body == {"error": "A place or a version is chosen on rb open's own page."}
    assert _answer(server.url + "/digest.json", {"Sec-Fetch-Site": "same-origin"})[0] == 200


def test_shell_is_self_contained_and_only_reads(shell_served):
    """The served shell has no external assets, write controls or commands for running the work."""
    _, url, _ = shell_served
    doc = _shell(url)
    for element in doc.walk():
        for key in ("src", "href", "action"):
            value = element.attrs.get(key, "")
            assert not value.startswith(("http:", "https:", "//")), (element.tag, value)
    assert not doc.all("form")
    assert not doc.all("script", src="")
    for id in ("overview", "research", "experiments", "criteria", "runs", "history", "resources", "your-call"):
        assert doc.at(id)
    text = doc.at("main").text
    assert "rb run add" not in text and "rb freeze" not in text and "rb approve" not in text
    assert "\u2014" not in text
    assert _get(url + "/page.html")[0] == 200


def test_shell_places_keep_short_labels_and_whole_paths(shell_placed):
    """Where names each place in short, keeps the whole path, and says why an unreadable choice cannot open."""
    _, server = shell_placed
    data = _answer(server.url + "/state.json")[1]
    doc = _shell(server.url)
    where = doc.at("where")
    assert where.all("details", **{"data-pick": "where"})
    assert where.all("details", **{"data-pick": "version"})
    for p in data["places"]:
        assert p["label_short"] in where.text
        if not p["readable"]:
            assert p["why_not"] in where.text
    # the Where button names the place by its branch alone, its whole path the tooltip; the place line, where it is
    # drawn, says only the rest
    [summary] = where.all("details", **{"data-pick": "where"})[0].all("summary")
    assert summary.attrs["title"] == data["place"]["path"]
    assert data["place"]["label_short"].split(" · ")[0] in summary.text
    for line in where.all("span", **{"class": "pl"}):
        assert data["place"]["label_short"] not in line.text and line.attrs["title"] == data["place"]["path"]


def test_shell_scope_names_away_and_past_and_returns_home(shell_placed):
    """Away and past documents say what is shown, read only, and link back; live at home has no scope message."""
    _, server = shell_placed
    data = _answer(server.url + "/state.json")[1]
    p = next(p for p in data["places"] if p["branch"] == "exp-c")
    sha = data["place"]["versions"][0]["sha"]
    assert not _shell(server.url).at("scope").text
    for query, back in ((f"?place={p['id']}", "Back to main"), (f"?at={sha}", "Back to now"),
                        (f"?place={p['id']}&at={sha}", "Back to now")):
        doc = _shell(server.url, query)
        scope = doc.at("scope")
        assert scope.attrs["role"] == "region" and scope.attrs["aria-label"] == "What rb open shows"
        assert "read only" in scope.text and back in scope.text
        [link] = scope.all("a", **{"class": "back"})
        assert link.attrs["data-at"] == "" and link.attrs["href"].startswith("#overview")
        [bar] = scope.all("p", **{"class": "bar"})
        if "at=" in query:
            assert sha[:12] in scope.text and sha[:12] in bar.attrs["data-title"]
        else:
            # the path said by its folder alone; the tab's title keeps the place in short
            name, path = p["label_short"].split(" · ", 1)
            assert f"{name} · {path.rstrip('/').rsplit('/', 1)[-1]}" in scope.text and p["label_short"] in bar.attrs["data-title"]


def test_shell_elsewhere_says_a_criterion_not_merged_here_as_a_rule(shell_placed):
    """On a screen a criterion is a rule: Elsewhere says what a place holds not merged here in that word (rb status,
    in a terminal, keeps the word its commands use: elsewhere.rs's unit test)."""
    rec, server = shell_placed
    rec.rb("criterion", "add", "int8 keeps top1 within 0.02", "-e", "e1", "--metric", "change.top1", "--at-least", "-0.02", "--id", "c9",
           cwd=rec.tree["exp-c"])
    row = next(r for r in _answer(server.url + "/state.json")[1]["elsewhere"] if "exp-c" in json.dumps(r))
    assert "1 rule" in row["adds"] and "criteri" not in row["adds"], row["adds"]
    elsewhere = _shell(server.url).at("elsewhere")
    assert "not merged here: " + row["adds"] in elsewhere.text and "criteri" not in elsewhere.text


def test_shell_elsewhere_keeps_each_places_own_counts_and_commands(shell_placed):
    """Elsewhere lists each place separately with its counts and pending commands, only on the live start page."""
    rec, server = shell_placed
    proposed = rec.rb("decide", "e1", "accept", "--why", "INT8 stays within the accuracy budget", cwd=rec.tree["exp-c"],
                      who={"CLAUDECODE": "1", "CLAUDE_CODE_SESSION_ID": "int8-away"})["object"]["id"]
    data = _answer(server.url + "/state.json")[1]
    doc = _shell(server.url)
    elsewhere = doc.at("elsewhere")
    [places] = elsewhere.all("ul", **{"class": "place-list"})
    rows = [c for c in places.children if isinstance(c, _ShellElement) and c.tag == "li"]
    assert len(rows) == len(data["elsewhere"])
    for expected in data["elsewhere"]:
        [row] = [r for r in rows if expected["label_short"] in r.text]
        assert expected["said"] in row.text
        if expected["adds"]:
            assert "not merged here: " + expected["adds"] in row.text
        for pending in expected["waiting"]:
            assert pending["confirm"] in row.text
    c = next(p for p in data["places"] if p["branch"] == "exp-c")
    assert f"cd {c['record_dir']} && rb confirm {proposed}" in elsewhere.text
    assert not _shell(server.url, f"?place={c['id']}").all(id="elsewhere")
    sha = data["place"]["versions"][0]["sha"]
    assert not _shell(server.url, f"?at={sha}").all(id="elsewhere")
    assert not _shell_document(_get(server.url + "/page.html")[1]).all(id="elsewhere")


def test_shell_receipts_keep_the_run_id_after_another_run_lands(shell_served):
    """Receipt links name a run, so a newer run cannot take the old run's receipt or its values."""
    rb, url, _ = shell_served
    assert rb("run", "add", "e1", "top1=0.76")[0] == 0
    a = _answer(url + "/state.json")[1]["home"]["runs"][0]["run_id"]
    assert _shell(url).at("x-e1").all("a", href=f"#run-{a}")
    assert rb("run", "add", "e1", "top1=0.81")[0] == 0
    b = _answer(url + "/state.json")[1]["home"]["runs"][0]["run_id"]
    doc = _shell(url)
    for rid, value, other in ((a, "top1=0.76", "top1=0.81"), (b, "top1=0.81", "top1=0.76")):
        receipt = doc.at(f"run-{rid}")
        assert value in receipt.text and other not in receipt.text
        assert doc.at("runs").all("a", href=f"#run-{rid}")
        status, html = _get(url + f"/o/run-{rid}.html")
        assert status == 200 and value in _shell_document(html).at(f"run-{rid}").text


def test_shell_sqlite_receipt_names_the_row_and_each_column(shell_served):
    """A SQLite receipt names its database, table, selected row and the column read for each value."""
    rb, url, _ = shell_served
    with sqlite3.connect("results.db") as con:
        con.execute("create table evals (run_name text, seed integer, top1 real, ended text)")
        con.execute("insert into evals values ('r1', 7, 0.76, '2026-05-01 10:00:00')")
    assert rb("spec", "set", "e1", "seed", "--per-run")[0] == 0
    code, env = rb("run", "add", "e1", "--variant", "new", "--from", "results.db", "--table", "evals", "--where", "run_name=r1",
                   "--set", "seed=@seed", "--produced-at", "@ended")
    assert code == 0, env.errors
    rid = env.data["object"]["id"]
    for html in (_get(url + "/")[1], _get(url + f"/o/run-{rid}.html")[1]):
        receipt = _shell_document(html).at(f"run-{rid}")
        for said in ("read from results.db, table evals, rowid 1 (run_name=r1)", "new.top1 from column top1", "seed from column seed", "produced at from column ended"):
            assert said in receipt.text


def test_shell_scope_and_uncited_files_stay_on_the_overview(shell_served):
    """The Overview draws rb's scope and uncited-file count, separate from the calls and criteria."""
    rb, url, _ = shell_served
    Path("configs").mkdir()
    Path("configs/eval.yaml").write_text("dataset: imagenet-val\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], check=True)
    subprocess.run(["git", "commit", "-qm", "Record the INT8 evaluation config"], check=True)
    assert rb("status")[0] == 0
    Path("configs/eval.yaml").write_text("dataset: imagenet-val\ncrop: resize\n", encoding="utf-8")
    subprocess.run(["git", "commit", "-qam", "Resize the evaluation images"], check=True)
    assert rb("status")[0] == 0
    home = _answer(url + "/state.json")[1]["home"]
    assert home["uncited"] == 1
    doc = _shell(url)
    [scope] = doc.at("overview").all("p", **{"data-live": "scope"})
    assert scope.text.startswith("rb looks in out/, outputs/, results/, eval-out/ and metrics/")
    assert scope.text.split(" UTC. ")[1] == home["scope"].split(" UTC. ")[1]
    assert home["uncited_line"] in doc.at("overview").text
    assert home["uncited_line"] not in doc.at("criteria").text
    assert not doc.at("criteria").all("p", **{"data-live": "scope"})


def test_shell_fixed_cells_keep_rbs_digits_and_descriptive_range(shell_served):
    """Fixed design cells draw rb's six-digit estimates and descriptive range, without a sampling interval across cells."""
    rb, url, _ = shell_served
    assert rb("spec", "set", "e1", "width", "--design-factor")[0] == 0
    assert rb("criterion", "add", "INT8 top1 holds at every width", "-e", "e1", "--metric", "top1", "--at-least", "0.3", "--id", "grid", "-q", "q1")[0] == 0
    for width, value in ((256, "0.3333333333333333"), (512, "0.6666666666666666")):
        assert rb("run", "add", "e1", "top1=" + value, "--set", f"width={width}")[0] == 0
    card = _shell(url).at("c-grid")
    text = card.text
    [cells] = card.all("ul", **{"class": "cells"})
    # in two decimals, rb's six digits on hover (DESIGN-PASS-1 P5)
    assert [e.text for e in cells.all("span", **{"class": "v"})] == ["0.33", "0.67"]
    assert [e.attrs.get("title") for e in cells.all("span", **{"class": "nb"})] == ["0.333333", "0.666667"]
    assert "descriptive range 0.333333 to 0.666667" in text
    assert "0.3333333333333333" not in text and "0.6666666666666666" not in text


def test_shell_interpretation_has_its_label_without_a_writer(shell_served, monkeypatch):
    """An interpretation appears under its label and confirmation standing, without a writer or an empty separator."""
    rb, url, _ = shell_served
    monkeypatch.setenv("CLAUDECODE", "1")
    assert rb("criterion", "add", "INT8 top1 stays above 0.6", "-e", "e1", "--metric", "top1", "--at-least", "0.6", "--id", "c2", "-q", "q1",
              "--when-met", "INT8 keeps ResNet-50 accuracy")[0] == 0
    monkeypatch.delenv("CLAUDECODE")
    assert rb("run", "add", "e1", "top1=0.76")[0] == 0
    doc = _shell(url)
    assert "INT8 keeps ResNet-50 accuracy" in doc.at("q-q1").text
    [label] = [k for k in doc.at("q-q1:ip:1").all("span", **{"class": "k"}) if "note" in k.text.lower()]  # the note's term (design pass 1)
    assert "A note, not confirmed" in label.text
    assert "claude" not in label.text.lower() and "agent" not in label.text.lower()
    assert "not confirmed" in doc.at("q-q1").text
    assert " ·  · " not in doc.at("q-q1").text


def test_shell_recorded_in_links_only_to_a_readable_record(shell_placed):
    """A run recorded in a readable sibling record links there; a worktree sharing this record stays plain words."""
    rec, server = shell_placed
    rec.git("add", ".rb", cwd=rec.tree["exp-c"])
    rec.git("commit", "-qm", "Record the INT8 sibling run", cwd=rec.tree["exp-c"])
    rec.git("merge", "-q", "--ff-only", "exp-c")
    data = _answer(server.url + "/state.json")[1]
    p = next(p for p in data["places"] if p["branch"] == "exp-c")
    doc = _shell(server.url)
    receipt = doc.at("run-" + rec.run_sibling)
    links = [e for e in receipt.all("a") if e.attrs.get("data-place") == p["id"]]
    assert links and all(p["label_short"] in e.text for e in links)
    notes = [e for e in doc.at("c-c1").all("a") if e.attrs.get("data-place") == p["id"]]
    assert notes and all(p["label_short"] in e.text for e in notes)
    assert "recorded in exp-b · .worktrees/b" in doc.at("run-" + rec.run_nested).text
    assert not any("data-place" in e.attrs for e in doc.at("run-" + rec.run_nested).all("a"))


def test_shell_calls_wait_until_the_result_has_a_call(shell_served):
    """A missed criterion leaves a call on the Overview and in Your call; after the call, nothing waits on it."""
    rb, url, _ = shell_served
    assert confirm_designs() == ["e1"]
    assert rb("run", "add", "e1", "top1=0.5")[0] == 0
    doc = _shell(url)
    # the Overview's queue (Overview v2): its heading counts nothing (the line under it says the counts), and the
    # call's one row
    queue = doc.at("queue")
    assert doc.at("h-queue").text.split() == ["Needs", "your", "judgment"]
    assert [li.attrs.get("data-kind") for li in queue.all("li")].count("call") == 1
    assert "Nothing waits on your judgment" not in doc.at("overview").text
    assert "c1" in doc.at("your-call").text
    assert rb("decide", "c1", "reject", "-m", "INT8 misses the accuracy budget")[0] == 0
    doc = _shell(url)
    assert "Nothing waits on your judgment." in doc.at("queue").text and not doc.at("queue").all("li")
    assert "Your call (0)" in doc.at("your-call").text


def test_shell_past_version_cannot_copy_commands(two_places, monkeypatch):
    """A past version offers no Copy control, and Back to now offers the live commands again."""
    from app.test_app import harness
    monkeypatch.setenv("RB_TEST_SHELL", "1")
    root, env = two_places
    from rbtest import command
    # A saved command with committed code must still be present in the past document: merely withholding a
    # proposed call would leave no command for a missing Copy guard to expose.
    (root / "eval.py").write_text('print("INT8 ResNet-50 evaluation")\n', encoding="utf-8")
    def record(*argv):
        result = subprocess.run([*command(), *argv], cwd=root, env=env, capture_output=True, text=True, timeout=60)
        assert result.returncode == 0, result.stderr
    def commit(message):
        subprocess.run(["git", "-C", str(root), "add", "-A"], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(root), "-c", "user.name=ana", "-c", "user.email=ana@example.org", "commit", "-qm", message], check=True, capture_output=True)
    record("experiment", "add", "INT8 against FP32", "--id", "int8", "--purpose", "Compare ResNet-50 accuracy", "--baseline", "fp32", "--candidate", "int8")
    commit("Record the INT8 evaluation code")
    record("run", "add", "int8", "fp32.top1=0.761", "int8.top1=0.759", "--command", "python eval.py --precision int8")
    commit("Record the INT8 evaluation run")
    said = harness(None, "--places", "--copy-only", "--rb", command()[0], "--record", str(root), timeout=180, env=env)
    assert said["exit"] == 0 and said["ok"], (said.get("error"), said["stderr"][-3000:])
    names = [c["name"] for c in said["checks"]]
    for at in ("390 light", "390 dark", "1280 light", "1280 dark"):
        assert f"places: past commands cannot be copied [{at}]" in names
        assert f"places: returning to now restores Copy [{at}]" in names


def test_shell_away_design_pane_names_the_directory(shell_placed):
    """An experiment's design pane and waiting group name the directory where their commands must run."""
    rec, server = shell_placed
    rec.rb("experiment", "add", "INT8 latency", "--id", "lat", "--hypothesis", "INT8 halves CPU latency",
           cwd=rec.tree["exp-c"], who={"CLAUDECODE": "1", "CLAUDE_CODE_SESSION_ID": "int8-design"})
    data = _answer(server.url + "/state.json")[1]
    p = next(p for p in data["places"] if p["branch"] == "exp-c")
    doc = _shell(server.url, f"?place={p['id']}")
    listing = f"design not confirmed · list it to confirm: cd {p['record_dir']} && rb confirm lat"
    assert doc.at("x-lat:design").text.count(listing) == 1
    cmds = doc.at("to-confirm").all("pre", **{"class": "cmd"})
    assert cmds and all(c.text.startswith(f"cd {p['record_dir']} && rb confirm ") and c.text.count("cd ") == 1 for c in cmds)


def test_shell_proposed_call_words_move_only_on_confirmation(shell_served, monkeypatch):
    """A proposed call keeps its words and confirm apart from Your call until it is confirmed."""
    rb, url, _ = shell_served
    monkeypatch.setenv("CLAUDECODE", "1")
    code, env = rb("decide", "e1", "accept", "-m", "INT8 clears the bar", "--words", "Ship INT8 when top1 holds")
    assert code == 0
    monkeypatch.delenv("CLAUDECODE")
    did = env.data["object"]["id"]
    doc = _shell(url)
    assert 'words (as given): "Ship INT8 when top1 holds"' in doc.at("to-confirm").text
    assert f"rb confirm {did}" in doc.at("to-confirm").text
    assert "Ship INT8 when top1 holds" not in doc.at("your-call").text
    assert rb("confirm", did)[0] == 0
    doc = _shell(url)
    assert "Ship INT8 when top1 holds" not in doc.at("to-confirm").text
    text = doc.at("q-q1").text
    assert '"Ship INT8 when top1 holds"' in text
    assert 'words (as given): "Ship INT8 when top1 holds"' not in text


def test_shell_each_waiting_call_is_listed_once_under_its_deepest_inquiry(workdir, monkeypatch, tmp_path_factory, capsys):
    """Your call lists each result once, under its deepest inquiry, while found files stay outside its count."""
    monkeypatch.setenv("RB_TEST_SHELL", "1")
    as_the_user(monkeypatch)
    cfg = tmp_path_factory.mktemp("gitconfig") / "config"
    cfg.write_text("[user]\n\tname = ana\n\temail = ana@example.com\n", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(cfg))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("RB_ACTOR", "human:ana")
    subprocess.run(["git", "init", "-q"], check=True)

    def rb(*argv):
        code = main([*argv, "--json"])
        out = capsys.readouterr().out
        assert code == 0, out
        return Envelope.model_validate_json(out)

    rb("init", "INT8 ResNet-50 on ImageNet", "--no-agents")
    rb("question", "add", "Does INT8 keep ResNet-50 accuracy on ImageNet?", "--id", "q1")
    rb("question", "add", "Does calibration size matter for INT8?", "--id", "q1a", "--parent", "q1")
    rb("experiment", "add", "INT8 against FP32", "--id", "int8", "-q", "q1", "--baseline", "fp32", "--candidate", "int8")
    rb("experiment", "add", "Calibration with 512 images", "--id", "calib", "-q", "q1a", "--baseline", "c1024", "--candidate", "c512")
    for cid, exp, metric, ref, sub, floor in (("c-top1", "int8", "top1", "fp32", "int8", "-0.5"), ("c-calib", "calib", "top1", "c1024", "c512", "-0.2"),
                                              ("c-calib5", "calib", "top5", "c1024", "c512", "-0.1")):
        rb("criterion", "add", f"{sub} loses at most {floor[1:]} {metric}", "-e", exp, "-q", "q1a" if exp == "calib" else "q1", "--metric", metric,
           "--subject", sub, "--reference", ref, "--at-least", floor, "--id", cid)
    rb("criterion", "file", "c-calib", "q1", "--why", "calibration bears on whether INT8 holds")     # the parent reads it too
    rb("run", "add", "int8", "fp32.top1=76.1", "int8.top1=75.2")
    rb("run", "add", "calib", "c1024.top1=75.9", "c512.top1=75.4", "c1024.top5=92.8", "c512.top5=92.5")
    assert [i["subject"] for i in rb("status").data["open"] if i["who"] == "human"] == ["c-top1", "c-calib", "c-calib5"]

    server = Open()
    try:
        doc = _shell(server.url)
        calls = doc.at("your-call")
        rows = [e for e in calls.walk() if e.attrs.get("data-for", "").startswith("call-")]
        assert [r.attrs["data-for"] for r in rows] == ["call-c-top1", "call-c-calib", "call-c-calib5"]
        assert calls.text.startswith("Your call (3)")
        Path("out").mkdir()
        Path("out/summary.json").write_text(json.dumps({"c1024": {"top5": 92.7}, "c512": {"top5": 92.4}}), encoding="utf-8")
        rb("journal", "scan")
        doc = _shell(server.url)
        assert doc.at("your-call").text.startswith("Your call (3)")
        assert "summary.json" not in doc.at("your-call").text
        assert "summary.json" in doc.at("overview").text
    finally:
        server.close()


def test_shell_escaped_question_and_waiting_design_parts_fit(shell_served, monkeypatch):
    """An escaped question wraps in every view at 390 pixels; a waiting design is one line with its command whole, and its
    card holds the gate's words as a session gave them."""
    from app.test_app import harness, _walked_whole
    rb, url, _ = shell_served
    monkeypatch.setenv("CLAUDECODE", "1")
    tags = "".join(chr(0xE0000 + ord(c)) for c in " INT8 ResNet-50" * 12)
    assert rb("question", "add", "Does INT8 keep top1" + tags + "?", "--id", "q2")[0] == 0
    assert rb("experiment", "gate", "e1", "--if-holds", "ship  INT8", "--if-not", "keep FP32")[0] == 0
    monkeypatch.delenv("CLAUDECODE")
    doc = _shell(url)
    assert "\\udb40\\udc20" in doc.at("q-q2").text and tags[0] not in doc.at("main").text
    # the waiting row names the design and its command once; the design's parts, the gate's words among them, are on its card
    assert "rb confirm e1" in doc.at("to-confirm").text
    assert "ship  INT8" in doc.at("x-e1").text and "rb confirm e1" in doc.at("x-e1").text
    _walked_whole(harness(url, "--generic", timeout=600))


def test_shell_experiment_reasons_keep_their_note_or_why_label(shell_served, monkeypatch):
    """An unconfirmed reason reads as a note; a reason given in the terminal keeps its why label."""
    rb, url, _ = shell_served
    monkeypatch.setenv("CLAUDECODE", "1")
    assert rb("experiment", "add", "INT8 calibration", "--id", "cal", "--hypothesis", "INT8 keeps top1", "--why", "INT8 needs 1024 calibration images")[0] == 0
    monkeypatch.delenv("CLAUDECODE")
    assert rb("experiment", "add", "INT8 validation", "--id", "val", "--hypothesis", "INT8 keeps top1", "--why", "Use ImageNet validation")[0] == 0
    doc = _shell(url)
    assert "noteINT8 needs 1024 calibration images · not confirmed" in doc.at("x-cal").text
    assert "whyUse ImageNet validation" in doc.at("x-val").text
    assert "whyINT8 needs 1024 calibration images" not in doc.at("x-cal").text


def test_shell_resource_availability_is_recomputed_in_its_row_and_card(shell_served):
    """A resource keeps its identity and readers when its file goes away; its row and card recompute availability."""
    rb, url, _ = shell_served
    Path("data").mkdir()
    Path("data/imagenet.bin").write_bytes(b"INT8 ResNet-50 validation images")
    assert rb("resource", "add", "imagenet", "--type", "dataset", "--name", "ImageNet-1k", "--version", "validation",
              "--file", "data/imagenet.bin", "--url", "https://example.org/imagenet")[0] == 0
    assert rb("run", "add", "e1", "top1=0.76", "--input", "imagenet:eval")[0] == 0
    home = _answer(url + "/state.json")[1]["home"]
    identity = home["resources"][0]["identity"]
    rid = home["runs"][0]["run_id"]
    for availability, where in (("present", "here"), ("reconstructible", "gone")):
        doc = _shell(url)
        card = doc.at("r-imagenet")
        assert availability in doc.at("resources").text and availability in card.text
        assert doc.at("resources").all(**{"data-for": "r-imagenet"})
        assert "ImageNet-1k validation" in card.text and identity in card.text
        assert card.all("a", href="#run-" + rid)
        assert f"local:data/imagenet.bin · {where}" in card.text
        assert "1 run" in card.text
        if where == "here":
            Path("data/imagenet.bin").unlink()
        else:
            assert "https://example.org/imagenet" in card.text


def test_shell_waiting_reasons_keep_the_wrap_rule_on_their_element(shell_served, monkeypatch):
    """A waiting reason is escaped in its own row, whose served styles wrap an unbroken word inside the card."""
    rb, url, _ = shell_served
    monkeypatch.setenv("CLAUDECODE", "1")
    tags = "".join(chr(0xE0000 + ord(c)) for c in " INT8 calibration" * 12)
    code, env = rb("decide", "e1", "park", "--why", "INT8 needs calibration" + tags)
    assert code == 0
    monkeypatch.delenv("CLAUDECODE")
    _, html = _get(url + "/")
    doc = _shell_document(html)
    waiting = doc.at("to-confirm")
    [reason] = [e for e in waiting.all("p", **{"class": "wt"}) if "INT8 needs calibration" in e.text]
    assert "\\udb40\\udc20" in reason.text and tags[0] not in reason.text
    assert f"rb confirm {env.data['object']['id']}" in waiting.text
    css = "".join(e.text for e in doc.all("style"))
    rules = re.findall(r"([^{}]+)\{([^{}]*)\}", css)
    assert any(".wt" in [s.strip() for s in selector.split(",")] and "overflow-wrap:break-word" in re.sub(r"\s+", "", body)
               for selector, body in rules)
