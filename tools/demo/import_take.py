#!/usr/bin/env python3
"""One take of the demo's import (SPRINT-OCT24 6.1, "The import is real").

It copies the two-week study into a take folder of its own and, in a pty with a clean environment (no agent-session
variable of the session that starts it), plays the researcher: `rb init` in their own terminal, then a Claude Code
session given the researcher's two-line brief and the line rb init printed for the agent's chat. Every byte the pty
prints is kept with its time, as an asciinema v2 cast, for the time-lapse. The record the session leaves is the take's
`.rb/`, written by rb as whoever ran each command.

    python tools/demo/import_take.py --study /srv/rb/demo/int8-cifar --take /srv/rb/demo/takes/1 \
        --rb-bin <dir holding rb> [--study-bin <dir holding the study's python>]

Needs pexpect and pyte. The driver reads the screen (pyte) only to answer Claude Code's first-run questions and to see
when the session has gone quiet; it never types anything into the session but the brief, the paste line and /exit.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

BRIEF = (
    "This is my int8-cifar study, two weeks of runs: does INT8 post-training quantization keep a ResNet-20 within "
    "1 point of FP32 top-1 on CIFAR-10?\n"
    "On the way I want to know how many calibration images INT8 needs, and whether per-channel scales matter."
)
THEME = "INT8 ResNet-20 on CIFAR-10"
PROMPT = "int8-cifar$ "  # the folder's own name, as bash's \W gives it: the prompt claims no path the take is not in


class Cast:
    """An asciinema v2 cast: a header line, then [seconds, "o" or "i", text] per chunk, in the order they came."""

    def __init__(self, path: Path, cols: int, rows: int, title: str):
        self.f = open(path, "w", encoding="utf-8")
        self.t0 = time.monotonic()
        head = {"version": 2, "width": cols, "height": rows, "timestamp": int(time.time()), "title": title,
                "env": {"TERM": "xterm-256color", "SHELL": "/bin/bash"}}
        self.f.write(json.dumps(head) + "\n")

    def event(self, kind: str, text: str) -> None:
        if text:
            self.f.write(json.dumps([round(time.monotonic() - self.t0, 6), kind, text]) + "\n")
            self.f.flush()

    def mark(self, label: str) -> None:
        self.event("m", label)

    def close(self) -> None:
        self.f.close()


def clean_env(rb_bin: str, study_bin: str | None, home: str) -> dict:
    """The researcher's terminal: none of the variables an agent session or a CI job leaves, telemetry off."""
    path = [p for p in (study_bin, rb_bin, f"{home}/.local/bin", "/usr/local/bin", "/usr/bin", "/bin") if p]
    return {"HOME": home, "USER": "rb", "LOGNAME": "rb", "SHELL": "/bin/bash", "PATH": ":".join(path),
            "TERM": "xterm-256color", "COLORTERM": "truecolor", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
            "RB_TELEMETRY": "0", "DO_NOT_TRACK": "1", "PS1": PROMPT,
            # the agent's own screen: no diff of the files a command changed (in bypass mode it shows .rb/log.jsonl,
            # whose lines name the writer and the session, which no screen of the demo shows), and no update mid-take
            "CLAUDE_CODE_BASH_EDIT_DIFF": "0", "DISABLE_AUTOUPDATER": "1"}


class Session:
    def __init__(self, cwd: Path, env: dict, cast: Cast, cols: int, rows: int, log):
        import pexpect
        import pyte
        self.screen = pyte.Screen(cols, rows)
        self.stream = pyte.ByteStream(self.screen)
        self.cast = cast
        self.log = log
        self.child = pexpect.spawn("/bin/bash", ["--norc", "--noprofile", "-i"], cwd=str(cwd), env=env,
                                   dimensions=(rows, cols), encoding=None, timeout=1)
        self.last_out = time.monotonic()
        self._partial = b""
        self.child_prompt = env.get("PS1", PROMPT).strip()

    def pump(self, secs: float) -> None:
        import pexpect
        end = time.monotonic() + secs
        while time.monotonic() < end:
            try:
                data = self.child.read_nonblocking(65536, timeout=min(0.2, max(0.01, end - time.monotonic())))
            except pexpect.TIMEOUT:
                continue
            except pexpect.EOF:
                raise
            if data:
                self.last_out = time.monotonic()
                self.stream.feed(data)
                buf = self._partial + data
                # keep a UTF-8 sequence split across reads whole in the cast
                text = buf.decode("utf-8", errors="ignore")
                cut = len(buf)
                while cut > 0 and cut > len(buf) - 4:
                    try:
                        buf[:cut].decode("utf-8")
                        break
                    except UnicodeDecodeError:
                        cut -= 1
                text = buf[:cut].decode("utf-8", errors="replace")
                self._partial = buf[cut:]
                self.cast.event("o", text)

    def text(self) -> str:
        return "\n".join(self.screen.display)

    def send(self, s: str, log_input: bool = True) -> None:
        if log_input:
            self.cast.event("i", s)
        self.child.send(s.encode("utf-8"))

    def type(self, s: str, cps: float = 18.0) -> None:
        for ch in s:
            self.send(ch)
            self.pump(1.0 / cps)

    def wait_for(self, pattern: str, secs: float) -> bool:
        rx = re.compile(pattern, re.S)
        end = time.monotonic() + secs
        while time.monotonic() < end:
            self.pump(0.5)
            if rx.search(self.text()):
                return True
        return False


def start_claude(s: Session, claude: str, say) -> bool:
    """Claude Code started at the prompt, its first-run questions answered as each screen asks: True once it waits."""
    s.type(f"{Path(claude).name} --dangerously-skip-permissions\r")
    # first-run questions: the folder's trust, and bypass mode's warning; each answered as its screen asks
    ready = r"(bypass permissions on|\? for shortcuts|shift\+tab to cycle)"
    prompt = s.child_prompt
    end = time.monotonic() + 120
    answered = set()
    while time.monotonic() < end:
        s.pump(1.0)
        t = s.text()
        # each question's default is "No, exit": the yes is one line down
        if "Yes, I accept" in t and "bypass" not in answered:
            answered.add("bypass")
            say("answering bypass mode's warning")
            s.send("\x1b[B")
            s.pump(0.5)
            s.send("\r")
            s.pump(2)
        elif "Yes, I trust this folder" in t and "trust" not in answered:
            answered.add("trust")
            say("answering the folder's trust")
            s.send("\x1b[B")
            s.pump(0.5)
            s.send("\r")
            s.pump(2)
        elif re.search(ready, t):
            return True
        elif prompt + "\n" + prompt in t or t.rstrip().endswith(prompt) and answered:
            say("Claude Code exited at its first-run questions")
            print(t)
            return False
    say("Claude Code never showed its prompt")
    print(s.text())
    return False


def copy_study(study: Path, take: Path) -> Path:
    dest = take / "int8-cifar"
    if dest.exists():
        raise SystemExit(f"{dest} exists: a take starts from a fresh copy")
    take.mkdir(parents=True, exist_ok=True)
    subprocess.run(["cp", "-a", str(study), str(dest)], check=True)
    shutil.rmtree(dest / "__pycache__", ignore_errors=True)
    if (dest / ".rb").exists():
        raise SystemExit(f"{study} holds a record already: the import starts from the study as the agent left it")
    return dest


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--study", required=True, type=Path)
    ap.add_argument("--take", required=True, type=Path)
    ap.add_argument("--rb-bin", required=True)
    ap.add_argument("--study-bin")
    ap.add_argument("--claude", default=str(Path.home() / ".local/bin/claude"))
    ap.add_argument("--cols", type=int, default=140)
    ap.add_argument("--rows", type=int, default=40)
    ap.add_argument("--skill", action="store_true", help="the researcher runs rb skill add --claude --project first, as rb init suggests")
    ap.add_argument("--quiet", type=float, default=90.0, help="seconds of a quiet, idle screen that end the session")
    ap.add_argument("--limit", type=float, default=150 * 60.0, help="seconds the agent session may take at most")
    a = ap.parse_args(argv)

    repo = copy_study(a.study, a.take)
    log = open(a.take / "driver.log", "a", encoding="utf-8")

    def say(msg):
        line = f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {msg}"
        print(line, flush=True)
        log.write(line + "\n")
        log.flush()

    env = clean_env(a.rb_bin, a.study_bin, str(Path.home()))
    cast = Cast(a.take / "import.cast", a.cols, a.rows, "int8-cifar: the import")
    s = Session(repo, env, cast, a.cols, a.rows, log)
    s.wait_for(re.escape(PROMPT.strip()), 10)

    cast.mark("rb init")
    s.type(f'rb init "{THEME}"\r')
    if not s.wait_for(r"Use Rabbit Brain to track this[^\n]*", 60):
        say("rb init printed no paste line")
        print(s.text())
        return 1
    s.wait_for(re.escape(PROMPT.strip()) + r"\s*$", 20)
    m = re.search(r"Use Rabbit Brain to track this:[^\n]*", s.text())
    paste = re.sub(r"\s+", " ", m.group(0)).strip()
    say(f"paste line: {paste}")
    s.pump(2)
    if a.skill:
        cast.mark("skill")
        s.type("rb skill add --claude --project\r")
        if not s.wait_for(r"skills/rabbit-brain/SKILL\.md", 30):
            say("rb skill add wrote no skill")
            print(s.text())
            return 1
        s.wait_for(re.escape(PROMPT.strip()) + r"\s*$", 20)
        s.pump(2)

    cast.mark("claude")
    if not start_claude(s, a.claude, say):
        return 1
    s.pump(3)

    cast.mark("brief")
    say("pasting the brief and the paste line")
    s.send("\x1b[200~" + BRIEF + "\n\n" + paste + "\x1b[201~")
    s.pump(1.5)
    s.send("\r")
    started = time.monotonic()
    busy_seen = False
    busy = re.compile(r"esc to interrupt")  # Claude Code's footer while it works, and only then
    while time.monotonic() - started < a.limit:
        try:
            s.pump(2.0)
        except Exception as e:  # the session ended on its own
            say(f"session ended: {e!r}")
            break
        t = s.text()
        if busy.search(t):
            busy_seen = True
            continue
        if busy_seen and time.monotonic() - s.last_out > a.quiet:
            say("the session is quiet")
            break
    else:
        say("time limit reached")
    (a.take / "final-screen.txt").write_text(s.text() + "\n", encoding="utf-8")
    cast.mark("exit")
    import pexpect
    try:
        s.type("/exit")
        s.pump(1)
        s.send("\r")
        s.pump(5)
        s.type("exit\r")
        s.pump(3)
    except pexpect.EOF:
        pass  # the shell ended with the session
    if s._partial:
        cast.event("o", s._partial.decode("utf-8", errors="replace"))
    cast.close()
    say(f"take done in {time.monotonic() - started:.0f} s of agent time")
    return 0


if __name__ == "__main__":
    sys.exit(main())
