"""EVIDENCE.md E3: a capture git could not make is said, never read as clean, and part of the code kept is kept.

Pinned here, through rb's own commands in git repositories in scratch folders, with git shims on PATH that fail or
sleep for one subcommand and run the real git otherwise:

- A status git could not give (an error, or no answer in RB_TEST_GIT_TIMEOUT_MS): the receipt keeps dirty: true and
  tree: null, with captured, kept none and why; rb run add says it, in data.warnings too.
- A listing that fails (untracked files, or the changed ones) makes no tree.
- A file over 10 MB, or one that is not a regular file, makes the code part kept, each named with why; one git tracks
  is in the tree as its commit holds it.
- Past 2,000 files no tree is kept, and the words name the folder that holds most of them; rb journal run says it
  before the command runs, and keeps its start code out of the entry's `code`.
- Older rb reads every such receipt: the keys are beside the code they describe, and `left_out` stays a count.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from rbtest import as_the_user, command, environment

pytestmark = pytest.mark.skipif(shutil.which("git") is None or sys.platform == "win32", reason="git shims are shell scripts")
GIT = shutil.which("git")


@pytest.fixture
def home(tmp_path, monkeypatch):
    as_the_user(monkeypatch)
    cfg = tmp_path / "gitconfig"
    cfg.write_text("[user]\n\temail = ana@example.org\n\tname = ana\n[init]\n\tdefaultBranch = main\n", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(cfg))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("RB_ACTOR", "human:ana")
    return tmp_path


def rb(cwd: Path, *argv: str, env: dict = None) -> dict:
    r = subprocess.run([*command(), *argv, "--json"], cwd=cwd, env=env or environment(), capture_output=True, text=True, stdin=subprocess.DEVNULL)
    assert r.returncode == 0, (argv, r.returncode, r.stdout, r.stderr)
    return json.loads(r.stdout)


def git(cwd: Path, *argv: str) -> str:
    return subprocess.run([GIT, *argv], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def record(home: Path) -> Path:
    root = home / "int8-resnet50"
    root.mkdir()
    git(root, "init", "-q")
    (root / "train.py").write_text("lr = 0.1\n", encoding="utf-8")
    rb(root, "init", "INT8 ResNet-50", "--no-agents")
    rb(root, "experiment", "add", "INT8 against FP32", "--id", "int8", "--baseline", "fp32", "--candidate", "int8")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "the record")
    (root / "train.py").write_text("lr = 0.2\n", encoding="utf-8")
    return root


def shim(home: Path, match: str, act: str) -> dict:
    """An env whose git does `act` (fail, or sleep) where its arguments hold `match`, and runs the real git otherwise."""
    d = home / f"shim-{abs(hash(match + act)) % 10000}"
    d.mkdir(exist_ok=True)
    body = 'echo "fatal: index file corrupt" >&2; exit 128' if act == "fail" else "sleep 3"
    (d / "git").write_text(f'#!/bin/sh\ncase "$*" in *"{match}"*) {body};; esac\nexec {GIT} "$@"\n', encoding="utf-8")
    (d / "git").chmod(0o755)
    return {**environment(), "PATH": f"{d}{os.pathsep}{os.environ['PATH']}", "RB_TEST_GIT_TIMEOUT_MS": "500"}


def add_now(root: Path, top1: str, env: dict = None) -> dict:
    """A run whose numbers come from a file written now: made when it was recorded, its code the recording's."""
    (root / "out").mkdir(exist_ok=True)
    f = root / "out" / f"m{top1}.json"
    f.write_text(f'{{"int8": {{"top1": {top1}}}, "fp32": {{"top1": 76.0}}}}', encoding="utf-8")
    return rb(root, "run", "add", "int8", "--from", f"out/{f.name}", env=env)


def attached(root: Path, run: str) -> dict:
    return rb(root, "show", run)["data"]["object"]["receipt"]["attached"]


def add(root: Path, top1: str, env: dict = None) -> dict:
    return rb(root, "run", "add", "int8", f"int8.top1={top1}", "fp32.top1=76.0", "--produced-at", "2026-11-09T09:40:00Z", env=env)


@pytest.mark.parametrize("act", ["fail", "sleep"])
def test_a_status_git_could_not_give_is_no_clean_tree(home, act):
    root = record(home)
    env = shim(home, "status --porcelain --untracked-files=all", act)
    out = add(root, "75.1", env)
    a = attached(root, out["data"]["object"]["id"])
    assert a["dirty"] is True and a["tree"] is None and "diff_sha256" not in a, a
    assert (a["captured"], a["kept"]) == ("after", "none"), a
    said = {"fail": "rb kept no code for this run: rb could not read git (fatal: index file corrupt).",
            "sleep": "rb kept no code for this run: git did not answer in 1 s."}[act]
    assert a["kept_why"] == ("git_unreadable" if act == "fail" else "git_timeout"), a
    assert said in out["data"]["warnings"], out["data"]["warnings"]


@pytest.mark.parametrize("match,listing", [("ls-files -z --others", "untracked"), ("--name-only -z HEAD", "changed")])
def test_a_listing_that_fails_makes_no_tree(home, match, listing):
    root = record(home)
    out = add(root, "75.1", shim(home, match, "fail"))
    a = attached(root, out["data"]["object"]["id"])
    assert a["tree"] is None and (a["kept"], a["kept_why"], a["listing"]) == ("none", "list_failed", listing), a
    assert any(w.startswith(f"rb kept no code for this run: git could not list the {listing} files") for w in out["data"]["warnings"]), out["data"]


def test_files_left_out_make_the_code_part_kept_each_named(home):
    root = record(home)
    (root / "big.bin").write_bytes(b"\0" * (11 * 1024 * 1024))
    (root / "dangling").symlink_to(root / "nowhere")
    tracked = root / "data.bin"
    tracked.write_bytes(b"1")
    git(root, "add", "data.bin")
    git(root, "commit", "-qm", "data")
    tracked.write_bytes(b"\0" * (11 * 1024 * 1024))
    # numbers from a file written now: a run made when it was recorded, whose code is the recording's
    (root / "out").mkdir()
    (root / "out" / "m.json").write_text('{"int8": {"top1": 75.1}, "fp32": {"top1": 76.0}}', encoding="utf-8")
    out = rb(root, "run", "add", "int8", "--from", "out/m.json")
    run = out["data"]["object"]["id"]
    a = attached(root, run)
    assert a["kept"] == "part" and a["tree"] and a["left_out"] == 3, a
    files = {f["path"]: (f["why"], f["tracked"]) for f in a["left_out_files"]}
    assert files == {"big.bin": ("over_10_mb", False), "dangling": ("not_regular", False), "data.bin": ("over_10_mb", True)}, files
    [line] = [w for w in out["data"]["warnings"] if w.startswith("rb kept part of this run's code")]
    assert "Not in it: big.bin (over 10 MB), dangling (not a regular file)." in line, line
    assert f"data.bin is in it as commit {a['commit'][:12]} holds it, not as the run had it (over 10 MB)." in line, line
    # the tracked file is in the kept tree as its commit holds it
    assert git(root, "cat-file", "-p", f"{a['tree']}:data.bin") == "1"
    # part kept is kept: the code row is partial, never missing
    row = next(r for r in rb(root, "show", "int8")["data"]["rerunnability"] if r["what"] == "code")
    assert row["state"] == "partial" and "part of the code kept" in row["detail"], row
    # Reproduce rebuilds the kept tree under the line that says what it lacks, never as the run's whole code
    shown = subprocess.run([*command(), "show", "int8"], cwd=root, env=environment(), capture_output=True, text=True).stdout
    # a page gives counts and reasons, and names none of the files (E3's page form)
    page = root / "p.html"
    subprocess.run([*command(), "page", "--out", str(page)], cwd=root, env=environment(), check=True, capture_output=True)
    import html as html_mod, re
    text = html_mod.unescape(re.sub(r"<[^>]+>", "", page.read_text(encoding="utf-8")))
    assert (f"rb kept part of this run's code: git tree {a['tree'][:12]}. Not in it: 2 files (1 over 10 MB, 1 not a regular file). 1 file is in it as"
            f" commit {a['commit'][:12]} holds it, not as the run had it (over 10 MB).") in text, text[:300]
    assert "big.bin" not in text and "dangling" not in text
    # on the user's own screen Reproduce names each file it lacks; a tracked one is the run's own copy it lacks
    assert ("The kept tree is not the run's whole code: it lacks big.bin (over 10 MB), dangling (not a regular file), data.bin as the run had it"
            " (over 10 MB). Put those files in place after these commands, never before.") in shown and "read-tree -u --reset" in shown, shown
    # and a page counts them as its run card does: the two not in the tree, the one in it as its commit holds it
    assert ("The kept tree is not the run's whole code: it lacks 2 files (1 over 10 MB, 1 not a regular file) and 1 file as the run had it"
            " (over 10 MB).") in text, text
    assert "it lacks 3 files" not in text


def test_past_2000_files_no_tree_and_the_folder_named(home):
    root = record(home)
    logs = root / "logs"
    logs.mkdir()
    for i in range(2001):
        (logs / f"step{i}.txt").write_text(str(i), encoding="utf-8")
    out = add(root, "75.1")
    a = attached(root, out["data"]["object"]["id"])
    assert a["tree"] is None and a["kept"] == "none" and a["too_many"] == {"files": 2002, "folder": "logs/", "in_folder": 2001}, a
    assert a["left_out"] == 2002   # the count every rb since 0.7 reads
    assert ("rb kept no code for this run: 2002 files changed or untracked, 2001 of them in logs/. Add such folders to .gitignore, or commit them,"
            " so the next run's code is kept.") in out["data"]["warnings"], out["data"]["warnings"]
    # rb journal run says it before the command runs, and keeps no start code where a reconcile would propose it
    r = subprocess.run([*command(), "journal", "run", "--", "true"], cwd=root, env=environment(), capture_output=True, text=True)
    assert "rb keeps no code for this command: 2002 files changed or untracked, 2001 of them in logs/." in r.stderr, r.stderr
    entry = [e for e in rb(root, "journal")["data"]["entries"] if e.get("op") == "run"][-1]
    assert "code" not in entry and entry["start_code"]["kept_why"] == "too_many", entry


def test_a_clean_start_pins_its_commit_s_tree_in_the_journal(home):
    root = record(home)
    git(root, "checkout", "-q", "--", "train.py")
    subprocess.run([*command(), "journal", "run", "--", "true"], cwd=root, env=environment(), capture_output=True, text=True, check=True)
    entry = [e for e in rb(root, "journal")["data"]["entries"] if e.get("op") == "run"][-1]
    head = git(root, "rev-parse", "HEAD")
    # its code is its commit: the commit's tree is pinned by a ref, and the entry names no tree, which every rb reads as
    # uncommitted changes
    assert entry["code"]["commit"] == head and "tree" not in entry["code"], entry
    tree = git(root, "rev-parse", "HEAD^{tree}")
    assert git(root, "rev-parse", f"refs/rb/snapshots/{tree}") == tree


def test_with_no_commit_yet_the_tree_holds_the_staged_files(home):
    """With no commit, the files staged and never committed are in no commit to read the tree from: the tree takes
    them from the index, and a tree without them is never the whole code."""
    root = home / "int8-resnet50"
    root.mkdir()
    git(root, "init", "-q")
    (root / "train.py").write_text("lr = 0.1\n", encoding="utf-8")
    rb(root, "init", "INT8 ResNet-50", "--no-agents")
    rb(root, "experiment", "add", "INT8 against FP32", "--id", "int8", "--baseline", "fp32", "--candidate", "int8")
    git(root, "add", "train.py")
    (root / "notes.py").write_text("seed = 1\n", encoding="utf-8")
    a = attached(root, add(root, "75.1")["data"]["object"]["id"])
    assert a["commit"] is None and a["dirty"] is True and a["kept"] == "whole" and a["tree"], a
    names = git(root, "ls-tree", "-r", "--name-only", a["tree"]).splitlines()
    assert "train.py" in names and "notes.py" in names, names


@pytest.mark.parametrize("act", ["fail", "sleep"])
def test_a_head_git_cannot_read_is_no_first_commit(home, act):
    """A HEAD git could not read is not "no commit yet": rb keeps no code and says why, in the receipt, rb run add,
    the code row and rb journal run's entry."""
    root = record(home)
    env = shim(home, "rev-parse --verify -q HEAD", act)
    out = add_now(root, "75.1", env)
    a = attached(root, out["data"]["object"]["id"])
    assert a["dirty"] is True and a["tree"] is None and a["kept"] == "none", a
    assert a["kept_why"] == ("git_unreadable" if act == "fail" else "git_timeout"), a
    assert not any(w.startswith("No commit yet") for w in out["data"]["warnings"]), out["data"]["warnings"]
    said = {"fail": "rb kept no code for this run: rb could not read git (fatal: index file corrupt).",
            "sleep": "rb kept no code for this run: git did not answer in 1 s."}[act]
    assert said in out["data"]["warnings"], out["data"]["warnings"]
    row = next(r for r in rb(root, "show", "int8")["data"]["rerunnability"] if r["what"] == "code")
    assert row["state"] == "missing" and "outside git" not in row["detail"] and "no code kept for 1 run" in row["detail"], row
    r = subprocess.run([*command(), "journal", "run", "--", "true"], cwd=root, env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    entry = [e for e in rb(root, "journal")["data"]["entries"] if e.get("op") == "run"][-1]
    assert "code" not in entry and entry["start_code"]["kept"] == "none", entry


@pytest.mark.parametrize("act,why", [("fail", "git_failed"), ("sleep", "git_timeout")])
def test_a_tree_git_could_not_write_is_none_kept(home, act, why):
    root = record(home)
    out = add(root, "75.1", shim(home, "write-tree", act))
    a = attached(root, out["data"]["object"]["id"])
    assert a["tree"] is None and (a["kept"], a["kept_why"]) == ("none", why), a
    said = {"git_failed": "rb kept no code for this run: git failed.", "git_timeout": "rb kept no code for this run: git did not answer in 1 s."}[why]
    assert said in out["data"]["warnings"], out["data"]["warnings"]


def test_a_listing_git_does_not_answer_says_so(home):
    """Every git call rb times waits as long, in rb's tests RB_TEST_GIT_TIMEOUT_MS: a listing that hangs did not answer."""
    root = record(home)
    out = add(root, "75.1", shim(home, "--name-only -z HEAD", "sleep"))
    a = attached(root, out["data"]["object"]["id"])
    assert a["tree"] is None and (a["kept"], a["kept_why"], a["git_secs"]) == ("none", "git_timeout", 1), a


def test_a_tree_no_ref_keeps_is_never_said_kept(home):
    """A tree git has with no ref under refs/rb/snapshots/ may go at git gc: the run and the experiment's code row say
    so, and the row is never ok."""
    root = record(home)
    run = add_now(root, "75.1", shim(home, "update-ref", "fail"))["data"]["object"]["id"]
    a = attached(root, run)
    assert a["kept"] == "whole" and a["tree"], a
    assert subprocess.run([GIT, "rev-parse", "--verify", "-q", f"refs/rb/snapshots/{a['tree']}"], cwd=root, capture_output=True).returncode != 0
    words = f"git tree {a['tree'][:12]} is in this repository, but no ref keeps it, so git gc may remove it"
    shown = subprocess.run([*command(), "show", run], cwd=root, env=environment(), capture_output=True, text=True).stdout
    assert f": {words}." in shown and f"{a['tree'][:12]}. git tree" not in shown, shown
    row = next(r for r in rb(root, "show", "int8")["data"]["rerunnability"] if r["what"] == "code")
    assert row["state"] == "partial" and words in row["detail"] and "kept as git trees" in row["detail"], row


def test_an_earlier_receipt_s_one_unnamed_file_is_said_as_one(home):
    root = record(home)
    run = add(root, "75.1")["data"]["object"]["id"]
    path = next((root / ".rb" / "experiments" / "int8" / "runs").glob("*.yaml"))
    # an earlier rb's receipt: a tree with one file left out, and no key this rb writes
    text = path.read_text(encoding="utf-8")
    for key in ("captured", "kept"):
        text = "\n".join(l for l in text.splitlines() if not l.strip().startswith(f"{key}:")) + "\n"
    text = "\n".join(l + ("\n" + l[:len(l) - len(l.lstrip())] + "left_out: 1" if l.strip().startswith("tree:") else "") for l in text.splitlines()) + "\n"
    path.write_text(text, encoding="utf-8")
    rb(root, "doctor", "--adopt", "--why", "an earlier rb's receipt")
    shown = subprocess.run([*command(), "show", run], cwd=root, env=environment(), capture_output=True, text=True).stdout
    assert "1 file was left out of it; this record does not name it." in shown, shown
