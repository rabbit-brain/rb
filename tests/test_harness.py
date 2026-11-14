"""The shared harness (tests/rbtest.py) plays the user where a test says it does, whatever runs the tests: on GitHub's
runner, whose own variables (GITHUB_ACTIONS, GITHUB_RUN_ID) rb reads as an agent session, and in an agent's terminal."""
import subprocess

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
