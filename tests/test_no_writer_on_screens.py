"""No screen says who made a write.

The pages (rb page, and the page rb export writes, alone or in a bundle), rb open (its tab, its page, /state.json and
/card.json) and rb ui name no writer, a human or an agent: no name or id, no agent session, no channel (via), nothing
of how rb knew (detected:), nothing rb keeps of an agent (its model, provider or version), and no branch an agent's
work sat on. Nor do they use a word that names a role (agent, human, session). rb open and rb ui are the user's own
screens: they say the user's words, without the tag rb's text puts on them ('your words:'), which would say an agent
wrote the line, and rb open says who the user is recorded as. A page anyone may be sent says neither.

The back end keeps every writer field (tests/test_no_writer_in_text.py). Every name, session id and branch here is made
up (tests/rbtest.py: writers_record).
"""
import asyncio
import html as html_mod
import json
import re
import shutil
import urllib.error
import urllib.request

import pytest

from rbtest import (AGENT_BRANCHES, AGENT_FACTS, CLAUDE_CODE, FOUND_WORKTREE, RB_WORDS_FOR_THE_READERS_AGENT, ROLE_WORDS, ROLE_WORDS_IN_AUTHOR_TEXT, USER_WORDS, WRITER_FIELDS, Open,
                    Places, Writers, as_the_user, mark_seen, writer_marks, writers_record)

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="the record is kept in a git repository")

_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # never let a proxy near loopback


@pytest.fixture(scope="module")
def rec(tmp_path_factory):
    return writers_record(tmp_path_factory.mktemp("writers") / "work")


@pytest.fixture(scope="module")
def platform(rec) -> str:
    """The machine's platform as the runs' receipts keep it: the user's own screens show it, and a page leaves it out."""
    said = rec.json(Writers.HUMAN, "show", rec.ids["run_claude"])["data"]["object"]["receipt"]["environment"]["platform"]
    assert said
    return said


@pytest.fixture
def copy(rec, tmp_path, monkeypatch):
    """The record, copied for a screen that looks at the repository as it draws (rb open and rb ui journal what they
    see), with this process at the user's own terminal there."""
    shutil.copytree(rec.root, tmp_path / "work", symlinks=True)
    as_the_user(monkeypatch)
    for k in ("GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM", "RB_TELEMETRY", "DO_NOT_TRACK"):
        monkeypatch.setenv(k, rec.env[k])
    monkeypatch.chdir(tmp_path / "work")
    return Writers(tmp_path / "work")


def on_screen(text: str) -> list:
    """What in a screen's text says who wrote or names a role: a writer's mark, a fact rb keeps of an agent, the tag on
    the user's words that only an agent's write carries, or a role word or 'via' as a word, outside the record's own
    author text, which is shown as written."""
    found = writer_marks(text) + [f for f in (*AGENT_FACTS, "your words") if f in text]
    for authored in (*ROLE_WORDS_IN_AUTHOR_TEXT, *RB_WORDS_FOR_THE_READERS_AGENT):
        text = text.replace(authored, "")
    return found + [w for w in (*ROLE_WORDS, "via") if re.search(rf"\b{w}s?\b", text, re.I)]


def texts(node, path: tuple = ()) -> list:
    """Every text a screen's data carries, wherever it sits, the fields that kept who wrote included: (path, text)."""
    if isinstance(node, dict):
        return [t for k, v in node.items() for t in texts(v, (*path, k))]
    if isinstance(node, list):
        return [t for i, v in enumerate(node) for t in texts(v, (*path, i))]
    return [(path, node)] if isinstance(node, str) else []


def in_data(node, *allowed: tuple) -> list:
    """What in a screen's data says who wrote or names a role, but at the paths `allowed`."""
    return [(path, text, on_screen(text)) for path, text in texts(node) if path not in allowed and on_screen(text)]


def page_checks(source: str, platform: str) -> None:
    """A page anyone may be sent, its source whole, as a reader reads it: no writer, no role word, none of the user's
    words or their own line, and not the machine's platform a receipt keeps."""
    text = html_mod.unescape(source)
    assert not on_screen(text), on_screen(text)
    assert "your words" not in text and not [w for w in USER_WORDS if w in text]
    assert not re.search(r"\byou: \S+ · ", text) and "recorded by" not in text
    # the receipt that would hold the platform is drawn, with the environment it keeps, and the platform is not in it
    assert platform not in text and "'platform'" not in text and '<dl class="rx" id="run-' in text and "<dt>environment</dt>" in text


def test_rb_page_names_no_writer_and_says_none_of_the_users_words(rec, tmp_path, platform):
    """rb page, its source whole: who made each write stays in the record, and so do the user's words and the
    machine's platform, since a page is sent to anyone. The page's own lines say what rb keeps without saying who."""
    rec.rb(Writers.HUMAN, "page", "--out", str(tmp_path / "page.html"))
    html = (tmp_path / "page.html").read_text(encoding="utf-8")
    page_checks(html, platform)
    assert "Run it in your terminal; rb records the call and your reason." in html
    assert "Each call keeps its reason and its date. A call does not change a finding." in html
    assert "Each run keeps when its output was produced and when it was recorded, the repository's state when it was recorded, and what it read and made." in html
    # the two runs rb found and nobody recorded, one of them in a worktree removed since, with how to record each
    found = "in the repository, not in the record · found by rb watching the repository · record it from your screen, or with the command rb reconcile gives for it"
    assert html.count(found) == 2, html.count(found)
    assert "Declaration and scope" in html and "both read one change" in html     # the relation's declarations, as declared


def test_an_export_is_a_page_that_names_no_writer_and_carries_the_record_only_when_asked(rec, tmp_path, platform):
    """rb export writes the page alone: no bundle, no record/ and no rb clone, and nothing in it, its README or its
    manifest says who wrote or names a role. With --with-record it is a bundle whose page is a page like any other,
    while the record it carries keeps every writer, and rb says so."""
    said = rec.rb(Writers.HUMAN, "export", "--out", str(tmp_path / "page"))
    page = tmp_path / "page"
    assert sorted(p.name for p in page.iterdir()) == ["README.md", "manifest.json", "page.html"]
    page_checks((page / "page.html").read_text(encoding="utf-8"), platform)
    for name in ("README.md", "manifest.json"):
        text = (page / name).read_text(encoding="utf-8")
        assert not on_screen(text) and "rb clone" not in text, name
    assert "rb clone" not in (page / "page.html").read_text(encoding="utf-8") and "who made each write" not in said
    said = rec.rb(Writers.HUMAN, "export", "--with-record", "--url", "https://example.org/mixed", "--out", str(tmp_path / "bundle"))
    bundle = tmp_path / "bundle"
    shown = (bundle / "page.html").read_text(encoding="utf-8")
    page_checks(shown, platform)
    assert "rb clone https://example.org/mixed/bundle.zip" in shown and (bundle / "bundle.zip").is_file()
    assert "writer-ana" in (bundle / "record" / ".rb" / "log.jsonl").read_text(encoding="utf-8")   # the record keeps them
    assert platform in (bundle / "record" / ".rb" / "experiments" / "e1" / "runs" / f"{rec.ids['run_claude']}.yaml").read_text(encoding="utf-8")
    assert "who made each write" in said and "chat session" in said


def _get(url: str) -> tuple:
    try:
        with _OPENER.open(url, timeout=30) as r:
            return r.status, r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8")


def test_rb_open_names_no_writer_in_its_tab_or_its_data(rec, copy, platform):
    """rb open's tab (its source whole), its page, and the data the tab draws (/state.json, and /card.json for a run, a
    criterion, a decision, an experiment, a question, a resource and each run rb found that nobody recorded, one of them
    in a worktree removed since) name no writer and no role, in any field: those
    that kept who wrote hold nothing. The user's own line says who they are recorded as. The user's words (a call they
    confirmed) are said with no tag, since rb open is the user's own screen, and words an agent session gave are said
    as given, never as theirs; the call that still waits for the user's confirmation is in a group of its own."""
    server = Open(copy.root)
    try:
        status, tab = _get(server.url + "/")
        assert status == 200 and not on_screen(html_mod.unescape(tab)), on_screen(html_mod.unescape(tab))
        status, page = _get(server.url + "/page.html")
        assert status == 200
        page_checks(page, platform)
        status, body = _get(server.url + "/state.json")
        state = json.loads(body)
        assert status == 200 and not in_data(state, ("home", "you")), in_data(state, ("home", "you"))
        assert state["home"]["you"] == "you: writer-ana · from git"
        said = [text for _, text in texts(state)]
        assert 'words (as given): "make it -0.5"' in said and '"make it -0.5"' not in said
        assert any(text.endswith('one more seed · "check it again"') for text in said)
        assert [(p["kind"], p["text"], p["confirm"]) for p in state["home"]["pending"] if p["kind"] != "design"] == [
            ("proposed", "park e2 · note: waiting on data", f"rb confirm {rec.ids['parked']}")]
        # each design a session wrote waits apart, named by its experiment, with no writer
        assert [p["text"] for p in state["home"]["pending"] if p["kind"] == "design"] and not in_data(state["home"]["pending"])
        for ref in (rec.ids["run_claude"], "c2", rec.ids["investigating"], "e1", "q2", "holdout"):
            status, body = _get(f"{server.url}/card.json?ref=o:{ref}")
            assert status in (200, 404) and not in_data(json.loads(body)), (ref, in_data(json.loads(body)))
        _, body = _get(server.url + "/card.json?ref=o:c2")
        assert json.loads(body)["card"]["who_line"].endswith(' · words (as given): "make it -0.5"')
        facts = []
        for item in state["home"]["found"]:
            status, body = _get(f"{server.url}/card.json?ref={item['request_id']}")
            card = json.loads(body)
            assert status == 200 and not in_data(card), (item, in_data(card))
            facts += card["card"]["facts"]
        assert len(state["home"]["found"]) == 2 and len([f for f in facts if " in a worktree removed since · exit 0 · " in f]) == 1, facts
        # the user's own screen shows the machine's platform, on a run's receipt and its card
        assert platform in {r["receipt"]["environment"].get("platform") for r in state["home"]["runs"]}
        _, body = _get(f"{server.url}/card.json?ref=o:{rec.ids['run_claude']}")
        assert any(f.startswith("environment: ") and f"platform {platform}" in f for f in json.loads(body)["card"]["facts"])
    finally:
        server.close()


def test_rb_opens_data_reads_the_same_whoever_wrote(rec, copy):
    """In rb open's /state.json, a question link and a relation declaration the user made and those an agent made
    carry the same keys, and the same "" in every field that kept who wrote, how rb knew, the channel or the session,
    though the record keeps a session for the agent's alone: neither a value nor a null tells them apart."""
    links = {c: copy.json(Writers.HUMAN, "show", c)["data"]["object"]["question_links"][0] for c in ("c1", "c2")}
    assert (links["c1"]["session"], links["c2"]["session"]) == (None, CLAUDE_CODE["CLAUDE_CODE_SESSION_ID"])
    server = Open(copy.root)
    try:
        _, body = _get(server.url + "/state.json")
    finally:
        server.close()
    [q1] = [b for b in json.loads(body)["home"]["briefs"] if b["id"] == "q1"]
    rows = {row["criterion"]: row for row in q1["inquiry"]["direct"]}
    [relation] = q1["inquiry"]["relations"]
    declared = {d["why"]: d for d in relation["declarations"]}
    # writer-ana filed c1 under q1 and related it to c2; Claude Code filed c2 and related it back
    for mine, its in ((rows["c1"]["link"], rows["c2"]["link"]), (declared["both read one change"], declared["the same change, read from c2"])):
        assert mine.keys() == its.keys()
        kept = [k for k in WRITER_FIELDS if k in mine]
        assert "session" in kept and {k: mine[k] for k in kept} == {k: its[k] for k in kept} == dict.fromkeys(kept, ""), (mine, its)


def _drawn(screen) -> list:
    """Everything a screen draws as text: its lines, its rows, the titles on its panels and its keys' labels."""
    from textual.widgets import OptionList, Static
    out = [str(w.render()) for w in screen.query(Static)]
    for w in screen.query("*"):
        out += [str(t) for t in (w.border_title, w.border_subtitle) if t]
    for options in screen.query(OptionList):
        out += [str(options.get_option_at_index(i).prompt) for i in range(options.option_count)]
    return out + [b.description for b in screen.BINDINGS]


def test_rb_ui_names_no_writer(rec, copy):
    """rb ui, drawn headless: the home screen with the line saying what was written since the user last looked
    (whoever wrote it), a card of every kind, the card of each run rb found that nobody recorded with what its y asks,
    and what is still open in the record (the a key) name no writer and no role, nor does the group of calls that wait
    for the user's confirmation. Words an agent session gave are said on a criterion's card as given, never as the
    user's."""
    from rabbit_brain.tui import CardScreen, HomeScreen, ObjectScreen, OpenItemsScreen, RBApp, Reason, home_view

    mark_seen(copy.root)                                       # the user looked; then an agent wrote
    copy.rb(CLAUDE_CODE, "note", "add", "a later look at e1", "-e", "e1")
    copy.rb(CLAUDE_CODE, "run", "add", "e1", "wide.top1=0.74", "base.top1=0.70", "--set", "seed=5", "--set", "width=256")
    subjects = [rec.ids["run_claude"], "c2", "e1", "q2", "holdout"]
    found = [item.request_id for item in home_view(copy.root).found]
    drawn: dict = {}

    async def drive():
        app = RBApp(copy.root)
        async with app.run_test(size=(160, 48)) as pilot:
            for _ in range(150):
                if isinstance(app.screen, HomeScreen) and app.screen.query_one("#tree-list").option_count:
                    break
                await pilot.pause(0.1)
            await pilot.pause(0.5)
            drawn["home"] = _drawn(app.screen)
            for subject in subjects:
                app.push_screen(ObjectScreen(copy.root, subject))
                await pilot.pause(0.3)
                drawn[subject] = _drawn(app.screen)
                app.pop_screen()
                await pilot.pause(0.2)
            for request_id in found:
                app.push_screen(CardScreen(copy.root, request_id))
                await pilot.pause(0.3)
                drawn[f"found {request_id}"] = _drawn(app.screen)
                await pilot.press("y")
                for _ in range(50):
                    if isinstance(app.screen, Reason):
                        break
                    await pilot.pause(0.1)
                drawn[f"found {request_id}: y"] = _drawn(app.screen)
                await pilot.press("escape")
                await pilot.pause(0.2)
                app.pop_screen()
                await pilot.pause(0.2)
            await pilot.press("a")
            for _ in range(100):
                if isinstance(app.screen, OpenItemsScreen):
                    break
                await pilot.pause(0.1)
            drawn["open in the record"] = _drawn(app.screen)

    asyncio.run(drive())
    leaks = [(where, line, on_screen(line)) for where, lines in drawn.items() for line in lines if on_screen(line)]
    assert not leaks, leaks
    home = "\n".join(drawn["home"])
    day = copy.json(Writers.HUMAN, "log", "-n", "1")["data"]["entries"][-1]["at"][:10]
    assert f"Since you last looked: 2 writes, 1 run recorded · last {day}" in home
    assert re.search(r"\d+ still open in the record", home) and "open in the record" in [b.description for b in HomeScreen.BINDINGS]
    assert any(line.startswith("Open in the record") for line in drawn["open in the record"])
    assert any(line.endswith(' · words (as given): "make it -0.5"') for line in drawn["c2"])
    # the call that still waits, and each design a session wrote, which waits with its gate
    designs = copy.json(Writers.HUMAN, "status")["data"]["counts"]["design_pending_count"]
    assert designs and f"TO CONFIRM ({1 + designs})" in drawn["home"] and any("park e2 · note: waiting on data" in line for line in drawn["home"])
    gone = [request_id for request_id in found if any(" in a worktree removed since · exit 0 · " in line for line in drawn[f"found {request_id}"])]
    assert len(found) == 2 and len(gone) == 1, found
    assert all(any("recorded as a run, with this note" in line for line in drawn[f"found {request_id}: y"]) for request_id in found)



# ---------------------------------------------------------------- a place is named by its branch and path, and no more

# where a place's branch and path are said on rb open's data: the labels rb computes for a place, and nowhere else
PLACE_TEXTS = {"label", "path", "branch", "record_dir", "root", "said", "shown", "label_short", "shown_short", "why_not"}


def place_text(path: tuple) -> bool:
    """Whether a text at `path` in rb open's data is a label rb computes for a place: the place shown and the places
    listed (and their versions), the other places the home lists with the command that confirms a call there, where a
    run was recorded, and a finding's notes on where its runs were recorded and what code they ran."""
    if path and path[0] in ("place", "places"):
        return path[-1] in PLACE_TEXTS
    if path and path[0] == "elsewhere":
        return path[-1] in PLACE_TEXTS | {"confirm", "calls_command"}
    return (len(path) >= 2 and path[-2] == "observed_in" and path[-1] in ("label", "label_short")) or path[-1:] in (("observed",), ("observed_short",), ("other_code",))


@pytest.fixture
def agent_worktree(tmp_path, monkeypatch):
    """The user's record, with a worktree nested in it named as agents name theirs (its branch claude/try-1, its folder
    work-codex-wt), forked before the record was committed, where Claude Code recorded a run on a changed train.py."""
    as_the_user(monkeypatch)
    rec = Places(tmp_path)
    rec.root.mkdir()
    rec.git("init", "-q")
    (rec.root / "train.py").write_text("lr = 0.1\n", encoding="utf-8")
    rec.git("add", "train.py")
    rec.git("commit", "-qm", "start")
    rec.rb("init", "INT8 ResNet-50 on ImageNet", "--no-agents")
    rec.rb("experiment", "add", "int8 against fp32", "--id", "e1", "--baseline", "fp32", "--candidate", "int8")
    rec.rb("criterion", "add", "int8 holds top1", "-e", "e1", "--metric", "change.top1", "--at-least", "-0.01", "--id", "c1")
    rec.rb("run", "add", "e1", "fp32.top1=0.761", "int8.top1=0.758")
    wt = rec.worktree(AGENT_BRANCHES[0], rec.root / ".worktrees" / FOUND_WORKTREE)
    (wt / "train.py").write_text("lr = 0.25\n", encoding="utf-8")
    rec.run_agent = rec.rb("run", "add", "e1", "fp32.top1=0.760", "int8.top1=0.757", cwd=wt, who=CLAUDE_CODE)["object"]["id"]
    for k in ("GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM", "RB_TELEMETRY", "DO_NOT_TRACK", "RB_ACTOR"):
        monkeypatch.setenv(k, rec.env[k])
    return rec


def test_rb_open_names_a_place_by_its_branch_and_path_only_in_a_place_s_label(agent_worktree):
    """A worktree an agent named is named by its branch and path verbatim, as git gives them, on the user's own screen:
    in the place shown, the places listed, where a run was recorded and a finding's note on it, and in no other field.
    None of those names who wrote, nor carries a key that would (no actor, session, agent or model), and rb adds no word
    of its own that ties a place to a writer. The page, which anyone may be sent, names no place: no branch, no path."""
    rec = agent_worktree
    server = Open(rec.root)
    try:
        _, body = _get(server.url + "/state.json")
        state = json.loads(body)
        _, card = _get(f"{server.url}/card.json?ref=o:{rec.run_agent}")
        status, page = _get(server.url + "/page.html")
    finally:
        server.close()
    marked = [(path, text) for path, text in texts(state) if on_screen(text)]
    assert marked and all(place_text(path) for path, _ in marked), [(p, t) for p, t in marked if not place_text(p)]
    # the marks a place's label carries are its branch and its path (whose folder holds "codex"), and nothing rb keeps
    # of the writer
    assert {m for _, text in marked for m in on_screen(text)} == {AGENT_BRANCHES[0], FOUND_WORKTREE, "codex"}
    for node in (state["place"], *state["places"], *state["elsewhere"], *[r["observed_in"] for r in state["home"]["runs"] if r["observed_in"]]):
        assert not set(node) & {"actor", "actor_via", "via", "session", "agent", "model", "by", "created_by", "worktree"}, node
    shown = json.dumps(state)
    assert CLAUDE_CODE["CLAUDE_CODE_SESSION_ID"] not in shown and "claude-code" not in shown and "anthropic" not in shown
    [row] = [r for r in state["home"]["runs"] if r["observed_in"]]
    listed = rec.git("worktree", "list", "--porcelain").split("worktree ")[2].splitlines()[0]
    assert row["observed_in"]["label"].startswith(f"recorded in {AGENT_BRANCHES[0]} · {listed} at ")
    assert row["observed_in"]["label"].endswith(", with uncommitted changes")
    assert json.loads(card)["card"]["facts"][-1] == row["observed_in"]["label_short"]
    # the page names no place
    assert status == 200
    for named in (rec.root.resolve().as_posix(), listed, AGENT_BRANCHES[0], FOUND_WORKTREE, "recorded in "):
        assert named not in html_mod.unescape(page), named
    rec.rb("page", "--out", str(rec.base / "page.html"))
    sent = html_mod.unescape((rec.base / "page.html").read_text(encoding="utf-8"))
    for named in (rec.root.resolve().as_posix(), listed, AGENT_BRANCHES[0], FOUND_WORKTREE, "recorded in "):
        assert named not in sent, named
