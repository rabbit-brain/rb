"""The exact code behind a run. A commit names the code only when the tree is clean; a run recorded or watched with
uncommitted changes keeps the git tree of the files as they were (the digest the receipt carries, the same for anyone
with the same files) and a snapshot commit that holds it in this repository, never touching the user's index,
branch or files. The record stays the truth; the snapshot is the evidence it points at."""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from rabbit_brain.models import Envelope
from rbtest import as_the_user, main, state

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="snapshots are git objects")


def git(*args) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout


@pytest.fixture
def repo(workdir, monkeypatch, tmp_path_factory, capsys):
    as_the_user(monkeypatch)
    cfg = tmp_path_factory.mktemp("gitconfig") / "config"
    cfg.write_text("[user]\n\tname = t\n\temail = t@example.com\n[commit]\n\tgpgsign = true\n", encoding="utf-8")   # signing on: rb must never sign
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(cfg))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    git("init", "-q")
    Path("train.py").write_text("lr = 0.1\n", encoding="utf-8")
    git("add", "-A")
    git("-c", "commit.gpgsign=false", "commit", "-qm", "start")

    def rb(*argv):
        args = list(argv)
        args.insert(args.index("--") if "--" in args else len(args), "--json")
        code = main(args)
        return code, Envelope.model_validate_json(capsys.readouterr().out)

    assert rb("init", "t")[0] == 0
    assert rb("experiment", "add", "e", "--id", "e1")[0] == 0
    assert rb("criterion", "add", "top1 holds", "-e", "e1", "--metric", "top1", "--at-least", "0.5", "--id", "c1")[0] == 0
    return rb


def test_a_clean_tree_keeps_its_commits_tree(repo):
    """No snapshot to make: the commit names the code, and its tree is kept beside the snapshots, so the code outlives
    the branch the commit was on."""
    git("add", "-A")
    git("-c", "commit.gpgsign=false", "commit", "-qm", "the record")
    code, env = repo("run", "add", "e1", "top1=0.6")
    attached = state().get(env.data["object"]["id"], "run").receipt.attached
    tree = git("rev-parse", "HEAD^{tree}").strip()
    assert attached["dirty"] is False and attached["tree"] == tree and "left_out" not in attached
    assert git("rev-parse", f"refs/rb/snapshots/{tree}").strip() == tree


def test_a_run_recorded_from_uncommitted_code_keeps_its_exact_code(repo, capsys):
    Path("train.py").write_text("lr = 0.3\n", encoding="utf-8")                  # changed, not committed
    Path("aug.py").write_text("flip = True\n", encoding="utf-8")                  # new, untracked
    big = Path("weights.bin")
    with big.open("wb") as f:
        f.truncate(11 * 1024 * 1024)                                             # too big to snapshot: counted
    index_before = git("diff", "--cached", "--name-only")
    code, env = repo("run", "add", "e1", "top1=0.7")
    assert code == 0
    attached = state().get(env.data["object"]["id"], "run").receipt.attached
    tree = attached["tree"]
    assert attached["dirty"] and len(tree) >= 40 and attached["left_out"] == 1 and "snapshot" not in attached
    assert git("rev-parse", f"refs/rb/snapshots/{tree}").strip() == tree       # kept here, as the tree itself
    assert git("cat-file", "-t", tree).strip() == "tree"
    assert "rb:" not in git("log", "--all", "--oneline")                         # and no commit of rb's in anyone's history
    assert git("show", f"{tree}:train.py") == "lr = 0.3\n" and git("show", f"{tree}:aug.py") == "flip = True\n"
    files = git("ls-tree", "-r", "--name-only", tree).split()
    assert "weights.bin" not in files and not any(f.startswith(".rb/") for f in files)
    assert sorted(git("diff", "--name-only", "HEAD", tree).split()) == ["aug.py", "train.py"]
    # nothing of the user's moved: their index, their branch, their files
    assert git("diff", "--cached", "--name-only") == index_before
    assert git("rev-parse", "HEAD").strip() == git("rev-parse", "refs/heads/" + git("symbolic-ref", "--short", "HEAD").strip()).strip()
    assert Path("train.py").read_text(encoding="utf-8") == "lr = 0.3\n"
    # the same files give the same tree: a digest anyone can check
    code, env2 = repo("run", "add", "e1", "top1=0.71")
    assert state().get(env2.data["object"]["id"], "run").receipt.attached["tree"] == tree
    # and rb show says where it is and how to see it
    assert main(["show", env.data["object"]["id"]]) == 0
    text = capsys.readouterr().out
    assert f"git tree {tree[:12]}" in text and f"git diff {attached['commit'][:12]} {tree[:12]}" in text


def test_a_watched_command_keeps_the_code_it_ran_with(repo, capsys):
    Path("train.py").write_text("lr = 0.5\n", encoding="utf-8")
    Path("eval.py").write_text('print(\'{"metrics": {"top1": 0.66}}\')\n', encoding="utf-8")
    code = main(["journal", "run", "--json", "--", sys.executable, "eval.py"])
    capsys.readouterr()
    assert code == 0
    entry = [e for e in state().journal(None) if e.get("op") == "run"][-1]
    ran = entry["code"]
    assert ran["commit"] == git("rev-parse", "HEAD").strip() and git("show", f"{ran['tree']}:train.py") == "lr = 0.5\n"
    item = next(i for i in state().found()[0] if i.path.endswith("eval.py"))
    assert item.argv[-4:] == ["--commit", ran["commit"], "--tree", ran["tree"]]  # the run names the exact code that ran
    Path("train.py").write_text("lr = 0.9\n", encoding="utf-8")                  # edited after the run, before recording
    code, env = repo(*item.argv)
    run = state().get(env.data["object"]["id"], "run")
    assert run.receipt.produced == {"commit": ran["commit"], "tree": ran["tree"], "from": "flag"}
    assert git("show", f"{run.receipt.produced['tree']}:train.py") == "lr = 0.5\n"     # what ran, not what is there now


def test_the_write_that_makes_a_ref_says_once_where_it_is_and_how_to_remove_it(repo, capsys):
    """Round 10: every trial left refs/rb/snapshots/<tree> in the researcher's repository and no command said so. The
    write that makes the ref says, once, where it put it and the command that removes it; a write that finds the ref
    there says nothing; rb reconcile --apply says it for the runs it records."""
    def told(tree: str) -> str:
        ref = f"refs/rb/snapshots/{tree}"
        return f"rb put the code in git at {ref}, so it outlives a deleted branch or worktree; git update-ref -d {ref} removes it."

    assert main(["run", "add", "e1", "top1=0.6"]) == 0                           # a clean tree: its commit's tree
    head = git("rev-parse", "HEAD^{tree}").strip()
    assert capsys.readouterr().out.count(told(head)) == 1
    code, env = repo("run", "add", "e1", "top1=0.61")
    assert not [w for w in env.data.get("warnings", []) if "refs/rb/" in w]
    Path("train.py").write_text("lr = 0.3\n", encoding="utf-8")                  # uncommitted: its exact code
    code, env = repo("run", "add", "e1", "top1=0.7")
    tree = state().get(env.data["object"]["id"], "run").receipt.attached["tree"]
    assert tree != head and env.data["warnings"].count(told(tree)) == 1
    assert main(["run", "add", "e1", "top1=0.71"]) == 0 and "update-ref" not in capsys.readouterr().out
    # the command it gives removes the ref
    git("update-ref", "-d", f"refs/rb/snapshots/{tree}")
    assert tree not in git("for-each-ref", "refs/rb/snapshots")
    # rb reconcile --apply records a run from a results file, from a tree with a new file in it: said by reconcile
    Path("out").mkdir()
    Path("out/metrics.json").write_text('{"top1": 0.79}', encoding="utf-8")
    assert main(["reconcile", "--apply"]) == 0
    out = capsys.readouterr().out
    newest = state().records("run", live=True)[-1].receipt.attached["tree"]
    assert newest not in (head, tree) and out.count(told(newest)) == 1, out
