"""The golden-record harness: command scripts run with the clock, the ids, the version and the machine fixed (RB_TEST_NOW,
RB_TEST_SEED, RB_TEST_VERSION, RB_TEST_ENV), and what they say and leave behind held byte for byte: every command's exit
code, stdout and stderr, and every file left in the working tree (.rb/ included; the gzipped copies under .rb/cache/ by
their content, a zip by its entries).

    python3 rust/tests_py/golden.py rust/target/release/rb                the binary against what is stored (golden/*.json)
    python3 rust/tests_py/golden.py rust/target/release/rb basics runs    the named scenarios
    python3 rust/tests_py/golden.py --record rust/target/release/rb       store what this binary does, after a change meant to change it
    ... -j 4                                                              four scenarios at a time

What is stored names the directory a scenario ran in @BASE@. Each scenario runs in the same directory every time
(/tmp/rb-golden/<scenario>): rb keeps a linked worktree's whole path in the record, and a hash of text that names it (a
run's identity, a commit that holds the record) holds only where that path does. /tmp/rb-golden must be a directory of
your own, never a link; its lock files go when their scenarios end. Files the scripts write get a fixed
modification time per step, so the times rb reads off them agree too. One file is not compared:
.rb/cache/environment.json, where rb remembers the project Python's answer about the environment when RB_TEST_ENV
does not pin one."""
import concurrent.futures
import contextlib
import difflib
import gzip
import hashlib
import json
import os
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
from golden_scenarios import SCENARIOS  # noqa: E402

AGENT_ENV = ("CLAUDECODE", "AI_AGENT", "GITHUB_ACTIONS", "RB_ACTOR", "RB_SESSION", "CLAUDE_CODE_SESSION_ID", "CURSOR_TRACE_ID",
             "CURSOR_SESSION_ID", "CURSOR_CONVERSATION_ID", "CURSOR_AGENT", "CODEX_THREAD_ID", "CODEX_SESSION_ID", "GROK_SESSION_ID",
             "GITHUB_RUN_ID", "RB_MODEL", "RB_PROVIDER", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY",
             "CLAUDE_CODE_USE_ANTHROPIC_AWS", "CLAUDE_CODE_USE_ANTHROPIC_GOOGLE_CLOUD", "CLAUDE_CODE_USE_MANTLE", "CLAUDE_CODE_USE_GATEWAY",
             "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_CHILD_SESSION", "CLAUDE_CODE_BRIDGE_SESSION_ID", "CLAUDE_CODE_EXECPATH", "CLAUDE_PID",
             "CLAUDE_CODE_MESSAGING_SOCKET", "CLAUDE_CODE_MESSAGING_TOKEN", "CLAUDE_CODE_SESSION_ATTENDED", "CLAUDE_CODE_TEAMMATE_MODE", "CLAUDE_CODE_SPAWN_BACKEND")
NOW = "2026-10-01T12:00:00.000+00:00"
GIT_DATE = "2026-10-01T12:00:00+00:00"
EPOCH = 1790856000          # 2026-10-01T12:00:00Z: files written by step n are dated EPOCH - 3600 + n
RUST_ONLY = ".rb/cache/environment.json"        # never compared: in the project and in any worktree of it
VERSION = "0.0.0"           # RB_TEST_VERSION: the version rb says it is here, so what is expected outlives a release
# RB_TEST_ENV: the machine every run's receipt names here, whatever machine this runs on
MACHINE = {"python": "3.12.1", "platform": "Linux-6.1.0-x86_64-with-glibc2.39", "torch": "2.4.1", "numpy": "2.0.2", "pydantic": "2.9.2"}


def environment(home: str, extra: dict) -> dict:
    """The caller's environment with everything that names a machine, a human or a session taken out or fixed."""
    env = {k: v for k, v in os.environ.items() if k not in AGENT_ENV and not k.startswith(("CODEX_", "RB_", "XDG_", "LC_", "GIT_"))}
    cfg = os.path.join(home, "gitconfig")
    with open(cfg, "w", encoding="utf-8") as f:
        f.write("[user]\n\temail = t@example.com\n\tname = t\n[init]\n\tdefaultBranch = main\n")
    env.update({"HOME": home, "GIT_CONFIG_GLOBAL": cfg, "GIT_CONFIG_NOSYSTEM": "1", "GIT_AUTHOR_DATE": GIT_DATE,
                "GIT_COMMITTER_DATE": GIT_DATE, "RB_TEST_SEED": "golden", "RB_TEST_NOW": NOW, "RB_PYTHON": sys.executable,
                "RB_TEST_VERSION": VERSION, "RB_TEST_ENV": json.dumps(MACHINE), "LOGNAME": "golden", "USER": "golden",
                "PYTHONHASHSEED": "0", "TZ": "UTC", "COLUMNS": "120", "LANG": "C", "LC_ALL": "C"})
    env.update(extra)
    return env


def snapshot(root: str) -> dict:
    files = {}
    for d, dirs, names in os.walk(root):
        dirs[:] = sorted(x for x in dirs if x != ".git")
        for n in sorted(x for x in names if x != ".git"):
            p = os.path.join(d, n)
            rel = os.path.relpath(p, root)
            if rel.endswith(".lock"):
                continue
            data = open(p, "rb").read()
            if rel.endswith(".gz"):
                data = b"gz:" + gzip.decompress(data)
            elif rel.endswith(".zip"):
                # a zip by what it holds: each entry's name and bytes (its compression and its file times are not rb's)
                try:
                    with zipfile.ZipFile(p) as z:
                        data = b"zip:" + b"".join(i.filename.encode() + b"\0" + z.read(i) + b"\0" for i in z.infolist())
                except zipfile.BadZipFile:
                    data = b"badzip:" + data
            files[rel] = data
    return files


def date_new_files(workdir: str, before: dict, stamp: int) -> None:
    """Give every file a step created or changed (outside .git and .rb) the step's fixed time."""
    for rel, sig in files_outside_rb(workdir).items():
        if before.get(rel) != sig:
            os.utime(os.path.join(workdir, rel), (stamp, stamp))


def files_outside_rb(workdir: str) -> dict:
    out = {}
    for d, dirs, names in os.walk(workdir):
        dirs[:] = [x for x in dirs if x not in (".git", ".rb")]
        names = [n for n in names if n != ".git"]
        for n in names:
            p = os.path.join(d, n)
            st = os.stat(p)
            out[os.path.relpath(p, workdir)] = (st.st_size, st.st_mtime_ns, open(p, "rb").read())
    return out


def run(argv_head: list, scenario, workdir: str, home: str) -> tuple:
    env = environment(home, scenario.get("env", {}))
    results = []
    for n, step in enumerate(scenario["steps"]):
        kind = step[0]
        stamp = EPOCH - 3600 + n
        if kind == "write":
            path = os.path.join(workdir, step[1])
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(step[2])
            os.utime(path, (stamp, stamp))
            continue
        if kind == "copy":
            shutil.copytree(os.path.join(REPO, step[1]), os.path.join(workdir, step[2]))
            continue
        if kind == "sh":
            before = files_outside_rb(workdir)
            subprocess.run(step[1], shell=True, cwd=workdir, env=env, check=True, capture_output=True)
            date_new_files(workdir, before, stamp)
            continue
        if kind == "rbsh":
            # an rb command line the shell expands first ($RB is the implementation), for ids only rb knows
            step_env = dict(env, RB=shlex.join(argv_head), **(step[2] if len(step) > 2 else {}))
            before = files_outside_rb(workdir)
            r = subprocess.run(step[1], shell=True, cwd=workdir, env=step_env, capture_output=True, stdin=subprocess.DEVNULL, timeout=600)
            date_new_files(workdir, before, stamp)
            results.append((step[1], r.returncode, r.stdout, r.stderr))
            continue
        args = shlex.split(step[1]) if isinstance(step[1], str) else list(step[1])
        cwd = os.path.join(workdir, step[2]) if len(step) > 2 and step[2] else workdir
        step_env = dict(env, **(step[3] if len(step) > 3 else {}))
        before = files_outside_rb(workdir)
        r = subprocess.run([*argv_head, *args], cwd=cwd, env=step_env, capture_output=True, stdin=subprocess.DEVNULL, timeout=600)
        date_new_files(workdir, before, stamp)
        results.append((step[1], r.returncode, r.stdout, r.stderr))
    return results, snapshot(workdir)


def show_diff(a: bytes, b: bytes, label: str, names: tuple = ("stored", "now")) -> str:
    al = a.decode("utf-8", "replace").splitlines()
    bl = b.decode("utf-8", "replace").splitlines()
    return "\n".join(list(difflib.unified_diff(al, bl, f"{names[0]} {label}", f"{names[1]} {label}", lineterm="", n=1))[:40])


def play(head: list, scenario, base: str) -> tuple:
    """One implementation through a scenario, in <base>/work with HOME at <base>/home: (step results, files left)."""
    workdir, home = os.path.join(base, "work"), os.path.join(base, "home")
    shutil.rmtree(workdir, ignore_errors=True)
    shutil.rmtree(home, ignore_errors=True)
    os.makedirs(workdir)
    os.makedirs(home)
    subprocess.run(["git", "init", "-q"], cwd=workdir, check=True, env=environment(home, {}), capture_output=True)
    results, files = run(head, scenario, workdir, home)
    return results, {r: d for r, d in files.items() if not r.endswith(RUST_ONLY)}


# ---------------------------------------------------------------- what is stored

STORED = os.path.join(HERE, "golden")
BASE = b"@BASE@"
TEXT_LIMIT = 64 * 1024          # a file larger than this, or not text, is stored by its hash


def lines_of(data: bytes) -> list:
    return data.decode("utf-8", "surrogateescape").split("\n")


def bytes_of(lines: list) -> bytes:
    return "\n".join(lines).encode("utf-8", "surrogateescape")


def stored_file(data: bytes):
    """A file as it is stored: its lines when it is text of a readable size, otherwise its hash and size."""
    if len(data) <= TEXT_LIMIT and not data.startswith((b"gz:", b"zip:", b"badzip:")):
        try:
            data.decode("utf-8")
            if b"\0" not in data:
                return lines_of(data)
        except UnicodeDecodeError:
            pass
    return {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}


def expectation(results: list, files: dict, base: str) -> dict:
    """What a scenario said and left, with the directory it ran in named @BASE@."""
    here = os.path.realpath(base).encode()
    plain = lambda data: data.replace(here, BASE)   # noqa: E731
    return {"steps": [{"run": cmd if isinstance(cmd, str) else shlex.join(cmd), "exit": code, "out": lines_of(plain(out)), "err": lines_of(plain(err))}
                      for cmd, code, out, err in results],
            "files": {rel: stored_file(plain(data)) for rel, data in sorted(files.items())}}


def stored_path(name: str) -> str:
    return os.path.join(STORED, f"{name}.json")


def head_of(implementation: str) -> list:
    return [os.path.abspath(implementation)]


# where scenarios run: the same path for a scenario on every run and every machine
RUN_ROOT = "/tmp/rb-golden" if os.name == "posix" else os.path.join(tempfile.gettempdir(), "rb-golden")


def run_root_problem():
    """Why the scenarios cannot run in RUN_ROOT, in one line, or None. Under /tmp any user can make that path first: a
    link or a directory of another user's would choose what the harness empties and writes."""
    try:
        st = os.lstat(RUN_ROOT)
    except FileNotFoundError:
        return None
    if stat.S_ISLNK(st.st_mode):
        return f"{RUN_ROOT} is a symbolic link, not a directory of your own: remove the link and run again"
    if not stat.S_ISDIR(st.st_mode):
        return f"{RUN_ROOT} is not a directory: remove it and run again"
    if hasattr(os, "getuid") and st.st_uid != os.getuid():
        return f"{RUN_ROOT} belongs to another user (uid {st.st_uid}), not to you: remove it or run as its owner"
    if st.st_mode & stat.S_IWOTH:
        return f"{RUN_ROOT} can be written by every user: remove it and run again"
    if st.st_mode & stat.S_IWGRP:
        return f"{RUN_ROOT} can be written by its group: remove it and run again"
    return None


def make_run_root() -> None:
    """RUN_ROOT as a directory only its user can enter, or SystemExit with why it cannot be."""
    with contextlib.suppress(FileExistsError):
        os.mkdir(RUN_ROOT, 0o700)
    problem = run_root_problem()
    if problem:
        raise SystemExit(problem)


@contextlib.contextmanager
def scenario_dir(name: str):
    """The scenario's own directory under RUN_ROOT, emptied first and taken away after, with its lock file; another
    harness playing the same scenario on this machine waits for it."""
    make_run_root()
    path = os.path.join(RUN_ROOT, f"{name}.lock")
    while True:
        lock = open(path, "w")
        try:
            import fcntl
            fcntl.flock(lock, fcntl.LOCK_EX)
        except ImportError:                 # Windows, where the goldens are not held
            break
        # the harness that held it took the file away: lock the one at the path now
        with contextlib.suppress(FileNotFoundError):
            if os.stat(path).st_ino == os.fstat(lock.fileno()).st_ino:
                break
        lock.close()
    with lock:
        base = os.path.join(RUN_ROOT, name)
        shutil.rmtree(base, ignore_errors=True)
        os.makedirs(base)
        try:
            yield base
        finally:
            shutil.rmtree(base, ignore_errors=True)
            with contextlib.suppress(FileNotFoundError):
                os.unlink(path)


def played(name: str, scenario, head: list) -> dict:
    """What a scenario said and left, played in its own directory."""
    with scenario_dir(name) as base:
        return expectation(*play(head, scenario, base), base)


def record(name: str, scenario, implementation: str) -> list:
    got = played(name, scenario, head_of(implementation))
    os.makedirs(STORED, exist_ok=True)
    with open(stored_path(name), "w", encoding="utf-8") as f:
        json.dump(got, f, indent=1, ensure_ascii=True)
        f.write("\n")
    return []


def against_stored(name: str, scenario, binary: str) -> list:
    """The binary against what is stored for the scenario."""
    try:
        with open(stored_path(name), encoding="utf-8") as f:
            want = json.load(f)
    except FileNotFoundError:
        return [f"nothing is stored for {name}: python3 rust/tests_py/golden.py --record <binary> {name}"]
    got = played(name, scenario, [binary])
    problems = []
    names = ("stored", "now")
    if len(want["steps"]) != len(got["steps"]):
        problems.append(f"{len(want['steps'])} steps are stored and the scenario has {len(got['steps'])}: record it again")
    for w, g in zip(want["steps"], got["steps"]):
        cmd = g["run"]
        if w["run"] != cmd:
            problems.append(f"the step stored here is rb {w['run']}, and the scenario runs rb {cmd}: record it again")
        if w["exit"] != g["exit"]:
            problems.append(f"rb {cmd}: exit {w['exit']} (stored) vs {g['exit']} (now)")
        if w["out"] != g["out"]:
            problems.append(f"rb {cmd}: stdout differs\n{show_diff(bytes_of(w['out']), bytes_of(g['out']), 'stdout', names)}")
        if w["err"] != g["err"]:
            problems.append(f"rb {cmd}: stderr differs\n{show_diff(bytes_of(w['err']), bytes_of(g['err']), 'stderr', names)}")
        if problems:
            break
    wf, gf = want["files"], got["files"]
    for rel in sorted(set(wf) | set(gf)):
        if rel not in gf:
            problems.append(f"{rel}: stored, and not written now")
        elif rel not in wf:
            problems.append(f"{rel}: written now, and not stored")
        elif wf[rel] != gf[rel]:
            if isinstance(wf[rel], list) and isinstance(gf[rel], list):
                problems.append(f"{rel}: differs\n{show_diff(bytes_of(wf[rel]), bytes_of(gf[rel]), rel, names)}")
            else:
                problems.append(f"{rel}: differs (stored {wf[rel] if isinstance(wf[rel], dict) else 'as text'}, now {gf[rel] if isinstance(gf[rel], dict) else 'text'})")
    return problems


def main() -> int:
    args = sys.argv[1:]
    jobs = 1
    if "-j" in args:
        i = args.index("-j")
        jobs = int(args[i + 1])
        del args[i:i + 2]
    mode = "stored"
    if args and args[0] in ("--stored", "--record"):
        mode = args.pop(0)[2:]
    if not args:
        print(__doc__)
        return 2
    implementation = os.path.abspath(args[0])
    if not os.path.isfile(implementation):
        print(f"no rb at {implementation}: the first argument is the binary to run")
        return 2
    names = args[1:] or list(SCENARIOS)
    unknown = [n for n in names if n not in SCENARIOS]
    if unknown:
        print(f"no scenario called {', '.join(unknown)}: {', '.join(SCENARIOS)}")
        return 2
    problem = run_root_problem()
    if problem:
        print(problem)
        return 2
    work = {"stored": against_stored, "record": record}[mode]
    failed = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(jobs, 1)) as pool:
        for name, problems in zip(names, pool.map(lambda n: work(n, SCENARIOS[n], implementation), names)):
            if problems:
                failed += 1
                print(f"FAIL {name}")
                for p in problems[:12]:
                    print("  " + p.replace("\n", "\n  "))
            else:
                print(f"{'kept' if mode == 'record' else 'ok  '} {name}", flush=True)
    if mode == "record":
        print(f"{len(names)} scenarios stored in {os.path.relpath(STORED)}, as {implementation} runs them")
        return 0
    print(f"{len(names) - failed} of {len(names)} scenarios agree with what is stored")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
