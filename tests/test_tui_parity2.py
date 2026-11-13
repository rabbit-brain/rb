"""rb ui's parity pins, part 2 (TUI-PARITY section 4): focus and movement, the preview following focus, counts that
open their lists (N5), the setting marks (N2), nothing past its pane (G4), one meaning per glyph (G7, in part), the
same numbers as the page (G6, the math sample) and OBSERVED as one line (X10). On the hosted samples, through the pilot."""
import io

import pytest

from test_tui_parity import HOSTED, name_of, rec, run, screen_text  # noqa: F401 - rec is a fixture

pytestmark = pytest.mark.skipif(not any((p / ".rb").is_dir() for p in HOSTED.values()), reason="no hosted sample here")


def _only(rec, name):
    if name_of(rec) != name:
        pytest.skip(f"{name}'s figures")


def test_focus_and_movement(rec):
    """Section 0 item 3: focus starts on the first Your call row; `j` `k` equal `↓` `↑`; `home` and `end` reach the
    first and last row; `tab` moves on in reading order and `shift-tab` comes back."""
    async def check(app, pilot):
        s = app.screen
        dl = s.query_one("#decisions-list")
        start = (s.focused is dl, dl.highlighted)
        await pilot.press("j")
        await pilot.pause(0.2)
        after_j = dl.highlighted
        await pilot.press("k")
        await pilot.pause(0.2)
        after_k = dl.highlighted
        await pilot.press("down")
        await pilot.pause(0.2)
        after_down = dl.highlighted
        await pilot.press("end")
        await pilot.pause(0.2)
        last = dl.highlighted
        await pilot.press("home")
        await pilot.pause(0.2)
        first = dl.highlighted
        await pilot.press("tab")
        await pilot.pause(0.2)
        moved = s.focused
        await pilot.press("shift+tab")
        await pilot.pause(0.2)
        return start, after_j, after_k, after_down, last, first, moved, s.focused, dl, dl.option_count

    start, j, k, down, last, first, moved, back, dl, n = run(rec, (80, 24), check)
    first_row = next(i for i in range(dl.option_count)
                     if not dl.get_option_at_index(i).disabled and not (dl.get_option_at_index(i).id or "").startswith("yc-"))
    assert start == (True, first_row), start
    assert j == down == first_row + 1 and k == first_row, (j, k, down)
    assert last is not None and last > j and first in (0, first_row), (last, first)     # home: the head's chips, or the first row
    assert moved is not dl and back is dl, (moved, back)


def test_preview_follows_focus(rec):
    """F3.4: at 120x40 each move in the tree redraws the right pane for that row; q1, the story's question, previews its
    Continue box, never the story's title, strip or chart; at 80x24 there is no preview."""
    _only(rec, "math")

    async def check(app, pilot):
        s = app.screen
        tree = s.query_one("#tree-list")
        tree.focus()
        ids = [tree.get_option_at_index(i).id for i in range(tree.option_count)]
        seen = []
        for subject in ("e1", "q3", "q1"):
            tree.highlighted = ids.index(next(o for o, r in s._object_refs.items() if r == subject))
            await pilot.pause(0.5)
            seen.append("\n".join(line[84:] for line in screen_text(app, 120, 40)))
        return seen

    e1, q3, q1 = run(rec, (120, 40), check)
    assert "Continue this research" in q1 and "[Open]" not in q1 and "■■" not in q1, q1
    assert q1 != e1 != q3 and "Next step" in q3 and "[Not tested yet]" in q3, (e1, q3)

    async def narrow(app, pilot):
        return app.screen.query_one("#side-card").display

    assert not run(rec, (80, 24), narrow)


def test_counts_open_their_lists(rec):
    """N5: the strip's groups and the rests-on counts take focus; `⏎` on a strip group opens a list of that many checks,
    each a row with its mark and word that opens its card; `⏎` on `tries` opens the runs."""
    _only(rec, "math")

    async def check(app, pilot):
        from rabbit_brain.tui_card import CardView
        s = app.screen
        head = s.query_one("#story-head")
        groups = [(a, b, act) for a, b, act in head.spans]
        out = []
        for n, (_a, _b, act) in enumerate(groups):
            head.focus()
            head.at = n
            await pilot.press("enter")
            await pilot.pause(0.5)
            view = app.screen.query_one(CardView)
            out.append((len(act[1]), [line for line in view.render().plain.splitlines() if line.rstrip().endswith("›")]))
            await pilot.press("escape")
            await pilot.pause(0.3)
        rests = s.query_one("#story-rests")
        tries = next(i for i, (_a, _b, act) in enumerate(rests.spans) if act[2] == "tries")
        rests.focus()
        rests.at = tries
        await pilot.press("enter")
        await pilot.pause(0.5)
        view = app.screen.query_one(CardView)
        return out, view.data.subject, view.data.tabs[view.tab]

    groups, subject, tab = run(rec, (80, 24), check)
    assert [n for n, _rows in groups] == [2, 2, 1], groups
    assert all(len(rows) == n for n, rows in groups), groups
    assert (subject, tab) == ("e1", "Runs")


def test_setting_marks(rec):
    """N2: on e1's card the unchecked setting reads `? population.unit · not checked`; its focus shows the hover's words
    as one dim line under it."""
    _only(rec, "math")

    async def check(app, pilot):
        from rabbit_brain.tui_card import CardView
        assert app.screen.open_card("e1")
        await pilot.pause(0.5)
        view = app.screen.query_one(CardView)
        before = view.render().plain
        for _ in range(5):
            await pilot.press("down")
        await pilot.pause(0.3)
        return before, view.render().plain

    before, focused = run(rec, (80, 24), check)
    line = next(x for x in before.splitlines() if "population.unit" in x)
    assert line.strip().startswith("? population.unit · not checked"), line
    under = focused.splitlines()[[i for i, x in enumerate(focused.splitlines()) if "population.unit" in x][0] + 1]
    assert "not checked against a source" in under, focused


@pytest.mark.parametrize("size", [(60, 20), (80, 24), (100, 30), (120, 40), (160, 50)])
def test_nothing_runs_past_its_pane(rec, size):
    """G4: no line is wider than the screen, with and without a card open; a box's right edge stays in its column."""
    async def check(app, pilot):
        shots = [screen_text(app, *size)]
        if app.screen.open_card("q1"):
            await pilot.pause(0.5)
            shots.append(screen_text(app, *size))
        return shots

    from rich.cells import cell_len
    for shot in run(rec, size, check):
        wide = [line for line in shot if cell_len(line) > size[0]]
        assert not wide, wide
        assert len(shot) <= size[1]


def test_one_meaning_per_glyph(rec):
    """G7, in part: `◆` only on a check's number, never a dot or a bar's edge; `▌` once, on the focused row; `│` never
    inside a figure."""
    async def check(app, pilot):
        return screen_text(app, 120, 40), screen_text(app, 120, 40)

    shot, _ = run(rec, (120, 40), check)
    middle = [line[33:84] for line in shot[2:-2]]
    assert sum(line.count("▌") for line in middle) == 1, middle
    figures = [line for line in middle if "├" in line or "◆" in line or "┆" in line]
    assert all("│" not in line.strip("│ ")[:-1] for line in figures), figures


def test_same_numbers_as_the_page(rec):
    """G6 (math): rb ui's 3.6, +3.60, +0.62, +6.58, the strip's 2, 2 and 1, 9 of 9, 0 of 5 and 2 of 4 are the view
    model's (state.json's), drawn as given."""
    _only(rec, "math")

    async def check(app, pilot):
        h = app.screen.home
        return screen_text(app, 120, 40), h

    shot, h = run(rec, (120, 40), check)
    text = "\n".join(shot)
    ov = h.get("overview") or {}
    c1 = next(r for r in ov["chart"] if r["id"] == "c1")
    for words in (c1["result_text"], *c1["range_text"].split("…")):
        assert words in text, words
    for g in ov["strip"]["groups"]:
        assert f"{len(g['checks'])} {g['label']}" in text
    for r in ov["rests"]:
        n_of = r["text"].split(" ", 1)[-1].replace("✓ ", "").replace("✗ ", "")
        assert n_of.split()[-1] in text
    assert "9 of 9 runs" in text and "0 of 5" in text and "2 of 4" in text


def test_observed_is_one_line(rec):
    """X10: OBSERVED is one focusable line over the footer at 120x40 and on the row above it at 80x24; `⏎` opens its
    list and `esc` goes back."""
    for size in ((120, 40), (80, 24)):
        async def check(app, pilot, size=size):
            from rabbit_brain.tui import ObservedLine
            s = app.screen
            lines = list(s.query(ObservedLine))
            if not lines or not lines[0].display:
                return None
            obs = lines[0]
            row = obs.region.y
            obs.focus()
            await pilot.press("enter")
            await pilot.pause(0.5)
            opened = app.screen is not s
            await pilot.press("escape")
            await pilot.pause(0.3)
            return row, obs.region.height, opened, app.screen is s

        got = run(rec, size, check)
        if got is None:
            continue
        row, height, opened, back = got
        assert height == 1 and row == size[1] - 2 and opened and back, (size, got)


def test_words_come_from_the_bridge(rec):
    """G5: each mark's word on the screen is the bridge's (home.ui.states, rb status's strip words); rb ui's sources
    hold no table of their own; the Your call rows' words are the rows' own fields."""
    import re
    from pathlib import Path

    import rabbit_brain

    async def check(app, pilot):
        h = app.screen.home
        return (h.get("ui") or {}).get("states"), ((h.get("ui") or {}).get("your_call") or {}).get("rows"), screen_text(app, 80, 24)

    states, rows, shot = run(rec, (80, 24), check)
    assert [s["mark"] for s in states] == ["■", "▲", "◧", "□"] and all(s["word"] for s in states), states
    from rabbit_brain.tui_card import STATE_WORD
    assert STATE_WORD == {s["mark"]: s["word"] for s in states}
    text = "\n".join(shot)
    for r in rows[:3]:
        if r.get("word"):
            assert r["word"] in text, (r, text)
    pkg = Path(rabbit_brain.__file__).parent
    for name in ("tui.py", "tui_card.py", "tui_parts.py", "tui_panels.py"):
        src = (pkg / name).read_text(encoding="utf-8")
        assert not re.search(r'"[■▲◧□]":\s*"(passed|failed|can\'t tell yet|no results yet)"', src), name


def test_rows_are_one_line(rec):
    """F3.3: each Your call row is one line with one status word and no `[decide]` or `[confirm]` tag; below 120
    columns a check's row carries a mini bar of at most 10 cells (`◆` the check's number); in the 50-column middle at
    120x40 none does."""
    async def rows(app, pilot, size):
        dl = app.screen.query_one("#decisions-list")
        return [dl.get_option_at_index(i).prompt.plain for i in range(dl.option_count)
                if not (dl.get_option_at_index(i).id or "").startswith(("yc-", "decisions-more"))], screen_text(app, *size)

    narrow, shot = run(rec, (80, 24), lambda app, pilot: rows(app, pilot, (80, 24)))
    wide, _ = run(rec, (120, 40), lambda app, pilot: rows(app, pilot, (120, 40)))
    assert narrow and all("\n" not in r for r in narrow + wide), (narrow, wide)
    assert not any(tag in "\n".join(shot) for tag in ("[decide]", "[confirm]"))
    bars = [r for r in narrow if "◆" in r or "●" in r or "○" in r]
    assert bars, narrow
    for r in bars:
        cells = r.rstrip(" ›").rsplit("  ", 1)[-1].strip()
        assert 1 <= len(cells) <= 10, (r, cells)
    assert not any(ch in r for r in wide for ch in "◆●○├┤"), wide
