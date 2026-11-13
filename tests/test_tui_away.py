"""rb ui's keys that write (y, n, c, d) act only where it started, live (AGENTS.md: "y, n, c and d act only where it
started"): at another place and at a past version no screen offers them and none sends a write, and a design's listing
is never confirmed there. A past version asks nothing either: no Next step and no Continue prompt, on the home, a
preview or a card, as the web draws none there (guarantee 38)."""
import asyncio
import sys
from pathlib import Path

import pytest

from rbtest import PLACES_AGENT
from test_tui import HAS_GIT, _choose, placed, until  # noqa: F401 - placed is a fixture

pytestmark = [pytest.mark.skipif(not HAS_GIT, reason="the record is played in a git repository"),
              pytest.mark.skipif(sys.platform == "win32", reason="the lock a past version's folder takes is a POSIX lock")]


@pytest.fixture
def waiting(placed):
    """The places record with e1's design written in part by a session, in the main checkout and in the sibling's own
    record, and a question nothing is filed under and a resource with no download link (each with a Next step live),
    committed: the past version shown is that commit, where all of it waits too."""
    rec = placed
    for cwd in (rec.root, rec.tree["exp-c"]):
        rec.rb("spec", "set", "e1", "optim.lr", "0.1", "--no-verify", cwd=cwd, who=PLACES_AGENT)
    rec.rb("question", "add", "Does INT8 cost accuracy on the held-out set?", "--id", "q-int8")
    rec.rb("resource", "add", "holdout-v2", "--type", "dataset", "--name", "held-out images, v2")
    rec.git("add", ".rb")
    rec.git("commit", "-qm", "a design a session wrote, a question, a resource")
    rec.head = rec.git("rev-parse", "HEAD")
    return rec


def _record(monkeypatch):
    import rabbit_brain.tui as tui
    sent: list = []
    monkeypatch.setattr(tui, "run_settle", lambda app, root, argv, word, after: sent.append((str(root), list(argv))))
    monkeypatch.setattr(tui, "ask", _no_dismiss(tui.ask, sent))
    return sent


def _no_dismiss(ask, sent):
    """rb ui's other write, a found item left out, goes through ask(dismiss): recorded here, never made."""
    def wrapped(root, op, **kw):
        if op == "dismiss":
            sent.append((str(root), ["dismiss", kw.get("key")]))
            return None
        return ask(root, op, **kw)
    return wrapped


async def _go_away(pilot, app, rec, where: str, monkeypatch, folder: Path):
    """Show `where`: the sibling worktree's own record (place), or the commit `rec.head` (past)."""
    from rabbit_brain.tui import HomeScreen
    home = app.screen
    if where == "place":
        await _choose(pilot, app, "#where", "exp-c · ")
        await until(pilot, lambda: home.place is not None and app.screen is home)
    else:
        monkeypatch.setenv("RB_VIEWS", folder.name)
        await _choose(pilot, app, "#versions", rec.head[:12])
        await until(pilot, lambda: home.at is not None and app.screen is home)
    assert isinstance(app.screen, HomeScreen) and not home.at_start, (home.place, home.at)
    return home


def _footer(screen) -> str:
    return str(screen.query_one("#card-footer").render())


@pytest.mark.parametrize("where", ["place", "past"])
def test_no_key_writes_away_from_where_rb_ui_started(waiting, monkeypatch, where):
    """At another place and at a past version: the home's y, n, c and d are unbound; e1's card offers no c (its footer
    names none, its Design tab says the design is not confirmed and acts not), and c there, the Design row's ⏎, the
    home's confirm_design and a decide send nothing; nor does a found item's y or n."""
    import fcntl
    from rabbit_brain import tui
    from rabbit_brain.tui import CheckCardScreen, HomeScreen, RBApp, card_act
    from rabbit_brain.tui_card import CardView
    rec = waiting
    sent = _record(monkeypatch)
    folder = rec.common / "rb" / "views" / f"4250-{where}"
    folder.mkdir(parents=True)
    got: dict = {}

    async def drive():
        app = RBApp(rec.root)
        async with app.run_test(size=(100, 30)) as pilot:
            await until(pilot, lambda: isinstance(app.screen, HomeScreen) and app.screen.query_one("#tree-list").option_count)
            with open(folder / ".lock", "w") as f:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                home = await _go_away(pilot, app, rec, where, monkeypatch, folder)
                got["home_keys"] = [home.check_action(a, ()) for a in ("yes", "no", "confirm", "decide")]
                home._paint_footer()
                got["home_footer"] = str(home.query_one("#footer").render())
                for key in ("c", "d", "n"):
                    await pilot.press(key)
                    await pilot.pause(0.2)
                data = home.card_data("e1")
                got["waits_here"] = any(p.kind == "design" and p.id.split("@")[0] == "e1" for p in getattr(home, "_pending", []))
                got["design_waits"] = data.card.get("design_waits")
                got["chips"] = data.chips.plain
                design = data.bodies(80)["Design"] if callable(data.bodies) else data.bodies["Design"]
                got["design_rows"] = [(t.plain, act) for t, act in design if "design" in t.plain]
                got["confirm_design"] = home.confirm_design("e1")
                app.push_screen(CheckCardScreen(home, data))
                await pilot.pause(0.5)
                card = app.screen
                got["card_keys"] = [card.check_action(a, ()) for a in ("confirm", "decide")]
                got["footer"] = _footer(card)
                await pilot.press("c")
                await pilot.pause(0.5)
                got["after_c"] = type(app.screen).__name__
                for _ in range(60):
                    await pilot.press("j")
                await pilot.press("c")
                await pilot.pause(0.3)
                view = card.query_one(CardView)
                card_act(home, view, ("confirm", "e1"))        # the Design row's ⏎, however it is reached
                card_act(home, view, ("decide", "c1"))
                home.decide_on("c1", "int8 holds top1")
                home.open_found("anything")
                await pilot.pause(0.5)
                got["screen"] = type(app.screen).__name__

    asyncio.run(drive())
    assert got["home_keys"] == [False, False, False, False], got["home_keys"]
    assert "confirm" not in got["home_footer"] and "decide" not in got["home_footer"], got["home_footer"]
    assert got["waits_here"], "e1's design waits at the place shown, so the test reaches the guarded paths"
    assert got["design_waits"] is True and "design not confirmed" in got["chips"] and "›" not in got["chips"].split("design not confirmed")[1][:2], got["chips"]
    assert got["design_rows"] and all(act is None and "›" not in text for text, act in got["design_rows"]), got["design_rows"]
    assert got["confirm_design"] is False
    assert got["card_keys"] == [False, False]
    assert " c " not in f" {got['footer']} " and "confirm" not in got["footer"] and "decide" not in got["footer"], got["footer"]
    assert got["after_c"] == "CheckCardScreen" and got["screen"] == "CheckCardScreen", got
    assert sent == [], sent


def test_c_confirms_the_design_where_rb_ui_started_by_its_listings_hash(waiting, monkeypatch):
    """Where it started, live: e1's card offers c, c shows the listing, and once its last line has shown, c runs the
    listing's own rb confirm e1@<hash> in the checkout the user is in."""
    from rabbit_brain.tui import CheckCardScreen, HomeScreen, RBApp, ask
    from rabbit_brain.tui_panels import ConfirmListing
    rec = waiting
    sent = _record(monkeypatch)
    got: dict = {}

    async def drive():
        app = RBApp(rec.root)
        async with app.run_test(size=(100, 30)) as pilot:
            await until(pilot, lambda: isinstance(app.screen, HomeScreen) and app.screen.query_one("#tree-list").option_count)
            home = app.screen
            got["listing"] = ask(home.root, "design_listing", experiment="e1")
            data = home.card_data("e1")
            got["chips"] = data.chips.plain
            app.push_screen(CheckCardScreen(home, data))
            await pilot.pause(0.5)
            got["footer"] = _footer(app.screen)
            await pilot.press("c")
            await pilot.pause(0.6)
            got["listing_shown"] = isinstance(app.screen, ConfirmListing)
            for _ in range(80):
                await pilot.press("j")
            await pilot.pause(0.5)
            await pilot.press("c")
            await pilot.pause(0.5)

    asyncio.run(drive())
    assert "design not confirmed ›" in got["chips"], got["chips"]
    assert "c confirm" in got["footer"], got["footer"]
    assert got["listing_shown"]
    confirm = got["listing"]["confirm"]
    assert sent == [(str(rec.root), confirm.split()[1:])], sent
    assert sent[0][1][1].startswith("e1@") and len(sent[0][1][1].split("@")[1]) == 64, sent


def test_a_past_version_draws_no_next_step(waiting, monkeypatch):
    """At a past version rb's bridge gives no Next step and no Continue prompt, so rb ui draws none: not on the
    question's card or preview, the resource's card or preview, nor the catalogue's footer (no p to copy one). Live,
    each is there."""
    import fcntl
    from rabbit_brain.tui import HomeScreen, RBApp
    rec = waiting
    folder = rec.common / "rb" / "views" / "4251-next"
    folder.mkdir(parents=True)
    got: dict = {}

    def draws(home) -> dict:
        ui = home.home.get("ui") or {}
        q, r = home.question_card("q-int8"), home.resource_card("holdout-v2")
        qp = home.evidence_for("q-int8")
        rp = home.evidence_for("holdout-v2")
        said = lambda ws: "\n".join(str(w.render()) for w in (ws or ([], ""))[0])   # noqa: E731
        return {"next_steps": dict(ui.get("next_steps") or {}), "continue": ui.get("continue"),
                "q_card": q.prompt, "r_card": r.prompt, "q_preview": said(qp), "r_preview": said(rp)}

    async def drive():
        app = RBApp(rec.root)
        async with app.run_test(size=(160, 50)) as pilot:
            await until(pilot, lambda: isinstance(app.screen, HomeScreen) and app.screen.query_one("#tree-list").option_count)
            home = app.screen
            got["live"] = draws(home)
            with open(folder / ".lock", "w") as f:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                home = await _go_away(pilot, app, rec, "past", monkeypatch, folder)
                got["past"] = draws(home)
                # the catalogue's footer below 120 columns: a row with a Next step offers p to copy it
                home.set_class(True, "-narrow")
                tree = home.query_one("#tree-list")
                tree.focus()
                rows = [tree.get_option_at_index(i).id for i in range(tree.option_count)]
                footers = set()
                for i, _ in enumerate(rows):
                    tree.highlighted = i
                    footers.add(home._footer_context())
                got["past_footers"] = footers

    asyncio.run(drive())
    live, past = got["live"], got["past"]
    assert {"q-int8", "holdout-v2"} <= set(live["next_steps"]), live["next_steps"]
    # the story's own question previews its Continue this research box live, any other its Next step
    assert live["q_card"] and live["r_card"] and "Next step" in live["r_preview"], live
    assert "Next step" in live["q_preview"] or "Continue this research" in live["q_preview"], live["q_preview"]
    assert past["next_steps"] == {} and not past["continue"], past
    assert not past["q_card"] and not past["r_card"], past
    assert not any(w in past[k] for w in ("Next step", "Continue this research", "⧉") for k in ("q_preview", "r_preview")), past
    assert "catalogue.next" not in got["past_footers"], got["past_footers"]


def test_a_found_items_card_unbinds_y_and_n_away(monkeypatch, tmp_path):
    """A found item's card (y records it, n leaves it out) keeps both only where rb ui started: shown with a place or a
    past version, each is unbound and its chips are not drawn."""
    import rabbit_brain.tui as tui
    monkeypatch.setattr(tui, "found_card", lambda root, rid, place=None, at=None: {"request_id": rid})
    keys = lambda card: [card.check_action(a, ()) for a in ("yes", "no")]   # noqa: E731
    assert keys(tui.CardScreen(tmp_path, "r1")) == [True, True]
    assert keys(tui.CardScreen(tmp_path, "r1", place="p_1")) == [False, False]
    assert keys(tui.CardScreen(tmp_path, "r1", at="4f1c2ab")) == [False, False]


def test_a_past_version_offers_nothing_to_copy(waiting, monkeypatch):
    """At a past version (guarantee 38: no command to copy) every card's footer names no p, the resource with no
    download link names no g, no footer calls a key "copy link", the crumb draws no ⧉, and p on each card and on the
    home copies nothing; live, the same cards offer p and it copies `rb show <id>` (MP-1113-1727 F3, the reviewer's
    p2.py)."""
    import fcntl
    from rabbit_brain.tui import CheckCardScreen, HomeScreen, RBApp, card_act
    from rabbit_brain.tui_card import CardView
    rec = waiting
    copied: list = []
    monkeypatch.setattr(HomeScreen, "copy_text", lambda self, text: (copied.append(text), "copied")[1])
    folder = rec.common / "rb" / "views" / "4252-copy"
    folder.mkdir(parents=True)
    got: dict = {"live": {}, "past": {}}

    async def cards(pilot, app, home, into: dict) -> None:
        for subject in ("c1", "e1", "q-int8", "holdout-v2"):
            data = home.card_data(subject) or home.resource_card(subject)
            app.push_screen(CheckCardScreen(home, data))
            await pilot.pause(0.5)
            card = app.screen
            view = card.query_one(CardView)
            before = len(copied)
            await pilot.press("p")
            await pilot.pause(0.2)
            card_act(home, view, view._items[0][1])             # the crumb's ⧉, however it is reached
            await pilot.pause(0.2)
            into[subject] = {"footer": _footer(card), "crumb": view.render().plain.splitlines()[0],
                             "copied": copied[before:], "kind": data.kind, "get_it": data.get_it}
            while not isinstance(app.screen, HomeScreen):
                app.pop_screen()
                await pilot.pause(0.2)

    async def drive():
        app = RBApp(rec.root)
        async with app.run_test(size=(100, 40)) as pilot:
            await until(pilot, lambda: isinstance(app.screen, HomeScreen) and app.screen.query_one("#tree-list").option_count)
            await cards(pilot, app, app.screen, got["live"])
            with open(folder / ".lock", "w") as f:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                home = await _go_away(pilot, app, rec, "past", monkeypatch, folder)
                await cards(pilot, app, home, got["past"])
                before = len(copied)
                for fid in ("#tree-list", "#decisions-list"):
                    lst = home.query(fid)
                    if lst:
                        lst.first().focus()
                        await pilot.pause(0.2)
                        home._paint_footer()
                        got["past"][fid] = str(home.query_one("#footer").render())
                        await pilot.press("p")
                        await pilot.pause(0.2)
                got["home_copied"] = copied[before:]
                got["home_copy_key"] = home.check_action("copy", ())

    asyncio.run(drive())
    live, past = got["live"], got["past"]
    assert live["c1"]["copied"] and live["c1"]["copied"][0] == "rb show c1", live["c1"]
    assert all(" p " in f" {live[s]['footer']} " for s in ("c1", "e1")), live
    assert "g get it" not in live["holdout-v2"]["footer"] and live["holdout-v2"]["get_it"] is None, live["holdout-v2"]
    for s in ("c1", "e1", "q-int8", "holdout-v2"):
        assert past[s]["copied"] == [], (s, past[s])
        assert " p " not in f" {past[s]['footer']} " and "copy" not in past[s]["footer"], (s, past[s]["footer"])
        assert "⧉" not in past[s]["crumb"], (s, past[s]["crumb"])
        assert "g get it" not in past[s]["footer"], (s, past[s]["footer"])
    for s in live:
        assert "copy link" not in live[s]["footer"], (s, live[s]["footer"])
    for fid in ("#tree-list", "#decisions-list"):
        if fid in past:
            assert "copy" not in past[fid] and " p " not in f" {past[fid]} ", (fid, past[fid])
    assert got["home_copied"] == [] and got["home_copy_key"] is False, got


@pytest.mark.parametrize("keys", [("shift+tab", "enter"), ("tab", "tab", "enter"), ("tab", "enter")])
def test_a_card_opened_while_one_is_open_takes_its_place(waiting, monkeypatch, keys):
    """At 160 columns ⏎ on a row opens its card beside the tree; moving off it and ⏎ on another row opens that one in
    its place, never a second card with the same id (DuplicateIds: MP-1113-1727 review, finding 2)."""
    from textual.widgets import OptionList
    from rabbit_brain.tui import HomeScreen, RBApp
    rec = waiting
    _record(monkeypatch)
    got: dict = {}

    async def drive():
        app = RBApp(rec.root)
        async with app.run_test(size=(160, 50)) as pilot:
            await until(pilot, lambda: isinstance(app.screen, HomeScreen) and app.screen.query_one("#tree-list").option_count)
            home = app.screen
            tree = home.query_one("#tree-list", OptionList)
            tree.focus()
            tree.highlighted = next(i for i in range(tree.option_count) if not tree.get_option_at_index(i).disabled
                                    and (tree.get_option_at_index(i).id or "").startswith("o:"))
            await pilot.pause(0.2)
            await pilot.press("enter")
            await pilot.pause(0.5)
            got["first"] = len(home.query("#card-view"))
            for k in keys:
                await pilot.press(k)
                await pilot.pause(0.3)
            await pilot.pause(0.5)
            got["after"] = len(home.query("#card-view"))

    asyncio.run(drive())
    assert got["first"] == 1 and got["after"] <= 1, got
