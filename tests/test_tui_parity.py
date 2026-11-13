"""rb ui's parity with the web's passes (TUI-PARITY, round ui-parity-1): the header's two rows (Q1, Q2, A15, M12), the tab
row and pins (F1, M1, M2), every footer as section 3 measures it (G1), no sentence under the panes (G3), no palette
(F1.3), on the hosted samples (math, int8) at 120x40 and 80x24, and the bridge fields rb ui draws them from."""
import asyncio
import io
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from rbtest import AGENT_VARS

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="every record here is a git repository")

SAMPLES = Path(os.environ.get("RB_DEMO_RECORDS", "/srv/rb/demo"))
HOSTED = {"int8": SAMPLES / "record-take4" / "int8-cifar", "math": SAMPLES / "math-record-take5" / "math-prompts"}

# section 3's footers with their measured lengths
MEASURED = {
    "home.decision": 58, "home.design": 48, "home.chip": 50, "home.chip_on": 66, "home.filtered": 47, "home.row": 48,
    "head.git": 49, "head.as_of": 54, "tree.row": 48, "tree.resource": 58, "catalogue.resource": 42, "catalogue.next": 47,
    "yourcall.decision": 53, "also": 32, "armed": 32, "card.decide": 67, "card": 57, "card.design": 68,
    "card.question.next": 69, "card.question": 70, "card.resource.next": 47, "card.resource": 58, "card.resource.plain": 48, "long": 48, "files": 32,
    "files.found": 47, "found": 34, "found.note": 18, "where": 32, "continue": 48, "prompt": 6, "decide": 43,
    "decide.why": 18, "keys": 17,
}

# sentences no screen says (G3)
GONE = ["Not decided yet", "Open one to see why it waits", "still open in the record", "rb status says where it looks",
        "rb last looked at", "Changed .json/.yaml/.toml files", "Kept with rb since", "not as they happened", "$ rb confirm",
        "[confirm] 1 plan", "RESEARCH", "wait on you", "palette"]


NAMES: dict = {}


def name_of(root) -> str:
    return NAMES[str(root)]


@pytest.fixture(params=sorted(HOSTED))
def rec(request, tmp_path, monkeypatch):
    src = HOSTED[request.param]
    if not (src / ".rb").is_dir():
        pytest.skip(f"the hosted sample {request.param} is not on this machine")
    home = tmp_path / "home"
    home.mkdir()
    (home / ".gitconfig").write_text("[user]\n\temail = ana@example.org\n\tname = ana\n[safe]\n\tdirectory = *\n", encoding="utf-8")
    root = tmp_path / "rec"
    shutil.copytree(src, root, symlinks=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for k in AGENT_VARS:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("RB_TELEMETRY", "0")
    monkeypatch.chdir(root)
    NAMES[str(root)] = request.param
    return root


def screen_text(app, cols, rows) -> list[str]:
    from rich.console import Console
    console = Console(width=cols, height=rows, file=io.StringIO(), record=True, legacy_windows=False, color_system="truecolor", force_terminal=True)
    console.print(app.screen._compositor.render_update(full=True))
    return [line.rstrip() for line in console.export_text().splitlines()]


def run(root, size, check):
    from rabbit_brain.tui import HomeScreen, RBApp

    async def go():
        app = RBApp(root)
        async with app.run_test(size=size) as pilot:
            for _ in range(80):
                await pilot.pause(0.1)
                if isinstance(app.screen, HomeScreen) and app.screen.query_one("#story-head").render().plain:
                    break
            await pilot.pause(0.4)
            return await check(app, pilot)
    return asyncio.run(go())


def git(root, *args) -> str:
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=True).stdout.strip()


# ------------------------------------------------------------ the bridge


def test_the_bridge_gives_the_headers_facts_as_chips(rec):
    """Q1, Q2, A15: the git chip is the checkout's branch and its commit in 7 hex, the late chip counts what was recorded
    afterwards, `as of` is the last look's minute, `since` the record's first day, and the version is rabbit_brain's."""
    import rabbit_brain
    from rabbit_brain.tui import home_view
    h = home_view(rec)["head"]
    head = git(rec, "rev-parse", "HEAD")
    assert h["git"] == f"{git(rec, 'symbolic-ref', '--short', 'HEAD')} · {head[:7]}"
    assert h["commit"] == head and h["dirty"] == "" and h["not_committed"] == 0
    assert h["version"] == f"rb {rabbit_brain.__version__}"
    assert h["since"].startswith("since ") and h["since"].endswith(" 2026")
    want = {"math": "recorded afterwards · 9 of 9 runs", "int8": "recorded afterwards · 22 of 22 runs · 1 of 1 decision"}[name_of(rec)]
    assert h["afterwards"] == want


def test_an_uncommitted_record_file_shows_the_amber_chip(rec):
    """Q1: with record files no commit holds, `dirty` counts them ("± 1 not committed"), from the same count rb status
    gives; committed again, it is empty."""
    from rabbit_brain.tui import home_view
    subprocess.run(["rb", "note", "add", "the held-out set is not in the pretraining data"], cwd=rec, check=True, capture_output=True)
    h = home_view(rec)["head"]
    st = subprocess.run(["rb", "status", "--json"], cwd=rec, capture_output=True, text=True).stdout
    import json
    n = json.loads(st)["data"]["repository"]["record_files_not_committed"]
    assert n > 0 and h["not_committed"] == n and h["dirty"] == f"± {n} not committed"


def test_the_bridge_counts_your_call_one_row_per_design(rec):
    """Section 5 item 10: one row per design, so the chips add up to the badge (math 3 and 1 of 4, int8 1 and 2 of 3),
    and int8's one decision made reads `decided 1`."""
    from rabbit_brain.tui import home_view
    yc = home_view(rec)["ui"]["your_call"]
    chips = [c["said"] for c in yc["chips"]]
    if name_of(rec) == "math":
        assert chips == ["3 decisions", "1 design to confirm"] and yc["count"] == 4 and yc["decided"] == ""
    else:
        assert chips == ["1 decision", "2 designs to confirm"] and yc["count"] == 3 and yc["decided"] == "decided 1"
    assert len(yc["rows"]) == yc["count"]
    assert [r["kind"] for r in yc["rows"] if r["kind"] == "design"] == ["design"] * (1 if name_of(rec) == "math" else 2)


def test_continue_this_research_names_the_first_open_question(rec):
    """H1 to H3: the Continue prompt is H's string, naming the first open question."""
    from rabbit_brain.tui import home_view
    h = home_view(rec)
    q = h["overview"]["question"]["text"]
    assert h["ui"]["continue"] == (f"Continue the research in \"{h['title']}\" with Rabbit Brain. In its repository, run rb context first, "
                                    f"then work on what is still open: {q} Record every run with rb. Don't decide anything for me: list what needs my decision.")


def test_a_question_nothing_tests_has_the_plan_prompt(rec):
    """F5.2: a question nothing is filed under has the web's Next step prompt, naming it."""
    from rabbit_brain.tui import home_view
    h = home_view(rec)
    if name_of(rec) != "math":
        pytest.skip("only the math sample holds a question nothing tests")
    q3 = next(r for r in h["tree"] if r["id"] == "q3")
    assert h["ui"]["next_steps"]["q3"] == (f"In this repository, use Rabbit Brain to plan a test for the question \"{q3['label']}\": propose the rule a "
                                            "result must meet and the runs to make, record them with rb, and don't run anything until I confirm.")


# ------------------------------------------------------------ the lines, as pure functions


def test_every_footer_fits():
    """G1: every footer as section 3 measures it, whole, within 76 characters; under 80 columns its keys alone."""
    from rabbit_brain import tui_parts as parts
    for ctx, n in MEASURED.items():
        said = parts.footer_plain(parts.FOOTERS[ctx], 80)
        assert len(said) == n and len(said) <= 76 and "…" not in said, (ctx, said)
        assert parts.footer_plain(parts.FOOTERS[ctx], 60) == " ".join(k for k, _ in parts.FOOTERS[ctx])
        assert parts.footer_text(parts.FOOTERS[ctx], 80).plain == said
    assert len(parts.footer_plain(parts.armed_footer("e1"), 80)) == 38
    assert parts.footer_plain(parts.FOOTERS["home.decision"], 60) == "⏎ d c p ? q"
    assert not any("W" == k for pairs in parts.FOOTERS.values() for k, _ in pairs), "W is on figures and ?, never a footer"


def test_the_header_draws_m12():
    """M12, M22: row 0 and row 1 at 120 and 80 columns, clean and with uncommitted record files, character for character."""
    from rabbit_brain import tui_parts as parts
    title = "Think step by step: a small model on grade-school math"
    head = {"git": "main · c07560f", "afterwards": "recorded afterwards · 9 of 9 runs", "as_of": "as of 14:29 UTC",
            "since": "since 23 Oct 2026", "version": "rb 0.8.6"}
    r0, r1 = parts.header_rows(title, head, 120)
    assert " " + r0.plain == " Think step by step: a small model on grade-school math" + " " * 46 + "[main · c07560f] w"
    assert " " + r1.plain == " [recorded afterwards · 9 of 9 runs]   as of 14:29 UTC  r   since 23 Oct 2026   rb 0.8.6"
    r0, r1 = parts.header_rows(title, head, 80)
    assert " " + r0.plain == " Think step by step: a small model on grade-school math      [main · c07560f] w"
    assert " " + r1.plain == " [recorded afterwards · 9 of 9 runs]   as of 14:29 UTC  r   since 23 Oct 2026"
    dirty = dict(head, dirty="± 11 not committed")
    assert " " + parts.header_rows(title, dirty, 120)[0].plain == " Think step by step: a small model on grade-school math" + " " * 24 + "[main · c07560f] w  [± 11 not committed]"
    assert " " + parts.header_rows(title, dirty, 80)[0].plain == " Think step by step: a small model…    [main · c07560f] w  [± 11 not committed]"
    int8 = dict(head, afterwards="recorded afterwards · 22 of 22 runs · 1 of 1 decision")
    assert " " + parts.header_rows("INT8 ResNet-20 on CIFAR-10", int8, 80)[1].plain == " [recorded afterwards · 22 of 22 runs · 1 of 1 decision]   as of 14:29 UTC  r"
    # the amber chips are amber, the keys bold
    d0 = parts.header_rows(title, dirty, 120)[0]
    spans = {d0.plain[s.start:s.end]: str(s.style) for s in d0.spans}
    assert spans.get("± 11 not committed") == parts.WARN and spans.get("w") == "bold"


def test_the_tab_row_is_one_line():
    """F1.1: row 2 below 120 columns holds Overview, Research, Files and the Your call pin, the pin ending at column 78."""
    from rabbit_brain import tui_parts as parts
    for width in (80, 100):
        row = " " + parts.tab_row("overview", 1, 4, width).plain
        assert row.startswith("  Overview  Research  t   Files ±1  f") and row.endswith("Your call 4  y") and len(row) == width - 1
    t = parts.tab_row("overview", 1, 4, 80)
    reverse = [t.plain[s.start:s.end] for s in t.spans if "reverse" in str(s.style)]
    assert reverse == ["Overview"]
    assert parts.left_title("research", 1).plain == "Research  t  Files ±1  f"


# ------------------------------------------------------------ the screen


@pytest.mark.parametrize("size", [(120, 40), (80, 24)])
def test_the_home_draws_the_header_and_says_no_sentence(rec, size):
    """M1, M2: row 0 and row 1 are the header; row 2 below 120 is the tab row; no G3 sentence anywhere; the footer is
    whole and starts at column 2."""
    async def check(app, pilot):
        return screen_text(app, *size)

    lines = run(rec, size, check)
    head = run_head = lines[0]
    assert run_head.startswith(" ") and "] w" in head
    assert lines[1].lstrip().startswith("[recorded afterwards · ")
    if size[0] < 120:
        assert lines[2].startswith("  Overview  Research  t   Files") and lines[2].endswith("y") and len(lines[2]) == size[0] - 1
    else:
        assert "Research  t  Files" in lines[2] and "RESEARCH" not in lines[2]
    text = "\n".join(lines)
    for gone in GONE:
        assert gone not in text, gone
    assert lines[-1].startswith("  ⏎ open") and len(lines[-1]) <= 78


def test_ctrl_p_opens_no_palette(rec):
    """F1.3: ^p changes nothing."""
    async def check(app, pilot):
        before = screen_text(app, 80, 24)
        await pilot.press("ctrl+p")
        await pilot.pause(0.3)
        return before, screen_text(app, 80, 24), type(app.screen).__name__

    before, after, name = run(rec, (80, 24), check)
    assert name == "HomeScreen" and before == after


# ------------------------------------------------------------ copy (G8, M16)

PROMPT = 'In this repository, plan a test for "INT8 −0.2 points": record it with rb.'


class Ran:
    """A stub for subprocess.run that records each route's command and bytes."""

    def __init__(self, codes: dict):
        self.codes, self.calls = codes, []

    def __call__(self, cmd, input=None, **kw):
        self.calls.append((cmd[0] if cmd[0] != "tmux" else "tmux " + " ".join(cmd[1:]), input))
        return subprocess.CompletedProcess(cmd, self.codes.get(cmd[0], 0), b"", b"")


def test_copy_routes():
    """G8: locally the system command gets the exact UTF-8 bytes (`copied`); in tmux with no system clipboard,
    `load-buffer -w -` gets them (`copied to tmux`); over SSH no system clipboard is tried, and without tmux nothing can
    tell, so no route claims a copy; clip.exe is never used."""
    from rabbit_brain import clip
    data = PROMPT.encode("utf-8")
    which = lambda c: f"/usr/bin/{c}" if c in ("xclip", "tmux", "pbcopy", "wl-copy", "powershell") else None
    ran = Ran({})
    assert clip.copy(PROMPT, {"DISPLAY": ":0"}, ran, which, "linux") == "copied" and ran.calls == [("xclip", data)]
    ran = Ran({})
    assert clip.copy(PROMPT, {}, ran, which, "darwin") == "copied" and ran.calls == [("pbcopy", data)]
    ran = Ran({})
    assert clip.copy(PROMPT, {}, ran, which, "win32") == "copied" and ran.calls[0][0] == "powershell" and ran.calls[0][1] == data
    ran = Ran({})
    assert clip.copy(PROMPT, {"TMUX": "/tmp/t,1,0"}, ran, which, "linux") == "copied to tmux"
    assert ran.calls == [("tmux load-buffer -w -", data)]
    ran = Ran({})
    assert clip.copy(PROMPT, {"SSH_CONNECTION": "1 2 3 4", "DISPLAY": ":0", "TMUX": "x"}, ran, which, "linux") == "copied to tmux"
    assert [c for c, _ in ran.calls] == ["tmux load-buffer -w -"], "over SSH no system clipboard is tried"
    ran = Ran({})
    assert clip.copy(PROMPT, {"SSH_TTY": "/dev/pts/1", "DISPLAY": ":0"}, ran, which, "linux") is None and ran.calls == []
    ran = Ran({"xclip": 1})
    assert clip.copy(PROMPT, {"DISPLAY": ":0"}, ran, which, "linux") is None, "a failed command claims no copy"
    assert "clip.exe" not in Path(clip.__file__).read_text(encoding="utf-8").replace("never clip.exe", "")


def test_the_prompt_view_writes_exactly_the_prompt():
    """M16: OSC 52 carries the base64 of the exact text; then the prompt alone in reverse video, no box or padding, and
    `⏎ back`."""
    import base64
    from rabbit_brain import clip
    out = clip.prompt_view(PROMPT)
    osc, rest = out.split("\x07", 1)
    assert osc == "\x1b]52;c;" + base64.b64encode(PROMPT.encode()).decode()
    assert rest == f"\x1b[7m{PROMPT}\x1b[0m\n\n\x1b[1m⏎\x1b[0m back"
    for glyph in "│▊▎":
        assert glyph not in rest


def test_p_on_the_home_copies_continue_and_shows_the_chip(rec, monkeypatch):
    """H2, H3, M13: p on the home opens Continue this research, copies its prompt (H's string) and shows `[copied]` in its
    head; esc closes; with no route that can tell, the prompt view opens and no chip shows."""
    from rabbit_brain import clip, tui_panels
    from rabbit_brain.tui import home_view
    want = home_view(rec)["ui"]["continue"]
    sent = []
    monkeypatch.setattr(clip, "copy", lambda text, *a, **k: sent.append(text) or "copied")

    async def check(app, pilot):
        await pilot.press("p")
        await pilot.pause(0.5)
        panel = app.screen
        lines = screen_text(app, 80, 24)
        await pilot.press("escape")
        await pilot.pause(0.3)
        return type(panel).__name__, lines, type(app.screen).__name__

    name, lines, after = run(rec, (80, 24), check)
    assert name == "ContinuePanel" and sent == [want] and after == "HomeScreen"
    text = "\n".join(lines)
    assert "Continue this research" in text and "[copied]" in text
    assert lines[-1].strip() == "⏎ open  p copy prompt  esc close  ? keys  q quit"
    monkeypatch.setattr(clip, "copy", lambda text, *a, **k: None)

    async def check2(app, pilot):
        await pilot.press("p")
        await pilot.pause(0.6)
        stack = [type(s).__name__ for s in app.screen_stack]
        lines = screen_text(app, 80, 24)
        await pilot.press("x")
        await pilot.pause(0.3)
        return stack, lines, type(app.screen).__name__

    stack, lines, back = run(rec, (80, 24), check2)
    assert stack[-1] == "PromptView" and back == "ContinuePanel"
    text = "\n".join(lines)
    assert want[:60] in text.replace("\n", "") or want[:40] in text and "copied" not in text


def test_keys_lists_every_key_and_closes_on_question_mark(rec):
    """M15: ? lists every key with three words at most, W among them and in no footer; ? again closes it."""
    async def check(app, pilot):
        await pilot.press("question_mark")
        await pilot.pause(0.4)
        lines = screen_text(app, 80, 24)
        await pilot.press("question_mark")
        await pilot.pause(0.3)
        return lines, type(app.screen).__name__

    lines, after = run(rec, (80, 24), check)
    text = "\n".join(lines)
    from rabbit_brain import tui_parts as parts
    for k, label in parts.KEYS:
        assert label in text and len(label.split()) <= 3
    assert lines[-1].strip() == "esc close  q quit" and after == "HomeScreen"


def test_decide_records_with_rb_decide(rec, monkeypatch):
    """A11, M14: d on a decision row opens Decide; a picks accept, the reason takes text, ⏎ sends rb decide with both;
    esc writes nothing."""
    from rabbit_brain import tui
    sent = []
    monkeypatch.setattr(tui, "run_settle", lambda app, root, argv, word, after: sent.append(argv))

    async def check(app, pilot):
        await pilot.press("d")
        await pilot.pause(0.4)
        name = type(app.screen).__name__
        foot = screen_text(app, 80, 24)[-1].strip()
        await pilot.press("a")
        await pilot.pause(0.2)
        foot2 = screen_text(app, 80, 24)[-1].strip()
        for ch in "within the budget":
            await pilot.press("space" if ch == " " else ch)
        await pilot.press("enter")
        await pilot.pause(0.4)
        return name, foot, foot2, type(app.screen).__name__

    name, foot, foot2, after = run(rec, (80, 24), check)
    assert name == "DecidePanel" and foot == "a accept  r reject  i investigate  esc back" and foot2 == "⏎ record  esc back"
    assert after == "HomeScreen" and len(sent) == 1
    argv = sent[0]
    assert argv[0] == "decide" and argv[2] == "accept" and argv[3:] == ["--why", "within the budget"]


def test_where_is_rows(rec, monkeypatch):
    """Q1, M17: w opens rows labelled branch, commit, record files, other files and one per watched place; ⏎ on commit
    copies the full hash; no git command and no sentence."""
    from rabbit_brain import clip
    sent = []
    monkeypatch.setattr(clip, "copy", lambda text, *a, **k: sent.append(text) or "copied")

    async def check(app, pilot):
        await pilot.press("w")
        await pilot.pause(0.4)
        lines = [l for l in screen_text(app, 80, 24) if "▊" in l] + screen_text(app, 80, 24)[-1:]
        await pilot.press("enter")
        await pilot.pause(0.3)
        return lines, screen_text(app, 80, 24)

    lines, after = run(rec, (80, 24), check)
    text = "\n".join(lines)
    labels = [l.split("▎")[0] for l in lines]
    for label in ("branch", "commit", "record files", "other files", "watched"):
        assert any(label in l for l in lines), label
    assert "git " not in text and "rb checked" not in text and "To check it yourself" not in text
    assert sent == [git(rec, "rev-parse", "HEAD")] and "[copied]" in "\n".join(after)
    assert lines[-1].strip() == "⏎ copy  esc back  ? keys  q quit"


def test_also_open_rows(rec):
    """M18: a lists the items that need no decision, one line each, `mark  subject · state ›`, never `still open`."""
    from rabbit_brain.tui import home_view
    items = home_view(rec)["ui"]["also_open"]

    async def check(app, pilot):
        await pilot.press("a")
        await pilot.pause(0.4)
        return screen_text(app, 80, 24)

    lines = run(rec, (80, 24), check)
    text = "\n".join(lines)
    assert f"ALSO OPEN {len(items)}" in text and "still open" not in text
    rows = [l for l in lines if l.rstrip().endswith("›") and " · " in l]
    assert len(rows) == min(len(items), 17) and lines[-1].strip() == "⏎ open  esc back  ? keys  q quit"
    assert rows[0].startswith(" ▌")


def test_meters_are_at_most_ten_cells():
    """A10: up to 10 items one cell each; past 10 proportional, empty only at 0 and full only when complete."""
    from rabbit_brain.tui_parts import meter, rests_lines
    assert meter(0, 5) == "▱▱▱▱▱" and meter(2, 4) == "▰▰▱▱" and meter(0, 1) == "▱"
    for of in (11, 22, 400):
        for n in range(0, of + 1, max(1, of // 40)):
            m = meter(n, of)
            assert len(m) == 10 and (m == "▱" * 10) == (n == 0) and (m == "▰" * 10) == (n == of), (n, of, m)
    rests = [{"label": "planned first", "n": 0, "of": 5, "ok": False, "text": "planned first ✗ 0 of 5"},
             {"label": "reproduced", "n": 2, "of": 4, "ok": None, "text": "reproduced 2 of 4"},
             {"label": "tries", "n": 1, "of": None, "ok": None, "text": "tries 1 · 9 runs"},
             {"label": "code kept", "n": 0, "of": 1, "ok": False, "text": "code kept ✗ 0 of 1"}]
    assert [t.plain for t in rests_lines(rests, 50)] == ["planned first ▱▱▱▱▱ 0 of 5  reproduced ▰▰▱▱ 2 of 4", "tries 1 · 9 runs  code kept ▱ 0 of 1"]
    assert [t.plain for t in rests_lines(rests, 76)] == ["planned first ▱▱▱▱▱ 0 of 5   reproduced ▰▰▱▱ 2 of 4   tries 1 · 9 runs", "code kept ▱ 0 of 1"]


@pytest.mark.parametrize("size", [(60, 20), (80, 24), (100, 30), (120, 40)])
def test_t_f_y_a_at_every_width(rec, size):
    """F1.1: t opens Research, f opens Files (pressed twice it stays on Files), y and a open their lists, at every
    width; j and k move as ↓ and ↑ do."""
    async def check(app, pilot):
        s = app.screen
        got = {}
        await pilot.press("f")
        await pilot.pause(0.3)
        await pilot.press("f")
        await pilot.pause(0.3)
        got["f"] = (s._tab, getattr(app.focused, "id", None))
        await pilot.press("t")
        await pilot.pause(0.3)
        got["t"] = (s._tab, getattr(app.focused, "id", None))
        await pilot.press("y")
        await pilot.pause(0.3)
        dl = s.query_one("#decisions-list")
        got["y"] = (getattr(app.focused, "id", None), dl.highlighted)
        await pilot.press("j")
        await pilot.pause(0.1)
        got["j"] = dl.highlighted
        await pilot.press("k")
        await pilot.pause(0.1)
        got["k"] = dl.highlighted
        await pilot.press("a")
        await pilot.pause(0.3)
        got["a"] = type(app.screen).__name__
        return got

    got = run(rec, size, check)
    assert got["f"] == ("files", "files-list") and got["t"] == ("research", "tree-list")
    assert got["y"] == ("decisions-list", 1) and got["j"] == 2 and got["k"] == 1 and got["a"] == "AlsoPanel"


# ------------------------------------------------------------ G1 at 60x20, G9, G10


@pytest.mark.parametrize("size", [(60, 20)])
def test_under_80_columns_every_footer_shows_its_keys_alone(rec, size):
    """G1, section 0 item 3: under 80 columns (a tmux split) the home's footer is its keys alone, in order, and every
    line of the screen keeps within it."""
    async def check(app, pilot):
        return screen_text(app, *size)

    lines = run(rec, size, check)
    from rabbit_brain.tui_parts import FOOTERS as F
    assert lines[-1].strip() in {" ".join(k for k, _ in pairs) for pairs in F.values()} and "  " not in lines[-1].strip(), lines[-1]
    assert all(len(line) <= size[0] for line in lines), [line for line in lines if len(line) > size[0]]
    from rabbit_brain.tui_parts import FOOTERS, footer_plain
    for ctx, pairs in FOOTERS.items():
        assert footer_plain(pairs, 60) == " ".join(k for k, _ in pairs), ctx


def test_every_screen_reads_without_colour(rec, monkeypatch):
    """G9: with NO_COLOR set, each state keeps its mark and word, the focus its `▌`, the open tab its reverse video."""
    monkeypatch.setenv("NO_COLOR", "1")

    async def check(app, pilot):
        from rich.console import Console
        console = Console(width=80, height=24, file=io.StringIO(), record=True, legacy_windows=False, force_terminal=True, no_color=True)
        console.print(app.screen._compositor.render_update(full=True))
        return console.export_text(styles=False), app.screen.query_one("#tabrow").render()

    text, row2 = run(rec, (80, 24), check)
    assert "▌" in text and "YOUR CALL" in text, text
    assert any(f"{m} {w}" in text for m, w in (("▲", "not met"), ("▲", "not reproduced"), ("■", "passed"), ("▲", "failed"))), text
    spans = getattr(row2, "spans", None) or []
    assert any(getattr(sp.style, "reverse", False) or "reverse" in str(sp.style) for sp in spans), spans       # the open tab, without colour too


def test_every_glyph_is_one_cell():
    """G10: each glyph of section 0's table is one cell wide by wcwidth (rich's cell_len)."""
    from rich.cells import cell_len
    glyphs = "●├─┤○┆◆░■▲◧□‾┃▸▌›▶▼⧉↗∅±△?✓❝⚙▣⊘→▰▱"
    assert [g for g in glyphs if cell_len(g) != 1] == []
