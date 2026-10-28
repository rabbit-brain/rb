"""How the agent contract drives rb and reads what an agent left: the command line, as a human or an agent types it,
and the SDK, which asks rb. Nothing here reaches into how rb works."""
from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import rabbit_brain
from rabbit_brain import _rb       # which rb this Python package asks, and the environment it asks it in

# the environment variables rb reads an agent from (detect_runtime and CODEX_SIGNS in rust/src/actor.rs), RB_ACTOR,
# which names whoever acts, RB_MODEL and RB_PROVIDER, an agent's model and provider for one command, and Claude Code's
# switches to another provider's API (CLAUDE_CODE_ELSEWHERE there); the benchmark
# runs without the tests' harness, so tests/test_ledger_integrity.py holds these and SESSION_SIGNS to actor.rs as it
# holds rbtest's
AGENT_SIGNS = ("CLAUDECODE", "AI_AGENT", "CODEX_SANDBOX", "CODEX_SANDBOX_NETWORK_DISABLED", "CODEX_CI", "CODEX_THREAD_ID", "CODEX_SESSION_ID",
               "CURSOR_AGENT", "GITHUB_ACTIONS", "RB_ACTOR", "RB_MODEL", "RB_PROVIDER", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX",
               "CLAUDE_CODE_USE_FOUNDRY", "CLAUDE_CODE_USE_ANTHROPIC_AWS", "CLAUDE_CODE_USE_ANTHROPIC_GOOGLE_CLOUD", "CLAUDE_CODE_USE_MANTLE",
               "CLAUDE_CODE_USE_GATEWAY")
# what else an agent's runtime leaves in its commands, on which rb confirm and rb ui fail closed (SESSION_LEFTOVERS there)
SESSION_LEFTOVERS = ("CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_CHILD_SESSION", "CLAUDE_CODE_BRIDGE_SESSION_ID", "CLAUDE_CODE_EXECPATH", "CLAUDE_PID",
                     "CLAUDE_CODE_MESSAGING_SOCKET", "CLAUDE_CODE_MESSAGING_TOKEN", "CLAUDE_CODE_SESSION_ATTENDED")
# the environment variables rb reads a chat session (or a CI run) from (SESSION_SIGNS there)
SESSION_SIGNS = ("RB_SESSION", "CLAUDE_CODE_SESSION_ID", "CODEX_SESSION_ID", "CODEX_THREAD_ID", "CURSOR_CONVERSATION_ID", "CURSOR_SESSION_ID",
                 "CURSOR_TRACE_ID", "GROK_SESSION_ID", "GITHUB_RUN_ID")


def command() -> list:
    """The command line that runs rb, for a script that stands in for it on an agent's PATH."""
    return _rb.command()


def rb(*argv: str) -> dict:
    """One rb command here, as whoever the environment says; its envelope back as a dict, with its exit code."""
    args = list(argv)
    at = args.index("--") if "--" in args else len(args)          # --json is rb's, never the watched command's
    args.insert(at, "--json")
    r = subprocess.run([*_rb.command(), *args], env=_rb.environment(), capture_output=True, stdin=subprocess.DEVNULL)
    code, said = r.returncode, r.stdout.decode("utf-8", "replace")
    try:
        env = json.loads(said)
    except ValueError:
        env = {"ok": False, "errors": [{"message": said[-400:]}]}
    env["exit"] = code
    return env


def record(root: Path) -> Any:
    """The record at `root`, through the SDK: every read asks rb."""
    return rabbit_brain.open(root)


def now() -> str:
    """The time now, as rb writes times (RB_TEST_NOW fixes rb's clock, and so this one)."""
    return os.environ.get("RB_TEST_NOW") or datetime.now(timezone.utc).isoformat(timespec="milliseconds")
