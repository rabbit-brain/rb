"""rb ui's three panes (UX5 2.3): at 120 columns and wider the left pane (Research | Files and Your call), the story and
the evidence side by side; below 120 the story alone, the left pane a tab row in the header (t brings the catalogue,
R22) and a card over the story. Every citation a card shows takes focus and opens (the owner, Nov 11: navigable,
enter-able), on the INT8 demo, the scale record and the hosted samples where this machine holds them."""
import asyncio
import os
import shutil
from pathlib import Path

import pytest

from rbtest import AGENT_VARS

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="every record here is a git repository")

SAMPLES = Path(os.environ.get("RB_DEMO_RECORDS", "/srv/rb/demo"))
HOSTED = {"int8-cifar": SAMPLES / "record-take4" / "int8-cifar", "math-prompts": SAMPLES / "math-record-take5" / "math-prompts"}


@pytest.fixture(params=["int8_demo", "scale_record", *HOSTED])
def rec(request, tmp_path, monkeypatch):
    """A copy of the record (rb ui marks what was seen), its environment the user's own."""
    if request.param in HOSTED:
        src, env = HOSTED[request.param], {}
        if not (src / ".rb").is_dir():
            pytest.skip(f"the hosted sample {request.param} is not on this machine")
        home = tmp_path / "home"
        home.mkdir()
        (home / ".gitconfig").write_text("[user]\n\temail = ana@example.org\n\tname = ana\n[safe]\n\tdirectory = *\n", encoding="utf-8")
        env = {"HOME": str(home), "GIT_CONFIG_NOSYSTEM": "1"}
    else:
        made = request.getfixturevalue(request.param)
        src, env = made.root, made.env
    root = tmp_path / "rec"
    shutil.copytree(src, root, symlinks=True)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    for k in AGENT_VARS:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("RB_TELEMETRY", "0")
    monkeypatch.chdir(root)
    return root


def run(root, size, check, theme=None):
    from rabbit_brain.tui import HomeScreen, RBApp

    async def go():
        app = RBApp(root)
        async with app.run_test(size=size) as pilot:
            if theme:
                app.theme = theme
            for _ in range(80):
                await pilot.pause(0.1)
                if isinstance(app.screen, HomeScreen) and app.screen.query_one("#story-head").render().plain:
                    break
            await pilot.pause(0.4)
            return await check(app, pilot)
    return asyncio.run(go())


@pytest.mark.parametrize("size", [(120, 40), (100, 30), (80, 24)])
def test_three_panes_from_120_and_the_story_alone_below(rec, size):
    """At 120 columns the three panes stand side by side and the header's tab row is off; below, the story has the
    screen, the tab row names Research (t) and Files (f) with Your call's count, and the left pane and the evidence wait
    for a key. Each pane fits the screen: nothing runs off its right edge."""
    async def check(app, pilot):
        s = app.screen
        shown = {w: s.query_one(w).display and s.query_one(w).region.width > 0 for w in ("#tree-panel", "#middle", "#side-card", "#tabrow")}
        right = max(s.query_one(w).region.right for w in ("#tree-panel", "#middle", "#side-card") if shown[w])
        return shown, right, str(s.query_one("#tabrow").render()), s.query_one("#pin").render().plain

    shown, right, row, pin = run(rec, size, check)
    wide = size[0] >= 120
    assert shown == {"#tree-panel": wide, "#middle": True, "#side-card": wide, "#tabrow": not wide}, shown
    assert right <= size[0]
    if not wide:
        # the tab row (M2): Overview, Research with t, Files with f, and Your call's pin with y at its end
        assert "Overview  Research  t   Files" in row and "  f " in row and row.rstrip().endswith("  y") and pin.split("  ")[0] in row, row


def test_tab_moves_through_the_lists_then_enter_opens_the_card(rec):
    """Tab goes from list to list (the left pane's, Decisions, the chart, Other questions); the right pane previews the
    focused row and takes no keys (TUI-PARITY F3.4). Enter on a check opens its card over the story and the preview, the
    tree still there (F2.2, M4); Escape closes it and puts the focus back on the row that opened it."""
    from rabbit_brain.evidence import Cite
    from rabbit_brain.tui_card import CardView

    async def check(app, pilot):
        s = app.screen
        seen = []
        for _ in range(12):
            await pilot.press("tab")
            await pilot.pause(0.2)
            f = app.focused
            seen.append("cite" if isinstance(f, Cite) else getattr(f, "id", None))
        dl = s.query_one("#decisions-list") if s.query_one("#decisions-list").option_count else s.query_one("#chart-list")
        dl.focus()
        await pilot.pause(0.2)
        await pilot.press("enter")
        await pilot.pause(0.6)
        opened = isinstance(app.focused, CardView) and s.query_one("#tree-list").display and not s.query_one("#middle").display
        await pilot.press("escape")
        await pilot.pause(0.3)
        return seen, opened, getattr(app.focused, "id", None), bool(s.query("#card-view"))

    seen, opened, back, still = run(rec, (120, 40), check)
    lists = [x for x in seen if x != "cite"]
    assert "decisions-list" in lists or "chart-list" in lists, seen
    assert "cite" not in seen, seen
    assert opened and back in ("decisions-list", "chart-list") and not still, (opened, back, still)


def test_every_citation_rb_ui_shows_opens(rec):
    """The citation walk: from every chart row and decision of the home, its card; on each card, every citation; each
    one that names a card opens one (rb's card model, a question's checks, or the record's own card), each long form
    opens, and nothing cited leads nowhere. Two steps deep from the home."""
    from rabbit_brain.evidence import Cite, subject_of

    async def check(app, pilot):
        s = app.screen
        start = [s._object_refs[o] for o in list(s._object_refs) if o.endswith(":chart") or ":dec" in o]
        start += [r.id for r in s.home.tree][:6]
        seen, nowhere, frontier = set(), [], [(x, 0) for x in dict.fromkeys(start)]
        while frontier:
            subject, depth = frontier.pop(0)
            if subject in seen:
                continue
            seen.add(subject)
            got = s.evidence_for(subject)
            if got is None:
                nowhere.append(subject)
                continue
            if depth >= 2:
                continue
            for w in got[0]:
                if isinstance(w, Cite):
                    kind, what = w.target
                    if kind == "card":
                        frontier.append((what, depth + 1))
                    elif kind == "long":
                        try:
                            s._object_card(what)
                        except Exception:  # noqa: BLE001 - what rb ui has no card of its own for: rb show --long
                            if s.long_text(what) is None:
                                nowhere.append(f"{what} --long")
                    else:
                        assert what.strip()
        return seen, nowhere

    seen, nowhere = run(rec, (120, 40), check)
    assert len(seen) > 3 and nowhere == [], nowhere


def test_marks_take_their_ascii_forms_where_the_terminal_asks(rec, monkeypatch):
    """RB_ASCII=1: the strip, the chart and the catalogue draw # ^ % . * |-| ~ : o in place of rb's marks."""
    monkeypatch.setenv("RB_ASCII", "1")

    async def check(app, pilot):
        s = app.screen
        texts = [s.query_one("#story-head").render().plain]
        for lid in ("#chart-list", "#tree-list", "#decisions-list"):
            ol = s.query_one(lid)
            texts += [ol.get_option_at_index(i).prompt.plain for i in range(ol.option_count)]
        return "\n".join(texts)

    text = run(rec, (120, 40), check)
    assert not any(ch in text for ch in "■▲◧□●○░┆├┤‾✓✗┃"), text
    assert any(ch in text for ch in "#^%.")


def test_a_light_theme_gets_its_own_palette(rec):
    """In a light theme the screen draws in the light palette: a strip's square in its dark-on-white colour, never the
    dark palette's."""
    from rabbit_brain import tui

    async def check(app, pilot):
        head = app.screen.query_one("#story-head").render()
        styles = {str(sp.style) for sp in head.spans}
        return styles, tui.GOOD, tui.STATE_COLOUR["meets"]

    styles, good, meets = run(rec, (120, 40), check, theme="textual-light")
    assert good == tui.LIGHT["GOOD"] == meets
    assert not (styles & set(tui.DARK.values()) - {tui.DARK["DIM"]}), styles
    tui.set_palette(True)


def test_no_two_catalogue_rows_read_the_same_and_no_title_is_cut_mid_word(rec):
    """The catalogue's titles are cut only between words; where two rows' cut titles would read the same, each adds its
    id, so no two rows read the same (the UX lead, Nov 11 16:10)."""
    async def check(app, pilot):
        tree = app.screen.query_one("#tree-list")
        refs = app.screen._object_refs
        opts = [tree.get_option_at_index(i) for i in range(tree.option_count)]
        by_id = {r.id: r for r in app.screen.home.tree}
        got = [(o.prompt.plain.split("\n")[0].strip(), by_id[refs[o.id]]) for o in opts if o.id in refs]
        return [t for t, _ in got], [r for _, r in got]

    titles, rows = run(rec, (120, 40), check)
    named = {}
    for t, r in zip(titles, rows):
        named.setdefault(t, set()).add(r.id)
    assert all(len(ids) == 1 for ids in named.values()), {t: i for t, i in named.items() if len(i) > 1}      # one record may be listed twice; two never read alike
    for t, r in zip(titles, rows):
        head = t.split(" (")[0] if t.endswith(f"({r.id})") else t
        head = head[:-1] if head.endswith("…") else head
        for kind in ("┃ ", f"▸ {r.id} "):              # a question's ┃, an experiment's ▸ and id before its title (A7)
            head = head[len(kind):] if head.startswith(kind) else head
        assert r.label.startswith(head) and (head in ("", r.label) or r.label[len(head)] == " "), (t, r.label)


def test_the_chart_says_the_number_its_check_accepts(rec):
    """Ruling 1 (Nov 12 05:30): under a panel's head, the zone's edge as its number (and 0 for a difference), so a cut
    panel still says it; no key under the chart; each result says its unit where the record has one."""
    async def check(app, pilot):
        s = app.screen
        ol = s.query_one("#chart-list")
        lines = [ol.get_option_at_index(i).prompt.plain for i in range(ol.option_count)]
        return lines, s.home.overview.chart, [str(w.render()) for w in s.query("Static")]

    lines, chart, statics = run(rec, (120, 40), check)
    if not chart:
        pytest.skip("no chart on this record's story")
    first = chart[0]
    text = "\n".join(lines)
    from rabbit_brain.tui_card import claim_text, edge_label
    said = claim_text(first) if first.get("claim") and first.get("side") == "eq" else edge_label(first)
    assert said and said in text, (said, text)                      # the check's number over its mark (A8, X4)
    # no key under the chart: its lines are labelled where they stand (no glossary, show instead: Nov 12 20:35 +08)
    assert not any("░ passes" in x or "the check's number" in x for x in [text, *statics]), statics
    shown = [r for r in chart if r["est_text"] in text]
    assert shown and all(r["result_text"] in text for r in shown), (chart, text)


def test_a_top_level_question_s_row_shows_the_marks_and_counts_its_strip_shows(rec):
    """Ruling 9 (Nov 12 05:30): a top-level question's catalogue row draws its strip's marks, counts and words,
    ■ 2 passed  ▲ 2 failed, its sub-questions' checks included, on every record (MP-1112-1030 S2: math's q1 drew its
    direct checks alone)."""
    async def check(app, pilot):
        s = app.screen
        tree = s.query_one("#tree-list")
        rows = {tree.get_option_at_index(i).id: tree.get_option_at_index(i).prompt.plain for i in range(tree.option_count)}
        ov = s.home.overview
        strips = {o["id"]: o["strip"]["groups"] for o in ov.get("others") or []}
        if ov.get("question"):
            strips[ov["question"]["id"]] = ov["strip"]["groups"]
        return rows, strips

    rows, strips = run(rec, (120, 40), check)
    assert strips
    for qid, groups in strips.items():
        # each mark with its count, compressed (TUI-PARITY A7): the strip says the words beside the story's question
        want = [f"{g['mark']} {len(g['checks'])}" for g in groups if g["checks"]]
        drawn = rows[f"o:{qid}"].split("\n")[1:]
        assert [p.strip() for line in drawn for p in line.split("  ") if p.strip()] == want, (qid, drawn, want)


def test_the_check_s_number_wins_over_the_dashed_zero_where_the_bar_has_room_for_one():
    """Ruling 1 (Nov 12 05:30): where a panel's bar has no room for both labels, the check's number stands and the
    dashed 0 gives way (MP-1112-1030 S3: math's c1 at 120x40 said only 0, never from +5)."""
    from rabbit_brain.tui import ov_axis

    row = {"threshold": 1.0, "edge_text": "from +1", "difference": True, "side": "ge"}
    narrow = ov_axis(row, -1.0, 8.0, 8, 0).plain
    assert "+1" in narrow and " 0 " not in f" {narrow} ", narrow
    wide = ov_axis(row, -1.0, 8.0, 60, 0).plain
    assert "+1" in wide and " 0 " in f" {wide} " and "from" not in wide, wide          # with room, both, as numbers


def test_math_c1_says_from_plus_5_at_120x40(rec, request):
    """The same on the record it was found on: math's c1 in the question's card beside the story at 120x40."""
    if "math" not in request.node.callspec.params.get("rec", ""):
        pytest.skip("math's c1")

    async def check(app, pilot):
        from textual.widgets import Static
        return [str(w.render()) for w in app.screen.query("#side-card Static")]

    side = "\n".join(run(rec, (120, 40), check))
    # the preview draws the focused Your call row, math's c1 (TUI-PARITY F3.4), with its threshold
    assert "+5" in side and "Step by step scores at least 5 points" in " ".join(side.split()), side


@pytest.mark.parametrize("which", ["int8_demo", "int8-cifar", "math-prompts"])
def test_at_80x24_the_home_draws_a_chart_row_and_decisions_fold_to_make_room(rec, request, which):
    """At 80x24 the home draws at least one chart row of its story, as rb status does at 80x24: Decisions folds sooner
    ('+n more') to make the room (MP-1112-1030 S4: the int8 demo, math and int8-cifar drew none)."""
    if request.node.callspec.params.get("rec") != which:
        pytest.skip("each record once")

    async def check(app, pilot):
        s = app.screen
        chart, dl = s.query_one("#chart-list"), s.query_one("#decisions-list")
        rows = [chart.get_option_at_index(i) for i in range(chart.option_count)]
        decisions = [dl.get_option_at_index(i).prompt.plain for i in range(dl.option_count)]
        return [o.id for o in rows if not o.disabled and o.id.endswith(":chart")], decisions, len(s.home.overview.decisions)

    checks, decisions, n = run(rec, (80, 24), check)
    assert checks, decisions
    assert decisions and (decisions[-1].strip().startswith(f"+") or len(decisions) >= n), decisions


MARK_WORDS = {"■": "passed", "▲": "failed", "◧": "can't tell yet", "□": "no results yet"}


@pytest.mark.parametrize("size", [(120, 40), (80, 24)])
def test_no_mark_stands_without_its_word(rec, size):
    """No glossary, show instead (Nov 12 20:35 +08): every check's mark on the home's catalogue and Decisions says its
    word beside it (■ 4 passed, ▲ failed · <title>); no line of marks and what they mean stands under the chart; what
    rests on it says its counts, no ✓ or ✗; a rule written after its results says so in words, never a ‾; the question's
    card puts the checks with no results under those words; and no sentence counts the calls the badge counts
    (MP-1112-1506 S-C)."""
    import re

    async def check(app, pilot):
        s = app.screen
        opts = lambda w: [s.query_one(w).get_option_at_index(i).prompt.plain for i in range(s.query_one(w).option_count)]
        side = [str(w.render()) for w in s.query("#side-card Static")] if s.query_one("#side-card").display else []
        return {"tree": opts("#tree-list"), "decisions": opts("#decisions-list"), "chart": opts("#chart-list"),
                "statics": [str(w.render()) for w in s.query("Static") if w.display], "side": side}

    got = run(rec, size, check)
    # a list row compresses to marks and counts (TUI-PARITY A7, section 5 item 3); the words show on the item itself
    for row in got["tree"]:
        for m in re.finditer(r"[■▲◧□]", row):
            assert re.match(rf"{m.group()} \d+(  |$)", row[m.start():]), row
    # Your call's rows say each mark's state in the web's words beside it (TUI-PARITY section 5 item 1)
    calls = {"▲": ("not met", "not reproduced", "gate missed"), "◧": ("can't tell yet",), "□": ("no results yet",), "■": ("met", "reproduced")}
    for row in got["decisions"]:
        for m in re.finditer(r"[■▲◧□]", row.split("\n")[0]):
            assert any(row[m.start():].startswith(f"{m.group()} {w} ") for w in calls[m.group()]), row
    for row in got["chart"]:
        for m in re.finditer(r"[■▲◧□]", row):
            assert re.match(rf"{m.group()} {re.escape(MARK_WORDS[m.group()])}  ", row[m.start():]), row
        if "○" in row:
            assert "no range yet" in row, row
    screen = "\n".join(got["chart"] + got["statics"])
    # ‾ shows with its words on a panel's head, or beside a state mark where a panel mixes (TUI-PARITY A9)
    assert all(screen[m.end():].startswith((" written after", " ■", " ▲", " ◧", " □")) for m in re.finditer("‾", screen)), screen
    assert "✓" not in screen.replace("✓ ", "") and "✗" not in screen, screen
    assert "░ passes" not in screen and "wait on you" not in screen and "waits on you" not in screen, screen
    side = "\n".join(got["side"])
    if "□ " in side:
        assert "no results yet" in side, side


def test_a_late_rule_says_so_once_and_a_row_with_room_says_its_mark_s_word():
    """A9: where every row's rule was written after its results, the panel's head carries `‾ written after the results`
    once and no row a `‾`; where a panel mixes, the late row carries `‾` beside its state mark. A row with room says its
    mark's word before its result; X4: `┆` is 0, `░` the zone, and the zone's edge is labelled by its number."""
    from rabbit_brain.tui import ov_chart

    row = {"id": "c1", "group": "int8 − fp32, top1", "label": "c1", "short": True, "est": -0.16, "lo": -0.22, "hi": -0.09,
           "threshold": -1.0, "side": "ge", "difference": True, "state": "meets", "late": True,
           "result_text": "−0.16 points", "range_text": "−0.22…−0.09", "edge_text": "from −1"}
    wide = [t.plain for t, _ in ov_chart([row], 100)]
    assert "■ passed  −0.16 points  −0.22…−0.09" in "\n".join(wide), wide
    assert wide[0].startswith("int8 − fp32, top1   ‾ written after the results") and wide[0].rstrip().endswith("W"), wide[0]
    assert "\n".join(wide).count("‾") == 1, wide
    bar = next(x for x in "\n".join(wide).split("\n") if "●" in x)
    axis = "\n".join(wide).split("\n")[["\n".join(wide).split("\n").index(bar)][0] - 1]
    edge = axis.index("−1")
    assert axis.index("0") == bar.index("┆") and "░" in bar[edge:edge + 2] and bar[edge - 1] == " ", (axis, bar)
    narrow = "\n".join(t.plain for t, _ in ov_chart([row], 50))
    assert "■ passed  −0.16 points" in narrow, narrow
    under = [x.strip() for t, _ in ov_chart([row], 30) for x in t.plain.split("\n")]
    assert any(x.startswith("■ passed  −0.16 points") for x in under), under
    pt = dict(row, lo=None, hi=None, range_text="")
    assert "■ passed  −0.16 points  no range yet" in "\n".join(t.plain for t, _ in ov_chart([pt], 50))
    one = [t.plain for t, cid in ov_chart([row, dict(row, id="c2", label="c2", late=False)], 100)]
    assert "written after" not in one[0] and sum("‾ ■ passed" in x for x in one) == 1, one

def test_the_files_tab_says_each_mark_in_words(rec):
    """The Files tab (f): under each file, each of its marks with words, its count in them (❝ cited 3 times, ⚙ source
    of 6 settings): no key explains a mark."""
    async def check(app, pilot):
        await pilot.press("f")
        for _ in range(50):
            await pilot.pause(0.1)
            fl = app.screen.query_one("#files-list")
            if fl.option_count:
                break
        fl = app.screen.query_one("#files-list")
        return [fl.get_option_at_index(i).prompt.plain for i in range(fl.option_count)]

    rows = run(rec, (120, 40), check)
    marked = [r for r in rows if any(g in r for g in "❝⚙▣△±⊘∅")]
    assert marked, rows
    for r in marked:
        assert not any(g in r.split("\n")[0] for g in "❝⚙▣△±⊘∅") and len(r.split("\n")) > 1, r
        for line in r.split("\n")[1:]:
            glyph, _, words = line.strip().partition(" ")
            assert glyph in "❝⚙▣△±⊘∅" and len(words) >= 3 and any(c.isalpha() for c in words), r


def test_a_card_says_each_item_s_mark_in_words_and_draws_no_marks_field():
    """A card in rb ui (rb's card model): its MARKS field, a list of what each mark means, is not drawn; each item's
    mark says its word beside it instead (■ passed · c1, ○ fp32.precision · not checked against a source, a commit's ±
    with changes no commit holds); a file's own lines are drawn as they are."""
    from rabbit_brain.evidence import card_widgets, said_item

    assert said_item("■ c-pt-128") == "■ passed · c-pt-128" and said_item("□ c-pc-128") == "□ no results yet · c-pc-128"
    assert said_item("○ fp32.precision") == "○ fp32.precision · not checked against a source"
    assert said_item("? data.root") == "? data.root · not known"
    assert said_item("2982f0fae9b4 ±") == "2982f0fae9b4 ± with changes no commit holds"
    assert said_item("runntvy7u") == "runntvy7u" and said_item("+11 not counted") == "+11 not counted"
    card = {"kind": "check", "id": "c1", "title": "INT8 keeps top-1", "fields": [
        {"key": "settings", "label": "SETTINGS", "value": "", "items": [{"text": "○ fp32.precision", "opens": "rb show e1/fp32.precision"}]},
        {"key": "code", "label": "CODE", "value": "", "items": [{"text": "2982f0fae9b4 ±", "opens": ""}]},
        {"key": "lines", "label": "LINES", "value": "", "items": [{"text": "▶ 3 lr: 0.1", "opens": ""}]},
        {"key": "marks", "label": "MARKS", "value": "░ what passes  ·  ○ not checked against a source"}]}
    drawn = "\n".join(str(w._Static__content) for w in card_widgets(card, 80))       # as built, before any app draws it
    assert "MARKS" not in drawn and "what passes" not in drawn, drawn
    assert "○ fp32.precision · not checked against a source" in drawn and "2982f0fae9b4 ± with changes no commit holds" in drawn
    assert "▶ 3 lr: 0.1" in drawn
