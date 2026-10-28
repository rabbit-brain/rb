"""Run the agent contract: build each case in a scratch directory, play its turns with an agent, and score what the agent
left in the record. With no --agent-cmd the scripted ideal agent plays (no model, no cost); with one, a real agent does,
with a counting `rb` first on its PATH. See bench/agent/SPEC.md.

    python bench/agent/run.py [--case NAME ...] [--agent-cmd '... {prompt}'] [--next-cmd '... {prompt}'] [--json] [--keep]
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable, Optional

if __package__ in (None, ""):                  # run as a script: make `bench.agent` importable from the repository root
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
    __package__ = "bench.agent"

from .cases import BY_NAME, CASES, Case, acting  # noqa: E402
from .checks import Context, Result  # noqa: E402
from .drive import AGENT_SIGNS, SESSION_LEFTOVERS, SESSION_SIGNS, command, now, record  # noqa: E402

Player = Callable[[Case, int, str, Path], str]

SHIM = '''#!{python}
import json, subprocess, sys
rc = subprocess.call([*{rb!r}, *sys.argv[1:]])
with open({log!r}, "a", encoding="utf-8") as f:
    f.write(json.dumps({{"argv": sys.argv[1:], "exit": rc}}) + "\\n")
sys.exit(rc)
'''


def _lines(root: Path) -> int:
    """How many lines the record's log holds: none while there is no record yet."""
    return len(record(root).log(None)) if (root / ".rb").is_dir() else 0


def play_case(case: Case, player: Player, workdir: Path) -> tuple[Context, list[Result]]:
    """Build the case, play every turn, and score it."""
    root = workdir / case.name / "repo"
    root.parent.mkdir(parents=True, exist_ok=True)
    here = Path.cwd()
    try:
        case.setup(root)
        os.chdir(root)
        head = subprocess.run(["git", "-C", str(root), "rev-parse", "--verify", "-q", "HEAD"], capture_output=True, text=True).stdout.strip()
        baseline = _lines(root)
        started = now()
        marks: list[int] = []
        answers: list[str] = []
        for i, turn in enumerate(case.turns):
            marks.append(_lines(root))
            if turn.happens:
                turn.happens(root)
            answers.append(player(case, i, turn.says, root) or "")
        calls_log = workdir / case.name / "calls.jsonl"
        calls = [json.loads(line) for line in calls_log.read_text(encoding="utf-8").splitlines()] if calls_log.exists() else []
        ctx = Context(root=root, said=[t.says for t in case.turns], answers=answers, baseline=baseline, marks=marks, started=started, calls=calls, head=head)
        return ctx, [check(ctx) for check in case.checks]
    finally:
        os.chdir(here)


def scripted(agents: dict) -> Player:
    def play(case: Case, i: int, says: str, root: Path) -> str:
        with acting("agent", session=f"bench-{case.name}"):
            return agents[case.name](i, says, root)
    return play


def live(agent_cmd: str, next_cmd: Optional[str], workdir: Path, timeout: float) -> Player:
    """A real agent: each turn runs the command with the user's message as {prompt}, in the case's repository, with a
    counting rb first on PATH. What the agent prints is its answer."""
    def play(case: Case, i: int, says: str, root: Path) -> str:
        bindir = workdir / case.name / "bin"
        bindir.mkdir(parents=True, exist_ok=True)
        shim = bindir / ("rb-counted.py" if os.name == "nt" else "rb")
        if not shim.exists():
            shim.write_text(SHIM.format(python=sys.executable, rb=command(), log=str(workdir / case.name / "calls.jsonl")), encoding="utf-8")
            if os.name == "nt":             # what Windows finds on PATH is a .cmd, which hands on to the same script
                (bindir / "rb.cmd").write_text(f'@"{sys.executable}" "{shim}" %*\n', encoding="utf-8")
            else:
                shim.chmod(0o755)
        template = agent_cmd if i == 0 or not next_cmd else next_cmd
        memory = case.turns[i].memory               # what the session holds of the work: the prompt carries it after the user's message
        prompt = f"{says}\n\n{memory}" if memory else says
        argv = [prompt if part == "{prompt}" else part.replace("{prompt}", prompt) for part in shlex.split(template)]
        env = {**os.environ, "PATH": f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}"}
        for k in (*AGENT_SIGNS, *SESSION_SIGNS, *SESSION_LEFTOVERS):        # the live agent's own runtime names it and its chat, never the launcher's
            env.pop(k, None)
        try:
            r = subprocess.run(argv, cwd=root, env=env, capture_output=True, text=True, timeout=timeout)
            return r.stdout
        except subprocess.TimeoutExpired as exc:
            return (exc.stdout or "") if isinstance(exc.stdout, str) else ""
    return play


def report(scored: list[tuple[Case, Context, list[Result]]]) -> dict:
    cases = []
    for case, ctx, results in scored:
        passed = sum(r.ok for r in results)
        calls = ctx.calls
        cases.append({"case": case.name, "about": case.about, "passed": passed, "of": len(results),
                      "score": round(passed / len(results), 3) if results else 1.0,
                      "checks": [{"check": r.check, "ok": r.ok, "why": r.why} for r in results],
                      **({"rb_calls": len(calls), "rb_errors": sum(1 for x in calls if x.get("exit"))} if calls else {})})
    total = sum(x["passed"] for x in cases)
    of = sum(x["of"] for x in cases)
    return {"passed": total, "of": of, "score": round(total / of, 3) if of else 1.0, "cases": cases}


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Run the agent contract (bench/agent/SPEC.md).")
    ap.add_argument("--case", action="append", choices=sorted(BY_NAME), help="run only these cases (repeatable)")
    ap.add_argument("--agent-cmd", default=None, help="a real agent's command for the first turn; {prompt} is the user's message")
    ap.add_argument("--next-cmd", default=None, help="the command for later turns (default: --agent-cmd)")
    ap.add_argument("--timeout", type=float, default=900.0, help="seconds per turn for a real agent")
    ap.add_argument("--json", action="store_true", help="print the report as JSON")
    ap.add_argument("--keep", action="store_true", help="keep the scratch directories, and say where they are")
    args = ap.parse_args(argv)
    cases = [BY_NAME[n] for n in args.case] if args.case else CASES
    workdir = Path(tempfile.mkdtemp(prefix="rb-agent-contract-"))
    if args.agent_cmd:
        player = live(args.agent_cmd, args.next_cmd, workdir, args.timeout)
    else:
        from .scripted import IDEAL
        player = scripted(IDEAL)
    gitcfg = workdir / "gitconfig"
    gitcfg.write_text("[user]\n\tname = researcher\n\temail = researcher@example.com\n[commit]\n\tgpgsign = false\n", encoding="utf-8")
    # the setup and both players inherit these: a benchmark is nobody's usage, so rb counts and sends none of it, and
    # keeps its settings (analytics, a hub sign-in) in the scratch directory, never the user's
    scoped = {"GIT_CONFIG_GLOBAL": str(gitcfg), "GIT_CONFIG_NOSYSTEM": "1", "RB_TELEMETRY_SUPPRESS": "1",
              "RB_CONFIG_DIR": str(workdir / "settings")}
    saved = {k: os.environ.get(k) for k in scoped}
    os.environ.update(scoped)
    try:
        scored = []
        for case in cases:
            ctx, results = play_case(case, player, workdir)
            scored.append((case, ctx, results))
        rep = report(scored)
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        if not args.keep:
            shutil.rmtree(workdir, ignore_errors=True)
    if args.json:
        print(json.dumps(rep, indent=1))
    else:
        for c in rep["cases"]:
            print(f"{c['case']:<13} {c['passed']}/{c['of']}" + (f"  ({c['rb_calls']} rb commands, {c['rb_errors']} failed)" if "rb_calls" in c else ""))
            for r in c["checks"]:
                print(f"  {'pass' if r['ok'] else 'FAIL'}  {r['check']}: {r['why']}")
        print(f"\n{rep['passed']} of {rep['of']} checks pass ({rep['score']:.0%})." + (f" Scratch kept in {workdir}" if args.keep else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
