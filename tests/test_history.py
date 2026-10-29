"""History in sittings and the Latest changes line (BP 5H), as the shell's document holds them: the log's writes split
into sittings on time only, each line in rb's words with no writer, the calls made counted (a proposal or a past call
mentioned never is), and rb open drawing them without ever writing the user's seen marker. The model's sentences and
splits are pinned in rust/src/history.rs's tests; these drive rb itself on records its own commands wrote. A line's reason
is handed in its voice: the user's own as `why`, a session's as `note`, which a screen says as rb's note form."""
import html.parser
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

from rbtest import AGENT_VARS, CLAUDE_CODE, USER_WORDS, WRITER_MARKS, Writers, command, environment, mark_seen, writers_record

HERE = Path(__file__).resolve().parent / "fixtures" / "shell"
NOT_BUILT = "is not built yet; RB_TEST_SHELL is a test hook."


def merged() -> set:
    """What is merged into int, as tests/test_shell_contract.py reads it: the phases there, and RB_SHELL_CONTRACT_MERGED."""
    text = (HERE / "merged-phases.txt").read_text(encoding="utf-8")
    tags = {t.strip() for t in text.splitlines() if t.strip() and not t.startswith("#")}
    return tags | {t.strip() for t in os.environ.get("RB_SHELL_CONTRACT_MERGED", "").split(",") if t.strip()}


def env_of(**more) -> dict:
    env = {k: v for k, v in environment().items() if k not in AGENT_VARS and not k.startswith("RB_TEST_SHELL")}
    env.update(RB_TELEMETRY="0", DO_NOT_TRACK="1", **more)
    return env


def dumped(root: Path, tmp: Path, surface: str = "page") -> dict:
    """The document rb page, rb export or rb open's `/` builds of the record at `root`, as RB_TEST_SHELL_DUMP writes it;
    a skip while 5H is not merged, and a failure once it is while the model's stub still answers."""
    if "5H" not in merged():
        pytest.skip("pending 5H (the model's history and home.latest)")
    tmp.mkdir(parents=True, exist_ok=True)
    dump = tmp / f"{surface}.view.json"
    hooks = {"RB_TEST_SHELL": "1", "RB_TEST_SHELL_DUMP": str(dump)}
    if surface == "open":
        proc, url = served(root, **hooks)
        try:
            said = get(url + "/")
        finally:
            stop(proc)
    else:
        out = tmp / (f"{surface}.html" if surface == "page" else f"{surface}-out")
        r = subprocess.run([*command(), surface, "--out", str(out)], cwd=root, env=env_of(**hooks), capture_output=True, encoding="utf-8",
                           errors="replace", stdin=subprocess.DEVNULL)
        said = r.stderr + r.stdout
    assert NOT_BUILT not in said or dump.exists(), f"5H is listed as merged, and the model's stub still answers: {said}"
    assert dump.exists(), said
    return json.loads(dump.read_text(encoding="utf-8"))


def lines_of(doc: dict) -> list:
    """Every line of every sitting, newest sitting first."""
    return [line for s in doc["history"]["sittings"] for line in s["lines"]]


def texts(node) -> list:
    if isinstance(node, dict):
        return [t for k, v in node.items() for t in [k, *texts(v)]]
    if isinstance(node, list):
        return [t for v in node for t in texts(v)]
    return [node] if isinstance(node, str) else []


# ---------------------------------------------------------------- a record its own commands wrote, at times it was told

T = {
    "init": "2026-10-06T10:02:00.000+00:00", "q1": "2026-10-06T10:04:00.000+00:00", "top1": "2026-10-06T10:05:00.000+00:00",
    "e1": "2026-10-06T10:06:00.000+00:00",
    "c1": "2026-10-06T10:08:00.000+00:00", "c3": "2026-10-06T10:13:00.000+00:00",
    "propose": "2026-10-13T14:10:00.000+00:00", "confirm": "2026-10-13T14:12:00.000+00:00", "made": "2026-10-13T14:16:00.000+00:00",
    "late": "2026-10-19T09:25:00.000+00:00",
}


@pytest.fixture
def int8(tmp_path) -> tuple:
    """The INT8 record, written by human:ana at their terminal and by a Claude Code session, in three sittings: the
    setup (a gap of five minutes inside it), the calls a week on, and one proposal the week after."""
    root = tmp_path / "int8"
    root.mkdir()
    rec = Writers(root)
    rec.git("init", "-q")
    rec.git("config", "user.email", "ana@example.com")
    rec.git("config", "user.name", "ana")
    ana, cc = Writers.HUMAN, CLAUDE_CODE
    at = lambda who, k: {**who, "RB_TEST_NOW": T[k]}  # noqa: E731
    rec.write(at(ana, "init"), "init", "INT8 ResNet-50 on ImageNet", "--no-agents")
    rec.write(at(ana, "q1"), "question", "add", "Does INT8 keep top-1 within half a point of fp32?", "--id", "q1")
    rec.write(at(ana, "top1"), "metric", "add", "top1", "--maximize", "--unit", "acc")
    rec.write(at(cc, "e1"), "experiment", "add", "int8 against fp32", "--id", "e1", "-q", "q1", "--baseline", "fp32", "--candidate", "int8")
    rec.write(at(cc, "c1"), "criterion", "add", "INT8 loses at most half a point", "-e", "e1", "--metric", "change.top1", "--at-least", "-0.005",
              "--id", "c1")
    rec.write(at(ana, "c3"), "criterion", "add", "INT8 loses at most a point", "-e", "e1", "--metric", "change.top1", "--at-least", "-0.01",
              "--id", "c3")
    proposal = rec.write(at(cc, "propose"), "decide", "c1", "accept", "--why", "holds on all three seeds")
    rec.write(at(ana, "confirm"), "confirm", proposal)
    rec.write(at(ana, "made"), "decide", "q1", "continue", "--why", "one more calibration size")
    rec.write(at(cc, "late"), "decide", "c3", "reject", "--why", "one seed is enough for the export check")
    return rec, root


def test_history_splits_on_time_only(int8, tmp_path):
    """Writes by the user and by a session, minutes apart, are one sitting and one line where they are one kind of
    change; the five minutes between c1 and c3 keep them in one sitting; a week's gap starts another."""
    rec, root = int8
    doc = dumped(root, tmp_path)
    sits = doc["history"]["sittings"]
    assert [s["n"] for s in sits] == [1, 3, 6]
    assert [s["head"] for s in sits] == [
        "2026-10-19, 09:25 UTC · 1 change · no calls made",
        "2026-10-13, 14:10 to 14:16 UTC · 3 changes · 2 calls made",
        "2026-10-06, 10:02 to 10:13 UTC · 6 changes · no calls made",
    ]
    setup = [line["sentence"] for line in sits[2]["lines"]]
    assert setup == ["Started the record: INT8 ResNet-50 on ImageNet", "Added question q1",
                     "Added metric top1", "Added an experiment: int8 against fp32 (e1)", "Added 2 rules to e1: c1, c3"]
    assert doc["history"]["summary"] == "10 changes recorded"
    # each line's first write is the log row of the same index
    rows = {r["log_index"]: r for r in doc["history"]["log"]}
    for s in sits:
        for line in s["lines"]:
            assert rows[line["log_index"]]["at"] == line["first"], line


def test_a_proposal_is_not_a_call_made(int8, tmp_path):
    """The session's proposal waits, so its sitting and Latest changes say no call made; the user's confirmation of the
    earlier proposal and the user's own call are the two calls made the week before."""
    rec, root = int8
    doc = dumped(root, tmp_path)
    sits = doc["history"]["sittings"]
    assert [s["calls"] for s in sits] == [0, 2, 0]
    assert [line["sentence"] for line in sits[0]["lines"]] == ["Proposed a call to reject c3, not made"]
    assert [line["sentence"] for line in sits[1]["lines"]] == ["Proposed a call to accept c1, not made", "Confirmed a call: accepted c1",
                                                               "Made a call: continued q1"]
    # a session's reason is its note, never the user's why: its proposal's, and the one the user's confirmation of a
    # proposal carries; the user's own call keeps theirs
    voices = [(line["why"], line["note"]) for line in lines_of(doc)]
    assert voices[:4] == [("", "one seed is enough for the export check"), ("", "holds on all three seeds"), ("", "holds on all three seeds"),
                          ("one more calibration size", "")], voices
    assert doc["home"]["latest"] == {"head": "2026-10-19, 09:25 UTC · 1 change", "calls_made": 0,
                                     "line": "Latest changes: 2026-10-19, 09:25 UTC · 1 change · no calls made"}


def test_no_names_in_any_sitting(tmp_path):
    """A record a human and three agents wrote, with a write of every kind rb has: no sitting, line or Latest changes
    names a writer, a session, a channel or a branch, or holds the user's words, on a page, in an export or on rb
    open's `/` (guarantee 9)."""
    rec = writers_record(tmp_path / "writers")
    for surface in ("page", "export", "open"):
        doc = dumped(rec.root, tmp_path / surface, surface)
        said = texts({"summary": doc["history"]["summary"], "sittings": doc["history"]["sittings"], "latest": doc["home"]["latest"]})
        assert doc["history"]["sittings"], surface
        for text in said:
            assert not [m for m in WRITER_MARKS if m in text], (surface, text)
            assert not [w for w in USER_WORDS if w in text], (surface, text)
            assert not re.search(r"\b(agent|human|session)\b", text), (surface, text)


def test_a_sessions_reason_is_never_the_users_why(tmp_path):
    """On the record four writers made, a line's why is only ever a reason the user gave: every line a session's write
    starts, and every confirmation of a session's call, holds its reason as its note."""
    rec = writers_record(tmp_path / "writers")
    log = rec.json(Writers.HUMAN, "log", "-n", "100000")["data"]["entries"]
    doc = dumped(rec.root, tmp_path / "page")
    lines = lines_of(doc)
    rows = {r["log_index"]: r for r in doc["history"]["log"]}
    for line in lines:
        row = log[line["log_index"]]
        assert rows[line["log_index"]]["op"] == row["op"], (line, row)   # rb log's entries, counted as the document counts them
        confirms = row["op"] == "decide" and (row.get("detail") or {}).get("confirms")
        if not row["actor"].startswith("human:") or confirms:
            assert line["why"] == "", (line, row["actor"], row["op"])
        assert not (line["why"] and line["note"]), line
    assert [line for line in lines if line["note"]] and [line for line in lines if line["why"]]
    vouched = [(line["why"], line["note"]) for line in lines if line["sentence"] == "Confirmed a call: accepted cuda of e1"]
    assert vouched == [("", "read it off the cluster image")], vouched


def test_a_sessions_interpretation_is_the_users_once_their_confirmation_covers_it(tmp_path):
    """A session links a criterion to its question with what meeting it would mean: History says the interpretation is
    not confirmed until the user confirms the design, which covers it as it reads; the session's reason for the link
    stays its note."""
    root = tmp_path / "int8"
    root.mkdir()
    rec = Writers(root)
    rec.git("init", "-q")
    rec.git("config", "user.email", "ana@example.com")
    rec.git("config", "user.name", "ana")
    ana, cc = Writers.HUMAN, CLAUDE_CODE
    rec.write(ana, "init", "INT8 ResNet-50 on ImageNet", "--no-agents")
    rec.write(ana, "question", "add", "Does INT8 keep top-1 within half a point of fp32?", "--id", "q1")
    rec.write(ana, "metric", "add", "top1", "--maximize", "--unit", "acc")
    rec.write(cc, "experiment", "add", "int8 against fp32", "--id", "e1", "-q", "q1", "--baseline", "fp32", "--candidate", "int8")
    rec.write(cc, "criterion", "add", "INT8 loses at most half a point", "-e", "e1", "--metric", "change.top1", "--at-least", "-0.005", "--id", "c1")
    rec.write(cc, "criterion", "file", "c1", "q1", "--when-met", "INT8 keeps top-1 within half a point", "--why", "the same comparison")

    def told(doc: dict) -> list:
        return [(line["sentence"], line["why"], line["note"]) for line in lines_of(doc)
                if line["sentence"].startswith(("Added a note on what rule", "Confirmed the design"))]

    assert told(dumped(root, tmp_path / "before")) == [("Added a note on what rule c1 means for q1, not confirmed", "", "the same comparison")]
    waiting = rec.json(ana, "status")["data"]["designs_pending"]
    assert [d["experiment"] for d in waiting] == ["e1"], waiting
    assert re.fullmatch(r"rb confirm e1@[0-9a-f]{64}", waiting[0]["confirm"]), waiting[0]["confirm"]
    rec.write(ana, *waiting[0]["confirm"].split()[1:])
    assert told(dumped(root, tmp_path / "after")) == [("Added a note on what rule c1 means for q1", "", "the same comparison"),
                                                      ("Confirmed the design of e1", "", "")]


# ---------------------------------------------------------------- rb open, and the user's seen marker

def served(root: Path, **env) -> tuple:
    said = root.parent / f"open-{time.monotonic_ns()}.txt"
    with open(said, "wb") as err:
        proc = subprocess.Popen([*command(), "open", "--port", "0"], cwd=root, env=env_of(**env), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=err)
    deadline = time.monotonic() + 60
    while not (m := re.search(r"^rb open · (http://[0-9.]+:\d+)/$", said.read_text(encoding="utf-8", errors="replace"), flags=re.M)):
        if proc.poll() is not None or time.monotonic() > deadline:
            raise AssertionError(said.read_text(encoding="utf-8", errors="replace"))
        time.sleep(0.05)
    return proc, m.group(1)


def stop(proc) -> None:
    proc.send_signal(signal.SIGINT) if sys.platform != "win32" else proc.terminate()   # Ctrl-C, as rb open says
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


def get(url: str) -> str:
    """What rb open answers at `url`, an error's body included: the shell's stubs answer one until their groups fill them."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # never a proxy near loopback
    try:
        with opener.open(url, timeout=30) as r:
            return r.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.read().decode("utf-8", errors="replace")


def visit(url: str) -> None:
    for path in ("/", "/state.json", "/card.json?ref=o:e1", "/card.json?ref=o:c1", "/page.html"):
        get(url + path)


@pytest.mark.parametrize("shell", ["", "1"], ids=["today", "shell"])
def test_rb_open_never_writes_the_seen_marker(int8, shell, monkeypatch):
    """rb open shows History and Latest changes and draws no since line: every route it serves, today's and the
    shell's, leaves the user's seen marker as their last look left it, and makes none where they never looked."""
    rec, root = int8
    marker = Path(rec.git("rev-parse", "--path-format=absolute", "--git-common-dir")) / "rb-last-seen.json"
    env = {"RB_TEST_SHELL": shell} if shell else {}
    proc, url = served(root, **env)
    try:
        for _ in range(3):
            visit(url)
    finally:
        stop(proc)
    assert not marker.exists(), "rb open made a seen marker"
    monkeypatch.chdir(root)
    for k in AGENT_VARS:
        monkeypatch.delenv(k, raising=False)
    mark_seen(root)                                            # the user's own look, in rb ui
    looked = marker.read_bytes()
    rec.write({**Writers.HUMAN, "RB_TEST_NOW": "2026-10-19T09:30:00.000+00:00"}, "note", "add", "the 128-image cell misses", "-e", "e1")
    proc, url = served(root, **env)
    try:
        for _ in range(3):
            visit(url)
    finally:
        stop(proc)
    assert marker.read_bytes() == looked, "rb open rewrote the seen marker"


# ---------------------------------------------------------------- the History view (H2), drawn from the contract's example

def drawn_page(tmp: Path, doc: dict) -> str:
    """`doc` drawn by rb page through the test hook (RB_TEST_SHELL_MODEL), in a record rb init started; a skip while the
    frame (R1) or the History view's call site (H2) is not merged."""
    for group in ("R1", "H2"):
        if group not in merged():
            pytest.skip(f"pending {group} (the History view's call site in the frame)")
    root = tmp / "rec"
    root.mkdir(parents=True, exist_ok=True)
    env = env_of(RB_ACTOR="human:ana")
    subprocess.run([*command(), "init", "INT8 ResNet-50 on ImageNet", "--no-agents"], cwd=root, env=env, check=True, capture_output=True,
                   stdin=subprocess.DEVNULL)
    model = tmp / "model.json"
    model.write_text(json.dumps(doc), encoding="utf-8")
    out = tmp / "page.html"
    r = subprocess.run([*command(), "page", "--out", str(out)], cwd=root, env={**env, "RB_TEST_SHELL": "1", "RB_TEST_SHELL_MODEL": str(model)},
                       capture_output=True, encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL)
    assert NOT_BUILT not in r.stderr, f"H2 is listed as merged, and the renderer's stub still answers: {r.stderr}"
    assert out.exists(), r.stderr + r.stdout
    return out.read_text(encoding="utf-8")


class _View(html.parser.HTMLParser):
    """The text of the element of one id, in the page's order: an inline element (a link, a span) joins the text around
    it, any other ends a run of text."""
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}

    def __init__(self, ident: str):
        super().__init__(convert_charrefs=True)
        self.ident, self.depth, self.found, self.out, self.anchors = ident, 0, False, [], []

    def handle_starttag(self, tag, attrs):
        if self.depth:
            if tag == "a":
                self.anchors.append(dict(attrs))
            if tag not in self.VOID:
                self.depth += 1
            if tag not in ("a", "span"):
                self.out.append(" ")
        elif dict(attrs).get("id") == self.ident:
            self.depth, self.found = 1, True

    def handle_endtag(self, tag):
        if self.depth and tag not in self.VOID:
            self.depth -= 1
            if tag not in ("a", "span"):
                self.out.append(" ")

    def handle_data(self, data):
        if self.depth:
            self.out.append(data)


def section(page: str, ident: str) -> str:
    """The view `ident` as a reader reads it: its text in order, spaces folded."""
    v = _View(ident)
    v.feed(page)
    assert v.found, f"no view #{ident}"
    return " ".join("".join(v.out).split())


def anchors(page: str, ident: str) -> list[dict]:
    """The attributes of every link inside the view `ident`, in the page's order."""
    v = _View(ident)
    v.feed(page)
    assert v.found, f"no view #{ident}"
    return v.anchors


def owner_example() -> dict:
    return json.loads((HERE / "int8-demo.owner.view.json").read_text(encoding="utf-8"))


def test_history_draws_every_sitting_and_the_latest_changes(tmp_path):
    """History holds the summary, every sitting's head and each line's sentence and why, newest first; the Overview holds
    the Latest changes line and the newest sitting's lines."""
    doc = owner_example()
    page = drawn_page(tmp_path, doc)
    history, overview = section(page, "history"), section(page, "overview")
    assert doc["history"]["summary"] in history
    at = []
    for s in doc["history"]["sittings"]:
        assert " ".join(s["head"].split()) in history, s["head"]
        at.append(history.index(" ".join(s["head"].split())))
        for line in s["lines"]:
            assert line["sentence"] in history, line["sentence"]
            if line["why"]:
                assert f"why: {line['why']}" in history, line["why"]
    assert at == sorted(at), "sittings read newest first"
    assert doc["home"]["latest"]["line"] in overview
    for line in doc["history"]["sittings"][0]["lines"]:
        assert line["sentence"] in overview, line["sentence"]


def test_a_sessions_reason_is_drawn_as_rbs_note(tmp_path):
    """A line whose reason is a session's (`note`) reads "note: ... · not confirmed", never as a why in the user's voice."""
    doc = owner_example()
    line = doc["history"]["sittings"][0]["lines"][2]
    assert line["sentence"] == "Proposed a call to accept c3, not made"
    reason = "one seed is enough for the export check"
    line["why"], line["note"] = "", reason
    history = section(drawn_page(tmp_path, doc), "history")
    assert f"note: {reason} · not confirmed" in history
    assert f"why: {reason}" not in history


def test_a_confirmed_calls_note_is_said_apart_from_the_call(int8, tmp_path):
    """The user confirmed the call, never the reason the session gave for it: the confirmation's line says the call it
    confirmed and the reason in rb's note form alone, as rb status says it ("· note: ..."), never "confirmed" beside
    "· not confirmed"; the proposal's note keeps "· not confirmed", since nothing of it is confirmed."""
    rec, root = int8
    dumped(root, tmp_path)
    history = section((tmp_path / "page.html").read_text(encoding="utf-8"), "history")
    assert "Confirmed a call: accepted c1 · note: holds on all three seeds" in history, history
    assert "Confirmed a call: accepted c1 · note: holds on all three seeds · not confirmed" not in history, history
    assert "Proposed a call to accept c1, not made · note: holds on all three seeds · not confirmed" in history, history
    assert history.count("· not confirmed") == 2, history


def test_history_gives_one_raw_log_link_a_day_and_counts_changes(int8, tmp_path):
    """Design pass 1: a line leads with what happened and carries no count of log writes; each day has one Raw log link,
    to the newest row of that day in the log below; the summary counts changes recorded."""
    rec, root = int8
    doc = dumped(root, tmp_path)
    page = (tmp_path / "page.html").read_text(encoding="utf-8")
    history = section(page, "history")
    assert "10 changes recorded" in history and "in the log" not in history, history
    days = sorted({s["first"].split(" ")[0] for s in doc["history"]["sittings"]}, reverse=True)
    links = re.findall(r'<a class="rawl" href="#(log-\d+)" aria-label="Raw log of (\d{4}-\d\d-\d\d)">Raw log</a>', page)
    assert [d for _, d in links] == days, links
    for target, day in links:
        assert f'id="{target}"' in page, target
        newest = max(r["log_index"] for r in doc["history"]["log"] if r["at"].startswith(day))
        assert target == f"log-{newest}", (target, day)
    # the log's own heading says "Raw log" too (strings.rs LOG): count the day links, never the words, and only
    # inside History's view
    assert len(re.findall(r'<a class="rawl"', page)) == len(days), links
    inside = [(a["href"], a["aria-label"]) for a in anchors(page, "history") if a.get("class") == "rawl"]
    assert inside == [(f"#{t}", f"Raw log of {d}") for t, d in links], inside


def sittings_name_runs_as_the_log_does(root: Path, tmp: Path) -> list:
    """FIX9: each run and analysis a sitting names, said as the model's History log row says it (`runs.{id}.named`,
    "record run <its words>"), never by the id rb minted, which its link keeps; the sentences that name a run."""
    doc = dumped(root, tmp)
    runs, analyses = doc["runs"], doc["analyses"]
    minted = [i for i in [*runs, *analyses] if re.fullmatch(r"(run|a)[a-z2-7]{6}", i)]
    assert runs and minted, "the record holds runs rb minted ids for"
    named = []
    for line in lines_of(doc):
        shown = [i for i in minted if re.search(rf"\b{i}\b", line["sentence"])]
        assert not shown, (line["sentence"], shown)
        # a line of many runs counts them by experiment and names none
        for r in (r for r in line["refs"] if r in runs and not re.match(r"Recorded \d+ runs: \d+ on ", line["sentence"])):
            # the run's words as its log row says them, "record <words>" then its reason
            rows = [x["sentence"] for x in doc["history"]["log"] if x["op"] == "record" and x["refs"][:1] == [r]]
            assert len(rows) == 1 and rows[0].startswith("record "), rows
            words = rows[0][len("record "):].split(": ")[0]
            assert words == runs[r].get("named", words) and (words.startswith(f"run {runs[r]['label']}") or words.startswith("a run of ")), (words, runs[r])
            # where History's own "Recorded" opens the line, a run told apart by when says it once: "<run> at <when>"
            said = (words, re.sub(r", recorded ", " at ", words, count=1))
            assert any(re.search(rf"(: |; |, |replacing |^Recorded ){re.escape(w)}(;|,|$)", line["sentence"]) for w in said), (line["sentence"], words)
            assert len(re.findall(r"\brecorded\b", line["sentence"], re.I)) <= 1 + line["sentence"].count("replacing "), line["sentence"]
            named.append(line["sentence"])
        for a in (a for a in line["refs"] if a in analyses and a in minted):
            assert f"analysis {analyses[a]['title'].strip()}" in line["sentence"], line["sentence"]
    # drawn, a run's words link to its own card, in the sentence's type
    page = (tmp / "page.html").read_text(encoding="utf-8")
    history = section(page, "history")
    assert not [i for i in minted if re.search(rf"\b{i}\b", history)], history
    links = {a["href"] for a in anchors(page, "history")}
    assert any(h in links for r in runs for h in (f"#run-{r}", "#runs")), links
    return named


def test_a_sitting_names_each_run_as_its_log_row_does(int8_demo, tmp_path):
    """The INT8 demo: Look 9 found its sittings naming runs by id ("Recorded 4 runs: runvgrvb3, ...", "replacing
    runn3j4ru (runqmiqv5)")."""
    named = sittings_name_runs_as_the_log_does(Path(getattr(int8_demo, "root", int8_demo)), tmp_path)
    assert any(s.startswith("Recorded ") and " runs: run " in s for s in named), named
    assert any(", replacing run " in s for s in named), named


def test_a_run_and_the_run_that_replaced_it_read_apart(int8, tmp_path):
    """Two runs with one label, the second replacing the first, minutes apart; and an analysis rb minted an id for, by its
    title."""
    rec, root = int8
    ana = Writers.HUMAN
    at = lambda k: {**ana, "RB_TEST_NOW": k}  # noqa: E731
    rec.write(at("2026-10-20T08:58:00.000+00:00"), "spec", "set", "e1", "seed", "--per-run")
    first = rec.write(at("2026-10-20T09:00:00.000+00:00"), "run", "add", "e1", "fp32.top1=75.6", "int8.top1=75.1", "--set", "seed=1")
    rec.write(at("2026-10-20T09:02:00.000+00:00"), "run", "add", "e1", "fp32.top1=75.6", "int8.top1=75.4", "--set", "seed=1",
              "--replaces", first, "--why", "the first read calibrated on training images")
    log = [json.loads(x) for x in (root / ".rb" / "log.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    again = [x["id"] for x in log if x.get("op") == "record"][-1]
    an = rec.write(at("2026-10-20T09:03:00.000+00:00"), "analysis", "add", "e1", "--compare", "int8/fp32:top1=-0.2[-0.3,-0.1]", "--method",
                   "paired bootstrap over images", "--reads", again, "--title", "per-image accuracy, int8 against fp32")
    assert re.fullmatch(r"run[a-z2-7]{6}", again) and re.fullmatch(r"a[a-z2-7]{6}", an), (again, an)
    named = sittings_name_runs_as_the_log_does(root, tmp_path)
    [line] = {s for s in named if ", replacing " in s}
    assert line == "Recorded a run on e1: run fp32 and int8 · seed 1 at 2026-10-20 09:02 UTC, replacing run fp32 and int8 · seed 1, " \
                   "recorded 2026-10-20 09:00 UTC", line
    doc = json.loads((tmp_path / "page.view.json").read_text(encoding="utf-8"))
    assert "Recorded an analysis on e1: analysis per-image accuracy, int8 against fp32" in [l["sentence"] for l in lines_of(doc)]


def test_an_untitled_analysis_and_a_proposal_taken_back_are_said_by_what_they_are(int8, tmp_path):
    """Review of FIX9: an analysis rb minted an id for with no title is said by its experiment and when it was recorded,
    in M's settled words ("an analysis on e1, recorded <minute> UTC", 0.6.1 item 9), with "recorded" said once where
    History's own "Recorded" opens the line; two proposals a session takes back together read as proposals, never as
    calls made."""
    rec, root = int8
    at = lambda who, k: {**who, "RB_TEST_NOW": k}  # noqa: E731
    an = rec.write(at(Writers.HUMAN, "2026-10-21T10:00:00.000+00:00"), "analysis", "add", "e1", "--compare", "int8/fp32:top1=-0.2[-0.3,-0.1]",
                   "--method", "paired bootstrap over images")
    ids = [rec.write(at(CLAUDE_CODE, f"2026-10-22T10:0{k}:00.000+00:00"), "decide", "e1", action, "--why", "two seeds settle it")
           for k, action in enumerate(("park", "continue"))]
    for k, d in enumerate(ids):
        rec.write(at(CLAUDE_CODE, f"2026-10-22T10:0{k + 3}:00.000+00:00"), "retract", d, "--why", "asked for too early")
    doc = dumped(root, tmp_path)
    said = [l["sentence"] for l in lines_of(doc)]
    assert "Recorded an analysis on e1 at 2026-10-21 10:00 UTC" in said and not [s for s in said if an in s], said
    # the model's rows say it in the same words, which History's names_of gives them
    rows = [x["sentence"] for x in doc["history"]["log"] if x["op"] == "analyze"]
    assert rows and all("an analysis on e1, recorded 2026-10-21 10:00 UTC" in r and an not in r for r in rows), rows
    assert "Retracted 2 calls: a proposed call to park e1, not made; a proposed call to continue e1, not made" in said, said
