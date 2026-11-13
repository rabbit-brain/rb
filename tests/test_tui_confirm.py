"""rb ui's `c` on a design (TUI-PARITY A11, M21): the listing full screen as rows (variants; settings as name, value and
file; checks in their statements), armed only once its last line has been on screen, then confirmed by the listing's
own command, which names its hash."""
import pytest

from test_tui_parity import HOSTED, rec, run, screen_text  # noqa: F401 - rec is a fixture

pytestmark = pytest.mark.skipif(not (HOSTED["math"] / ".rb").is_dir(), reason="the hosted samples are not here")


def test_confirm_arms_after_the_listing_is_seen(rec, monkeypatch):
    import rabbit_brain.tui as tui
    from rabbit_brain.tui_panels import ConfirmListing
    sent = []
    monkeypatch.setattr(tui, "run_settle", lambda app, root, argv, word, after: sent.append(argv))

    async def check(app, pilot):
        s = app.screen
        exp = next(r for r in s.home.ui.your_call.rows if r.kind == "design").subject
        listing = tui.ask(s.root, "design_listing", experiment=exp)
        assert s.confirm_design(exp)
        await pilot.pause(0.6)
        assert isinstance(app.screen, ConfirmListing)
        first = screen_text(app, 80, 24)
        await pilot.press("c")                                # before the end has shown: nothing is sent
        await pilot.pause(0.3)
        early = list(sent)
        for _ in range(400):                                 # j until its last line has shown, however long it is
            if getattr(app.screen, "seen_end", True):
                break
            await pilot.press("j")
        await pilot.pause(0.5)
        armed = screen_text(app, 80, 24)
        await pilot.press("c")
        await pilot.pause(0.5)
        return exp, listing, first, early, armed

    exp, listing, first, early, armed = run(rec, (80, 24), check)
    body = "\n".join(first)
    assert first[0].split() == ["design", "▸", exp], first[0]
    assert "VARIANTS" in body and "SETTINGS" in body and "[design not confirmed]" in body, body
    assert first[-1].strip() == "↓ more  esc back  ? keys  q quit", first[-1]
    assert early == [], early
    assert armed[-1].strip() == f"c confirm {exp}  esc back  ? keys  q quit", armed[-1]
    assert listing["checks"] and listing["checks"][-1]["id"] in "\n".join(armed)
    assert sent == [listing["confirm"].split()[1:]] and "@" in sent[0][1] and len(sent[0][1].split("@")[1]) == 64, sent


def test_escape_disarms_and_writes_nothing(rec, monkeypatch):
    import rabbit_brain.tui as tui
    sent = []
    monkeypatch.setattr(tui, "run_settle", lambda app, root, argv, word, after: sent.append(argv))

    async def check(app, pilot):
        s = app.screen
        exp = next(r for r in s.home.ui.your_call.rows if r.kind == "design").subject
        s.confirm_design(exp)
        await pilot.pause(0.5)
        for _ in range(400):                                 # j until its last line has shown, however long it is
            if getattr(app.screen, "seen_end", True):
                break
            await pilot.press("j")
        await pilot.press("escape")
        await pilot.pause(0.4)
        await pilot.press("c")                                 # on the home again: c opens the listing, sends nothing
        await pilot.pause(0.4)
        return type(app.screen).__name__

    run(rec, (80, 24), check)
    assert sent == [], sent


def test_listing_rows_fit_and_name_files(rec):
    from rabbit_brain.tui import ask
    from rabbit_brain.tui_panels import listing_lines

    async def check(app, pilot):
        s = app.screen
        exp = next(r for r in s.home.ui.your_call.rows if r.kind == "design").subject
        return ask(s.root, "design_listing", experiment=exp)

    listing = run(rec, (80, 24), check)
    lines = listing_lines(dict(listing), 76)
    assert all(len(t.plain) <= 76 for t in lines), [t.plain for t in lines if len(t.plain) > 76]
    sett = [s for s in listing["settings"] if s["file"]]
    assert sett and all(set(s) == {"name", "value", "file"} for s in listing["settings"])


def test_a_listing_the_screen_cannot_hold_arms_only_at_its_end(rec):
    """At 80x12 the listing is longer than the screen: `↓ more` until its last line has shown, then `c confirm`."""
    from rabbit_brain.tui_panels import ConfirmListing

    async def check(app, pilot):
        s = app.screen
        exp = next(r for r in s.home.ui.your_call.rows if r.kind == "design").subject
        s.confirm_design(exp)
        await pilot.pause(0.6)
        assert isinstance(app.screen, ConfirmListing)
        before = app.screen.seen_end
        for _ in range(400):
            if app.screen.seen_end:
                break
            await pilot.press("j")
        await pilot.pause(0.4)
        return before, app.screen.seen_end

    before, after = run(rec, (80, 12), check)
    assert before is False and after is True
