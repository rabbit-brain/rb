"""MP-1111-0219's L1 and the lens past a finding's reading (f13h item 1), closed on every line rb draws (f13i item 1):
what git takes out never reads as if it had never been written.

The kinds of write and the surfaces come from rb itself, not from a list written by hand, so a kind rb gains is read:
- every write rb makes, as its command line declares them (each command that takes --dry-run) and the writes that
  take none (rb reconcile --apply, a design's confirmation): WRITES maps each to the cases that make it, and
  test_every_write_rb_makes_is_taken_out_by_a_case fails when rb gains one no case makes;
- every record rb show takes, as `rb schema ir` names the kinds: each row of rb status's data, each setting as
  <experiment>/<name>, each note; a kind read only within another (a variant, a finding, a relation) is placed in
  WITHIN, and test_every_kind_rb_names_is_read fails when rb gains a kind placed nowhere;
- every surface, read unit by unit from its data: rb show of each record (text, block by block under its first line,
  and --json), its card on rb ui, rb status --json and rb context --json (every row, Needs judgment and Can be done
  now, what it takes to run each experiment again, each design's standing), rb open's /state.json (the document the
  page and rb open draw), and rb ui's home. A unit is a JSON object's own values, or a block of rb show's lines.

For each kind of write, a record is built with it and committed but for that write, the write is taken out with
`git checkout -- .`, and every unit that reads otherwise afterwards, or newly, must name the take-out itself or in what
it sits under (its parent object, rb show's first line, a finding whose rule names it), or read undetermined; and every
--fail-on gate fails closed. Left out, by their keys: what says how git holds the record's own files (a checkout
changes them by itself), counts and tallies (said beside the count of what git took out), and the log's own lines (the
log on disk). The text of rb status, rb context and the page is read for each subject's state; the rest of their
lines are drawn from the data read above.

L1 fails on 085ffcf; MP-1111-0616's N1, N2 and N3 (a resource's reproduce line, a setting's view, a design's
confirmation) and 33 more kinds fail on ccd738a.
"""
import html
from collections import Counter
from html.parser import HTMLParser
import json
import re
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

import pytest

from rbtest import AGENT_VARS, Open, as_the_user, command, environment

pytestmark = pytest.mark.skipif(shutil.which("git") is None or sys.platform == "win32", reason="needs git and a POSIX shell")
GIT = shutil.which("git")

# what names the take-out, or reads undetermined
MARKS = ("taken out", "took out", "taken_out", "undetermined", "can't tell", "doctor --restore", "order unknown", "stash")
TIMES = [re.compile(p) for p in (r"\d{4}-\d\d-\d\d[T ][\d:.]+(\+00:00|Z)?( UTC)?", r"\d\d:\d\d UTC", r"\d+ (seconds?|minutes?|hours?) ago", r"just now")]


@pytest.fixture
def home(tmp_path, monkeypatch):
    as_the_user(monkeypatch)
    for k in AGENT_VARS:
        monkeypatch.delenv(k, raising=False)
    cfg = tmp_path / "gitconfig"
    cfg.write_text("[user]\n\temail = ana@example.org\n\tname = ana\n[init]\n\tdefaultBranch = main\n", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(cfg))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("RB_ACTOR", "human:ana")
    return tmp_path


def rb(root: Path, *argv: str, js: bool = False, agent: bool = False):
    env = environment()
    if agent:
        env.pop("RB_ACTOR", None)
        env.update({"CLAUDECODE": "1", "CLAUDE_CODE_SESSION_ID": "s-test"})
    r = subprocess.run([*command(), *argv, *(["--json"] if js else [])], cwd=root, env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL)
    if js:
        assert r.stdout, (argv, r.stderr)
        return json.loads(r.stdout)
    return r.stdout


def git(root: Path, *argv: str) -> None:
    subprocess.run([GIT, *argv], cwd=root, check=True, capture_output=True, text=True)


def last_id(root: Path, kind: str) -> str:
    return rb(root, "status", js=True)["data"][kind][-1]["id"]


def norm(s: str) -> str:
    for t in TIMES:
        s = t.sub("T", s)
    return " ".join(s.split())


def page_lines(path: Path) -> list:
    t = path.read_text(encoding="utf-8")
    t = re.sub(r"<script.*?</script>|<style.*?</style>", "", t, flags=re.S)
    t = re.sub(r"<(br|/p|/div|/li|/tr|/h\d|/section|/details|/summary|/dt|/dd|/td|/th|/header|/footer|/ul|/ol|/button)\b[^>]*>", "\n", t)
    return html.unescape(re.sub(r"<[^>]+>", "", t)).splitlines()


def leaves(node, out: list) -> list:
    """The sentences a screen draws from its data: every text with a space in it (codes and ids are not read)."""
    if isinstance(node, dict):
        for v in node.values():
            leaves(v, out)
    elif isinstance(node, list):
        for v in node:
            leaves(v, out)
    elif isinstance(node, str) and " " in node.strip():
        out.append(node)
    return out


STATES = ("planned", "open", "accepted", "rejected", "investigating", "parked", "stopped", "pivoted", "shipped", "answered", "dropped")
STATE_IN_LINE = re.compile(r"(?:· |\()(" + "|".join(STATES) + r")(?: ·|\)|$)")


def states_in(lines: list, names: dict) -> dict:
    """Each subject's states as the lines of a text surface say them: the state words beside its id or its title."""
    out: dict = {}
    for l in lines:
        found = STATE_IN_LINE.findall(l)
        took = any(m in l.lower() for m in MARKS)
        for sid, words in names.items():
            if any(re.search(r"(?<![\w.-])" + re.escape(w) + r"(?![\w.-])", l) for w in words) and (found or took):
                out.setdefault(sid, set()).update(found + (["(taken out)"] if took else []))
    return out


# --- every surface, from rb itself (f13i item 1) ---------------------------------------------------------------------
# A key whose value is a time, a digest or an address a server picked says nothing a take-out changes; every other key
# is read. The repository's own lines (how git holds the record's files, what rb found when it checked them) say the
# checkout, which a checkout changes by itself: they are read too, and are the one thing left out, by their keys.
VOLATILE = re.compile(r"(^|_)(at|sha256|hash|digest|stamp|seconds|elapsed|port|url|href|checked_at|made_at|when|since|ago|last_looked|looked)$")
CHECKOUT = {"place", "places", "repository", "checkout", "rb_delta", "scope", "page_says", "in_git", "evidence", "page_evidence", "record_files"}
# the lines that say how git holds the record's own files, which git's checkout changes by itself
CHECKOUT_LINE = re.compile(r"record files?\b|all record files committed|rb checked the record with git|check it yourself|git -C .* status|^(- )?Not committed:")
# lines that say no reading: the next commands, the log's own lines (Recent activity), and a label over the lines below
LABEL_LINE = re.compile(r"^(Next: |- T \w|experiment context: |On every \S+ criterion( below)?:$)")
# what says how many (records on disk, results of each kind, how each was recorded) and what is the log's own lines:
# the take-out is said once beside them, as the count of what git took out, and the records they count say it each
AGGREGATE = {"counts", "witness", "record_start", "results", "tally", "tally_on_estimate", "history", "facts"}
# the keys rb show's data and a card say what git took out of the record, or under it, before anything else
TOOK_KEYS = {".taken_out", ".taken_under"}
IDENT = ("id", "criterion", "code", "address", "part", "name", "key", "path", "subject", "kind")


def ident(node, i):
    if isinstance(node, dict):
        got = [str(node[k]) for k in IDENT if isinstance(node.get(k), (str, int)) and not isinstance(node.get(k), bool)]
        if got:
            return "/".join(got[:3])
    return str(i)


def json_units(node, path: str, out: dict) -> dict:
    """A JSON surface, unit by unit: each object's own scalars, at its path, list items named by what they are."""
    if isinstance(node, dict):
        own = {k: norm(v) if isinstance(v, str) else v for k, v in node.items()
               if not isinstance(v, (dict, list)) and not VOLATILE.search(k)}
        if own:
            out[path] = own
        for k, v in node.items():
            if isinstance(v, (dict, list)) and k not in CHECKOUT and not VOLATILE.search(k):
                json_units(v, f"{path}.{k}", out)
    elif isinstance(node, list):
        if node and all(not isinstance(x, (dict, list)) for x in node):
            out[path] = [norm(x) if isinstance(x, str) else x for x in node]
            return out
        for i, x in enumerate(node):
            json_units(x, f"{path}[{ident(x, i)}]", out)
    return out


def text_units(text: str, head: bool = False) -> list:
    """A text surface, block by block: a line with every line indented under it, with the lines it sits under (less
    indented, before it); for rb show also its first line, which says what git took out under the record before
    anything else."""
    rows = [(len(r) - len(r.lstrip()), norm(r)) for r in text.splitlines() if norm(r)]
    out: list = []
    for n, (ind, l) in enumerate(rows):
        if ind and not head:
            continue            # rb status's and rb context's blocks: a line at the margin with every line under it
        end = n + 1
        while end < len(rows) and rows[end][0] > ind:
            end += 1
        above, need = [], ind
        for i2, l2 in reversed(rows[:n]):
            if i2 < need:
                above.append(l2)
                need = i2
        if head and n:
            above.append(rows[0][1])
        out.append((" / ".join(l2 for _, l2 in rows[n:end]), above))
    return out


class PageText(HTMLParser):
    """A page's text, block by block: each block that holds no other (a row, a list item, a card), with the first text
    of every block it sits in (a card's or a section's head), as rb show's lines sit under its first."""
    BLOCKS = {"section", "article", "details", "div", "li", "tr", "table", "ul", "ol", "header", "main", "aside", "dl", "figure", "p", "summary", "dd", "dt"}
    VOID = {"br", "img", "input", "meta", "link", "hr", "wbr", "source", "col", "path", "circle", "rect", "line", "polyline"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack: list = []      # [tag, head, texts, holds a block]
        self.out: list = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1
        if tag in self.VOID:
            return
        if tag in self.BLOCKS:
            for f in self.stack:
                f[3] = True
        self.stack.append([tag, None, [], False])

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.skip = max(0, self.skip - 1)
        for n in range(len(self.stack) - 1, -1, -1):
            if self.stack[n][0] == tag:
                for f in reversed(self.stack[n:]):
                    if f[0] in self.BLOCKS and not f[3] and f[2]:
                        heads = [h for t, h, _, _ in self.stack[:self.stack.index(f)] if t in self.BLOCKS and h]
                        self.out.append((" ".join(f[2]), heads))
                del self.stack[n:]
                break

    def handle_data(self, data):
        t = norm(data)
        if self.skip or not t:
            return
        for frame in self.stack:
            frame[2].append(t)
            if frame[0] in self.BLOCKS and frame[1] is None:
                frame[1] = t


def page_units(path: Path) -> list:
    p = PageText()
    p.feed(path.read_text(encoding="utf-8"))
    return p.out


# every kind of record rb show takes, as `rb schema ir` names the kinds, and where each kind that is no record of its
# own is read: a new kind fails test_every_kind_rb_names_is_read until it is placed here
SHOWN = {"question": "questions", "experiment": "experiments", "criterion": "findings", "run": "runs", "analysis": "analyses",
         "resource": "resources", "metric": "metrics", "decision": "decisions", "note": "notes", "setting": "settings"}
WITHIN = {"variant": "rb show <experiment>", "comparison": "rb show <criterion>", "result": "rb show <run>",
          "finding": "rb show <criterion>", "relation": "rb show <experiment> and <criterion>"}


def record_ids(root: Path) -> list:
    """Every id rb show takes in this record: each row of rb status's data, each setting as <experiment>/<name> (a
    variant's own as <experiment>/<variant>.<name>), and each note."""
    st = rb(root, "status", js=True)["data"]
    ids = {o["id"] for k in ("runs", "analyses", "decisions", "experiments", "questions", "resources", "metrics") for o in st.get(k) or []
           if isinstance(o, dict) and o.get("id")}
    ids |= {f["criterion"] for f in st.get("findings") or [] if isinstance(f, dict) and f.get("criterion")}
    for e in st.get("experiments") or []:
        o = rb(root, "show", e["id"], js=True)["data"].get("object") or {}
        ids |= {f"{e['id']}/{x['name']}" for x in o.get("settings") or []}
        for v in o.get("variants") or []:
            ids |= {f"{e['id']}/{v['name']}.{x['name']}" for x in v.get("settings") or []}
        ids |= {n["id"] for n in o.get("notes") or [] if isinstance(n, dict) and n.get("id")}
    ids |= {n["id"] for n in st.get("notes") or [] if isinstance(n, dict) and n.get("id")}
    return sorted(ids)


def surfaces(root: Path, ids: list, names: dict) -> dict:
    """What every surface draws, unit by unit: rb show of every record (text and --json) and its card on rb ui; rb
    status and rb context (text and --json: every row, Needs judgment and Can be done now, what it takes to run each
    experiment again, each design's standing); the page; rb open's /state.json; rb ui's home."""
    from rbtest import home as ui_home, object_card
    out: dict = {}
    for i in ids:
        r = rb(root, "show", i, js=True)
        # a finding's lines sit under its rule, which names what git took out of what it reads (f13g)
        took = [("(its finding says what git took out)", [])] if marked((r.get("data") or {}).get("finding", {}).get("says")) else []
        out[f"rb show {i}"] = ("text", [(l, a + [t for t, _ in took]) for l, a in text_units(rb(root, "show", i), head=True)])
        out[f"rb show {i} --json"] = ("json", json_units(r.get("data"), "", {}))
        try:
            out[f"rb ui's card {i}"] = ("json", json_units(object_card(i, root), "", {}))
        except KeyError:
            pass
    for name in ("status", "context"):
        # their text is drawn from their data, read unit by unit below; the text is read for each subject's state
        text = rb(root, name)
        out[f"rb {name}"] = ("text", [(f"{sid}: " + " ".join(sorted(w)), []) for sid, w in states_in(text.splitlines(), names).items()])
        # and each line, with the lines it sits under: one that names a record whose rb show names what git took out
        # of it or under it must say so too (unsaid's `touched`)
        rows = [(len(r) - len(r.lstrip()), norm(r)) for r in text.splitlines() if norm(r)]
        lines, heading = [], None
        for n, (ind, l) in enumerate(rows):
            if l.startswith("#"):
                heading = l      # rb context's lines sit under their heading too
            above, need = [heading] if heading and heading != l else [], ind
            for i2, l2 in reversed(rows[:n]):
                if i2 < need:
                    above.append(l2)
                    need = i2
            lines.append((l, above))
        out[f"rb {name} lines"] = ("lines", lines)
        out[f"rb {name} --json"] = ("json", json_units(rb(root, name, js=True)["data"], "", {}))
    p = root.parent / f"{root.name}-page.html"
    rb(root, "page", "--out", str(p))
    out["the page"] = ("text", [(f"{sid}: " + " ".join(sorted(w)), []) for sid, w in states_in(page_lines(p), names).items()])
    p.unlink()
    server = Open(root)
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(server.url + "/state.json", timeout=60) as resp:
            doc = json.loads(resp.read().decode("utf-8"))
    finally:
        server.close()
    out["rb open /state.json"] = ("json", json_units(doc, "", {}))
    out["rb ui's home"] = ("json", json_units(ui_home(root), "", {}))
    return out


def marked(x) -> bool:
    t = json.dumps(x, ensure_ascii=False).lower() if not isinstance(x, str) else x.lower()
    return any(m in t for m in MARKS)


def touched(after: dict, ids: list) -> set:
    """The records whose rb show says first what git took out of them or under them."""
    out = set()
    for i in ids:
        units = after.get(f"rb show {i} --json", ("json", {}))[1]
        if any(marked(units[k]) for k in units if k.split("[")[0] in TOOK_KEYS) or marked((units.get("", {}) or {}).get("taken_under", "")):
            out.add(i)
    return out


def unsaid(before: dict, after: dict, ids: list = ()) -> list:
    """Each unit that reads otherwise after the take-out, or newly, and names it neither itself nor in what it sits
    under (a JSON object's parents, a line's less indented lines, rb show's first line, a page's blocks' heads), nor
    reads undetermined."""
    bad = []
    hit = touched(after, list(ids))
    names = re.compile(r"(?<![\w.-])(" + "|".join(re.escape(i) for i in sorted(hit, key=len, reverse=True)) + r")(?![\w-])") if hit else None
    for surface, (form, now) in after.items():
        was = before.get(surface, (form, {} if form == "json" else []))[1]
        if form == "lines":
            had = Counter(l for l, _ in was)
            seen_l: Counter = Counter()
            for line, above in now:
                seen_l[line] += 1
                if any(LABEL_LINE.search(a) and a.startswith("On every") for a in above):
                    continue     # a note said once for every criterion of an experiment, as each finding says it
                if seen_l[line] <= had[line] or not names or not names.search(line) or CHECKOUT_LINE.search(line) or LABEL_LINE.search(line) or marked(line) or any(marked(a) for a in above):
                    continue
                bad.append(f"{surface}: a line naming {names.search(line).group(1)}, new and unmarked: {line[:240]}")
            continue
        if form == "text":
            had = Counter(l for l, _ in was)
            seen: Counter = Counter()
            for line, above in now:
                seen[line] += 1
                if seen[line] <= had[line] or CHECKOUT_LINE.search(line) or marked(line) or any(marked(a) for a in above):
                    continue
                bad.append(f"{surface}: new line: {line[:240]}")
            continue
        whole = surface.startswith(("rb show", "rb ui's card")) and (any(marked(now[k]) for k in now if k.count(".") == 1 and k.split("[")[0] in TOOK_KEYS)
                                                                    or marked((now.get(".facts") or [""])[0]) or marked((now.get("", {}) or {}).get("taken_under", ""))
                                                                    or marked(now.get(".finding.says")))
        for key, val in now.items():
            if whole or was.get(key) == val or marked(val) or CHECKOUT_LINE.search(json.dumps(val, ensure_ascii=False)):
                continue
            if any(part.split("[")[0] in AGGREGATE or part.split("[")[0].endswith("_count") for part in key.split(".")):
                continue
            if key == ".gate" and isinstance(val, dict) and val.get("integrity"):
                continue    # every gate fails closed while a write stands taken out: test_every_gate_fails_closed
            parents = [key[:n] for n, c in enumerate(key) if c in ".[" and key[:n] in now]
            if any(marked(now[k]) for k in parents) or marked(now.get("", "")):
                continue
            bad.append(f"{surface} {key}: was {json.dumps(was.get(key), ensure_ascii=False)[:200]} · now {json.dumps(val, ensure_ascii=False)[:200]}")
    return bad


def base(home: Path) -> Path:
    """q1, int8 comparing fp32 and int8 with a per-run seed and lr from a config, c1 linked to q1 with an
    interpretation, a gate g1 that misses, a resource the runs evaluate on, and three seeds."""
    root = home / "base"
    root.mkdir()
    git(root, "init", "-q")
    (root / "train.yaml").write_text("lr: 0.1\nbatch: 32\n", encoding="utf-8")
    for argv in (
        ("init", "INT8 ResNet-50", "--no-agents"),
        ("question", "add", "Does INT8 cost accuracy?", "--id", "q1"),
        ("metric", "add", "top1", "--maximize"),
        ("experiment", "add", "INT8 against FP32", "--id", "int8", "-q", "q1", "--baseline", "fp32", "--candidate", "int8", "--varies", "precision"),
        ("spec", "set", "int8", "fp32.precision", "fp32", "--no-verify"),
        ("spec", "set", "int8", "int8.precision", "int8", "--no-verify"),
        ("spec", "set", "int8", "lr", "--source", "train.yaml#lr"),
        ("spec", "set", "int8", "seed", "--per-run"),
        ("spec", "set", "int8", "cuda", "12.1", "--no-verify"),
        ("resource", "add", "holdout", "--type", "dataset", "--name", "held-out images", "--url", "https://data.example.org/holdout"),
        ("criterion", "add", "INT8 loses at most one point", "-e", "int8", "-q", "q1", "--id", "c1", "--metric", "change.top1", "--at-least", "-1",
         "--min-n", "3", "--when-met", "INT8 stays within the accuracy budget"),
        ("criterion", "add", "INT8 gains a point", "-e", "int8", "--id", "g1", "--role", "gate", "--metric", "change.top1", "--at-least", "1"),
    ):
        rb(root, *argv, js=True)
    for n in (1, 2, 3):
        rb(root, "run", "add", "int8", f"int8.top1={75.6 + n * 0.01:.2f}", f"fp32.top1={76.0 + n * 0.01:.2f}", "--set", f"seed={n}",
           "--input", "holdout:eval", "--produced-at", f"2026-11-0{n}T10:00:00Z", js=True)
    git(root, "add", "-A")
    git(root, "commit", "-qm", "the base")
    return root


def call(root, *argv):
    return lambda: rb(root, "decide", *argv, js=True)["data"]["object"]["id"]


# each case: (what is committed beside the base, the write git takes out); each a function of the record's folder
CASES = {
    # L1: a call on a line of work, and a gate's settlement
    "a stop on the experiment, retracted": (lambda r: call(r, "int8", "stop", "--why", "enough")(), lambda r, d: rb(r, "retract", d, "--why", "not after all")),
    "a stop on the question, retracted": (lambda r: call(r, "q1", "stop", "--why", "enough")(), lambda r, d: rb(r, "retract", d, "--why", "not after all")),
    "an accept on the question, retracted": (lambda r: call(r, "q1", "accept", "--why", "answered")(), lambda r, d: rb(r, "retract", d, "--why", "not after all")),
    "a park that settles the gate, retracted": (lambda r: call(r, "int8", "park", "--why", "later", "--revisit", "next month")(), lambda r, d: rb(r, "retract", d, "--why", "no")),
    "a stop on the experiment": (None, lambda r, d: rb(r, "decide", "int8", "stop", "--why", "enough")),
    "an accept on the question": (None, lambda r, d: rb(r, "decide", "q1", "accept", "--why", "answered")),
    "an investigate on the criterion": (None, lambda r, d: rb(r, "decide", "c1", "investigate", "--why", "one more seed")),
    "a vouch for a setting": (None, lambda r, d: rb(r, "decide", "int8/lr", "accept", "--why", "read it off the config")),
    # what a finding's reading reads (f13g's lens)
    "a run": (None, lambda r, d: rb(r, "run", "add", "int8", "int8.top1=70.0", "fp32.top1=76.0", "--set", "seed=4", "--produced-at", "2026-11-04T10:00:00Z")),
    "an analysis": (None, lambda r, d: rb(r, "analysis", "add", "int8", "--compare", "int8/fp32:top1=-0.4[-0.6,-0.2]", "--method", "paired bootstrap over images")),
    "a run retracted": (None, lambda r, d: rb(r, "retract", last_id(r, "runs"), "--why", "bad")),
    "a measurement superseded": (None, lambda r, d: rb(r, "retract", last_id(r, "runs"), "--why", "timing bug", "--scope", "top1")),
    "a criterion's link revised": (None, lambda r, d: rb(r, "criterion", "file", "c1", "q1", "--when-met", "INT8 is within budget", "--why", "narrower words")),
    "a criterion's link removed": (None, lambda r, d: rb(r, "criterion", "file", "c1", "q1", "--remove", "--why", "no")),
    "a criterion retracted": (None, lambda r, d: rb(r, "retract", "c1", "--why", "no")),
    "a variant": (None, lambda r, d: rb(r, "variant", "add", "int8", "int4", "--role", "ablation")),
    "a setting": (None, lambda r, d: rb(r, "spec", "set", "int8", "optim.momentum", "0.9", "--no-verify")),
    "a setting's conflict": (None, lambda r, d: rb(r, "spec", "set", "int8", "lr", "0.2", "--source", "train.yaml#lr")),
    "a confound": (None, lambda r, d: (rb(r, "spec", "set", "int8", "fp32.batch", "64", "--no-verify"), rb(r, "spec", "set", "int8", "int8.batch", "32", "--no-verify"))),
    "a design factor": (None, lambda r, d: rb(r, "spec", "set", "int8", "seed", "--design-factor")),
    "a metric": (None, lambda r, d: rb(r, "metric", "add", "loss", "--minimize", "--alias", "ce")),
    "a metric retracted": (None, lambda r, d: rb(r, "retract", "top1", "--why", "no")),
    "an exposure": (None, lambda r, d: rb(r, "resource", "expose", "holdout", "inspected", "--at", "2020-01-01", "--why", "looked at cases")),
    "a question reworded": (None, lambda r, d: rb(r, "question", "reword", "q1", "Does INT8 cost top-1 accuracy?", "--why", "sharper")),
    "a freeze": (None, lambda r, d: rb(r, "freeze", "int8", "-m", "before more runs")),
    "a gate on the experiment": (None, lambda r, d: rb(r, "experiment", "gate", "int8", "--if-holds", "ship INT8", "--if-not", "keep FP32")),
    # lens.sh's kinds (MP-1111-0219), and the rest of what rb writes
    "a park on the experiment": (None, lambda r, d: rb(r, "decide", "int8", "park", "--why", "later", "--revisit", "next month")),
    "a past call": (None, lambda r, d: rb(r, "decide", "int8", "park", "--why", "parked then", "--past", "--when", "last week")),
    "a unit": (None, lambda r, d: rb(r, "spec", "set", "int8", "population.unit", "seed", "--no-verify")),
    "a repeat": (None, lambda r, d: rb(r, "spec", "set", "int8", "population.repeat", "seed", "--no-verify")),
    "a run that replaces another": (None, lambda r, d: rb(r, "run", "add", "int8", "int8.top1=75.9", "fp32.top1=76.0", "--set", "seed=3", "--produced-at", "2026-11-05T10:00:00Z",
                                                           "--replaces", last_id(r, "runs"), "--why", "fixed")),
    "an amended rule": (lambda r: rb(r, "freeze", "int8", "-m", "before more runs"),
                        lambda r, d: rb(r, "criterion", "add", "INT8 loses at most a tenth", "-e", "int8", "--id", "c2", "--metric", "change.top1", "--at-least", "-0.1", "--amend", "--why", "tighter")),
    "a resource retracted": (None, lambda r, d: rb(r, "retract", "holdout", "--why", "leaked")),
    "what the experiment varies": (None, lambda r, d: rb(r, "spec", "vary", "int8", "batch")),
    "a reverses relation": (lambda r: rb(r, "criterion", "add", "FP32 loses at most one point", "-e", "int8", "--id", "c2", "--metric", "change.top1", "--at-least", "-1"),
                            lambda r, d: rb(r, "criterion", "relate", "c1", "reverses", "c2", "--orientation", "same", "--why", "the same comparison")),
    "a note": (None, lambda r, d: rb(r, "note", "add", "the held-out set is not in the pretraining data", "-e", "int8")),
    "a question": (None, lambda r, d: rb(r, "question", "add", "Does INT8 cost latency?", "--id", "q2", "--parent", "q1")),
    "an experiment": (None, lambda r, d: rb(r, "experiment", "add", "INT4 against FP32", "--id", "int4", "-q", "q1", "--baseline", "fp32", "--candidate", "int4")),
    "a resource located": (None, lambda r, d: rb(r, "resource", "locate", "holdout", "s3", "s3://my-bucket/holdout")),
    "an experiment retracted": (None, lambda r, d: rb(r, "retract", "int8", "--why", "wrong setup")),
    # the f13h review's: a call on a criterion and a vouch, each retracted
    "an accept on the criterion, retracted": (lambda r: call(r, "c1", "accept", "--why", "within budget")(), lambda r, d: rb(r, "retract", d, "--why", "no")),
    "a vouch for a setting, retracted": (lambda r: call(r, "int8/lr", "accept", "--why", "read it off the config")(), lambda r, d: rb(r, "retract", d, "--why", "no")),
    # MP-1111-0616's (f13i): a setting's reject call taken out whole (N2), a resource's retraction under what it takes to
    # run the experiment again (N1, "a resource retracted" above), a design's confirmation, a log line alone (N3), and a
    # proposed call's confirmation; and the writes no case above made
    "a reject on a setting": (None, lambda r, d: rb(r, "decide", "int8/cuda", "reject", "--why", "not the cluster's image")),
    "a reject on the criterion": (None, lambda r, d: rb(r, "decide", "c1", "reject", "--why", "the wrong set")),
    "a design's confirmation": (lambda r: (rb(r, "experiment", "gate", "int8", "--if-holds", "ship INT8", agent=True), confirm_key(r, "int8"))[1],
                                lambda r, d: rb(r, "confirm", d)),
    "a proposed call's confirmation": (lambda r: rb(r, "decide", "int8", "park", "--why", "later", js=True, agent=True)["data"]["object"]["id"],
                                       lambda r, d: rb(r, "confirm", d)),
    "a project title": (None, lambda r, d: rb(r, "project", "title", "INT8 ResNet-50, held-out", "--why", "narrower")),
    "a question moved": (lambda r: rb(r, "question", "add", "Can we ship INT8?", "--id", "q0"), lambda r, d: rb(r, "question", "move", "q1", "--parent", "q0")),
    "an experiment filed": (lambda r: rb(r, "question", "add", "Can we ship INT8?", "--id", "q0"), lambda r, d: rb(r, "experiment", "file", "int8", "q0")),
    "an experiment related": (None, lambda r, d: rb(r, "experiment", "relate", "int8", "reproduces", "exp_01JABCDEFGHJKMNPQRSTVWXYZ0")),
    "a setting checked again": (lambda r: (r / "train.yaml").write_text("lr: 0.2\nbatch: 32\n", encoding="utf-8"), lambda r, d: rb(r, "spec", "verify", "int8")),
    "a resource": (None, lambda r, d: rb(r, "resource", "add", "calib", "--type", "dataset", "--name", "calibration images", "--url", "https://data.example.org/calib")),
    "a note retracted": (lambda r: rb(r, "note", "add", "a caveat", "-e", "int8", js=True)["data"]["object"]["id"], lambda r, d: rb(r, "retract", d, "--why", "wrong")),
    "a results folder kept by rb reconcile --apply": (lambda r: ((r / "results" / "int4").mkdir(parents=True), (r / "results" / "int4" / "README.md").write_text("# INT4\n\nTop-1 75.0.\n", encoding="utf-8")),
                                                      lambda r, d: rb(r, "reconcile", "--apply")),
}

# every write rb makes, by its command: rb's own list (the commands that take --dry-run, as rb's command line declares
# them) and the writes that take none, each with the cases that make it. rb init starts the record and rb remove
# takes it away, so neither is a write git can take out of one; rb doctor --restore, --adopt and --forget are repairs
# of what git or a hand changed, and rb clone starts a record of its own.
WRITES = {
    "project title": ["a project title"], "question add": ["a question"], "question move": ["a question moved"],
    "question reword": ["a question reworded"], "experiment add": ["an experiment"], "experiment file": ["an experiment filed"],
    "experiment relate": ["an experiment related"], "experiment gate": ["a gate on the experiment"], "note add": ["a note"],
    "variant add": ["a variant"], "metric add": ["a metric"], "spec set": ["a setting", "a setting's conflict", "a design factor", "a unit"],
    "spec verify": ["a setting checked again"], "spec vary": ["what the experiment varies"], "criterion add": ["an amended rule"],
    "criterion file": ["a criterion's link revised", "a criterion's link removed"], "criterion relate": ["a reverses relation"],
    "analysis add": ["an analysis"], "run add": ["a run", "a run that replaces another"], "resource add": ["a resource"],
    "resource locate": ["a resource located"], "resource expose": ["an exposure"],
    "retract": ["a run retracted", "a criterion retracted", "a resource retracted", "an experiment retracted", "a note retracted", "a metric retracted"],
    "freeze": ["a freeze"], "decide": ["a stop on the experiment", "an accept on the question", "an investigate on the criterion", "a vouch for a setting",
                                       "a reject on a setting", "a reject on the criterion", "a park on the experiment", "a past call"],
    "confirm": ["a design's confirmation", "a proposed call's confirmation"],
    "reconcile --apply": ["a results folder kept by rb reconcile --apply"],
}
NOT_TAKEN = {"init", "remove"}
GATES = ("unmet", "unresolved", "untested", "integrity", "late", "undecided", "stale", "checks", "unknown", "unestablished")


def confirm_key(root: Path, exp: str) -> str:
    """The key rb confirm lists an experiment's design under, to confirm it as listed."""
    m = re.search(rf"{exp}@[0-9a-f]{{64}}", json.dumps(rb(root, "confirm", exp, js=True)["data"]))
    assert m, rb(root, "confirm", exp)
    return m.group(0)


def dry_run_commands() -> set:
    """The commands rb's command line says take --dry-run: the writes, as rb lists them."""
    spec = json.loads((Path(__file__).resolve().parents[1] / "rust" / "src" / "cli_spec.json").read_text(encoding="utf-8"))
    out = set()

    def walk(node, words):
        for a in node.get("actions", []):
            if a.get("kind") == "parsers":
                for name, sub in a["parsers"]:
                    walk(sub, [*words, name])
        if any("--dry-run" in a.get("options", []) for a in node.get("actions", [])):
            out.add(" ".join(words))
    walk(spec, [])
    return out


def test_every_write_rb_makes_is_taken_out_by_a_case():
    """A write rb gains fails here until a case takes it out: WRITES holds every command rb declares a write."""
    assert dry_run_commands() - NOT_TAKEN == set(WRITES) - {"reconcile --apply"}, sorted(dry_run_commands() ^ set(WRITES))
    assert {c for cs in WRITES.values() for c in cs} <= set(CASES), sorted({c for cs in WRITES.values() for c in cs} - set(CASES))


def test_every_kind_rb_names_is_read(home):
    """Every kind `rb schema ir` names is either a record rb show takes, whose ids the property reads, or read within
    one: a kind rb gains fails here until it is placed."""
    kinds = set(rb(home, "schema", "ir", js=True)["data"]["objects"])
    assert kinds == set(SHOWN) | set(WITHIN), sorted(kinds ^ (set(SHOWN) | set(WITHIN)))


@pytest.mark.parametrize("case", sorted(CASES))
def test_no_line_reads_as_if_the_write_git_took_out_was_never_made(home, case):
    root = base(home)
    setup, write = CASES[case]
    made = setup(root) if setup else None
    if setup:
        git(root, "add", "-A")
        git(root, "commit", "-qm", "the call")
    write(root, made)
    st = rb(root, "status", js=True)["data"]
    ids = record_ids(root)
    names = {o["id"]: {o["id"], *([o["title"]] if o.get("title") else []), *([o["text"]] if o.get("text") else [])} for k in ("experiments", "questions") for o in st.get(k) or []}
    before = surfaces(root, ids, names)
    git(root, "checkout", "--", ".")
    assert rb(root, "status", js=True)["data"].get("taken_out"), case
    after = surfaces(root, ids, names)
    bad = unsaid(before, after, ids)
    assert not bad, "\n".join([case, *bad])
    # and every gate fails closed, whatever it read before
    for gate in GATES:
        assert subprocess.run([*command(), "status", "--fail-on", gate], cwd=root, env=environment(), capture_output=True).returncode == 1, (case, gate)


def test_the_gate_s_miss_stays_with_its_own_line_while_the_call_that_settled_it_is_taken_out(home):
    """g.sh (MP-1111-0219): a missed gate settled by a park on its experiment; the park retracted and not committed;
    git checkout -- . takes the retraction out. The park reads in force on disk, but what settles the gate rests on a
    write git took out: gate_missed stays in Needs judgment, its line naming it, and the experiment's state reads
    undetermined, never parked. Fails on 085ffcf."""
    root = base(home)
    d = call(root, "int8", "park", "--why", "later", "--revisit", "next month")()
    git(root, "add", "-A")
    git(root, "commit", "-qm", "parked")
    assert "gate_missed" not in {i["code"] for i in rb(root, "status", js=True)["data"]["open"]}
    rb(root, "retract", d, "--why", "no")
    git(root, "checkout", "--", ".")
    data = rb(root, "status", js=True)["data"]
    gate = [i for i in data["open"] if i["code"] == "gate_missed"]
    assert gate and f"a write to {d}" in gate[0]["what"] and "taken out of the record by git" in gate[0]["what"], data["open"]
    e = next(e for e in data["experiments"] if e["id"] == "int8")
    assert e["state"] == "undetermined" and e["state_said"] == "a write to a call on it taken out by git", e
    assert "parked" not in rb(root, "show", "int8").splitlines()[0]
    rb(root, "doctor", "--restore")
    data = rb(root, "status", js=True)["data"]
    assert "gate_missed" in {i["code"] for i in data["open"]} and next(e for e in data["experiments"] if e["id"] == "int8")["state"] == "open"


@pytest.mark.parametrize("subject", ["int8", "q1"])
def test_a_call_whose_retraction_git_took_out_reads_undetermined_never_in_force(home, subject):
    """s9.sh (MP-1111-0219): rb decide <subject> stop committed, its retraction not; git checkout -- . takes the
    retraction out. The state reads undetermined, in words that name the take-out, on rb show, rb status, rb context and
    the page; the call's listing says a write to it was taken out; rb doctor --restore gives "open" back. Fails on
    085ffcf ("stopped")."""
    root = base(home)
    d = call(root, subject, "stop", "--why", "enough")()
    git(root, "add", "-A")
    git(root, "commit", "-qm", "stopped")
    rb(root, "retract", d, "--why", "not after all")
    git(root, "checkout", "--", ".")
    said = "a write to a call on it taken out by git"
    data = rb(root, "status", js=True)["data"]
    row = next(x for x in data["experiments" if subject == "int8" else "questions"] if x["id"] == subject)
    assert (row["state"], row["state_said"]) == ("undetermined", said), row
    shown = rb(root, "show", subject).splitlines()
    head = next(l for l in shown if l.startswith(("Experiment int8", "Question q1")))
    assert said in head and "stopped" not in head, head
    assert shown[0].startswith(f"1 write to a decision under it was taken out of the record by git: {d}."), shown[0]
    assert said in rb(root, "status") and said in rb(root, "context")
    dec = next(x for x in data["decisions"] if x["id"] == d)
    assert dec["taken_out"].startswith("a write to it was taken out of the record by git"), dec
    p = home / "p.html"
    rb(root, "page", "--out", str(p))
    names = {subject: {subject, "INT8 against FP32" if subject == "int8" else "Does INT8 cost accuracy?"}}
    assert states_in(page_lines(p), names)[subject] == {"(taken out)"}, states_in(page_lines(p), names)
    rb(root, "doctor", "--restore")
    head = rb(root, "show", subject).splitlines()[0]
    assert head.startswith(("Experiment int8", "Question q1")) and head.count("open") == 1, head


def test_with_every_run_taken_out_the_experiments_lists_and_the_question_say_so_never_planned(home):
    """MP-1111-0219's S1 (S4 made whole): with every run git took out, the Experiments list on rb open and on a page,
    rb open's /state.json (its briefs) and rb show <question>'s line say the evidence was taken
    out by git, never "planned" or "untested". Fails on 085ffcf."""
    root = home / "s1"
    root.mkdir()
    git(root, "init", "-q")
    for argv in (("init", "INT8 ResNet-50", "--no-agents"), ("question", "add", "Does INT8 cost accuracy?", "--id", "q-acc"),
                 ("experiment", "add", "INT8 against FP32", "--id", "int8", "-q", "q-acc", "--baseline", "fp32", "--candidate", "int8"),
                 ("spec", "set", "int8", "seed", "--per-run"), ("metric", "add", "top1", "--maximize"),
                 ("criterion", "add", "INT8 loses at most one point", "-e", "int8", "-q", "q-acc", "--metric", "change.top1", "--at-least", "-1", "--id", "c1")):
        rb(root, *argv, js=True)
    git(root, "add", "-A")
    git(root, "commit", "-qm", "the rule")
    for n in (1, 2, 3):
        rb(root, "run", "add", "int8", f"int8.top1=75.{n}", f"fp32.top1=76.{n}", "--set", f"seed={n}", js=True)
    git(root, "checkout", "--", ".")
    said = "3 runs taken out by git"
    q = rb(root, "show", "q-acc")
    assert f"  experiment int8 has {said}: " in q and "untested: experiment int8" not in q, q
    p = home / "s1.html"
    rb(root, "page", "--out", str(p))
    rows = [l for l in page_lines(p) if l.startswith("Experiment · ")]
    assert rows and all(said in l and "planned" not in l for l in rows), rows
    server = Open(root)
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(server.url + "/state.json", timeout=60) as resp:
            doc = json.loads(resp.read().decode("utf-8"))
        with opener.open(server.url + "/", timeout=60) as resp:
            live = resp.read().decode("utf-8")
    finally:
        server.close()
    brief = next(e for b in doc["home"]["briefs"] for e in b["experiments"] if e["id"] == "int8")
    assert brief["state_said"] == said, brief
    p.write_text(live, encoding="utf-8")
    rows = [l for l in page_lines(p) if l.startswith("Experiment · ")]
    assert rows and all(said in l and "planned" not in l for l in rows), rows


def test_a_result_with_no_direction_because_git_took_its_metric_out_says_so(home):
    """MP-1111-0219's later: in B1's case (c1 on change.ce, rb metric add loss --minimize --alias ce taken out) the
    result reads "higher" with no direction beside the undetermined rule; its words now say why. Fails on 085ffcf."""
    root = home / "b1"
    root.mkdir()
    git(root, "init", "-q")
    for argv in (("init", "INT8 ResNet-50", "--no-agents"), ("experiment", "add", "INT8 against FP32", "--id", "int8", "--baseline", "fp32", "--candidate", "int8"),
                 ("spec", "set", "int8", "seed", "--per-run"),
                 ("criterion", "add", "INT8 adds at most 0.05 loss", "-e", "int8", "--metric", "change.ce", "--at-most", "0.05", "--id", "c1")):
        rb(root, *argv, js=True)
    for n in (1, 2, 3):
        rb(root, "run", "add", "int8", f"int8.ce=1.0{n + 1}", f"fp32.ce=1.0{n}", "--set", f"seed={n}", js=True)
    git(root, "add", "-A")
    git(root, "commit", "-qm", "the runs")
    rb(root, "metric", "add", "loss", "--minimize", "--alias", "ce", js=True)
    git(root, "checkout", "--", ".")
    f = rb(root, "show", "c1", js=True)["data"]["finding"]
    assert f["result"] == "higher" and f["rule_outcome"] == "undetermined", f
    assert f["says"]["result"] == "higher: the catalogue's entry for loss, which gives its direction, was taken out of the record by git", f["says"]


def test_a_confirmation_git_took_out_never_offers_the_call_to_be_confirmed_again(home, monkeypatch):
    """The f13h review's: a call proposed in an agent session, committed, then confirmed in the user's terminal; git
    checkout -- . takes the confirmation out. The call is never listed as waiting again (a second rb confirm would
    record it twice once restored), and the experiment's state reads undetermined, naming it; restored, it reads
    stopped, confirmed once."""
    root = base(home)
    monkeypatch.setenv("CLAUDECODE", "1")
    d = rb(root, "decide", "int8", "stop", "--why", "enough", js=True)["data"]["object"]["id"]
    monkeypatch.delenv("CLAUDECODE")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "proposed")
    rb(root, "confirm", d, js=True)
    git(root, "checkout", "--", ".")
    data = rb(root, "status", js=True)["data"]
    assert d not in {p.get("decision") or p.get("id") for p in data["pending"]}, data["pending"]
    e = next(e for e in data["experiments"] if e["id"] == "int8")
    assert e["state"] == "undetermined" and "taken out by git" in e["state_said"], e
    assert f"confirm: rb confirm {d}" not in rb(root, "status")
    rb(root, "doctor", "--restore")
    e = next(e for e in rb(root, "status", js=True)["data"]["experiments"] if e["id"] == "int8")
    assert e["state"] == "stopped", e


def test_a_state_resting_on_a_stashed_call_names_the_stash(home):
    """The f13h review's later: with the retraction in git's stash, the state says so as the call's line does."""
    root = base(home)
    d = call(root, "int8", "stop", "--why", "enough")()
    git(root, "add", "-A")
    git(root, "commit", "-qm", "stopped")
    rb(root, "retract", d, "--why", "not after all")
    git(root, "stash")
    e = next(e for e in rb(root, "status", js=True)["data"]["experiments"] if e["id"] == "int8")
    assert (e["state"], e["state_said"]) == ("undetermined", "a write to a call on it in git's stash"), e
    git(root, "stash", "pop")
    e = next(e for e in rb(root, "status", js=True)["data"]["experiments"] if e["id"] == "int8")
    assert e["state"] == "open", e


def test_the_reproduce_line_reads_a_resource_whose_retraction_git_took_out_as_unknown(home):
    """MP-1111-0616's N1: rb retract holdout (read by every run), not committed, then git checkout -- .: what it takes to
    run int8 again read "eval ok/partial held-out images: ..." as if never retracted. It reads unknown, naming the
    take-out, on rb show and rb status; restored, it reads retracted. Fails on ccd738a."""
    root = base(home)
    rb(root, "retract", "holdout", "--why", "leaked")
    git(root, "checkout", "--", ".")
    row = next(r for r in rb(root, "show", "int8", js=True)["data"]["rerunnability"] if r.get("resource") == "holdout")
    assert row["state"] == "unknown" and "taken out of the record by git" in row["detail"], row
    assert "eval (holdout) unknown" in rb(root, "status")
    rb(root, "doctor", "--restore")
    row = next(r for r in rb(root, "show", "int8", js=True)["data"]["rerunnability"] if r.get("resource") == "holdout")
    assert row["state"] == "missing" and "retracted (leaked)" in row["detail"], row


def test_a_setting_whose_reject_git_took_out_names_it_and_offers_no_vouch(home):
    """MP-1111-0616's N2: rb decide int8/cuda reject, not committed, then git checkout -- .: rb show int8/cuda read
    "provisional: no check of it is recorded" and Can be done now offered the vouch. It names the take-out, its status
    reads undetermined, and its item asks for rb doctor --restore. Fails on ccd738a."""
    root = base(home)
    d = rb(root, "decide", "int8/cuda", "reject", "--why", "not the cluster's image", js=True)["data"]["object"]["id"]
    git(root, "checkout", "--", ".")
    shown = rb(root, "show", "int8/cuda").splitlines()
    assert shown[0].startswith("a write it may rest on was taken out of the record by git") and d in shown[0], shown
    assert shown[1] == "int8/cuda = 12.1 · undetermined: a call on it taken out by git", shown
    item = next(i for i in rb(root, "status", js=True)["data"]["open"] if i.get("subject") == "int8/cuda")
    assert item["do"] == "rb doctor --restore" and "vouch" not in item["do"] and "taken out of the record by git" in item["what"], item
    rb(root, "doctor", "--restore")
    codes = {(i["code"], i.get("subject")) for i in rb(root, "status", js=True)["data"]["open"]}
    assert ("rejected_setting", "int8/cuda") in codes, codes


def test_a_design_s_confirmation_git_took_out_is_named_restored_and_counted(home):
    """MP-1111-0616's N3: a design's confirmation is a log line alone. Taken out by git, it is one of rb's writes: rb
    status names it, every gate fails closed, rb confirm neither lists the design nor confirms it a second time, and rb
    doctor --restore puts the confirmation back. Fails on ccd738a (taken_out None, gates as before)."""
    root = base(home)
    rb(root, "experiment", "gate", "int8", "--if-holds", "ship INT8", agent=True)
    key = confirm_key(root, "int8")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "a session's gate")
    rb(root, "confirm", key)
    assert rb(root, "show", "int8", js=True)["data"]["design"]["standing"] == "confirmed"
    git(root, "checkout", "--", ".")
    data = rb(root, "status", js=True)["data"]
    assert data["taken_out"]["records"] == [{"kind": "experiment", "id": "int8"}], data["taken_out"]
    assert "int8" not in {d["experiment"] for d in data["designs_pending"]} and not data["gates_pending"], data["designs_pending"]
    assert any(l.startswith("A write to experiment int8 was taken out of the record by git") for l in rb(root, "show", "int8").splitlines()[:2])
    for gate in GATES:
        assert subprocess.run([*command(), "status", "--fail-on", gate], cwd=root, env=environment(), capture_output=True).returncode == 1, gate
    again = rb(root, "confirm", key, js=True)
    assert not again["ok"] and "rb doctor --restore puts it back" in again["errors"][0]["fix"], again
    rb(root, "doctor", "--restore")
    data = rb(root, "status", js=True)["data"]
    assert not data.get("taken_out") and rb(root, "show", "int8", js=True)["data"]["design"]["standing"] == "confirmed"
    lines = [json.loads(l) for l in (root / ".rb" / "log.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    assert sum(1 for l in lines if l.get("op") == "gate" and (l.get("detail") or {}).get("owned")) == 1


def test_a_call_whose_confirmation_git_took_out_is_not_confirmed_twice(home):
    """MP-1111-0616's L-a: rb confirm <id> typed by hand after git took the confirmation out confirmed it again, and rb
    doctor --restore then left two confirmed parks. It is refused while the take-out stands, saying why."""
    root = base(home)
    d = rb(root, "decide", "int8", "park", "--why", "later", js=True, agent=True)["data"]["object"]["id"]
    git(root, "add", "-A")
    git(root, "commit", "-qm", "proposed")
    rb(root, "confirm", d)
    git(root, "checkout", "--", ".")
    again = rb(root, "confirm", d, js=True)
    assert not again["ok"] and again["errors"][0]["message"] == (
        f"{d} was confirmed, and that confirmation was taken out of the record by git: confirming it again would record it twice once it is put back."), again
    rb(root, "doctor", "--restore")
    calls = [x for x in rb(root, "status", js=True)["data"]["decisions"] if x["subject"] == "int8" and x["action"] == "park"]
    assert len([x for x in calls if (x.get("authority") or {}).get("state") == "confirmed"]) == 1, calls


def test_a_state_resting_on_a_call_git_took_out_reads_as_one_sentence_never_as_a_call(home):
    """MP-1111-0616's L-b: rb status printed the state under the calls as `call: a write to a call on it taken out by
    git: "INT8 against FP32"`. It reads as one sentence of rb's, never after "call:"."""
    root = base(home)
    d = call(root, "int8", "stop", "--why", "enough")()
    git(root, "add", "-A")
    git(root, "commit", "-qm", "stopped")
    rb(root, "retract", d, "--why", "not after all")
    git(root, "checkout", "--", ".")
    text = rb(root, "status")
    assert '"INT8 against FP32" reads undetermined: a write to a call on it taken out by git' in text, text
    assert "call: a write to a call on it" not in text
