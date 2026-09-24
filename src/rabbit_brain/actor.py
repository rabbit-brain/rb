"""Who is making a write, and how rb knows.

Only a person freezes, decides, retracts what something rests on, or amends a frozen spec. That rule is worth nothing
if an agent is recorded as a person by default, so the actor is resolved in this order:

1. an agent runtime detected from the environment: Claude Code (`CLAUDECODE=1`), `AI_AGENT`, Codex (its sandbox
   variables), GitHub Actions. `RB_ACTOR=agent:<name>` names the agent; `RB_ACTOR=human:<name>` is ignored here, because
   a person's name typed inside an agent session is exactly what an agent would type;
2. otherwise `RB_ACTOR` (`human:<name>` or `agent:<name>`), when set;
3. otherwise a person named from git (`user.email`'s local part, else `user.name`), else the login.

A person inside an agent's terminal is recorded as the agent too. That is the price of the rule: they make a person's
call from their own terminal, which is what the handoff tells them to do.
"""
from __future__ import annotations

import getpass
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .errors import RBError

ACTOR_PATTERN = r"^(human|agent):[A-Za-z0-9_.@+-]{1,80}$"


@dataclass(frozen=True)
class Actor:
    id: str                      # human:jh or agent:claude-code
    via: str                     # how rb knows: RB_ACTOR, detected:CLAUDECODE, git-email, git-name, login
    runtime: Optional[str]       # the agent runtime detected in the environment, whatever RB_ACTOR says
    ignored: Optional[str] = None  # a person's name RB_ACTOR gave inside that runtime, which rb did not take

    @property
    def is_person(self) -> bool:
        return self.id.startswith("human:")

    @property
    def asserted_from(self) -> Optional[str]:
        """Always None: a person's name given inside a detected agent session is ignored, not recorded as a person."""
        return None


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.@+-]", "_", text.strip())[:80].strip("_") or "unknown"


CODEX_SIGNS = ("CODEX_SANDBOX", "CODEX_SANDBOX_NETWORK_DISABLED", "CODEX_CI")


def detect_runtime(env: Optional[dict] = None) -> Optional[tuple[str, str]]:
    """(agent name, the variable that gave it away), or None."""
    env = os.environ if env is None else env
    if env.get("CLAUDECODE") == "1":
        return "claude-code", "CLAUDECODE"
    if env.get("AI_AGENT"):
        name = re.split(r"[_/]", env["AI_AGENT"], maxsplit=1)[0] or "agent"
        return _slug(name), "AI_AGENT"
    codex = [k for k in CODEX_SIGNS if env.get(k)]   # set by Codex for the commands it runs; CODEX_HOME in a profile is not
    if codex:
        return "codex", codex[0]
    if env.get("GITHUB_ACTIONS") == "true":
        return "github-actions", "GITHUB_ACTIONS"
    return None


def _git_config(key: str, cwd: Optional[Path]) -> Optional[str]:
    try:
        r = subprocess.run(["git", "config", key], capture_output=True, text=True, timeout=10, cwd=cwd)
    except (OSError, subprocess.SubprocessError):
        return None
    out = r.stdout.strip()
    return out if r.returncode == 0 and out else None


def current(cwd: Optional[Path] = None, env: Optional[dict] = None) -> Actor:
    env = os.environ if env is None else env
    runtime = detect_runtime(env)
    raw = env.get("RB_ACTOR")
    if raw is not None and not re.fullmatch(ACTOR_PATTERN, raw):
        raise RBError("E_ACTOR_INVALID", message=f"RB_ACTOR is {raw!r}.")
    if runtime:
        if raw is not None and raw.startswith("agent:"):
            return Actor(raw, f"RB_ACTOR in {runtime_label(runtime[0])}", runtime[0])
        return Actor(f"agent:{runtime[0]}", f"detected:{runtime[1]}", runtime[0], ignored=raw)
    if raw is not None:
        return Actor(raw, "RB_ACTOR", None)
    email = _git_config("user.email", cwd)
    if email and "@" in email:
        return Actor(f"human:{_slug(email.split('@', 1)[0])}", "git-email", None)
    name = _git_config("user.name", cwd)
    if name:
        return Actor(f"human:{_slug(name.lower().replace(' ', '-'))}", "git-name", None)
    try:
        login = getpass.getuser()
    except Exception:  # noqa: BLE001 - no login name in some containers
        login = "unknown"
    return Actor(f"human:{_slug(login)}", "login", None)


RUNTIME_NAMES = {"claude-code": "Claude Code", "codex": "Codex", "github-actions": "GitHub Actions"}


def runtime_label(runtime: str) -> str:
    return f"a {RUNTIME_NAMES.get(runtime, runtime)} session"
