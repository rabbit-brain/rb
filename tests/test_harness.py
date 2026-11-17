"""The shared harness (tests/rbtest.py) plays the user where a test says it does, whatever runs the tests: on GitHub's
runner, whose own variables (GITHUB_ACTIONS, GITHUB_RUN_ID) rb reads as an agent session, and in an agent's terminal.

The older_rb marker (tests/conftest.py, PLAN-R19 C0): a test that does not apply to an older rb is deselected by name
through it, never skipped, and the older-rb stage's RB_NO_SKIP=1 counts every skip as a failure.
Each pin runs pytest on a small test file beside a copy of tests/conftest.py, with a stand-in RB_OLD_BIN that says its
version as rb version does."""
import contextlib
import functools
import http.server
import json
import re
import os
import shutil
import threading
import subprocess
import sys
from pathlib import Path

import pytest

from rbtest import AGENT_VARS, Open, command, users_env

RUNNER = {"CI": "true", "GITHUB_ACTIONS": "true", "GITHUB_RUN_ID": "1"}
AGENT = {"CLAUDECODE": "1", "CLAUDE_CODE_SESSION_ID": "sess-harness-1", "CODEX_SANDBOX": "seatbelt"}


def test_the_users_environment_holds_no_agent_or_session_sign_and_keeps_a_human_actor():
    env = users_env({**RUNNER, **AGENT, "RB_ACTOR": "agent:x", "RB_MODEL": "m", "PATH": "/bin"})
    assert not set(env) & set(AGENT_VARS) and env == {"CI": "true", "PATH": "/bin"}, env
    assert users_env({"RB_ACTOR": "human:ana"}) == {"RB_ACTOR": "human:ana"}


def test_rb_open_starts_as_the_user_on_githubs_runner_and_in_an_agent_session(tmp_path, monkeypatch):
    """Step 59 (0.8.6's full run): every test that started rb open through the harness failed on GitHub with "rb open is
    the user's view and this is an agent session"."""
    root = tmp_path / "int8"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    r = subprocess.run([*command(), "init", "INT8 ResNet-50 on ImageNet", "--no-agents"], cwd=root, env=users_env(), capture_output=True, text=True,
                       stdin=subprocess.DEVNULL)
    assert r.returncode == 0, r.stderr
    for name, value in {**RUNNER, **AGENT}.items():
        monkeypatch.setenv(name, value)
    server = Open(root)
    try:
        assert server.url.startswith("http://127.0.0.1:")
    finally:
        server.close()


CONFTEST = Path(__file__).with_name("conftest.py")

TESTS = '''
import json
import os
import pytest


def test_runs():
    pass


@pytest.mark.older_rb(until="0.8.5", reason="0.8.6 reads PLAIN.md")
def test_before_plain():
    pass


@pytest.mark.older_rb(from_="0.8", reason="0.7.x reads format 1 alone")
def test_from_eight():
    pass


@pytest.mark.skipif(not os.environ.get("RB_OLD_BIN"), reason="RB_OLD_BIN names an older rb")
def test_needs_old():
    pass


def test_skips():
    pytest.skip("git is not here")


def test_long(older_show):
    print("ARGV", older_show)
'''


def pytest_run(tmp_path, *args, version=None, **env):
    shutil.copy(CONFTEST, tmp_path / "conftest.py")
    (tmp_path / "test_some.py").write_text(TESTS, encoding="utf-8")
    environ = {k: v for k, v in os.environ.items() if k not in ("RB_OLD_BIN", "RB_NO_SKIP", "RB_REQUIRE_OLD", "PYTEST_ADDOPTS")}
    if version:
        old = tmp_path / "old-rb"
        old.write_text(f"#!/bin/sh\necho rabbit-brain {version}\n", encoding="utf-8")
        old.chmod(0o755)
        environ["RB_OLD_BIN"] = str(old)
    environ.update(env)
    r = subprocess.run([sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-v", "-rA", "-s", "-p", "no:randomly", "--rootdir", str(tmp_path), *args, str(tmp_path)],
                       cwd=tmp_path, capture_output=True, text=True, env=environ)
    return r.returncode, r.stdout + r.stderr


def outcomes(out: str) -> dict:
    said = {}
    for line in out.splitlines():
        words = line.split()
        if len(words) > 1 and "::" in words[0] and words[1] in ("PASSED", "FAILED", "SKIPPED", "ERROR"):
            said.setdefault(words[0].split("::")[-1], words[1])
    return said


POSIX = pytest.mark.skipif(sys.platform == "win32", reason="the stand-in RB_OLD_BIN is a POSIX shell script")


@POSIX
def test_a_test_for_other_versions_is_deselected_by_name_and_listed(tmp_path):
    code, out = pytest_run(tmp_path, version="0.8.6")
    said = outcomes(out)
    assert "test_before_plain" not in said and said["test_from_eight"] == "PASSED" and said["test_needs_old"] == "PASSED", out
    assert "1 deselected" in out
    assert "test_some.py::test_before_plain (rb 0.8.6): for rb 0.8.5 and before: 0.8.6 reads PLAIN.md" in out
    code, out = pytest_run(tmp_path, version="0.7.1")
    said = outcomes(out)
    assert said["test_before_plain"] == "PASSED" and "test_from_eight" not in said, out
    assert "test_some.py::test_from_eight (rb 0.7.1): for rb 0.8 on: 0.7.x reads format 1 alone" in out


@POSIX
def test_without_an_older_rb_the_marker_deselects_nothing(tmp_path):
    code, out = pytest_run(tmp_path)
    said = outcomes(out)
    assert said["test_before_plain"] == "PASSED" and said["test_from_eight"] == "PASSED" and said["test_needs_old"] == "SKIPPED", out
    assert "deselected" not in out


@POSIX
def test_rb_no_skip_counts_a_skip_as_a_failure_with_its_reason(tmp_path):
    code, out = pytest_run(tmp_path, version="0.8.6")
    assert outcomes(out)["test_skips"] == "SKIPPED" and code == 0, out
    code, out = pytest_run(tmp_path, version="0.8.6", RB_NO_SKIP="1")
    said = outcomes(out)
    assert said["test_skips"] == "FAILED" and code == 1, out
    assert "RB_NO_SKIP=1 counts a skip as a failure: git is not here" in out
    assert "test_before_plain" not in said, "a deselected test is not a skip"


@POSIX
def test_rb_require_old_with_no_older_rb_fails_the_tests_that_need_one(tmp_path):
    code, out = pytest_run(tmp_path, RB_REQUIRE_OLD="1")
    said = outcomes(out)
    assert said["test_needs_old"] == "ERROR" and said["test_skips"] == "SKIPPED" and code == 1, out
    assert "RB_REQUIRE_OLD=1 with no RB_OLD_BIN counts a skip as a failure: RB_OLD_BIN names an older rb" in out


@POSIX
def test_the_older_rbs_long_show_is_its_own(tmp_path):
    for version, argv in (("0.8.5", "['show']"), ("0.8.6", "['show', '--long']"), ("0.9.0", "['show', '--long']")):
        code, out = pytest_run(tmp_path, "-k", "test_long", version=version)
        assert f"ARGV {argv}" in out, (version, out)


@POSIX
def test_a_marker_without_its_reason_stops_the_run(tmp_path):
    global TESTS
    kept, TESTS = TESTS, "import pytest\n\n@pytest.mark.older_rb(until='0.8.5')\ndef test_x():\n    pass\n"
    try:
        code, out = pytest_run(tmp_path, version="0.8.6")
    finally:
        TESTS = kept
    assert code != 0 and "older_rb needs reason=" in out


@POSIX
def test_under_xdist_the_controller_lists_what_its_workers_deselected(tmp_path):
    code, out = pytest_run(tmp_path, "-n", "2", version="0.8.6")
    assert code == 0 and "test_some.py::test_before_plain (rb 0.8.6): for rb 0.8.5 and before: 0.8.6 reads PLAIN.md" in out, out


# ---------------------------------------------------------------- the viewport switch (build order step 3)
# tests/app/test_app.py's walks split by viewport across two Chrome slots: RB_APP_VIEWPORT=390 keeps the widths under
# 960, 1280 those from 960 up (tests/conftest.py, tests/app/cdp.mjs's inHalf); unset, both. These read the split without
# Chrome: what each half collects, that each walk the file starts takes its half, and the runners' widths.
APP = Path(__file__).parent / "app"
ROOT = Path(__file__).resolve().parents[1]


def collected(viewport) -> list:
    environ = {k: v for k, v in os.environ.items() if k not in ("RB_APP_VIEWPORT", "PYTEST_ADDOPTS")}
    if viewport is not None:
        environ["RB_APP_VIEWPORT"] = viewport
    r = subprocess.run([sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-o", "addopts=", "--collect-only", "-q", str(APP / "test_app.py")],
                       cwd=ROOT, capture_output=True, text=True, env=environ)
    return r.returncode, [ln for ln in r.stdout.splitlines() if "::" in ln], r.stdout + r.stderr


def test_unset_the_walk_collects_exactly_both_halves_and_no_test_twice():
    (c0, both, o0), (c1, narrow, o1), (c2, wide, o2) = collected(None), collected("390"), collected("1280")
    assert c0 == c1 == c2 == 0, (o0, o1, o2)
    assert narrow and wide and len(both) == len(set(both)), both
    assert set(both) == set(narrow) | set(wide), sorted(set(both) ^ (set(narrow) | set(wide)))
    assert not set(narrow) & set(wide), sorted(set(narrow) & set(wide))
    # a half's tests say which half they are; a test with no width (no Chrome, or none that walks a width) is 1280's
    import re
    half = lambda n: re.search(r"[\[-]w(390|1280)[\]-]", n)
    assert all(half(n) and half(n)[1] == "390" for n in narrow), narrow
    assert all(not half(n) or half(n)[1] == "1280" for n in wide), wide
    # every test that walks at 390 walks at 1280 too, but the five that walk only from 960 up
    bare = lambda ids: {re.sub(r"\[w\d+\]$|w\d+-|-w\d+", "", n.split("::")[-1]) for n in ids if half(n)}
    assert bare(narrow) <= bare(wide), sorted(bare(narrow) - bare(wide))
    only_wide = {n.split("[")[0] for n in bare(wide) - bare(narrow)}
    assert only_wide == {"test_a_reader_s_page_says_no_your_and_offers_no_action", "test_the_owner_reads_your_call_where_the_export_says_open_calls",
                         "test_a_reader_s_share_link_names_no_rb_command_in_a_sentence", "test_the_owner_s_export_composes_a_call_in_the_hub_s_frame",
                         "test_the_overview_inspector_shows_its_empty_line"}, only_wide


def test_the_viewport_switch_takes_390_or_1280_and_nothing_else():
    code, ids, out = collected("800")
    assert code != 0 and not ids and "RB_APP_VIEWPORT is 390 or 1280, or unset; not '800'" in out, out


def test_every_walk_test_app_starts_takes_its_half():
    """Each harness() call in tests/app/test_app.py passes the test's viewport, but the harness's own two checks (a
    page without the script, a stub runner), which walk no width; and no runner it drives walks a width outside its
    half: every loop over widths goes through cdp.mjs's widths()."""
    import ast
    tree = ast.parse((APP / "test_app.py").read_text(encoding="utf-8"))
    bare = []
    for fn in (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)):
        for call in (n for n in ast.walk(fn) if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "harness"):
            if not any(k.arg == "viewport" for k in call.keywords):
                bare.append(fn.name)
    assert sorted(set(bare)) == ["test_a_walk_has_time_for_a_slow_machine_and_stops_soon_when_it_hangs", "test_the_harness_fails_a_page_without_the_script"], bare
    import re
    for runner in ("app.mjs", "v2.mjs", "viewer.mjs", "rerun.mjs", "rail.mjs", "p12.mjs", "hub.mjs"):
        js = (APP / runner).read_text(encoding="utf-8")
        loose = re.findall(r"for \(const w of (?!widths\()[^)]*\)", js)
        assert not loose, (runner, loose)


def test_cdp_s_halves_part_every_width_at_960():
    node = shutil.which("node")
    if not node:
        pytest.skip("no node on PATH")
    probe = f"import({str((APP / 'cdp.mjs').as_uri())!r}).then(m => console.log(JSON.stringify(m.widths([390, 800, 959, 960, 1024, 1280, 1440]))))"
    said = {}
    for half in ("", "390", "1280", "800"):
        r = subprocess.run([node, "--input-type=module", "-e", probe], capture_output=True, text=True, env={**os.environ, "RB_APP_VIEWPORT": half})
        said[half] = (r.returncode, r.stdout.strip())
    assert said[""] == (0, "[390,800,959,960,1024,1280,1440]"), said
    assert said["390"] == (0, "[390,800,959]") and said["1280"] == (0, "[960,1024,1280,1440]"), said
    assert said["800"][0] == 2, said


# cdp.mjs's clickEl and waitFor (round cdp-1, from F's tabs-1): a click measured while its target still moves (a sheet
# sliding in, a card's entrance) landed where the target was, not where it stopped, and a wait that timed out returned
# null with nothing said, so the step after it failed with no word of where it was
def test_a_click_lands_where_its_target_stops_and_a_named_wait_throws_naming_its_step(tmp_path):
    """clickEl presses only once its target's box is the same over two drawn frames, and waitFor given a step name
    throws on its timeout, naming the step, what it last read and what `state` reads of the page; with no name it
    returns null, as its callers expect. Here a button slides for 1.5 s from the moment the page shows it, and a link is
    clicked on a page whose scripts are off, where the settle once waited on a frame callback that never ran."""
    from test_shell import _cdp_script, _node, _runner
    page = tmp_path / "slide.html"
    page.write_text("<!doctype html><style>#b{position:fixed;left:200px;top:100px;width:60px;height:30px;"
                    "transition:transform 1.5s linear}html.go #b{transform:translateX(-180px)}</style><button id=b>Go</button>"
                    "<script>window.down=null;window.got=false;"
                    "b.addEventListener('mousedown',()=>{window.down={moving:document.getAnimations().length,x:Math.round(b.getBoundingClientRect().x)}});"
                    "b.addEventListener('click',()=>{window.got=true});"
                    "requestAnimationFrame(()=>requestAnimationFrame(()=>document.documentElement.classList.add('go')));</script>", encoding="utf-8")
    off = tmp_path / "off.html"
    off.write_text("<!doctype html><a id=a href=#x>go</a>", encoding="utf-8")
    script = _cdp_script(tmp_path / "slide.mjs", """
await cdp.run(async () => {
  const page = await (await cdp.launchChrome()).page();
  await page.goto(%s);
  await page.waitFor("document.documentElement.classList.contains('go')", 10000, 'the slide starts');
  const click = await page.clickEl("document.getElementById('b')");
  let said = null;
  try { await page.waitFor('false', 300, 'a state that never comes', '1 + 1'); } catch (e) { said = e.message; }
  const bare = await page.waitFor('false', 300);
  const off = await (await cdp.launchChrome()).page();
  await off.noScript(true);
  await off.goto(%s);
  const t0 = Date.now();
  const late = await Promise.race([off.clickEl("document.getElementById('a')").then(() => false), cdp.sleep(15000).then(() => true)]);
  const noscript = { late, ms: Date.now() - t0, hash: await off.ev('location.hash') };
  return { down: await page.ev('window.down'), got: await page.ev('window.got'), said, bare, hit: click && click.hit, noscript };
}, { timeoutMs: 120000 });
""" % (json.dumps(page.as_uri()), json.dumps(off.as_uri())))
    code, said, err = _runner(_node(), script, env=dict(os.environ))
    assert code == 0 and said["ok"], err
    assert said["down"] == {"moving": 0, "x": 20} and said["got"] is True, said
    assert said["hit"] == "BUTTON#b Go", said
    assert said["said"] == "a state that never comes: not so after 0.3 s (last false); the page then: 2", said
    assert said["bare"] is None, said
    # with the page's scripts off (v2.mjs's queue button without the script), the settle still ends: no frame callback
    assert said["noscript"]["late"] is False and said["noscript"]["hash"] == "#x" and said["noscript"]["ms"] < 5000, said["noscript"]


# round mac-tabs: GitHub's macOS runner drew the card's sheet at its start for over 300 ms, so clickEl's three equal
# reads 50 ms apart took a sheet that had not started moving for one that had stopped, and pressed Rule at 499 px in a
# 390 px sheet; nothing pressed it again once the sheet came in
def test_a_click_waits_for_a_target_that_has_not_started_moving_and_presses_again_where_it_went(tmp_path):
    """clickEl waits out a slide still in its delay (the target drawn where it starts, its box the same read after
    read, its transition running), and presses where it stops; given the state the click brings, when that has not come
    and the target moved away under the press, it presses once more where the target went, and says both presses."""
    from test_shell import _cdp_script, _node, _runner
    late = tmp_path / "late.html"
    late.write_text("<!doctype html><style>#b{position:fixed;left:200px;top:100px;width:60px;height:30px;"
                    "transition:transform .6s linear .8s}html.go #b{transform:translateX(-180px)}</style><button id=b>Go</button>"
                    "<script>window.down=null;window.got=false;"
                    "b.addEventListener('mousedown',()=>{window.down={moving:document.getAnimations().length,x:Math.round(b.getBoundingClientRect().x)}});"
                    "b.addEventListener('click',()=>{window.got=true});"
                    "requestAnimationFrame(()=>requestAnimationFrame(()=>document.documentElement.classList.add('go')));</script>", encoding="utf-8")
    away = tmp_path / "away.html"
    away.write_text("<!doctype html><style>#b{position:fixed;left:200px;top:100px;width:60px;height:30px}"
                    "#b.moved{left:20px;top:300px}</style><button id=b>Go</button>"
                    "<script>window.got=false;window.downs=0;"
                    "b.addEventListener('mousedown',()=>{if(!window.downs++)b.classList.add('moved')});"
                    "b.addEventListener('click',()=>{window.got=true});</script>", encoding="utf-8")
    script = _cdp_script(tmp_path / "late.mjs", """
await cdp.run(async () => {
  const page = await (await cdp.launchChrome()).page();
  await page.goto(%s);
  await page.waitFor("document.documentElement.classList.contains('go')", 10000, 'the slide is asked for');
  const late = await page.clickEl("document.getElementById('b')");
  const first = { down: await page.ev('window.down'), got: await page.ev('window.got'), said: late && late.said };
  await page.goto(%s);
  const again = await page.clickEl("document.getElementById('b')", { until: 'window.got', againMs: 1000 });
  return { first, again: { got: await page.ev('window.got'), downs: await page.ev('window.downs'), said: again && again.said,
    presses: again && (again.presses || [again.at]).map(p => [Math.round(p.x), Math.round(p.y)]) } };
}, { timeoutMs: 120000 });
""" % (json.dumps(late.as_uri()), json.dumps(away.as_uri())))
    code, said, err = _runner(_node(), script, env=dict(os.environ))
    assert code == 0 and said["ok"], err
    # pressed once the slide had run its course, where the button stopped (200 - 180 = 20), with nothing still moving
    assert said["first"]["down"] == {"moving": 0, "x": 20} and said["first"]["got"] is True and said["first"]["said"] == "50, 115", said
    # the first press moved the button away before the button came up; the second found it where it went
    assert said["again"]["got"] is True and said["again"]["downs"] == 2, said
    assert said["again"]["presses"] == [[230, 115], [50, 315]] and said["again"]["said"] == "230, 115, then at 50, 315", said


def test_a_click_presses_again_only_where_the_target_still_shows_and_never_after_its_state_came(tmp_path):
    """A second press is for a target that moved and is still shown: not one the first press hid (its box then reads
    0, 0, and a press there lands on whatever is in the corner), not one whose state came while it settled (a second
    press would undo it); a target gone before it could be pressed gives null at once, not a press 10 s later; a
    colour moving on what the target sits in is not waited for, a cover still sliding off a target that never moved
    is, and a target that never stops is said so."""
    from test_shell import _cdp_script, _node, _runner
    pages = {
        # the first press hides the button (it moves and is hidden); a link sits in the top left corner
        "hide": "<a id=corner href=#corner style='position:fixed;left:0;top:0;width:40px;height:40px'>x</a>"
                "<button id=b style='position:fixed;left:200px;top:100px;width:60px;height:30px'>Go</button>"
                "<script>window.n=0;b.addEventListener('mousedown',()=>{window.n++;b.style.left='20px';b.hidden=true})</script>",
        # the first press moves the button 5 px and its state comes 3.2 s later, after againMs, before the last check ends
        "slow": "<button id=b style='position:fixed;left:200px;top:100px;width:60px;height:30px'>Go</button>"
                "<script>window.n=0;window.on=false;b.addEventListener('click',()=>{window.n++;b.style.left='205px';"
                "setTimeout(()=>{window.on=!window.on},3200)})</script>",
        # the button is removed 200 ms into its slide
        "gone": "<style>#b{position:fixed;left:200px;top:100px;width:60px;height:30px;transition:transform 2s linear}"
                "html.go #b{transform:translateX(-180px)}</style><button id=b>Go</button>"
                "<script>requestAnimationFrame(()=>requestAnimationFrame(()=>{document.documentElement.classList.add('go');setTimeout(()=>b.remove(),200)}))</script>",
        # a 30 s colour change on the body the button sits in
        "colour": "<style>body{transition:background-color 30s linear}body.go{background-color:#888}</style>"
                  "<button id=b style='position:fixed;left:200px;top:100px;width:60px;height:30px'>Go</button>"
                  "<script>requestAnimationFrame(()=>requestAnimationFrame(()=>document.body.classList.add('go')))</script>",
        # a still button under a cover that slides off it over 0.8 s, as a closing sheet over the menu's button
        "cover": "<style>#c{position:fixed;left:150px;top:80px;width:200px;height:80px;background:#ccc;transition:transform .8s linear}"
                 "html.go #c{transform:translateX(400px)}</style>"
                 "<button id=b style='position:fixed;left:200px;top:100px;width:60px;height:30px'>Go</button><div id=c></div>"
                 "<script>window.got=false;b.addEventListener('click',()=>{window.got=true});"
                 "requestAnimationFrame(()=>requestAnimationFrame(()=>document.documentElement.classList.add('go')))</script>",
        # a button moved by the page on every frame
        "jiggle": "<button id=b style='position:fixed;left:200px;top:100px;width:60px;height:30px'>Go</button>"
                  "<script>let i=0;(function f(){b.style.left=(200+(i++%7))+'px';requestAnimationFrame(f)})()</script>",
    }
    urls = {}
    for name, body in pages.items():
        (tmp_path / f"{name}.html").write_text("<!doctype html>" + body, encoding="utf-8")
        urls[name] = (tmp_path / f"{name}.html").as_uri()
    script = _cdp_script(tmp_path / "again.mjs", """
const U = %s, B = "document.getElementById('b')";
await cdp.run(async () => {
  const page = await (await cdp.launchChrome()).page();
  const out = {};
  await page.goto(U.hide);
  let r = await page.clickEl(B, { until: 'false', againMs: 500 });
  out.hide = { said: r && r.said, n: await page.ev('window.n'), hash: await page.ev('location.hash') };
  await page.goto(U.slow);
  r = await page.clickEl(B, { until: 'window.on', againMs: 3000 });
  await cdp.sleep(1500);
  out.slow = { said: r && r.said, n: await page.ev('window.n'), on: await page.ev('window.on') };
  await page.goto(U.gone);
  await page.waitFor("document.documentElement.classList.contains('go')", 10000, 'the slide starts');
  let t0 = Date.now();
  r = await page.clickEl(B);
  out.gone = { r, ms: Date.now() - t0 };
  await page.goto(U.colour);
  await page.waitFor("document.body.classList.contains('go')", 10000, 'the colour starts');
  t0 = Date.now();
  r = await page.clickEl(B);
  out.colour = { said: r && r.said, ms: Date.now() - t0 };
  await page.goto(U.cover);
  await page.waitFor("document.documentElement.classList.contains('go')", 10000, 'the cover starts to slide');
  r = await page.clickEl(B);
  out.cover = { said: r && r.said, hit: r && r.hit, got: await page.ev('window.got') };
  await page.goto(U.jiggle);
  out.jiggle = await page.stopped(B, { timeoutMs: 1500 });
  return out;
}, { timeoutMs: 120000 });
""" % json.dumps(urls))
    code, said, err = _runner(_node(), script, env=dict(os.environ))
    assert code == 0 and said["ok"], err
    assert said["hide"] == {"said": "230, 115", "n": 1, "hash": ""}, said["hide"]
    assert said["slow"]["said"] == "230, 115" and said["slow"]["n"] == 1 and said["slow"]["on"] is True, said["slow"]
    assert said["gone"]["r"] is None and said["gone"]["ms"] < 5000, said["gone"]
    assert said["colour"]["said"] == "230, 115" and said["colour"]["ms"] < 3000, said["colour"]
    assert said["cover"] == {"said": "230, 115", "hit": "BUTTON#b Go", "got": True}, said["cover"]
    assert said["jiggle"]["stopped"] is False and said["jiggle"]["gone"] is False and said["jiggle"]["frames"] is True, said["jiggle"]


def test_a_click_reads_its_point_again_as_it_presses_and_brings_back_a_target_scrolled_out_of_view(tmp_path):
    """Round l1 (run 43713175764's rail check, after a scroll into view): a target the page scrolls out of its
    scroller's view after the click is asked for is still drawn on the page, clipped, and a press at its place lands
    beside it; clickEl reads the point once more as it presses, and a target its scroller no longer shows is brought into
    view again, waited for and pressed there. A target the page scrolls (smoothly or at once) while the click waits is
    pressed where it is, whenever the scroll comes."""
    from test_shell import _cdp_script, _node, _runner
    box = ("<style>#s{position:fixed;left:50px;top:50px;width:400px;height:300px;overflow:auto%s}#pad,#pad2{height:1500px}"
           "#b{display:block;width:120px;height:30px}</style><div id=s><div id=pad></div><button id=b>Go</button><div id=pad2></div></div>"
           "<script>window.got=false;window.down=null;b.addEventListener('mousedown',ev=>{const r=b.getBoundingClientRect(),v=s.getBoundingClientRect();"
           "window.down={on:ev.clientX>=r.left&&ev.clientX<=r.right&&ev.clientY>=r.top&&ev.clientY<=r.bottom,shown:r.top>=v.top&&r.bottom<=v.top+s.clientHeight}});"
           "b.addEventListener('click',()=>{window.got=true});%s</script>")
    # the first scroll into view is taken away 20 ms later, once: the button is then above its scroller's top, clipped
    away = box % ("", "let once=true;s.addEventListener('scroll',()=>{if(once){once=false;setTimeout(()=>{s.scrollTop+=200},20)}});")
    pages = {"away": away, "smooth": box % (";scroll-behavior:smooth", ""), "instant": box % ("", "")}
    urls = {}
    for name, body in pages.items():
        (tmp_path / f"{name}.html").write_text("<!doctype html>" + body, encoding="utf-8")
        urls[name] = (tmp_path / f"{name}.html").as_uri()
    script = _cdp_script(tmp_path / "scroll.mjs", """
const U = %s, B = "document.getElementById('b')";
await cdp.run(async () => {
  const p = await (await cdp.launchChrome()).page();
  const one = async (u, pre) => { await p.goto(u); await p.settle({ quietMs: 50 }); if (pre) await p.ev(pre);
    const r = await p.clickEl(B); await new Promise(z => setTimeout(z, 1200));
    return { got: await p.ev('window.got'), down: await p.ev('window.down'), hit: r && r.hit, said: r && r.said }; };
  const out = { away: await one(U.away) };
  // the page scrolls the shown button by 200 px, smoothly or at once, d ms after the click is asked for
  for (const kind of ['smooth', 'instant']) for (const d of [10, 20, 30, 150, 300])
    out[kind + d] = await one(U[kind], `s.style.scrollBehavior='auto';b.scrollIntoView({block:'center'});s.style.scrollBehavior='';setTimeout(()=>{s.scrollTop+=200},${d});true`);
  return out;
}, { timeoutMs: 180000 });
""" % json.dumps(urls))
    code, said, err = _runner(_node(), script, env=dict(os.environ), timeout=240)
    assert code == 0 and said["ok"], err
    # brought back into view and pressed there, once: the press lands on the button, which its scroller shows whole as it
    # goes down (round r20f: what the press hits, not the point, which the layout decides)
    away = said["away"]
    assert away["got"] is True and away["down"] == {"on": True, "shown": True} and away["hit"] == "BUTTON#b Go", json.dumps(said)
    assert ", then at " not in away["said"] and "not the target" not in away["said"], json.dumps(said)
    for k in [f"{kind}{d}" for kind in ("smooth", "instant") for d in (10, 20, 30, 150, 300)]:
        assert said[k]["got"] is True, f"{k}: {json.dumps(said)}"


def test_a_click_whose_target_the_page_moves_as_it_is_pressed_is_pressed_again_where_it_is(tmp_path):
    """Round r67a (MP-1116-1626's flake: instant150 got no press at a load of about 26): the last read and the press
    are two steps, and a scroll the page makes between them sends the press to where the target was. Here the page
    scrolls on the press's own first mouse move, so the race comes every time: the press that landed beside the target
    is said, and the target is read and pressed again where it is."""
    from test_shell import _cdp_script, _node, _runner
    body = ("<style>#s{position:fixed;left:50px;top:50px;width:400px;height:300px;overflow:auto}#pad,#pad2{height:1500px}"
            "#b{display:block;width:120px;height:30px}</style><div id=s><div id=pad></div><button id=b>Go</button><div id=pad2></div></div>"
            "<script>window.got=false;window.downs=0;b.addEventListener('mousedown',()=>{window.downs++});"
            "b.addEventListener('click',()=>{window.got=true});let once=true;"
            "addEventListener('mousemove',()=>{if(once){once=false;s.scrollTop+=200}},true);</script>")
    (tmp_path / "moves.html").write_text("<!doctype html>" + body, encoding="utf-8")
    script = _cdp_script(tmp_path / "moves.mjs", """
const U = %s, B = "document.getElementById('b')";
await cdp.run(async () => {
  const p = await (await cdp.launchChrome()).page();
  await p.goto(U); await p.settle({ quietMs: 50 });
  await p.ev("s.style.scrollBehavior='auto';b.scrollIntoView({block:'center'});true");
  const r = await p.clickEl(B); await new Promise(z => setTimeout(z, 500));
  return { got: await p.ev('window.got'), downs: await p.ev('window.downs'), said: r && r.said, n: r && r.presses.length };
}, { timeoutMs: 120000 });
""" % json.dumps((tmp_path / "moves.html").as_uri()))
    code, said, err = _runner(_node(), script, env=dict(os.environ))
    assert code == 0 and said["ok"], err
    assert said["got"] is True and said["downs"] == 1 and said["n"] == 2, json.dumps(said)
    assert said["said"] == "80, 200 on DIV#pad2 , not the target, then at 80, 200", json.dumps(said)


def test_a_click_says_what_each_press_landed_on_when_it_was_not_the_target(tmp_path):
    """Round r19 (MP-1116-0723's later items): a press that lands on something other than its target (here a still
    cover that takes the first press, then goes while the button moves) says what it landed on, in its own words and in
    the failure of any step after it, so a second press that passes cannot hide a first that hit another tab, and a
    walk that fails later names that press, not only its own step."""
    from test_shell import _cdp_script, _node, _runner
    pages = {
        "decoy": "<button id=b style='position:fixed;left:200px;top:100px;width:60px;height:30px'>Go</button>"
                 "<div id=d style='position:fixed;left:180px;top:90px;width:120px;height:50px;background:#ccc'>cover</div>"
                 "<script>window.got=false;b.addEventListener('click',()=>{window.got=true});"
                 "d.addEventListener('click',()=>{d.remove();b.style.left='20px';b.style.top='300px'})</script>",
    }
    urls = {}
    for name, body in pages.items():
        (tmp_path / f"{name}.html").write_text("<!doctype html>" + body, encoding="utf-8")
        urls[name] = (tmp_path / f"{name}.html").as_uri()
    script = _cdp_script(tmp_path / "landed.mjs", """
const U = %s, B = "document.getElementById('b')";
const later = async page => { try { await page.waitFor('false', 300, 'a later step'); return null; } catch (e) { return e.message; } };
await cdp.run(async () => {
  const page = await (await cdp.launchChrome()).page();
  const out = {};
  await page.goto(U.decoy);
  const r = await page.clickEl(B, { until: 'window.got', againMs: 1000 });
  out.decoy = { said: r && r.said, got: await page.ev('window.got'), later: await later(page) };
  return out;
}, { timeoutMs: 120000 });
""" % json.dumps(urls))
    code, said, err = _runner(_node(), script, env=dict(os.environ))
    assert code == 0 and said["ok"], err
    assert said["decoy"]["got"] is True, said["decoy"]
    assert said["decoy"]["said"] == "230, 115 on DIV#d cover, not the target, then at 50, 315", said["decoy"]
    assert said["decoy"]["later"] == ("a later step: not so after 0.3 s (last false); before it, "
                                      "a click on BUTTON#b Go pressed 230, 115 on DIV#d cover"), said["decoy"]

def test_a_click_presses_only_where_its_target_shows_in_the_viewport(tmp_path):
    """Round r19 (MP-1116-0723's later items): a target half out of the viewport is pressed where it shows, never
    outside the viewport, and one wholly out of view (fixed, so no scroll brings it back) is not pressed at all, and
    the failure of a step after it says so."""
    from test_shell import _cdp_script, _node, _runner
    pages = {
        # 40 of its 100 px left of the viewport
        "half": "<button id=b style='position:fixed;left:-40px;top:100px;width:100px;height:30px'>Go</button>"
                "<script>window.got=false;window.x=null;b.addEventListener('mousedown',e=>{window.x=e.clientX});"
                "b.addEventListener('click',()=>{window.got=true})</script>",
        # wholly left of the viewport
        "off": "<button id=b style='position:fixed;left:-200px;top:100px;width:60px;height:30px'>Go</button>",
    }
    urls = {}
    for name, body in pages.items():
        (tmp_path / f"{name}.html").write_text("<!doctype html>" + body, encoding="utf-8")
        urls[name] = (tmp_path / f"{name}.html").as_uri()
    script = _cdp_script(tmp_path / "landed.mjs", """
const U = %s, B = "document.getElementById('b')";
const later = async page => { try { await page.waitFor('false', 300, 'a later step'); return null; } catch (e) { return e.message; } };
await cdp.run(async () => {
  const page = await (await cdp.launchChrome()).page();
  const out = {};
  await page.goto(U.half);
  let r = await page.clickEl(B);
  out.half = { said: r && r.said, got: await page.ev('window.got'), x: await page.ev('window.x'), later: await later(page) };
  await page.goto(U.off);
  const t0 = Date.now();
  r = await page.clickEl(B);
  out.off = { r, ms: Date.now() - t0, later: await later(page) };
  return out;
}, { timeoutMs: 120000 });
""" % json.dumps(urls))
    code, said, err = _runner(_node(), script, env=dict(os.environ))
    assert code == 0 and said["ok"], err
    assert said["half"] == {"said": "30, 115", "got": True, "x": 30,
                            "later": "a later step: not so after 0.3 s (last false)"}, said["half"]
    assert said["off"]["r"] is None and said["off"]["ms"] < 15000, said["off"]
    assert said["off"]["later"] == ("a later step: not so after 0.3 s (last false); before it, "
                                    "a click on BUTTON#b Go was not pressed: -170, 115 was out of view for 10 s"), said["off"]

def test_a_click_presses_a_target_that_an_overflow_box_it_escapes_does_not_clip(tmp_path):
    """Round r19b (T's t19b 7): an overflow box clips only what it holds for layout. A target placed absolutely under
    a positioned box above it, or fixed, is drawn outside an overflow-hidden box between them, and is pressed where it
    shows (r-19 judged it clipped there and pressed nothing: the Files sheet at 390); one placed absolutely by the
    overflow-hidden box itself, outside it, is clipped there and not pressed."""
    from test_shell import _cdp_script, _node, _runner
    clicks = ("<script>window.got=false;window.x=null;b.addEventListener('mousedown',e=>{window.x=[e.clientX,e.clientY]});"
              "b.addEventListener('click',()=>{window.got=true})</script>")
    box = "overflow:hidden;height:30px;width:300px;margin:0;padding:0"
    pages = {
        # the sheet's shape: positioned nav, an overflow-hidden list in it, the sheet placed by the nav, under the list
        "absolute": "<nav style='position:relative;margin:0'><ul style='" + box + "'><li style='list-style:none'>row"
                    "<div style='position:absolute;left:0;top:80px;width:200px'><button id=b style='width:100px;height:30px;margin:0'>Go</button>"
                    "</div></li></ul></nav>" + clicks,
        # fixed: only the viewport clips it
        "fixed": "<div style='" + box + "'><button id=b style='position:fixed;left:10px;top:200px;width:100px;height:30px;margin:0'>Go</button></div>" + clicks,
        # placed by the overflow-hidden box itself, above its top where no scroll reaches: clipped, never pressed
        "clipped": "<div style='position:relative;margin-top:300px;" + box + "'><button id=b style='position:absolute;left:10px;top:-100px;"
                   "width:100px;height:30px;margin:0'>Go</button></div>" + clicks,
    }
    urls = {}
    for name, body in pages.items():
        (tmp_path / f"{name}.html").write_text("<!doctype html><body style='margin:0'>" + body, encoding="utf-8")
        urls[name] = (tmp_path / f"{name}.html").as_uri()
    script = _cdp_script(tmp_path / "escape.mjs", """
const U = %s, B = "document.getElementById('b')";
await cdp.run(async () => {
  const page = await (await cdp.launchChrome()).page();
  await page.viewport(390, 844);
  const out = {};
  for (const k of Object.keys(U)) {
    await page.goto(U[k]);
    const r = await page.clickEl(B);
    out[k] = { said: r && r.said, got: await page.ev('window.got'), x: await page.ev('window.x') };
  }
  return out;
}, { timeoutMs: 120000 });
""" % json.dumps(urls))
    code, said, err = _runner(_node(), script, env=dict(os.environ))
    assert code == 0 and said["ok"], err
    # the press as said, and the mousedown the page saw there (its clientX a whole number, the press's point may not be)
    for k, at in (("absolute", (30, 95)), ("fixed", (40, 215))):
        assert said[k]["said"] == "%d, %d" % at and said[k]["got"] is True, (k, said[k])
        assert all(abs(a - b) <= 1 for a, b in zip(said[k]["x"], at)), (k, said[k])
    assert said["clipped"] == {"said": None, "got": False, "x": None}, said["clipped"]


def test_rb_app_frame_stall_holds_each_animation_at_its_first_frame(tmp_path):
    """RB_APP_FRAME_STALL=1500 holds every animation and transition a page starts at its first frame for 1.5 s, still
    running, as GitHub's macOS runner drew the card's sheet at its start; unset, nothing is held, and a value under 0
    stops the runner."""
    from test_shell import _cdp_script, _node, _runner
    page = tmp_path / "go.html"
    page.write_text("<!doctype html><style>#b{transition:transform 1s linear}html.go #b{transform:translateX(100px)}</style>"
                    "<button id=b>Go</button>", encoding="utf-8")
    script = _cdp_script(tmp_path / "stall.mjs", """
await cdp.run(async () => {
  const page = await (await cdp.launchChrome()).page();
  await page.goto(%s);
  await page.ev("document.documentElement.classList.add('go'); true");
  await cdp.sleep(500);
  return { at: await page.ev("Math.round(document.getElementById('b').getBoundingClientRect().x)"), stall: cdp.FRAME_STALL,
    state: await page.ev("(document.getAnimations()[0] || {}).playState || null") };
}, { timeoutMs: 120000 });
""" % json.dumps(page.as_uri()))
    node = _node()
    said = {}
    for ms in ("", "1500"):
        code, said[ms], err = _runner(node, script, env={**os.environ, "RB_APP_FRAME_STALL": ms})
        assert code == 0 and said[ms]["ok"], err
    # the button starts at 8 (the body's margin): unheld it is well on its way after 0.5 s, held it has moved a few
    # frames' worth at most (the clock stops once Chrome says the transition started, which a loaded box says later:
    # 13 in the l1 dry run, beside three other Chromes), and it still reads as running
    assert said[""]["stall"] == 0 and said[""]["at"] > 30, said
    assert said["1500"]["stall"] == 1500 and 8 <= said["1500"]["at"] <= 20 and said["1500"]["state"] == "running", said
    bad = subprocess.run([node, script], env={**os.environ, "RB_APP_FRAME_STALL": "-1"}, capture_output=True, text=True, timeout=60)
    assert bad.returncode == 2 and "RB_APP_FRAME_STALL is a number of ms from 0" in bad.stderr, bad.stderr


def test_rb_app_first_frame_holds_a_documents_first_frames(tmp_path):
    """RB_APP_FIRST_FRAME=800 holds every animation frame a document asks for until 800 ms after it starts, as GitHub's
    macOS runner drew its first page's first frame after the walk read it as ready (round r61r); a frame cancelled while
    held never comes; unset, frames come as drawn, and a value under 0 stops the runner. The hold counts from the
    document's start, and so does the held frame's time (round r20f: read from DOMContentLoaded, a slow first page took
    its parse time off the margin); the page here takes 300 ms to parse, as a slow first page on a macOS runner may."""
    from test_shell import _cdp_script, _node, _runner
    page = tmp_path / "frames.html"
    page.write_text("<!doctype html><script>window.said = {}; const slow = performance.now() + 300; while (performance.now() < slow) {}"
                    " document.addEventListener('DOMContentLoaded', () => {"
                    " const t = performance.now(); requestAnimationFrame(() => { said.first = Math.round(performance.now() - t);"
                    " said.fromStart = Math.round(performance.now()); });"
                    " const c = requestAnimationFrame(() => { said.cancelled = true; }); cancelAnimationFrame(c); });</script><p>frames</p>",
                    encoding="utf-8")
    script = _cdp_script(tmp_path / "first.mjs", """
await cdp.run(async () => {
  const page = await (await cdp.launchChrome()).page();
  await page.goto(%s);
  await page.waitFor('window.said.first !== undefined', 20000);
  await cdp.sleep(300);
  return { said: await page.ev('window.said'), hold: cdp.FIRST_FRAME };
}, { timeoutMs: 120000 });
""" % json.dumps(page.as_uri()))
    node = _node()
    said = {}
    for ms in ("", "800"):
        code, said[ms], err = _runner(node, script, env={**os.environ, "RB_APP_FIRST_FRAME": ms})
        assert code == 0 and said[ms]["ok"], err
    assert said[""]["hold"] == 0 and said[""]["said"]["first"] < 700 and "cancelled" not in said[""]["said"], said
    # held: 800 ms after the document started at the soonest (performance.now() counts from the navigation, before the
    # hold's own start; 5 ms for a timer's rounding)
    assert said["800"]["hold"] == 800 and said["800"]["said"]["fromStart"] >= 795 and "cancelled" not in said["800"]["said"], said
    bad = subprocess.run([node, script], env={**os.environ, "RB_APP_FIRST_FRAME": "-1"}, capture_output=True, text=True, timeout=60)
    assert bad.returncode == 2 and "RB_APP_FIRST_FRAME is a number of ms from 0" in bad.stderr, bad.stderr


# a folder served on 127.0.0.1 for the length of a with block, its address given: a walk that writes localStorage in
# one document and reads it in the next loads its page over http, since Chrome lost such a write between two file://
# documents 5 times in 20 (round r20d), and 0 in 20 over http
@contextlib.contextmanager
def _served(folder):
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=str(folder)))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}/"
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_a_guide_seen_before_the_page_runs_never_opens_however_late_the_first_frame(tmp_path):
    """A page's guide decides at DOMContentLoaded whether to start and starts on the next frame, as rb's shell does.
    Put away once the page reads ready, it opens after the walk when the first frame is late (RB_APP_FIRST_FRAME, round
    r61r); page.guideSeen() marks it seen before the page's DOMContentLoaded listeners run, in every document after, so it never opens,
    until the function it gives back stops it. Each case starts with no guide key (round r20f: the cases share a profile
    and an origin, and "seen first" read the key "after ready" had set, so it passed without guideSeen)."""
    from test_shell import _cdp_script, _node, _runner
    page = tmp_path / "guide.html"
    page.write_text("<!doctype html><script>document.addEventListener('DOMContentLoaded', () => {"
                    " document.documentElement.classList.add('ready');"
                    " if (localStorage.getItem('rb.guide.1') !== null) return;"
                    " requestAnimationFrame(() => { document.getElementById('g').hidden = false; }); });</script>"
                    "<div id=g class=guide hidden>guide</div>", encoding="utf-8")
    with _served(tmp_path) as base:
        script = _cdp_script(tmp_path / "seen.mjs", """
const SKIP = `(() => { try { localStorage.setItem('rb.guide.1', 'ended'); } catch (e) {} return true; })()`;
await cdp.run(async () => {
  const b = await cdp.launchChrome();
  const out = {};
  for (const how of ['after ready', 'seen first', 'stopped']) {
    const page = await b.page();
    if (how !== 'after ready') { const off = await page.guideSeen(); if (how === 'stopped') await off(); }
    // every case starts with no guide key: the cases share a profile and an origin, and the case before set it
    await page.goto(%s);
    await page.ev("localStorage.removeItem('rb.guide.1'); true");
    await page.goto(%s);
    await page.waitFor(`document.documentElement.classList.contains('ready')`, 20000);
    await page.ev(SKIP);
    await cdp.sleep(1500);
    out[how] = await page.ev(`!document.getElementById('g').hidden`);
  }
  return out;
}, { timeoutMs: 120000 });
""" % ((json.dumps(base + page.name),) * 2))
        code, said, err = _runner(_node(), script, env={**os.environ, "RB_APP_FIRST_FRAME": "800"})
    assert code == 0 and said["ok"], err
    # stopped (the function guideSeen gives back), the next document marks nothing: a guide forgotten opens again
    assert said["after ready"] is True and said["seen first"] is False and said["stopped"] is True, said


def test_a_guide_seen_leaves_storage_alone_until_the_page_s_own_dom_content_loaded(tmp_path):
    """page.guideSeen() marks the guide at DOMContentLoaded, by a listener it adds before the page's own, and touches
    no storage before then: written as each document started (round r61s's first try), it lost the theme the document
    before had just stored, and app.mjs's "dark wins over a light system and is kept" read null 10 times in 30. The
    page's script reads no guide key while it is parsed, its own DOMContentLoaded listener reads it ended, and a key
    already there is kept as it is."""
    from test_shell import _cdp_script, _node, _runner
    page = tmp_path / "early.html"
    page.write_text("<!doctype html><script>window.early = localStorage.getItem('rb.guide.1');"
                    " document.addEventListener('DOMContentLoaded', () => { window.atload = localStorage.getItem('rb.guide.1'); });</script>"
                    "INT8 ResNet-50", encoding="utf-8")
    with _served(tmp_path) as base:
        script = _cdp_script(tmp_path / "early.mjs", """
await cdp.run(async () => {
  const page = await (await cdp.launchChrome()).page();
  await page.guideSeen();
  await page.goto(%s);
  const first = await page.ev('[window.early, window.atload]');
  await page.ev("localStorage.setItem('rb.guide.1', 'seen'); true");
  await page.goto(%s);
  return { first, kept: await page.ev('[window.early, window.atload]') };
}, { timeoutMs: 120000 });
""" % ((json.dumps(base + page.name),) * 2))
        code, said, err = _runner(_node(), script, env=dict(os.environ))
    assert code == 0 and said["ok"], err
    assert said["first"] == [None, "ended"] and said["kept"] == ["seen", "seen"], said


def test_the_rail_walk_folds_its_card_on_esc_when_the_first_frame_comes_late(tmp_path):
    """Run 43910176640 (public main a4bcfdb, GitHub's macOS): rail.mjs's "Esc folds it back [1280 light]" read "closed"
    late. The walk's first page drew its first frame after the walk read it as ready and put the guide away; the guide
    opened then, and the walk's Esc ended the guide, not the card. With the first frames held 1 s (RB_APP_FIRST_FRAME)
    the walk passes: the guide is seen before the page's DOMContentLoaded (page.guideSeen())."""
    from test_shell import _node, _runner
    rec = tmp_path / "rec"
    rec.mkdir()
    env = {k: v for k, v in os.environ.items() if k not in AGENT_VARS and not k.startswith("RB_TEST_SHELL")}
    env.update(RB_TELEMETRY="0", DO_NOT_TRACK="1", RB_TEST_NOW="2026-10-19T09:41:00.000+00:00", RB_TEST_SHELL="1",
               RB_TEST_SHELL_MODEL=str(ROOT / "tests" / "fixtures" / "shell" / "int8-demo.owner.view.json"))
    subprocess.run(["git", "init", "-q"], cwd=rec, check=True)
    for argv in (["init", "INT8 ResNet-50 on ImageNet", "--no-agents"], ["page", "--out", str(tmp_path / "page.html")]):
        r = subprocess.run([*command(), *argv], cwd=rec, env=env, capture_output=True, encoding="utf-8", errors="replace",
                           stdin=subprocess.DEVNULL)
        assert r.returncode == 0, r.stderr
    walk = {k: v for k, v in os.environ.items() if k not in ("RB_APP_VIEWPORT", "RB_APP_FRAME_STALL")}
    walk.update(RB_APP_VIEWPORT="1280", RB_APP_FIRST_FRAME="1000")
    code, said, err = _runner(_node(), str(APP / "rail.mjs"), "--url", (tmp_path / "page.html").as_uri(), env=walk, timeout=600)
    failed = [c for c in said.get("checks") or said.get("results") or [] if not c.get("pass")]
    assert code == 0 and said["ok"], "\n".join([*(f"{c['name']}: {c['detail']}" for c in failed), err[-3000:]])


def test_the_card_tabs_walk_clicks_its_tabs_when_the_first_frame_comes_late(int8_demo, tmp_path, monkeypatch):
    """Run 43910303264 (public main a4bcfdb, GitHub's macOS, job leaving): test_shell's card-tabs walk pressed Rule at
    390 and hit P#guide-p for 60 s. It set the guide ended once the first page had loaded, then went to '#c-c2', which
    only changes the address, so the first document stayed; its first frame came late and the guide started on it, over
    the tabs. With the first frames held 1 s (RB_APP_FIRST_FRAME) the walk passes: the guide is seen before each
    document's DOMContentLoaded (page.guideSeen())."""
    import test_shell
    monkeypatch.setenv("RB_APP_FIRST_FRAME", "1000")
    test_shell.test_a_card_opens_as_tabs_with_its_result_drawn(int8_demo, tmp_path)


# a walk that sets the guide ended from a page already loaded (an ev, or a demo walk's eval step): a line that sets an
# item and names the key, as setItem('rb.guide.1', ...) or a list of keys it sets
_GUIDE_SET = re.compile(r"""setItem\(.*['"]rb\.guide\.1['"]|['"]rb\.guide\.1['"].*setItem\(""")


def test_no_walk_marks_the_guide_seen_only_once_its_page_has_loaded():
    """Rounds r61r and r61s (GitHub's macOS, public main a4bcfdb): the shell's guide decides at DOMContentLoaded and
    starts on the next frame, so a walk that sets rb.guide.1 once the page has loaded, and stays in that document (it
    reads on, or only changes the address), can have the guide open over it when the first frame comes late. Every
    browser walk under tests/ and tools/ that sets it so also marks it seen before each document's DOMContentLoaded
    (page.guideSeen()), or is one of the walks below that load the page again after setting it."""
    reloads = {
        # its lead steps load the page again after the eval, before a view's address (r61s)
        "tools/demo/agent_page.mjs",
        # a stand-in page with no guide, loaded again after its lead eval
        "tests/test_demo_record.py",
    }
    own = {"tests/app/cdp.mjs", "tests/test_harness.py"}   # guideSeen itself, and the stand-ins that pin it
    files = sorted(p for d in ("tests", "tools") for ext in ("*.py", "*.mjs") for p in (ROOT / d).rglob(ext)
                   if "__pycache__" not in p.parts)
    late, seen_by_reload = [], []
    for p in files:
        rel = p.relative_to(ROOT).as_posix()
        text = p.read_text(encoding="utf-8", errors="replace")
        lines = [n for n, line in enumerate(text.splitlines(), 1) if _GUIDE_SET.search(line) and "<script>" not in line]
        if not lines or rel in own:
            continue
        if rel in reloads:
            seen_by_reload.append(rel)
        elif "guideSeen(" not in text:
            late.append(f"{rel}:{lines[0]}")
    assert not late, "sets rb.guide.1 once the page has loaded, with no page.guideSeen() first: " + ", ".join(late)
    assert sorted(seen_by_reload) == sorted(reloads), seen_by_reload


def test_rb_app_cpu_throttle_slows_every_page_it_opens(tmp_path):
    """RB_APP_CPU_THROTTLE=4 runs every page cdp.mjs opens four times slower (Emulation.setCPUThrottlingRate), so a walk
    can be read as a slow runner reads it; unset, no page is slowed, and a value under 1 stops the runner."""
    from test_shell import _cdp_script, _node, _runner
    script = _cdp_script(tmp_path / "slow.mjs", """
await cdp.run(async () => {
  const b = await cdp.launchChrome();
  const page = await b.page();
  return { asked: b.asked('Emulation.setCPUThrottlingRate'), rate: cdp.CPU_THROTTLE };
}, { timeoutMs: 120000 });
""")
    node = _node()
    said = {}
    for rate in ("", "4"):
        code, said[rate], err = _runner(node, script, env={**os.environ, "RB_APP_CPU_THROTTLE": rate})
        assert code == 0 and said[rate]["ok"], err
    assert (said[""]["asked"], said[""]["rate"]) == (False, 1), said
    assert (said["4"]["asked"], said["4"]["rate"]) == (True, 4), said
    bad = subprocess.run([node, script], env={**os.environ, "RB_APP_CPU_THROTTLE": "0.5"}, capture_output=True, text=True, timeout=60)
    assert bad.returncode == 2 and "RB_APP_CPU_THROTTLE is a number from 1" in bad.stderr, bad.stderr


# cdp.mjs's media (round media-1): media() sent the colour scheme alone, so every other feature fell back to the
# machine's own setting. GitHub's macOS image turns reduced motion on, so the guide read as still where it should glide
def test_every_page_s_media_is_said_whole_so_no_walk_reads_the_machine_s_settings(tmp_path):
    """A page cdp.mjs opens, and each media() call, sends prefers-reduced-motion, prefers-contrast, forced-colors and
    prefers-reduced-transparency with the colour scheme, each as asked or else at no preference, so a Chrome whose
    machine has motion turned down (RB_APP_MACHINE=reduced-motion, the switch that stands for it) still reads no
    preference; media({ motion: 'reduce' }) reads reduce for that call alone, and a call without it reads no-preference."""
    from test_shell import _cdp_script, _node, _runner
    script = _cdp_script(tmp_path / "media.mjs", """
const Q = `[matchMedia('(prefers-reduced-motion: reduce)').matches, matchMedia('(prefers-contrast: more)').matches,
  matchMedia('(forced-colors: active)').matches, matchMedia('(prefers-color-scheme: dark)').matches]`;
await cdp.run(async () => {
  const b = await cdp.launchChrome();
  const p = await b.page();
  const opened = await p.ev(Q);
  await p.media({ type: '', scheme: 'dark' });
  const dark = await p.ev(Q);
  const sent = b.lastSent('Emulation.setEmulatedMedia');
  await p.media({ motion: 'reduce' });
  const reduce = await p.ev(Q);
  const reduceSent = b.lastSent('Emulation.setEmulatedMedia');
  await p.media({ type: '', scheme: 'light' });
  const back = await p.ev(Q);
  let bad = null;
  try { await p.media({ motoin: 'reduce' }); } catch (e) { bad = e.message; }
  return { opened, dark, sent, reduce, reduceSent, back, bad, machine: cdp.MACHINE };
}, { timeoutMs: 120000 });
""")
    node = _node()
    said = {}
    for machine in ("", "reduced-motion,high-contrast"):
        code, said[machine], err = _runner(node, script, env={**os.environ, "RB_APP_MACHINE": machine})
        assert code == 0 and said[machine]["ok"], err
    for machine, got in reversed(said.items()):     # the machine with motion turned down first: what a runner reads
        # [reduced motion, more contrast, forced colours, dark], as the page reads them
        reads = {k: got[k] for k in ("opened", "dark", "reduce", "back")}
        assert reads == {"opened": [False, False, False, False], "dark": [False, False, False, True],
                         "reduce": [True, False, False, True], "back": [False, False, False, False]}, (machine, reads)
        assert got["sent"] == {"media": "", "features": [
            {"name": "prefers-color-scheme", "value": "dark"}, {"name": "prefers-reduced-motion", "value": "no-preference"},
            {"name": "prefers-contrast", "value": "no-preference"}, {"name": "forced-colors", "value": "none"},
            {"name": "prefers-reduced-transparency", "value": "no-preference"}]}, (machine, got)
        assert {"name": "prefers-reduced-motion", "value": "reduce"} in got["reduceSent"]["features"], (machine, got)
        assert got["bad"] and "no feature motoin" in got["bad"], (machine, got)
    assert said["reduced-motion,high-contrast"]["machine"] == ["reduced-motion", "high-contrast"], said
    bad = subprocess.run([node, str(tmp_path / "media.mjs")], env={**os.environ, "RB_APP_MACHINE": "slow"}, capture_output=True, text=True, timeout=60)
    assert bad.returncode == 2 and "RB_APP_MACHINE is a comma list of" in bad.stderr, bad.stderr


# ---------------------------------------------------------------- every MCP tool takes a call (round mcp-1)
# MP-1115-1552 was undone because a new tool refused every call (its command words lacked one suffix) and no test called
# it. These call each tool rb mcp lists, as tests/fixtures/mcp_calls.py builds the call from the tool's own schema.

sys.path.insert(0, str(Path(__file__).parent / "fixtures"))
import mcp_calls  # noqa: E402

ASSETS = Path(__file__).resolve().parents[1] / "rust" / "src" / "assets" / "mcp.json"


@pytest.fixture(scope="module")
def every_tool(tmp_path_factory):
    """rb mcp on a small INT8 record, its tools as it lists them, and the ids the record drew."""
    from rbtest import Mcp
    root = tmp_path_factory.mktemp("mcp-every-tool") / "int8"
    ids = mcp_calls.record(root, users_env())
    server = Mcp("claude-code", root=root)
    answer = lambda method, params: server.handle({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})  # noqa: E731
    answer("initialize", {"protocolVersion": "2025-06-18", "clientInfo": {"name": "claude-code", "version": "2.1.0"}})
    tools = answer("tools/list", {})["result"]["tools"]
    yield server, answer, tools, ids
    server.close()


def test_every_tool_s_schema_is_the_command_line_it_stands_for(every_tool):
    """Each tool in assets/mcp.json, as rb mcp lists it, reads as rb's declared command line: its words name a command,
    each option a flag of it that takes the same kind of value (a switch, one value, a list), its positionals in order,
    and every argument the command requires is one the schema requires."""
    from rbtest import cli_parser
    _, _, tools, _ = every_tool
    assets = json.loads(ASSETS.read_text(encoding="utf-8"))
    parser = cli_parser.build_parser()
    said = mcp_calls.schemas_listed(tools, assets)
    for t in assets["tools"]:
        said += mcp_calls.against_the_parser(t, parser)
    assert not said, "\n".join(said)


def test_every_tool_rb_mcp_lists_takes_a_valid_call(every_tool):
    """Each tool, called once with valid arguments built from its own schema (dry_run where it takes one, so nothing is
    written; a read where it does not), does its job; and each argument its schema lists, added to that call in turn,
    is one the command line parses. A failure names the tool."""
    _, answer, tools, ids = every_tool
    assets = {t["name"]: t for t in json.loads(ASSETS.read_text(encoding="utf-8"))["tools"]}
    refused, unparsed = [], []
    for listed in tools:
        tool = assets.get(listed["name"])
        assert tool, f"{listed['name']}: listed by rb mcp, not in assets/mcp.json"
        call = mcp_calls.arguments(tool, ids)
        said = mcp_calls.refusal(answer("tools/call", {"name": tool["name"], "arguments": call})["result"])
        if said:
            refused.append(f"{tool['name']} {json.dumps(call)}: {said}")
    for listed in tools:
        tool = assets[listed["name"]]
        for added, call in mcp_calls.one_of_each(tool, ids):
            said = mcp_calls.not_parsed(answer("tools/call", {"name": tool["name"], "arguments": call})["result"])
            if said:
                unparsed.append(f"{tool['name']} with {added}: {said}")
    assert not refused and not unparsed, "\n".join(["refused a valid call:", *refused, "could not parse an argument it lists:", *unparsed])
