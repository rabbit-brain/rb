"""UX5 round 1 on every surface: Decisions (1.3), Files (1.4), rb ui's home (1.5) and rb status as the dashboard (1.6),
each drawn from the one `overview` (overview.rs) and `files` (files.rs) every surface holds."""
import asyncio
import json
import os
import re
import subprocess

import pytest

from rbtest import command
from words_budget import home_story_text, screen_text


def text_of(rec, *argv, **env) -> tuple:
    r = subprocess.run([*command(), *argv], cwd=rec.root, env={**rec.env, "COLUMNS": "80", "LINES": "24", **env}, capture_output=True, encoding="utf-8")
    return r.returncode, r.stdout


def page_of(rec, tmp_path, *argv) -> str:
    out = tmp_path / "page.html"
    rec.rb("page", "--out", str(out), *argv)
    return out.read_text(encoding="utf-8")


# ---------------------------------------------------------------- 1.3 Decisions

def test_the_decisions_count_is_one_number_on_every_surface(int8_demo, tmp_path):
    ov = int8_demo.rb("status")["overview"]
    n = len(ov["decisions"])
    assert n > 0
    _, text = text_of(int8_demo, "status")
    assert f"Decisions ({n})" in text
    page = page_of(int8_demo, tmp_path)
    # the Overview's Decisions counts them in its chips by kind, one row each, with no count on its heading
    head = re.search(r'id="decisions">Decisions(.*?)</h2>', page, flags=re.S)
    assert head and '<span class="n">' not in head.group(1), head
    chips = {k: int(m) for k, m in re.findall(r'<button type="button" class="dchip" data-filter="(decide|confirm|design)"[^>]*>(\d+) ', page)}
    rows = re.findall(r'<li class="dr dl1" data-kind="(decide|confirm|design)">', page)
    assert len(rows) == n and set(chips) == set(rows), (chips, rows)
    # a chip of decisions or of calls to confirm counts its rows (a design's row may count several designs)
    assert all(chips[k] == rows.count(k) for k in chips if k != "design"), (chips, rows)
    assert re.search(r'<li class="yc"[^>]*><a href="#your-call">[^<]*<span class="n">' + str(n) + "</span>", page)
    # the kinds: a call to make, and what waits for a confirmation, which carries its command
    kinds = {d["kind"] for d in ov["decisions"]}
    assert kinds <= {"decide", "confirm"}
    for d in ov["decisions"]:
        assert (d["kind"] == "confirm") == d["cmd"].startswith("rb confirm")


def test_needs_your_judgment_is_said_on_no_screen_and_kept_in_the_long_form(int8_demo, tmp_path):
    _, text = text_of(int8_demo, "status")
    assert "Needs judgment" not in text and "Needs your judgment" not in text
    _, full = text_of(int8_demo, "status", "--full")
    assert "Needs judgment" in full
    page = page_of(int8_demo, tmp_path)
    i = page.index('id="sec-overview"')
    assert "Needs your judgment" not in page[i:page.index('id="sec-research"', i)]


def test_a_readers_view_shows_no_confirmation(int8_demo, tmp_path):
    out = tmp_path / "x"
    int8_demo.rb("export", "--out", str(out))
    page = (out / "page.html").read_text(encoding="utf-8")
    i = page.index('id="sec-overview"')
    ov = page[i:page.index('id="sec-research"', i)]
    for li in re.findall(r"<li class=\"dr\"[^>]*>.*?</li>", ov, flags=re.S):
        if "chip k kc" in li:
            assert 'data-vw="none owner"' in li, li
        else:
            assert 'data-vw="reader">open</span>' in li, li


# ---------------------------------------------------------------- 1.4 Files

def git(rec, *argv) -> str:
    return subprocess.run(["git", *argv], cwd=rec.root, env=rec.env, capture_output=True, encoding="utf-8").stdout


def test_files_marks_are_what_git_and_the_record_say(int8_demo):
    files = int8_demo.rb("files")["files"]
    by = {f["path"]: f for f in files}
    status = [ln[3:] for ln in git(int8_demo, "-c", "core.quotepath=off", "status", "--porcelain", "--untracked-files=all", "--", ".", ":(exclude).rb").splitlines()]
    marked = {p for p, f in by.items() if any(m["key"] == "uncommitted" for m in f["marks"])}
    assert marked == {p for p in status if p in by}
    st = int8_demo.rb("status")
    # every file a run read its numbers from is a run's output
    for r in st["runs"]:
        for f in int8_demo.rb("show", r["id"])["object"].get("files") or []:
            if f["path"] in by:
                assert any(m["key"] == "output" for m in by[f["path"]]["marks"]), f["path"]


def test_a_page_lists_only_the_files_the_record_names_each_with_its_card(int8_demo, tmp_path):
    dump = tmp_path / "doc.json"
    env = {**int8_demo.env, "RB_TEST_SHELL": "1", "RB_TEST_SHELL_DUMP": str(dump)}
    subprocess.run([*command(), "page", "--out", str(tmp_path / "p.html")], cwd=int8_demo.root, env=env, check=True, capture_output=True)
    doc = json.loads(dump.read_text(encoding="utf-8"))
    assert doc["files"] and all(f["named"] for f in doc["files"])
    page = (tmp_path / "p.html").read_text(encoding="utf-8")
    assert '<div class="ftree" id="files"' in page and '<li class="fls">' in page and 'data-tab="files"' not in page
    for f in doc["files"]:
        assert f'id="file-{f["path"]}"' in page
        cited = [k for k, c in doc["citations"].items() if c["path"] == f["path"]]
        n = next((m["n"] for m in f["marks"] if m["key"] == "cited"), 0)
        assert n >= len(cited)


def test_rb_files_names_a_files_cited_lines(int8_demo):
    files = int8_demo.rb("files")["files"]
    cited = next(f for f in files if any(m["key"] == "cited" for m in f["marks"]))
    code, text = text_of(int8_demo, "files", cited["path"])
    assert code == 0 and text.startswith(cited["path"]) and "▸" in text and "Cited by" in text


# ---------------------------------------------------------------- 1.6 the dashboard

def test_rb_status_is_one_screen_and_full_is_the_long_form(int8_demo):
    code, text = text_of(int8_demo, "status")
    body = text.rstrip("\n").splitlines()
    assert "Next:" not in text and len(body) <= 24 and max(len(ln) for ln in body) <= 80, text
    ov = int8_demo.rb("status")["overview"]
    assert ov["question"]["text"].split()[0] in body[1]
    assert ov["strip"]["text"].split()[0] in text
    assert body[-1] == "rb show <id> opens one · rb status --full prints everything"
    _, full = text_of(int8_demo, "status", "--full")
    assert "Rules:" in full and "Experiments" in full and "rb status --full prints everything" not in full


def test_rb_status_words_says_each_figure(int8_demo):
    ov = int8_demo.rb("status")["overview"]
    _, text = text_of(int8_demo, "status", "--words")
    assert ov["strip"]["said"] in text
    for r in ov["rests"]:
        assert r["said"] in text


def test_a_gate_fails_as_before_under_the_dashboard(int8_demo):
    code, text = text_of(int8_demo, "status", "--fail-on", "unestablished")
    full_code, full = text_of(int8_demo, "status", "--full", "--fail-on", "unestablished")
    assert code == full_code
    if code == 1:
        assert "GATE FAILED on:" in text and "GATE FAILED on:" in full


def test_rb_context_says_where_a_person_reads_it_first(int8_demo):
    _, text = text_of(int8_demo, "context")
    assert text.splitlines()[0] == "To read this record yourself: rb status in a terminal, or rb ui."


# ---------------------------------------------------------------- 1.5 rb ui's home

def _ui(rec, monkeypatch, size, keys=()):
    pytest.importorskip("textual")
    from rabbit_brain.tui import RBApp
    for k, v in rec.env.items():
        monkeypatch.setenv(k, v)
    for k in [k for k in os.environ if k.startswith(("CLAUDE", "CODEX", "CURSOR", "AI_AGENT"))]:
        monkeypatch.delenv(k, raising=False)
    shots = []

    async def drive():
        app = RBApp(rec.root)
        async with app.run_test(size=size) as pilot:
            await pilot.pause(1.0)
            shots.append((screen_text(app) + "\n\x00\n" + home_story_text(app), type(app.screen).__name__))
            for k in keys:
                await pilot.press(k)
                await pilot.pause(0.6)
                shots.append((screen_text(app), type(app.screen).__name__))
    asyncio.run(drive())
    return shots


@pytest.mark.parametrize("size", [(120, 40), (80, 24)])
def test_rb_ui_home_is_the_overview_as_rb_status_draws_it(int8_demo, monkeypatch, size):
    ov = int8_demo.rb("status")["overview"]
    [(text, _)] = _ui(int8_demo, monkeypatch, size)
    for g in ov["strip"]["groups"]:
        assert f"{g['mark'] * len(g['checks'])} {len(g['checks'])} {g['label']}" in text, text
    yc = int8_demo.rb("status")
    n = len(yc["pending"]) + len(yc["designs_pending"]) + sum(1 for o in yc["open"] if o.get("who") == "human")
    assert f"YOUR CALL {n}" in text and f"Your call {n}  y" in text, text      # one row per design (section 5 item 10)
    if size[1] >= 30:
        assert ov["chart"][0]["est_text"] in text
        for r in ov["rests"]:
            # its count says it with its meter after the label (A10): no ✓ or ✗ beside it
            said = re.sub(r" [✓✗](?= )", "", r["text"])
            assert said.split(" ")[-1] in text and r["label"] in text, text
        assert "✓" not in text and "✗" not in text, text


def test_rb_ui_tab_and_enter_open_a_chart_row_and_escape_returns(int8_demo, monkeypatch):
    """Enter opens the row's card in the evidence pane, as fields (UX5 2.3), on the home screen; escape returns."""
    # the focus starts on Your call's first row; Tab goes on in reading order to the chart
    shots = _ui(int8_demo, monkeypatch, (120, 40), keys=("enter", "escape", "f"))
    names = [s for _, s in shots]
    assert set(names) == {"HomeScreen"}, names
    opened = shots[1][0]
    # the card over the story (TUI-PARITY M4): its tab row, Result open first, then escape gives the story back
    assert "Result  Rule  Runs" in opened and "━━━━━━" in opened, opened
    assert "YOUR CALL" in shots[2][0], shots[2][0]
    assert "Files" in shots[-1][0] and "FILES" not in shots[-1][0]


def test_rb_ui_home_at_120x40_shows_the_chart_and_counts_what_it_leaves_out(int8_demo, monkeypatch):
    """At 120x40 the Overview has the priority over the lines under the work: the strip, Decisions and the chart's rows
    are on the screen itself, not only in its lists, and what a list leaves out for room is counted on its last row
    ('+3 more checks'), so a chart row and a decision are each shown or counted. What waits for a confirmation is a row
    of Decisions, so the TO CONFIRM group stays off the home, and the scope lines keep to one line each."""
    ov = int8_demo.rb("status")["overview"]
    [(text, _)] = _ui(int8_demo, monkeypatch, (120, 40))
    screen, story = text.split("\n\x00\n")
    # the story pane's own rows (three panes: the evidence card beside it draws a chart of its own)
    shown = story
    assert ov["chart"][0]["est_text"] in shown, shown
    chart_shown = sum(1 for line in shown.splitlines() if "●" in line or "○" in line)      # a chart row is its bar
    more = re.search(r"\+(\d+) more checks?", shown)
    assert chart_shown >= 1 and chart_shown + (int(more.group(1)) if more else 0) == len(ov["chart"]), shown
    # Your call's rows, one line each ending in ›, then "+n more" (TUI-PARITY F3.3)
    st = int8_demo.rb("status")
    n = len(st["pending"]) + len(st["designs_pending"]) + sum(1 for o in st["open"] if o.get("who") == "human")
    head = next(i for i, line in enumerate(shown.splitlines()) if line.lstrip().startswith("YOUR CALL"))
    rows = [l for l in shown.splitlines()[head + 1:head + 5] if l.rstrip().endswith("›")]
    more = re.search(r"^\s+\+(\d+) more$", shown, re.M)
    assert len(rows) + (int(more.group(1)) if more else 0) == n, shown
    assert "TO CONFIRM" not in screen and "rb looks in " not in screen and "rb last looked at " not in screen and "as of " in screen
