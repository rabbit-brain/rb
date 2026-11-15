"""The shared harness (tests/rbtest.py) plays the user where a test says it does, whatever runs the tests: on GitHub's
runner, whose own variables (GITHUB_ACTIONS, GITHUB_RUN_ID) rb reads as an agent session, and in an agent's terminal.

The older_rb marker (tests/conftest.py, PLAN-R19 C0): a test that does not apply to an older rb is deselected by name
through it, never skipped, and the older-rb stage's RB_NO_SKIP=1 counts every skip as a failure.
Each pin runs pytest on a small test file beside a copy of tests/conftest.py, with a stand-in RB_OLD_BIN that says its
version as rb version does."""
import json
import os
import shutil
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
