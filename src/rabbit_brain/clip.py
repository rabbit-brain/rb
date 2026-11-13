"""How rb ui copies a prompt, a commit, a card's reference or an address (TUI-PARITY section 0 item 5): by the first route
that can tell it worked.

1. The system clipboard of this machine: pbcopy, Set-Clipboard, wl-copy, or xclip or xsel with a display. Never over
   SSH (SSH_CONNECTION or SSH_TTY set), so a copy never lands on a remote machine's clipboard; never clip.exe, which
   mangles UTF-8. Exit 0 reads `copied`.
2. Inside tmux, with no system clipboard: `tmux load-buffer -w -`. Exit 0 reads `copied to tmux`.
3. Otherwise OSC 52 to the terminal, which says nothing back: the caller shows the text itself, the prompt view, and no
   chip claims a copy.

Each route takes the text's exact UTF-8 bytes. `run` and `which` are injectable, so a test records every route.
"""
from __future__ import annotations

import base64
import os
import shutil
import subprocess
import sys
from typing import Callable, Mapping, Optional

COPIED = "copied"
COPIED_TMUX = "copied to tmux"


def over_ssh(env: Mapping[str, str]) -> bool:
    return bool(env.get("SSH_CONNECTION") or env.get("SSH_TTY"))


def system_command(env: Mapping[str, str], platform: str, which: Callable[[str], Optional[str]]) -> Optional[tuple[list[str], dict]]:
    """The command that puts stdin on this machine's clipboard, with the environment it needs, or None."""
    if platform == "darwin":
        if which("pbcopy"):
            # pbcopy reads stdin in the locale's encoding: a UTF-8 locale keeps − and " as they are
            return ["pbcopy"], {"LC_CTYPE": "UTF-8", "LANG": env.get("LANG") or "en_US.UTF-8"}
        return None
    if platform == "win32":
        for ps in ("pwsh", "powershell"):
            if which(ps):
                script = "[Console]::InputEncoding=[Text.Encoding]::UTF8; Set-Clipboard -Value ([Console]::In.ReadToEnd())"
                return [ps, "-NoProfile", "-NonInteractive", "-Command", script], {}
        return None
    if env.get("WAYLAND_DISPLAY") and which("wl-copy"):
        return ["wl-copy"], {}
    if env.get("DISPLAY"):
        if which("xclip"):
            return ["xclip", "-selection", "clipboard"], {}
        if which("xsel"):
            return ["xsel", "--clipboard", "--input"], {}
    return None


def copy(text: str, env: Optional[Mapping[str, str]] = None, run: Callable = subprocess.run,
         which: Callable[[str], Optional[str]] = shutil.which, platform: Optional[str] = None) -> Optional[str]:
    """Copy by the first route that can tell it worked: `copied`, `copied to tmux`, or None where neither could (the
    caller then sends OSC 52 and shows the prompt view)."""
    env = os.environ if env is None else env
    platform = sys.platform if platform is None else platform
    data = text.encode("utf-8")
    if not over_ssh(env):
        found = system_command(env, platform, which)
        if found is not None:
            cmd, extra = found
            try:
                r = run(cmd, input=data, capture_output=True, timeout=5, env={**env, **extra})
                if r.returncode == 0:
                    return COPIED
            except (OSError, subprocess.SubprocessError):
                pass
    if env.get("TMUX") and which("tmux"):
        try:
            r = run(["tmux", "load-buffer", "-w", "-"], input=data, capture_output=True, timeout=5, env=dict(env))
            if r.returncode == 0:
                return COPIED_TMUX
        except (OSError, subprocess.SubprocessError):
            pass
    return None


def osc52(text: str) -> str:
    """The OSC 52 sequence that asks the terminal to put `text` on its clipboard: the base64 of its exact UTF-8 bytes."""
    return "\x1b]52;c;" + base64.b64encode(text.encode("utf-8")).decode("ascii") + "\x07"


def prompt_view(text: str) -> str:
    """The prompt view (M16) as the terminal is sent it: OSC 52, then each paragraph on a line of its own in reverse
    video, nothing else on those lines, the terminal wrapping it; a blank line, then `⏎ back` with the key bold."""
    out = [osc52(text)]
    for para in text.split("\n"):
        out.append(f"\x1b[7m{para}\x1b[0m\n")
    out.append("\n\x1b[1m⏎\x1b[0m back")
    return "".join(out)


def wait_for_a_key() -> None:
    """Read one key from the terminal, raw, so any key returns to rb ui."""
    try:
        if sys.platform == "win32":
            import msvcrt
            msvcrt.getwch()
            return
        import termios
        import tty
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            os.read(fd, 16)
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
    except Exception:  # noqa: BLE001 - no terminal to read: return at once
        return


OPENED = "opened"


def open_address(url: str, env: Optional[Mapping[str, str]] = None, run: Callable = subprocess.run,
                 which: Callable[[str], Optional[str]] = shutil.which, platform: Optional[str] = None,
                 startfile: Optional[Callable[[str], None]] = None) -> Optional[str]:
    """Open a web address on this machine (TUI-PARITY L5): macOS `open`, `os.startfile` on Windows, `xdg-open` where a
    display is set; never Python's webbrowser, which can start a text browser inside the terminal over SSH. `opened`
    where it did; None over SSH or with no opener, where the caller copies the address by the `p` routes instead."""
    env = os.environ if env is None else env
    platform = sys.platform if platform is None else platform
    if over_ssh(env) or not url.startswith(("https://", "http://")):
        return None
    try:
        if platform == "darwin" and which("open"):
            argv = ["open", url]
        elif platform == "win32":
            (startfile or getattr(os, "startfile"))(url)
            return OPENED
        elif (env.get("DISPLAY") or env.get("WAYLAND_DISPLAY")) and which("xdg-open"):
            argv = ["xdg-open", url]
        else:
            return None
        done = run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
        return OPENED if getattr(done, "returncode", 1) == 0 else None
    except Exception:  # noqa: BLE001 - an opener that fails is no open: the caller copies
        return None
