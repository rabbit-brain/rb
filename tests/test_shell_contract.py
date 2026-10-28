"""The shell's contract (SPRINT-OCT24.md section 1.7): one versioned JSON document, rb-shell-view/1.1, that the model
builds (track M), the renderer draws (R) and rb open serves (A). Its examples, its shape and the DOM contract are in
tests/fixtures/shell/, beside the scripts that make them.

A test whose stub is not filled yet skips with "pending <group>", never passes in silence. A group counts as filled
once tests/fixtures/shell/merged-phases.txt lists it (I adds it at the merge point that carries it); a track runs its
own group's tests before then with RB_SHELL_CONTRACT_MERGED=<group>[,<group>...]. A group listed there whose stub still
answers "not built yet" fails."""
import html.parser
import importlib.util
import json
import os
import re
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from rbtest import AGENT_VARS, command, environment

HERE = Path(__file__).parent / "fixtures" / "shell"
EXAMPLES = ("live", "owner", "away", "past")
NOT_BUILT = "not built yet"
_spec = importlib.util.spec_from_file_location("shell_contract_shape", HERE / "shape.py")
shape_py = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(shape_py)


CONTRACT = "rb-shell-view/1.9"   # a minor bump adds fields only (section 1.8), so a document of an earlier minor still meets it


def same_major(contract) -> bool:
    """A document of this contract's major and a minor no later than its own."""
    m = re.fullmatch(r"rb-shell-view/1\.(\d+)", contract or "")
    return bool(m) and int(m.group(1)) <= int(CONTRACT.rsplit(".", 1)[1])


def example(name: str) -> dict:
    return json.loads((HERE / f"int8-demo.{name}.view.json").read_text(encoding="utf-8"))


def shape() -> dict:
    lines = (HERE / "view-1.shape.tsv").read_text(encoding="utf-8").splitlines()
    assert lines[0] == "path\ttype\tphase\ttoday\ttier"
    rows = {}
    for line in lines[1:]:
        path, types, phase, _, tier = line.split("\t")
        rows[path] = {"types": set(types.split("|")), "phase": phase, "tier": tier}
    return rows


def merged() -> set:
    text = (HERE / "merged-phases.txt").read_text(encoding="utf-8")
    tags = {t.strip() for t in text.splitlines() if t.strip() and not t.startswith("#")}
    return tags | {t.strip() for t in os.environ.get("RB_SHELL_CONTRACT_MERGED", "").split(",") if t.strip()}


def needs(*groups: str) -> None:
    for g in groups:
        if g not in merged():
            pytest.skip(f"pending {g}")


# ---------------------------------------------------------------- what a document may hold

def walk(v, p: str = "", at: str = ""):
    """Every value of a document: its path in the shape, where it sits in this document, the value."""
    yield p, at or "$", v
    if isinstance(v, dict):
        for k, x in v.items():
            yield from walk(x, shape_py.child(p, k), f"{at}.{k}" if at else k)
    elif isinstance(v, list):
        for i, x in enumerate(v):
            yield from walk(x, p + "[]", f"{at}[{i}]")


def fixed_children(rows: dict) -> dict:
    """Each object path's own keys, as the shape lists them (a map's ids and names are not keys)."""
    kids: dict = {}
    for q in rows:
        if q.endswith("[]") or q.endswith(".{id}") or q.endswith(".{key}"):
            continue
        parent, _, key = q.rpartition(".")
        kids.setdefault(parent, []).append((key, q))
    return kids


EMPTY = ("", None, [], {})


def shape_problems(doc: dict, rows: dict, phases: set = None) -> tuple:
    """(problems, pending) of a document against the shape: a path outside it, a type it does not list, a key missing
    from an object (pending instead, where `phases` is given and lacks the field's phase), a Live-only field holding
    something on the Owner tier, and the Owner-only reproduce_url on the Live tier."""
    tier, kids = doc.get("tier"), fixed_children(rows)
    problems, pending = [], set()
    for p, at, v in walk(doc):
        if p:
            row = rows.get(p)
            if row is None:
                problems.append(f"{at}: outside the shape ({p})")
                continue
            if shape_py.kind(v) not in row["types"]:
                problems.append(f"{at}: {shape_py.kind(v)}, where the shape says {'|'.join(sorted(row['types']))}")
            if tier == "owner" and row["tier"] == "live" and v not in EMPTY:
                problems.append(f"{at}: Live only, and not empty on the Owner tier")
            if tier == "live" and row["tier"] == "owner":
                problems.append(f"{at}: Owner only, on the Live tier")
        if not isinstance(v, dict):
            continue
        for key, q in kids.get(p, []):
            row = rows[q]
            if key in v or (tier == "owner" and row["tier"] == "live") or (tier != "owner" and row["tier"] == "owner"):
                continue
            if phases is not None and row["phase"] not in phases:
                pending.add(q)
            else:
                problems.append(f"{at}.{key}: missing ({q}, phase {row['phase']})" if at != "$" else f"{key}: missing")
    return problems, sorted(pending)


# section 1.2: a writer never enters the document; counts_toward[].via is the analysis a run counts through
WRITER_KEYS = {"actor", "actor_via", "via", "session", "session_id", "agent", "provider", "model", "by", "writer", "author", "you"}
VIA = "runs.{id}.counts_toward[].via"
WRITER_WORDS = re.compile(r"\b(?:human|agent):[\w.-]+|\bby (?:human|agent|ana|a session|the session|an agent|the agent|claude|codex)\b|\b(?:claude|codex)\b", re.I)
WORDING = re.compile("\u2014|\\b(?:the|a) person\\b", re.I)


def live_only_values(live: dict) -> set:
    """What only the Live tier may say: a place's path and name, the machine's platform, the user's own words."""
    out = set()
    places = live.get("places") or {}
    for x in [places.get("place") or {}, *places.get("places", []), *places.get("elsewhere", [])]:
        out |= {x.get("path") or "", x.get("label") or ""}
        if x.get("id") and x["id"] != (places.get("place") or {}).get("id"):
            out.add(x["id"])
    out |= {row[1] for r in live["runs"].values() for row in r["receipt_rows"] if row[0] == "platform"}
    out |= {f["your_words"].strip('"') for f in live["findings"].values()}
    return {v for v in out if v}


def writer_problems(doc: dict, live: dict = None) -> list:
    out = []
    banned = live_only_values(live) if live and doc.get("tier") == "owner" else set()
    for p, at, v in walk(doc):
        key = at.rsplit(".", 1)[-1] if not at.endswith("]") else ""
        if key in WRITER_KEYS and p != VIA:
            out.append(f"{at}: a writer's field")
        if isinstance(v, str):
            for rx, what in ((WRITER_WORDS, "names a writer"), (WORDING, "breaks a wording rule")):
                m = rx.search(v)
                if m:
                    out.append(f"{at}: {what}: {m.group(0)!r}")
            out += [f"{at}: a Live-only value on the Owner tier: {b!r}" for b in banned if b in v]
    return out


# ---------------------------------------------------------------- always: the examples and the shape

@pytest.mark.parametrize("name", EXAMPLES)
def test_example_meets_shape(name):
    """Each example holds every path of the shape its tier and objects hold, each with a type the shape lists, and
    nothing outside it."""
    doc = example(name)
    assert doc["contract"] == CONTRACT
    problems, pending = shape_problems(doc, shape())
    assert not problems and not pending, problems[:20]


@pytest.mark.parametrize("name", [*EXAMPLES, "shape"])
def test_no_writer_in_the_contract(name):
    """Section 1.2's list, over the keys and values of each example and over the shape's paths."""
    if name == "shape":
        hits = [p for p in shape() if p != VIA and set(re.split(r"[.\[\]]+", p)) & WRITER_KEYS]
    else:
        hits = writer_problems(example(name), live=example("live"))
    assert not hits, hits[:20]


def test_the_checks_catch_a_broken_example():
    """Each check fails on an example broken the way it guards against."""
    rows = shape()
    dropped = example("live")
    del dropped["findings"]["c1"]["units_line"]
    assert any("units_line: missing" in p for p in shape_problems(dropped, rows)[0])
    retyped = example("live")
    retyped["home"]["counts"]["runs"] = "9"
    assert any("counts.runs: string" in p for p in shape_problems(retyped, rows)[0])
    grown = example("owner")
    grown["runs"]["runs1"]["shell_note"] = ""
    assert any("outside the shape" in p for p in shape_problems(grown, rows)[0])
    leaked = example("owner")
    leaked["runs"]["runs3"]["observed_in"] = example("live")["runs"]["runs3"]["observed_in"]
    assert any("Live only" in p for p in shape_problems(leaked, rows)[0])
    acted = example("live")
    acted["runs"]["runs1"]["actor"] = "human:ana"
    assert any("a writer's field" in p for p in writer_problems(acted))
    said = example("owner")
    said["history"]["log"][0]["sentence"] += " by a session"
    assert any("names a writer" in p for p in writer_problems(said))
    quoted = example("owner")
    quoted["made_calls"][0]["line"] = example("live")["made_calls"][0]["line"]
    assert any("Live-only value" in p for p in writer_problems(quoted, live=example("live")))


def test_the_fixtures_are_what_their_generators_give():
    """The Owner example, rb open's other two places and the shape are what owner_of.py, place_of.py and shape.py make."""
    def made(*argv):
        return subprocess.run([sys.executable, "-I", *argv], cwd=HERE, capture_output=True, encoding="utf-8", check=True).stdout
    live = "int8-demo.live.view.json"
    assert made("owner_of.py", live) == (HERE / "int8-demo.owner.view.json").read_text(encoding="utf-8")
    for state in ("away", "past"):
        assert made("place_of.py", state, live) == (HERE / f"int8-demo.{state}.view.json").read_text(encoding="utf-8")
    assert made("shape.py", live, "int8-demo.away.view.json", "int8-demo.past.view.json") == (HERE / "view-1.shape.tsv").read_text(encoding="utf-8")


def test_every_owned_path_is_one_owners():
    """OWNERS.tsv: one path or glob and its owner (M, P, H, R, C, A, L, D, I) per line, each path once."""
    lines = [line for line in (HERE / "OWNERS.tsv").read_text(encoding="utf-8").splitlines() if line and not line.startswith("#")]
    pairs = [line.split("\t") for line in lines]
    assert all(len(p) == 2 and p[1] in set("MPHRCALDI") for p in pairs), [p for p in pairs if len(p) != 2 or p[1] not in "MPHRCALDI"]
    assert len({p[0] for p in pairs}) == len(pairs)


# ---------------------------------------------------------------- rb, under the shell's test hooks

def rb_env(**more) -> dict:
    env = {k: v for k, v in environment().items() if k not in AGENT_VARS and not k.startswith("RB_TEST_SHELL")}
    env.update(RB_TELEMETRY="0", DO_NOT_TRACK="1", RB_TEST_NOW="2026-10-19T09:41:00.000+00:00", **more)
    return env


def rb(root: Path, *argv: str, **env) -> subprocess.CompletedProcess:
    return subprocess.run([*command(), *argv], cwd=root, env=rb_env(**env), capture_output=True, encoding="utf-8", errors="replace",
                          stdin=subprocess.DEVNULL)


@pytest.fixture(scope="module")
def record(tmp_path_factory):
    """A record to run rb page, rb export and rb open in, where the document they draw is the example's."""
    root = tmp_path_factory.mktemp("shell-contract")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    r = rb(root, "init", "INT8 ResNet-50 on ImageNet")
    assert r.returncode == 0, r.stderr
    return root


_made = iter(range(10 ** 6))


def drawn(root: Path, surface: str, name: str = None, dump: Path = None) -> tuple:
    """(page, error) of `surface` (page, export or open) in `root`, drawn from example `name` when one is named."""
    env = {"RB_TEST_SHELL": "1"}
    if name:
        env["RB_TEST_SHELL_MODEL"] = str(HERE / f"int8-demo.{name}.view.json")
    if dump:
        env["RB_TEST_SHELL_DUMP"] = str(dump)
    n = next(_made)
    if surface == "page":
        out = root / f"shell-{n}.html"
        r = rb(root, "page", "--out", str(out), **env)
        return (out.read_text(encoding="utf-8"), "") if r.returncode == 0 else ("", r.stderr + r.stdout)
    if surface == "export":
        out = root / f"shell-export-{n}"
        r = rb(root, "export", "--out", str(out), **env)
        return ((out / "page.html").read_text(encoding="utf-8"), "") if r.returncode == 0 else ("", r.stderr + r.stdout)
    said = root / f"open-{n}.txt"
    with open(said, "wb") as err:
        proc = subprocess.Popen([*command(), "open", "--port", "0"], cwd=root, env=rb_env(**env), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=err)
    try:
        deadline = time.monotonic() + 60
        while not re.search(r"^rb open · (http://[0-9.]+:\d+)/$", said.read_text(encoding="utf-8", errors="replace"), flags=re.M):
            if proc.poll() is not None or time.monotonic() > deadline:
                return "", said.read_text(encoding="utf-8", errors="replace")
            time.sleep(0.05)
        url = re.search(r"^rb open · (http://[0-9.]+:\d+)/$", said.read_text(encoding="utf-8", errors="replace"), flags=re.M).group(1)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # never a proxy near loopback
        try:
            with opener.open(url + "/", timeout=30) as resp:
                return resp.read().decode("utf-8"), ""
        except urllib.error.HTTPError as e:
            return "", e.read().decode("utf-8", "replace")
    finally:
        proc.send_signal(signal.SIGINT) if sys.platform != "win32" else proc.terminate()   # Ctrl-C, as rb open says
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


def drawn_or_pending(root: Path, surface: str, name: str, group: str) -> str:
    """The example drawn, or a skip naming the group while its stub answers "not built yet"."""
    needs(group)
    page, error = drawn(root, surface, name)
    assert NOT_BUILT not in error, f"{group} is listed as merged, and the stub still answers: {error}"
    assert page, error
    return page


class Tree(html.parser.HTMLParser):
    """The page as elements: tag, attributes, children and text, enough for the selectors the DOM contract names."""
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}

    def __init__(self, text: str):
        super().__init__(convert_charrefs=True)
        self.root = {"tag": "#root", "attrs": {}, "kids": [], "text": [], "seq": [], "up": None}
        self.at = self.root
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        el = {"tag": tag, "attrs": {k: v or "" for k, v in attrs}, "kids": [], "text": [], "seq": [], "up": self.at}
        self.at["kids"].append(el)
        self.at["seq"].append(el)
        if tag not in self.VOID:
            self.at = el

    def handle_endtag(self, tag):
        el = self.at
        while el is not self.root and el["tag"] != tag:
            el = el["up"]
        if el is not self.root:
            self.at = el["up"]

    def handle_data(self, data):
        self.at["text"].append(data)
        self.at["seq"].append(data)

    def all(self, el=None):
        for k in (el or self.root)["kids"]:
            yield k
            yield from self.all(k)

    def text(self, el) -> str:
        """The element's text in document order: its own text where it sits among its children's."""
        return "".join(x if isinstance(x, str) else " " + self.text(x) for x in el["seq"])

    def by_id(self, ident: str):
        return next((e for e in self.all() if e["attrs"].get("id") == ident), None)


COMPOUND = re.compile(r'([a-z][a-z0-9]*)?((?:[#.][\w-]+|\[[\w-]+(?:\^?="[^"]*")?\])*)$')


def matches(el, compound: str) -> bool:
    tag, rest = COMPOUND.match(compound).groups()
    if tag and el["tag"] != tag:
        return False
    for part in re.findall(r'#[\w-]+|\.[\w-]+|\[[^\]]+\]', rest or ""):
        a = el["attrs"]
        if part[0] == "#" and a.get("id") != part[1:]:
            return False
        if part[0] == "." and part[1:] not in a.get("class", "").split():
            return False
        if part[0] == "[":
            m = re.match(r'\[([\w-]+)(\^?=)?(?:"([^"]*)")?\]', part)
            name, op, value = m.groups()
            if name not in a or (op == "=" and a[name] != value) or (op == "^=" and not a[name].startswith(value)):
                return False
    return True


def select(tree: Tree, selector: str) -> list:
    """The elements a selector of the DOM contract names: compounds joined by a space (inside) or by > (a child)."""
    steps = re.split(r"\s*(>)\s*|\s+", selector.strip())
    steps = [s for s in steps if s]
    found = []
    for el in tree.all():
        if not matches(el, steps[-1]):
            continue
        at, i, ok = el, len(steps) - 2, True
        while i >= 0 and ok:
            child = steps[i] == ">"
            want = steps[i - 1] if child else steps[i]
            up = at["up"]
            if child:
                ok = up is not None and up["tag"] != "#root" and matches(up, want)
                at, i = up, i - 2
            else:
                while up is not None and up["tag"] != "#root" and not matches(up, want):
                    up = up["up"]
                ok = up is not None and up["tag"] != "#root"
                at, i = up, i - 1
        if ok:
            found.append(el)
    return found


def said(text: str) -> str:
    return " ".join(re.sub("[​‌‍⁠﻿]", "", text).split())


def values(doc, spec: str) -> list:
    """The texts at a path of the document, * standing for every element of a list or every value of a map."""
    items = [doc]
    for part in re.findall(r"[^.\[\]]+|\[\d+\]|\[\*\]", spec):
        nxt = []
        for x in items:
            if part == "[*]" or part == "*":
                nxt += list(x.values()) if isinstance(x, dict) else list(x)
            elif part.startswith("["):
                nxt.append(x[int(part[1:-1])])
            else:
                nxt.append(x[part])
        items = nxt
    return [str(x) for x in items if x not in EMPTY]


# Section 1.4: each view, the element that holds it, the group that draws it, and the fields it shows (the Owner example
# on rb page). A field is a path into the document; [*] and * stand for every element.
VIEWS = {
    "frame": (None, "R1", ["project.title", "at", "rb_version"]),
    "nav": ("nav", "R1", ["questions[*].text", "experiments[*].short_title"]),
    "overview": ("overview", "R1", ["home.latest.head", "home.began", "home.scope", "home.uncited_line", "home.found[*].title",
                                    "pending.rows[*].text", "home.open_line", "home.notes[*].text", "questions[*].text",
                                    "home.standing[*].line", "home.standing[*].state_word",
                                    "home.standing[*].tallies[*].label", "home.integrity.parts[*].line", "home.integrity.affects",
                                    "home.queue[*].title", "home.queue[*].detail"]),
    "overview-sitting": ("overview", "H2", ["history.sittings[0].head"]),
    "overview-recent": ("overview", "H2", ["history.sittings[0].lines[*].sentence"]),
    "your-call": ("your-call", "R1", ["home.quiet", "calls[*].title", "calls[*].reason.label", "calls[*].reason.detail", "calls[*].actions[*].line",
                                      "home.waiting_line"]),
    "open-items": ("open-items", "R1", ["open_items[*].head", "unfiled.open_items[*].what"]),
    "research": ("research", "R3", ["questions[*].text", "unfiled.line"]),
    "question": ("q-q1", "R3", ["questions[0].text", "findings.c1.statement", "findings.c1.reading.estimate_text", "findings.c1.reading.result",
                                "findings.c1.units_line", "findings.c2.rule_detail", "questions[0].next_step", "made_calls[*].line",
                                "questions[0].overview.interpretations[*].text"]),
    "criteria": ("criteria", "R3", ["findings.*.statement"]),
    "experiments": ("experiments", "R4", ["experiments[*].short_title", "experiments[*].design.standing"]),
    "experiment": ("x-e1", "R4", ["experiments[0].who_line", "experiments[0].hypothesis", "experiments[0].reproduce.ready_line", "runs.runs1.label"]),
    "runs": ("runs", "R4", ["runs.*.produced"]),
    "resources": ("resources", "R4", ["resources.*.name", "resources.*.role", "resources.*.availability"]),
    "history": ("history", "R4", ["history.log[*].sentence"]),
    "history-sittings": ("history", "H2", ["history.summary", "history.sittings[*].head", "history.sittings[*].lines[*].sentence",
                                           "history.sittings[*].lines[*].why"]),
    "finding-card": ("c-c1", "C2", ["findings.c1.reading.estimate_text", "findings.c1.rule", "findings.c1.units_line", "findings.c1.receipt_rows[*][1]"]),
    "run-receipt": ("run-runs1", "C2", ["runs.runs1.label", "runs.runs1.at", "runs.runs1.metrics_text", "runs.runs1.receipt_rows[*][1]",
                                        "runs.runs1.reproduce.ready_line"]),
    "resource-card": ("r-imagenet-val", "C2", ["resources.imagenet-val.name", "resources.imagenet-val.acquisition.command"]),
    "analysis-card": ("a-a1", "C2", ["analyses.a1.title", "analyses.a1.recorded"]),
    "call-card": ("call-c2", "C2", ["calls[0].reason.label", "calls[0].actions[*].consequence"]),
}


# ---------------------------------------------------------------- M: the model

def int8_root(request) -> Path:
    """Phase 0's INT8 demo record (conftest's int8_demo), once phase 0 is merged."""
    try:
        return request.getfixturevalue("int8_demo").root
    except Exception as e:
        if type(e).__name__ != "FixtureLookupError":
            raise
        pytest.skip("pending phase 0 (the INT8 demo record)")


def model_document(root: Path, tier: str, tmp: Path) -> dict:
    dump = tmp / f"model-{tier}.json"
    page, error = drawn(root, "open" if tier == "live" else "page", dump=dump)
    assert dump.exists(), f"M0 is listed as merged, and rb wrote no document: {error}"
    return json.loads(dump.read_text(encoding="utf-8"))


@pytest.mark.parametrize("tier", ["live", "owner"])
def test_model_meets_contract(request, tier, tmp_path):
    """RB_TEST_SHELL_DUMP on the INT8 demo record matches the shape: a path whose phase is merged is there, a path whose
    phase is not is pending (listed, never failed), and nothing is outside the shape."""
    needs("M0")
    doc = model_document(int8_root(request), tier, tmp_path)
    assert doc.get("tier") == tier and same_major(doc.get("contract")), doc.get("contract")
    problems, pending = shape_problems(doc, shape(), phases=merged())
    assert not problems, problems[:20]
    if pending:
        print(f"pending ({len(pending)} paths of phases not merged): " + ", ".join(pending[:40]))
    assert not writer_problems(doc), writer_problems(doc)[:20]


def test_model_tiers_differ_only_as_listed(request, tmp_path):
    """M's Owner document is its Live one with section 1.5's rows applied (owner_of.py), and nothing else."""
    needs("M0", "M4")
    root = int8_root(request)
    live = model_document(root, "live", tmp_path)
    owner = model_document(root, "owner", tmp_path)
    expected = subprocess.run([sys.executable, "-I", str(HERE / "owner_of.py"), str(tmp_path / "model-live.json")], capture_output=True,
                              encoding="utf-8", check=True).stdout
    assert owner == json.loads(expected)
    assert live.get("tier") == "live"


# ---------------------------------------------------------------- R: the renderer

@pytest.mark.parametrize("view", list(VIEWS))
def test_every_drawn_field_shows(record, view):
    """The Owner example on rb page (RB_TEST_SHELL_MODEL): each field section 1.4 lists shows its example text in its
    view."""
    ident, group, fields = VIEWS[view]
    needs(group)
    tree = Tree(drawn_or_pending(record, "page", "owner", "R1"))
    el = tree.root if ident is None else tree.by_id(ident)
    assert el is not None, f"no element #{ident}"
    text = said(tree.text(el))
    doc = example("owner")
    missing = [(f, v) for f in fields for v in values(doc, f) if said(v) not in text]
    assert not missing, missing[:10]


def test_live_only_never_on_a_page(record):
    """The Owner example drawn holds none of the Live-only values: a place (exp-b, its path), the user's quoted
    words, the machine's platform."""
    page = said(drawn_or_pending(record, "page", "owner", "R1"))
    hits = [v for v in live_only_values(example("live")) if said(v) in page]
    assert not hits, hits


@pytest.mark.parametrize("surface", ["page", "export"])
def test_a_page_refuses_a_live_document(record, surface):
    """A Live document drawn as rb page or rb export is an error, never a page."""
    drawn_or_pending(record, "page", "owner", "R1")
    page, error = drawn(record, surface, "live")
    assert not page and error and NOT_BUILT not in error, (page[:200], error)


# ---------------------------------------------------------------- R and A: the markup A's script reads

@pytest.mark.parametrize("surface", ["page", "open"])
def test_dom_contract_holds(record, surface):
    """Every selector of dom-contract.json that the markup holds is in the render of its example, on rb page (the
    Owner example) and on rb open (the Live one, or another place's), for each group merged."""
    contract = json.loads((HERE / "dom-contract.json").read_text(encoding="utf-8"))
    first = "R1" if surface == "page" else "L1"   # rb open's / is R's frame with L's live module (4a)
    needs("R1", first)
    trees, missing = {}, []
    for s in contract["selectors"]:
        if s["made_by"] != "markup" or surface not in s["on"] or s["group"] not in merged():
            continue
        name = "owner" if surface == "page" else s["example"]
        if name not in trees:
            trees[name] = Tree(drawn_or_pending(record, surface, name, first))
        if not select(trees[name], s["selector"]):
            missing.append((s["selector"], name))
    assert not missing, missing


# ---------------------------------------------------------------- I: the real model, drawn

FIXED_VIEWS = {"overview": "R1", "your-call": "R1", "research": "R3", "criteria": "R3", "experiments": "R4", "runs": "R4", "resources": "R4",
               "history": "R4"}


def view_ids(tree: Tree) -> set:
    return {e["attrs"]["id"] for e in tree.all() if "view" in e["attrs"].get("class", "").split() and e["attrs"].get("id")}


@pytest.mark.parametrize("part", ["views", "cards", "links", "lint"])
def test_real_model_renders_like_the_example(request, record, part, tmp_path):
    """M's real document of the INT8 demo, drawn by R: the views the example draws, a card per object, every link
    landing on an element of the page, and rb's word lint."""
    needs("M0", "R1", *(["C2"] if part == "cards" else []))
    dump = tmp_path / "real.json"
    page, error = drawn(int8_root(request), "page", dump=dump)
    assert page, error
    real, tree = json.loads(dump.read_text(encoding="utf-8")), Tree(page)
    if part == "views":
        want = {v for v, g in FIXED_VIEWS.items() if g in merged()}
        if "R3" in merged():
            want |= {f"q-{q['id']}" for q in real["questions"]}
        if "R4" in merged():
            want |= {f"x-{x['id']}" for x in real["experiments"]}
        assert want <= view_ids(tree), sorted(want - view_ids(tree))
        assert view_ids(Tree(drawn_or_pending(record, "page", "owner", "R1"))) & want <= view_ids(tree)
    elif part == "cards":
        ids = {e["attrs"].get("id") for e in tree.all()}
        want = {f"c-{c}" for c in real["findings"]} | {f"run-{r}" for r in real["runs"]} | {f"r-{r}" for r in real["resources"]}
        want |= {f"a-{a}" for a in real["analyses"]} | {f"call-{c['subject']}" for c in real["calls"]}
        assert want <= ids, sorted(want - ids)
    elif part == "links":
        ids = {e["attrs"].get("id") for e in tree.all()}
        dead = sorted({h for e in select(tree, 'a[href^="#"]') for h in [re.split(r"[&?]", e["attrs"]["href"][1:])[0]] if h and h not in ids})
        assert not dead, dead[:20]
    else:
        lint = Path(__file__).parent / "test_shell.py"
        spec = importlib.util.spec_from_file_location("shell_words", lint) if lint.exists() else None
        module = importlib.util.module_from_spec(spec) if spec else None
        if module:
            spec.loader.exec_module(module)
        if not hasattr(module, "words_problems"):
            pytest.skip("pending R1 (the word lint: test_shell.py's words_problems)")
        assert not module.words_problems(page)
