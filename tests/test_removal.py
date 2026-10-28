"""Leaving rb: what rb writes outside its record is kept in its ownership ledger, and rb remove takes back what is rb's.

The fixture is a research repository under way (tests/fixtures/repo_study.py: result folders, a manifest, audit
scripts, a reproduction guide, one commit), with a SQLite results database in WAL mode (its -wal and -shm beside it)
and a script nobody committed. Every name and number is synthetic. The user is human:ana, at their own terminal.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from rbtest import AGENT_VARS, command, environment

sys.path.insert(0, str(Path(__file__).parent / "fixtures"))
import repo_study  # noqa: E402

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="the record, its receipts and its refs are git's")

NOW = "2026-10-12T09:00:00.000+00:00"
# an agent session, as Claude Code's runtime sets one: made up, as every id here is
SESSION = {"CLAUDECODE": "1", "CLAUDE_CODE_SESSION_ID": "sess-removal-3c9e"}
UNTRACKED = "research/warmup/sweep_next.sh"


class Study:
    """The repository at base/study, rb run there as human:ana (or as `who`), with HOME, TMPDIR and git's own
    configuration of the test's own, so what rb writes anywhere is in what `fingerprint` reads."""

    def __init__(self, base: Path) -> None:
        self.base = base
        self.root = base / "study"
        self.home = base / "home"
        self.tmp = base / "tmp"
        for d in (self.home, self.tmp):
            d.mkdir(parents=True)
        cfg = base / "gitconfig"
        cfg.write_text("[user]\n\temail = ana@example.org\n\tname = ana\n[init]\n\tdefaultBranch = main\n[core]\n\tautocrlf = false\n", encoding="utf-8")
        self.env = {k: v for k, v in environment().items() if k not in AGENT_VARS}
        self.env.update(GIT_CONFIG_GLOBAL=str(cfg), GIT_CONFIG_NOSYSTEM="1", RB_TELEMETRY="0", DO_NOT_TRACK="1", RB_ACTOR="human:ana",
                        HOME=str(self.home), USERPROFILE=str(self.home), TMPDIR=str(self.tmp), TMP=str(self.tmp), TEMP=str(self.tmp),
                        RB_TEST_NOW=NOW, RB_TEST_SEED="removal")

    def git(self, *args: str, cwd: Path | None = None) -> str:
        r = subprocess.run(["git", *args], cwd=cwd or self.root, env={**self.env, "GIT_OPTIONAL_LOCKS": "0"}, capture_output=True, text=True)
        assert r.returncode == 0, (args, r.stderr)
        return r.stdout

    def run(self, *argv: str, cwd: Path | None = None, who: dict | None = None, stdin: str | None = None) -> subprocess.CompletedProcess:
        env = {**self.env, **(who or {})}
        if who and any(k in who for k in ("CLAUDECODE", "CODEX_THREAD_ID")):
            env.pop("RB_ACTOR", None)
        return subprocess.run([*command(), *argv], cwd=cwd or self.root, env=env, capture_output=True, encoding="utf-8", errors="replace",
                              input=stdin, stdin=None if stdin is not None else subprocess.DEVNULL)

    def rb(self, *argv: str, cwd: Path | None = None, who: dict | None = None) -> dict:
        """rb <argv> --json, which must do its job: its envelope's data."""
        r = self.run(*argv, "--json", cwd=cwd, who=who)
        out = json.loads(r.stdout)
        assert out["ok"] and r.returncode in (0, 1), (argv, r.returncode, out.get("errors"), r.stderr)
        return out["data"]

    def look(self) -> None:
        """rb ui's look at the record (its seen marker), as the screen asks rb for it, in a process of its own."""
        code = "import sys; from pathlib import Path; from rabbit_brain import tui; tui.ask(Path(sys.argv[1]), 'mark_seen')"
        r = subprocess.run([sys.executable, "-c", code, str(self.root)], cwd=self.root, env=self.env, capture_output=True, text=True)
        assert r.returncode == 0, r.stderr

    def common(self) -> Path:
        return Path(self.git("rev-parse", "--path-format=absolute", "--git-common-dir").strip())

    def ledger(self) -> list:
        """The ownership ledger's lines, each path whole (the ledger keeps one in the checkout from its own folder)."""
        path = self.common() / "rb" / "owned.jsonl"
        lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()] if path.is_file() else []
        for line in lines:
            if line["how"] != "ref" and not Path(line["path"]).is_absolute():
                line["path"] = os.path.normpath(path.parent / line["path"]).replace(os.sep, "/")
        return lines


def results_db(path: Path) -> None:
    """A results database in WAL mode, as a sweep left it: every write checkpointed, its -wal (empty) and -shm beside it."""
    con = sqlite3.connect(path)
    con.execute("pragma journal_mode=wal")
    con.execute("create table runs (arm text, seed integer, steps real, final_loss real)")
    con.executemany("insert into runs values (?, ?, ?, ?)",
                    [("warmup", 1, 0.91, 2.31), ("baseline", 1, 1.0, 2.33), ("warmup", 2, 0.89, 2.30), ("baseline", 2, 1.01, 2.34)])
    con.commit()
    con.execute("pragma wal_checkpoint(truncate)")
    beside = {suffix: Path(f"{path}{suffix}").read_bytes() for suffix in ("-wal", "-shm")}
    con.close()
    for suffix, data in beside.items():
        Path(f"{path}{suffix}").write_bytes(data)


def study(base: Path, signs: bool = False) -> Study:
    """repo_study's repository, committed, with results.db (WAL) committed beside it and an untracked script; with
    `signs`, the agents' files a team keeps: .claude/, an AGENTS.md and a .mcp.json with another server, in 4 spaces."""
    rec = Study(base)
    rec.root.mkdir()
    repo_study.build(rec.root, commit=False)
    results_db(rec.root / "results.db")
    if signs:
        (rec.root / ".claude").mkdir()
        (rec.root / "AGENTS.md").write_bytes(b"# House rules\r\n\r\nRun the tests before a commit.\r\n")
        (rec.root / ".mcp.json").write_bytes(b'{\n    "mcpServers": {\n        "other": {\n            "command": "other-server"\n        }\n    }\n}\n')
    rec.git("add", "-A")
    rec.git("-c", "commit.gpgsign=false", "commit", "-qm", "checkpoint the warmup study")
    (rec.root / UNTRACKED).write_text("#!/bin/sh\npython -m research.warmup.run_transfer --widths 16\n", encoding="utf-8", newline="\n")
    return rec


def fingerprint(rec: Study) -> dict:
    """Everything a write anywhere could change: each file outside .git with its bytes and time, each directory's time
    (kept apart: a directory's time moves whenever an entry comes and goes), each file in .git but its objects, the refs,
    git status (ignored files too), the worktree list, the reachable objects (as git gc keeps them), every loose object,
    git fsck, the HOME tree and rb's files in TMPDIR."""
    files, dirs, git_files, git_dirs, loose = {}, {}, {}, set(), set()
    for dirpath, dirnames, filenames in os.walk(rec.root):
        rel = Path(dirpath).relative_to(rec.root).as_posix()
        if rel == ".git" or rel.startswith(".git/"):
            if rel.startswith(".git/objects"):
                if len(Path(dirpath).name) == 2:
                    loose.update(Path(dirpath).name + f for f in filenames)
                continue
            git_dirs.add(rel)
            for name in filenames:
                p = Path(dirpath, name)
                git_files[f"{rel}/{name}"] = (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns)
            continue
        dirs[rel] = os.stat(dirpath).st_mtime_ns
        for name in filenames:
            p = Path(dirpath, name)
            files[(Path(rel) / name).as_posix()] = (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns)
    home = sorted(p.relative_to(rec.home).as_posix() for p in rec.home.rglob("*"))
    return {
        "files": files, "dirs": dirs, "git": git_files, "git_dirs": git_dirs, "loose": loose, "home": home,
        "tmp": sorted(p.name for p in rec.tmp.iterdir() if p.name.startswith("rb")),
        "refs": rec.git("for-each-ref", "--format=%(refname) %(objectname)"), "head": rec.git("rev-parse", "HEAD"),
        "status": rec.git("status", "--porcelain=v1", "--ignored", "-uall"), "worktrees": rec.git("worktree", "list", "--porcelain"),
        "reachable": set(rec.git("rev-list", "--objects", "--all", "--reflog", "--indexed-objects").split()),
        "fsck": subprocess.run(["git", "fsck", "--no-dangling", "--no-progress"], cwd=rec.root, env=rec.env, capture_output=True, text=True).returncode,
    }


def changed_paths(before: dict, after: dict) -> set:
    """The paths whose bytes, time or presence changed: files outside .git by their path, .git's by theirs."""
    out = set()
    for key in ("files", "git"):
        out |= {k for k in set(before[key]) | set(after[key]) if before[key].get(k) != after[key].get(k)}
    return out


def covered(path: str, rec: Study, lines: list) -> bool:
    """Whether a changed path (from the repository's top) is one the ledger keeps, or lies in a folder it keeps, or
    is a ref under refs/rb/."""
    if path.startswith(".git/refs/rb/"):
        return True
    full = (rec.root / path).resolve().as_posix()
    for line in lines:
        kept = line["path"]
        if full == kept or full.startswith(kept.rstrip("/") + "/"):
            return True
    return False


def test_every_write_rb_makes_outside_the_record_is_in_its_ledger(tmp_path):
    """Item 18: after init (installing rb's instructions beside an agent's folder), a run on uncommitted code, a
    journal scan, a page, an export and an export zip at their defaults, and rb ui's look (the seen marker), every path
    outside .rb/ that changed is in the ownership ledger, or in a folder it keeps, or a ref under refs/rb/. The ledger
    is outside the record: no file in .rb/ names it."""
    rec = study(tmp_path, signs=False)
    (rec.root / ".claude").mkdir()
    # code changed since the commit, which the run's receipt keeps a tree of
    (rec.root / "research" / "warmup" / "run_transfer.py").write_text("def main():\n    return 1\n", encoding="utf-8", newline="\n")
    before = fingerprint(rec)
    rec.rb("init", "Warmup study")
    rec.rb("experiment", "add", "warmup against cosine only", "--id", "warmup", "--baseline", "baseline", "--candidate", "warmup")
    rec.rb("run", "add", "warmup", "warmup.steps=0.9", "baseline.steps=1.0")
    rec.rb("journal", "scan")
    rec.rb("page")
    rec.rb("export")
    rec.rb("export", "--zip")
    rec.look()
    after = fingerprint(rec)
    lines = rec.ledger()
    hows = {line["how"] for line in lines}
    assert {"created", "ref", "output", "dir"} <= hows, hows
    left = sorted(p for p in changed_paths(before, after) if not p.startswith(".rb/") and not covered(p, rec, lines))
    assert left == [], left
    assert all(line["command"] and line["scope"] == "repo" and line["at"] == NOW for line in lines), lines
    assert not any("owned" in p.name for p in (rec.root / ".rb").rglob("*"))


# ---------------------------------------------------------------- the round trip: try rb, leave it, nothing of it left

class Served:
    """rb open in the study, with the study's environment, on a port of its own."""

    def __init__(self, rec: Study) -> None:
        import re
        import time
        self.err = rec.base / "open-said.txt"
        self.out = open(self.err, "wb")
        own_group = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if sys.platform == "win32" else {}
        self.proc = subprocess.Popen([*command(), "open", "--port", "0"], cwd=rec.root, env=rec.env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                     stderr=self.out, **own_group)
        deadline = time.monotonic() + 60
        while True:
            said = self.err.read_text(encoding="utf-8", errors="replace")
            link = re.search(r"^rb open · (http://[0-9.]+:\d+)/$", said, flags=re.M)
            if link and said.rstrip().endswith("stops the server."):
                self.url = link.group(1)
                return
            assert self.proc.poll() is None and time.monotonic() < deadline, said
            time.sleep(0.05)

    def get(self, route: str, headers: dict | None = None) -> int:
        import urllib.error
        import urllib.request
        req = urllib.request.Request(self.url + route, headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                r.read()
                return r.status
        except urllib.error.HTTPError as e:
            return e.code

    def close(self) -> None:
        import signal
        self.proc.send_signal(signal.CTRL_BREAK_EVENT if sys.platform == "win32" else signal.SIGINT)
        self.proc.wait(timeout=60)
        self.out.close()
        self.err.unlink()


def same_but_dirs_and_residue(before: dict, after: dict, residue: list) -> list:
    """What differs between two fingerprints, but directory times and the loose objects rb named as residue (which
    rb deletes none of: git's own gc takes them in its time). Which directories there are is compared: a folder rb
    made and left behind empty is a difference."""
    diff = []
    for key in before:
        if key == "dirs":
            if set(before["dirs"]) != set(after["dirs"]):
                diff.append(("dirs", sorted(set(before["dirs"]) ^ set(after["dirs"]))[:10]))
            continue
        if key == "loose":
            extra = after["loose"] - before["loose"]
            if extra != set(residue) & extra or not before["loose"] <= after["loose"]:
                diff.append(("loose", sorted(extra - set(residue))[:5], sorted(before["loose"] - after["loose"])[:5]))
            continue
        if before[key] != after[key]:
            if isinstance(before[key], dict):
                diff.append((key, sorted(k for k in set(before[key]) | set(after[key]) if before[key].get(k) != after[key].get(k))[:10]))
            else:
                diff.append((key, before[key] if not isinstance(before[key], set) else sorted(before[key] ^ after[key])[:5], after[key] if not isinstance(after[key], set) else ""))
    return diff


def play(rec: Study, who: dict | None = None) -> dict:
    """What a researcher's first afternoon with rb writes: the start protocol's writes (a question, an experiment, a
    metric, a setting from a file, a criterion, two runs from rows of the WAL database and one on uncommitted code, a
    note), a journal scan, a page and an export at their defaults, one GET of each route rb open serves, and a run from
    a worktree nested in the checkout (which the user then removes). Returns the ids it made."""
    rb = lambda *argv: rec.rb(*argv, who=who)                                     # noqa: E731
    rb("question", "add", "Does warmup cut the steps to the target loss?", "--id", "q-warmup")
    rb("experiment", "add", "warmup against no warmup", "--id", "warmup", "-q", "q-warmup", "--baseline", "baseline", "--candidate", "warmup")
    rb("metric", "add", "steps", "--minimize")
    rb("spec", "set", "warmup", "widths", "--source", "results/warmup_cost/summary.json#widths")
    rb("criterion", "add", "warmup takes fewer steps", "-e", "warmup", "-q", "q-warmup", "--metric", "change.steps", "--at-most", "0", "--id", "c1")
    runs = [rb("run", "add", "warmup", "--from", "results.db", "--table", "runs", "--where", f"arm={arm},seed=1", "--variant", arm)["object"]["id"]
            for arm in ("warmup", "baseline")]
    code = rec.root / "research" / "warmup" / "run_transfer.py"
    kept, at = code.read_bytes(), code.stat()
    code.write_text("def main():\n    return 2\n", encoding="utf-8", newline="\n")
    runs.append(rb("run", "add", "warmup", "warmup.steps=0.88", "baseline.steps=1.0")["object"]["id"])
    code.write_bytes(kept)                                        # the user's change, undone as it was
    os.utime(code, ns=(at.st_atime_ns, at.st_mtime_ns))
    rb("note", "add", "the widths come from the cost summary", "-e", "warmup")
    rb("journal", "scan")
    rb("page")
    rb("export")
    server = Served(rec)
    try:
        for route in ("/", "/index.html", "/state.json", f"/card.json?ref=o:{runs[0]}", "/page.html"):
            assert server.get(route) == 200, route
        assert server.get("/telemetry/inspected", {"X-RB-Usage": "1", "Sec-Fetch-Site": "same-origin"}) == 200
    finally:
        server.close()
    wt = rec.root / ".worktrees" / "wide"
    rec.git("worktree", "add", "-q", "--detach", str(wt))
    (wt / "research" / "warmup" / "run_transfer.py").write_text("def main():\n    return 3\n", encoding="utf-8", newline="\n")
    runs.append(rec.rb("run", "add", "warmup", "warmup.steps=0.87", "baseline.steps=1.0", cwd=wt, who=who)["object"]["id"])
    rec.git("worktree", "remove", "--force", str(wt))
    rec.git("worktree", "prune")
    shutil.rmtree(rec.root / ".worktrees", ignore_errors=True)       # the folder the user made for it
    return {"runs": runs}


@pytest.mark.parametrize("signs", [False, True], ids=["no agent files", "agent files, --yes"])
def test_trying_rb_and_removing_it_leaves_the_repository_as_it_was(tmp_path, signs):
    """Item 21's round trip: the preview commands write nothing; rb init --dry-run lists what init writes; a first
    afternoon of work; rb remove --dry-run, then rb remove --yes. Afterwards every file outside .git (bytes and times),
    every file in .git outside its objects, the refs, git status, the worktree list, the reachable objects, git fsck,
    HOME and rb's files in TMPDIR are as before rb: only directory times differ, and the loose objects rb named as
    residue, left for git's gc. What the dry run said it would remove is what it removed, and the database and its -wal
    and -shm were never touched."""
    rec = study(tmp_path, signs=signs)
    before = fingerprint(rec)
    db = {s: (rec.root / f"results.db{s}").read_bytes() for s in ("", "-wal", "-shm")}
    db_times = {s: (rec.root / f"results.db{s}").stat().st_mtime_ns for s in db}
    for argv in (("compile", "results.db", "research"), ("status",), ("reconcile",), ("docs", "--start")):
        r = rec.run(*argv)
        assert r.returncode in (0, 1) or (argv == ("status",) and "rb init" in r.stderr), (argv, r.stderr)
    assert fingerprint(rec) == before                               # the preview commands write nothing
    plan = rec.rb("init", "Warmup study", "--dry-run", *(["--yes"] if signs else []))["plan"]
    assert fingerprint(rec) == before
    rec.rb("init", "Warmup study", *(["--yes"] if signs else []))
    made = changed_paths(before, fingerprint(rec))
    assert all(any(p == q or (q.endswith("/") and p.startswith(q)) for q in [x["path"] for x in plan]) for p in made), made
    play(rec)
    said = rec.rb("remove", "--dry-run")
    assert said["others"] == [] and said["keep"] == [], said["keep"]
    done = rec.rb("remove", "--yes")
    assert done["removed"] == [i["path"] for i in said["remove"]] and done["failed"] == []
    after = fingerprint(rec)
    assert same_but_dirs_and_residue(before, after, said["residue"]) == []
    assert {s: (rec.root / f"results.db{s}").read_bytes() for s in db} == db and {s: (rec.root / f"results.db{s}").stat().st_mtime_ns for s in db} == db_times
    assert after["fsck"] == 0 and after["reachable"] == before["reachable"]


def test_an_agent_session_s_record_is_removed_by_the_user(tmp_path):
    """An agent session starts the record and does the afternoon's work (installing nothing, keeping its session files
    in .rb/cache/); it cannot remove rb, but its dry run shows the plan; the user's rb remove leaves the repository
    as it was."""
    rec = study(tmp_path, signs=True)
    before = fingerprint(rec)
    rec.rb("init", "Warmup study", who=SESSION)
    play(rec, who=SESSION)
    r = rec.run("remove", "--yes", who=SESSION)
    assert r.returncode == 2 and "this is an agent session" in r.stderr
    said = rec.rb("remove", "--dry-run", who=SESSION)
    done = rec.rb("remove", "--yes")
    assert done["removed"] == [i["path"] for i in said["remove"]]
    assert same_but_dirs_and_residue(before, fingerprint(rec), said["residue"]) == []


def test_a_committed_record_comes_out_of_the_working_tree_as_the_deletions_git_shows(tmp_path):
    """With .rb/ committed, rb remove deletes its files from the working tree only, as its plan says (git will show N
    files deleted; history keeps them): every other file is as before rb, and git status shows exactly those deletions."""
    rec = study(tmp_path)
    before = fingerprint(rec)
    rec.rb("init", "Warmup study")
    play(rec)
    rec.git("add", ".rb")
    rec.git("-c", "commit.gpgsign=false", "commit", "-qm", "the record")
    tracked = rec.git("ls-files", ".rb").split()
    said = rec.rb("remove", "--dry-run")
    assert said["tracked_deleted"] == len(tracked) and said["uncommitted"] == 0
    assert f"git will show {len(tracked)} files deleted; history keeps them." in rec.run("remove", "--dry-run").stdout
    rec.rb("remove", "--yes")
    after = fingerprint(rec)
    assert after["files"] == before["files"]
    status = [line for line in after["status"].splitlines()]
    assert sorted(s for s in status if s.startswith(" D ")) == sorted(f" D {p}" for p in tracked)
    assert [s for s in status if not s.startswith(" D ")] == [s for s in before["status"].splitlines()]


def test_with_a_nested_and_an_outside_worktree_each_checkout_removes_its_own(tmp_path):
    """While a worktree outside the checkout holds a record of its own, rb remove in the checkout keeps what the clone
    holds for rb and names that worktree with its command; run there, it takes the rest. Once the user removes the
    worktree, the repository is as it was before rb."""
    rec = study(tmp_path)
    before = fingerprint(rec)
    rec.rb("init", "Warmup study")
    play(rec)
    out = rec.base / "wt-out"
    rec.git("worktree", "add", "-q", "--detach", str(out))           # no branch: deleting one rewrites git's config
    rec.rb("init", "Warmup transfer", "--new-project", cwd=out)
    rec.rb("experiment", "add", "transfer", "--id", "transfer", "--baseline", "baseline", "--candidate", "warmup", cwd=out)
    (out / "research" / "warmup" / "run_transfer.py").write_text("def main():\n    return 4\n", encoding="utf-8", newline="\n")
    rec.rb("run", "add", "transfer", "warmup.steps=1.09", "baseline.steps=1.0", cwd=out)
    (out / "research" / "warmup" / "run_transfer.py").write_bytes((rec.root / "research" / "warmup" / "run_transfer.py").read_bytes())
    here = rec.rb("remove", "--dry-run")
    [other] = here["others"]
    assert other["command"].startswith("cd ") and other["command"].endswith("wt-out && rb remove") and "detached at " in other["place"]
    assert any(k["path"].startswith("refs/rb/") for k in here["keep"])
    rec.rb("remove", "--yes")
    there = rec.rb("remove", "--dry-run", cwd=out)
    assert there["others"] == [] and any(i["path"].startswith("refs/rb/") for i in there["remove"])
    rec.rb("remove", "--yes", cwd=out)
    residue = here["residue"] + there["residue"]
    rec.git("worktree", "remove", str(out))
    assert same_but_dirs_and_residue(before, fingerprint(rec), residue) == []


def test_a_linked_worktree_s_first_journal_is_in_the_ledger(tmp_path):
    """A worktree's journal folder, made at its first look there, is kept in the ownership ledger with rb's folder inside
    git's, which rb init's own look made with the main checkout's journal."""
    rec = study(tmp_path)
    rec.rb("init", "Warmup study")
    rec.git("add", ".rb")
    rec.git("-c", "commit.gpgsign=false", "commit", "-qm", "the record")
    wt = rec.base / "wt-a"
    rec.git("worktree", "add", "-q", "--detach", str(wt))
    assert {(line["how"], Path(line["path"]).name) for line in rec.ledger()} == {("dir", "rb"), ("dir", "main")}
    rec.rb("journal", "scan", cwd=wt)
    kept = {(line["how"], Path(line["path"]).name) for line in rec.ledger()}
    assert ("dir", "rb") in kept and ("dir", "wt-a") in kept, kept
