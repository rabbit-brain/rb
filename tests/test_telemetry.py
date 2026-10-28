"""Exercise the production transport and interface hooks against an owned loopback receiver.

These processes deliberately do not inherit the test/CI opt-out variables: each has an isolated
configuration directory and an explicit loopback destination, never a live analytics project.
A command hands a due delivery to a detached `rb telemetry flush` and returns at once, so a test
that needs that delivery waits for the receiver; an explicit `rb telemetry flush` answers synchronously.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from rbtest import AGENT_VARS, CI_VARIABLES, ROOT, command


pytestmark = pytest.mark.skipif(not shutil.which("git"), reason="research fixtures use git")


@pytest.fixture
def receiver():
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            self.server.requests.append((self.path, json.loads(body)))
            self.server.release.wait(self.server.delay)
            self.server.answered.append(self.path)
            self.send_response(self.server.response_code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":1}')

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    server.requests = []
    # Automatic delivery attempts retain their queue until a test explicitly accepts a batch.
    server.response_code = 500
    # Each answer is held until `release` is set, `delay` seconds at most; `answered` grows as each one goes out.
    server.delay = 0.0
    server.release = threading.Event()
    server.answered = []
    server.url = f"http://127.0.0.1:{server.server_port}"
    worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
    worker.start()
    try:
        yield server
    finally:
        server.release.set()
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


@pytest.fixture
def trial(tmp_path, receiver):
    root = tmp_path / "private-research-marker"
    root.mkdir()
    config = tmp_path / "user-settings"
    gitconfig = tmp_path / "gitconfig"
    gitconfig.write_text("[user]\nname = private-name-marker\nemail = private-email-marker@example.test\n", encoding="utf-8")
    env = dict(os.environ)
    exact = {
        *CI_VARIABLES, *AGENT_VARS, "DO_NOT_TRACK", "PYTEST_CURRENT_TEST",
        "RB_CONFIG_DIR", "RB_POSTHOG_KEY", "RB_POSTHOG_HOST", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
        "http_proxy", "https_proxy", "all_proxy",
    }
    for name in list(env):
        if name in exact or name.startswith(("RB_TEST_", "RB_TELEMETRY", "CODEX_", "CLAUDE_CODE_", "CURSOR_", "GROK_")):
            env.pop(name)
    env.update({
        "RB_CONFIG_DIR": str(config), "RB_POSTHOG_KEY": "phc_localtestonly", "RB_POSTHOG_HOST": receiver.url,
        "RB_PYTHON": sys.executable, "GIT_CONFIG_GLOBAL": str(gitconfig), "GIT_CONFIG_NOSYSTEM": "1",
        "NO_PROXY": "127.0.0.1", "no_proxy": "127.0.0.1",
    })

    class Trial:
        def __init__(self):
            self.root, self.config, self.env = root, config, env

        def raw(self, *args, overrides=None):
            return subprocess.run([*command(), *args], cwd=root, env={**env, **(overrides or {})},
                                  stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30)

        def cli(self, *args, overrides=None, code=0):
            run = self.raw(*args, "--json", overrides=overrides)
            assert run.returncode == code, (args, run.stdout, run.stderr)
            return json.loads(run.stdout)

        def telemetry(self, action, **kwargs):
            return self.cli("telemetry", action, **kwargs)["data"]["telemetry"]

        def pending(self):
            payload = self.telemetry("preview")["payload"]
            return payload["batch"] if payload else []

        def state(self):
            return json.loads((config / "telemetry" / "state.json").read_text(encoding="utf-8"))

        def project(self):
            self.cli("init", "private-project-title-marker", "--no-agents")
            self.cli("experiment", "add", "private-experiment-title-marker", "--id", "private-exp-marker")

        def bridge(self, requests):
            run = subprocess.run([*command(), "bridge"], input="".join(json.dumps(r) + "\n" for r in requests),
                                 cwd=root, env=env, capture_output=True, text=True, timeout=30)
            assert run.returncode == 0, run.stderr
            answers = [json.loads(line) for line in run.stdout.splitlines()]
            assert len(answers) == len(requests)
            assert all(a["ok"] for a in answers), answers
            return answers

        def mcp(self, *calls):
            messages = [{"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                         "clientInfo": {"name": "private-client-marker", "version": "1"}}}]
            messages += [{"jsonrpc": "2.0", "id": i, "method": "tools/call", "params": {"name": name, "arguments": arguments}}
                         for i, (name, arguments) in enumerate(calls, start=1)]
            run = subprocess.run([*command(), "mcp"], input="".join(json.dumps(m) + "\n" for m in messages),
                                 cwd=root, env=env, capture_output=True, text=True, timeout=30)
            assert run.returncode == 0, run.stderr
            answers = [json.loads(line) for line in run.stdout.splitlines()]
            assert len(answers) == len(messages) and not any(a["result"].get("isError") for a in answers[1:]), answers
            return answers

        def wait(self, ready, timeout=30.0):
            """Poll until `ready()` holds: a background delivery has no end to wait on."""
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if ready():
                    return True
                time.sleep(0.05)
            return ready()

    return Trial()


def names(events):
    return [e["event"] for e in events]


def drain(fd, into):
    """Read what a program writes to a terminal so the terminal never fills up."""
    while True:
        try:
            chunk = os.read(fd, 65536)
        except OSError:
            return
        if not chunk:
            return
        into.append(chunk)


def test_default_off_never_creates_identity_queue_or_network(trial, receiver):
    assert trial.telemetry("status")["enabled"] is False
    assert trial.telemetry("preview")["payload"] is None
    trial.cli("version")
    trial.cli("version", overrides={"RB_TELEMETRY": "1"})
    assert trial.telemetry("flush")["sent"] == 0
    trial.project()
    assert not trial.config.exists()
    assert receiver.requests == []


def test_consent_controls_are_local_and_disabling_resets_identity(trial, receiver):
    trial.telemetry("enable")
    before = trial.pending()
    assert names(before) == ["rb_telemetry_enabled"]
    identity = before[0]["distinct_id"]
    trial.telemetry("status")
    trial.telemetry("enable")
    assert trial.pending() == before
    assert receiver.requests == []

    trial.telemetry("disable")
    assert not (trial.config / "telemetry" / "state.json").exists()
    assert trial.pending() == []
    trial.telemetry("enable")
    after = trial.pending()
    assert len(after) == 1 and after[0]["distinct_id"] != identity
    assert receiver.requests == []


@pytest.mark.parametrize("agent", [{"CLAUDECODE": "1"}, {"AI_AGENT": "some-agent"}, {"CODEX_SANDBOX": "seatbelt"}])
def test_an_agent_session_cannot_turn_analytics_on(trial, receiver, agent):
    refused = trial.cli("telemetry", "enable", overrides=agent, code=2)["errors"][0]
    assert refused["code"] == "E_USAGE" and "agent session" in refused["message"] and "their own terminal" in refused["fix"]
    assert not trial.config.exists()
    trial.telemetry("enable")
    assert trial.cli("telemetry", "disable", overrides=agent)["data"]["telemetry"]["enabled"] is False   # turning it off is anyone's
    assert not (trial.config / "telemetry" / "state.json").exists()


def test_wire_payload_contains_usage_but_never_research_or_identity(trial, receiver):
    receiver.response_code = 200
    trial.telemetry("enable")
    trial.project()
    trial.cli("run", "add", "private-exp-marker", "private_metric_marker=123.456789",
              "--command", "python private_script_marker.py --email private-email-marker@example.test")
    trial.cli("status")
    trial.cli("context", overrides={"AI_AGENT": "private-agent-marker", "RB_SESSION": "private-session-marker"})
    # every command returned at once; the background delivery leaves nothing behind
    assert trial.wait(lambda: receiver.requests and trial.pending() == []), trial.pending()
    assert {path for path, _ in receiver.requests} == {"/batch/"}
    assert {body["api_key"] for _, body in receiver.requests} == {"phc_localtestonly"}
    batch = [e for _, body in receiver.requests for e in body["batch"]]
    assert len({e["uuid"] for e in batch}) == len(batch)

    assert {"rb_telemetry_enabled", "rb_project_initialized", "rb_record_ready", "rb_record_inspected", "rb_context_delivered"} <= set(names(batch))
    operations = [e["properties"] for e in batch if e["event"] == "rb_operation_completed"]
    assert any(p["command"] == "run add" and p["surface"] == "cli" and p["actor"] == "human_inferred" for p in operations)
    assert any(p["command"] == "context" and p["actor"] == "agent" for p in operations)
    encoded = json.dumps([body for _, body in receiver.requests])
    for marker in ("private-", "private_", "123.456789", str(trial.root), "@example.test"):
        assert marker not in encoded
    for event in batch:
        assert set(event) == {"event", "uuid", "timestamp", "distinct_id", "properties"}
        props = event["properties"]
        assert props["$ip"] == "0.0.0.0"
        assert props["$geoip_disable"] is True
        assert props["$process_person_profile"] is False
        assert set(props) <= {"schema_version", "rb_version", "os", "arch", "surface", "actor", "cohort",
                              "$ip", "$geoip_disable", "$process_person_profile", "command", "outcome", "duration_bucket", "route"}


@pytest.mark.parametrize("switch", [
    {"DO_NOT_TRACK": "1"}, {"RB_TELEMETRY": "0"}, {"RB_TELEMETRY": "no"}, {"RB_TELEMETRY": " 0"}, {"RB_TELEMETRY": "disabled"},
    {"CI": "1"}, {"TEAMCITY_VERSION": "2024.1"}, {"CODEBUILD_BUILD_ARN": "arn:aws:codebuild:us-east-1:1:build/x"},
    {"bamboo_planKey": "PROJ-PLAN"}, {"BUILDER_OUTPUT": "/builder/outputs"},
])
def test_privacy_switches_suppress_new_events_and_explicit_flush(trial, receiver, switch):
    trial.telemetry("enable")
    before = trial.pending()
    trial.cli("version", overrides=switch)
    assert trial.telemetry("flush", overrides=switch)["sent"] == 0
    assert trial.pending() == before
    assert trial.state()["last_attempt"] == 0     # no delivery was started either
    assert receiver.requests == []


def test_the_tests_clear_every_variable_rb_takes_for_ci():
    """The tests that play the user's terminal clear what rb takes for CI: the list they share is rb's own."""
    source = (ROOT / "rust" / "src" / "telemetry.rs").read_text(encoding="utf-8")
    declared = re.search(r"const CI_VARIABLES: \[&str; \d+\] = \[(.*?)\];", source, re.S)
    assert declared and tuple(re.findall(r'"([^"]+)"', declared.group(1))) == CI_VARIABLES


def test_only_an_explicit_yes_leaves_rb_telemetry_on_and_a_generic_build_number_is_no_ci(trial, receiver):
    trial.telemetry("enable")
    for overrides in ({"RB_TELEMETRY": "1"}, {"RB_TELEMETRY": " YES "}, {"RB_TELEMETRY": "on"}, {"BUILD_NUMBER": "12"}):
        before = len(trial.pending())
        trial.cli("version", overrides=overrides)
        assert len(trial.pending()) == before + 1, overrides


def test_a_command_line_that_names_no_command_or_did_not_parse_uploads_nothing(trial, receiver):
    receiver.delay = 8.0                  # the delivery's answer is held until released, within its 10 s budget
    trial.telemetry("enable")             # the consent event, never attempted: a delivery is due
    for argv in (["--json", "telemetry", "preview"], ["--json", "telemetry", "status"], ["status", "--no-such-option"]):
        run = trial.raw(*argv)
        assert run.returncode == 2, (argv, run.stdout, run.stderr)
    trial.telemetry("status")
    trial.telemetry("preview")
    assert trial.state()["last_attempt"] == 0 and len(trial.pending()) == 1
    trial.cli("version")                  # a command that ran: its delivery starts in the background
    assert trial.wait(lambda: receiver.requests)
    assert trial.state()["last_attempt"] > 0      # read while the delivery waits for its answer and writes nothing
    receiver.release.set()


@pytest.mark.parametrize("switch, said", [({"DO_NOT_TRACK": "1"}, "DO_NOT_TRACK is set"), ({"CI": "true"}, "CI environment")])
def test_consent_given_under_a_switch_goes_out_with_the_first_event_after_it(trial, receiver, switch, said):
    enabled = trial.raw("telemetry", "enable", overrides=switch)
    assert enabled.returncode == 0 and said in enabled.stdout, enabled.stdout
    assert trial.pending() == []
    trial.cli("version", overrides=switch)
    assert trial.pending() == []
    trial.cli("version")
    assert names(trial.pending()) == ["rb_telemetry_enabled", "rb_operation_completed"]
    trial.cli("version")
    assert names(trial.pending()).count("rb_telemetry_enabled") == 1


def test_the_cohort_belongs_to_the_installation(trial, receiver):
    built_in = trial.telemetry("enable", overrides={"RB_POSTHOG_KEY": "", "RB_POSTHOG_HOST": ""})
    if not built_in["configured"]:
        assert built_in["cohort"] == "internal"          # a build with no destination of its own is the project's
    default = built_in["cohort"]
    trial.telemetry("disable")
    trial.telemetry("enable")
    trial.cli("version", overrides={"RB_TELEMETRY_COHORT": "Internal"})   # one process, any case
    trial.cli("version")
    assert [e["properties"]["cohort"] for e in trial.pending()] == [default, "internal", default]
    assert trial.telemetry("status")["cohort"] == default

    trial.telemetry("disable")
    trial.telemetry("enable", overrides={"RB_TELEMETRY_COHORT": "EXTERNAL"})
    trial.cli("version")
    assert trial.telemetry("status")["cohort"] == "external"
    assert {e["properties"]["cohort"] for e in trial.pending()} == {"external"}


def test_failed_delivery_retries_identical_event_ids_and_timestamps(trial, receiver):
    trial.telemetry("enable")
    expected = trial.telemetry("preview")["payload"]
    assert trial.telemetry("flush")["sent"] == 0
    assert trial.telemetry("preview")["payload"] == expected
    receiver.response_code = 200
    assert trial.telemetry("flush")["sent"] == 1
    assert [body for _, body in receiver.requests] == [expected, expected]
    assert trial.pending() == []


def test_a_slow_answer_counts_as_sent_and_what_was_queued_meanwhile_is_kept(trial, receiver):
    receiver.response_code, receiver.delay = 200, 8.0     # answered when released, within the delivery's 10 s budget
    trial.telemetry("enable")
    trial.cli("version")                  # starts the delivery; the receiver holds its answer back
    assert trial.wait(lambda: receiver.requests, timeout=10)
    for _ in range(3):
        trial.cli("version")              # queued while the batch is on the wire
    assert receiver.answered == []        # every command returned while the batch was still unanswered
    receiver.release.set()
    assert trial.wait(lambda: trial.pending() == [])
    uuids = [e["uuid"] for _, body in receiver.requests for e in body["batch"]]
    assert len(uuids) == len(set(uuids)) == 5


@pytest.mark.parametrize("program", ["rb", "rabbit-brain"])
def test_a_command_read_to_its_end_returns_before_the_delivery_it_started_is_answered(trial, receiver, program):
    """A caller that reads a command's output to its end, as a script, the SDK or CI does, has it when the command ends:
    the delivery the command starts holds none of its pipes. On Windows too, where rabbit-brain runs rb as a child."""
    path = os.path.join(os.path.dirname(command()[0]), program + (".exe" if os.name == "nt" else ""))
    if not os.path.isfile(path):
        pytest.skip(f"no {program} beside the rb under test")
    receiver.response_code, receiver.delay = 200, 8.0     # answered when released, within the delivery's 10 s budget
    trial.telemetry("enable")             # the consent event: a delivery is due
    run = subprocess.run([path, "version"], cwd=trial.root, env=trial.env, stdin=subprocess.DEVNULL, capture_output=True,
                         text=True, timeout=30)
    answered = list(receiver.answered)
    assert run.returncode == 0 and run.stdout.startswith("rabbit-brain "), (run.stdout, run.stderr)
    assert trial.wait(lambda: receiver.requests, timeout=10), "the command started no delivery"
    assert answered == []
    receiver.release.set()


@pytest.mark.skipif(sys.platform == "win32", reason="hands rb a descriptor beyond its standard streams, as a POSIX parent can")
def test_a_delivery_holds_nothing_the_command_was_handed(trial, receiver):
    """A command can be handed more than its standard streams (a pipe at descriptor 3 or up, as make's jobserver or a
    test harness hands one): the delivery it starts keeps none of them open, so whoever reads such a pipe to its end
    is not kept waiting."""
    receiver.response_code, receiver.delay = 200, 8.0
    trial.telemetry("enable")             # the consent event: a delivery is due
    read, write = os.pipe()
    try:
        run = subprocess.run([*command(), "version"], cwd=trial.root, env=trial.env, stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, pass_fds=(write,), timeout=30)
        os.close(write)
        write = None
        assert run.returncode == 0
        assert os.read(read, 1) == b""        # the write end is closed everywhere: rb has ended and nothing kept it
        answered = list(receiver.answered)
        assert trial.wait(lambda: receiver.requests, timeout=10), "the command started no delivery"
        assert answered == []
    finally:
        receiver.release.set()
        os.close(read)
        if write is not None:
            os.close(write)


@pytest.mark.skipif(sys.platform == "win32", reason="runs the command in a POSIX pseudo-terminal, as the user's terminal is")
def test_a_watched_command_in_a_terminal_ends_before_the_delivery_started_inside_it_is_answered(trial, receiver):
    """In a terminal, rb journal run gives the command a terminal of its own and reads it to its end: the delivery an
    rb inside the command starts holds none of that terminal, so the watched command ends when the command does."""
    import pty

    trial.project()
    receiver.response_code, receiver.delay = 200, 8.0
    trial.telemetry("enable")             # the consent event: the rb inside starts a delivery
    leader, follower = pty.openpty()
    inside = " ".join(shlex.quote(part) for part in [*command(), "status"])
    watched = subprocess.Popen([*command(), "journal", "run", "--", "sh", "-c", inside], cwd=trial.root, env=trial.env,
                               stdin=follower, stdout=follower, stderr=follower, close_fds=True)
    os.close(follower)
    shown = []
    threading.Thread(target=drain, args=(leader, shown), daemon=True).start()
    try:
        code = watched.wait(timeout=30)
        answered = list(receiver.answered)
        assert code == 0, b"".join(shown)[-2000:]
        assert trial.wait(lambda: receiver.requests, timeout=10), "the rb inside started no delivery"
        assert answered == []
    finally:
        receiver.release.set()
        if watched.poll() is None:
            watched.kill()
            watched.wait(timeout=5)
        os.close(leader)


def test_a_refused_delivery_waits_an_hour_and_a_success_ends_the_wait(trial, receiver):
    receiver.response_code = 401          # the destination will not take the batch as it is
    trial.telemetry("enable")
    assert trial.telemetry("flush")["reason"] == "refused"
    refused = trial.state()
    assert (refused["failures"], refused["refused"]) == (1, True)
    for _ in range(3):
        trial.cli("version")              # the consent milestone waits, and no delivery starts within the hour
    assert trial.state()["last_attempt"] == refused["last_attempt"] and len(receiver.requests) == 1
    receiver.response_code = 200
    assert trial.telemetry("flush")["sent"] == 4      # the user's own flush tries at once
    assert (trial.state()["failures"], trial.state()["refused"]) == (0, False)


def test_sdk_and_mcp_each_count_one_operation_with_their_own_surface(trial, receiver):
    trial.project()
    trial.telemetry("enable")
    trial.bridge([{"op": "status", "args": {}, "root": str(trial.root), "via": "sdk"}])
    trial.mcp(("status", {}))
    events = trial.pending()
    operations = [e["properties"] for e in events if e["event"] == "rb_operation_completed"]
    assert [(p["command"], p["surface"], p["actor"]) for p in operations] == [
        ("status", "sdk", "human_inferred"), ("status", "mcp", "agent")]
    assert len([e for e in events if e["event"] == "rb_record_inspected"]) == 2
    assert "private-client-marker" not in json.dumps(events)


def test_the_sdk_counts_what_the_command_line_counts(trial, receiver, monkeypatch):
    import rabbit_brain as rb
    from rabbit_brain import _rb

    trial.project()
    monkeypatch.setattr(_rb, "environment", lambda: dict(trial.env))
    state = rb.open(trial.root)
    trial.telemetry("enable")
    before = len(trial.pending())
    experiment = state.experiment("private-exp-marker")     # a handle: rb's own lookup, no command anyone ran
    assert experiment.data.id == "private-exp-marker"
    assert len(trial.pending()) == before
    state.log()                                             # rb log, which reads the record as rb status does
    added = trial.pending()[before:]
    assert [e["properties"].get("command") for e in added if e["event"] == "rb_operation_completed"] == ["log"]
    assert names(added).count("rb_record_inspected") == 1


def test_an_mcp_session_leaves_nothing_behind_when_it_ends(trial, receiver):
    trial.project()
    receiver.response_code = 200
    trial.telemetry("enable")
    trial.mcp(("status", {}), ("context", {}), ("status", {}))
    assert trial.wait(lambda: trial.pending() == []), trial.pending()
    delivered = names(e for _, body in receiver.requests for e in body["batch"])
    assert delivered.count("rb_record_inspected") == 2 and delivered.count("rb_context_delivered") == 1


def test_tour_never_counts_as_activation(trial, receiver):
    trial.telemetry("enable")
    before = trial.pending()
    trial.cli("tour")
    assert (trial.root / "rb-tour" / ".rb").is_dir()
    assert trial.pending() == before


def test_sdk_duplicate_runs_and_analyses_do_not_repeat_readiness(trial, receiver, monkeypatch):
    import rabbit_brain as rb
    from rabbit_brain import _rb

    trial.project()
    monkeypatch.setattr(_rb, "environment", lambda: dict(trial.env))
    experiment = rb.open(trial.root).experiment("private-exp-marker")
    trial.telemetry("enable")

    def readiness():
        return [e for e in trial.pending() if e["event"] == "rb_record_ready"]

    run = experiment.record({"private_metric_marker": 0.9})
    assert run is not None
    after_run = readiness()
    assert len(after_run) == 1
    assert experiment.record({"private_metric_marker": 0.9}) is None
    assert readiness() == after_run

    comparisons = [{"subject": "candidate", "reference": "baseline", "metric": "private_metric_marker",
                    "estimate": 0.1, "lo": 0.05, "hi": 0.15}]
    analysis = experiment.analysis(comparisons, reads=[run.id], method="private-method-marker", unit="sample", units=10)
    after_analysis = readiness()
    assert len(after_analysis) == 2
    duplicate = experiment.analysis(comparisons, reads=[run.id], method="private-method-marker", unit="sample", units=10)
    assert duplicate.id == analysis.id
    assert readiness() == after_analysis
    assert all(e["properties"]["surface"] == "sdk" and e["properties"]["route"] == "declared" for e in after_analysis)
    operations = [e["properties"] for e in trial.pending() if e["event"] == "rb_operation_completed"]
    assert [(p["command"], p["outcome"]) for p in operations] == [
        ("run add", "success"), ("run add", "success"), ("analysis add", "success"), ("analysis add", "success")]


def test_reconcile_counts_readiness_only_when_it_records_a_run(trial, receiver):
    trial.project()
    trial.cli("criterion", "add", "private-claim-marker", "-e", "private-exp-marker", "--metric", "score", "--at-least", "0.5", "--id", "c1")
    trial.cli("run", "add", "private-exp-marker", "score=0.9")
    trial.telemetry("enable")
    for _ in range(2):                    # runs already in the record, and nothing new to record
        assert trial.cli("reconcile", "--apply")["data"]["recorded"] == []
    assert "rb_record_ready" not in names(trial.pending())
    watched = trial.raw("journal", "run", "--json", "--", sys.executable, "-c", 'print(\'{"metrics": {"score": 0.91}}\')')
    assert watched.returncode == 0, watched.stderr
    recorded = trial.cli("reconcile", "--apply")["data"]["recorded"]
    assert len(recorded) == 1 and not recorded[0].startswith("not recorded"), recorded
    ready = [e["properties"]["route"] for e in trial.pending() if e["event"] == "rb_record_ready"]
    assert ready == ["reconciled"]


def test_failed_research_check_counts_inspection_without_product_failure(trial, receiver):
    trial.project()
    trial.cli("criterion", "add", "private-gate-marker", "-e", "private-exp-marker", "--metric", "score",
              "--at-most", "0.1", "--id", "c1")
    trial.cli("run", "add", "private-exp-marker", "score=0.9")
    trial.telemetry("enable")
    answer = trial.cli("status", "--fail-on", "unmet", code=1)
    assert answer["ok"] is True and answer["data"]["outcome"]["passed"] is False
    events = trial.pending()
    operations = [e["properties"] for e in events if e["event"] == "rb_operation_completed"]
    assert len(operations) == 1
    assert (operations[0]["command"], operations[0]["outcome"]) == ("status", "check_failed")
    inspections = [e for e in events if e["event"] == "rb_record_inspected"]
    assert len(inspections) == 1 and inspections[0]["properties"]["surface"] == "cli"
    assert not any(e["event"] == "rb_record_ready" for e in events)


def test_analytics_state_is_refused_inside_a_record_however_it_is_spelled(trial, receiver):
    for config in (trial.root / ".RB" / "settings", trial.root / "missing" / ".." / ".Rb" / "x", trial.root / "missing" / ".." / "settings"):
        refused = trial.cli("telemetry", "enable", overrides={"RB_CONFIG_DIR": str(config)}, code=2)["errors"][0]
        assert refused["code"] == "E_WRITE_FAILED", refused
    assert list(trial.root.iterdir()) == []


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="a FIFO is a POSIX file")
def test_a_state_file_that_is_not_a_regular_file_holds_no_command(trial, receiver):
    trial.project()
    trial.telemetry("enable")
    state = trial.config / "telemetry" / "state.json"
    state.unlink()
    os.mkfifo(state)
    trial.cli("status")                   # opening a FIFO would block for good: the timeout would fail this
    assert trial.telemetry("status")["enabled"] is False
    trial.telemetry("disable")
    assert not os.path.lexists(state)
