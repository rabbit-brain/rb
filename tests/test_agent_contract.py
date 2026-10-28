"""The agent contract (bench/agent/SPEC.md) holds itself to account: the scripted ideal agent keeps every invariant in
every case, and each careless agent fails exactly the checks it is written to fail. A check that stops telling the two
apart fails here, so the contract cannot drift toward whatever an agent happens to do."""
import json
import os
import shlex
import shutil
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bench.agent.cases import BY_NAME, CASES  # noqa: E402
from bench.agent.drive import command  # noqa: E402
from bench.agent.run import live, play_case, scripted  # noqa: E402
from bench.agent.scripted import CARELESS, IDEAL  # noqa: E402
from rbtest import AGENT_VARS, as_the_user, CI_VARIABLES  # noqa: E402

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="the cases are git repositories")


@pytest.fixture
def scratch(tmp_path, tmp_path_factory, monkeypatch):
    cfg = tmp_path_factory.mktemp("gitconfig") / "config"
    cfg.write_text("[user]\n\tname = researcher\n\temail = researcher@example.com\n[commit]\n\tgpgsign = false\n", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(cfg))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    as_the_user(monkeypatch)
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.mark.parametrize("name", [c.name for c in CASES])
def test_the_ideal_agent_keeps_every_invariant(scratch, name):
    _ctx, results = play_case(BY_NAME[name], scripted(IDEAL), scratch)
    failed = {r.check: r.why for r in results if not r.ok}
    assert not failed, failed


@pytest.mark.parametrize("case,agent", sorted(CARELESS))
def test_a_careless_agent_fails_exactly_what_it_breaks(scratch, case, agent):
    play, expected = CARELESS[(case, agent)]
    _ctx, results = play_case(BY_NAME[case], scripted({case: play}), scratch)
    failed = {r.check for r in results if not r.ok}
    assert failed == expected, {r.check: r.why for r in results}
    assert all(r.why for r in results)                 # every verdict says why, in words


def test_every_check_has_a_careless_agent_that_breaks_it():
    names = {_name(chk) for c in CASES for chk in c.checks}
    broken = set().union(*(expected for _play, expected in CARELESS.values()))
    assert names == broken, f"checks no careless agent breaks: {sorted(names - broken)}; unknown: {sorted(broken - names)}"


def _name(chk) -> str:
    """A check's name: the function's own, or, for a check built for one case, the function that built it."""
    return chk.__name__ if chk.__name__ != "check" else chk.__qualname__.split(".")[0]


def test_a_live_turn_carries_none_of_the_launchers_agent_or_session_variables(scratch, monkeypatch):
    """Started inside an agent session or in CI, the runner hands a live agent none of those variables: the live
    agent's own runtime names it and its chat."""
    for name in AGENT_VARS:
        monkeypatch.setenv(name, "1")
    shows = f'{shlex.quote(sys.executable)} -c "import json, os; print(json.dumps(sorted(os.environ)))"'
    said = live(shows, None, scratch, 120)(BY_NAME["chatter"], 0, "hello", scratch)
    left = sorted(set(AGENT_VARS) & set(json.loads(said)))       # names only: values stay out of a log
    assert not left, f"a live turn runs with {left} set"


def test_a_benchmark_run_is_nobodys_usage(tmp_path):
    """A user who opted in to analytics runs the benchmark from their own terminal: rb counts none of what the setup
    or the agent does there, and sends nothing."""
    received = []

    class Receiver(BaseHTTPRequestHandler):
        def do_POST(self):
            received.append(self.rfile.read(int(self.headers["Content-Length"])))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Receiver)
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()
    # the user's terminal: no agent session, no proxy, and nothing else that keeps rb quiet (a test, CI, an opt-out)
    others = {*CI_VARIABLES, *AGENT_VARS, "DO_NOT_TRACK", "PYTEST_CURRENT_TEST",
              "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"}
    env = {k: v for k, v in os.environ.items()
           if k not in others and not k.startswith(("RB_TEST_", "RB_TELEMETRY", "CODEX_", "CLAUDE_CODE_", "CURSOR_", "GROK_"))}
    env.update({"RB_CONFIG_DIR": str(tmp_path / "settings"), "NO_PROXY": "127.0.0.1", "no_proxy": "127.0.0.1",
                "PATH": os.path.dirname(sys.executable) + os.pathsep + os.environ.get("PATH", "")})

    def telemetry(*args):
        r = subprocess.run([*command(), "telemetry", *args, "--json"], env=env, capture_output=True, text=True, timeout=60)
        return json.loads(r.stdout)["data"]["telemetry"]

    try:
        telemetry("enable", "--project-key", "phc_localtestonly", "--host", f"http://127.0.0.1:{server.server_port}")
        consent = telemetry("preview")["payload"]["batch"]
        # analytics is on here: what the benchmark adds would show (a switch left set would hide it, and pass for nothing)
        assert [e["event"] for e in consent] == ["rb_telemetry_enabled"], consent
        played = subprocess.run([sys.executable, str(ROOT / "bench" / "agent" / "run.py"), "--case", "chatter"], cwd=tmp_path,
                                env=env, capture_output=True, text=True, timeout=600)
        assert played.returncode == 0 and "1 of 1 checks pass" in played.stdout, played.stdout + played.stderr
        assert telemetry("preview")["payload"]["batch"] == consent
        assert received == []
    finally:
        server.shutdown()
        server.server_close()
