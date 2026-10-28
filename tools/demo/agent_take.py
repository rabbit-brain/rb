#!/usr/bin/env python3
"""One agent session for the demo's agent beats (follow-up 14): Claude Code in a pty, in a copy of a record, told one
plain line, filmed as an asciinema v2 cast.

    python tools/demo/agent_take.py --folder <the record's copy> --say "<one line>" --cast <out.cast> \
        --rb-bin <dir holding rb> [--study-bin <dir holding the study's python>] [--until-runs N] [--quiet 60]

The session is the researcher's: a clean terminal (import_take.py's), the folder's name as its prompt, Claude Code
started with its first-run questions answered as each screen asks, the line pasted and sent. The driver types nothing
else but /exit. It ends the session once the agent has worked and gone quiet for --quiet seconds, and, with
--until-runs, only once the record holds that many runs (its run files, read without running rb, so the driver's
looks leave no trace in the record); --limit caps it. Beside the cast, <out.cast>.json keeps the wall-clock time of
the cast's zero and of its markers (`said`: the line sent; `done`: the session quiet), so another capture can be put
on the same clock.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
import time
from pathlib import Path

_spec = importlib.util.spec_from_file_location("demo_import_take", Path(__file__).with_name("import_take.py"))
take = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(take)


def run_files(folder: Path) -> int:
    """The runs the record holds, retracted ones too: one file each under .rb/experiments/<id>/runs/."""
    return len(list((folder / ".rb" / "experiments").glob("*/runs/*.yaml")))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--folder", required=True, type=Path)
    ap.add_argument("--say", required=True)
    ap.add_argument("--cast", required=True, type=Path)
    ap.add_argument("--rb-bin", required=True)
    ap.add_argument("--study-bin")
    ap.add_argument("--claude", default=str(Path.home() / ".local/bin/claude"))
    ap.add_argument("--cols", type=int, default=64)
    ap.add_argument("--rows", type=int, default=16,
                    help="a window 16 rows high (default %(default)s): a top window ends above the comparison grid's header row")
    ap.add_argument("--threads", type=int, default=8, help="OMP_NUM_THREADS in the session: the study's runs use at most this many")
    ap.add_argument("--until-runs", type=int)
    ap.add_argument("--quiet", type=float, default=60.0)
    ap.add_argument("--limit", type=float, default=150 * 60.0)
    ap.add_argument("--title")
    a = ap.parse_args(argv)
    if "\n" in a.say or not a.say.strip():
        raise SystemExit("--say is one plain line")
    folder = a.folder.resolve()
    if not (folder / ".rb").is_dir():
        raise SystemExit(f"{folder} holds no .rb/")

    log = open(a.cast.with_suffix(".log"), "a", encoding="utf-8")

    def say(msg):
        line = f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {msg}"
        print(line, flush=True)
        log.write(line + "\n")
        log.flush()

    prompt = f"{folder.name}$ "
    env = take.clean_env(a.rb_bin, a.study_bin, str(Path.home()))
    env["PS1"] = prompt
    env["OMP_NUM_THREADS"] = str(a.threads)
    cast = take.Cast(a.cast, a.cols, a.rows, a.title or folder.name)
    wall0 = time.time() - (time.monotonic() - cast.t0)
    marks = {}

    def mark(label):
        cast.mark(label)
        marks[label] = round(time.monotonic() - cast.t0, 3)

    def keep():
        a.cast.with_suffix(".cast.json").write_text(json.dumps({"wall0": wall0, "marks": marks, "say": a.say,
                                                                 "cols": a.cols, "rows": a.rows}, indent=1) + "\n")

    s = take.Session(folder, env, cast, a.cols, a.rows, log)
    s.wait_for(re.escape(prompt.strip()), 10)
    mark("claude")
    if not take.start_claude(s, a.claude, say):
        cast.close()
        return 1
    s.pump(3)
    mark("said")
    keep()
    say(f"saying: {a.say}")
    s.send("\x1b[200~" + a.say + "\x1b[201~")
    s.pump(1.5)
    s.send("\r")
    started = time.monotonic()
    busy_seen = False
    # Claude Code's footer while it works, and only then, read in the screen's last rows (a reply that quotes it is
    # no footer); a narrow window cuts it, so its start is enough. Output well
    # after the line was sent counts as work too
    busy = re.compile(r"esc to\s*int")
    ended = False
    while time.monotonic() - started < a.limit:
        try:
            s.pump(2.0)
        except Exception as e:  # the session ended on its own
            say(f"session ended: {e!r}")
            ended = True
            break
        if busy.search("\n".join(s.screen.display[-6:])):
            busy_seen = True
            continue
        busy_seen = busy_seen or s.last_out - started > 5
        if busy_seen and time.monotonic() - s.last_out > a.quiet:
            if a.until_runs is None or run_files(folder) >= a.until_runs:
                say(f"the session is quiet; the record holds {run_files(folder)} runs")
                break
    else:
        say(f"time limit reached; the record holds {run_files(folder)} runs")
    mark("done")
    keep()
    (a.cast.with_suffix(".screen.txt")).write_text(s.text() + "\n", encoding="utf-8")
    if not ended:
        import pexpect
        try:
            s.type("/exit")
            s.pump(1)
            s.send("\r")
            s.pump(5)
            s.type("exit\r")
            s.pump(3)
        except pexpect.EOF:
            pass
    if s._partial:
        cast.event("o", s._partial.decode("utf-8", errors="replace"))
    cast.close()
    if a.until_runs is not None and run_files(folder) < a.until_runs:
        say(f"the record holds {run_files(folder)} runs, not {a.until_runs}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
