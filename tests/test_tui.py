"""The user's screen: the view model is the record's own numbers, and the keys run the same calls.

The screens draw what rb answers for them (the home view, the cards). So the tests hold those views
to the record, and drive the app: an agent's call lands as the agent's and waits in a quiet group of
its own until c confirms it, the words it came with are quoted whole where the card says the call once
the user confirms it, with no writer, a card of what rb found answers to the mouse as it does to the
keys, and a rule met on evidence with a problem is never drawn green, nor counted green on any
question that counts it.
"""
import asyncio
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from rabbit_brain.models import Envelope
from rbtest import PLACES_AGENT, as_the_user, at_home, command, confirm_designs, environment, home, in_short, main, object_card, places_record, state

HAS_GIT = shutil.which("git") is not None
pytestmark = pytest.mark.skipif(not HAS_GIT, reason="the record is played in a git repository")


@pytest.fixture
def repo(workdir, monkeypatch, tmp_path_factory, capsys):
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

    assert rb("init", "t")[0] == 0
    # The agent does the work: the criterion is agent-written, the record says so, and its words name no writer.
    monkeypatch.setenv("CLAUDECODE", "1")
    assert rb("question", "add", "does it work", "--id", "q1")[0] == 0
    assert rb("question", "add", "does it scale", "--id", "q2")[0] == 0   # nothing filed under it: the agent's backlog, not the user's
    assert rb("experiment", "add", "the experiment", "--id", "e1", "--hypothesis", "it works", "--question", "q1")[0] == 0
    assert rb("criterion", "add", "top1 holds", "-e", "e1", "--metric", "top1", "--at-least", "0.7", "--noise", "0.001", "--id", "c1", "-q", "q1")[0] == 0
    assert rb("run", "add", "e1", "top1=0.76")[0] == 0
    monkeypatch.delenv("CLAUDECODE")
    return rb


async def until(pilot, ready, timeout: float = 15.0) -> None:
    """Pause until the screen is in the state the next key acts on; fixed pauses alone are too short on a loaded
    machine."""
    for _ in range(int(timeout * 10)):
        if ready():
            return
        await pilot.pause(0.1)


def _shown(screen) -> str:
    """Every line the screen draws, as plain text."""
    from textual.widgets import Static
    return "\n".join(str(w.render()) for w in screen.query(Static))


def _colour_of(line, part: str) -> str:
    """The colour a line the screen drew puts `part` in: rb's own words for it ('1 criterion met'), found as the screen
    says them ('1 rule met')."""
    from rabbit_brain.tui import plain_word
    at = line.plain.index(plain_word(part))
    return next(str(s.style) for s in line.spans if s.start <= at < s.end)


def status_counts(capsys) -> tuple:
    """How many items rb status lists under Needs judgment and under Can be done now."""
    assert main(["status"]) == 0
    text = capsys.readouterr().out
    return tuple(int(m.group(1)) if m else 0 for m in (re.search(rf"^{t} \((\d+)\)$", text, re.M) for t in ("Needs judgment", "Can be done now")))


def test_the_home_view_counts_only_what_the_user_can_clear(repo, monkeypatch, capsys):
    # An agent's call lands, recorded as the agent's, proposed: it waits in a group of its own, never counted as a call
    # waiting on the user, and changes nothing until the user confirms it.
    assert confirm_designs() == ["e1"]                       # the agent set it up: the user confirms its design
    monkeypatch.setenv("CLAUDECODE", "1")
    code, env = repo("decide", "e1", "accept", "-m", "the run holds")
    monkeypatch.delenv("CLAUDECODE")
    proposed = env.data["object"]
    assert code == 0 and proposed["by"] == "agent:claude-code"
    h = home()
    assert [(p.kind, p.text, p.confirm) for p in h.pending] == [("proposed", "accept e1 · note: the run holds", f"rb confirm {proposed['id']}")]
    assert h.waiting == 0 and h.briefs[0].ruled == []
    assert next(r for r in h.tree if r.id == "e1").standing == "open · 1 criterion met · not locked"
    assert repo("confirm", proposed["id"])[0] == 0
    h = home()
    assert h.found == [] and not hasattr(h, "your_call") and h.pending == []
    humans, agents = status_counts(capsys)                   # only an agent's items, as rb status lists them
    assert humans == 0 and agents and h.waiting == 0
    assert h.quiet == f"Nothing waits on you. 1 rule is met; {agents} thing{' is' if agents == 1 else 's are'} still open in the record."
    assert h.agent_line == f"{agents} open" and h.open_line == f"{agents} still open in the record"
    kinds = [(r.kind, r.id) for r in h.tree]
    assert ("question", "q1") in kinds and ("experiment", "e1") in kinds
    assert {k for k, _ in kinds} <= {"question", "experiment", "resource"}
    exp_row = next(r for r in h.tree if r.id == "e1")
    assert exp_row.standing == "accepted · 1 criterion met · not locked"
    assert h.briefs[0].ruled == ['accepted: "the experiment" · note: the run holds']


def test_the_head_says_where_the_record_began_in_one_quiet_line(repo):
    """A run made before the record began: under the title, one dim line, the record line's sentence, says when rb
    began keeping the record and what was recorded afterwards; with nothing recorded afterwards, there is no such line."""
    from rabbit_brain.tui import DIM, RBApp

    async def drawn():
        app = RBApp(Path.cwd())
        async with app.run_test(size=(120, 36)) as pilot:
            await pilot.pause(0.3)
            began = app.screen.query_one("#began")
            return began.display, str(began.content)

    assert home().began is None and asyncio.run(drawn())[0] is False
    assert repo("run", "add", "e1", "top1=0.75", "--produced-at", "2026-05-07T12:00:00Z")[0] == 0
    line = home().began
    assert line.startswith("Kept with rb since ") and line.endswith(". 1 run was recorded afterwards, not as it happened.")
    assert asyncio.run(drawn()) == (True, f"[{DIM}]{line}[/{DIM}]")


def test_the_line_under_the_work_never_says_nothing_waits_while_a_call_does(repo, monkeypatch, capsys):
    """A criterion that misses with nobody's decision on it is a call rb status lists for the user, so the line under
    the work says a call waits on you, naming no command or section of rb's, drawn in the colour of what is yours to
    do, never green; the rest of what rb status lists is still open in the record. Once the call is made, nothing waits
    on you again. No line names a role."""
    from rabbit_brain.tui import ACCENT, RBApp

    monkeypatch.setenv("CLAUDECODE", "1")
    assert repo("criterion", "add", "top1 stays high", "-e", "e1", "--metric", "top1", "--at-least", "0.9", "--id", "c2")[0] == 0
    monkeypatch.delenv("CLAUDECODE")
    humans, agents = status_counts(capsys)
    h = home()
    assert humans == 1 and h.waiting == 1
    assert h.quiet == "1 call waits on you."
    assert h.agent_line == f"{agents} open" and h.open_line == f"{agents} still open in the record"

    async def drive():
        app = RBApp(Path.cwd())
        async with app.run_test(size=(120, 36)) as pilot:
            await pilot.pause(0.3)
            assert app.screen.query_one("#quiet").content == f"[{ACCENT}]1 call waits on you.[/{ACCENT}]"
            assert app.screen.query_one("#tree-panel").border_subtitle == f"{agents} still open in the record"

    asyncio.run(drive())
    assert repo("decide", "c2", "reject", "-m", "0.9 was never the bar")[0] == 0
    assert confirm_designs() == ["e1"]                       # the agent set it up: the user confirms its design
    humans, agents = status_counts(capsys)
    h = home()
    assert humans == 0 and h.waiting == 0
    assert h.quiet == f"Nothing waits on you. 1 rule is met; {agents} thing{' is' if agents == 1 else 's are'} still open in the record."


def test_a_call_made_on_your_words_shows_them_whole_on_the_card(repo, monkeypatch):
    """When an agent acts on the user's own words, the words given with its call are said as given, never as the
    user's, while the call waits for the user. Confirmed, they are part of the call: right after the reason, whole and
    verbatim, on the experiment's card, in the brief, and on the card the screen opens, with no tag."""
    words = "if top1 stays above 0.7 on base and wide, accept e1 [both seeds]"
    monkeypatch.setenv("CLAUDECODE", "1")
    code, env = repo("decide", "e1", "accept", "-m", "the run holds", "--words", words)
    monkeypatch.delenv("CLAUDECODE")
    assert code == 0 and (env.data["object"]["by"], env.data["object"]["words"]) == ("agent:claude-code", words)
    assert [p.text for p in home().pending if p.kind != "design"] == [f'accept e1 · note: the run holds · words (as given): "{words}"']
    assert not [f for f in object_card("e1").facts if f.startswith("decision: ")]
    code, env = repo("confirm", env.data["object"]["id"])
    d = env.data["confirmed"][0]["object"]
    assert code == 0 and (d["by"], d["words"]) == ("human:t", words)
    said = f'"{words}"'
    card = object_card("e1")
    assert "your words" not in " ".join([*card.facts, *home().briefs[0].ruled])
    assert f"decision: accepted {d['at'][:10]} · note: the run holds · {said}" in card.facts
    assert home().briefs[0].ruled == [f'accepted: "the experiment" · note: the run holds · {said}']
    from rabbit_brain.tui import ObjectScreen, RBApp

    async def drive():
        app = RBApp(Path.cwd())
        async with app.run_test(size=(120, 36)) as pilot:
            await pilot.pause(0.3)
            tree = app.screen.query_one("#tree-list")
            ids = [tree.get_option_at_index(i).id for i in range(tree.option_count)]
            tree.focus()
            tree.highlighted = ids.index("o:e1")
            await pilot.pause(0.2)
            await pilot.press("enter")
            await until(pilot, lambda: isinstance(app.screen, ObjectScreen))
            assert isinstance(app.screen, ObjectScreen) and app.screen.subject == "e1"
            assert f"decision: accepted {d['at'][:10]} · note: the run holds · {said}" in _shown(app.screen)
            await pilot.press("escape")

    asyncio.run(drive())


def test_c_on_rb_ui_confirms_the_highlighted_call_that_waits(repo, monkeypatch):
    """A call an agent session proposed waits on rb ui in a quiet group of its own, never under YOUR CALL; c confirms
    the highlighted one through the same rb confirm the user would type, only once the group has focus, the other keeps
    waiting, and the confirmed call is the user's."""
    from rabbit_brain.tui import RBApp
    assert confirm_designs() == ["e1"]                       # the agent set it up: the user confirms its design
    monkeypatch.setenv("CLAUDECODE", "1")
    first = repo("decide", "e1", "park", "-m", "later")[1].data["object"]["id"]
    second = repo("decide", "q1", "investigate", "-m", "one more seed")[1].data["object"]["id"]
    monkeypatch.delenv("CLAUDECODE")

    async def drive():
        app = RBApp(Path.cwd())
        async with app.run_test(size=(120, 40)) as pilot:
            await until(pilot, lambda: bool(app.screen.query("#pending")) and app.screen.query_one("#pending").option_count == 2)
            panel = app.screen.query_one("#pending-panel")
            assert panel.display and panel.border_title == "TO CONFIRM (2)" and not app.screen.query_one("#calls-panel").display
            # c while the user browses another list confirms nothing: it takes them to the group, and c there confirms
            app.screen.query_one("#tree-list").focus()
            await pilot.press("c")
            await until(pilot, lambda: getattr(app.focused, "id", None) == "pending")
            await pilot.pause(0.5)
            assert app.screen.query_one("#pending").option_count == 2 and len(state().status()["pending"]) == 2
            app.screen.query_one("#pending").highlighted = 1
            await pilot.press("c")
            await until(pilot, lambda: app.screen.query_one("#pending").option_count == 1)
            assert app.screen.query_one("#pending").option_count == 1

    asyncio.run(drive())
    st = state().status()
    assert [p["id"] for p in st["pending"]] == [first]
    [made] = [d for d in st["decisions"] if d["authority"].get("confirms") == second]
    assert (made["by"], made["subject"], made["action"], made["authority"]["state"]) == ("human:t", "q1", "investigate", "confirmed")


TAGS = "".join(chr(0xE0000 + ord(c)) for c in " and ship today")
ESCAPED = "".join(f"\\udb40\\udc{ord(c):02x}" for c in " and ship today")


def test_rb_ui_draws_a_waiting_calls_hidden_characters_as_escapes_where_c_confirms_it(repo, monkeypatch):
    """Last recheck of the snapshot rule: rb ui's card beside what waits drew a session's reason and words raw, Unicode
    tag characters among them, and c made that call the user's; so did its list of what is open, for a hypothesis. The
    row, the card and the list draw each as its escape, as rb confirm lists it; the record keeps the text."""
    from rabbit_brain.tui import RBApp, OpenItemsScreen
    assert confirm_designs() == ["e1"]
    monkeypatch.setenv("CLAUDECODE", "1")
    made = repo("decide", "e1", "park", "-m", "later" + TAGS, "--words", "park it" + TAGS)[1].data["object"]["id"]
    assert repo("experiment", "add", "the next one", "--id", "e2", "--hypothesis", "it scales" + TAGS, "--question", "q2")[0] == 0
    monkeypatch.delenv("CLAUDECODE")
    [row] = [r for r in home().pending if r.kind == "proposed"]
    assert TAGS[0] not in row.text and f'note: later{ESCAPED} · words (as given): "park it{ESCAPED}"' in row.text, row.text

    async def drive():
        app = RBApp(Path.cwd())
        async with app.run_test(size=(120, 40)) as pilot:
            await until(pilot, lambda: bool(app.screen.query("#pending")) and app.screen.query_one("#pending").option_count == 2)
            screen = app.screen
            screen.query_one("#pending").focus()
            card = lambda: _shown(screen.query_one("#side-card"))
            await until(pilot, lambda: "later" in card())
            assert TAGS[0] not in card() and f"later{ESCAPED}" in card(), card()
            await pilot.press("c")
            await until(pilot, lambda: screen.query_one("#pending").option_count == 1)
            screen.query_one("#tree-list").focus()
            await pilot.press("a")
            await until(pilot, lambda: isinstance(app.screen, OpenItemsScreen))
            listed = _shown(app.screen)
            assert TAGS[0] not in listed and f"it scales{ESCAPED}" in listed, listed

    asyncio.run(drive())
    [d] = [d for d in state().status()["decisions"] if d["authority"].get("confirms") == made]
    assert (d["by"], d["why"], d["authority"]["state"]) == ("human:t", "later" + TAGS, "confirmed")


def test_c_on_rb_ui_confirms_a_gate_a_session_set(repo, monkeypatch):
    """Review of group 9: rb ui's card said `confirm: rb confirm e1` for a gate a session set, but the group c acts on
    held only calls, so the gate took the terminal. It waits there too, and c confirms it as it was listed. Review of
    group 10: its card was titled a past call, and said confirming it makes a call. Group E: a gate a session set is
    part of its experiment's design and waits with it, in one row titled a design, its card saying the design part by
    part, the gate among them; c confirms the design as listed, with its gate."""
    from rabbit_brain.tui import RBApp
    monkeypatch.setenv("CLAUDECODE", "1")
    assert repo("experiment", "gate", "e1", "--if-holds", "ship it next week")[0] == 0
    monkeypatch.delenv("CLAUDECODE")
    [row] = home().pending
    assert (row.kind, row.text) == ("design", "e1's design: the experiment") and re.fullmatch(r"e1@[0-9a-f]{64}", row.id)
    assert row.confirm == f"rb confirm {row.id}" and row.confirm == state().status()["gates_pending"][0]["confirm"]
    assert "gate: if it holds: ship it next week · not confirmed" in row.lines and "why: not recorded" in row.lines

    async def drive():
        app = RBApp(Path.cwd())
        async with app.run_test(size=(120, 40)) as pilot:
            await until(pilot, lambda: bool(app.screen.query("#pending")) and app.screen.query_one("#pending").option_count == 1)
            app.screen.query_one("#pending").focus()
            await until(pilot, lambda: getattr(app.focused, "id", None) == "pending")
            card = lambda: _shown(app.screen.query_one("#side-card"))
            # the card is replaced as focus moves: wait until the design's is the only one, not only until its words show
            titles = lambda: [str(w.render()) for w in app.screen.query_one("#side-card").query(".card-title")]
            await until(pilot, lambda: "A design, not confirmed" in card() and titles() == ["A design, not confirmed"])
            assert titles() == ["A design, not confirmed"]
            assert f"the design is yours, as listed here, with its gate, through the same rb confirm anyone would type: {row.confirm}" in card()
            assert "gate: if it holds: ship it next week · not confirmed" in card()
            assert "past call" not in card() and "the call is made" not in card()
            await pilot.press("c")
            await until(pilot, lambda: not app.screen.query_one("#pending-panel").display)

    asyncio.run(drive())
    st = state().status()
    assert st["gates_pending"] == [] and st["designs_pending"] == [] and home().pending == []
    assert [e["design"]["standing"] for e in st["experiments"]] == ["confirmed"]


def test_c_shows_the_design_it_would_confirm_before_it_confirms_it(repo, monkeypatch):
    """Review of group E: c pressed while the user browsed the catalogue took them to the group of what waits, but the
    card beside it still showed a question, and the next c confirmed a design they never saw. The first c now shows the
    highlighted design part by part (beside the list, or full screen on a narrow terminal) and says the next c confirms
    that design; only then does c confirm it."""
    from rabbit_brain.tui import RBApp
    monkeypatch.setenv("CLAUDECODE", "1")
    assert repo("experiment", "gate", "e1", "--if-holds", "ship it next week")[0] == 0
    monkeypatch.delenv("CLAUDECODE")
    [row] = home().pending
    said = []

    async def drive(size):
        app = RBApp(Path.cwd())
        app.notify = lambda message, **kw: said.append(message)
        async with app.run_test(size=size) as pilot:
            await until(pilot, lambda: bool(app.screen.query("#pending")) and app.screen.query_one("#pending").option_count == 1)
            app.screen.query_one("#tree-list").focus()
            await pilot.pause(0.3)
            await pilot.press("c")
            await until(pilot, lambda: said == ["c again confirms the highlighted design."])
            assert said == ["c again confirms the highlighted design."], said
            card = lambda: _shown(app.screen.query_one("#side-card"))
            await until(pilot, lambda: "A design, not confirmed" in card())
            assert "A design, not confirmed" in card() and "gate: if it holds: ship it next week · not confirmed" in card() and row.confirm in card(), card()
            assert state().status()["designs_pending"], "the first c confirms nothing"
            assert getattr(app.focused, "id", None) == "pending"
            await pilot.press("c")
            await until(pilot, lambda: not state().status()["designs_pending"])

    asyncio.run(drive((120, 40)))
    assert [e["design"]["standing"] for e in state().status()["experiments"]] == ["confirmed"]


def test_c_on_a_narrow_rb_ui_shows_the_design_full_screen_first(repo, monkeypatch):
    """The same on a terminal too narrow for a card beside the list: the first c opens the design full screen."""
    from rabbit_brain.tui import PendingScreen, RBApp
    monkeypatch.setenv("CLAUDECODE", "1")
    assert repo("experiment", "gate", "e1", "--if-holds", "ship it next week")[0] == 0
    monkeypatch.delenv("CLAUDECODE")
    said = []

    async def drive():
        app = RBApp(Path.cwd())
        app.notify = lambda message, **kw: said.append(message)
        async with app.run_test(size=(90, 40)) as pilot:
            await until(pilot, lambda: bool(app.screen.query("#pending")) and app.screen.query_one("#pending").option_count == 1)
            app.screen.query_one("#tree-list").focus()
            await pilot.pause(0.3)
            await pilot.press("c")
            await until(pilot, lambda: isinstance(app.screen, PendingScreen))
            assert isinstance(app.screen, PendingScreen) and "gate: if it holds: ship it next week · not confirmed" in _shown(app.screen)
            # recheck of group E: a full screen has no list, so nothing on it is highlighted
            assert "c confirms this one: the design is yours" in _shown(app.screen) and "highlighted" not in _shown(app.screen)
            assert state().status()["designs_pending"], "the first c confirms nothing"
            await pilot.press("escape")
            await until(pilot, lambda: not isinstance(app.screen, PendingScreen) and getattr(app.focused, "id", None) == "pending")
            assert getattr(app.focused, "id", None) == "pending"
            await pilot.press("c")
            await until(pilot, lambda: not state().status()["designs_pending"])

    asyncio.run(drive())
    assert said[:1] == ["c again confirms the highlighted design."]


def test_c_twice_on_a_narrow_rb_ui_confirms_what_its_full_screen_card_shows(repo, monkeypatch):
    """Recheck of group E: at 90x30 the first c opened the full-screen card of what waits, and the second c did
    nothing there: only escape and then c confirmed it, a call too (group D's c, c confirmed a call). c on the card
    confirms the one it shows; one a session changed while it was open is shown as it reads now, and the next c says
    so before the c after it confirms it."""
    from rabbit_brain.tui import HomeScreen, PendingScreen, RBApp
    monkeypatch.setenv("CLAUDECODE", "1")
    assert repo("decide", "e1", "park", "--why", "later")[0] == 0
    monkeypatch.delenv("CLAUDECODE")
    said = []
    got = {}

    def session_changes_it(text):
        r = subprocess.run([*command(), "experiment", "gate", "e1", "--if-holds", text], env={**environment(), "CLAUDECODE": "1"},
                           capture_output=True, stdin=subprocess.DEVNULL, encoding="utf-8")
        assert r.returncode == 0, (r.stdout, r.stderr)

    async def drive():
        app = RBApp(Path.cwd())
        app.notify = lambda message, **kw: said.append(message)
        async with app.run_test(size=(90, 30)) as pilot:
            await until(pilot, lambda: bool(app.screen.query("#pending")) and app.screen.query_one("#pending").option_count == 2)
            home_screen = app.screen
            assert isinstance(home_screen, HomeScreen) and not home_screen._wide
            home_screen.query_one("#tree-list").focus()
            await pilot.pause(0.3)
            # the call first: c shows it full screen, c again confirms it
            await pilot.press("c")
            await until(pilot, lambda: isinstance(app.screen, PendingScreen))
            await pilot.press("c")
            await until(pilot, lambda: not state().status().get("pending") and not isinstance(app.screen, PendingScreen), timeout=30)
            got["call"] = state().status().get("pending")
            # then the design: a session changes it while its card is open
            await until(pilot, lambda: home_screen.query_one("#pending").option_count == 1)
            home_screen.query_one("#pending").focus()
            await pilot.pause(0.3)
            await pilot.press("c")
            await until(pilot, lambda: isinstance(app.screen, PendingScreen) and app.screen.row.kind == "design")
            first = app.screen.row.id
            session_changes_it("ship it next week")
            await until(pilot, lambda: isinstance(app.screen, PendingScreen) and app.screen.row.id != first and "ship it next week" in _shown(app.screen), timeout=30)
            await pilot.press("c")
            await until(pilot, lambda: bool(said) and said[-1].endswith("changed since it was shown: c again confirms it as it reads now."))
            got["shown again"] = (said[-1], _confirmed_keys())
            now = app.screen.row.id
            await pilot.press("c")
            await until(pilot, lambda: not state().status()["designs_pending"], timeout=30)
            got["keys"] = (now, _confirmed_keys())

    asyncio.run(drive())
    assert got["call"] == [], got
    assert got["shown again"] == ("e1's design changed since it was shown: c again confirms it as it reads now.", []), got["shown again"]
    now, keys = got["keys"]
    assert keys == [now.split("@")[1]], got["keys"]


def test_a_designs_card_says_its_gate_only_where_it_has_one(repo, monkeypatch):
    """Recheck of group E: the card of e1's design said c confirms it `with its gate`, and e1 has no gate."""
    from rabbit_brain.tui import PendingScreen, RBApp
    got = {}

    async def drive():
        app = RBApp(Path.cwd())
        app.notify = lambda message, **kw: None
        async with app.run_test(size=(90, 30)) as pilot:
            await until(pilot, lambda: bool(app.screen.query("#pending")) and app.screen.query_one("#pending").option_count == 1)
            app.screen.query_one("#tree-list").focus()
            await pilot.pause(0.3)
            await pilot.press("c")
            await until(pilot, lambda: isinstance(app.screen, PendingScreen))
            got["card"] = _shown(app.screen)

    asyncio.run(drive())
    assert "the design is yours, as listed here, through the same rb confirm" in got["card"] and "with its gate" not in got["card"], got["card"]


def _confirmed_keys() -> list:
    """The full design identities the user's confirmations keep, in order. The short `key` remains a summary;
    `detail.design` is the hash an earlier rb names the design by."""
    lines = [json.loads(l) for l in (Path.cwd() / ".rb" / "log.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    return [k["full"] for l in lines if l.get("op") == "gate" and (l.get("detail") or {}).get("design") for k in l["detail"]["owned"].values()]


@pytest.mark.parametrize("size", [(120, 40), (90, 40)], ids=["wide", "narrow"])
def test_c_never_confirms_a_design_changed_after_it_was_shown(repo, monkeypatch, size):
    """Trial of group E: the first c showed e1's design; a session changed it before the second c, rb ui refreshed the
    row under the user (and, wide, put a question's card beside the list), and the second c confirmed the design as it
    read after the change, which the user never saw. c confirms only the design as shown: a second c before the screen
    has read the change runs rb confirm with the key shown, which rb refuses; once the screen has read it, what is in
    view is the design as it reads now (the card beside the list, or the full-screen card, redrawn), and the next c
    shows it again and says it changed; only the c after that confirms it."""
    from rabbit_brain.tui import HomeScreen, PendingScreen, RBApp
    monkeypatch.setenv("CLAUDECODE", "1")
    assert repo("experiment", "gate", "e1", "--if-holds", "ship it next week")[0] == 0
    monkeypatch.delenv("CLAUDECODE")
    said = []
    got = {}

    def session_changes_it(text):
        # its own process, as a session's write is: the screen holds this one's output
        r = subprocess.run([*command(), "experiment", "gate", "e1", "--if-holds", text], env={**environment(), "CLAUDECODE": "1"},
                           capture_output=True, stdin=subprocess.DEVNULL, encoding="utf-8")
        assert r.returncode == 0, (r.stdout, r.stderr)
        return home().pending[0].id

    def waiting():
        return [d["confirm"] for d in state().status()["designs_pending"]]

    async def drive():
        app = RBApp(Path.cwd())
        app.notify = lambda message, **kw: said.append(message)
        async with app.run_test(size=size) as pilot:
            await until(pilot, lambda: bool(app.screen.query("#pending")) and app.screen.query_one("#pending").option_count == 1)
            screen = app.screen
            assert isinstance(screen, HomeScreen)
            narrow = not screen._wide
            card = (lambda: _shown(app.screen)) if narrow else (lambda: _shown(screen.query_one("#side-card")))

            async def back():
                if narrow:
                    await pilot.press("escape")
                    await until(pilot, lambda: not isinstance(app.screen, PendingScreen))

            screen.query_one("#tree-list").focus()
            await pilot.pause(0.3)
            await pilot.press("c")
            await until(pilot, lambda: "ship it next week" in card() and said)
            got["first"] = (list(said), card())
            # a session changes it before the screen reads the change: c confirms the key shown, and rb refuses it
            stamp = screen._mtimes()
            screen._mtimes = lambda: stamp
            second = session_changes_it("ship it in two weeks")
            await back()
            await pilot.press("c")
            await until(pilot, lambda: len(said) == 2, timeout=30)
            got["refused"] = (said[-1], waiting(), _confirmed_keys())
            # the screen reads it: c shows it as it reads now, and says it changed
            del screen._mtimes
            await until(pilot, lambda: bool(screen._pending) and screen._pending[0].id == second)
            await pilot.pause(0.5)
            got["beside after the write"] = card()
            await pilot.press("c")
            await until(pilot, lambda: len(said) == 3 and "ship it in two weeks" in card())
            got["shown again"] = (said[-1], card(), waiting())
            # a session changes it while it is in view: the card is redrawn as it reads now, and c shows it again; the
            # redraw takes the earlier card away only after it mounts the new one, so it is read once the earlier one is gone
            third = session_changes_it("ship it in three weeks")
            await until(pilot, lambda: bool(screen._pending) and screen._pending[0].id == third and "ship it in three weeks" in card()
                        and "ship it in two weeks" not in card())
            got["redrawn"] = card()
            await back()
            await pilot.press("c")
            await until(pilot, lambda: len(said) == 4 and "ship it in three weeks" in card())
            got["shown a third time"] = (said[-1], waiting())
            await back()
            await pilot.press("c")
            await until(pilot, lambda: not waiting(), timeout=30)
            got["keys"] = (second, third, _confirmed_keys())

    asyncio.run(drive())
    said_first, shown = got["first"]
    assert said_first == ["c again confirms the highlighted design."] and "ship it next week" in shown, got["first"]
    said_then, still, keys = got["refused"]
    assert said_then.strip().endswith(f"To confirm it as it reads now: {still[0]}") and keys == [], got["refused"]
    if size[0] >= 100:       # beside the list stays the design the user had in view, never a question's card
        assert "A design, not confirmed" in got["beside after the write"] and "ship it in two weeks" in got["beside after the write"], got
    changed = "e1's design changed since it was shown: c again confirms it as it reads now."
    said_again, shown, still = got["shown again"]
    assert said_again == changed and "ship it in two weeks" in shown and "ship it next week" not in shown and still, got["shown again"]
    assert "ship it in three weeks" in got["redrawn"] and "ship it in two weeks" not in got["redrawn"], got["redrawn"]
    said_again, still = got["shown a third time"]
    assert said_again == changed and still, got["shown a third time"]
    second, third, keys = got["keys"]
    assert keys == [third.split("@")[1]], (keys, second, third)
    st = state().status()
    assert [e["design"]["standing"] for e in st["experiments"]] == ["confirmed"] and st["experiments"][0]["if_holds"] == "ship it in three weeks"


def test_an_investigation_reads_as_still_going_on_the_card(repo):
    """An investigation is still going: the experiment's card says since when and why; any other call says what was
    made and on which day, by no writer."""
    code, env = repo("decide", "e1", "investigate", "-m", "one more seed")
    assert code == 0
    day = env.data["object"]["at"][:10]
    assert f"decision: investigating since {day}: one more seed" in object_card("e1").facts
    assert repo("decide", "e1", "park", "-m", "later")[0] == 0
    assert f"decision: parked {day}: later" in object_card("e1").facts


def test_a_shared_experiment_opens_from_both_question_rows_and_keeps_its_selection_on_refresh(repo):
    """The same experiment remains visible under both questions; row identity never becomes record identity."""
    from rabbit_brain.tui import HomeScreen, ObjectScreen, RBApp

    assert repo("experiment", "file", "e1", "q2")[0] == 0

    async def drive():
        app = RBApp(Path.cwd())
        async with app.run_test(size=(160, 40)) as pilot:
            await until(pilot, lambda: isinstance(app.screen, HomeScreen) and app.screen.query_one("#tree-list").option_count == 4)
            tree = app.screen.query_one("#tree-list")

            def ids():
                return [tree.get_option_at_index(i).id for i in range(tree.option_count)]

            original = ids()
            assert len(set(original)) == len(original)
            occurrences = [original[original.index(f"o:{question}") + 1] for question in ("q1", "q2")]
            assert occurrences[0] == "o:e1" and occurrences[1] != occurrences[0]

            def preview_is_experiment():
                return [str(w.render()) for w in app.screen.query_one("#side-card").query(".card-title")] == ["the experiment"]

            for row_id in occurrences:
                tree.focus()
                tree.highlighted = ids().index(row_id)
                await until(pilot, preview_is_experiment)
                assert preview_is_experiment()
                await pilot.press("enter")
                await until(pilot, lambda: isinstance(app.screen, ObjectScreen))
                assert app.screen.subject == "e1" and app.screen.card.title == "the experiment"
                await pilot.press("escape")
                await until(pilot, lambda: isinstance(app.screen, HomeScreen))
                assert tree.get_option_at_index(tree.highlighted).id == row_id

            # A new sibling inserts a row before the selected occurrence. Refresh preserves that row, not its old index.
            state().add_experiment("another experiment", id="e2", question="q1", purpose="another measurement")
            await pilot.press("r")
            await until(pilot, lambda: "o:e2" in ids())
            assert len(set(ids())) == len(ids()) and all(row_id in ids() for row_id in occurrences)
            assert tree.get_option_at_index(tree.highlighted).id == occurrences[1]
            await until(pilot, preview_is_experiment)
            assert preview_is_experiment()
            # Mouse selection uses the same mapping as the keyboard's priority binding and the side preview.
            assert await pilot.click("#tree-list", offset=(20, ids().index(occurrences[1])))
            await until(pilot, lambda: isinstance(app.screen, ObjectScreen))
            assert app.screen.subject == "e1" and app.screen.card.title == "the experiment"

    asyncio.run(drive())


def test_a_rule_met_only_on_its_estimate_is_never_drawn_green(repo):
    """top1 of 0.76 alone meets at least 0.7 on its estimate, with no interval to read; with 0.62 and 0.95 beside 0.78
    and 0.8 it meets on its estimate, its interval across 0.7: the screen draws either without green wherever it is
    counted, in the tree, the tally, the line that counts met rules when nothing waits on the user, and the experiment's
    criteria, as it draws a rule with a problem under it. Met across its whole interval (0.76, 0.78 and 0.8), with
    nothing under it, it is green."""
    from rabbit_brain.tui import DIM, GOOD, ObjectScreen, RBApp
    assert confirm_designs() == ["e1"]                       # the agent set it up: the user confirms its design

    def drawn(app) -> dict:
        """Each tree row's count of met rules, whole, and the colour it is drawn in, the tally line and the quiet line's
        markup. At 120 columns e1's row has no room for rb's whole words, so every row counts in short ones."""
        tree = app.screen.query_one("#tree-list")
        rows = {}
        for i in range(tree.option_count):
            opt = tree.get_option_at_index(i)
            t = opt.prompt
            rows.update({opt.id: str(sp.style) for sp in t.spans if str(t)[sp.start:sp.end] == "1 met"})
        return {"tree": rows, "tally": app.screen.query_one("#tally").content, "quiet": app.screen.query_one("#quiet").content}

    async def drive(colour: str) -> None:
        app = RBApp(Path.cwd())
        async with app.run_test(size=(120, 36)) as pilot:
            await pilot.pause(0.3)
            d = drawn(app)
            assert d["tree"] == {"o:q1": colour, "o:e1": colour} and _colour_of(d["tally"], "1 met") == colour
            assert d["quiet"].startswith(f"[{colour}]Nothing waits on you. 1 rule is met;")
            tree = app.screen.query_one("#tree-list")
            tree.focus()
            tree.highlighted = [tree.get_option_at_index(i).id for i in range(tree.option_count)].index("o:e1")
            await pilot.pause(0.2)
            await pilot.press("enter")
            await until(pilot, lambda: isinstance(app.screen, ObjectScreen))
            label = "rule met" if colour == GOOD else "estimate meets the rule"
            assert f"[{colour}]{label} · " in "\n".join(str(w.content) for w in app.screen.query(".bar-body"))
            await pilot.press("escape")

    h = home()
    [f] = h.briefs[0].experiments[0].findings
    assert f.standing == "estimate meets criterion · point only" and not f.good and not f.row.good
    assert {r.id: r.muted for r in h.tree} == {"q1": ["meets"], "e1": ["meets"], "q2": []} and h.muted == ["meets"]
    asyncio.run(drive(DIM))
    for top1 in ("0.78", "0.8"):
        assert repo("run", "add", "e1", f"top1={top1}")[0] == 0
    h = home()
    assert h.briefs[0].experiments[0].findings[0].good and h.muted == [] and all(r.muted == [] for r in h.tree)
    asyncio.run(drive(GOOD))
    for top1 in ("0.62", "0.95"):
        assert repo("run", "add", "e1", f"top1={top1}")[0] == 0
    h = home()
    [f] = h.briefs[0].experiments[0].findings
    assert f.standing == "estimate meets criterion · measured" and not f.good and not f.row.good
    assert {r.id: r.muted for r in h.tree} == {"q1": ["meets"], "e1": ["meets"], "q2": []} and h.muted == ["meets"]
    assert object_card("e1").pass_bars[0].muted == ["meets"]
    asyncio.run(drive(DIM))


def _laid(app) -> bool:
    """Whether the home screen's catalogue rows are laid for the panel they are drawn in: each fills it, but for the
    few columns kept for the scrollbar."""
    from rabbit_brain.tui import HomeScreen
    if not isinstance(app.screen, HomeScreen):
        return False
    tree = app.screen.query_one("#tree-list")
    rows = [tree.get_option_at_index(i).prompt for i in range(tree.option_count)]
    return bool(rows) and all(tree.size.width - 4 <= len(t.plain) < tree.size.width for t in rows)


async def catalogue(width: int) -> dict:
    """Each row of the catalogue as rb ui draws it in a terminal `width` columns wide, by its option id, once the rows
    are laid for the panel they are drawn in."""
    from rabbit_brain.tui import RBApp
    app = RBApp(Path.cwd())
    async with app.run_test(size=(width, 36)) as pilot:
        await until(pilot, lambda: _laid(app))
        tree = app.screen.query_one("#tree-list")
        return {tree.get_option_at_index(i).id: tree.get_option_at_index(i).prompt for i in range(tree.option_count)}


def short_words(part: str) -> str:
    """A count as the catalogue says it in short words: '2 criteria not met' is '2 not met'."""
    from rabbit_brain.tui import _short_count
    return _short_count(part)


def _standing_at(line, part: str):
    """Where `part` stands whole on a catalogue row: after a space, and before a separator, the '…' that ends a row
    leaving something out, or the row's end."""
    from rabbit_brain.tui import plain_word
    found = re.search(rf"(?:^| )({re.escape(plain_word(part))})(?: ·| …|$)", line.plain)
    return found.start(1) if found else None


def whole(line, part: str) -> str:
    """The colour `part` is drawn in, once it stands whole on a catalogue row."""
    at = _standing_at(line, part)
    assert at is not None, (part, line.plain)
    return next(str(s.style) for s in line.spans if s.start <= at < s.end)


def test_each_count_on_a_row_is_whole_and_in_its_colour_at_120_columns(repo):
    """At 120 columns every count on q1's and e1's rows stands whole in its colour: c1 met across its interval, then
    c1 met beside c2 not met. With no room for rb's whole words on every row, every row counts in short ones; at 240
    columns they are rb's own. What is not a count gives way first, the lock word and then the state word, with a '…'
    where they were. Cut where there is no room at all, each count keeps its colour."""
    from rabbit_brain.tui import BAD, GOOD, standing_text

    for top1 in ("0.78", "0.8"):
        assert repo("run", "add", "e1", f"top1={top1}")[0] == 0
    drawn = asyncio.run(catalogue(120))
    assert [whole(drawn[oid], "1 met") for oid in ("o:q1", "o:e1")] == [GOOD, GOOD]
    assert repo("criterion", "add", "top5 holds", "-e", "e1", "--metric", "top5", "--at-least", "0.99", "--noise", "0.001",
                "--id", "c2", "-q", "q1")[0] == 0
    for top5 in ("0.9", "0.91", "0.92"):
        assert repo("run", "add", "e1", "top1=0.79", f"top5={top5}")[0] == 0
    assert [r.standing for r in home().tree if r.id in ("q1", "e1")] == [
        "open · 1 criterion met · 1 criterion not met", "open · 1 criterion met · 1 criterion not met · not locked"]
    drawn = asyncio.run(catalogue(120))
    assert [(whole(drawn[oid], "1 met"), whole(drawn[oid], "1 not met")) for oid in ("o:q1", "o:e1")] == [(GOOD, BAD)] * 2
    assert drawn["o:q1"].plain.endswith(" open · 1 met · 1 not met")
    assert drawn["o:e1"].plain.endswith(" open · 1 met · 1 not met …")       # never as if it said all there is
    drawn = asyncio.run(catalogue(240))
    assert ([(whole(drawn[oid], "1 criterion met"), whole(drawn[oid], "1 criterion not met")) for oid in ("o:q1", "o:e1")]
            == [(GOOD, BAD)] * 2)
    standing = "open · 1 criterion met · 1 criterion not met · not locked"
    assert [standing_text(standing, short=True, room=room).plain for room in (37, 26, 19)] == [
        "open · 1 met · 1 not met · not locked", "open · 1 met · 1 not met …", "1 met · 1 not met …"]
    assert standing_text(standing, short=True, room=12, spare=7).plain == "1 met · 1 not met …"   # the title gives way
    assert standing_text("planned · not locked", room=8, spare=12).plain == "planned…"    # never for what is not a count
    cut = standing_text(standing, short=True, room=12)
    assert cut.plain == "1 met · 1 n…" and (whole(cut, "1 met"), _colour_of(cut, "1 n…")) == (GOOD, BAD)


def test_a_long_title_or_id_never_cuts_a_count_at_120_columns(repo):
    """At 120 columns a long title or a long id never cuts a count: beside a 38-character question, an experiment with an
    id rb made up and one with a 21-character id keep every count whole and in its colour. What is not a count gives way
    first, the lock word and then the state word, and then the title. A row that leaves out any part of its standing
    ends in '…', so no row reads as whole when it is not; every id stands whole."""
    from rabbit_brain.tui import BAD, GOOD

    assert repo("question", "add", "does the wider model keep its accuracy", "--id", "q3")[0] == 0
    code, env = repo("experiment", "add", "wider model against the baseline", "--hypothesis", "it keeps its accuracy", "-q", "q3")
    made_up, long_id = env.data["object"]["id"], "wider-cosine-ablation"
    assert code == 0 and len(made_up) == 7
    assert repo("experiment", "add", "wider model on a cosine schedule", "--id", long_id, "--hypothesis", "it keeps its accuracy",
                "-q", "q3")[0] == 0
    for e in (made_up, long_id):
        assert repo("criterion", "add", "top1 holds", "-e", e, "--metric", "top1", "--at-least", "0.7", "--noise", "0.001", "-q", "q3")[0] == 0
    assert repo("criterion", "add", "top5 holds", "-e", made_up, "--metric", "top5", "--at-least", "0.99", "--noise", "0.001", "-q", "q3")[0] == 0
    for top1, top5 in (("0.76", "0.9"), ("0.78", "0.91"), ("0.8", "0.92")):
        assert repo("run", "add", made_up, f"top1={top1}", f"top5={top5}")[0] == 0
        assert repo("run", "add", long_id, f"top1={top1}")[0] == 0
    rows = {r.id: r for r in home().tree}
    assert [rows[i].standing for i in ("q3", made_up, long_id)] == [
        "open · 2 criteria met · 1 criterion not met", "open · 1 criterion met · 1 criterion not met · not locked",
        "open · 1 criterion met · not locked"]
    drawn = asyncio.run(catalogue(120))
    counts = {i: [whole(drawn[f"o:{i}"], short_words(p)) for p in rows[i].standing.split(" · ") if p[:1].isdigit()]
              for i in ("q3", made_up, long_id)}
    assert counts == {"q3": [GOOD, BAD], made_up: [GOOD, BAD], long_id: [GOOD]}
    for i, r in rows.items():
        line = drawn[f"o:{i}"]
        assert f" {i}  " in line.plain, line.plain
        said = all(_standing_at(line, short_words(part)) is not None for part in r.standing.split(" · "))
        assert said or line.plain.endswith("…"), line.plain
    assert all(drawn[f"o:{i}"].plain.endswith(" …") for i in (made_up, long_id))


def test_on_a_narrow_terminal_the_catalogue_takes_the_room_of_the_card_beside_it(repo):
    """Below 96 columns no card is drawn beside the catalogue, so the catalogue takes its room: in an 80 by 24 terminal
    it spans the screen, and so it stays across a resize. Each count on q1's and e1's rows stands whole in its colour."""
    from rabbit_brain.tui import BAD, GOOD, RBApp

    assert repo("criterion", "add", "top5 holds", "-e", "e1", "--metric", "top5", "--at-least", "0.99", "--noise", "0.001",
                "--id", "c2", "-q", "q1")[0] == 0
    for top1, top5 in (("0.78", "0.9"), ("0.8", "0.91"), ("0.79", "0.92")):
        assert repo("run", "add", "e1", f"top1={top1}", f"top5={top5}")[0] == 0

    async def drive() -> list:
        app = RBApp(Path.cwd())
        seen = []
        async with app.run_test(size=(80, 24)) as pilot:
            for size in ((80, 24), (120, 36), (80, 24)):
                await pilot.resize_terminal(*size)
                screen = app.screen
                await until(pilot, lambda: _laid(app) and screen.query_one("#side-card").display == (size[0] >= 96))
                tree, panel, top = (screen.query_one(s) for s in ("#tree-list", "#tree-panel", "#top"))
                rows = {tree.get_option_at_index(i).id: tree.get_option_at_index(i).prompt for i in range(tree.option_count)}
                seen.append((size[0], panel.region.width == top.region.width,
                             [(whole(rows[oid], "1 met"), whole(rows[oid], "1 not met")) for oid in ("o:q1", "o:e1")]))
        return seen

    assert asyncio.run(drive()) == [(80, True, [(GOOD, BAD)] * 2), (120, False, [(GOOD, BAD)] * 2), (80, True, [(GOOD, BAD)] * 2)]


def test_the_lines_over_the_catalogue_never_hide_what_they_leave_out(repo):
    """The project's title and the count of its rules by their reading are one line each. The count says the page's
    and rb status's words at 100 and at 160 columns, each whole, shown and in its colour. Where they do not fit, as at
    60 columns, the line ends in '…', as a title too long for its line does."""
    from textual.geometry import Region
    from rabbit_brain.tui import BAD, DIM, GOOD, WARN, HomeScreen, RBApp

    title = "Does the wider model keep its accuracy on the held-out split under the longer schedule"
    assert repo("project", "title", title, "--why", "the work moved")[0] == 0
    for top1 in ("0.78", "0.8"):
        assert repo("run", "add", "e1", f"top1={top1}")[0] == 0
    for c in (["top5 holds", "--metric", "top5", "--at-least", "0.99", "--noise", "0.001"],
              ["loss holds", "--metric", "loss", "--at-most", "1.0", "--min-n", "5"],
              ["rate holds", "--metric", "rate", "--at-least", "100"],
              ["top1 clears the bar", "--metric", "top1", "--at-least", "0.9", "--role", "gate"]):
        assert repo("criterion", "add", c[0], "-e", "e1", *c[1:], "-q", "q1")[0] == 0
    for top5, loss in (("0.9", "0.5"), ("0.91", "0.6"), ("0.92", "0.7")):
        assert repo("run", "add", "e1", f"top5={top5}", f"loss={loss}")[0] == 0
    assert dict(home().tally) == {"meets": 1, "misses": 1, "undetermined": 1, "untested": 1, "gate missed": 1}

    async def lines(width: int) -> tuple:
        """The title's line and the criteria's line as the screen shows them, and what the criteria's line draws."""
        app = RBApp(Path.cwd())
        async with app.run_test(size=(width, 36)) as pilot:
            await until(pilot, lambda: isinstance(app.screen, HomeScreen) and _laid(app))
            head, tally = (app.screen.query_one(s) for s in ("#head", "#tally"))
            shown = [w.render_lines(Region(0, 0, w.size.width, 1))[0].text.rstrip() for w in (head, tally)]
            return *shown, tally.content

    for width, said in ((100, ("1 met", "1 not met", "1 can't tell yet", "1 not tested", "1 gate missed")),
                        (160, ("1 met", "1 not met", "1 can't tell yet", "1 not tested", "1 gate missed"))):
        head, shown, drawn = asyncio.run(lines(width))
        assert all(re.search(rf"(?:^| ){re.escape(part)}(?: ·|$)", shown) for part in said), (width, shown)
        assert [whole(drawn, part) for part in said] == [GOOD, BAD, WARN, DIM, WARN]
        assert head == title or (head.endswith("…") and title.startswith(head[:-1])), head
    head, shown, _ = asyncio.run(lines(60))
    assert shown.startswith("Rules: 1 met · 1 not met · ") and shown.endswith("…"), shown
    assert title.startswith(head[:-1]) and head.endswith("…"), head


def test_the_criterion_card_says_the_words_it_was_declared_on_and_no_writer(repo, monkeypatch):
    """A criterion's card says the words it was declared on, never a grant and never who declared it: words an agent
    session gave as given, never as the user's; a criterion declared without them says neither."""
    monkeypatch.setenv("CLAUDECODE", "1")
    assert repo("criterion", "add", "score holds", "-e", "e1", "--metric", "score", "--at-least", "0.5", "--id", "c2",
                "--words", "track score for wide too")[0] == 0
    monkeypatch.delenv("CLAUDECODE")
    c2 = object_card("c2")
    assert c2.who_line.endswith(' · words (as given): "track score for wide too"') and "declared by" not in c2.who_line
    assert any(f.startswith("timing: declared") and f.endswith('· words (as given): "track score for wide too"') for f in c2.facts)
    assert "your words" not in " ".join([c2.who_line, *c2.facts, *c2.sentences])
    c1 = object_card("c1")
    assert c1.who_line.startswith("rule · ") and "track score" not in " ".join([c1.who_line, *c1.facts])
    assert not [f for f in [c1.who_line, c2.who_line, *c1.facts, *c2.facts] if "claude-code" in f]
    assert "grant" not in " ".join([c2.who_line, *c2.facts, *c2.sentences])


def test_the_experiment_card_counts_a_setting_as_this_read_finds_its_source(repo):
    """The card rb ui, rb open and rb page draw re-reads every verified source, as rb status does: a file that now
    states another value, or no longer states it, is counted under that word, never as verified, and said beneath; a
    conflict re-verifying puts on record is counted as one still."""
    Path("configs").mkdir()
    Path("configs/train.json").write_text('{"optim": {"lr": 0.0003, "wd": 0.01}}', encoding="utf-8")
    for name in ("optim.lr", "optim.wd"):
        assert repo("spec", "set", "e1", name, "--source", f"configs/train.json#{name}")[0] == 0
    card = object_card("e1")
    assert "settings: 2 verified" in card.facts and card.blockers == []
    Path("configs/train.json").write_text('{"optim": {"lr": 0.0005}}', encoding="utf-8")
    card = object_card("e1")
    assert "settings: 1 conflict · 1 stale" in card.facts
    assert card.blockers == ["optim.lr    its source now states another value", "optim.wd    its source no longer states it"]
    _, env = repo("status")
    assert {("conflict", "e1/optim.lr"), ("stale", "e1/optim.wd")} <= {(o["code"], o["subject"]) for o in env.data["open"]}
    assert repo("spec", "verify", "e1")[0] == 1             # the conflict goes on record, and is counted as one still
    card = object_card("e1")
    assert "settings: 1 conflict · 1 provisional" in card.facts
    assert card.blockers == ["optim.lr    its source states a different value"]


def test_the_experiment_card_crowds_out_no_reason_a_setting_is_in_trouble(repo):
    """Up to six lines say a setting each; past six, a line says each reason and the settings it holds for, so a
    required setting nobody knows is not pushed out by seven sources that changed."""
    Path("configs").mkdir()
    Path("configs/train.json").write_text('{"a1": 1, "a2": 2, "a3": 3, "a4": 4, "a5": 5, "a6": 6, "a7": 7, "a8": 8}', encoding="utf-8")
    for i in range(1, 9):
        assert repo("spec", "set", "e1", f"a{i}", "--source", f"configs/train.json#a{i}")[0] == 0
    assert repo("spec", "set", "e1", "zz", "--unknown")[0] == 0
    assert object_card("e1").blockers == ["zz    unknown"]
    Path("configs/train.json").write_text("{}", encoding="utf-8")
    card = object_card("e1")
    assert "settings: 8 stale · 1 unknown" in card.facts
    assert card.blockers == ["a1, a2, a3, a4, a5, a6 and 2 more    its source no longer states it", "zz    unknown"]


def test_a_long_title_of_what_rb_found_keeps_a_gap_before_its_hint(repo, capsys):
    """What a watched command printed, named by a command too long for the column, still has a gap before the words
    that say where it is, never running into them."""
    show = "import json; print(json.dumps({'metrics': {'top1': 0.81}}))  # the same evaluation, again"
    assert main(["journal", "run", "--json", "--", sys.executable, "-c", show]) == 0
    capsys.readouterr()
    from textual.widgets import OptionList
    from rabbit_brain.tui import HomeScreen, RBApp

    async def drawn():
        app = RBApp(Path.cwd())
        async with app.run_test(size=(160, 44)) as pilot:
            await until(pilot, lambda: isinstance(app.screen, HomeScreen) and app.screen.query_one("#calls", OptionList).option_count)
            calls = app.screen.query_one("#calls", OptionList)
            return [str(calls.get_option_at_index(i).prompt) for i in range(calls.option_count)]

    [row] = asyncio.run(drawn())
    assert len(row.split("` printed on e1")[0]) > 44 and "` printed on e1  in the repository, not in the record" in row, row


def test_bare_rb_without_a_terminal_prints_help_as_before(repo, capsys):
    assert main([]) == 0
    out = capsys.readouterr().out
    assert "usage: rb" in out or "The record" in out


def test_rb_ui_refuses_agent_sessions_and_pipes(repo, capsys, monkeypatch):
    monkeypatch.setenv("CLAUDECODE", "1")
    code = main(["ui", "--json"])
    env = Envelope.model_validate_json(capsys.readouterr().out)
    assert code != 0 and "agent session" in env.errors[0].message
    monkeypatch.delenv("CLAUDECODE")
    code = main(["ui", "--json"])       # pytest has no tty, which is the pipe case
    env = Envelope.model_validate_json(capsys.readouterr().out)
    assert code != 0 and "interactive terminal" in env.errors[0].message


@pytest.mark.parametrize("left", [{"CLAUDECODE": "1"}, {"CLAUDE_CODE_SESSION_ID": "sess-ui-3f1c", "CLAUDE_CODE_ENTRYPOINT": "cli"}])
def test_the_screen_refuses_an_agent_session_however_it_is_started(repo, monkeypatch, left):
    """python -m rabbit_brain.tui, which rb ui runs, refuses an agent session as rb ui does, in the same words and with
    the same exit code, before the screen takes the terminal: also where only the session's id and entry point are
    left."""
    for k, v in left.items():
        monkeypatch.setenv(k, v)

    def run(argv):
        return subprocess.run(argv, env=environment(), capture_output=True, stdin=subprocess.DEVNULL, encoding="utf-8",
                              errors="replace", timeout=60)

    ui = run([*command(), "ui"])
    screen = run([sys.executable, "-m", "rabbit_brain.tui", str(Path.cwd())])
    assert ui.returncode == screen.returncode == 2 and screen.stdout == "" and screen.stderr == ui.stderr
    assert screen.stderr.startswith("rb ui: rb ui is an interactive screen, and this is an agent session")


def test_a_hand_edited_field_closes_the_screen_with_rbs_message_and_its_fix(repo, capsys):
    """A field rb does not know, added to a question by hand, crashed rb ui with a traceback of its locals and the
    message alone, where every rb command names the hand edit and offers rb doctor --restore. The screen gives the
    terminal back and says it as rb does: rb ui: <message>, then the fix."""
    from rabbit_brain import tui
    q1 = Path(".rb/questions/q1.yaml")
    q1.write_text(q1.read_text(encoding="utf-8") + "future_field: something\n", encoding="utf-8")

    async def started():
        app = tui.RBApp(Path.cwd())
        async with app.run_test(size=(120, 36)) as pilot:
            await pilot.pause(0.3)
        return app

    app = asyncio.run(started())
    assert app.failed is not None and app.failed.code == "E_STATE_NEWER" and app.return_code == 2
    capsys.readouterr()
    assert tui.refused(app.failed) == 2
    said = capsys.readouterr().err.splitlines()
    assert said == ["rb ui: .rb/questions/q1.yaml uses a shape this rb does not know, and it is not what rb last wrote: it was edited by hand, "
                    "or written by a newer rb.",
                    "  → If it was edited by hand, `rb doctor --restore` puts back what rb last wrote; if a newer rb wrote it, upgrade: "
                    "`pip install --upgrade rabbit-brain`."], said


@pytest.mark.parametrize("how", ["r", "look", "card"])
def test_a_hand_edit_made_while_the_screen_is_open_closes_it_with_rbs_message(repo, how):
    """A field rb does not know, added by hand to a question while rb ui was open, crashed the screen with a traceback
    of its locals and exit 0 at the next repaint: r, the look every 10 s that found a new results file, or a card
    opened. The screen closes, and rb ui says it as on opening: its message, then the fix, with rb's exit code."""
    from rabbit_brain import tui
    q1 = Path(".rb/questions/q1.yaml")

    async def opened():
        app = tui.RBApp(Path.cwd())
        async with app.run_test(size=(120, 40)) as pilot:
            await until(pilot, lambda: isinstance(app.screen, tui.HomeScreen))
            home = app.screen
            q1.write_text(q1.read_text(encoding="utf-8") + "future_field: something\n", encoding="utf-8")
            if how == "r":
                app.simulate_key("r")                # as pressed, with no pilot waiting on a screen that closes
            elif how == "look":
                Path("out/new").mkdir(parents=True)
                Path("out/new/metrics.json").write_text('{"top1": 0.71}', encoding="utf-8")
                home._tick = 4                       # the next tick is the 10 s look
                home.call_later(home._poll)
            else:
                home.call_later(lambda: app.push_screen(tui.ObjectScreen(Path.cwd(), "q1")))
            for _ in range(150):                     # a closing screen answers no pilot: the app's own end is awaited
                if app.return_code is not None:
                    break
                await asyncio.sleep(0.1)
        return app

    app = asyncio.run(opened())
    assert app.failed is not None and app.failed.code == "E_STATE_NEWER" and app.return_code == 2, (app.failed, app.return_code)


def test_rb_ui_ends_with_the_screens_code_when_it_fails(repo, monkeypatch):
    """rb ui exited 0 after the screen ended on an error it did not catch: it ends with the screen's code."""
    from rabbit_brain import tui

    def ended_on_an_error(self, *args, **kwargs):
        self._return_code = 1

    monkeypatch.setattr(tui.RBApp, "run", ended_on_an_error)
    assert tui.run_ui(Path.cwd()) == 1


def test_q_closes_the_screen_as_its_footer_says(repo):
    """The footer names q as quit: on the home screen it closes rb, and on a card it only goes back."""
    from rabbit_brain.tui import HomeScreen, ObjectScreen, RBApp
    assert confirm_designs() == ["e1"]                       # nothing waits: tab goes from the catalogue to the card

    async def drive(keys):
        """Press each key and wait for what it should bring: a screen by its class, the app's end ("closed"), or
        nothing in particular (None). Returns how the app ended and the screen seen after each key."""
        app = RBApp(Path.cwd())
        async with app.run_test(size=(120, 36)) as pilot:
            await until(pilot, lambda: isinstance(app.screen, HomeScreen))
            seen = []
            for key, brings in keys:
                await pilot.press(key)
                if brings == "closed":
                    await until(pilot, lambda: app.return_code is not None)
                    break
                if brings is None:
                    await pilot.pause(0.3)
                else:
                    await until(pilot, lambda: isinstance(app.screen, brings))
                seen.append(type(app.screen).__name__)
        return app.return_code, seen

    assert asyncio.run(drive([("q", "closed")]))[0] == 0
    code, seen = asyncio.run(drive([("tab", None), ("enter", ObjectScreen), ("q", HomeScreen)]))
    assert code is None and seen[1:] == [ObjectScreen.__name__, HomeScreen.__name__]
    assert asyncio.run(drive([("x", None)]))[0] is None


def test_q_while_the_screen_still_draws_closes_it_with_no_traceback(repo):
    """q pressed while the screen was still drawing its first rows, as a loaded machine leaves them, ended rb ui with a
    traceback and exit 1: the row's highlight, handled after q, drew its card beside the catalogue on a screen already
    closing. What the screen handles once it is closing draws nothing, and q still ends rb ui with 0."""
    from textual.widgets import OptionList
    from rabbit_brain.tui import HomeScreen, RBApp

    async def closed_with_a_highlight_still_to_handle():
        app = RBApp(Path.cwd())
        async with app.run_test(size=(120, 36)) as pilot:
            await until(pilot, lambda: isinstance(app.screen, HomeScreen) and app.screen.query_one("#tree-list").option_count > 0)
            home = app.screen
            tree = home.query_one("#tree-list", OptionList)
            assert home._wide
            app.exit()                                  # what q does
            at = tree.highlighted or 0
            home.on_option_list_option_highlighted(OptionList.OptionHighlighted(tree, tree.get_option_at_index(at), at))
            home._fill()                                # the 2 s poll, due as it closes, draws the card again
        return app.return_code

    assert asyncio.run(closed_with_a_highlight_still_to_handle()) == 0


def test_a_full_screen_card_closing_as_a_write_lands_draws_nothing(repo, monkeypatch):
    """The same for the full-screen card of what waits on a narrow terminal: a write the poll sees as the screen
    closes shows that card as it reads now, and drew it on a screen already closing."""
    from rabbit_brain.tui import PendingScreen, RBApp
    monkeypatch.setenv("CLAUDECODE", "1")
    assert repo("experiment", "gate", "e1", "--if-holds", "ship it next week")[0] == 0
    monkeypatch.delenv("CLAUDECODE")

    async def closed_as_a_write_lands():
        app = RBApp(Path.cwd())
        async with app.run_test(size=(90, 40)) as pilot:
            await until(pilot, lambda: bool(app.screen.query("#pending")) and app.screen.query_one("#pending").option_count == 1)
            app.screen.query_one("#tree-list").focus()
            await pilot.pause(0.3)
            await pilot.press("c")
            await until(pilot, lambda: isinstance(app.screen, PendingScreen))
            card = app.screen
            app.exit()
            card.show(card.row)
        return app.return_code

    assert asyncio.run(closed_as_a_write_lands()) == 0


def test_a_card_answers_to_the_mouse_too(repo):
    """The terminal streams mouse events to the screen (Textual turns mouse reporting on), so the
    card's chips must run exactly what the keys run: clicking y opens the same Reason modal."""
    from rabbit_brain.tui import CardScreen, HomeScreen, RBApp, Reason
    Path("out").mkdir()
    Path("out/metrics.json").write_text('{"top1": 0.81}', encoding="utf-8")     # a run nobody recorded
    state().scan()
    assert len(home().found) == 1

    async def drive():
        app = RBApp(Path.cwd())
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause(0.3)
            await until(pilot, lambda: isinstance(app.screen, HomeScreen) and getattr(app.focused, "id", None) == "calls")
            await pilot.press("enter")
            await until(pilot, lambda: isinstance(app.screen, CardScreen))
            assert isinstance(app.screen, CardScreen) and app.screen.card.kind == "record"
            await pilot.click(".chip-yes")
            await until(pilot, lambda: isinstance(app.screen, Reason))
            assert isinstance(app.screen, Reason)
            await pilot.press("escape")
            await pilot.pause(0.2)
            assert isinstance(app.screen, CardScreen)   # escape closed the modal alone, not the card
            await pilot.click(".chip-no")
            await until(pilot, lambda: isinstance(app.screen, Reason))
            assert isinstance(app.screen, Reason)
            await pilot.press("escape")

    asyncio.run(drive())
    rec = state()
    assert len(rec.found()[0]) == 1 and len(rec.records("run", live=True)) == 1   # looked at with the mouse, recorded by nobody


def test_the_tree_lists_resources_and_opens_one(repo, capsys):
    """A resource is a row of the catalogue with where it stands now, and enter opens it like any other row."""
    from rabbit_brain.tui import ObjectScreen, RBApp
    assert main(["resource", "add", "imagenet", "--type", "dataset", "--name", "ImageNet-1k", "--version", "validation",
                 "--at", "s3:s3://lab-bucket/imagenet", "--json"]) == 0
    capsys.readouterr()
    row = next(r for r in home().tree if r.kind == "resource")
    assert (row.id, row.label, row.standing, row.depth) == ("imagenet", "ImageNet-1k validation", "remote", 0)
    writes = len(state().log(None))

    async def drive():
        app = RBApp(Path.cwd())
        async with app.run_test(size=(120, 36)) as pilot:
            await pilot.pause(0.3)
            tree = app.screen.query_one("#tree-list")
            ids = [tree.get_option_at_index(i).id for i in range(tree.option_count)]
            assert "o:imagenet" in ids and "o:e1" in ids
            tree.focus()
            tree.highlighted = ids.index("o:imagenet")
            await pilot.pause(0.2)
            await pilot.press("enter")
            await pilot.pause(0.3)
            assert isinstance(app.screen, ObjectScreen) and app.screen.subject == "imagenet"
            assert app.screen.card.title == "ImageNet-1k validation" and app.screen.card.lead == "dataset · remote"
            await pilot.press("escape")

    asyncio.run(drive())
    assert len(state().log(None)) == writes      # browsing the catalogue writes nothing


def test_a_rule_met_on_evidence_with_a_problem_is_never_green(repo):
    """Green is a rule met with nothing wrong with what it rests on: the view model mutes any other met word, for rb open
    and rb page as for the screen. When the source of a setting e2 reads comes to state another value, c2 still meets its rule, and the
    screen draws it dim wherever it draws it, as rb open does: counted on e2's row, on both questions it is explicitly
    linked to (q1a and q1), and as a pass bar on the card of what rb found and on e2's card, beside the catalogue and full
    screen. What is sound stays green beside it: e1's rules, each met across the interval of three runs; the gate q1
    counts beside c2 draws neutral, met or not, a gate being policy (D4)."""
    from rabbit_brain.tui import DIM, GOOD, CardScreen, HomeScreen, ObjectScreen, RBApp
    Path("configs").mkdir()
    Path("configs/train.yaml").write_text("optim:\n  lr: 0.001\n", encoding="utf-8")
    assert repo("criterion", "add", "top1 clears the bar", "-e", "e1", "--metric", "top1", "--at-least", "0.6", "--role", "gate",
                "--id", "g1", "-q", "q1")[0] == 0
    assert repo("question", "add", "does it hold up", "--parent", "q1", "--id", "q1a")[0] == 0
    assert repo("experiment", "add", "the follow-up", "--id", "e2", "--hypothesis", "it holds up", "-q", "q1a")[0] == 0
    assert repo("spec", "set", "e2", "optim.lr", "--source", "configs/train.yaml#optim.lr")[0] == 0
    assert repo("criterion", "add", "loss stays low", "-e", "e2", "--metric", "loss", "--at-most", "0.5", "--id", "c2", "-q", "q1a", "-q", "q1")[0] == 0
    for top1 in ("0.78", "0.8"):
        assert repo("run", "add", "e1", f"top1={top1}")[0] == 0
    for loss in ("0.3", "0.32", "0.34"):
        assert repo("run", "add", "e2", f"loss={loss}")[0] == 0
    Path("out").mkdir()
    Path("out/metrics.json").write_text('{"loss": 0.25}', encoding="utf-8")     # a run nobody recorded, read against c2
    state().scan()
    words = {"c1": "criterion met", "g1": "gate met", "c2": "criterion met"}

    async def drawn() -> dict:
        """The colour of each met rule, everywhere the screen draws it."""
        app = RBApp(Path.cwd())
        seen = {}

        def bars(where) -> dict:
            """The colour of each pass bar's rule in `where`, by its criterion."""
            out = {}
            for bar in where.query(".bar"):
                c = str(bar.query_one(".bar-id").render())
                out[c] = _colour_of(bar.query_one(".bar-body").render(), words[c])
            return out

        def beside(title: str):
            """The card beside the catalogue, once it is the one titled `title` and its pass bars are drawn."""
            pane = app.screen.query_one("#side-card")
            if [str(w.render()) for w in pane.query(".card-title")] == [title] and pane.query(".bar"):
                return pane
            return None

        async with app.run_test(size=(240, 40)) as pilot:
            await until(pilot, lambda: isinstance(app.screen, HomeScreen) and getattr(app.focused, "id", None) == "calls")
            tree = app.screen.query_one("#tree-list")
            ids = [tree.get_option_at_index(i).id for i in range(tree.option_count)]
            for row, parts in (("q1", ("2 criteria met", "1 gate met")), ("e1", ("1 criterion met", "1 gate met")), ("q1a", ("1 criterion met",)),
                               ("e2", ("1 criterion met",))):
                for part in parts:
                    seen[f"{row}'s row, {part}"] = _colour_of(tree.get_option_at_index(ids.index(f"o:{row}")).prompt, part)
            await pilot.press("enter")
            await until(pilot, lambda: isinstance(app.screen, CardScreen))
            seen["what rb found"] = bars(app.screen)
            await pilot.press("escape")
            await until(pilot, lambda: isinstance(app.screen, HomeScreen))
            tree.focus()
            for e, title in (("e1", "the experiment"), ("e2", "the follow-up")):
                tree.highlighted = ids.index(f"o:{e}")
                await until(pilot, lambda: beside(title) is not None)
                seen[f"{e} beside"] = bars(beside(title))
            await pilot.press("enter")
            await until(pilot, lambda: isinstance(app.screen, ObjectScreen))
            seen["e2 full screen"] = bars(app.screen)
            await pilot.press("escape")
        return seen

    # a gate met draws neutral: a gate is policy, not a finding (D4)
    green = {"q1's row, 2 criteria met": GOOD, "q1's row, 1 gate met": DIM, "e1's row, 1 criterion met": GOOD, "e1's row, 1 gate met": DIM,
             "q1a's row, 1 criterion met": GOOD, "e2's row, 1 criterion met": GOOD, "what rb found": {"c2": GOOD},
             "e1 beside": {"c1": GOOD, "g1": DIM}, "e2 beside": {"c2": GOOD}, "e2 full screen": {"c2": GOOD}}
    assert asyncio.run(drawn()) == green
    Path("configs/train.yaml").write_text("optim:\n  lr: 0.002\n", encoding="utf-8")
    c2 = next(f for b in home().briefs for e in b.experiments for f in e.findings if f.criterion_id == "c2")
    assert c2.standing == "criterion met · measured" and not c2.good
    assert asyncio.run(drawn()) == {**green, "q1's row, 2 criteria met": DIM, "q1a's row, 1 criterion met": DIM, "e2's row, 1 criterion met": DIM,
                                    "what rb found": {"c2": DIM}, "e2 beside": {"c2": DIM}, "e2 full screen": {"c2": DIM}}


def test_questions_a_merge_put_each_under_the_other_count_as_rb_counts(repo):
    """Two branches each move a question under the other and the merge is clean: q2 and q3 now sit each under the
    other. Each still counts only its directly linked rule, as rb counts it. A problem in q3's evidence dims q3's
    count without leaking through the cycle into q2, whichever row the tree draws first."""
    from rabbit_brain.tui import DIM, GOOD, HomeScreen, RBApp
    Path("configs").mkdir()
    Path("configs/train.yaml").write_text("optim:\n  lr: 0.001\n", encoding="utf-8")
    assert repo("question", "add", "is it cheap", "--id", "q3")[0] == 0
    assert repo("experiment", "add", "the scale run", "--id", "e2", "--hypothesis", "it scales", "-q", "q2")[0] == 0
    assert repo("criterion", "add", "throughput holds", "-e", "e2", "--metric", "rate", "--at-least", "100", "--id", "c2", "-q", "q2")[0] == 0
    for rate in ("120", "125", "130"):
        assert repo("run", "add", "e2", f"rate={rate}")[0] == 0
    assert repo("experiment", "add", "the cost run", "--id", "e3", "--hypothesis", "it is cheap", "-q", "q3")[0] == 0
    assert repo("spec", "set", "e3", "optim.lr", "--source", "configs/train.yaml#optim.lr")[0] == 0
    assert repo("criterion", "add", "cost stays low", "-e", "e3", "--metric", "cost", "--at-most", "10", "--id", "c3", "-q", "q3")[0] == 0
    for cost in ("8", "8.1", "8.2"):
        assert repo("run", "add", "e3", f"cost={cost}")[0] == 0

    def git(*argv):
        subprocess.run(["git", *argv], check=True, capture_output=True)

    git("add", "-A")
    git("commit", "-qm", "the record")
    git("checkout", "-qb", "elsewhere")
    assert repo("question", "move", "q2", "--parent", "q3")[0] == 0
    git("add", "-A")
    git("commit", "-qm", "q2 under q3")
    git("checkout", "-q", "-")
    assert repo("question", "move", "q3", "--parent", "q2")[0] == 0
    git("add", "-A")
    git("commit", "-qm", "q3 under q2")
    git("merge", "-q", "--no-edit", "elsewhere")
    assert all(r.muted == [] for r in home().tree if r.kind == "question" and r.id in ("q2", "q3"))
    Path("configs/train.yaml").write_text("optim:\n  lr: 0.002\n", encoding="utf-8")
    standing = {r.id: r.standing for r in home().tree if r.kind == "question"}
    assert standing["q2"] == standing["q3"] == "open · 1 criterion met"

    async def drawn() -> dict:
        """The colour of each row's count of met rules, for the two questions and their experiments."""
        app = RBApp(Path.cwd())
        async with app.run_test(size=(240, 40)) as pilot:
            await until(pilot, lambda: isinstance(app.screen, HomeScreen) and any(t.option_count for t in app.screen.query("#tree-list")))
            tree = app.screen.query_one("#tree-list")
            rows = [tree.get_option_at_index(i) for i in range(tree.option_count)]
            return {o.id[2:]: _colour_of(o.prompt, "criterion met") for o in rows if o.id in ("o:q2", "o:q3", "o:e2", "o:e3")}

    assert asyncio.run(drawn()) == {"q2": GOOD, "q3": DIM, "e2": GOOD, "e3": DIM}


# ---------------------------------------------------------------- other places and past versions: the essentials


def placed_at(base: Path, monkeypatch):
    """The screen started in the main checkout of a clone at `base` with a place of every kind (tests/rbtest.py), with
    a call proposed in the sibling's own record, which waits for a confirmation there."""
    as_the_user(monkeypatch)
    rec = places_record(base)
    for k in ("GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM", "RB_TELEMETRY", "DO_NOT_TRACK", "RB_ACTOR"):
        monkeypatch.setenv(k, rec.env[k])
    rec.proposed = rec.rb("decide", "e1", "accept", "--why", "the sibling's runs hold", cwd=rec.tree["exp-c"], who=PLACES_AGENT)["object"]["id"]
    monkeypatch.chdir(rec.root)
    rec.common = Path(rec.git("rev-parse", "--path-format=absolute", "--git-common-dir"))
    return rec


@pytest.fixture
def placed(tmp_path, monkeypatch):
    """placed_at, in the test's own folder."""
    return placed_at(tmp_path / "clone", monkeypatch)


def _options(screen, list_id: str) -> list:
    from textual.widgets import OptionList
    options = screen.query_one(list_id, OptionList)
    return [(str(options.get_option_at_index(i).prompt), options.get_option_at_index(i).disabled) for i in range(options.option_count)]


async def _choose(pilot, app, list_id: str, starts: str) -> None:
    """w, then enter on the row of `list_id` that starts with `starts`."""
    from textual.widgets import OptionList
    from rabbit_brain.tui import PlacesScreen
    await pilot.press("w")
    await until(pilot, lambda: isinstance(app.screen, PlacesScreen))
    options = app.screen.query_one(list_id, OptionList)
    options.focus()
    options.highlighted = next(i for i, (text, _) in enumerate(_options(app.screen, list_id)) if text.startswith(starts))
    await pilot.press("enter")


def test_w_lists_the_places_and_shows_another_place_only_to_read(placed):
    """w lists each worktree by its branch and path with what it is (one with no record of its own to show cannot be
    chosen), then the versions of the record shown; enter on another place shows its record, says which, and leaves y,
    n and c unbound there: its call waits with the one command that confirms it there, and c confirms nothing. The
    mark of what the user has seen is never moved by looking at another place, and escape goes back."""
    from rabbit_brain.tui import HomeScreen, PlacesScreen, RBApp
    rec = placed
    seen = rec.common / "rb-last-seen.json"
    got: dict = {}

    async def drive():
        app = RBApp(rec.root)
        async with app.run_test(size=(150, 44)) as pilot:
            await until(pilot, lambda: isinstance(app.screen, HomeScreen) and app.screen.query_one("#tree-list").option_count)
            home = app.screen
            got["start line"] = str(home.query_one("#place").render())
            got["start keys"] = [home.check_action(a, ()) for a in ("yes", "no", "confirm")]
            await pilot.press("w")
            await until(pilot, lambda: isinstance(app.screen, PlacesScreen))
            got["where"], got["versions"] = _options(app.screen, "#where"), _options(app.screen, "#versions")
            await pilot.press("escape")
            await until(pilot, lambda: app.screen is home)
            got["mark"] = seen.read_bytes()
            await _choose(pilot, app, "#where", "exp-c · ")
            await until(pilot, lambda: home.place is not None)
            got["there line"] = str(home.query_one("#place").render())
            got["there runs"] = {r.run_id for r in home.home.runs}
            got["there keys"] = [home.check_action(a, ()) for a in ("yes", "no", "confirm")]
            got["there pending"] = [(p.id, p.confirm) for p in home.home.pending]
            got["subtitle"] = home.query_one("#pending-panel").border_subtitle
            home.query_one("#pending").focus()
            await pilot.press("c")
            await pilot.press("c")
            await pilot.pause(1.0)
            home.refresh_home()
            await pilot.pause(2.5)                                 # a poll passes
            got["mark there"] = seen.read_bytes()
            await _choose(pilot, app, "#where", "main · ")
            await until(pilot, lambda: home.place is None)
            got["back keys"] = [home.check_action(a, ()) for a in ("yes", "no", "confirm")]
            got["back runs"] = {r.run_id for r in home.home.runs}

    asyncio.run(drive())
    c_path = rec.tree["exp-c"].as_posix()
    main_short, c_short = in_short(rec.root.resolve().as_posix()), in_short(c_path)
    assert got["start line"] == f"main · {main_short}" and got["start keys"] == [True, True, True]
    where = dict(got["where"])
    assert got["where"][0][0].startswith(f"main · {main_short}: this record · last write ") and got["where"][0][1] is False
    assert [t for t, off in got["where"] if off] == [t for t in where if t.startswith(("exp-b · ", "exp-g · ", "exp-n · "))]
    assert any(t.startswith(f"exp-c · {c_short}: its own record · ") for t in where)
    assert any(t.startswith("exp-b · ") and ": no record of its own: it writes to this record · 1 run recorded from it" in t for t in where)
    assert [t for t, _ in got["versions"]] == [f"{rec.git('rev-parse', 'HEAD')[:12]} · " + got["versions"][0][0].split(" · ")[1]]
    assert got["there line"].startswith(f"exp-c · {c_short} · read only · esc: back to main · its own record · ")
    assert rec.run_sibling in got["there runs"] and rec.run_sibling not in got["back runs"]
    assert got["there keys"] == [False, False, False] and got["back keys"] == [True, True, True]
    assert got["there pending"] == [(rec.proposed, f"cd {c_path} && rb confirm {rec.proposed}")] and got["subtitle"] == ""
    assert [d["id"] for d in rec.rb("status", cwd=rec.tree["exp-c"])["pending"]] == [rec.proposed]     # c confirmed nothing
    assert got["mark there"] == got["mark"]


def test_w_names_each_place_in_short_with_its_whole_path_in_the_detail(tmp_path, monkeypatch):
    """With the home folder above the clone, the line under the head and w name each place in short (its branch, then
    its path from the main checkout or from ~), and the highlighted place's whole path is said under the list."""
    from textual.widgets import OptionList
    from rabbit_brain.tui import HomeScreen, PlacesScreen, RBApp
    as_the_user(monkeypatch)
    rec = places_record(tmp_path / "clone")
    for k in ("GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM", "RB_TELEMETRY", "DO_NOT_TRACK", "RB_ACTOR"):
        monkeypatch.setenv(k, rec.env[k])
    at_home(monkeypatch, tmp_path)
    monkeypatch.chdir(rec.root)
    got: dict = {}

    async def drive():
        app = RBApp(rec.root)
        async with app.run_test(size=(150, 44)) as pilot:
            await until(pilot, lambda: isinstance(app.screen, HomeScreen) and app.screen.query_one("#tree-list").option_count)
            got["line"] = str(app.screen.query_one("#place").render())
            await pilot.press("w")
            await until(pilot, lambda: isinstance(app.screen, PlacesScreen))
            got["where"] = [t for t, _ in _options(app.screen, "#where")]
            where = app.screen.query_one("#where", OptionList)
            where.highlighted = next(i for i, t in enumerate(got["where"]) if t.startswith("exp-c · "))
            await pilot.pause(0.3)
            got["detail"] = str(app.screen.query_one("#where-detail").render())

    asyncio.run(drive())
    assert got["line"] == "main · ~/clone/p"
    assert got["where"][0].startswith("main · ~/clone/p: this record · ")
    assert any(t.startswith("exp-b · .worktrees/b: no record of its own: it writes to this record") for t in got["where"])
    assert any(t.startswith("exp-c · ~/clone/wt-c: its own record") for t in got["where"])
    listed = [l[len("worktree "):] for l in rec.git("worktree", "list", "--porcelain").splitlines() if l.startswith("worktree ")]
    assert got["detail"] == next(l for l in listed if l.endswith("/wt-c"))


def test_away_from_where_it_started_the_line_says_so_and_escape_goes_back(placed):
    """Showing another place, the line under the head is in the warning colour, says read only and that escape goes
    back to the branch the screen started on; escape does, and the line is quiet again. Where it started, escape is
    no key at all."""
    from rabbit_brain.tui import HomeScreen, RBApp, WARN
    rec = placed
    got: dict = {}

    async def drive():
        app = RBApp(rec.root)
        async with app.run_test(size=(150, 44)) as pilot:
            await until(pilot, lambda: isinstance(app.screen, HomeScreen) and app.screen.query_one("#tree-list").option_count)
            home = app.screen
            got["start"] = (str(home.query_one("#place").render()), home.check_action("back_to_start", ()))
            await _choose(pilot, app, "#where", "exp-c · ")
            await until(pilot, lambda: home.place is not None and app.screen is home)
            line = home.query_one("#place").render()
            got["away"] = (str(line), {str(sp.style) for sp in getattr(line, "spans", [])}, home.check_action("back_to_start", ()))
            await pilot.press("escape")
            await until(pilot, lambda: home.place is None)
            got["back"] = str(home.query_one("#place").render())

    asyncio.run(drive())
    c_short = in_short(rec.tree["exp-c"].as_posix())
    assert got["start"] == (f"main · {in_short(rec.root.resolve().as_posix())}", False)
    line, styles, bound = got["away"]
    assert line.startswith(f"exp-c · {c_short} · read only · esc: back to main · its own record · ") and bound is True
    assert any(WARN.lower() in st.lower() for st in styles), styles
    assert got["back"] == got["start"][0]


def test_away_on_a_narrow_terminal_the_line_keeps_read_only_and_the_way_back(tmp_path, monkeypatch):
    """At 100 columns, with a place whose path is long, the line under the head still shows, in words, that what it
    shows is read only and that escape goes back: it wraps rather than cuts, and they come before the place's counts."""
    from textual.geometry import Region
    from rabbit_brain.tui import HomeScreen, RBApp
    # long as the screen names it, wherever the tests run
    rec = placed_at(tmp_path / "a-folder-whose-name-is-long-on-purpose" / "so-the-place-line-has-to-wrap-at-100" / "clone", monkeypatch)
    got: dict = {}

    async def drive():
        app = RBApp(rec.root)
        async with app.run_test(size=(100, 44)) as pilot:
            await until(pilot, lambda: isinstance(app.screen, HomeScreen) and app.screen.query_one("#tree-list").option_count)
            home = app.screen
            await _choose(pilot, app, "#where", "exp-c · ")
            await until(pilot, lambda: home.place is not None and app.screen is home)
            await pilot.pause(0.3)
            w = home.query_one("#place")
            got["seen"] = [strip.text.rstrip() for strip in w.render_lines(Region(0, 0, w.size.width, w.size.height))]

    asyncio.run(drive())
    seen = " ".join(got["seen"])
    assert len(f"exp-c · {in_short(rec.tree['exp-c'].as_posix())}") > 90 and "…" not in seen, got
    assert "read only · esc: back to main · its own record · " in seen and " · last write " in seen, got


def test_w_in_a_single_checkout_names_its_place_as_it_always_has(repo, workdir, monkeypatch):
    """In a single checkout, w lists its one place by its branch and whole path, even under the home folder, with
    nothing under it, as before rb named places in short; the line under the head names it by its branch alone."""
    from rabbit_brain.tui import HomeScreen, PlacesScreen, RBApp
    at_home(monkeypatch, workdir.parent)
    branch = subprocess.run(["git", "symbolic-ref", "--short", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
    got: dict = {}

    async def drive():
        app = RBApp(workdir)
        async with app.run_test(size=(150, 44)) as pilot:
            await until(pilot, lambda: isinstance(app.screen, HomeScreen) and app.screen.query_one("#tree-list").option_count)
            got["line"] = str(app.screen.query_one("#place").render())
            await pilot.press("w")
            await until(pilot, lambda: isinstance(app.screen, PlacesScreen))
            await pilot.pause(0.3)
            got["where"] = _options(app.screen, "#where")
            got["detail"] = app.screen.query_one("#where-detail").display

    asyncio.run(drive())
    whole = f"{branch} · {workdir.resolve().as_posix()}"
    assert got["line"] == branch and "/" not in got["line"]
    [(text, off)] = got["where"]
    assert text.startswith(f"{whole}: this record · ") and off is False and got["detail"] is False


def test_the_place_line_follows_the_writes_made_there(placed):
    """The line under the head says the place shown with its own counts, and they move with the writes made there."""
    from rabbit_brain.tui import HomeScreen, RBApp
    rec = placed
    got: dict = {}

    async def drive():
        app = RBApp(rec.root)
        async with app.run_test(size=(150, 44)) as pilot:
            await until(pilot, lambda: isinstance(app.screen, HomeScreen) and app.screen.query_one("#tree-list").option_count)
            home = app.screen
            await _choose(pilot, app, "#where", "exp-c · ")
            await until(pilot, lambda: home.place is not None)
            got["before"] = str(home.query_one("#place").render())
            rec.rb("run", "add", "e1", "fp32.top1=0.764", "int8.top1=0.760", cwd=rec.tree["exp-c"])
            await until(pilot, lambda: "4 record files not committed" in str(home.query_one("#place").render()), timeout=20)
            got["after"] = str(home.query_one("#place").render())

    asyncio.run(drive())
    assert " · 3 record files not committed · " in got["before"] and " · 4 record files not committed · " in got["after"]


def test_on_a_narrow_screen_enter_opens_a_waiting_call_with_the_command_that_confirms_it(placed):
    """Below the width where a card sits beside the list, enter on a call that waits opens its card full screen: away
    from where the screen started, with the one command that confirms it there."""
    from textual.widgets import OptionList
    from rabbit_brain.tui import HomeScreen, PendingScreen, RBApp
    rec = placed
    got: dict = {}

    async def drive():
        app = RBApp(rec.root)
        async with app.run_test(size=(90, 40)) as pilot:
            await until(pilot, lambda: isinstance(app.screen, HomeScreen) and app.screen.query_one("#tree-list").option_count)
            home = app.screen
            await _choose(pilot, app, "#where", "exp-c · ")
            await until(pilot, lambda: home.place is not None and app.screen is home)
            home.query_one("#pending", OptionList).focus()
            await pilot.press("enter")
            await until(pilot, lambda: isinstance(app.screen, PendingScreen))
            got["opened"] = isinstance(app.screen, PendingScreen)
            got["card"] = " ".join(str(w.render()) for w in app.screen.query("Static"))
            await pilot.press("escape")
            await until(pilot, lambda: app.screen is home)

    asyncio.run(drive())
    assert got["opened"] and f"cd {rec.tree['exp-c'].as_posix()} && rb confirm {rec.proposed}" in got["card"]


@pytest.mark.skipif(sys.platform == "win32", reason="the lock a test holds is a POSIX lock")
def test_w_shows_a_past_version_while_rb_ui_holds_its_folder(placed, monkeypatch):
    """A version is shown from a checkout of its commit in the views folder rb ui holds (RB_VIEWS), said as a past
    version, read only, with y, n and c unbound; without a folder held, it is said why and the screen stays."""
    import fcntl
    from rabbit_brain.tui import HomeScreen, RBApp
    rec = placed
    head = rec.git("rev-parse", "HEAD")
    folder = rec.common / "rb" / "views" / "4242-feedface"
    folder.mkdir(parents=True)
    got: dict = {}

    async def drive():
        app = RBApp(rec.root)
        async with app.run_test(size=(150, 44)) as pilot:
            await until(pilot, lambda: isinstance(app.screen, HomeScreen) and app.screen.query_one("#tree-list").option_count)
            home = app.screen
            await _choose(pilot, app, "#versions", head[:12])
            await pilot.pause(1.0)
            got["unheld"] = (home.at, str(home.query_one("#place").render()))
            with open(folder / ".lock", "w") as f:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                monkeypatch.setenv("RB_VIEWS", folder.name)
                await _choose(pilot, app, "#versions", head[:12])
                await until(pilot, lambda: home.at is not None)
                got["line"] = str(home.query_one("#place").render())
                got["keys"] = [home.check_action(a, ()) for a in ("yes", "no", "confirm")]
                got["runs"] = len(home.home.runs)
                got["kept"] = sorted(d.name for d in folder.iterdir() if not d.name.startswith("."))

    asyncio.run(drive())
    main_short = in_short(rec.root.resolve().as_posix())
    assert got["unheld"] == (None, f"main · {main_short}")
    assert got["line"].startswith(f"main · {main_short} as of {head[:12]} · ") and got["line"].endswith(" UTC · read only · esc: back to main")
    assert got["keys"] == [False, False, False] and got["runs"] == 2 and got["kept"] == [head[:12]]
    import shutil as _shutil
    _shutil.rmtree(folder)


@pytest.mark.skipif(sys.platform == "win32", reason="the lock a test holds is a POSIX lock")
def test_a_past_version_s_waiting_call_shows_no_command(placed, monkeypatch):
    """Guarantee 38: a past version offers no command to copy. A call a session proposed, committed, and shown at that
    version on rb ui keeps its card, what it is and its words, with no rb confirm line."""
    import fcntl
    from textual.widgets import OptionList
    from rabbit_brain.tui import HomeScreen, PendingScreen, RBApp
    rec = placed
    proposed = rec.rb("decide", "e1", "investigate", "--why", "one more seed", who=PLACES_AGENT)["object"]["id"]
    rec.git("add", ".rb")
    rec.git("commit", "-qm", "a proposed call")
    head = rec.git("rev-parse", "HEAD")
    folder = rec.common / "rb" / "views" / "4243-feedface"
    folder.mkdir(parents=True)
    got: dict = {}

    async def drive():
        app = RBApp(rec.root)
        async with app.run_test(size=(90, 40)) as pilot:
            await until(pilot, lambda: isinstance(app.screen, HomeScreen) and app.screen.query_one("#tree-list").option_count)
            home = app.screen
            with open(folder / ".lock", "w") as f:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                monkeypatch.setenv("RB_VIEWS", folder.name)
                await _choose(pilot, app, "#versions", head[:12])
                await until(pilot, lambda: home.at is not None and app.screen is home)
                got["rows"] = [(r.id, r.confirm) for r in home.home.pending]
                home.query_one("#pending", OptionList).focus()
                await pilot.press("enter")
                await until(pilot, lambda: isinstance(app.screen, PendingScreen))
                got["card"] = " ".join(str(w.render()) for w in app.screen.query("Static"))
                await pilot.press("escape")

    asyncio.run(drive())
    assert got["rows"] == [(proposed, "")], got["rows"]
    assert "one more seed" in got["card"] and "rb confirm" not in got["card"], got["card"]
    import shutil as _shutil
    _shutil.rmtree(folder)


def _y_on_the_first_found(rec, app_here) -> dict:
    """y, then enter, on the first thing the screen started in `app_here` lists as found: the receipt of the run it
    recorded, with its metrics and files."""
    from rabbit_brain.tui import HomeScreen, RBApp, Reason
    before = {r["id"] for r in rec.rb("status")["runs"]}

    async def drive():
        app = RBApp(rec.root, app_here)
        async with app.run_test(size=(120, 36)) as pilot:
            await until(pilot, lambda: isinstance(app.screen, HomeScreen) and getattr(app.focused, "id", None) == "calls")
            await pilot.press("y")
            await until(pilot, lambda: isinstance(app.screen, Reason))
            await pilot.press("enter")
            await until(pilot, lambda: len(rec.rb("status")["runs"]) > len(before), timeout=60)

    asyncio.run(drive())
    [made] = [r for r in rec.rb("status")["runs"] if r["id"] not in before]
    return rec.rb("show", made["id"])["object"]


def test_y_records_what_rb_found_where_rb_found_it_wherever_the_screen_started(placed):
    """y runs what rb found in the checkout rb found it in, whose paths and code it is: from a screen started in a
    nested worktree that holds a file of the same name, a file found in the record's checkout is recorded with its own
    numbers and the checkout's code, and so is what a command printed in the checkout."""
    rec = placed
    b = rec.tree["exp-b"]
    for top, nums in ((rec.root, {"int8": {"top1": 0.761}, "fp32": {"top1": 0.77}}), (b, {"int8": {"top1": 0.5}, "fp32": {"top1": 0.9}})):
        (top / "out").mkdir()
        (top / "out" / "metrics.json").write_text(json.dumps(nums), encoding="utf-8")
    rec.rb("journal", "scan")
    run = _y_on_the_first_found(rec, b)
    assert run["metrics"] == {"int8.top1": 0.761, "fp32.top1": 0.77} and [f["path"] for f in run["files"]] == ["out/metrics.json"]
    assert "worktree" not in run["receipt"]["attached"] and run["receipt"]["attached"]["branch"] == "main"
    printed = 'import json; print(json.dumps({"metrics": {"fp32.top1": 0.75, "int8.top1": 0.748}}))'
    ran = subprocess.run([*command(), "journal", "run", "--", sys.executable, "-c", printed], cwd=rec.root, env=rec.env, capture_output=True, text=True)
    assert ran.returncode == 0, ran.stderr                                   # a run nobody recorded, printed in the checkout
    run = _y_on_the_first_found(rec, b)
    assert run["metrics"] == {"fp32.top1": 0.75, "int8.top1": 0.748}
    assert "worktree" not in run["receipt"]["attached"] and run["receipt"]["attached"]["branch"] == "main"


def test_y_on_what_a_command_printed_in_a_nested_worktree_records_that_worktree_s_code(placed):
    """What a watched command printed in a worktree nested in the checkout is recorded by y where it ran, as the user
    would type it there, wherever the screen started: the run keeps the worktree's code."""
    from rabbit_brain.tui import HomeScreen, RBApp, Reason
    rec = placed
    b = rec.tree["exp-b"]
    printed = 'import json; print(json.dumps({"metrics": {"fp32.top1": 0.75, "int8.top1": 0.749}}))'
    ran = subprocess.run([*command(), "journal", "run", "--", sys.executable, "-c", printed], cwd=b, env=rec.env, capture_output=True, text=True)
    assert ran.returncode == 0, ran.stderr                                   # a run nobody recorded, printed there
    before = {r["id"] for r in rec.rb("status")["runs"]}

    async def drive():
        app = RBApp(rec.root)
        async with app.run_test(size=(120, 36)) as pilot:
            await until(pilot, lambda: isinstance(app.screen, HomeScreen) and getattr(app.focused, "id", None) == "calls")
            await pilot.press("y")
            await until(pilot, lambda: isinstance(app.screen, Reason))
            await pilot.press("enter")
            await until(pilot, lambda: len(rec.rb("status")["runs"]) > len(before), timeout=60)

    asyncio.run(drive())
    [made] = [r for r in rec.rb("status")["runs"] if r["id"] not in before]
    attached = rec.rb("show", made["id"])["object"]["receipt"]["attached"]
    assert attached["worktree"] == {"path": b.as_posix()} and attached["branch"] == "exp-b" and attached["dirty"] is True


@pytest.mark.skipif(sys.platform == "win32", reason="the terminal rb ui needs is a pty here")
def test_rb_ui_opens_the_screen_where_the_user_is_and_holds_a_folder_for_past_versions_while_open(placed, tmp_path):
    """rb ui hands the screen the record and the checkout the user is in, and holds a views folder for the past
    versions it shows (RB_VIEWS) while the screen is open; it is taken away when the screen closes."""
    import os
    import pty
    rec = placed
    said = tmp_path / "said.json"
    screen = tmp_path / "screen"                                    # stands in for the Python that draws the screen
    views = rec.common / "rb" / "views"
    screen.write_text(f"""#!{sys.executable}
import json, os, sys
folder = os.path.join({str(views)!r}, os.environ.get("RB_VIEWS", "-"))
json.dump({{"argv": sys.argv[1:], "held": os.path.isfile(os.path.join(folder, ".lock"))}}, open({str(said)!r}, "w"))
""", encoding="utf-8")
    screen.chmod(0o755)
    main_side, user_side = pty.openpty()
    try:
        r = subprocess.run([*command(), "ui"], cwd=rec.tree["exp-b"], env={**rec.env, "RB_PYTHON": str(screen)}, stdin=user_side,
                           stdout=user_side, stderr=user_side, timeout=60)
    finally:
        os.close(user_side)
        os.close(main_side)
    got = json.loads(said.read_text(encoding="utf-8"))
    assert r.returncode == 0 and got["argv"][-2:] == [rec.root.resolve().as_posix(), rec.tree["exp-b"].as_posix()]
    assert got["held"] is True and not views.exists()


@pytest.mark.skipif(sys.platform == "win32", reason="the terminal rb ui needs is a pty here")
def test_rb_ui_ended_by_a_request_to_end_ends_its_screen_and_takes_its_folder_away(placed, tmp_path):
    """A request to end rb ui (SIGTERM, as a closed ssh session's SIGHUP is) is passed on to the screen, and the
    folder rb ui held for past versions goes with it before rb ends as that signal ends a program."""
    import os
    import pty
    import signal
    import time
    rec = placed
    said = tmp_path / "said.json"
    screen = tmp_path / "screen"                                    # stands in for the Python that draws the screen
    views = rec.common / "rb" / "views"
    screen.write_text(f"""#!{sys.executable}
import json, os, time
json.dump({{"held": os.environ.get("RB_VIEWS", "")}}, open({str(said)!r} + ".part", "w"))
os.replace({str(said)!r} + ".part", {str(said)!r})
time.sleep(60)
""", encoding="utf-8")
    screen.chmod(0o755)
    main_side, user_side = pty.openpty()
    was = signal.signal(signal.SIGTERM, signal.SIG_DFL)            # a signal rb is started to ignore stays ignored
    try:
        proc = subprocess.Popen([*command(), "ui"], cwd=rec.root, env={**rec.env, "RB_PYTHON": str(screen)}, stdin=user_side,
                                stdout=user_side, stderr=user_side)
    finally:
        signal.signal(signal.SIGTERM, was)
    try:
        deadline = time.monotonic() + 60
        while not said.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        token = json.loads(said.read_text(encoding="utf-8"))["held"]
        assert token and (views / token).is_dir()
        proc.send_signal(signal.SIGTERM)
        assert proc.wait(timeout=30) == -signal.SIGTERM
    finally:
        if proc.poll() is None:
            proc.kill()
        os.close(user_side)
        os.close(main_side)
    assert not views.exists()


def test_rb_ui_says_rules_as_the_page_and_rb_status_do(repo, capsys, tmp_path):
    """One wording for rules (0.6.1): rb ui's tally says the page's and rb status's line exactly ("Rules: 1 met · 1 not
    met · 1 can't tell yet · 1 gate missed"), its catalogue rows and pass bars say a rule's words as the page does ("1
    rule met", "rule not met"), and an experiment's card heads its pass bars "Its rules:", as rb open's pane does. The
    model keeps rb's own words ("1 criterion met"), which the colours read."""
    import html as _html
    from rabbit_brain.tui import plain_word, said_standing, tally_text

    for top1 in ("0.78", "0.8"):
        assert repo("run", "add", "e1", f"top1={top1}")[0] == 0
    for c in (["top5 holds", "--metric", "top5", "--at-least", "0.99", "--noise", "0.001"],
              ["loss holds", "--metric", "loss", "--at-most", "1.0", "--min-n", "5"],
              ["top1 clears the bar", "--metric", "top1", "--at-least", "0.9", "--role", "gate"]):
        assert repo("criterion", "add", c[0], "-e", "e1", *c[1:], "-q", "q1")[0] == 0
    for top5, loss in (("0.9", "0.5"), ("0.91", "0.4"), ("0.92", "0.45")):
        assert repo("run", "add", "e1", "top1=0.79", f"top5={top5}", f"loss={loss}")[0] == 0
    assert main(["status"]) == 0
    [status] = [x for x in capsys.readouterr().out.splitlines() if x.startswith("Rules: ")]
    assert main(["page", "--out", str(tmp_path / "page.html")]) == 0
    capsys.readouterr()
    page = (tmp_path / "page.html").read_text(encoding="utf-8")
    [tally] = {_html.unescape(re.sub(r"<[^>]+>", "", m)) for m in re.findall(r'<p class="stt">(.*?)</p>', page)}
    h = home()
    shown = tally_text(h.tally, h.get("muted", ())).plain
    assert shown == status == tally == "Rules: 1 met · 1 not met · 1 can't tell yet · 1 gate missed", (shown, status, tally)
    rows = {r.id: r.standing for r in h.tree}
    assert rows["e1"].startswith("open · 1 criterion met · 1 criterion not met")       # rb's own words, in the model
    assert said_standing(rows["e1"]).startswith("open · 1 rule met · 1 rule not met · 1 can't tell yet")
    assert plain_word("2 criteria met") == "2 rules met" and plain_word("estimate meets criterion") == "estimate meets the rule"
    drawn = asyncio.run(catalogue(240))
    assert "1 rule met · 1 rule not met" in drawn["o:e1"].plain and "criteri" not in drawn["o:e1"].plain, drawn["o:e1"].plain
    assert object_card("e1").bars_label == "Its rules:"

    async def card(subject: str) -> str:
        from rabbit_brain.tui import ObjectScreen, RBApp
        app = RBApp(Path.cwd())
        async with app.run_test(size=(160, 48)) as pilot:
            await until(pilot, lambda: isinstance(app.screen, HomeScreen) and _laid(app))
            app.push_screen(ObjectScreen(Path.cwd(), subject))
            await until(pilot, lambda: isinstance(app.screen, ObjectScreen) and app.screen.query(".lead"))
            return _shown(app.screen)

    from rabbit_brain.tui import HomeScreen
    c1 = object_card("c1")
    assert c1.who_line.startswith("rule · ") and " · criterion met" in c1.lead, (c1.who_line, c1.lead)
    shown = asyncio.run(card("c1"))
    assert " · rule met" in shown and "rule: rule met: estimate" in shown and "; rule met: estimate" in shown, shown
    assert "criteri" not in shown, shown


def test_a_rule_card_says_rb_s_clauses_plainly_and_the_statement_as_written(repo):
    """On a rule's card the finding's sentence keeps its statement as written, a statement that reads like a rule word
    ("untested: example") included, and says rb's own clauses after it as rb open does: a bare "criterion untested"
    clause reads "not tested", as the card's lead does."""
    from rabbit_brain.tui import said_lead, said_sentence

    assert repo("criterion", "add", "untested: example", "-e", "e1", "--metric", "top5", "--at-least", "0.9", "--id", "c2")[0] == 0
    c2 = object_card("c2")
    [sentence] = c2.sentences
    assert sentence == "untested: example. top5: no reading yet; criterion untested; declared; no evidence recorded yet.", sentence
    assert said_sentence(sentence, c2.title) == (
        "untested: example. top5: no reading yet; not tested; declared; no evidence recorded yet.")
    assert said_lead("criterion met: estimate 0.785 ≥ 0.7; declared before the evidence.") == (
        "rule met: estimate 0.785 ≥ 0.7; declared before the evidence.")
    assert said_sentence("other words. criterion untested.", "untested: example") == "other words. criterion untested."


def test_a_question_s_changes_say_a_freeze_as_the_spec_and_rules_locked(repo):
    """What moved under a question says a freeze in the words every view says a rule with: the spec and rules locked,
    with its reason, never "criteria"."""
    assert repo("freeze", "e1", "-m", "before the held-out runs")[0] == 0
    changed = home().briefs[0].changed
    assert any(line.endswith(": spec and rules locked · note: before the held-out runs") or line.endswith(": spec and rules locked: before the held-out runs") for line in changed), changed
    assert not any("criteri" in line for line in changed), changed
