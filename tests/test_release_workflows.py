"""The release denylist reaches only a job that installs nothing, and in it only the step that checks; a checkout keeps
no credentials for what runs after it; and an event's text reaches a script only through its environment. If a
workflow ever hands the secret to a job that builds, pip installs or runs the suite, or publishes before the check and
its own tests pass, this fails. The test workflow's own pins (which jobs run when, on which systems, with which
tests) are here too, and each workflow is pinned to the digest it was last read at, so a change to any workflow moves
its pins in the same commit."""
import hashlib
import json
import math
import re
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"
SECRET = "secrets.RB_RELEASE_DENYLIST"
GITHUB_OWN = ("actions/checkout@", "actions/download-artifact@")      # all a job that holds the secret may use
CHECK = "python3 -I -S .github/scripts/check-public-content.py "
TESTS = "python3 -I -S .github/scripts/test-public-content.py"


def workflows() -> dict:
    if not WORKFLOWS.is_dir():
        pytest.skip("the workflows are not beside the tests")
    # BaseLoader keeps every value as written: 'on' stays a key and 'false' a string
    return {p.name: yaml.load(p.read_text(encoding="utf-8"), Loader=yaml.BaseLoader) for p in sorted(WORKFLOWS.glob("*.yml"))}


def jobs():
    for name, flow in workflows().items():
        for job_name, job in flow["jobs"].items():
            yield name, job_name, job


def test_the_denylist_reaches_only_the_check_in_a_job_that_installs_nothing():
    holders = []
    for name, flow in workflows().items():
        assert SECRET not in json.dumps(flow.get("env", {})), name          # never workflow-wide
    for name, job_name, job in jobs():
        assert "secrets" not in job, (name, job_name)          # a called workflow, its builds included, gets none
        if SECRET not in json.dumps(job):
            continue
        holders.append(f"{name} {job_name}")
        assert SECRET not in json.dumps(job.get("env", {})), (name, job_name)
        for step in job["steps"]:
            assert step.get("uses", "").startswith(GITHUB_OWN) or "uses" not in step, (name, job_name, step)
            run = step.get("run", "")
            assert not re.search(r"\b(pip|pytest|cargo|maturin|npm|setup\.py|install)\b", run), (name, job_name, run)
            # the runner's Python, isolated: no site-packages, no .pth file, no PYTHON* variable runs code in it
            assert all(m.group(0) == "python3 -I -S" for m in re.finditer(r"\bpython\S*(?: -\S+)*", run)), (name, job_name, run)
            if SECRET in json.dumps(step):
                assert set(step) == {"name", "env", "run"} and run.startswith(CHECK), (name, job_name, step)
    assert sorted(holders) == ["build-wheels.yml scan", "public-content.yml scan", "publish.yml scan"]


def test_every_checkout_keeps_no_credentials_for_what_runs_after_it():
    for name, job_name, job in jobs():
        for step in job.get("steps", []):
            if step.get("uses", "").startswith("actions/checkout@"):
                assert step.get("with", {}).get("persist-credentials") == "false", (name, job_name)


def test_a_release_publishes_only_after_the_check_and_its_own_tests_pass():
    release = workflows()["publish.yml"]["jobs"]
    scan = release["scan"]
    assert scan["needs"] == "packages" and "scan" in release["publish"]["needs"]
    # a failed build leaves the other builds' files downloadable: the check reads them all the same
    assert scan["if"] == "${{ !cancelled() }}"
    assert all("if" not in release[job] for job in ("check", "check-windows", "check-sdist", "publish", "github-release"))
    assert sorted(release["publish"]["needs"]) == ["check", "check-sdist", "check-windows", "scan"]
    assert release["github-release"]["needs"] == "publish"
    assert [s["run"] for s in scan["steps"] if "run" in s] == [TESTS, CHECK + "--source . --artifacts dist"]
    assert [s["uses"].split("@")[0] for s in scan["steps"] if "uses" in s] == ["actions/checkout", "actions/download-artifact"]


def test_trial_packages_are_checked_after_every_build_and_a_release_leaves_it_to_its_own_check():
    trial = workflows()["build-wheels.yml"]
    scan = trial["jobs"]["scan"]
    assert sorted(scan["needs"]) == ["sdist", "wheels"] and set(trial["jobs"]) == {"wheels", "sdist", "scan"}
    # after a failed build too; called by publish.yml it gets no secret, so there it never runs
    assert scan["if"] == "${{ !cancelled() && github.event_name == 'workflow_dispatch' }}"
    assert [s["run"] for s in scan["steps"] if "run" in s] == [TESTS, CHECK + "--source . --artifacts dist"]
    assert [s["uses"].split("@")[0] for s in scan["steps"] if "uses" in s] == ["actions/checkout", "actions/download-artifact"]


def test_a_push_is_checked_over_its_whole_history_and_its_text_reaches_the_check_as_data():
    scan = workflows()["public-content.yml"]["jobs"]["scan"]
    [checkout] = [s for s in scan["steps"] if s.get("uses", "").startswith("actions/checkout@")]
    assert checkout["with"]["fetch-depth"] == "0"
    [check] = [s for s in scan["steps"] if SECRET in json.dumps(s)]
    assert check["run"] == CHECK + "--source . --history"
    assert check["env"]["SCAN_PR_TITLE"] == "${{ github.event.pull_request.title }}"
    assert check["env"]["SCAN_PR_BODY"] == "${{ github.event.pull_request.body }}"
    for name, job_name, job in jobs():                                   # no expression is written into a script
        for step in job.get("steps", []):
            assert "${{" not in step.get("run", ""), (name, job_name, step.get("name"))


def test_the_release_checks_name_tests_that_exist_and_every_release_job_stops_after_20_minutes():
    import ast
    release = workflows()["publish.yml"]
    words = release["env"]["WHEEL_TESTS"].split()
    root = WORKFLOWS.parents[1]
    paths = [w for w in words if not w.startswith("--") and "::" not in w]
    deselected = [words[i + 1] for i, w in enumerate(words) if w == "--deselect"]
    deselected += [w.split("=", 1)[1] for w in words if w.startswith("--deselect=")]   # pytest reads both spellings
    assert paths and all((root / p).is_file() for p in paths), [p for p in paths if not (root / p).is_file()]
    for node_id in deselected:
        path, name = node_id.split("::")
        assert path in paths, node_id          # a --deselect of a file the set does not run deselects nothing
        tree = ast.parse((root / path).read_text(encoding="utf-8"))
        tests = [f for f in tree.body if isinstance(f, ast.FunctionDef) and f.name == name]
        assert len(tests) == 1, node_id          # renamed or gone, the --deselect would stop matching silently
        assert not any("parametrize" in ast.dump(d) for d in tests[0].decorator_list), node_id
    for job in ("check", "check-windows", "check-sdist", "scan"):
        assert release["jobs"][job].get("timeout-minutes") == "20", job


def test_the_source_package_is_built_and_tested_from_itself():
    """check-sdist builds rb from the sdist PyPI gets, on Python 3.10, with no checkout beside it, then runs the wheel
    tests from the unpacked sdist, so a file the sdist leaves out fails there."""
    job = workflows()["publish.yml"]["jobs"]["check-sdist"]
    assert job["needs"] == "packages"
    steps = job["steps"]
    assert not any(s.get("uses", "").startswith("actions/checkout@") for s in steps)
    [python] = [s for s in steps if s.get("uses", "").startswith("actions/setup-python@")]
    assert python["with"]["python-version"] == "3.10"
    [download] = [s for s in steps if s.get("uses", "").startswith("actions/download-artifact@")]
    assert download["with"]["name"] == "sdist" and "merge-multiple" not in download["with"]
    [build] = [s["run"] for s in steps if "run" in s]
    lines = [line.split("#")[0].strip() for line in build.splitlines()]
    install = lines.index("python -m pip install --no-cache-dir dist/rabbit_brain-*.tar.gz")
    into = next(i for i, line in enumerate(lines) if line.startswith("cd ") and "sdist/rabbit_brain-*/" in line)
    assert install < into < lines.index("python -m pytest -q -n auto --reruns 2 $WHEEL_TESTS")
    assert not any("pip install" in line and ".whl" in line for line in lines)


# Each workflow as its pins were last read against: its sha256 with line endings as LF. A change to a workflow fails here
# until whoever changes it reads the pins in this file and tests/test_release_setup.py against the change, moves them
# where they need to move, and sets its digest here, in the same commit. A pin that reads the YAML checks only what
# someone thought to check; the digest makes every change an explicit read of them.
READ_AT = {
    "attribution.yml": "ff2a58e7b402315180dc9a4d6f63693a13cc8c7844b91696e8d9f490aa679366",
    "build-wheels.yml": "120af6e45ac60c66680a2d4ee46ee2eda8f6e14770c16e2bcc0a149db06e2b3c",
    "public-content.yml": "59f5cc53f2fdf6b9f1a10a1fda577cd88262704b972cf66ac6aacdce47f3c18b",
    "publish.yml": "3cb52de825b70bf955e8c4d5583bbbf852101e6ccaf39c2b42fa63643c86d78b",
    "tests.yml": "b8179a93d47542c7e9d1239bc5a494101f05273a4f7fdf5b0b8d1ec12420fff2",
}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def test_every_workflow_is_the_one_its_pins_were_read_against():
    if not WORKFLOWS.is_dir():
        pytest.skip("the workflows are not beside the tests")
    now = {p.name: digest(p) for p in sorted(WORKFLOWS.glob("*.yml"))}
    assert sorted(now) == sorted(READ_AT), "a workflow was added or removed: read it, then list it in READ_AT"
    moved = {name: d for name, d in now.items() if d != READ_AT[name]}
    assert not moved, ("changed since its pins were read: read the pins in this file and tests/test_release_setup.py "
                       f"against the change, then set READ_AT to {moved!r}")


def test_a_workflow_digest_ignores_line_endings_and_sees_one_byte(tmp_path):
    lf, crlf, other = tmp_path / "lf.yml", tmp_path / "crlf.yml", tmp_path / "other.yml"
    lf.write_bytes(b"on: push\njobs: {}\n")
    crlf.write_bytes(b"on: push\r\njobs: {}\r\n")
    other.write_bytes(b"on: push\njobs: {} \n")
    assert digest(lf) == digest(crlf) != digest(other)


# The test workflow. The browser step's run line: tests/test_shell.py, and the app harness beside it, or the browser
# checks the release check names in RELEASE_CHECK_BROWSER.
RUNNER_STEPS = ("python -m pytest -q -n auto --reruns 2 tests/test_shell.py", "python -m pytest -q -n auto --reruns 2 tests/test_shell.py tests/app/",
                "python -m pytest -q -n auto --reruns 2 $RELEASE_CHECK_BROWSER")
# when a job runs: the release check on every push, pull request and tag, the full matrix after the tag, daily and by hand
WHEN = {"release-check": "github.event_name == 'push' || github.event_name == 'pull_request' || github.event_name == 'workflow_dispatch'",
        "full": "github.event_name == 'schedule' || github.event_name == 'workflow_dispatch' || startsWith(github.ref, 'refs/tags/')"}
ROOT = WORKFLOWS.parents[1]


def read_tests_yml() -> dict:
    return workflows()["tests.yml"]


def systems_of(job: dict) -> list:
    """The systems a job runs on: its matrix's, each `include` that adds a system of its own among them."""
    if job["runs-on"] != "${{ matrix.os }}":
        return [job["runs-on"]]
    matrix = job["strategy"]["matrix"]
    return list(matrix["os"]) + [i["os"] for i in matrix.get("include", []) if i["os"] not in matrix["os"]]


def test_ci_runs_the_app_runner_on_every_system_and_never_skips_it():
    """Each job in tests.yml sets up node 22, then runs the app runner with RB_REQUIRE_BROWSER=1 in a step with no
    condition, so on Linux, macOS and Windows a missing node or Chrome fails the run instead of skipping the app runner. A
    job is chosen by when it runs alone (the release check, or the full matrix), and never allowed to fail."""
    workflow = read_tests_yml()
    systems = set()
    for name, job in workflow["jobs"].items():
        steps = job["steps"]
        node = [i for i, s in enumerate(steps) if s.get("uses", "").startswith("actions/setup-node@")]
        runs = [i for i, s in enumerate(steps) if s.get("env", {}).get("RB_REQUIRE_BROWSER") == "1"]
        assert len(node) == 1 and len(runs) == 1 and node[0] < runs[0], name
        assert int(steps[node[0]]["with"]["node-version"].split(".")[0]) >= 22, name
        step = steps[runs[0]]
        assert set(step) - {"timeout-minutes"} == {"name", "env", "run"} and step["run"] in RUNNER_STEPS, (name, step)
        if "$RELEASE_CHECK_BROWSER" in step["run"]:
            named = workflow["env"]["RELEASE_CHECK_BROWSER"].split()
            assert "tests/test_shell.py::test_the_app_runner_starts" in named and any(n.startswith("tests/app/") for n in named), named
        assert "continue-on-error" not in job and job.get("if") in WHEN.values(), name
        systems |= set(systems_of(job))
    assert {s.split("-")[0] for s in systems} == {"ubuntu", "macos", "windows"}


def test_the_release_check_is_windows_macos_and_linux_on_3_10_on_every_push_and_the_full_matrix_comes_after():
    """The release check (tests.yml's release-check job) builds rb on Windows and macOS on Python 3.12, and on Linux on
    3.10, the lowest Python the package takes, and runs what differs there on every push to main, pull request and
    release tag, within 45 minutes: rb remove's round trip among it on all three. On Linux it also imports every module
    on 3.10 and runs the tests the wheels are checked with and the SDK's time file. Every other job is the full matrix,
    which runs after the tag, daily and by hand. Each test the release check names is one that exists."""
    workflow = read_tests_yml()
    on = workflow["on"]
    assert on["push"]["branches"] == ["main"] and on["push"]["tags"] and "pull_request" in on and "workflow_dispatch" in on and on["schedule"]
    jobs = workflow["jobs"]
    check = jobs["release-check"]
    assert check["if"] == WHEN["release-check"] and sorted(systems_of(check)) == ["macos-latest", "ubuntu-latest", "windows-latest"]
    assert check["strategy"]["matrix"]["include"] == [{"os": "ubuntu-latest", "python": "3.10"}]
    assert check["timeout-minutes"] == "45"
    steps = check["steps"]
    [python] = [s for s in steps if s.get("uses", "").startswith("actions/setup-python@")]
    assert python["with"]["python-version"] == "${{ matrix.python || '3.12' }}"
    [py310] = [s for s in steps if "$RELEASE_CHECK_PY310" in s.get("run", "")]
    assert py310["if"] == "matrix.os == 'ubuntu-latest'" and int(py310["timeout-minutes"]) <= 15
    lines = py310["run"].splitlines()
    assert lines[0] == "python -m compileall -q src/rabbit_brain" and "pkgutil.iter_modules(rabbit_brain.__path__)" in lines[1]
    assert lines[-1] == "python -m pytest -q -n auto --reruns 2 $RELEASE_CHECK_PY310"
    assert all(job["if"] == WHEN["full"] for name, job in jobs.items() if name != "release-check")
    # on 3.10, every file the wheels are checked with, and the SDK's time file
    py310_named = workflow["env"]["RELEASE_CHECK_PY310"].split()
    wheel = [w for w in workflows()["publish.yml"]["env"]["WHEEL_TESTS"].split() if w.startswith("tests/") and "::" not in w]
    assert set(wheel) | {"tests/test_sdk_time.py"} <= set(py310_named), sorted(set(wheel) - set(py310_named))
    # the tests that prove plat-1b's Windows fixes run where those fixes matter (MP-1113-1145 S1), and rb remove's round
    # trip on every system (build order step 60; step 55's "rb can be tried without risk" waits for it to pass there)
    named = workflow["env"]["RELEASE_CHECK_TESTS"].split() + workflow["env"]["RELEASE_CHECK_BROWSER"].split()
    for proof in ("tests/test_surface.py::test_rb_in_a_uv_tool_dir_of_any_name_upgrades_itself_through_uv",
                  "tests/test_live.py::test_a_chrome_that_hands_its_start_on_is_waited_for_and_stopped_and_one_that_fails_says_why",
                  "tests/test_shell.py::test_a_chrome_whose_first_process_hands_its_start_on_is_used_and_stopped"):
        assert proof in named, proof
    assert "tests/test_removal.py" in named and "def test_trying_rb_and_removing_it_leaves_the_repository_as_it_was(" in (
        ROOT / "tests" / "test_removal.py").read_text(encoding="utf-8")
    for node in named + py310_named:
        path, _, test = node.partition("::")
        assert (ROOT / path).exists(), node
        assert not test or f"def {test}(" in (ROOT / path).read_text(encoding="utf-8"), node


def test_the_full_matrix_runs_once_a_day_and_by_hand():
    """The full matrix runs after each tag, once a day from the default branch's tests.yml, and by hand. GitHub stops a
    public repository's schedule after 60 days with no activity in it, which RELEASING.md dates."""
    workflow = read_tests_yml()
    [daily] = workflow["on"]["schedule"]
    minute, hour, *rest = daily["cron"].split()
    assert minute.isdigit() and hour.isdigit() and rest == ["*", "*", "*"], daily
    assert "workflow_dispatch" in workflow["on"]
    full = [name for name, job in workflow["jobs"].items() if job["if"] == WHEN["full"]]
    assert sorted(full) == ["leaving", "test", "windows"], full


# The slowest time of each job in GitHub's two full runs (43586178395, the Nov 14 2026 daily run, and 43498656381, the
# v0.8.6 tag), in minutes: windows-latest's for windows, macos-latest's for leaving (windows-latest 91.8), Python 3.13's
# for test (3.10 69.6). Both runs failed, so these are read again after the first green full run (RELEASING.md).
SLOWEST = {"release-check": 22.1, "test": 76.4, "windows": 151.7, "leaving": 94.9}


def test_each_job_stops_at_twice_the_slowest_time_github_measured_for_it():
    """Every job in tests.yml has a timeout, set at twice its slowest measured run rounded up to 5 minutes, so a hung job
    stops long before GitHub's own six hours and a slow one is never cut short."""
    jobs = read_tests_yml()["jobs"]
    assert sorted(jobs) == sorted(SLOWEST)
    for name, slowest in SLOWEST.items():
        assert int(jobs[name]["timeout-minutes"]) == math.ceil(2 * slowest / 5) * 5, name
    assert {name: jobs[name]["timeout-minutes"] for name in SLOWEST} == {
        "release-check": "45", "test": "155", "windows": "305", "leaving": "190"}
