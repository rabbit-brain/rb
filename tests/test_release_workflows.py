"""The release denylist reaches only a job that installs nothing, and in it only the step that checks; a checkout keeps
no credentials for what runs after it; and an event's text reaches a script only through its environment. If a
workflow ever hands the secret to a job that builds, pip installs or runs the suite, or publishes before the check and
its own tests pass, this fails."""
import json
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
    assert all("if" not in release[job] for job in ("check", "check-windows", "publish", "github-release"))
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
