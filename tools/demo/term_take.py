#!/usr/bin/env python3
"""A terminal beat recorded for real: types each command into bash in a pty, in the folder given, and keeps every byte
the pty prints with its time, as an asciinema v2 cast (import_take.py's terminal, its clean environment and its cast).

    python tools/demo/term_take.py --repo <folder> --cast <out.cast> [--rb-bin <dir>] [--pause 1.5] -- '<command>' ...

Each command runs to its end (the prompt comes back) before the next is typed; a marker names each in the cast.
"""
from __future__ import annotations

import argparse
import importlib.util
import re
import sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location("demo_import_take", Path(__file__).with_name("import_take.py"))
take = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(take)


def record(repo: Path, cast_path: Path, commands: list[str], rb_bin: str | None, pause: float, cols: int, rows: int,
           limit: float = 120.0) -> None:
    env = take.clean_env(rb_bin or "/usr/bin", None, str(Path.home()))
    env["PS1"] = f"{repo.name}$ "
    prompt = re.escape(env["PS1"].strip())
    cast = take.Cast(cast_path, cols, rows, repo.name)
    s = take.Session(repo, env, cast, cols, rows, None)
    try:
        if not s.wait_for(prompt, 10):
            raise SystemExit("bash showed no prompt")
        s.pump(0.8)
        for cmd in commands:
            cast.mark(f"cmd {cmd}")
            s.type(cmd + "\r")
            if not s.wait_for(prompt + r"\s*$", limit):
                raise SystemExit(f"{cmd!r} did not end within {limit:.0f} s")
            s.pump(pause)
        cast.mark("end")
    finally:
        cast.close()
        s.child.close(force=True)


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    commands = argv[argv.index("--") + 1:] if "--" in argv else []
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True, type=Path)
    ap.add_argument("--cast", required=True, type=Path)
    ap.add_argument("--rb-bin")
    ap.add_argument("--pause", type=float, default=1.5)
    ap.add_argument("--cols", type=int, default=140)
    ap.add_argument("--rows", type=int, default=40)
    a = ap.parse_args(argv[:argv.index("--")] if "--" in argv else argv)
    if not commands:
        raise SystemExit("no commands: give them after --")
    record(a.repo.resolve(), a.cast, commands, a.rb_bin, a.pause, a.cols, a.rows)
    return 0


if __name__ == "__main__":
    sys.exit(main())
