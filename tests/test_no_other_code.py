"""EVIDENCE.md E4: no reproduction on other code.

A run whose code rb kept as a git tree in the clone that recorded it is not here in a clone without that tree: every
view says so in E4's words, and none hands out the commit's command as if it were the run's code. The promises a page
and rb sync make read what origin held. rb's restore line and the clone plan's comparison never touch the user's
index, and "your checkout differs" counts a file the run's code had that your checkout lacks.
"""
import html
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from rbtest import as_the_user, command, environment

pytestmark = pytest.mark.skipif(shutil.which("git") is None or sys.platform == "win32", reason="git repositories in scratch folders")
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


def text(cwd: Path, *argv: str) -> str:
    return subprocess.run([*command(), *argv], cwd=cwd, env=environment(), capture_output=True, text=True, stdin=subprocess.DEVNULL).stdout


def git(cwd: Path, *argv: str) -> str:
    return subprocess.run([GIT, *argv], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def recorded(home: Path) -> tuple[Path, str, str]:
    """A record whose one run had changes outside the record not committed: rb kept them as a git tree."""
    root = home / "int8-resnet50"
    root.mkdir()
    git(root, "init", "-q")
    (root / "train.py").write_text("lr = 0.1\n", encoding="utf-8")
    rb(root, "init", "INT8 ResNet-50", "--no-agents")
    rb(root, "experiment", "add", "INT8 against FP32", "--id", "int8", "--baseline", "fp32", "--candidate", "int8")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "the record")
    (root / "train.py").write_text("lr = 0.2\n", encoding="utf-8")
    (root / "calib.py").write_text("images = 512\n", encoding="utf-8")
    (root / "out").mkdir()
    (root / "out" / "m.json").write_text('{"int8": {"top1": 75.1}, "fp32": {"top1": 76.0}}', encoding="utf-8")
    run = rb(root, "run", "add", "int8", "--from", "out/m.json", "--command", "python train.py")["data"]["object"]["id"]
    tree = rb(root, "show", run)["data"]["object"]["receipt"]["attached"]["tree"]
    git(root, "add", ".rb", "out")
    git(root, "commit", "-qm", "the run")
    return root, run, tree


def page_doc(cwd: Path) -> dict:
    dump = cwd.parent / f"{cwd.name}.view.json"
    env = {**environment(), "RB_TEST_SHELL": "1", "RB_TEST_SHELL_DUMP": str(dump)}
    subprocess.run([*command(), "page", "--out", str(cwd.parent / f"{cwd.name}.html")], cwd=cwd, env=env, check=True, capture_output=True)
    return json.loads(dump.read_text(encoding="utf-8"))


def test_a_clone_without_the_run_s_tree_hands_out_no_command(home):
    root, run, tree = recorded(home)
    other = home / "other"
    git(home, "clone", "-q", "--no-local", str(root), str(other))   # neither refs nor trees
    t12 = tree[:12]
    not_here = (f"This run's code is not in this clone: it had changes outside the record not committed, and rb kept them as git tree {t12}"
                " in the clone that recorded the run.")
    shown = text(other, "show", run)
    assert not_here in shown and "the clone that recorded the run kept it" not in shown, shown
    row = next(r for r in rb(other, "show", "int8")["data"]["rerunnability"] if r["what"] == "code")
    assert row["state"] == "missing" and f"code not in this clone for 1 run: rb kept it as git tree {t12} in the clone that recorded it" in row["detail"], row
    assert "never committed" not in row["detail"], row      # the contradiction goes: it was kept, elsewhere
    # rb ui's run card, which has no Reproduce, says it in the same words; the clone that recorded it does not
    from rbtest import object_card
    assert not_here in object_card(run, other)["facts"], object_card(run, other)["facts"]
    assert not_here not in object_card(run, root)["facts"]
    # a page's Reproduce: the code step in the page's words, nothing to run, and the run's command as what it ran
    doc = page_doc(other)
    steps = next(e for e in doc["experiments"] if e["id"] == "int8")["reproduce"]["steps"]
    code = next(s for s in steps if s["what"] == "code")
    assert not code["runnable"] and not code["command"], code
    assert (f"A rerun from this page does not get this run's code: it had changes outside the record not committed, which rb kept as git tree"
            f" {t12} in the clone that recorded the run.") in code["label"], code
    last = doc["runs"][run]["reproduce"]["steps"][-1]
    assert last["what"] == "command (as given)" and not last["runnable"] and "python train.py" in last["label"], last
    # where the tree is here, the steps are as before: a worktree of the commit and the kept tree, and the command
    doc = page_doc(root)
    steps = doc["runs"][run]["reproduce"]["steps"]
    assert next(s for s in steps if s["what"] == "code")["runnable"] and steps[-1]["runnable"], steps


def test_the_restore_line_never_touches_the_index(home):
    root, run, tree = recorded(home)
    shown = text(root, "show", run)
    [line] = [l for l in shown.splitlines() if "Restore a file:" in l]
    restore = line.split("Restore a file: ", 1)[1].strip()
    assert restore == f"git restore --source={tree} --worktree -- <path>", restore
    (root / "train.py").write_text("lr = 0.3\n", encoding="utf-8")
    index = root / ".git" / "index"
    before = (index.read_bytes(), index.stat().st_mtime_ns)
    subprocess.run(restore.replace("<path>", "train.py").split(), cwd=root, check=True, capture_output=True)
    assert (root / "train.py").read_text(encoding="utf-8") == "lr = 0.2\n"
    assert (index.read_bytes(), index.stat().st_mtime_ns) == before
    assert "train.py" not in git(root, "diff", "--cached", "--name-only")


def test_your_checkout_differs_in_a_file_the_run_had_that_it_lacks(home):
    root, run, tree = recorded(home)
    (root / "calib.py").unlink()
    got = rb(root, "show", "int8")["data"]
    said = json.dumps(got)
    assert "your checkout differs in 1 file: calib.py" in said, said[:2000]


def test_the_clone_plan_leaves_the_index_as_it_was(home):
    root, run, tree = recorded(home)
    git(root, "checkout", "-q", "--", "train.py")
    git(root, "add", "calib.py")
    git(root, "commit", "-qm", "calib")
    bundle = home / "bundle"
    rb(root, "export", "--with-record", "--out", str(bundle))
    fork = home / "fork"
    rb(home, "clone", str(bundle), str(fork), "--code", str(root))
    # the same bytes with a new time: a git diff on the user's index would refresh it and write it back
    st = (fork / "train.py").stat()
    os.utime(fork / "train.py", ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
    index = fork / ".git" / "index"
    before = (index.read_bytes(), index.stat().st_mtime_ns)
    rb(fork, "status")
    rb(fork, "context")
    assert (index.read_bytes(), index.stat().st_mtime_ns) == before


def _origin(root: Path, at: str = None) -> None:
    git(root, "remote", "add", "origin", "https://example.org/ana/int8-resnet50.git")
    if at:
        git(root, "update-ref", "refs/remotes/origin/main", at)


@pytest.mark.parametrize("fetched", [True, False])
def test_a_page_promises_the_code_only_where_origin_holds_it(home, fetched):
    root, run, tree = recorded(home)
    first = git(root, "rev-list", "--max-parents=0", "HEAD")
    git(root, "checkout", "-q", "--", "train.py")
    git(root, "add", "calib.py")
    git(root, "commit", "-qm", "calib")                           # code origin's branches do not hold
    head = git(root, "rev-parse", "HEAD")[:12]
    _origin(root, first if fetched else None)
    out = home / "site"
    said = rb(root, "export", "--with-record", "--url", "https://example.org/int8/", "--out", str(out))
    page = html.unescape(re.sub(r"<[^>]+>", "", (out / "page.html").read_text(encoding="utf-8")))
    lacks = (f"When this page was made, origin's branches, as git last fetched them, did not hold commit {head}: a rerun cannot get the code"
             " from https://example.org/ana/int8-resnet50.git until it is pushed there." if fetched else
             f"When this page was made, git had fetched no branch of origin, so rb could not tell whether origin holds commit {head}.")
    assert lacks in page, page[-3000:]
    assert "rb clone gets the code at that commit" not in page and "rb clone gets it too" not in page
    # where origin holds it, the page promises it as before
    git(root, "update-ref", "refs/remotes/origin/main", git(root, "rev-parse", "HEAD"))
    shutil.rmtree(out)
    rb(root, "export", "--with-record", "--url", "https://example.org/int8/", "--out", str(out))
    page = html.unescape(re.sub(r"<[^>]+>", "", (out / "page.html").read_text(encoding="utf-8")))
    assert "rb clone gets the code at that commit" in page and "did not hold commit" not in page and "fetched no branch" not in page


def test_a_start_captured_tree_a_clone_lacks_reads_as_not_in_this_clone(home):
    """S3 (MP-1110-1703): the code a watched command started with, kept as a git tree, is not in a clone (a clone carries
    no refs/rb). rb show <run> then gives no git diff against it, and says it is not in this clone, as rb ui's run card
    does; the clone that recorded it gives the diff and says nothing of the kind. Fails on e6d6d7b."""
    root = home / "int8-resnet50"
    root.mkdir()
    git(root, "init", "-q")
    (root / "train.py").write_text("lr = 0.1\n", encoding="utf-8")
    rb(root, "init", "INT8 ResNet-50", "--no-agents")
    rb(root, "experiment", "add", "INT8 against FP32", "--id", "int8", "--baseline", "fp32", "--candidate", "int8")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "the record")
    (root / "train.py").write_text("lr = 0.2\n", encoding="utf-8")
    write = "import os; os.makedirs('out', exist_ok=True); open('out/m.json', 'w').write('{\"int8\": {\"top1\": 75.1}, \"fp32\": {\"top1\": 76.0}}')"
    subprocess.run([*command(), "journal", "run", "--", sys.executable, "-c", write], cwd=root, env=environment(), check=True, capture_output=True)
    run = rb(root, "run", "add", "int8", "--from", "out/m.json")["data"]["object"]["id"]
    produced = rb(root, "show", run)["data"]["object"]["receipt"]["produced"]
    assert produced.get("captured") == "start" and produced.get("tree"), produced
    t12 = produced["tree"][:12]
    git(root, "add", ".rb", "out", "train.py")
    git(root, "commit", "-qm", "the run")
    other = home / "other"
    git(home, "clone", "-q", "--no-local", str(root), str(other))
    away = (f"This run's code is not in this clone: rb kept the code its command started with as git tree {t12} in the clone that"
            " recorded the run.")
    shown = text(other, "show", run)
    assert away in shown, shown
    assert f"git diff {produced['commit'][:12]} {t12}" not in shown, shown
    from rbtest import object_card
    assert away in object_card(run, other)["facts"], object_card(run, other)["facts"]
    mine = text(root, "show", run)
    assert away not in mine and f"git diff {produced['commit'][:12]} {t12}" in mine, mine
