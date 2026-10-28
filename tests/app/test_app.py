"""The app harness (tests/app/app.mjs): the shell's script driven in headless Chrome by real clicks and keys.

Until the shell renders, it runs on the DOM fixture (tests/fixtures/shell/dom-fixture.html), which loads the script from
rust/src/shell/assets/script.js, so a change to the script is checked here at once. The browser tests need node 22 or
later and Chrome (RB_CHROME, then the usual places, then google-chrome on PATH). Without either they skip, saying which;
with RB_REQUIRE_BROWSER=1, as CI sets it, they fail instead."""
import contextlib
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from html.parser import HTMLParser
from pathlib import Path

import pytest

from rbtest import AGENT_VARS, command, environment

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "tests" / "app"
FIXTURE = ROOT / "tests" / "fixtures" / "shell" / "dom-fixture.html"
SCRIPT = ROOT / "rust" / "src" / "shell" / "assets" / "script.js"
GUIDE_CSS = ROOT / "rust" / "src" / "shell" / "assets" / "guide.css"
CONTRACT = ROOT / "tests" / "fixtures" / "shell" / "dom-contract.json"
EXAMPLE = str(ROOT / "tests" / "fixtures" / "shell" / "int8-demo.{}.view.json")
REQUIRED = os.environ.get("RB_REQUIRE_BROWSER") == "1"
# checks that fail on GitHub's macOS and Windows runners and pass on Linux and in Chrome on a Mac (issue #1)
RUNNER_KNOWN = pytest.mark.xfail(os.environ.get("GITHUB_ACTIONS") == "true" and sys.platform in ("darwin", "win32"),
                                 reason="fails on GitHub's macOS and Windows runners (issue #1)", strict=False)
WINDOWS_KNOWN = pytest.mark.xfail(os.name == "nt", reason="fails on Windows (issue #1)", strict=False)


def _without(why: str) -> None:
    if REQUIRED:
        pytest.fail(f"{why} (RB_REQUIRE_BROWSER=1)")
    pytest.skip(why)


def _node() -> str:
    node = shutil.which("node")
    if not node:
        _without("the app harness needs node 22 or later, and no node is on PATH")
    said = subprocess.run([node, "--version"], capture_output=True, text=True, timeout=60).stdout.strip()
    m = re.fullmatch(r"v(\d+)\.\d+\.\d+\S*", said)
    if not m or int(m[1]) < 22:
        _without(f"the app harness needs node 22 or later, for its global WebSocket; {node} is {said or 'unreadable'}")
    return node


@contextlib.contextmanager
def _one_browser_at_a_time():
    """One Chrome at a time across test processes, on the lock tests/test_shell.py takes too."""
    with open(Path(tempfile.gettempdir()) / "rb-tests-browser.lock", "a+b") as f:
        if os.name == "nt":
            import msvcrt
            while True:
                try:
                    f.seek(0)
                    msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)
                    break
                except OSError:
                    continue
        else:
            import fcntl
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == "nt":
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)


# how many times slower than a quiet machine a walk may run before its whole deadline (RB_APP_SLOW)
SLOW = float(os.environ.get("RB_APP_SLOW") or 3)


def harness(url, *argv: str, timeout: int = 300, lock: bool = True, env: dict = None, runner: str = "app.mjs") -> dict:
    """`node tests/app/app.mjs [--url <url>] <argv>`: its last line, as JSON, with its exit code under `exit`. `lock`
    False when the caller holds the browser lock already; `env` the environment it runs in (rb open's, with --rb)."""
    node = _node()
    # `timeout` is what the walk takes on a quiet machine; a loaded one (a full run, another OS) can take several times
    # that, so the whole deadline is SLOW times it, and a walk that hangs stops sooner: no check for --idle seconds
    timeout = int(timeout * SLOW)
    if "--timeout" not in argv:
        argv = (*argv, "--timeout", str(max(60, timeout - 30)))
    kw = {"start_new_session": True} if os.name != "nt" else {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    with _one_browser_at_a_time() if lock else contextlib.nullcontext():
        proc = subprocess.Popen([node, str(APP / runner), *(["--url", url] if url else []), *argv], stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, encoding="utf-8", env=env, **kw)
        try:
            out, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            if os.name != "nt":
                os.killpg(proc.pid, signal.SIGKILL)
            else:
                subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
            proc.communicate()
            pytest.fail(f"the app harness did not end in {timeout} s")
    if proc.returncode == 3:
        _without("the app harness needs Chrome, and none was found (set RB_CHROME)")
    lines = [ln for ln in out.splitlines() if ln.strip()]
    try:
        said = json.loads(lines[-1])
    except (IndexError, ValueError):
        pytest.fail(f"the app harness said no JSON (exit {proc.returncode}): {err[-2000:]}")
    said["exit"] = proc.returncode
    said["stderr"] = err
    return said


def _failed(said: dict) -> list:
    """The checks that failed, by name: a failing run says them in its error, "N of M checks failed: a; b"."""
    err = said.get("error") or ""
    return err.split(" checks failed: ", 1)[1].split("; ") if " checks failed: " in err else [err]


@pytest.fixture(scope="module")
def walked() -> dict:
    """One walk of the fixture by every check, shared by the tests below: a failing check fails each test that names
    it, and a walk that fails as a whole fails them all."""
    # the suite's workers each ask: the first walks, under the browser lock, and the rest read what it found, for the
    # same script, fixture and harness
    key = hashlib.sha256(b"".join(p.read_bytes() for p in (SCRIPT, GUIDE_CSS, FIXTURE, APP / "app.mjs", APP / "cdp.mjs"))).hexdigest()[:16]
    cache = Path(tempfile.gettempdir()) / f"rb-app-walk-{key}.json"
    with _one_browser_at_a_time():
        if cache.is_file():
            return json.loads(cache.read_text(encoding="utf-8"))
        said = harness(FIXTURE.as_uri(), timeout=1200, lock=False)
        if said.get("exit") in (0, 1):
            tmp = cache.with_suffix(f".{os.getpid()}.tmp")
            tmp.write_text(json.dumps(said), encoding="utf-8")
            os.replace(tmp, cache)
    return said


def _checks(said: dict, *starts: str) -> dict:
    """The checks whose names start with one of `starts`, by name: pass or fail. A failed walk names its failures."""
    failed = set(_failed(said)) if not said["ok"] else set()
    names = [c["name"] for c in said.get("checks") or []] or sorted(failed)
    return {n: n not in failed for n in names if n.startswith(starts)}


def _passes(said: dict, *starts: str, at_least: int = 1) -> None:
    got = _checks(said, *starts)
    assert said["exit"] in (0, 1), said["stderr"][-3000:]
    assert len(got) >= at_least, (starts, sorted(got), said.get("error"))
    assert all(got.values()), sorted(n for n, ok in got.items() if not ok)


@RUNNER_KNOWN
def test_the_script_works_the_fixture_by_real_clicks(walked):
    """Views, the inspector stack, crumbs, Back, Esc, focus return and cold links, at 390 and 1280, light and dark,
    then print and with the script off: every check of the walk passes."""
    assert walked["exit"] == 0 and walked["ok"], (_failed(walked), walked["stderr"][-3000:])
    names = [c["name"] for c in walked["checks"]]
    for part in ("start", "views", "stack", "crumbs", "back", "esc", "close", "cold", "root", "moves", "print", "no script",
                 "address", "your call", "composer", "waiting", "copy", "filter", "keys", "theme", "live", "nothing past the screen",
                 "guide"):
        assert any(n.startswith(part) for n in names), part
    for at in ("390 light", "390 dark", "1280 light", "1280 dark"):
        assert sum(1 for n in names if n.endswith(f"[{at}]")) >= 30, at


def test_the_top_bar_s_about_opens_its_view(walked):
    """Design pass 1, target 11: About in the top bar opens its view, marked there as the nav marks its own, and a
    word's address opens About at the word."""
    _passes(walked, "about", at_least=12)


def test_the_inspector_stack_crumbs_back_and_esc(walked):
    _passes(walked, "stack", "crumbs", "back", "esc", "close", "moves", at_least=40)


@RUNNER_KNOWN
def test_cold_links_land_on_their_card_and_back_stays_on_the_page(walked):
    _passes(walked, "cold", at_least=40)


def test_the_address_grammar(walked):
    """An extra '#', a name that is nowhere, rb's old view names and rb open's anchors, its place and version kept."""
    _passes(walked, "address", at_least=30)


def test_your_call_starts_on_the_first_call(walked):
    _passes(walked, "your call", "root: Your call", at_least=6)


def test_composer_never_posts(walked):
    """Nothing to copy before an action and a reason; the reason single-quoted; Copy sends nothing and no form exists."""
    _passes(walked, "composer", at_least=10)


def test_a_past_version_composes_no_command(walked):
    """Guarantee 38: on a past version a choice by key or by click composes no rb decide line, and a call chosen live is
    not put back when the past version is swapped in, at 390 and 1280."""
    _passes(walked, "composer: a past version", at_least=4)


def test_a_waiting_item_copies_only_its_confirm(walked):
    _passes(walked, "waiting", at_least=4)


def test_copy_filters_keys_and_theme(walked):
    _passes(walked, "copy", "filter", "keys", "theme", at_least=30)


def test_rb_open_s_live_module_hears_of_cards_and_redraws(walked):
    """rb:card for each card brought to the inspector's top by the user, never on a redraw; after rb:swapped the app
    stands where it stood and says so with preventDefault (L's live.js, BP 4a)."""
    _passes(walked, "live", at_least=8)


def test_no_overflow_at_390(walked):
    """Nothing past the screen at 390 and no line past its box: every view, a card open, a sheet narrowed into, no script."""
    _passes(walked, "nothing past the screen", "no line runs past", "moves: narrowed across 960, nothing", "no script: nothing past",
           at_least=10)


def test_print_and_no_script_show_the_whole_document(walked):
    _passes(walked, "print", "no script", at_least=7)


# ---------------------------------------------------------------- the pointer guide (script.js's last block, guide.css)

def test_the_guide_shows_on_a_first_load_and_not_the_second(walked):
    """A labelled dialog on the navigator with focus in it, its steps in order (Your call only while a call waits),
    Next, Back and Done, kept as ended; the next load shows nothing, and the guide moves no layout and blocks no click."""
    _passes(walked, "guide: a first load", "guide: four steps", "guide: Back", "guide: Done", "guide: not on the second",
            "guide: it moves no layout", "guide: the page behind", "guide: no Your call step", "guide: with no storage",
            "guide: without its styles", at_least=34)


def test_esc_ends_the_guide(walked):
    """Esc ends the guide and only it; under 960 a card's sheet hides it and Esc closes the sheet first; the keys list
    opens over it and its own Esc closes the list alone."""
    _passes(walked, "guide: Esc ends it", "guide: a sheet over it", "guide: the keys list opens over it", at_least=10)


def test_the_guide_replays_from_the_keys_list(walked):
    _passes(walked, "guide: \"Show the guide again\"", at_least=4)


def test_the_guide_is_never_in_print_no_script_or_a_deep_link(walked):
    _passes(walked, "guide: never in print", "guide: never in the no-script", "guide: never on a deep link", at_least=12)


@RUNNER_KNOWN
def test_the_guide_fits_390_reads_at_aa_and_keeps_still_under_reduced_motion(walked):
    """No horizontal scroll at any step, each tooltip whole on the screen and at 390 below or above its part; its words
    at AA contrast in light and dark; no motion under prefers-reduced-motion; scrolled or resized across 960, the
    tooltip stays whole and the spotlight follows its part."""
    _passes(walked, "guide: nothing past the screen", "guide: at 390", "guide: its words", "guide: no motion", "guide: scrolled",
            "guide: resized across 960", at_least=18)


def test_the_guide_never_covers_what_it_lights_and_shows_a_findings_provenance(walked):
    """Polish 1 (Look 2 item 10): each step lights the part its words name (at 390, step 1 the Overview's list of the
    questions), and its tooltip covers neither that part nor the view's title and summary; step 3 opens a finding in
    the inspector (a sheet under 960, which the guide stays over), the next step closes it, and closed by the user it
    takes the guide a step on; in dark the dimming darkens the page's text at least threefold, the tooltip at AA."""
    _passes(walked, "guide: each step lights its part", "guide: step 3 opens a finding", "guide: the finding it opened",
            "guide: in dark its dimming", "guide: Back to step 3", at_least=18)


def test_the_guide_gives_focus_back(walked):
    _passes(walked, "guide: focus returns", at_least=4)


def _guide_block() -> str:
    script = SCRIPT.read_text(encoding="utf-8")
    start = script.index("// ---------- the pointer guide")
    return script[start:]


@pytest.fixture(scope="module")
def v2_walked() -> dict:
    """One walk of tests/app/overview-v2.html (the Overview of OVERVIEW-V2, with C's card levels and rb open's live
    module) by tests/app/v2.mjs, shared by the tests below as `walked` is."""
    parts = (SCRIPT, GUIDE_CSS, SCRIPT.with_name("live.js"), APP / "overview-v2.html", APP / "v2.mjs", APP / "cdp.mjs")
    key = hashlib.sha256(b"".join(p.read_bytes() for p in parts)).hexdigest()[:16]
    cache = Path(tempfile.gettempdir()) / f"rb-app-v2-{key}.json"
    with _one_browser_at_a_time():
        if cache.is_file():
            return json.loads(cache.read_text(encoding="utf-8"))
        said = harness(None, timeout=600, lock=False, runner="v2.mjs")
        if said.get("exit") in (0, 1):
            tmp = cache.with_suffix(f".{os.getpid()}.tmp")
            tmp.write_text(json.dumps(said), encoding="utf-8")
            os.replace(tmp, cache)
    return said


def test_a_queue_button_opens_the_inspector_at_decide(v2_walked):
    """The queue's button opens its call in the inspector at the Decide level: its first action has focus, a key acts
    on it at once, Esc gives focus back to the button; without the script it jumps to the Decide part."""
    _passes(v2_walked, "v2: a queue's button", "v2: opened at Decide", "v2: Esc closes it", "v2: after a live reload, Esc", "v2: without the script", at_least=17)


def test_j_and_k_move_through_the_queue_and_enter_opens(v2_walked):
    _passes(v2_walked, "v2: j and k move", "v2: Enter opens", "v2: on Your call, j opens", at_least=12)


def test_rb_open_s_top_bar_says_the_place_with_or_without_a_where_picker(v2_walked):
    _passes(v2_walked, "v2: from 1280 the top bar", at_least=1)


def test_source_details_stay_as_they_were_across_a_live_reload(v2_walked):
    _passes(v2_walked, "v2: Source details", at_least=8)


def test_the_overview_inspector_shows_its_empty_line(v2_walked):
    _passes(v2_walked, "v2: the Overview's inspector", at_least=2)


def test_the_guide_follows_the_new_overview(v2_walked):
    """The guide's steps on the Overview V2: where the record stands, how far to trust it (titled as that section's own
    heading), what needs your judgment, then a finding in the inspector; each lit, its tooltip beside it."""
    _passes(v2_walked, "v2: the guide's steps", "v2: each guide step", "v2: the guide's integrity step", "v2: the guide's inspector step", at_least=16)


def test_the_guide_says_rbs_own_words():
    """Every word the guide shows keeps rb's word bans (tests/test_shell.py's lint), with no em dash; and the guide is
    one block at the end of the script, so it can be left out whole with guide.css."""
    import sys
    sys.path.insert(0, str(ROOT / "tests"))
    import test_shell as lint
    block = _guide_block()
    words = re.findall(r"(?:title|text|textContent): '([^']+)'", block)
    # and the words a step chooses by the screen's width
    words += [w for w in re.findall(r"[?:] '([A-Z][^']{15,})'", block) if w not in words]
    words += re.findall(r"'(Show the guide again|Skip|Back|Next|Done)'", block) + re.findall(r">([A-Z][a-z]+)</button>", block)
    assert len(words) >= 15, words
    page = "<html><body>" + "".join(f"<p>{w}</p>" for w in words) + "</body></html>"
    assert lint.words_problems(page) == []
    assert "\u2014" not in block
    # Design pass 1 (P9): one name per thing; the screens call what rb judges a rule, never a finding
    assert not [w for w in words if "finding" in w.lower()], [w for w in words if "finding" in w.lower()]
    # nothing above the block names it: the rest of the script works the same without it
    head = SCRIPT.read_text(encoding="utf-8")[: SCRIPT.read_text(encoding="utf-8").index("// ---------- the pointer guide")]
    assert "guide" not in head.lower(), [ln for ln in head.splitlines() if "guide" in ln.lower()]


def test_the_harness_fails_a_page_without_the_script(tmp_path):
    """The harness is the proof: the same fixture without its script fails it, rather than passing in document mode."""
    page = tmp_path / "fixture" / "tests" / "fixtures" / "shell" / "dom-fixture.html"
    page.parent.mkdir(parents=True)
    page.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    js = tmp_path / "fixture" / "rust" / "src" / "shell" / "assets" / "script.js"
    js.parent.mkdir(parents=True)
    js.write_text("", encoding="utf-8")
    said = harness(page.as_uri(), "--only", "start,stack")
    assert said["exit"] == 1 and not said["ok"]
    # the app never starts: the walk stops at the first page it opens, saying so
    assert any(n.startswith("start: html.app") or n.endswith("never became ready") for n in _failed(said)), _failed(said)


def _rb_env(model: Path) -> dict:
    """rb's environment for a test of the shell's render: no agent session, and the shell drawing `model`."""
    env = {k: v for k, v in environment().items() if k not in AGENT_VARS and not k.startswith("RB_TEST_SHELL")}
    env.update(RB_TELEMETRY="0", DO_NOT_TRACK="1", RB_TEST_NOW="2026-10-19T09:41:00.000+00:00", RB_TEST_SHELL="1",
               RB_TEST_SHELL_MODEL=str(model))
    return env


@pytest.fixture
def a_record(tmp_path):
    root = tmp_path / "rec"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    r = subprocess.run([*command(), "init", "INT8 ResNet-50 on ImageNet", "--no-agents"], cwd=root, env=_rb_env(EXAMPLE.format("owner")),
                       capture_output=True, encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL)
    assert r.returncode == 0, r.stderr
    return root


def _walked_whole(said: dict, calls: bool = False) -> None:
    """A walk of a shell page by its own markup that passed whole; `calls`: the record holds calls that wait and
    findings, so the Polish 1 checks of a call's row, the composer and the guide's step 3 must be among its checks."""
    assert said["exit"] == 0 and said["ok"], (_failed(said), said["stderr"][-3000:])
    names = [c["name"] for c in said["checks"]]
    for at in ("390 light", "390 dark", "1280 light", "1280 dark"):
        assert f"generic: nothing past the screen in any view [{at}]" in names, at
    assert "generic: no line runs past its box in any view [390 light]" in names
    assert any(n.startswith("generic: each waiting item copies only its confirm") for n in names)
    assert any(n.startswith("print") for n in names) and any(n.startswith("no script: nothing past the screen [390]") for n in names)
    # Polish 1: the inspector's state, a call's row, the composer, and the guide, on rb's own render
    if not calls:
        return
    assert "generic: guide: each step lights its part, whole on the screen, over neither it nor the view's title and summary [390x667 light]" in names
    for at in ("390 light", "390 dark", "1280 light", "1280 dark"):
        for part in ("no view shows a card of the document's under it", "a call's row in Your call opens its card",
                     "the composer's Copy and its Copied never cover the line", "guide: each step lights its part",
                     "guide: step 3 opens a finding", "guide: the finding it opened is closed"):
            assert any(n.startswith("generic: " + part) and n.endswith(f"[{at}]") for n in names), (part, at)
        # Back to step 3 where a step follows it; on the Overview v2 the inspector is the last step, in its order
        assert any(n.startswith(("generic: guide: Back to step 3", "generic: guide: the Overview v2's steps")) and n.endswith(f"[{at}]")
                   for n in names), at
        if at.startswith("1280"):
            assert f"generic: a view with no rail of its own shows rb's line, not another view's rail [{at}]" in names, at
    assert "print: the document's cards print" in names
    assert "generic: the Overview says when the record changed, relative only on rb open's live view [1280]" in names


def test_rb_page_s_render_holds_up_by_real_clicks(a_record, tmp_path):
    """rb page's shell, the INT8 Owner example drawn, walked by its own markup: every view by its address and the nav,
    nothing past the screen at 390 in any view (SHELL-SPEC 3.1, light and dark, with the script, in print and with no
    script), the rails as roots, what waits copying only its confirm, the address, the theme and keys controls."""
    page = tmp_path / "page.html"
    r = subprocess.run([*command(), "page", "--out", str(page)], cwd=a_record, env=_rb_env(EXAMPLE.format("owner")),
                       capture_output=True, encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL)
    assert r.returncode == 0, r.stderr
    _walked_whole(harness(page.as_uri(), "--generic", timeout=600), calls=True)


@pytest.mark.parametrize("surface", ["page", "export"])
def test_an_empty_inspector_folds_to_a_slim_rail_on_every_page_the_script_runs(a_record, tmp_path, surface):
    """Design pass 1, target 8: with nothing picked, from 960 the inspector is a slim rail and the working view takes its
    width; a card opened by a click takes it back and Esc folds it again. On rb page, and on rb export's page as the hub
    serves it (rb's script beside it, named by its sha256), whose styles carry none of A's own."""
    out = tmp_path / surface
    argv = ["page", "--out", str(out / "page.html")] if surface == "page" else ["export", "--out", str(out)]
    r = subprocess.run([*command(), *argv], cwd=a_record, env=_rb_env(EXAMPLE.format("owner")),
                       capture_output=True, encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL)
    assert r.returncode == 0, r.stderr
    page = out / "page.html"
    if surface == "export":
        html = page.read_text(encoding="utf-8")
        script = SCRIPT.read_text(encoding="utf-8")
        assert f'data-shell="sha256:{hashlib.sha256(script.encode()).hexdigest()}"' in html and "<script" not in html
        page.write_text(html.replace("</head>", f"<script>{script}</script></head>", 1), encoding="utf-8")
    said = harness(page.as_uri(), timeout=240, runner="rail.mjs")
    assert said["exit"] == 0 and said["ok"], (_failed(said), said["stderr"][-3000:])
    names = [c["name"] for c in said["checks"]]
    for at in ("1280 light", "1100 dark", "390 light"):
        assert any(n.endswith(f"[{at}]") for n in names), at


def test_rb_open_s_render_holds_up_by_real_clicks(a_record):
    """rb open's / under the shell (the Live example drawn, with the live module), walked the same way: started by the
    harness on a free port, and stopped by it."""
    said = harness(None, "--generic", "--rb", command()[0], "--record", str(a_record), timeout=600, env=_rb_env(EXAMPLE.format("live")))
    _walked_whole(said, calls=True)
    assert said["started"]["rb_open_url"], said
    for at in ("390 light", "390 dark", "1280 light", "1280 dark"):
        assert f"generic: the top bar says no absolute path [{at}]" in [c["name"] for c in said["checks"]], at
    assert "generic: past 30 days rb open's live view says the exact time it changed [1280]" in [c["name"] for c in said["checks"]]


def test_a_walk_has_time_for_a_slow_machine_and_stops_soon_when_it_hangs(tmp_path):
    """A walk's whole deadline is SLOW times what it takes on a quiet machine (a macOS full run took longer than 570 s);
    a walk that says no check for --idle seconds stops then, naming its last check, however long the whole deadline."""
    stub = tmp_path / "stub.mjs"
    stub.write_text(f"""import {{ parseArgs, progress, run, sleep }} from {json.dumps((APP / "cdp.mjs").as_uri())};
const a = parseArgs(process.argv.slice(2), {{ flags: ['hang'] }});
await run(async () => {{
  for (let n = 0; n < 6; n++) {{ await sleep(400); progress('check ' + n); }}
  if (a.hang) await sleep(60000);
  return {{ argv: process.argv.slice(2) }};
}}, {{ timeoutMs: Number(a.timeout) * 1000, idleMs: Number(a.idle) * 1000 }});
""", encoding="utf-8")
    said = harness(None, "--idle", "1", timeout=100, lock=False, runner=str(stub))
    assert said["exit"] == 0 and said["ok"], said
    assert said["argv"][said["argv"].index("--timeout") + 1] == str(int(100 * SLOW) - 30), said
    started = time.monotonic()
    said = harness(None, "--idle", "1", "--hang", timeout=100, lock=False, runner=str(stub))
    assert said["exit"] == 1 and said["error"] == "stopped: no progress for 1 s after 'check 5'", said
    assert time.monotonic() - started < 30


def test_a_single_checkout_s_top_bar_says_no_path(tmp_path):
    """The film rule: rb open on one checkout with no committed version (no Where picker) names the place by its
    branch on the top bar, at 390 and 1280, light and dark; its whole path is only the tooltip's. The bare record
    walks short of the generic walk's richer checks, so only the top bar's are read here."""
    root = tmp_path / "int8-resnet50"
    root.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
    env = _rb_env(EXAMPLE.format("owner"))
    env.pop("RB_TEST_SHELL_MODEL")
    r = subprocess.run([*command(), "init", "INT8 ResNet-50 on ImageNet", "--no-agents"], cwd=root, env=env, capture_output=True,
                       encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL)
    assert r.returncode == 0, r.stderr
    said = harness(None, "--generic", "--rb", command()[0], "--record", str(root), timeout=600, env=env)
    bar = {c["name"]: c for c in said.get("checks", []) if c["name"].startswith("generic: the top bar says no absolute path")}
    for at in ("390 light", "390 dark", "1280 light", "1280 dark"):
        c = bar.get(f"generic: the top bar says no absolute path [{at}]")
        assert c and c["pass"], (at, c, said.get("error"), said.get("stderr", "")[-2000:])
        assert "main" in c["detail"] and str(root) not in c["detail"], c


@pytest.fixture
def two_places():
    """The INT8 record committed in a checkout, with a second worktree (exp-b, .worktrees/b) holding its own copy. Its
    path is short, as a home folder's is, so the place's label does not wrap the pickers onto a line of their own."""
    top = Path(tempfile.mkdtemp(prefix="rb", dir="/tmp" if os.name != "nt" else None))
    root = top / "int8"
    root.mkdir()
    env = _rb_env(EXAMPLE.format("owner"))
    env.pop("RB_TEST_SHELL_MODEL")
    git = lambda *argv, cwd=root: subprocess.run(["git", "-c", "user.name=ana", "-c", "user.email=ana@example.org", *argv], cwd=cwd, check=True, capture_output=True)
    git("init", "-q", "-b", "main")
    r = subprocess.run([*command(), "init", "INT8 ResNet-50 on ImageNet", "--no-agents"], cwd=root, env=env, capture_output=True, encoding="utf-8",
                       errors="replace", stdin=subprocess.DEVNULL)
    assert r.returncode == 0, r.stderr
    r = subprocess.run([*command(), "question", "add", "Does INT8 cost accuracy on ImageNet?", "--id", "q-int8"], cwd=root, env=env,
                       capture_output=True, encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL)
    assert r.returncode == 0, r.stderr
    # a call that waits for a confirmation, so the live record offers a command to copy and a past version must not
    r = subprocess.run([*command(), "decide", "q-int8", "park", "--why", "the calibration runs come first"], cwd=root, env={**env, "CLAUDECODE": "1"},
                       capture_output=True, encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL)
    assert r.returncode == 0, r.stderr
    git("add", "-A")
    git("commit", "-q", "-m", "the record")
    git("worktree", "add", "-q", "-b", "exp-b", ".worktrees/b")
    yield root, env
    shutil.rmtree(top, ignore_errors=True)


def _places(said: dict, wired: bool) -> None:
    assert said["exit"] == 0 and said["ok"], (_failed(said), said["stderr"][-3000:])
    names = [c["name"] for c in said["checks"]]
    for at in ("390 light", "1280 dark"):
        assert f"places: choosing exp-b shows it, the address keeps it, the view stays [{at}]" in names, at
        assert f"places: a place rb does not list falls back, saying why [{at}]" in names, at
        assert f"places: a reload shows the place again [{at}]" in names and f"places: a reload shows the past version again [{at}]" in names, at
        assert f"places: the open Version picker stays on the screen [{at}]" in names, at
        assert f"places: of two quick choices the last stays [{at}]" in names, at
        assert (f"places: a past version offers no command to copy [{at}]" in names) == wired, at
    for w in (1280, 1440):
        assert f"places: a space between the Version picker and the clock [{w}]" in names, w


def test_rb_open_s_places_are_chosen_by_real_clicks(two_places):
    """rb open in a checkout with a second worktree: the Where and Version pickers by real clicks at 390 and 1280, light
    and dark; the live module swaps the other place in and the address keeps it; Back, a reload and a link show it
    again; a place rb does not list falls back to the place rb open started on, live, saying why; nothing past the
    screen with a picker open."""
    root, env = two_places
    _places(harness(None, "--places", "--rb", command()[0], "--record", str(root), timeout=900, env=env), wired=False)


def test_a_past_version_offers_no_command(two_places):
    """Away, the scope bar says where in words, read only, with the way back; a past version offers no command to copy,
    with rb's reason, and Back to now offers them again (BP 4b; guarantee 38)."""
    root, env = two_places
    _places(harness(None, "--places", "--wired", "--rb", command()[0], "--record", str(root), timeout=900, env=env), wired=True)


@pytest.mark.parametrize("which", ["away", "past"])
def test_rb_open_says_another_place_and_a_past_version(a_record, which):
    """rb open drawing the INT8 example away from the start place and at a past version: the title names the place
    first, once, on every view and card; a past version offers no command to copy and selects none whole, while away
    commands are offered; an address naming what is shown in other words is written as rb open names it; a refusal is
    said until the next move."""
    said = harness(None, "--shown", which, "--rb", command()[0], "--record", str(a_record), timeout=600, env=_rb_env(EXAMPLE.format(which)))
    assert said["exit"] == 0 and said["ok"], (_failed(said), said["stderr"][-3000:])
    names = [c["name"] for c in said["checks"]]
    assert sum(n.startswith("shown: ") for n in names) == 4 * 6, names


class _Doc(HTMLParser):
    """Each element's tag, id, classes and attributes, in document order."""

    def __init__(self):
        super().__init__()
        self.els = []

    def handle_starttag(self, tag, attrs):
        d = dict(attrs)
        self.els.append((tag, d, set((d.get("class") or "").split())))


def _matches(el, selector: str) -> bool:
    """The DOM contract's simple selectors (tag, #id, .class, [attr], [attr^="v"]), read by hand: no new dependency."""
    tag, attrs, classes = el
    m = re.fullmatch(r'([a-z]*)((?:#[\w-]+)?)((?:\.[\w-]+)*)((?:\[[\w-]+(?:\^?="[^"]*")?\])*)', selector)
    assert m, selector
    if m[1] and m[1] != tag:
        return False
    if m[2] and attrs.get("id") != m[2][1:]:
        return False
    if any(c not in classes for c in m[3].split(".")[1:]):
        return False
    for name, op, val in re.findall(r'\[([\w-]+)(?:(\^?=)"([^"]*)")?\]', m[4]):
        if name not in attrs:
            return False
        if op == "=" and attrs[name] != val or op == "^=" and not (attrs[name] or "").startswith(val):
            return False
    return True


def _holds(els, selector: str) -> bool:
    if " > " in selector:
        parent, child = selector.split(" > ")
        return any(_matches(e, child) for e in els) and any(_matches(e, parent) for e in els)
    return any(_matches(e, selector) for e in els)


def test_the_fixture_holds_the_markup_the_contract_names():
    """Every selector dom-contract.json says the markup makes is in the fixture, so the script is built against all of
    it; the places' markup (4b) is A5's, and the folds' keys are the fixture's from the start."""
    doc = _Doc()
    doc.feed(FIXTURE.read_text(encoding="utf-8"))
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    missing = [s["selector"] for s in contract["selectors"] if s["made_by"] == "markup" and s["group"] not in ("A5",)
               and not _holds(doc.els, s["selector"])]
    assert not missing, missing


def test_the_fixture_loads_the_script_it_checks():
    """The fixture runs the script in the tree, never a copy of it."""
    html = FIXTURE.read_text(encoding="utf-8")
    assert html.count("<script") == 1
    assert '<script src="../../../rust/src/shell/assets/script.js"></script>' in html
    assert '<link rel="stylesheet" href="../../../rust/src/shell/assets/guide.css">' in html
    assert (FIXTURE.parent / "../../../rust/src/shell/assets/guide.css").resolve() == GUIDE_CSS.resolve()
    assert (FIXTURE.parent / "../../../rust/src/shell/assets/script.js").resolve() == SCRIPT.resolve()


# the hub says who looks, just before the shell's script it serves the page with (the orchestrator's Oct 25 07:30 +08
# brief, "Your view and the reader's view")
_VIEWER = '<meta name="rb-viewer" content="{}">'
# what a page shows in the hub's sandboxed frame (sandbox="allow-scripts", no allow-same-origin: storage throws), sent to
# the frame's host by message: the owner's composer composed by the page's own handlers, or what a reader is shown
_VIEWER_PROBE = """<script>
const said = []; const ce = console.error; console.error = (...a) => { said.push(a.map(String).join(' ')); ce(...a); };
let net = 0; const tick = () => { net++; };
window.fetch = () => { tick(); return Promise.reject(new Error('no network')); };
const xo = XMLHttpRequest.prototype.open; XMLHttpRequest.prototype.open = function (...a) { tick(); return xo.apply(this, a); };
if (navigator.sendBeacon) navigator.sendBeacon = () => { tick(); return false; };
window.WebSocket = function () { tick(); throw new Error('no network'); };
const vis = e => e.checkVisibility() && e.getBoundingClientRect().width > 0;
const acts = () => { const rb = /^(cd \\S.* && )?rb (?!clone\\b)[a-z]/, n = s => Array.from(document.querySelectorAll(s)).filter(vis).length;
  return { cmds: Array.from(document.querySelectorAll('.cmdbox, pre, code')).filter(e => vis(e) && rb.test(((e.querySelector('.cmd') || e).textContent || '').trim())).map(e => e.textContent.trim().slice(0, 60)),
    copy: n('.cp, [data-act="copy"]'), choices: n('.seg button, .decide button'), why: n('input.why'), composer: n('.composer'), decide: n('[data-level="decide"], .decide') }; };
setTimeout(() => {
  let stored = 'kept'; try { localStorage.getItem('x'); } catch (_) { stored = 'throws'; }
  const row = document.querySelector('#overview .queue a[data-decide], #overview .queue a');
  // where a row opens nothing (the export's queue today), the first call's card by its address
  if (row) row.click(); else { const c = document.querySelector('main .nodes .card[id^="call-"]') || document.querySelector('main .nodes .card[id]'); if (c) location.hash = '#' + c.id; }
  setTimeout(() => {
    const pick = document.querySelector('.ibody .decide .seg button'); if (pick && vis(pick)) pick.click();
    const why = document.querySelector('.ibody .decide input.why');
    if (why && vis(why)) { why.value = 'ok for ana'; why.dispatchEvent(new Event('input', { bubbles: true })); }
    const out = document.querySelector('.ibody .composer:not([hidden]) .cmd'), cp = document.querySelector('.ibody .composer:not([hidden]) .cp');
    parent.postMessage(JSON.stringify({ stored, hosted: document.documentElement.classList.contains('hosted'), viewer: document.documentElement.dataset.viewer || null,
      line: out && vis(out) ? out.textContent : null, live: !!(cp && !cp.disabled), acts: acts(), net, errors: said }), '*');
  }, 600);
}, 1500);
</script>"""
_VIEWER_HOST = """<!doctype html><html><body style="margin:0"><script>
addEventListener('message', e => document.body.setAttribute('data-probe', e.data));
</script><iframe sandbox="allow-scripts" style="width:1440px;height:900px;border:0" src="{}"></iframe></body></html>"""


def test_the_owner_s_view_composes_and_a_reader_s_view_offers_no_action(a_record, tmp_path):
    """Your view and the reader's view: the hub injects <meta name="rb-viewer"> before the shell's script, "owner" on
    the owner's record address and "reader" on a share link; the script sets html[data-viewer]. The owner's queue row
    opens its call at Record a call and the composer composes and copies by real input; a reader's opens it to read, with
    no rb command, Copy, choice or reason field on any view or card (rb page, and rb export's page as the hub serves a
    share link), keys that make or copy nothing, and every open row, the trust line and every heading still shown; no
    meta, the page as it was. In the hub's sandboxed frame (no storage) the owner's composer composes and nothing is sent."""
    env = _rb_env(EXAMPLE.format("owner"))
    page = tmp_path / "page.html"
    for argv in (["page", "--out", str(page)], ["export", "--out", str(tmp_path / "exp")]):
        r = subprocess.run([*command(), *argv], cwd=a_record, env=env, capture_output=True, encoding="utf-8", errors="replace",
                           stdin=subprocess.DEVNULL)
        assert r.returncode == 0, r.stderr
    html = page.read_text(encoding="utf-8")
    # no meta, the page as it was: the head holds no viewer meta (shell.css may name the meta in its selectors)
    assert "<head>" in html and not re.search(r'<meta[^>]*name="rb-viewer"', html.split("<script", 1)[0])
    urls = {}
    for who in ("owner", "reader"):
        # rb page holds its script in its head: the meta goes before it, as the hub puts it before the one it injects
        at = tmp_path / f"{who}.html"
        at.write_text(html.replace("<head>", "<head>" + _VIEWER.format(who), 1), encoding="utf-8")
        urls[who] = at.as_uri()
    exp = (tmp_path / "exp" / "page.html").read_text(encoding="utf-8")
    script = SCRIPT.read_text(encoding="utf-8")
    assert "<script" not in exp
    share = tmp_path / "share.html"
    share.write_text(exp.replace("</head>", _VIEWER.format("reader") + f"<script>{script}</script></head>", 1), encoding="utf-8")
    # a reader's page whose app never starts (no #main to build on): no command box shows all the same
    broken = tmp_path / "broken.html"
    broken.write_text(urls_html := html.replace("<head>", "<head>" + _VIEWER.format("reader"), 1).replace('id="main"', 'id="main-x"', 1), encoding="utf-8")
    assert 'id="main-x"' in urls_html
    framed = []
    for name, src, who in (("owner page", html.replace("<head>", "<head>" + _VIEWER.format("owner"), 1), "owner"),
                           ("reader export", exp.replace("</head>", _VIEWER.format("reader") + f"<script>{script}</script></head>", 1), "reader")):
        inner, host = tmp_path / f"in-{who}.html", tmp_path / f"host-{who}.html"
        inner.write_text(src.replace("</body>", _VIEWER_PROBE + "</body>"), encoding="utf-8")
        host.write_text(_VIEWER_HOST.format(inner.name), encoding="utf-8")
        framed.append([name, host.as_uri(), who])
    said = harness(None, "--none", page.as_uri(), "--owner", urls["owner"], "--reader", urls["reader"], "--export", share.as_uri(),
                   "--framed", json.dumps(framed), "--failsafe", broken.as_uri(), timeout=420, runner="viewer.mjs")
    assert said["exit"] == 0 and said["ok"], (_failed(said), said["stderr"][-3000:])
    names = [c["name"] for c in said["checks"]]
    for at in ("1440 light", "1440 dark", "390 light", "390 dark"):
        for n in ("reader: a queue's row opens its item to read, with no command, button or reason field", "owner: the composer composes the call with the reason and copies it",
                  "export, reader: no view or card shows an rb command, a Copy, a call's choices or reason"):
            assert f"{n} [{at}]" in names, (n, at)
    assert sum(n.startswith("framed (") for n in names) == 2, names
    assert "reader: the guide says where the calls stand, by no word of whose judgment [1440 light]" in names
    assert "reader: where the app never starts, no rb command box shows" in names


@pytest.mark.xfail(strict=True, reason="R's and M's words: rb page's views draw the owner's wording whatever the meta says (by_viewer only on "
                   "the export: views.rs:3024 the queue's heading, nodes.rs:435 the nav's Your call), and on both surfaces "
                   "'Your reading:' (R, now 'Note:' in design pass 1) and '... none of them for your judgment' (M, PLAIN4 M1) "
                   "speak to the owner: requests.md, PLAIN4. Passes once they land.")
@pytest.mark.parametrize("surface", ["page", "export"])
def test_a_reader_s_page_says_no_your_and_offers_no_action(a_record, tmp_path, surface):
    """Plain words pass 4: rb page with the reader's meta (as the preview shows a reader), and rb export's page as the
    hub serves a share link, read as a reader's: no view, navigator or top bar says "your", and none offers an action
    (a queue row's Review button included)."""
    if surface == "export":
        reader = _export_with(a_record, tmp_path, "reader")
    else:
        page = tmp_path / "page.html"
        r = subprocess.run([*command(), "page", "--out", str(page)], cwd=a_record, env=_rb_env(EXAMPLE.format("owner")),
                           capture_output=True, encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL)
        assert r.returncode == 0, r.stderr
        reader = tmp_path / "reader.html"
        reader.write_text(page.read_text(encoding="utf-8").replace("<head>", "<head>" + _VIEWER.format("reader"), 1), encoding="utf-8")
    said = harness(None, "--yours", reader.as_uri(), timeout=180, runner="viewer.mjs")
    assert said["exit"] == 0 and said["ok"], (_failed(said), said["stderr"][-3000:])


def test_a_reader_s_queue_row_opens_by_its_own_link_with_no_review_button(a_record, tmp_path):
    """Plain words pass 4: a reader is shown no queue row's Review or Inspect button where the row's title is a link of
    its own (design pass 1's rows: `a.qa` beside `a.qbtn`, both to the item), on rb page with the reader's meta too; the
    title opens the item to read."""
    page = tmp_path / "page.html"
    r = subprocess.run([*command(), "page", "--out", str(page)], cwd=a_record, env=_rb_env(EXAMPLE.format("owner")),
                       capture_output=True, encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL)
    assert r.returncode == 0, r.stderr
    html = page.read_text(encoding="utf-8")
    # each row's title is its link, beside the row's Review button, as design pass 1 draws them (R's views.rs)
    rows = html
    assert re.search(r'<span class="qv"><a class="qa" href="(#[^"]+)">.*?</a></span>.*?<a class="qbtn" href="\1"', html), "no queue row drawn"
    reader = tmp_path / "reader.html"
    reader.write_text(rows.replace("<head>", "<head>" + _VIEWER.format("reader"), 1), encoding="utf-8")
    said = harness(None, "--rows", reader.as_uri(), timeout=120, runner="viewer.mjs")
    assert said["exit"] == 0 and said["ok"], (_failed(said), said["stderr"][-3000:])


def test_the_owner_reads_your_call_where_the_export_says_open_calls(a_record, tmp_path):
    """R's request (MP-1025-15): the export names the calls "Open calls" in the attributes the script reads (a view's
    data-title and data-crumb, a rail's data-rootname and aria-label); served to its owner it reads "Your call" there
    (the tab's title, the crumbs, a question's rail), and on a share link "Open calls"."""
    exp = _export_with(a_record, tmp_path / "o", "owner")
    html = exp.read_text(encoding="utf-8")
    assert 'data-crumb="Open calls"' in html and 'data-rootname="Open calls' in html
    said = harness(None, "--ownerexp", exp.as_uri(), "--readerexp", _export_with(a_record, tmp_path / "r", "reader").as_uri(),
                   timeout=120, runner="viewer.mjs")
    assert said["exit"] == 0 and said["ok"], (_failed(said), said["stderr"][-3000:])


def test_a_term_says_its_meaning_in_place_and_moves_nothing(a_record, tmp_path):
    """Plain words pass 4 (Oct 25 15:05 +08): a click or a tap on a word with a meaning shows it beside the word and
    follows nothing, not the link or the row the word sits in; the working view, its scroll, the address and the
    inspector stay; Esc or a click elsewhere closes it. On rb export's page as the hub serves it to its owner, on a
    question's view and in a rule's card, at 1280 and 390, light and dark."""
    exp = _export_with(a_record, tmp_path, "owner")
    said = harness(None, "--url", exp.as_uri(), "--view", "q-q1", "--card", "c-c1", "--inlink", "1", timeout=240, runner="terms.mjs")
    assert said["exit"] == 0 and said["ok"], (_failed(said), said["stderr"][-3000:])
    names = [c["name"] for c in said["checks"]]
    for at in ("1280 light", "390 dark"):
        assert any(n.startswith("term: ") and "(in a card)" in n and n.endswith(f"[{at}]") for n in names), at


def test_inside_the_hub_the_page_draws_no_bar_of_its_own(a_record, tmp_path):
    """Plain words pass 4, one bar on the hub (Oct 25 13:35 +08): a page the hub names a viewer of draws no top bar of
    its own, under the hub's; About is in the navigator, the guide's first step shows, and at 390 the navigator opens
    a view. The same export with no meta keeps its bar."""
    exp = _export_with(a_record, tmp_path, "owner")
    plain = tmp_path / "plain.html"
    plain.write_text(exp.read_text(encoding="utf-8").replace(_VIEWER.format("owner"), "", 1), encoding="utf-8")
    said = harness(None, "--url", exp.as_uri(), "--plain", plain.as_uri(), timeout=180, runner="hub.mjs")
    assert said["exit"] == 0 and said["ok"], (_failed(said), said["stderr"][-3000:])
    assert len(said["checks"]) == 3, said["checks"]
    # the DOM fixture, whose bar holds About and which carries the guide's styles, with the owner's meta
    fx = tmp_path / "fixture" / "tests" / "fixtures" / "shell"
    fx.mkdir(parents=True)
    html = FIXTURE.read_text(encoding="utf-8")
    assert 'href="#about"' in html.split('<header class="top"', 1)[1].split("</header>", 1)[0]
    (fx / "dom-fixture.html").write_text(html, encoding="utf-8")
    (fx / "hub.html").write_text(html.replace("<head>", "<head>" + _VIEWER.format("owner"), 1), encoding="utf-8")
    assets = tmp_path / "fixture" / "rust" / "src" / "shell" / "assets"
    assets.mkdir(parents=True)
    for f in (SCRIPT, GUIDE_CSS):
        shutil.copy(f, assets / f.name)
    said = harness(None, "--url", (fx / "hub.html").as_uri(), "--plain", (fx / "dom-fixture.html").as_uri(), "--guide", "1", "--about", "1",
                   timeout=180, runner="hub.mjs")
    assert said["exit"] == 0 and said["ok"], (_failed(said), said["stderr"][-3000:])
    assert len(said["checks"]) == 5, said["checks"]


@pytest.mark.parametrize("surface", ["fixture", "page", "export"])
def test_a_detail_on_another_view_opens_in_the_inspector_and_back_returns(a_record, tmp_path, surface):
    """Design pass 1, P12: a link in a view or a card to a part in another view's fold (the project's notes, the open
    items, what the words mean) opens it in the inspector under its fold's words; the working view stays, the link where it
    was on the screen, and Back returns the view and its scroll as they were. By real clicks at 1280 and 390, light and dark: on the DOM fixture, rb page, and
    rb export's page as the hub serves it."""
    if surface == "fixture":
        url = FIXTURE.as_uri()
    else:
        out = tmp_path / surface
        argv = ["page", "--out", str(out / "page.html")] if surface == "page" else ["export", "--out", str(out)]
        r = subprocess.run([*command(), *argv], cwd=a_record, env=_rb_env(EXAMPLE.format("owner")),
                           capture_output=True, encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL)
        assert r.returncode == 0, r.stderr
        page = out / "page.html"
        if surface == "export":
            html = page.read_text(encoding="utf-8")
            page.write_text(html.replace("</head>", _VIEWER.format("owner") + f"<script>{SCRIPT.read_text(encoding='utf-8')}</script></head>", 1), encoding="utf-8")
        url = page.as_uri()
    said = harness(url, timeout=240, runner="p12.mjs")
    assert said["exit"] == 0 and said["ok"], (_failed(said), said["stderr"][-3000:])
    names = [c["name"] for c in said["checks"]]
    for at in ("1280 light", "390 dark"):
        assert f"P12: the page holds a link to another view's fold [{at}]" in names, at
        assert any(n.startswith("P12: ") and "opens the part in the inspector" in n and n.endswith(f"[{at}]") for n in names), at


def _export_with(a_record, tmp_path, who: str) -> Path:
    """rb export's page as the hub serves it to `who`: the viewer meta, then rb's script, before </head>."""
    r = subprocess.run([*command(), "export", "--out", str(tmp_path / "exp")], cwd=a_record, env=_rb_env(EXAMPLE.format("owner")),
                       capture_output=True, encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL)
    assert r.returncode == 0, r.stderr
    exp = (tmp_path / "exp" / "page.html").read_text(encoding="utf-8")
    at = tmp_path / f"{who}.html"
    at.write_text(exp.replace("</head>", _VIEWER.format(who) + f"<script>{SCRIPT.read_text(encoding='utf-8')}</script></head>", 1), encoding="utf-8")
    return at


@pytest.mark.xfail(strict=True, reason="M's words (rust/src/ledger/design.rs:418 'design not confirmed · list it to confirm: rb confirm <exp>', "
                   "rust/src/view.rs:3146 'rb confirm lists them', the uncited files' 'rb reconcile lists it by path') name an rb command "
                   "in a sentence; a reader is told the state ('The design is not confirmed yet.'): R draws both under html[data-viewer] "
                   "(requests.md, OWNER-READER). Passes once they land.")
def test_a_reader_s_share_link_names_no_rb_command_in_a_sentence(a_record, tmp_path):
    """The reader's view on a share link: no command anywhere, sentences included, every fold open."""
    said = harness(None, "--prose", _export_with(a_record, tmp_path, "reader").as_uri(), timeout=240, runner="viewer.mjs")
    assert said["exit"] == 0 and said["ok"], (_failed(said), said["stderr"][-3000:])


def test_the_owner_s_export_composes_a_call_in_the_hub_s_frame(a_record, tmp_path):
    """The owner's record on the hub: rb export's page in the hub's sandboxed frame, the owner meta: a call's Decide part
    composes the line with the reason typed, keeping nothing and sending nothing."""
    page = _export_with(a_record, tmp_path, "owner")
    inner, host = tmp_path / "in-owner-exp.html", tmp_path / "host-owner-exp.html"
    inner.write_text(page.read_text(encoding="utf-8").replace("</body>", _VIEWER_PROBE + "</body>"), encoding="utf-8")
    host.write_text(_VIEWER_HOST.format(inner.name), encoding="utf-8")
    said = harness(None, "--framed", json.dumps([["owner export", host.as_uri(), "owner"]]), timeout=120, runner="viewer.mjs")
    assert said["exit"] == 0 and said["ok"], (_failed(said), said["stderr"][-3000:])


# Rerun this research (the owner's Oct 26 feature, REPRODUCE-WORDS.md; R draws it and words it in views.rs and strings.rs):
# the prompt as a reader copies it, rb's own addresses replaced by the hub's metas
_BUNDLE = "https://rabbitbrain.ai/s/ana-int8/bundle.zip"
_PAGE = "https://rabbitbrain.ai/s/ana-int8"
_RB_BUNDLE = "https://example.org/int8/bundle.zip"
_RB_PAGE = "https://example.org/int8/page.html"
_CLONE_METAS = f'<meta name="rb-clone" content="{_BUNDLE}"><meta name="rb-page" content="{_PAGE}">'


def _rerun_prompt(page: str, bundle: str, folder: str, exp: tuple = None) -> str:
    """R's words (strings.rs rerun_prompt), as the fixture holds them."""
    run = f'Rerun experiment {exp[0]}, "{exp[1]}", the way the record describes it' if exp else "Run each experiment the way the record describes it"
    if exp and len(exp[2]) == 1:
        tell = f"whether its rule {exp[2][0]} reproduced, with its numbers"
    elif exp and exp[2]:
        tell = f"which of its rules ({', '.join(exp[2])}) reproduced and which did not, with their numbers"
    else:
        tell = "which rules reproduced and which did not, with their numbers"
    return (f"Rerun this research with Rabbit Brain (rb). Its record: {page}\n\n"
            "1. Install rb: pip install rabbit-brain\n"
            f"2. Clone the record into a new folder: rb clone {bundle} {folder}\n"
            "   Where the record names the code the runs need, rb clone says its repository and commit. If it names none, ask me where the code is.\n"
            "3. In that folder, run rb context and read it before anything else. It names each resource, such as a model or a dataset, with how to get it.\n"
            "4. Fetch that repository and check out its commit in the same folder, beside .rb/, leaving out its own .rb/ and the results its runs "
            f"wrote (its results folders and databases). Get those resources. {run}, with rb journal run -- <command>, and record each run on its "
            'experiment: rb run add <experiment> --from <the file it wrote> --command "<that command>" (rb says any per-run value it needs).\n'
            f"5. Then run rb status, and tell me {tell}. Do not change a rule. If something cannot run here, say what and why.")


def _inline(html: str) -> str:
    """The fixture with its script and the guide's styles in the page, so a copy of it runs anywhere."""
    out = html.replace('<script src="../../../rust/src/shell/assets/script.js"></script>', "<script>" + SCRIPT.read_text(encoding="utf-8") + "</script>")
    out = out.replace('<link rel="stylesheet" href="../../../rust/src/shell/assets/guide.css">', "<style>" + GUIDE_CSS.read_text(encoding="utf-8") + "</style>")
    assert "<script src=" not in out and "guide.css\">" not in out
    return out


def _without_rerun(html: str) -> str:
    """The page as rb draws it with no clone address: none of the feature's parts (class rrun)."""
    out = re.sub(r'<a class="rrb rrun [^"]*" href="#rerun" data-rerun>Rerun this research</a>', "", html)
    out = re.sub(r'<section class="repro rrun" id="reproduce".*?</section>\n', "", out)
    out = re.sub(r'<aside class="rerun rrun" id="rerun".*?</aside>\n', "", out, flags=re.S)
    out = re.sub(r'<div class="rrx rrun".*?</div>\n', "", out, flags=re.S)
    assert 'href="#rerun"' not in out and 'id="rerun' not in out and not re.search(r'class="[^"]*\brrun\b', out), "a part is left"
    return out


@WINDOWS_KNOWN
def test_rerun_this_research_copies_its_prompt_by_real_clicks(tmp_path):
    """Rerun this research on the DOM fixture, drawn as rb draws an export with a clone address (R's markup: the openers
    a.rrb[href="#rerun"], the panel aside#rerun, an experiment's prompt, rb's own addresses in the text, named by
    data-rb-bundle and data-rb-page; or each in a data-fill span): with none of it drawn nothing shows; with the hub's rb-clone and rb-page metas the bar's and the Overview's
    link open the panel, Copy prompt puts the exact prompt, the hub's addresses in it, on the clipboard and says Copied
    for 2 s, the rb clone line copies alone, an experiment's Copy prompt for this experiment its own prompt, Esc, Close
    and a click outside close it with focus back on the link, keys do nothing under it, nothing is sent, a failed copy
    says "Select the text and copy it" with the prompt selected, a reader copies it too; a past version, and the hub with
    no rb-clone (or one that is no https address), show none of it; print shows the panel; with the script off the
    prompt is selectable text, whole. At 1440 and 390, light and dark."""
    html = _inline(FIXTURE.read_text(encoding="utf-8"))
    head = '<meta name="viewport"'
    assert html.count(head) == 1 and '<aside class="rerun rrun" id="rerun"' in html and '<main id="main"' in html
    pages = {}

    def page(name: str, text: str) -> None:
        at = tmp_path / f"{name}.html"
        at.write_text(text, encoding="utf-8")
        pages[name] = at.as_uri()

    page("none", _inline(_without_rerun(FIXTURE.read_text(encoding="utf-8"))))
    page("meta", html.replace(head, _CLONE_METAS + head, 1))
    page("reader", html.replace(head, _CLONE_METAS + _VIEWER.format("reader") + head, 1))
    page("past", html.replace(head, _CLONE_METAS + head, 1).replace('<main id="main"', '<div id="withheld"><p class="withheld">A past version offers no command to copy.</p></div><main id="main"', 1))
    # a run's row is class rr (shell.css), the feature's parts rrun: it stays where they go
    hubnone = re.sub(r'(<section class="view" id="runs"[^>]*>)', r'\1<div class="rr" id="rr-probe"><span class="rid">runs1</span></div>', html, count=1)
    assert 'id="rr-probe"' in hubnone
    page("hubnone", hubnone.replace(head, _VIEWER.format("reader") + head, 1))
    page("bad", html.replace(head, _VIEWER.format("owner") + '<meta name="rb-clone" content="https://x.example/$(touch${IFS}/tmp/pwn)/b.zip;id"><meta name="rb-page" content="https://x.example/p|sh&amp;&amp;x">' + head, 1))
    page("query", html.replace(head, _VIEWER.format("owner") + f'<meta name="rb-clone" content="{_BUNDLE}?t=1"><meta name="rb-page" content="{_PAGE}">' + head, 1))
    page("pagequery", html.replace(head, f'<meta name="rb-clone" content="{_BUNDLE}"><meta name="rb-page" content="{_PAGE}?view=record">' + head, 1))
    page("plain", html)
    # rb's addresses in spans that say what they are, as R first drew them: the hub's go in the spans
    spans = html
    for kind, addr in (("bundle", _RB_BUNDLE), ("page", _RB_PAGE)):
        spans = re.sub(r"(<pre[^>]*>[^<]*?|<pre class=\"cmd\">rb clone )" + re.escape(addr), lambda m: m[1] + f'<span data-fill="{kind}">{addr}</span>', spans)
    assert spans.count('data-fill="bundle"') == 3 and spans.count('data-fill="page"') == 2, (spans.count('data-fill="bundle"'), spans.count('data-fill="page"'))
    page("spans", spans.replace(head, _CLONE_METAS + head, 1))
    page("cloneonly", html.replace(head, f'<meta name="rb-clone" content="{_BUNDLE}">' + head, 1))
    pages["cloneonly"] += "?x=$(id)"
    # the hub's frame: sandbox="allow-scripts", no storage and no clipboard permission; its probe says where the opener
    # and Copy prompt are, and what the button reads, to the host, which the walk clicks through
    probe = """<script>
addEventListener('message', e => { const at = x => { if (!x) return null; const b = x.getBoundingClientRect(); return { x: b.x + Math.min(b.width / 2, 30), y: b.y + b.height / 2 }; };
  const cp = () => document.querySelector('#rerun [data-copybtn="rerun-prompt"] .cp'); let out = null;
  if (e.data === 'opener') out = at(Array.from(document.querySelectorAll('a[href="#rerun"]')).find(l => l.checkVisibility()));
  else if (e.data === 'copy') out = at(cp());
  else if (e.data === 'label') out = cp() && cp().textContent.trim();
  else if (e.data === 'focus') out = document.activeElement && (document.activeElement.className + ' ' + document.activeElement.textContent.trim());
  else if (e.data === 'parts') out = document.querySelectorAll('a[href="#rerun"][data-rerun], #rerun, .rrx, #reproduce.rrun, [data-rerun]').length;
  parent.postMessage(JSON.stringify(out), '*'); });
setTimeout(() => parent.postMessage(JSON.stringify('ready'), '*'), 1500);
</script>"""
    (tmp_path / "framed-in.html").write_text(html.replace(head, _CLONE_METAS + _VIEWER.format("owner") + head, 1).replace("</body>", probe + "</body>"), encoding="utf-8")
    (tmp_path / "framednone-in.html").write_text(html.replace("</body>", probe + "</body>"), encoding="utf-8")
    page("framednone", '<!doctype html><html><body style="margin:0"><script>addEventListener("message", e => document.body.setAttribute("data-probe", e.data));</script>'
                       '<iframe sandbox="allow-scripts" style="position:fixed;inset:0;width:100vw;height:100vh;border:0" src="framednone-in.html"></iframe></body></html>')
    page("framed", '<!doctype html><html><body style="margin:0"><script>addEventListener("message", e => document.body.setAttribute("data-probe", e.data));</script>'
                   '<iframe sandbox="allow-scripts" style="position:fixed;inset:0;width:100vw;height:100vh;border:0" src="framed-in.html"></iframe></body></html>')
    e1 = ("e1", "int8 against fp32", ["c1", "c2"])
    want = {"prompt": _rerun_prompt(_PAGE, _BUNDLE, "int8-resnet50"), "experiment": _rerun_prompt(_PAGE, _BUNDLE, "int8-resnet50", e1),
            "plain": _rerun_prompt(_RB_PAGE, _RB_BUNDLE, "int8-resnet50"), "cloneonly": _rerun_prompt(_RB_PAGE, _BUNDLE, "int8-resnet50"),
            "pagequery": _rerun_prompt(_PAGE + "?view=record", _BUNDLE, "int8-resnet50"),
            "clone": f"rb clone {_BUNDLE}", "cmds": _rerun_cmds(html, _BUNDLE, _PAGE), "card": "x-e1", "rows": 1}
    (tmp_path / "want.json").write_text(json.dumps(want), encoding="utf-8")
    shots = os.environ.get("RB_RERUN_SHOTS")
    said = harness(None, *(x for k in ("none", "meta", "reader", "past", "hubnone", "bad", "query", "pagequery", "plain", "cloneonly", "framed", "spans", "framednone") for x in (f"--{k}", pages[k])),
                   "--expect", str(tmp_path / "want.json"), *(["--shots", shots] if shots else []), timeout=420, runner="rerun.mjs")
    if os.environ.get("RB_RERUN_SAID"):
        Path(os.environ["RB_RERUN_SAID"]).write_text(json.dumps(said), encoding="utf-8")
    assert said["exit"] == 0 and said["ok"], [(c["name"], c["detail"][:600]) for c in said.get("checks") or [] if not c["pass"]] or said["stderr"][-3000:]
    names = [c["name"] for c in said["checks"]]
    for at in ("1440 light", "1440 dark", "390 light", "390 dark"):
        for n in ("rerun: without a clone address nothing of it shows", "rerun: Copy prompt puts the exact prompt on the clipboard and says Copied",
                  "rerun: the panel fits the screen and nothing runs past it", "rerun: a reader opens it and copies the prompt too",
                  "rerun: at a past version none of it is there, the metas whatever", "rerun: nothing is sent",
                  "rerun: in the hub with no rb-clone meta none of it is there, a run's row kept", "rerun: the panel offers Copy prompt once and a Copy for each command",
                  "rerun: in the hub, an rb-clone meta that is no https address shows nothing", "rerun: the top bar keeps to the screen with it",
                  "rerun: in the hub, an rb-clone meta holding a query shows nothing", "rerun: an rb-page meta holding a query goes in the prompt whole",
                  "rerun: #rerun followed after load opens the panel and keeps the address", "rerun: printed with the panel open, it prints in its place and opens again after",
                  "rerun: an address opened with shell characters in it is not put in the prompt",
                  "rerun: with rb's addresses in spans of their own, the hub's go in the spans",
                  "rerun: on a hub page a link under the title, inside the first screen, opens the panel",
                  "rerun: each command's Copy sits inside its box's corner or outside it, never on the border",
                  "rerun: each command's Copy copies the command it sits on alone"):
            assert f"{n} [{at}]" in names, (n, at)
    assert "rerun: without a script the prompt is selectable text, whole" in names
    assert "rerun: on a hub page a link under the title, inside the first screen, opens the panel [1280 light]" in names
    for at in ("1024 light", "960 light", "800 light"):
        assert f"rerun: the panel covers nothing of the top bar [{at}]" in names, at
    for n in ("rerun: in the hub's sandboxed frame Copy prompt copies the prompt itself [1440]", "rerun: in the hub's sandboxed frame focus stays on Copy prompt after it copies [390]",
              "rerun: in the hub's frame with no rb-clone meta none of it is there", "rerun: Copied is said aloud from inside the open panel [390 dark]",
              "rerun: a selection dragged out of the prompt past the panel's edge leaves it open [390 light]", "rerun: Back while the panel is open closes it [1440 dark]"):
        assert n in names, n


_RUN_ROW = re.compile(r'class="rr(?: [^"]*)?"')


def _rerun_cmds(html: str, bundle: str, page: str) -> list:
    """The panel's commands by hand, in order, as Copy gives each: rb's own addresses (data-rb-bundle, data-rb-page)
    put in the hub's place. However many rb draws."""
    import html as h
    panel = re.search(r'<aside class="rerun[^"]*" id="rerun"[^>]*data-rb-page="([^"]+)" data-rb-bundle="([^"]+)".*?</aside>', html, re.S)
    assert panel, "no Rerun panel drawn"
    own_page, own_bundle = h.unescape(panel[1]), h.unescape(panel[2])
    cmds = [h.unescape(re.sub(r"<[^>]+>", "", c)).rstrip().replace(own_bundle, bundle).replace(own_page, page)
            for c in re.findall(r'<div class="cmdbox"[^>]*><pre class="cmd">(.*?)</pre></div>', panel[0], re.S)]
    assert len(cmds) >= 2 and any(c.startswith(f"rb clone {bundle}") for c in cmds), cmds
    return cmds


def _rerun_want(html: str, bundle: str, page: str) -> dict:
    """What Copy gives on a page rb drew: its prompts as text, rb's own addresses (data-rb-bundle, data-rb-page) put in
    the hub's place; the card of the first experiment's prompt; how many runs' rows (class rr, not the feature's) it holds."""
    import html as h
    panel = re.search(r'<aside class="rerun[^"]*" id="rerun"[^>]*data-rb-page="([^"]+)" data-rb-bundle="([^"]+)"', html)
    assert panel, "no Rerun panel drawn"
    own_page, own_bundle = h.unescape(panel[1]), h.unescape(panel[2])
    text = lambda pre_id: h.unescape(re.sub(r"<[^>]+>", "", re.search(r'<pre[^>]*id="' + re.escape(pre_id) + r'"[^>]*>(.*?)</pre>', html, re.S)[1])).rstrip()
    swap = lambda t, b, p: t.replace(own_bundle, b).replace(own_page, p)
    xid = re.search(r'<pre[^>]*id="(x-[^"]+):prompt"', html)[1]
    rows = sum(1 for m in _RUN_ROW.finditer(html) if not re.search(r"\b(rrb|rrx|repro|rerun)\b", m[0]))
    return {"prompt": swap(text("rerun-prompt"), bundle, page), "experiment": swap(text(f"{xid}:prompt"), bundle, page),
            "plain": text("rerun-prompt"), "cloneonly": swap(text("rerun-prompt"), bundle, own_page), "clone": f"rb clone {bundle}", "cmds": _rerun_cmds(html, bundle, page),
            "card": xid, "rows": rows, "bar": False}


@WINDOWS_KNOWN
def test_rerun_this_research_on_an_export_as_the_hub_serves_it(a_record, tmp_path):
    """The same walk on the page rb draws: rb export --with-record --url, served as the hub serves it (the shell's script
    and the viewer, rb-clone and rb-page metas beside it); rb page (no clone address) offers none of it; a reader copies
    it too; the hub with no rb-clone shows none of it and keeps every run's row; with no script the prompt is selectable
    text. At 1440 and 390, light and dark."""
    env = _rb_env(EXAMPLE.format("owner"))
    for argv in (["page", "--out", str(tmp_path / "page.html")], ["export", "--with-record", "--url", "https://example.org/int8/", "--out", str(tmp_path / "exp")]):
        r = subprocess.run([*command(), *argv], cwd=a_record, env=env, capture_output=True, encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL)
        assert r.returncode == 0, r.stderr
    exp = (tmp_path / "exp" / "page.html").read_text(encoding="utf-8")
    want = _rerun_want(exp, _BUNDLE, _PAGE)
    script = "<script>" + SCRIPT.read_text(encoding="utf-8") + "</script>"
    pages = {"none": (tmp_path / "page.html").as_uri(), "plain": (tmp_path / "exp" / "page.html").as_uri()}
    for name, metas in (("meta", _CLONE_METAS + _VIEWER.format("owner")), ("reader", _CLONE_METAS + _VIEWER.format("reader")),
                        ("hubnone", _VIEWER.format("reader")), ("cloneonly", f'<meta name="rb-clone" content="{_BUNDLE}">' + _VIEWER.format("owner"))):
        at = tmp_path / "exp" / f"{name}.html"
        at.write_text(exp.replace("</head>", metas + script + "</head>", 1), encoding="utf-8")
        pages[name] = at.as_uri()
    (tmp_path / "want.json").write_text(json.dumps(want), encoding="utf-8")
    shots = os.environ.get("RB_RERUN_SHOTS")
    said = harness(None, *(x for k in pages for x in (f"--{k}", pages[k])), "--expect", str(tmp_path / "want.json"),
                   *(["--shots", shots] if shots else []), timeout=420, runner="rerun.mjs")
    if os.environ.get("RB_RERUN_SAID"):
        Path(os.environ["RB_RERUN_SAID"]).write_text(json.dumps(said), encoding="utf-8")
    assert said["exit"] == 0 and said["ok"], [(c["name"], c["detail"][:600]) for c in said.get("checks") or [] if not c["pass"]] or said["stderr"][-3000:]
    names = [c["name"] for c in said["checks"]]
    for at in ("1440 light", "390 dark"):
        for n in ("rerun: without a clone address nothing of it shows", "rerun: Copy prompt puts the exact prompt on the clipboard and says Copied",
                  "rerun: an experiment's Copy prompt for this experiment copies its own prompt", "rerun: a reader opens it and copies the prompt too",
                  "rerun: in the hub with no rb-clone meta none of it is there, a run's row kept", "rerun: the top bar keeps to the screen with it",
                  "rerun: each command box shows its whole line", "rerun: with no rb-clone meta there is no link under the title",
                  "rerun: each command's Copy sits inside its box's corner or outside it, never on the border",
                  "rerun: each command's Copy copies the command it sits on alone"):
            assert f"{n} [{at}]" in names, (n, at)
    for at in ("1440 light", "1280 light", "390 light", "1440 dark", "1280 dark", "390 dark"):
        assert f"rerun: on a hub page a link under the title, inside the first screen, opens the panel [{at}]" in names, at
    for at in ("1440 light", "1024 light", "960 light", "800 light"):
        assert f"rerun: the panel covers nothing of the top bar [{at}]" in names, at
    assert "rerun: without a script the prompt is selectable text, whole" in names
