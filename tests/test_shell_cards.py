"""The shell's cards (BP 2b-1, SHELL-SPEC section 5), drawn by rb page, rb export and rb open from the contract's INT8
examples (RB_TEST_SHELL_MODEL): every object has its card once, in the frame's cards region; each keeps the waterline,
its receipts folded last; numbers, ids and standing words drill; a page names no place, platform or writer; an export
offers no control a script would drive. They wait for R's frame (R1), whose cards region calls nodes::all; a track
runs them before then with RB_SHELL_CONTRACT_MERGED=R1."""
import html
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from test_shell_contract import Tree, drawn_or_pending, example, needs, rb, record, said, select  # noqa: F401  (record is a fixture)


def anchors(doc: dict) -> list:
    """The anchor of every card the frame's cards region holds (SHELL-SPEC section 2.2). A question's qualifications
    and reading groups live in its view's folds, which R draws at the same anchors."""
    want = [f"c-{c}" for c in doc["findings"]] + [f"run-{r}" for r in doc["runs"]] + [f"r-{r}" for r in doc["resources"]]
    want += [f"a-{a}" for a in doc["analyses"]] + [f"call-{c['subject']}" for c in doc["calls"]]
    for x in doc["experiments"]:
        want += sorted({f"x-{x['id']}:v:{row['variant']}" for row in x["table"]["rows"]})
    return want


def card(tree: Tree, ident: str):
    el = tree.by_id(ident)
    assert el is not None, f"no #{ident}"
    assert "card" in el["attrs"].get("class", "").split(), (ident, el["attrs"])
    return el


@pytest.mark.parametrize("surface", ["page", "export"])
def test_every_object_has_its_card_once(record, surface):
    """Every finding, run, resource, analysis, call and variant of the Owner example has its card, once,
    with the view it belongs to and its crumb, inside the frame's cards region."""
    page = drawn_or_pending(record, surface, "owner", "R1")
    tree = Tree(page)
    for ident in anchors(example("owner")):
        assert page.count(f' id="{ident}"') == 1, ident
        el = card(tree, ident)
        assert el["attrs"].get("data-home") and el["attrs"].get("data-crumb"), ident
    region = [e for e in tree.all() if "nodes" in e["attrs"].get("class", "").split()]
    assert region and len([e for e in select(tree, ".card[id][data-home][data-crumb]")]) >= len(anchors(example("owner")))
    assert not [e for e in select(tree, "details[id]")], "an id on a fold"


def test_a_finding_card_keeps_its_levels(record):
    """c1's card, in OVERVIEW-V2's levels: Understand (its standing), then Examine (reading, declared criterion, what it
    rests on and what rests on it, in that order and unfolded), then "Source details", folded, every receipt row rb
    gives drawn inside it. c2, which a call waits on, ends in Decide."""
    page = drawn_or_pending(record, "page", "owner", "R1")
    start = page.index(' id="c-c1"')
    end = page.index("</details></div>", start)
    c1 = page[start:end]
    marks = ['data-level="understand"', 'class="stand"', 'data-level="examine"', 'id="c-c1:reading"', 'id="c-c1:rule"', 'id="c-c1:runs"',
             'id="c-c1:impact"', '<details class="fold lv src" data-level="trace" data-fold="c-c1:source"><summary>Source details</summary>']
    at = [c1.index(m) for m in marks]
    assert at == sorted(at), dict(zip(marks, at))
    assert "<details" not in c1[:at[-1]]
    c2 = page[page.index(' id="c-c2"'):]
    c2 = c2[:c2.index('<div class="card')] if '<div class="card' in c2 else c2
    assert c2.index('data-level="trace"') < c2.index('data-level="decide" id="c-c2:decide"'), "Decide comes after Source details"
    doc = example("owner")
    text = said(html.unescape(re.sub(r"<[^>]+>", "", page[start:page.index("</div>", end + 10)])))
    for row in doc["findings"]["c1"]["receipt_rows"]:
        assert said(row[1]) in text, row


# rb's rule phrases as a card says them (strings.rs plain_word, plain words pass 2)
PLAIN = {"criterion met": "rule met", "criterion not met": "rule not met", "criterion undetermined": "can't tell yet",
         "criterion untested": "not tested", "estimate meets criterion": "estimate meets the rule"}


# the state word a rule's verdict leads with (nodes.rs verdict), by rb's state
STATE_WORD = {"meets": "Met", "gate met": "Met", "misses": "Not met", "gate missed": "Not met", "reproduced": "Reproduced",
              "not reproduced": "Not reproduced", "undetermined": "Can't tell yet", "gate undetermined": "Can't tell yet",
              "untested": "Not tested", "gate untested": "Not tested"}


def said_count(text: str, phrase: str) -> int:
    """How often a state phrase is said, lowercased, as a phrase of its own: "met" not inside "not met" or "meets"."""
    pat = r"(?<![a-z'])" + ("" if phrase.lower().startswith("not ") else r"(?<!not )") + re.escape(phrase.lower()) + r"(?![a-z])"
    return len(re.findall(pat, text.lower()))


@pytest.mark.parametrize("surface", ["page", "export"])
def test_a_rule_card_says_its_verdict_once(record, surface):
    """A rule's card says its verdict once, under its title (FIX2): its state phrase is said once in the card's text,
    no state words follow the verdict, checks are a row of The rule as written, and its call box says no state."""
    tree = Tree(drawn_or_pending(record, surface, "owner", "R1"))
    doc = example("owner")
    for cid, f in doc["findings"].items():
        el = card(tree, f"c-{cid}")
        text = said(tree.text(el))
        word = STATE_WORD[f["state"]]
        assert said_count(text, word) == 1, (cid, word, text)
        assert not select(tree, f"#c-{cid} p.sti"), cid
        rule = tree.by_id(f"c-{cid}:rule")
        assert not f["says"].get("integrity") or "checks" in said(tree.text(rule)), cid
        for part in [p for p in [tree.by_id(f"c-{cid}:decide")] if p is not None]:
            for w in set(STATE_WORD.values()):
                assert said_count(said(tree.text(part)), w) == 0, (cid, w, said(tree.text(part)))


def test_numbers_and_standing_drill(record):
    """Every reading's number on a finding card is a link, and its verdict leads with the rule's state word; each
    observation's value and run drill to the run's receipt."""
    tree = Tree(drawn_or_pending(record, "page", "owner", "R1"))
    doc = example("owner")
    links = {(e["attrs"]["href"], said(tree.text(e))) for e in select(tree, 'a[href^="#"]')}
    for cid, f in doc["findings"].items():
        if f["reading"]["estimate_text"]:
            assert any(f["reading"]["estimate_text"] in t for h, t in links if h == f"#c-{cid}:runs"), cid
        # the verdict leads with the rule's state word, once (FIX2); its numbers drill (above)
        assert said(tree.text(select(tree, f"#c-{cid} p.str")[0])).startswith(STATE_WORD[f["state"]] + ":"), cid
        for o in f["observations"]:
            # the value as the card says it (two decimals, more where two would read alike; DESIGN-PASS-1 P5), rb's own
            # digits on hover, drilling to the run
            said_as = [t for h, t in links if h == f"#run-{o['run']}" and re.fullmatch(r"[+-]?\d+(\.\d+)?", t)]
            assert any(abs(float(t) - float(o["value_text"])) <= 0.5 * 10 ** -len((t.split(".") + [""])[1]) + 1e-12 for t in said_as), (cid, o["run"], said_as)
            assert any(o["run"] in t for h, t in links if h == f"#run-{o['run']}"), (cid, o["run"])


def test_a_page_names_no_place_platform_or_writer(record):
    """The Live example's cards on rb open name where runs were recorded and the machine's platform; the Owner
    example's cards on rb page name none of that, and no card names a writer."""
    page = drawn_or_pending(record, "page", "owner", "R1")
    # the page's markup and words: the inlined script's own source names the attributes it reads (data-place), no place
    markup = re.sub(r"<script>.*?</script>", "", page, flags=re.S)
    for leak in ["exp-b", ".worktrees", "Linux-6.8.0", "data-place", "human:ana", "agent:", "session"]:
        assert leak not in markup, leak
    script = page.split("<script>", 1)[1].split("</script>", 1)[0] if "<script>" in page else ""
    for leak in ["exp-b", ".worktrees", "Linux-6.8.0", "human:ana", "agent:"]:
        assert leak not in script, leak


def as_viewer(page: str, viewer: str) -> Tree:
    """The export as `viewer` ("none", "owner" or "reader") reads it (OWNER-READER): every element marked for other
    viewers (`data-vw`) left out, as shell.css hides it."""
    tree = Tree(page)

    def prune(el):
        el["kids"] = [k for k in el["kids"] if viewer in k["attrs"].get("data-vw", viewer).split()]
        kept = {id(k) for k in el["kids"]}
        el["seq"] = [k for k in el["seq"] if isinstance(k, str) or id(k) in kept]
        for k in el["kids"]:
            prune(k)
    prune(tree.root)
    return tree


def test_an_export_offers_no_control(record):
    """rb export runs nothing: with no viewer named, each call card lists its actions with their lines and what the
    review will read, and no button or field a script would drive. The hub's working view of the record's owner has the
    composer (OWNER-READER); a reader of a shared link is offered no action at all, no rb command, and still reads why
    each call is open."""
    page = drawn_or_pending(record, "export", "owner", "R1")
    doc = example("owner")
    # the cards: each call's own, and each finding a call waits on (the views around them are R's)
    ids = [f"call-{c['subject']}" for c in doc["calls"]] + [f"c-{c['subject']}" for c in doc["calls"] if c["subject"] in doc["findings"]]
    for viewer in ["none", "owner", "reader"]:
        tree = as_viewer(page, viewer)
        cards = [card(tree, i) for i in ids]
        text = said(" ".join(tree.text(c) for c in cards))
        tags = {e["tag"] for c in cards for e in tree.all(c)}
        assert ("button" in tags and "input" in tags) == (viewer == "owner"), viewer
        for c in doc["calls"]:
            assert said(c["title"]) in text, (viewer, c["title"])
            for a in c["actions"]:
                assert (said(a["line"]) in text and said(a["consequence"]) in text) == (viewer != "reader"), (viewer, a["line"])
        assert ("Record a call" in text) == (viewer != "reader"), viewer
        assert ("rb decide" in text) == (viewer != "reader"), viewer
        assert ("Your call" in text) == (viewer == "owner"), viewer
        assert ("Still open" in text) == (viewer == "reader"), viewer
    # with no viewer named the whole export, cards and views, holds no button or field: the composer is the owner's
    whole = as_viewer(page, "none")
    assert not {"button", "input"} & {e["tag"] for e in whole.all()}
    # with no viewer named the export is as it was: the composer is drawn for the owner alone
    for m in re.finditer(r'<(?:div|button|input)[^>]*class="(?:seg|composer)"[^>]*>', page):
        assert 'data-vw="owner"' in m.group(0), m.group(0)


def _cited_export(record, tmp_path, doc: dict) -> str:
    """page.html of rb export drawn from `doc`, a document with citations."""
    model = tmp_path / "cited.view.json"
    model.write_text(json.dumps(doc), encoding="utf-8")
    r = rb(record, "export", "--out", str(tmp_path / "export"), RB_TEST_SHELL="1", RB_TEST_SHELL_MODEL=str(model))
    assert r.returncode == 0, r.stderr
    return (tmp_path / "export" / "page.html").read_text(encoding="utf-8")


def test_a_citation_opens_its_source_view_on_an_export(record, tmp_path):
    """CITE item 3 on a real rb export, from a document with M's citations (CITE-FIELDS.md): a rule's statement draws
    README.md:3 as a chip that leads to its Source view, which quotes the cited lines with their numbers and the commit
    they were read at; a log line a shared page may not quote shows its file and line and says so, with no line of it."""
    doc = example("owner")
    line = lambda n, on: {"n": n, "text": f"line {n} of the README", "cited": on}
    base = {"raw": [], "commit": "6adf8a5c0ffee00000000000000000000000000a", "commit_short": "6adf8a5", "read": "read at commit 6adf8a5",
            "sha256": "7029e0be441faa31bb434075899b69a46f0c607ff2e05ff232b06a971ad19e63", "tracked": True, "cut": 0, "missing": "",
            "open_url": "", "cited_by": [{"kind": "criterion", "id": "c1"}]}
    doc["citations"] = {
        "README.md:3": {**base, "key": "README.md:3", "raw": ["README.md:3"], "path": "README.md", "name": "README.md", "folder": "",
                        "line": 3, "end": 3, "label": "README.md \u00b7 line 3", "quoted": True, "not_quoted": "",
                        "lines": [line(2, False), line(3, True), line(4, False)]},
        "logs/agent.log:669": {**base, "key": "logs/agent.log:669", "raw": ["logs/agent.log:669"], "path": "logs/agent.log", "name": "agent.log",
                               "folder": "logs/", "line": 669, "end": 669, "label": "logs/agent.log \u00b7 line 669", "quoted": False,
                               "lines": [], "read": "", "sha256": "", "tracked": False, "not_quoted": "Not quoted on a shared page"},
    }
    doc["findings"]["c1"]["statement"] = "int8 holds top1 within 1 point of fp32, as README.md:3 says (see logs/agent.log:669)"
    page = _cited_export(record, tmp_path, doc)
    tree = Tree(page)
    c1 = card(tree, "c-c1")
    chips = [a for a in tree.all(c1) if a["tag"] == "a" and "cite" in a["attrs"].get("class", "").split()]
    assert [a["attrs"]["href"] for a in chips] == ["#cite-README.md:3", "#cite-logs/agent.log:669"], chips
    src = card(tree, "cite-README.md:3")
    assert src["attrs"]["data-kind"] == "source"
    text = said(tree.text(src))
    assert "README.md · line 3" in text and "3 ▸ line 3 of the README" in text and "2 line 2 of the README" in text, text
    assert "read at commit 6adf8a5" in text and "Rule int8 holds top1" in text, text
    log = said(tree.text(card(tree, "cite-logs/agent.log:669")))
    assert "agent.log · line 669" in log and "Not quoted on a shared page" in log and "of the README" not in log, log


def test_every_link_in_a_card_lands(record):
    """Each link a card draws lands on an element of the page: another card, a part of one, or a view (the question
    and experiment views come with R3 and R4)."""
    needs("R3", "R4")
    page = drawn_or_pending(record, "page", "owner", "R1")
    tree = Tree(page)
    ids = {e["attrs"].get("id") for e in tree.all()}
    region = page[page.index('<div class="nodes">'):]
    dead = sorted({h for h in re.findall(r'href="#([^"&?]+)', region) if h not in ids})
    assert not dead, dead[:20]


# A page printed in Chrome with print media emulated: how many card receipts there are, how many print, how many of those
# are folds left closed, and how many composer controls print (rb's own script never runs here: the probe only reads).
PRINT_PROBE = r"""// node printcheck.mjs <chrome> <page.html> : prints JSON {rx_total, rx_shown, seg_shown, closed}
import { spawn } from 'node:child_process';
import { mkdtempSync, readFileSync, existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
const [chrome, file] = process.argv.slice(2);
const dir = mkdtempSync(join(tmpdir(), 'rbprint-'));
const proc = spawn(chrome, ['--headless=new', '--no-sandbox', '--disable-gpu', '--remote-debugging-port=0', `--user-data-dir=${dir}`, 'about:blank'], { stdio: 'ignore' });
const portFile = join(dir, 'DevToolsActivePort');
const t0 = Date.now();
while (!existsSync(portFile) || !readFileSync(portFile, 'utf8').includes('\n')) { if (Date.now() - t0 > 30000) { proc.kill(); throw new Error('no chrome'); } await new Promise(r => setTimeout(r, 100)); }
const port = readFileSync(portFile, 'utf8').split('\n')[0];
const targets = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
const ws = new WebSocket(targets.find(t => t.type === 'page').webSocketDebuggerUrl);
let id = 0; const wait = new Map();
ws.onmessage = m => { const d = JSON.parse(m.data); if (d.id && wait.has(d.id)) { wait.get(d.id)(d); wait.delete(d.id); } };
await new Promise(r => ws.onopen = r);
const send = (method, params = {}) => new Promise(r => { const i = ++id; wait.set(i, r); ws.send(JSON.stringify({ id: i, method, params })); });
await send('Page.enable');
await send('Page.navigate', { url: 'file://' + file });
await new Promise(r => setTimeout(r, 1500));
await send('Emulation.setEmulatedMedia', { media: 'print' });
const res = await send('Runtime.evaluate', { returnByValue: true, expression: `(() => {
  const rx = [...document.querySelectorAll('.card details.src > dl.rx')];
  const shown = rx.filter(e => e.checkVisibility());
  const seg = [...document.querySelectorAll('.card .seg, .card .composer')].filter(e => e.checkVisibility());
  // every fold of a card's Trace level (Source details, Reproduce this run) prints its first part, though closed
  const trace = [...document.querySelectorAll('.card details[data-level="trace"]')];
  const traced = trace.filter(d => !d.open && [...d.children].some(e => e.tagName !== 'SUMMARY' && e.checkVisibility()));
  return { rx_total: rx.length, rx_shown: shown.length, seg_shown: seg.length, closed: rx.filter(e => !e.parentElement.open).length,
           trace: trace.length, trace_printed: traced.length };
})()` });
console.log(JSON.stringify(res.result.result.value));
ws.close(); proc.kill();
"""


@pytest.mark.parametrize("surface", ["page", "export"])
def test_a_fold_prints(record, surface, tmp_path):
    """Printed, every card's receipts show though their folds stay closed, and no control a script drives prints."""
    page = drawn_or_pending(record, surface, "owner", "R1")
    chrome = shutil.which("google-chrome") or shutil.which("chromium") or os.environ.get("RB_TEST_CHROME")
    node = shutil.which("node")
    if not chrome or not node:
        pytest.skip("needs Chrome and node to print")
    (tmp_path / "page.html").write_text(page, encoding="utf-8")
    (tmp_path / "probe.mjs").write_text(PRINT_PROBE, encoding="utf-8")
    out = subprocess.run([node, str(tmp_path / "probe.mjs"), chrome, str(tmp_path / "page.html")], capture_output=True, encoding="utf-8",
                         timeout=120)
    assert out.returncode == 0, out.stderr
    seen = json.loads(out.stdout.strip().splitlines()[-1])
    ex = example("owner")
    # each card with Source details: findings, runs, resources, analyses, and each citation's Source view (CITE)
    want = sum(len(ex.get(k) or {}) for k in ("findings", "runs", "resources", "analyses", "citations"))
    assert seen["trace"] > want and seen["trace_printed"] == seen["trace"], seen
    del seen["trace"], seen["trace_printed"]
    assert seen == {"rx_total": want, "rx_shown": want, "seg_shown": 0, "closed": want}, seen


WRAP_PROBE = PRINT_PROBE.split("await send('Page.enable');")[0] + r"""await send('Page.enable');
await send('Emulation.setDeviceMetricsOverride', { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false });
await send('Page.navigate', { url: 'file://' + file + '#run-runs1' });
await new Promise(r => setTimeout(r, 1500));
const res = await send('Runtime.evaluate', { returnByValue: true, expression: `(() => {
  const card = [...document.querySelectorAll('.ibody .card.run')].find(e => e.checkVisibility());
  if (!card) return { error: 'no run card in the inspector' };
  const part = card.querySelector('section.part[data-id$=":reproduce"]');
  if (!part) return { error: 'no Reproduce this run' };
  // folded under "Reproduce this run": opened, as a reader opens it
  part.closest('details').open = true;
  const width = part.getBoundingClientRect().width;
  const lines = [...part.children].flatMap(e => e.tagName === 'UL' ? [...e.children] : [e]).filter(e => /^(P|LI)$/.test(e.tagName));
  return { width, lines: lines.map(e => {
    const r = document.createRange(); r.selectNodeContents(e);
    const tops = new Set([...r.getClientRects()].filter(b => b.width > 0).map(b => Math.round(b.top)));
    return { text: e.textContent.trim(), rows: tops.size, width: e.getBoundingClientRect().width };
  }) };
})()` });
console.log(JSON.stringify(res.result.result.value));
ws.close(); proc.kill();
"""


def test_a_run_cards_reproduce_lines_take_the_cards_width(record, tmp_path):
    """In the inspector, each line of a run card's "Reproduce this run" runs the card's full width, never a column a
    few characters wide ("ok · co / m / mi / t")."""
    page = drawn_or_pending(record, "page", "owner", "R1")
    chrome = shutil.which("google-chrome") or shutil.which("chromium") or os.environ.get("RB_TEST_CHROME")
    node = shutil.which("node")
    if not chrome or not node:
        pytest.skip("needs Chrome and node to lay the page out")
    (tmp_path / "page.html").write_text(page, encoding="utf-8")
    (tmp_path / "probe.mjs").write_text(WRAP_PROBE, encoding="utf-8")
    out = subprocess.run([node, str(tmp_path / "probe.mjs"), chrome, str(tmp_path / "page.html")], capture_output=True, encoding="utf-8",
                         timeout=120)
    assert out.returncode == 0, out.stderr
    seen = json.loads(out.stdout.strip().splitlines()[-1])
    assert "error" not in seen, seen
    assert len(seen["lines"]) >= 6, seen  # the ready line, its four parts, what the run made
    for ln in seen["lines"]:
        assert ln["width"] >= seen["width"] - 1, (ln, seen["width"])
        # a line of about 90 characters in a card some 400 pixels wide takes two or three rows, never one a word
        assert ln["rows"] <= 1 + len(ln["text"]) * 8 // seen["width"], (ln, seen["width"])


# The run card of the INT8 demo's seed-1 run (its command recorded, what it made got again by a recipe) in the
# inspector, at a width and a colour scheme: its command (as given) and how rb knows it, the Copy buttons its Reproduce
# part holds, the recipe box's scroll and cue, and how often the command is said outside the recipe. With a fourth
# argument, a shot of the inspector goes there.
RUN_CARD_PROBE = PRINT_PROBE.split("await send('Page.enable');")[0] + r"""await send('Page.enable');
const [width, scheme, shot] = process.argv.slice(4);
await send('Emulation.setDeviceMetricsOverride', { width: +width, height: 900, deviceScaleFactor: 1, mobile: +width < 640 });
await send('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-color-scheme', value: scheme }] });
await send('Page.navigate', { url: 'file://' + file });
await new Promise(r => setTimeout(r, 1500));
const find = await send('Runtime.evaluate', { returnByValue: true, expression: `(() => {
  const c = [...document.querySelectorAll('.card.run')].find(e => e.querySelector('.asgiven code') && e.querySelector('[id$=":recipe"]'));
  return c ? c.id : '';
})()` });
const rid = find.result.result.value;
if (!rid) { console.log(JSON.stringify({ error: 'no run card with a command and a recipe' })); ws.close(); proc.kill(); process.exit(0); }
await send('Page.navigate', { url: 'file://' + file + '#' + rid });
await new Promise(r => setTimeout(r, 1500));
await send('Runtime.evaluate', { expression: `(() => {
  const c = [...document.querySelectorAll('.ibody .card.run')].find(e => e.checkVisibility());
  // the command is in Source details and the recipe under Reproduce this run: both folded, opened as a reader opens them
  if (c) c.querySelectorAll('details[data-level="trace"]').forEach(d => { d.open = true; });
  const p = c && c.querySelector('.asgiven'); if (p) p.scrollIntoView({ block: 'center' });
})()` });
await new Promise(r => setTimeout(r, 300));
const res = await send('Runtime.evaluate', { returnByValue: true, expression: `(() => {
  const card = [...document.querySelectorAll('.ibody .card.run')].find(e => e.checkVisibility());
  if (!card) return { error: 'no run card in the inspector' };
  const box = (e) => { const b = e.getBoundingClientRect(); return { left: b.left, top: b.top, right: b.right, bottom: b.bottom }; };
  const ag = card.querySelector('.asgiven');
  const code = ag.querySelector('code');
  const kids = [...ag.children];
  const part = card.querySelector('section.part[data-id$=":reproduce"]');
  const pre = part.querySelector('pre');
  const cps = [...part.querySelectorAll('.cp')].filter(e => e.checkVisibility());
  const outside = card.cloneNode(true); outside.querySelector('pre')?.remove(); outside.querySelector('dl.rx')?.remove();
  const cs = getComputedStyle(pre);
  return {
    rid: ${JSON.stringify(rid)}, code: code.textContent, code_box: box(code),
    labels: kids.filter(e => e.classList.contains('k')).map(e => e.textContent.trim()),
    values: kids.filter(e => e.classList.contains('v')).map(e => ({ text: e.textContent.trim(), box: box(e) })),
    copies: cps.map(e => ({ text: e.textContent.trim(), box: box(e) })), pre_box: box(pre),
    lines: (() => { const r = document.createRange(); r.selectNodeContents(pre); return [...r.getClientRects()].filter(b => b.width > 0).map(b => ({ left: b.left, top: b.top, right: b.right, bottom: b.bottom })); })(),
    scrolls: pre.scrollWidth > pre.clientWidth, overflow: cs.overflowX, cue: cs.backgroundImage,
    said: outside.textContent.split(code.textContent).length - 1,
  };
})()` });
if (shot) {
  const img = await send('Page.captureScreenshot', { format: 'png' });
  (await import('node:fs')).writeFileSync(shot, Buffer.from(img.result.data, 'base64'));
}
console.log(JSON.stringify(res.result.result.value ?? { error: res.result.exceptionDetails || res.result }));
ws.close(); proc.kill();
"""


def _run_card_seen(request, tmp_path, width: int, scheme: str) -> dict:
    needs("M0", "R1")
    chrome = shutil.which("google-chrome") or shutil.which("chromium") or os.environ.get("RB_TEST_CHROME")
    node = shutil.which("node")
    if not chrome or not node:
        pytest.skip("needs Chrome and node to lay the page out")
    from rbtest import command
    demo = request.getfixturevalue("int8_demo")
    page = tmp_path / "page.html"
    if not page.exists():
        r = subprocess.run([*command(), "page", "--out", str(page)], cwd=demo.root, env={**demo.env, "RB_TEST_SHELL": "1"}, capture_output=True,
                           encoding="utf-8", stdin=subprocess.DEVNULL, timeout=600)
        assert r.returncode == 0, r.stderr
    (tmp_path / "probe.mjs").write_text(RUN_CARD_PROBE, encoding="utf-8")
    shots = os.environ.get("RB_TEST_SHOTS")
    shot = [str(Path(shots) / f"run-card-{width}-{scheme}.png")] if shots else []
    out = subprocess.run([node, str(tmp_path / "probe.mjs"), chrome, str(page), str(width), scheme, *shot], capture_output=True,
                         encoding="utf-8", timeout=180)
    assert out.returncode == 0, out.stderr
    seen = json.loads(out.stdout.strip().splitlines()[-1])
    assert "error" not in seen, seen
    return seen


@pytest.mark.parametrize("width", [390, 1280])
@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_a_run_cards_command_and_recipe(request, tmp_path, width, scheme):
    """A run card in the inspector: its command (as given) is the code alone, how rb knows it a row under a label of
    its own (never "--seed 1as given"); the card says the command once outside its recipe; the recipe has one Copy, in
    the row above it, which covers none of its lines; and the recipe scrolls sideways in its box."""
    seen = _run_card_seen(request, tmp_path, width, scheme)
    assert seen["labels"] in (["command (as given)", "how rb knows"], ["command given later, not observed", "how rb knows"]), seen
    assert seen["values"][0]["text"] == seen["code"] and seen["values"][1]["text"], seen
    assert seen["values"][1]["box"]["top"] >= seen["values"][0]["box"]["bottom"] - 1, "how rb knows sits on the command's line"
    assert seen["said"] == 1, seen
    assert [c["text"] for c in seen["copies"]] == ["Copy recipe"], seen
    cp = seen["copies"][0]["box"]
    for ln in seen["lines"]:
        assert cp["bottom"] <= ln["top"] or cp["top"] >= ln["bottom"] or cp["right"] <= ln["left"] or cp["left"] >= ln["right"], (cp, ln)
    assert seen["overflow"] == "auto" and seen["scrolls"], seen


@pytest.mark.parametrize("width", [390, 1280])
def test_a_run_cards_command_is_never_squeezed_by_its_label(request, tmp_path, width):
    """A long label ("command given later, not observed") wraps beside the command, never leaving the command a column
    a few characters wide in the inspector (the int8-cifar demo's run card, Overview v2's shots)."""
    seen = _run_card_seen(request, tmp_path, width, "light")
    code, recipe = seen["values"][0]["box"], seen["pre_box"]
    if code["right"] - code["left"] < 0.5 * (recipe["right"] - recipe["left"]):
        # laid out as it renders, whatever the CSS says: expected to fail until R's shell.css caps the label column
        css = (Path(__file__).parent.parent / "rust" / "src" / "shell" / "assets" / "shell.css").read_text(encoding="utf-8")
        if not re.search(r"\.asgiven\s*\{[^}]*fit-content", css):
            pytest.xfail("waits on R's shell.css: .asgiven's label column capped (C's request, Overview v2)")
        raise AssertionError(seen)


@pytest.mark.parametrize("width", [390, 1280])
def test_a_run_cards_recipe_scrolls_with_a_cue(request, tmp_path, width):
    """A recipe cut at the right says so: its box draws the shadow a table that scrolls draws (shell.css's .wrap)."""
    css = (Path(__file__).parent.parent / "rust" / "src" / "shell" / "assets" / "shell.css").read_text(encoding="utf-8")
    if not re.search(r"\.cmd[^{]*\{[^}]*local", css):
        pytest.skip("waits on R's shell.css: a scroll cue on .cmd (C's request, Polish 1)")
    seen = _run_card_seen(request, tmp_path, width, "light")
    assert seen["scrolls"] and "gradient" in seen["cue"], seen


# A card in the inspector, opened by its address at a width and a colour scheme: the order of its levels, whether its
# Source details fold is shut, and whether its Examine and Decide levels show. The first criterion card a call waits on
# is the one opened (else the first criterion card). With a fourth argument, a shot of the screen goes there.
INSPECT_PROBE = PRINT_PROBE.split("await send('Page.enable');")[0] + r"""await send('Page.enable');
const [width, scheme, shot] = process.argv.slice(4);
await send('Emulation.setDeviceMetricsOverride', { width: +width, height: 900, deviceScaleFactor: 1, mobile: +width < 640 });
await send('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-color-scheme', value: scheme }] });
await send('Page.navigate', { url: 'file://' + file });
await new Promise(r => setTimeout(r, 1500));
const find = await send('Runtime.evaluate', { returnByValue: true, expression: `(() => {
  const cs = [...document.querySelectorAll('.nodes .card.crit')];
  const c = cs.find(e => e.querySelector('[data-level="decide"]')) || cs[0];
  return c ? c.id : '';
})()` });
const cid = find.result.result.value;
if (!cid) { console.log(JSON.stringify({ error: 'no criterion card' })); ws.close(); proc.kill(); process.exit(0); }
await send('Page.navigate', { url: 'file://' + file + '#' + cid });
await new Promise(r => setTimeout(r, 1500));
const res = await send('Runtime.evaluate', { returnByValue: true, expression: `(() => {
  const card = [...document.querySelectorAll('.ibody .card.crit')].find(e => e.checkVisibility());
  if (!card) return { error: 'no criterion card in the inspector' };
  const levels = [...card.querySelectorAll('[data-level]')].map(e => e.dataset.level);
  const src = card.querySelector('details[data-level="trace"]');
  const dl = src && src.querySelector('dl.rx');
  const ex = card.querySelector('[data-level="examine"]');
  const dec = card.querySelector('[data-level="decide"]');
  return { cid: ${JSON.stringify(cid)}, levels, src_open: src ? src.open : null, src_summary: src ? src.querySelector('summary').textContent : '',
           receipts_shown: dl ? dl.checkVisibility() : null, examine_shown: !!ex && ex.checkVisibility(),
           decide: !!dec, decide_shown: !!dec && dec.checkVisibility() };
})()` });
if (shot) {
  const fs = await import('node:fs');
  let img = await send('Page.captureScreenshot', { format: 'png' });
  fs.writeFileSync(shot, Buffer.from(img.result.data, 'base64'));
  // and its lower levels: Source details, shut, and Decide
  await send('Runtime.evaluate', { expression: `(() => {
    const card = [...document.querySelectorAll('.ibody .card.crit')].find(e => e.checkVisibility());
    const src = card && card.querySelector('details[data-level="trace"]'); if (src) src.scrollIntoView({ block: 'start' });
  })()` });
  await new Promise(r => setTimeout(r, 300));
  img = await send('Page.captureScreenshot', { format: 'png' });
  fs.writeFileSync(shot.replace(/\.png$/, '-lower.png'), Buffer.from(img.result.data, 'base64'));
}
console.log(JSON.stringify(res.result.result.value ?? { error: res.result.exceptionDetails || res.result }));
ws.close(); proc.kill();
"""


@pytest.mark.parametrize("width", [390, 1280])
@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_a_card_in_the_inspector_shows_its_levels(request, tmp_path, width, scheme):
    """A criterion card a call waits on, in the inspector (OVERVIEW-V2): Understand, Examine, then Source details shut
    (its receipts one click down, never opened for the reader), then Decide, showing."""
    needs("M0", "R1")
    chrome = shutil.which("google-chrome") or shutil.which("chromium") or os.environ.get("RB_TEST_CHROME")
    node = shutil.which("node")
    if not chrome or not node:
        pytest.skip("needs Chrome and node to lay the page out")
    from rbtest import command
    demo = request.getfixturevalue("int8_demo")
    page = tmp_path / "page.html"
    r = subprocess.run([*command(), "page", "--out", str(page)], cwd=demo.root, env={**demo.env, "RB_TEST_SHELL": "1"}, capture_output=True,
                       encoding="utf-8", stdin=subprocess.DEVNULL, timeout=600)
    assert r.returncode == 0, r.stderr
    (tmp_path / "probe.mjs").write_text(INSPECT_PROBE, encoding="utf-8")
    shots = os.environ.get("RB_TEST_SHOTS")
    shot = [str(Path(shots) / f"finding-card-{width}-{scheme}.png")] if shots else []
    out = subprocess.run([node, str(tmp_path / "probe.mjs"), chrome, str(page), str(width), scheme, *shot], capture_output=True,
                         encoding="utf-8", timeout=180)
    assert out.returncode == 0, out.stderr
    seen = json.loads(out.stdout.strip().splitlines()[-1])
    assert "error" not in seen, seen
    assert seen["levels"][:3] == ["understand", "examine", "trace"], seen
    assert seen["src_summary"] == "Source details" and seen["src_open"] is False and seen["receipts_shown"] is False, seen
    assert seen["examine_shown"], seen
    assert seen["decide"] and seen["decide_shown"] and seen["levels"][-1] == "decide", seen


# A card of rb open's page in the inspector, at a width and a colour scheme, with its folds opened as a reader opens
# them: what it offers to copy or run, and the text it says. With a fifth argument, a shot of the inspector goes there.
PAST_PROBE = PRINT_PROBE.split("await send('Page.enable');")[0] + r"""await send('Page.enable');
const [width, scheme, anchor, shot] = process.argv.slice(4);
await send('Emulation.setDeviceMetricsOverride', { width: +width, height: 900, deviceScaleFactor: 1, mobile: +width < 640 });
await send('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-color-scheme', value: scheme }] });
await send('Page.navigate', { url: 'file://' + file + '#' + anchor });
await new Promise(r => setTimeout(r, 1500));
const res = await send('Runtime.evaluate', { returnByValue: true, expression: `(() => {
  const card = [...document.querySelectorAll('.ibody .card')].find(e => e.checkVisibility());
  if (!card) return { error: 'no card in the inspector' };
  card.querySelectorAll('details').forEach(d => { d.open = true; });
  const rx = card.querySelector('[data-level="trace"]'); if (rx) rx.scrollIntoView({ block: 'start' });
  return { id: card.id || card.dataset.id, text: card.textContent, boxes: card.querySelectorAll('.cmdbox, pre.cmd, code, [data-copybtn], .cp').length };
})()` });
await new Promise(r => setTimeout(r, 300));
if (shot) {
  const img = await send('Page.captureScreenshot', { format: 'png' });
  (await import('node:fs')).writeFileSync(shot, Buffer.from(img.result.data, 'base64'));
}
console.log(JSON.stringify(res.result.result.value ?? { error: res.result.exceptionDetails || res.result }));
ws.close(); proc.kill();
"""

PAST_VIEW = Path(__file__).parent / "fixtures" / "shell" / "int8-demo.past.view.json"


def _served(request, tmp_path, monkeypatch, doc: dict) -> str:
    """What rb open serves at / for the INT8 demo when its document is `doc` (RB_TEST_SHELL_MODEL)."""
    from urllib.request import urlopen
    from rbtest import Open, as_the_user
    demo = request.getfixturevalue("int8_demo")
    for k, v in demo.env.items():
        monkeypatch.setenv(k, v)
    as_the_user(monkeypatch)
    model = tmp_path / f"model-{len(list(tmp_path.glob('model-*')))}.json"
    model.write_text(json.dumps(doc), encoding="utf-8")
    monkeypatch.setenv("RB_TEST_SHELL", "1")
    monkeypatch.setenv("RB_TEST_SHELL_MODEL", str(model))
    server = Open(demo.root)
    try:
        with urlopen(server.url + "/", timeout=60) as r:
            return r.read().decode("utf-8")
    finally:
        server.close()


def _cards(page: str) -> str:
    return page[page.index('<div class="nodes">'):page.index('<div class="inone"')]


@pytest.mark.parametrize("width", [390, 1280])
@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_a_past_versions_cards_offer_no_command(request, tmp_path, monkeypatch, width, scheme):
    """Guarantee 38 on the cards: rb open at a past version of the INT8 demo draws no command on any card, as a box to
    copy, as code or as words (a run's command and recipe, a resource's way to get it again), while the same document
    drawn live does; the inspector's run, resource and call cards hold none either."""
    needs("M0", "R1")
    past = json.loads(PAST_VIEW.read_text(encoding="utf-8"))
    live = json.loads(PAST_VIEW.read_text(encoding="utf-8"))
    live["places"]["place"]["version"] = None
    page = tmp_path / "past.html"
    if not page.exists():
        page.write_text(_served(request, tmp_path, monkeypatch, past), encoding="utf-8")
    marks = ['class="cmdbox', 'class="cmd', "data-copybtn", "<code>", "python get_data.py", "python quantize.py"]
    if width == 390 and scheme == "light":
        drawn = _cards(_served(request, tmp_path, monkeypatch, live))
        assert all(m in drawn for m in ['class="cmdbox', "data-copybtn", "python get_data.py"]), "drawn live, the cards offer their commands"
    cards = _cards(page.read_text(encoding="utf-8"))
    for m in marks:
        assert m not in cards, f"a past version's cards draw {m}"
    chrome = shutil.which("google-chrome") or shutil.which("chromium") or os.environ.get("RB_TEST_CHROME")
    node = shutil.which("node")
    if not chrome or not node:
        pytest.skip("needs Chrome and node to lay the page out")
    (tmp_path / "probe.mjs").write_text(PAST_PROBE, encoding="utf-8")
    shots = os.environ.get("RB_TEST_SHOTS")
    for anchor in ["run-runs1", "r-imagenet-val", "call-c2"]:
        shot = [str(Path(shots) / f"past-{anchor}-{width}-{scheme}.png")] if shots else []
        out = subprocess.run([node, str(tmp_path / "probe.mjs"), chrome, str(page), str(width), scheme, anchor, *shot], capture_output=True,
                             encoding="utf-8", timeout=180)
        assert out.returncode == 0, out.stderr
        seen = json.loads(out.stdout.strip().splitlines()[-1])
        assert "error" not in seen and seen["id"] == anchor, seen
        assert seen["boxes"] == 0 and not any(c in seen["text"] for c in ("get_data.py", "quantize.py", "train.py", "rb decide")), seen


def _glossary_id(term: str) -> str:
    """strings.rs glossary_id: g-, the term in lower case, each run of other characters one hyphen."""
    return "g-" + "-".join(w for w in re.split(r"[^a-z0-9]+", term.lower()) if w)


@pytest.mark.parametrize("surface", ["page", "export"])
def test_every_term_chip_on_a_real_page_carries_its_hover(int8_demo, tmp_path, surface):
    """Plain words pass 2, on the INT8 demo's real rb page and rb export: every kind chip on a card that names a word
    the page's glossary holds ("rule") carries rb's line for it on hover, described by its glossary entry, as does every
    term a card draws; and rb's rule phrases on a card's standing line, rule row, call and figure read in the screens'
    words (rule met, rule not met, can't tell yet, not tested), never criterion, criteria, untested or undetermined."""
    needs("M0", "R1")
    from rbtest import command
    if surface == "page":
        out = tmp_path / "page.html"
        args = ["page", "--out", str(out)]
    else:
        args = ["export", "--out", str(tmp_path / "export")]
        out = tmp_path / "export" / "page.html"
    r = subprocess.run([*command(), *args], cwd=int8_demo.root, env={**int8_demo.env, "RB_TEST_SHELL": "1"}, capture_output=True,
                       encoding="utf-8", stdin=subprocess.DEVNULL, timeout=600)
    assert r.returncode == 0, r.stderr
    page = out.read_text(encoding="utf-8")
    ids = set(re.findall(r' id="(g-[^"]+)"', page))
    assert "g-rule" in ids, "the glossary holds rule"
    cards = _cards(page)
    chips = re.findall(r'<span class="kind">(.*?)</span>(?=<span class="(?:id|lab)"|</div>|<span class="dim)', cards)
    named = [c for c in chips if _glossary_id(html.unescape(re.sub(r"<[^>]+>", "", c))) in ids]
    assert len([c for c in named if re.sub(r"<[^>]+>", "", c) == "rule"]) >= 6, chips
    bare = [c for c in named if 'class="term" data-hint="' not in c]
    assert not bare, f"chips with no hover: {bare[:5]}"
    # the hover is said once a card: "rule" on its chip, not again on its rule row
    for c in re.findall(r'<div class="card crit[^"]*" id="c-[^"]+"', cards):
        one = cards.split(c, 1)[1].split('<div class="card ', 1)[0]
        assert one.count('aria-describedby="g-rule"') == 1, c
    for hint, described in re.findall(r'<span class="term" data-hint="([^"]*)" aria-describedby="([^"]*)"', cards):
        assert hint and described in ids, (hint, described)
    old = re.compile(r"\b(criterion|criteria|untested|undetermined)\b", re.I)
    said = re.findall(r'<p class="stand">(.*?)</p>', cards) + re.findall(r'<p class="reads">(.*?)</p>', cards)
    said += [dd.split('<span class="r2">')[0] for dd in re.findall(r'<dt>rule</dt><dd>(.*?)</dd>', cards)]
    said += re.findall(r'<svg class="fig"[^>]* aria-label="([^"]*)"', cards)
    assert len(said) > 10, len(said)
    # the lead and the standing words are rb's phrases; what follows a lead's colon is rb's own sentence (M's)
    leads = [html.unescape(re.sub(r"<[^>]+>", "", s)).split(": ")[0] for s in said]
    hits = [s for s in leads if old.search(s)]
    assert not hits, hits[:10]


@pytest.mark.parametrize("width", [390, 1440])
def test_a_source_views_lines_wrap_at_any_width(record, tmp_path, width):
    """A cited line of 300 characters wraps inside its Source view, on a phone and at a desk: never cut at the right
    edge of the card (the review of CITE item 3)."""
    from test_shell import _cdp_script, _node, _runner
    doc = example("owner")
    long = " ".join(["INT8 keeps top-1 within one point of FP32 on the ImageNet validation set"] * 4)
    doc["citations"] = {"README.md:3": {
        "key": "README.md:3", "raw": ["README.md:3"], "path": "README.md", "name": "README.md", "folder": "", "line": 3, "end": 3,
        "label": "README.md \u00b7 line 3", "commit": "6adf8a5", "commit_short": "6adf8a5", "read": "read at commit 6adf8a5", "sha256": "",
        "tracked": True, "quoted": True, "lines": [{"n": 3, "text": long, "cited": True}], "cut": 0, "missing": "", "not_quoted": "",
        "open_url": "", "cited_by": []}}
    page = tmp_path / "export" / "page.html"
    _cited_export(record, tmp_path, doc)
    script = _cdp_script(tmp_path / "wrap.mjs", f"""
await cdp.run(async () => {{
  const page = await (await cdp.launchChrome()).page();
  await page.viewport({width}, 900);
  await page.goto({json.dumps(page.as_uri() + "#cite-README.md:3")}); await cdp.sleep(500);
  const seen = await page.ev(`(() => {{ const p = document.getElementById('cite-README.md:3').querySelector('pre.srclines');
    const c = p.closest('.card').getBoundingClientRect(), b = p.getBoundingClientRect();
    return {{ scroll: p.scrollWidth, client: p.clientWidth, right: b.right, card: c.right }}; }})()`);
  await cdp.stopAll();
  return seen;
}}, {{ timeoutMs: 120000 }});
""")
    code, seen, err = _runner(_node(), script, env=dict(os.environ), timeout=180)
    assert code == 0 and "scroll" in seen, (seen, err[-2000:])
    assert seen["scroll"] <= seen["client"] + 1 and seen["right"] <= seen["card"] + 1, seen


def test_a_past_versions_source_view_draws_no_recorded_command(workdir, monkeypatch, tmp_path_factory):
    """Guarantee 38 on a real record: a resource's recipe cites the README line that holds its command, and rb open at
    the first version quotes no line of the file in the Source view, so never the command; drawn live it quotes it."""
    needs("M0", "R1", "CITE")
    from urllib.request import Request, urlopen
    from rbtest import Open, as_the_user, main
    as_the_user(monkeypatch)
    cfg = tmp_path_factory.mktemp("gitconfig") / "config"
    cfg.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(cfg))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    git = lambda *a: subprocess.run(["git", *a], check=True, capture_output=True, encoding="utf-8").stdout.strip()
    git("init", "-q")
    git("config", "user.email", "ana@example.org")
    git("config", "user.name", "ana")
    Path("README.md").write_text("# INT8 ResNet-50 on ImageNet\n\npython eval.py --seed 1\n", encoding="utf-8")
    git("add", "README.md")
    git("commit", "-qm", "README")
    assert main(["init", "INT8 ResNet-50 on ImageNet", "--no-agents"]) == 0
    assert main(["resource", "add", "imagenet-val", "--type", "dataset", "--name", "ImageNet-1k validation",
                 "--recipe", "python eval.py --seed 1 (README.md:3)"]) == 0
    git("add", "-A")
    git("commit", "-qm", "v1")
    first = git("rev-parse", "HEAD")
    assert main(["note", "add", "the validation set is the 50,000 images"]) == 0
    git("add", "-A")
    git("commit", "-qm", "v2")
    monkeypatch.setenv("RB_TEST_SHELL", "1")
    server = Open()
    try:
        def page(ask: str) -> str:
            with urlopen(Request(server.url + "/" + ask, headers={"Sec-Fetch-Site": "same-origin"}), timeout=180) as r:
                return _cards(r.read().decode("utf-8"))
        live, past = page(""), page("?at=" + first)
    finally:
        server.close()
    source = lambda cards: cards[cards.index('id="cite-README.md:3"'):][:4000]
    assert "python eval.py --seed 1" in source(live), source(live)
    assert "python eval.py --seed 1" not in past, source(past)
    assert 'class="srclines"' not in source(past) and "A past version quotes no line of a file" in source(past), source(past)
    assert "README.md" in source(past) and "line 3" in source(past), source(past)


def test_on_the_int8_demo_cards_name_runs_and_calls_as_history_does(int8_demo, tmp_path):
    """FIX9: on the INT8 demo's rb page, each run's card says what tells it apart from a run whose label reads the same
    exactly as History's row that records it does (the run and the one that replaced it read apart), and the cited
    file's card names the past call it rests on by what it is, as History's rows do, never by the id rb minted."""
    needs("M0", "R1")
    from rbtest import command
    page, dump = tmp_path / "page.html", tmp_path / "doc.json"
    r = subprocess.run([*command(), "page", "--out", str(page)], cwd=int8_demo.root, capture_output=True, encoding="utf-8",
                       env={**int8_demo.env, "RB_TEST_SHELL": "1", "RB_TEST_SHELL_DUMP": str(dump)}, stdin=subprocess.DEVNULL, timeout=600)
    assert r.returncode == 0, r.stderr
    doc, tree = json.loads(dump.read_text(encoding="utf-8")), Tree(page.read_text(encoding="utf-8"))
    apart = re.compile(r", recorded \d{4}-\d\d-\d\d \d\d:\d\d(?::\d\d)? UTC(?:, the \d+(?:st|nd|rd|th) of \d+)?")
    titles, told = {}, 0
    for row in (x for x in doc["history"]["log"] if x["op"] == "record"):
        rid = row["refs"][0]
        rest = row["sentence"].removeprefix(f"record run {doc['runs'][rid]['label']}")
        m = apart.match(rest)
        title = tree.text(next(e for e in tree.all(card(tree, f"run-{rid}")) if e["tag"] == "h3")).strip()
        titles[rid] = title
        if m:
            told += 1
            assert title.endswith(m.group(0)), (title, row["sentence"])
        else:
            assert ", recorded " not in title, (title, row["sentence"])
    assert told >= 2 and len(set(titles.values())) == len(titles), titles
    # the past call notes/2026-05-07.md:3 states, by what it is
    past = next(p for p in doc["pending"]["rows"] if p["kind"] == "past")
    source = " ".join(tree.text(card(tree, "cite-notes/2026-05-07.md:3")).split())
    assert f"A past call to continue {past['subject']} mentioned, not confirmed" in source, source
    assert past["id"] not in source, source
