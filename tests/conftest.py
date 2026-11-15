import os

import pytest

# rb checks PyPI for a newer rb at most once a day (AGENTS.md, Updates): never from the tests, whatever a test's own
# environment clears to look like the user's; the tests of it turn it back on
os.environ["RB_AUTO_UPDATE"] = "0"

# rabbitbrain.ai is open from 0.6.1 (hub.rs HOSTED_OPEN): no test ever reaches it. RB_TEST_HOSTED_CLOSED plays the
# service closed, for every command a test runs; the test of the open service unsets it itself
os.environ["RB_TEST_HOSTED_CLOSED"] = "1"

# Which rb the tests drive (tests/rbtest.py): RB_BIN=<path to an rb binary> names it, for every command, every SDK call
# and the screen's reads; without it, the rb installed beside this Python.


@pytest.fixture
def workdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture(scope="session")
def int8_demo(tmp_path_factory):
    """The INT8 demo record (rbtest.int8_demo), made once for the test process: read it, never write to it."""
    from rbtest import int8_demo
    return int8_demo(tmp_path_factory.mktemp("int8-demo"))


@pytest.fixture(scope="session")
def scale_record(tmp_path_factory):
    """The scale record (rbtest.scale_record), made once for the test process: read it, never write to it."""
    from rbtest import scale_record
    return scale_record(tmp_path_factory.mktemp("scale"))


# Which older rb a test applies to (PLAN-R19 C0). RB_OLD_BIN names a released rb for the tests that read it;
# `@pytest.mark.older_rb(from_="0.8.0", until="0.8.5", reason="...")` says which of them a test is for, both ends
# inclusive, each compared on as many parts as it gives ("0.5" is every 0.5.x). A test RB_OLD_BIN's version is
# outside of is deselected by name, the way -k deselects, never skipped, and the run's summary lists each with the
# version and its reason. Without RB_OLD_BIN the marker deselects nothing: the test's own skipif says RB_OLD_BIN.
#
# RB_NO_SKIP=1 (the older-rb stage sets it for every file it runs) reports a skipped test as failed (an error where
# it skipped at setup, as a skipif does), with its reason; RB_REQUIRE_OLD=1 does the same for a skip that names RB_OLD_BIN while it is unset, so no older-rb test
# passes unrun.

_OLDER_VERSION = []


def _version(said: str) -> tuple:
    return tuple(int(x) for x in said.strip().split(".") if x.isdigit())


def older_rb_version():
    """RB_OLD_BIN's version, (0, 8, 5) for rabbit-brain 0.8.5; None without RB_OLD_BIN. Asked once a process."""
    if not _OLDER_VERSION:
        old, said = os.environ.get("RB_OLD_BIN"), None
        if old:
            import subprocess
            out = subprocess.run([old, "version"], capture_output=True, text=True).stdout.split()
            said = _version(out[1]) if len(out) > 1 else ()
            if not said:
                raise pytest.UsageError(f"RB_OLD_BIN={old} says no version (rb version: {out!r})")
        _OLDER_VERSION.append(said)
    return _OLDER_VERSION[0]


def older_rb_outside(mark, version: tuple):
    """Why a test marked older_rb does not apply to `version`, or None where it does."""
    from_, until = mark.kwargs.get("from_"), mark.kwargs.get("until")
    reason = mark.kwargs.get("reason")
    if not reason:
        raise pytest.UsageError("older_rb needs reason=: why the test is for those versions alone")
    if from_ and version[:len(_version(from_))] < _version(from_):
        return f"for rb {from_} on: {reason}"
    if until and version[:len(_version(until))] > _version(until):
        return f"for rb {until} and before: {reason}"
    return None


@pytest.fixture
def older_show():
    """The older rb's own long form of rb show: `rb show --long` from 0.8.6, where `rb show <id>` became a card,
    and `rb show <id>` before it, which refuses --long."""
    version = older_rb_version()
    return ["show", "--long"] if version is None or version >= (0, 8, 6) else ["show"]


# The viewport switch (build order step 3: tests/app/test_app.py's walk splits by viewport across two Chrome slots).
# A test module marked viewport_split walks the shell in two halves: 390, the widths under 960 (one column, a card as a
# sheet), and 1280, those from 960 up (tests/app/cdp.mjs's inHalf). A test there that takes `viewport` runs once per
# half its viewports() mark names (both, unmarked), and the walk it starts takes that half's widths alone; one that
# takes none (no Chrome, or none that says a width) is the 1280 half's. RB_APP_VIEWPORT=390 or 1280 keeps one half;
# unset keeps both, so the two halves added up are exactly what runs unset.
VIEWPORTS = ("390", "1280")


def app_viewport():
    said = os.environ.get("RB_APP_VIEWPORT") or None
    if said not in (None, *VIEWPORTS):
        raise pytest.UsageError(f"RB_APP_VIEWPORT is 390 or 1280, or unset; not {said!r}")
    return said


def pytest_generate_tests(metafunc):
    if "viewport" not in metafunc.fixturenames or not metafunc.definition.get_closest_marker("viewport_split"):
        return
    mark = metafunc.definition.get_closest_marker("viewports")
    halves = mark.args if mark else VIEWPORTS
    assert halves and set(halves) <= set(VIEWPORTS), halves
    metafunc.parametrize("viewport", [h for h in VIEWPORTS if h in halves], ids=lambda h: f"w{h}")


def _viewport_of(item):
    """The half a viewport_split test is in, or None for a test outside such a module."""
    if not item.get_closest_marker("viewport_split"):
        return None
    spec = getattr(item, "callspec", None)
    return spec.params["viewport"] if spec and "viewport" in spec.params else "1280"


def pytest_configure(config):
    config.addinivalue_line("markers", "older_rb(from_=None, until=None, reason=...): the released rbs (RB_OLD_BIN) a test is for")
    config.addinivalue_line("markers", "viewport_split: the module's walks split by viewport (RB_APP_VIEWPORT)")
    config.addinivalue_line("markers", "viewports(*halves): the halves (390, 1280) a viewport_split test runs in")
    config._older_rb_deselected = []
    config._app_viewport = app_viewport()


def pytest_collection_modifyitems(config, items):
    half = config._app_viewport
    if half:
        kept = [i for i in items if _viewport_of(i) in (None, half)]
        if len(kept) < len(items):
            config.hook.pytest_deselected(items=[i for i in items if i not in kept])
            items[:] = kept
    version = older_rb_version()
    if version is None:
        return
    kept, gone = [], []
    for item in items:
        why = next((w for m in item.iter_markers("older_rb") if (w := older_rb_outside(m, version))), None)
        if why:
            gone.append(item)
            config._older_rb_deselected.append(f"{item.nodeid} (rb {'.'.join(map(str, version))}): {why}")
        else:
            kept.append(item)
    if gone:
        config.hook.pytest_deselected(items=gone)
        items[:] = kept
    if hasattr(config, "workeroutput"):
        config.workeroutput["older_rb_deselected"] = config._older_rb_deselected


@pytest.hookimpl(optionalhook=True)
def pytest_testnodedown(node, error):
    # pytest-xdist: each worker collects and deselects; the controller lists what they did, once
    seen = node.config._older_rb_deselected
    seen.extend(x for x in getattr(node, "workeroutput", {}).get("older_rb_deselected", []) if x not in seen)


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    if config._older_rb_deselected:
        terminalreporter.section("deselected for this older rb (older_rb)")
        for line in sorted(config._older_rb_deselected):
            terminalreporter.write_line(line)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if not report.skipped or hasattr(report, "wasxfail"):
        return
    no_skip = os.environ.get("RB_NO_SKIP") == "1"
    reason = report.longrepr[2] if isinstance(report.longrepr, tuple) else str(report.longrepr)
    reason = reason.removeprefix("Skipped: ")
    unrun = os.environ.get("RB_REQUIRE_OLD") == "1" and not os.environ.get("RB_OLD_BIN") and "RB_OLD_BIN" in reason
    if no_skip or unrun:
        report.outcome = "failed"
        report.longrepr = f"skipped, and {'RB_NO_SKIP=1' if no_skip else 'RB_REQUIRE_OLD=1 with no RB_OLD_BIN'} counts a skip as a failure: {reason}"
