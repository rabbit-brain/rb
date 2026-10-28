"""Only inspection counts: a terminal-screen session once, when the record first shows; the browser tab once, when it
first renders the record, and each card opened there. Repainting and polling keep usage unchanged."""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

import pytest

from rbtest import command
from test_telemetry import drain, names, receiver, trial  # noqa: F401 - shared isolated fixtures, no live analytics project

pytestmark = pytest.mark.skipif(not shutil.which("git"), reason="research fixtures use git")
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def test_the_screen_counts_one_inspection_a_session(trial, receiver, monkeypatch):
    from rabbit_brain import _rb, tui

    trial.project()
    trial.telemetry("enable")
    before = trial.pending()
    monkeypatch.setattr(_rb, "environment", lambda: dict(trial.env))
    for _ in range(3):                    # what the screen reads to paint, repaint and poll
        tui.home_view(trial.root)
        tui.ask(trial.root, "status")
        tui.object_card(trial.root, "private-exp-marker")
    assert trial.pending() == before

    async def session():
        app = tui.RBApp(trial.root)
        async with app.run_test(size=(120, 36)) as pilot:
            for _ in range(150):
                if isinstance(app.screen, tui.HomeScreen) and len(trial.pending()) > len(before):
                    break
                await pilot.pause(0.1)
            home = app.screen
            home.action_refresh()
            home._poll()
            home.query_one("#tree-list").focus()
            await pilot.press("enter")    # open a record from the catalogue
            for _ in range(50):
                if isinstance(app.screen, tui.ObjectScreen):
                    break
                await pilot.pause(0.1)
            assert isinstance(app.screen, tui.ObjectScreen)
            await pilot.press("q")
            await pilot.pause(0.2)

    asyncio.run(session())
    added = trial.pending()[len(before):]
    assert names(added) == ["rb_record_inspected"]
    assert added[0]["properties"]["surface"] == "tui"
    assert "private-" not in json.dumps(added)


def test_the_screens_quiet_requests_start_no_delivery(trial, receiver, monkeypatch):
    """The screen asks rb every few seconds, and a request that records nothing starts no delivery, so a screen left
    open never drives the network. Its one inspection a session still does."""
    from rabbit_brain import _rb, tui

    trial.project()
    trial.telemetry("enable")             # the consent event: a delivery is due
    monkeypatch.setattr(_rb, "environment", lambda: dict(trial.env))
    for _ in range(3):                    # what the screen reads to paint, repaint and poll
        tui.home_view(trial.root)
        tui.ask(trial.root, "scan")
        tui.object_card(trial.root, "private-exp-marker")
    assert trial.state()["last_attempt"] == 0 and receiver.requests == []
    _rb.call("telemetry_inspected", root=trial.root, via="cli")
    assert trial.wait(lambda: receiver.requests)


@pytest.mark.skipif(sys.platform == "win32", reason="drives the screen through a POSIX pseudo-terminal")
def test_bare_rb_in_a_terminal_is_the_screen_measured_as_rb_ui(trial, receiver):
    import fcntl
    import pty
    import struct
    import termios

    trial.project()
    trial.telemetry("enable")
    before = trial.pending()
    leader, follower = pty.openpty()
    fcntl.ioctl(follower, termios.TIOCSWINSZ, struct.pack("HHHH", 36, 120, 0, 0))
    screen = subprocess.Popen(command(), cwd=trial.root, env={**trial.env, "TERM": "xterm-256color"},
                              stdin=follower, stdout=follower, stderr=follower, close_fds=True)
    os.close(follower)
    drawn = []
    reader = threading.Thread(target=lambda: drain(leader, drawn), daemon=True)
    reader.start()
    try:
        assert trial.wait(lambda: len(trial.pending()) > len(before), timeout=60), b"".join(drawn)[-2000:]
        os.write(leader, b"q")
        assert screen.wait(timeout=30) == 0
    finally:
        if screen.poll() is None:
            screen.kill()
            screen.wait(timeout=5)
        os.close(leader)
    added = trial.pending()[len(before):]
    assert names(added) == ["rb_record_inspected", "rb_operation_completed"]
    assert [e["properties"]["surface"] for e in added] == ["tui", "tui"]
    assert added[1]["properties"]["command"] == "ui"


@contextlib.contextmanager
def _served(trial, tmp_path, name="web", **more):
    """rb open on the trial's project, as the user starts it, for as long as the block runs: the address it printed.
    `more` adds to its environment."""
    said = tmp_path / f"{name}.stderr"
    with said.open("wb") as stderr:
        process = subprocess.Popen([*command(), "open", "--port", "0"], cwd=trial.root, env={**trial.env, **more},
                                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=stderr)
        try:
            deadline = time.monotonic() + 15
            url = None
            while time.monotonic() < deadline and process.poll() is None:
                match = re.search(r"http://127\.0\.0\.1:\d+", said.read_text(encoding="utf-8"))
                if match:
                    url = match.group(0)
                    break
                time.sleep(0.02)
            assert url, said.read_text(encoding="utf-8")
            yield url
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def test_browser_polling_is_not_engagement(trial, receiver, tmp_path):
    trial.project()
    trial.telemetry("enable")
    before = trial.pending()
    with _served(trial, tmp_path) as url:
        for _ in range(3):
            with _OPENER.open(url + "/state.json", timeout=5) as response:
                assert response.status == 200
                json.load(response)
        assert trial.pending() == before
        with pytest.raises(urllib.error.HTTPError) as refused:
            _OPENER.open(url + "/telemetry/inspected", timeout=5)
        assert refused.value.code == 403
        refused.value.close()
        assert trial.pending() == before
        request = urllib.request.Request(url + "/telemetry/inspected", headers={"X-RB-Usage": "1"})
        with _OPENER.open(request, timeout=5) as response:
            assert json.load(response)["ok"] is True
        added = trial.pending()[len(before):]
        assert len(added) == 1 and added[0]["event"] == "rb_record_inspected"
        assert added[0]["properties"]["surface"] == "web"
        assert "private-" not in json.dumps(added)


def test_a_page_that_is_not_the_tab_records_nothing_and_sends_nothing(trial, receiver, tmp_path):
    """A page whose name was rebound to 127.0.0.1 sends the header the tab sends, so its Host is what refuses it; a page
    the browser says is another site is refused too. Neither queues an event or starts a delivery."""
    trial.project()
    trial.telemetry("enable")
    before = trial.pending()
    with _served(trial, tmp_path) as url:
        port = url.rsplit(":", 1)[1]
        for headers in ({"Host": f"attacker.example:{port}", "Sec-Fetch-Site": "same-origin"},
                        {"Sec-Fetch-Site": "cross-site"}, {"Sec-Fetch-Site": "same-site"}, {"Sec-Fetch-Site": "none"}):
            with pytest.raises(urllib.error.HTTPError) as refused:
                _OPENER.open(urllib.request.Request(url + "/telemetry/inspected", headers={"X-RB-Usage": "1", **headers}), timeout=5)
            assert refused.value.code == 403, headers
            refused.value.close()
        assert trial.pending() == before and receiver.requests == []
        request = urllib.request.Request(url + "/telemetry/inspected", headers={"X-RB-Usage": "1", "Sec-Fetch-Site": "same-origin"})
        with _OPENER.open(request, timeout=5) as response:
            assert json.load(response)["ok"] is True
        assert [e["event"] for e in trial.pending()[len(before):]] == ["rb_record_inspected"]


def test_the_live_page_counts_its_first_render_and_each_card_once(trial, receiver, tmp_path):
    """The shell's page on rb open, its live module run in Chrome (test_live's stand-in page until R's frame draws it):
    the first render counts one inspection through the route a card uses, its polls and the swap after a write in the
    terminal count none, and a card opened counts one more. Under RB_TEST_SHELL nothing is collected, so the page's
    inspections go to a second rb open on the record without it, which counts them as the tab's always were."""
    from test_live import POLL, Chrome, Front, until
    trial.project()
    trial.telemetry("enable")
    before = trial.pending()
    added = lambda: [e for e in trial.pending()[len(before):] if e["event"] != "rb_operation_completed"]   # the write's own aside
    with _served(trial, tmp_path, "shell", RB_TEST_SHELL="1") as shell, _served(trial, tmp_path, "plain") as plain:
        front = Front(shell, routes={"/telemetry/inspected": plain})
        browser = None
        try:
            browser = Chrome(tmp_path / "chrome")
            browser.goto(front.url + "/")
            assert browser.wait("!!window.rbLive && !!document.querySelector('#insp .ibody')", 30), browser.thrown
            until(lambda: len(added()) == 1, 30, added)
            polls = front.count("/digest.json")
            until(lambda: front.count("/digest.json") >= polls + 2, 2 * POLL + 4, lambda: front.asked)
            trial.cli("experiment", "add", "private-second-experiment-marker", "--id", "private-exp-2")   # a write in the terminal
            until(lambda: front.count("/") == 2, POLL + 4, lambda: front.asked)
            until(lambda: front.count("/digest.json") >= polls + 4, 2 * POLL + 4, lambda: front.asked)
            assert len(added()) == 1 and front.count("/telemetry/inspected") == 1, front.asked
            browser.js("document.querySelector('[data-go=\"call-c1\"]').click()")     # a card opened still counts
            until(lambda: len(added()) == 2, 30, added)
            assert {(e["event"], e["properties"]["surface"]) for e in added()} == {("rb_record_inspected", "web")}
            assert "private-" not in json.dumps(added()) and browser.thrown == []
        finally:
            if browser:
                browser.close()
            front.close()
