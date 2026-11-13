"""rb ui's listing of a waiting design is rb confirm's own (AGENTS.md: "rb confirm lists each waiting design with every
keyed field"; "nothing the user confirms hides from them"): every line `rb confirm <exp>` prints, each rule's metric,
threshold, role and question link, the variants' roles, a long source path and a long value, whole at any size; and
every way `c` reaches a design (beside the list, the full screen ⏎ opens below the wide layout, a card) goes through
that listing, so nothing is sent before its last line has been on screen."""
import asyncio
import subprocess

import pytest

from rbtest import AGENT_VARS, Recorder, as_the_user, command
from test_tui import HAS_GIT, until

pytestmark = pytest.mark.skipif(not HAS_GIT, reason="the record is played in a git repository")

SESSION = {"CLAUDECODE": "1", "CLAUDE_CODE_SESSION_ID": "sess-listing-7a1c"}
LONG_PATH = "configs/imagenet/resnet50/quantization/post-training/calibration-sweeps/held-out-v2/eval-int8-per-channel.yaml"
LONG_VALUE = ("calibrate on 512 held-out images drawn from the ImageNet validation split, stratified by class, with the "
              "per-channel scales frozen after the first pass")


@pytest.fixture
def listed(tmp_path, monkeypatch):
    """A record whose e1 design a session wrote: its question, purpose, variants with their roles, a setting read from
    a config at a long path with a long value, the variants' own values, and a secondary rule on change.top1 with its
    threshold and its question link; committed. The user opens rb ui in it."""
    rec = Recorder(tmp_path, "p")
    rec.start()
    rec.write("train.py", "lr = 0.1\n")
    rec.write(LONG_PATH, f"eval:\n  calibration_note: {LONG_VALUE}\n")
    rec.git("add", "-A")
    rec.git("commit", "-qm", "start")
    rec.rb("init", "INT8 ResNet-50 on ImageNet", "--no-agents")
    rec.rb("question", "add", "Does INT8 cost accuracy on the held-out set?", "--id", "q-int8")
    rec.rb("metric", "add", "top1", "--maximize")
    rec.rb("experiment", "add", "int8 against fp32", "--id", "e1", "-q", "q-int8", "--baseline", "fp32", "--candidate", "int8",
           "--varies", "precision", "--purpose", "measure what INT8 costs on the held-out set", who=SESSION)
    rec.rb("spec", "set", "e1", "eval.calibration_note", "--source", f"{LONG_PATH}#eval.calibration_note", who=SESSION)
    rec.rb("spec", "set", "e1", "int8.precision", "int8", "--no-verify", who=SESSION)
    rec.rb("spec", "set", "e1", "fp32.precision", "fp32", "--no-verify", who=SESSION)
    rec.rb("spec", "set", "e1", "eval.note", "held-out  v2, as  given", "--no-verify", who=SESSION)   # runs of spaces
    rec.rb("criterion", "add", "int8 holds top1", "-e", "e1", "-q", "q-int8", "--id", "c1", "--metric", "change.top1",
           "--at-least", "-0.01", "--role", "secondary", "--when-met", "INT8 keeps top1 on the held-out set", who=SESSION)
    rec.git("add", "-A")
    rec.git("commit", "-qm", "the record")
    as_the_user(monkeypatch)
    for k, v in rec.env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.chdir(rec.root)
    return rec


def confirm_lines(rec, exp: str) -> list:
    """What `rb confirm <exp>` prints for it in the user's own terminal: each line under the design's own, stripped."""
    env = {k: v for k, v in rec.env.items() if k not in AGENT_VARS}
    out = subprocess.run([*command(), "confirm", exp], cwd=rec.root, env=env, capture_output=True, encoding="utf-8",
                         stdin=subprocess.DEVNULL).stdout.splitlines()
    head = next(i for i, line in enumerate(out) if line.startswith(f"  {exp}: "))
    return [line.strip() for line in out[head + 1:] if line.startswith("    ")]


def _record(monkeypatch):
    import rabbit_brain.tui as tui
    sent: list = []
    monkeypatch.setattr(tui, "run_settle", lambda app, root, argv, word, after: sent.append(list(argv)))
    return sent


@pytest.mark.parametrize("size", [(120, 40), (80, 24), (60, 20)])
def test_the_listing_holds_every_line_rb_confirm_prints_whole(listed, monkeypatch, size):
    """At 120x40, 80x24 and 60x20 the listing c opens holds every line rb confirm e1 prints, in its order and whole:
    the rule's threshold, its role and its question link, the variants' roles, the long source path and the long value;
    no row is wider than the listing, and none is cut with an ellipsis."""
    from rabbit_brain.tui import HomeScreen, RBApp
    from rabbit_brain.tui_panels import ConfirmListing
    rec = listed
    want = confirm_lines(rec, "e1")
    assert any("criterion c1 (secondary): change.top1 ≥ -0.01 · for q-int8" in w for w in want), want
    assert any(w.startswith("variants: fp32 (baseline), int8 (candidate)") for w in want), want
    assert any(LONG_PATH in w and LONG_VALUE in w for w in want), want
    assert any("held-out  v2, as  given" in w for w in want), want
    sent = _record(monkeypatch)
    got: dict = {}

    async def drive():
        app = RBApp(rec.root)
        async with app.run_test(size=size) as pilot:
            await until(pilot, lambda: isinstance(app.screen, HomeScreen) and app.screen.query_one("#tree-list").option_count)
            assert app.screen.confirm_design("e1")
            await until(pilot, lambda: isinstance(app.screen, ConfirmListing))
            await pilot.pause(0.3)
            body = app.screen.query_one("#body")
            got["rows"] = [line.plain for line in body.render().split()]
            got["room"] = body.content_region.width
            await pilot.press("c")                         # before its end has shown, at a size where it scrolls
            await pilot.pause(0.3)
            got["early"] = list(sent)

    asyncio.run(drive())
    rows = got["rows"]
    assert all(len(r) <= got["room"] for r in rows), (got["room"], [r for r in rows if len(r) > got["room"]])
    assert not any("…" in r for r in rows), rows
    at = 0
    for w in want:                                          # each line, whole and as it is, after the one before it
        start = next(i for i in range(at, len(rows)) if rows[i] == "  " + w[:len(rows[i]) - 2])
        end = start + 1
        while end < len(rows) and rows[end].startswith("    "):
            end += 1
        assert _joined([rows[start][2:]] + [r[4:] for r in rows[start + 1:end]], w) == w, (w, rows[start:end])
        at = end
    if size != (120, 40):
        assert got["early"] == [], got["early"]


@pytest.mark.parametrize("size", [(100, 40), (80, 24)])
def test_enter_on_a_waiting_design_then_c_shows_its_listing_first(listed, monkeypatch, size):
    """Below the wide layout, ⏎ on a waiting design's row opens it full screen (PendingScreen); c there shows the
    design's listing and sends nothing; only c after the listing's last line has been on screen sends rb confirm
    e1@<hash>, the listing's own command (MP-1113-1727 F2, the reviewer's path)."""
    from textual.widgets import OptionList
    from rabbit_brain.tui import HomeScreen, PendingScreen, RBApp, ask
    from rabbit_brain.tui_panels import ConfirmListing
    rec = listed
    sent = _record(monkeypatch)
    got: dict = {}

    async def drive():
        app = RBApp(rec.root)
        async with app.run_test(size=size) as pilot:
            await until(pilot, lambda: isinstance(app.screen, HomeScreen) and app.screen.query_one("#tree-list").option_count)
            home = app.screen
            got["listing"] = ask(home.root, "design_listing", experiment="e1")
            lst = next(o for o in home.query(OptionList)
                       if any((o.get_option_at_index(i).id or "").startswith("p:e1@") for i in range(o.option_count)))
            lst.focus()
            lst.highlighted = next(i for i in range(lst.option_count) if (lst.get_option_at_index(i).id or "").startswith("p:e1@"))
            await pilot.pause(0.3)
            await pilot.press("enter")
            await until(pilot, lambda: isinstance(app.screen, PendingScreen))
            got["pending"] = type(app.screen).__name__
            await pilot.press("c")
            await until(pilot, lambda: isinstance(app.screen, ConfirmListing))
            got["after_c"] = type(app.screen).__name__
            got["on_pending_c"] = list(sent)                   # PendingScreen's c sent nothing: it showed the listing
            await pilot.press("c")
            await pilot.pause(0.3)
            got["early"] = list(sent)
            for _ in range(80):
                await pilot.press("j")
            await pilot.pause(0.5)
            await pilot.press("c")
            await pilot.pause(0.5)

    asyncio.run(drive())
    assert got["pending"] == "PendingScreen" and got["after_c"] == "ConfirmListing", got
    assert got["on_pending_c"] == [], got["on_pending_c"]
    if size == (80, 24):                                   # the listing scrolls there: c before its end sends nothing
        assert got["early"] == [], got["early"]
    confirm = got["listing"]["confirm"]
    assert sent == [confirm.split()[1:]] and sent[0][1].startswith("e1@") and len(sent[0][1].split("@")[1]) == 64, sent


def test_a_design_is_never_confirmed_without_its_listing(listed, monkeypatch):
    """However a design's row reaches the home's confirm (c beside the list, or PendingScreen's c through
    _confirm_shown), it opens the listing and sends nothing."""
    from rabbit_brain.tui import HomeScreen, RBApp
    from rabbit_brain.tui_panels import ConfirmListing
    rec = listed
    sent = _record(monkeypatch)
    got: dict = {}

    async def drive():
        app = RBApp(rec.root)
        async with app.run_test(size=(160, 50)) as pilot:
            await until(pilot, lambda: isinstance(app.screen, HomeScreen) and app.screen.query_one("#tree-list").option_count)
            home = app.screen
            row = next(r for r in home._pending if r.kind == "design")
            home._confirm_shown(row)
            await until(pilot, lambda: isinstance(app.screen, ConfirmListing))
            await pilot.pause(0.5)
            got["screen"] = type(app.screen).__name__

    asyncio.run(drive())
    assert got["screen"] == "ConfirmListing" and sent == [], (got, sent)


def _joined(rows: list, line: str) -> str:
    """Rows joined back as the listing broke them: where the text holds a space at the break, the break took it."""
    acc = rows[0]
    for r in rows[1:]:
        acc += (" " if line[len(acc):len(acc) + 1] == " " else "") + r
    return acc


def test_listing_rows_keep_every_character_of_rb_confirms_line():
    """A path or a hash longer than the listing's width is broken across rows, never cut, and a run of spaces in a
    value stays a run: rows joined back are rb confirm's line, character for character (no space collapsed, none put
    inside a word: MP-1113-1727 review, finding 1)."""
    from rabbit_brain.tui_panels import _hang, _rows, listing_lines
    path = "configs/" + "abcdefghij/" * 9 + "f.yaml#k"
    for line in (f'setting x: "a  b" from {path}', f"setting a.v: 1 from {'x/' * 50}f.yaml#k", 'setting eval.note2: "a  b\\tc"',
                 "criterion c1 (secondary): change.top1 ≥ -0.01 · for q-int8, when met, \"INT8   keeps top1\""):
        for width in (20, 36, 40, 56, 76, 116):
            rows = _hang(line, width)
            assert all(len(r) <= width for r in rows), (width, rows)
            assert rows[0].startswith("  ") and all(r.startswith("    ") for r in rows[1:]), rows
            assert _joined([rows[0][2:]] + [r[4:] for r in rows[1:]], line) == line, (width, rows)
    assert _rows("a  b", 20, 20) == ["a  b"]
    lines = listing_lines({"experiment": "e1", "title": "t", "lines": [f"setting a.v: 1 from {path}", "criterion c1 (gate): top1 ≥ 1"],
                           "confirm": "rb confirm e1@" + "a" * 64, "notes_left": ""}, 40)
    plain = [t.plain for t in lines]
    assert all(len(p) <= 40 for p in plain), plain
    assert "SETTINGS" in plain and "CHECKS" in plain, plain
    assert ("a" * 64) in "".join(p.strip() for p in plain), plain
