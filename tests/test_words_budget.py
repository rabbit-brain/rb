"""The words budget (UX5 1.7, section 5): each view a reader meets first stays within its count of rb's own words, on
the INT8 demo and the scale record. The Overview (owner: its story and the left pane's Research tab), rb ui's home at
120x40 (its Overview: the panel's tabs and pin, the story and the key line) and rb status at 80 columns. A view over
its budget fails with its count and the words it found."""
import asyncio
import os
import subprocess

import pytest

from rbtest import command
from words_budget import home_story_text, overview_text, rbs_words

BUDGET = {"overview": 90, "ui_home": 110, "status": 70}


def _say(view: str, words: list, status: dict) -> str:
    return f"{view}: {len(words)} words of rb's own, over {BUDGET[view]}: {' '.join(words)[:600]}"


@pytest.mark.parametrize("which", ["int8_demo", "scale_record"])
def test_rb_status_stays_within_its_words(which, request):
    rec = request.getfixturevalue(which)
    st = rec.rb("status")
    env = {**rec.env, "COLUMNS": "80", "LINES": "24"}
    text = subprocess.run([*command(), "status"], cwd=rec.root, env=env, capture_output=True, encoding="utf-8").stdout
    text = text.split("\nNext:")[0]
    words = rbs_words(text, st)
    assert len(words) <= BUDGET["status"], _say("status", words, st)


@pytest.mark.parametrize("which", ["int8_demo", "scale_record"])
def test_the_overview_stays_within_its_words(which, request, tmp_path):
    rec = request.getfixturevalue(which)
    st = rec.rb("status")
    rec.rb("page", "--out", str(tmp_path / "page.html"))
    words = rbs_words(overview_text((tmp_path / "page.html").read_text(encoding="utf-8")), st)
    assert len(words) <= BUDGET["overview"], _say("overview", words, st)


@pytest.mark.parametrize("which", ["int8_demo", "scale_record"])
def test_rb_ui_home_stays_within_its_words(which, request, monkeypatch):
    textual = pytest.importorskip("textual")
    from rabbit_brain.tui import RBApp
    rec = request.getfixturevalue(which)
    st = rec.rb("status")
    for k, v in rec.env.items():
        monkeypatch.setenv(k, v)
    for k in [k for k in os.environ if k.startswith(("CLAUDE", "CODEX", "CURSOR", "AI_AGENT"))]:
        monkeypatch.delenv(k, raising=False)

    async def drawn():
        app = RBApp(rec.root)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(1.0)
            return home_story_text(app)
    words = rbs_words(asyncio.run(drawn()), st)
    assert len(words) <= BUDGET["ui_home"], _say("ui_home", words, st)
