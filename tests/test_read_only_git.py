"""A coding agent's sandbox can leave the project writable and git's folder read-only (Codex's default sandbox does).
rb keeps its journal and its list of what it wrote outside .rb/ in git's shared folder; there, every look kept nothing,
rb journal run lost what it saw, and rb reconcile said it could not write its git journal. Promises pinned here:

- While git's folder cannot be written, the journal is kept in the record's own .rb/journal/, which .rb/.gitignore keeps
  out of git, and the line that says where rb looks says so once. rb status, rb context, rb reconcile, rb journal scan
  and rb journal run work, and what a watched command printed is kept.
- rb's list of what it wrote outside .rb/ is kept in .rb/cache/owned.jsonl meanwhile, so rb remove still takes the
  skill and the MCP entry rb init installed.
- The first look that can write git's folder again moves both there, every entry once, in order, and the line says
  nothing more; a move stopped halfway leaves no entry twice.
- A linked worktree keeps a journal of its own, beside its own record, and then in its own folder in git's.
"""
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from rabbit_brain.models import Envelope
from rbtest import as_the_user, main

pytestmark = [
    pytest.mark.skipif(shutil.which("git") is None, reason="git's folder is git's"),
    pytest.mark.skipif(sys.platform == "win32", reason="a folder's write bit does not stop new files in it on Windows"),
    pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="root writes in a folder whatever its mode"),
]

ASIDE = "Git's folder cannot be written here, so rb keeps its journal in .rb/journal/, which git ignores, until a look can move it there."
EVAL = ("import json, os, sys\nseed = sys.argv[1]\nos.makedirs(f'out/seed{seed}', exist_ok=True)\n"
        "json.dump({'fp32': {'val_loss': 0.95}, 'int8': {'val_loss': 0.97}}, open(f'out/seed{seed}/metrics.json', 'w'))\n"
        "print(json.dumps({'metrics': {'fp32.val_loss': 0.95, 'int8.val_loss': 0.97}}))\n")


def git(*argv, cwd=None):
    return subprocess.run(["git", *argv], check=True, capture_output=True, text=True, cwd=cwd).stdout


def read_only(folder: Path, on: bool) -> None:
    """git's folder as an agent's sandbox leaves it (no write bit anywhere in it), or as it was."""
    for top, dirs, files in os.walk(folder):
        for name in [*dirs, *files, ""]:
            p = Path(top) / name
            mode = p.lstat().st_mode
            if stat.S_ISLNK(mode):
                continue
            p.chmod(mode & ~0o222 if on else mode | stat.S_IWUSR)


@pytest.fixture
def project(workdir, monkeypatch, tmp_path_factory, capsys):
    """ana's INT8 ResNet-50 project, already under way: a commit with the evaluation script."""
    as_the_user(monkeypatch)
    monkeypatch.setenv("RB_ACTOR", "human:ana")
    cfg = tmp_path_factory.mktemp("gitconfig") / "config"
    cfg.write_text("[user]\n\temail = ana@example.org\n\tname = ana\n[init]\n\tdefaultBranch = main\n", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(cfg))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    git("init", "-q")
    Path("eval.py").write_text(EVAL, encoding="utf-8")
    git("add", "eval.py")
    git("commit", "-qm", "the evaluation")
    locked = []

    def rb(*argv, cwd=None):
        here = os.getcwd()
        os.chdir(cwd or here)
        args = list(argv)
        args.insert(args.index("--") if "--" in args else len(args), "--json")
        try:
            code = main(args)
        finally:
            os.chdir(here)
        return code, Envelope.model_validate_json(capsys.readouterr().out)

    def text(*argv):
        main(list(argv))
        out = capsys.readouterr()
        return out.out + out.err

    def lock(folder=Path(".git"), on=True):
        read_only(Path(folder), on)
        if on:
            locked.append(Path(folder).resolve())

    rb.text, rb.lock = text, lock
    yield rb
    for folder in locked:
        read_only(folder, False)


def entries(rb, cwd=None):
    code, env = rb("journal", "-n", "1000", cwd=cwd)
    assert code == 0
    return env.data["entries"]


def lines(path: Path) -> list:
    return [x for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def test_a_read_only_git_folder_keeps_the_journal_beside_the_record_and_moves_it_home_after(project):
    rb = project
    rb.lock()                                                 # the sandbox the session runs rb init in
    assert rb("init", "INT8 ResNet-50 on ImageNet", "--no-agents")[0] == 0
    for argv in (["status"], ["context"], ["reconcile"], ["journal", "scan"]):
        code, env = rb(*argv)
        assert code == 0, (argv, env.errors)
    assert not Path(".git/rb").exists()
    aside = Path(".rb/journal")
    assert (aside / "journal.jsonl").is_file() and (aside / "state.json").is_file()
    assert "journal/" in Path(".rb/.gitignore").read_text(encoding="utf-8").splitlines()
    status = rb.text("status")
    assert status.count(ASIDE) == 1 and ASIDE in rb.text("context")
    assert rb("status")[1].data["scope"]["journal_aside"] is True
    # what a watched command printed is kept, and reconcile reads it
    code, env = rb("journal", "run", "--", sys.executable, "eval.py", "3")
    assert code == 0, env.errors
    ran = [e for e in entries(rb) if e["op"] == "run"]
    assert len(ran) == 1 and ran[0]["metrics"] == {"fp32.val_loss": 0.95, "int8.val_loss": 0.97} and ran[0]["exit"] == 0
    seen = entries(rb)
    # the first look that can write git's folder moves the journal there, each entry once
    rb.lock(on=False)
    code, env = rb("status")
    assert code == 0 and env.data["scope"]["journal_aside"] is False
    assert not aside.exists() and ASIDE not in rb.text("status")
    home = Path(".git/rb/journal/main/journal.jsonl")
    after = entries(rb)
    assert after[:len(seen)] == seen and [e for e in after if e["op"] == "run"] == ran
    assert len(lines(home)) == len(set(lines(home))) == len(after)
    assert entries(rb) == after                              # another look moves nothing twice
    manual = " ".join((Path(__file__).resolve().parents[1] / "AGENTS.md").read_text(encoding="utf-8").split())
    assert "the checkout's journal is kept in its own `.rb/journal/`, which `.rb/.gitignore` keeps out of git" in manual


def test_a_journal_home_made_before_the_sandbox_reads_on_and_its_first_sightings_stay(project):
    """The journal's home was made while git's folder could be written; a sandboxed stretch keeps its looks beside the
    record, read after the home's, and the first sighting of a file keeps its time through the move."""
    rb = project
    assert rb("init", "INT8 ResNet-50 on ImageNet", "--no-agents")[0] == 0
    assert rb("journal", "run", "--", sys.executable, "eval.py", "1")[0] == 0
    home = Path(".git/rb/journal/main")
    before = entries(rb)
    first = json.loads((home / "state.json").read_text(encoding="utf-8"))["files"]["out/seed1/metrics.json"]["at"]
    rb.lock()
    assert rb("journal", "run", "--", sys.executable, "eval.py", "2")[0] == 0
    during = entries(rb)
    assert during[:len(before)] == before and any(e.get("path") == "out/seed2/metrics.json" for e in during[len(before):])
    assert [e["command"].split()[-1] for e in during if e["op"] == "run"] == ["1", "2"]
    rb.lock(on=False)
    assert rb("journal", "scan")[0] == 0
    after = entries(rb)
    assert after[:len(during)] == during and not Path(".rb/journal").exists()
    st = json.loads((home / "state.json").read_text(encoding="utf-8"))
    assert st["files"]["out/seed1/metrics.json"]["at"] == first and "out/seed2/metrics.json" in st["files"]


def test_a_move_stopped_halfway_leaves_no_entry_twice(project):
    """A look that appended what was kept aside and stopped before taking it away: the next one adds none of it again."""
    rb = project
    assert rb("init", "INT8 ResNet-50 on ImageNet", "--no-agents")[0] == 0
    assert rb("status")[0] == 0
    rb.lock()
    assert rb("journal", "run", "--", sys.executable, "eval.py", "4")[0] == 0
    kept = lines(Path(".rb/journal/journal.jsonl"))
    rb.lock(on=False)
    home = Path(".git/rb/journal/main/journal.jsonl")
    with home.open("a", encoding="utf-8") as f:            # as far as that look got
        f.write("\n".join(kept[:2]) + "\n")
    seen = len(lines(home)) - 2 + len(kept)
    assert rb("status")[0] == 0
    held = lines(home)
    assert len(held) == len(set(held)) and len(held) >= seen and all(line in held for line in kept)


def test_what_rb_init_installs_in_a_sandbox_is_listed_and_rb_remove_takes_it(project):
    """rb init run where git's folder cannot be written installs the skill and the MCP entry; its list of what it wrote
    is kept in .rb/cache/owned.jsonl, so rb remove takes both, then and after the list moves to git's folder."""
    rb = project
    Path(".claude").mkdir()
    rb.lock()
    code, env = rb("init", "INT8 ResNet-50 on ImageNet")
    assert code == 0 and Path(".claude/skills/rabbit-brain/SKILL.md").is_file() and Path(".mcp.json").is_file()
    aside = Path(".rb/cache/owned.jsonl")
    kept = [json.loads(x)["path"] for x in lines(aside)]
    assert "../../.claude/skills/rabbit-brain/SKILL.md" in kept and "../../.mcp.json" in kept
    assert rb("page", "--out", "page.html")[0] == 0
    plan = rb.text("remove", "--dry-run")
    will = plan.split("Will remove:", 1)[1].split("Not rb's to touch", 1)[0]
    assert ".claude/skills/rabbit-brain/SKILL.md: rb's skill for Claude Code, as rb wrote it" in will
    assert ".mcp.json: made by rb, and as rb wrote it" in will and "rb did not write it here" not in plan
    rb.lock(on=False)
    assert rb("status")[0] == 0                              # a look that can write there moves the list
    home = Path(".git/rb/owned.jsonl")
    moved = [json.loads(x) for x in lines(home)]
    assert not aside.exists() and [m["path"] for m in moved].count("../../.mcp.json") == 1
    assert [m["path"] for m in moved][0] == "../../.git/rb" and "../../page.html" in [m["path"] for m in moved]
    code, env = rb("remove", "--yes")
    assert code == 0, env.errors
    assert not Path(".claude/skills/rabbit-brain").exists() and not Path(".mcp.json").exists() and not Path(".git/rb").exists()


def test_a_removal_stopped_midway_keeps_the_list_kept_in_the_record_for_the_next(project):
    """rb remove stops at a file it cannot take (here the skill's folder is read-only too): the list kept in the record's
    cache stays with what is left of .rb/, so rb remove, run again, still takes the skill as rb's."""
    rb = project
    Path(".claude").mkdir()
    rb.lock()
    assert rb("init", "INT8 ResNet-50 on ImageNet")[0] == 0
    skill = Path(".claude/skills/rabbit-brain")
    rb.lock(skill)
    code, env = rb("remove", "--yes")
    assert code != 0 and (skill / "SKILL.md").is_file() and Path(".rb/cache/owned.jsonl").is_file()
    rb.lock(skill, on=False)
    code, env = rb("remove", "--yes")
    assert code == 0, env.errors
    assert not skill.exists() and not Path(".mcp.json").exists() and not Path(".rb").exists()


def test_a_linked_worktree_keeps_its_own_journal_beside_its_own_record(project, tmp_path_factory):
    rb = project
    assert rb("init", "INT8 ResNet-50 on ImageNet", "--no-agents")[0] == 0
    git("add", ".rb")
    git("commit", "-qm", "the record")
    assert rb("status")[0] == 0
    wt = tmp_path_factory.mktemp("trees") / "calib"
    git("worktree", "add", "-q", "-b", "calib", str(wt))
    main_journal = lines(Path(".git/rb/journal/main/journal.jsonl"))
    rb.lock()
    assert rb("journal", "run", "--", sys.executable, "eval.py", "5", cwd=wt)[0] == 0
    assert (wt / ".rb/journal/journal.jsonl").is_file() and not Path(".rb/journal").exists()
    assert [e["command"].split()[-1] for e in entries(rb, cwd=wt) if e["op"] == "run"] == ["5"]
    assert not any(e["op"] == "run" for e in entries(rb))     # the main checkout's journal is its own
    rb.lock(on=False)
    assert rb("status", cwd=wt)[0] == 0
    theirs = Path(".git/rb/journal/calib")
    assert not (wt / ".rb/journal").exists() and (theirs / "journal.jsonl").is_file()
    assert json.loads((theirs / "place.json").read_text(encoding="utf-8"))["path"] == str(wt.resolve()).replace("\\", "/")
    assert lines(Path(".git/rb/journal/main/journal.jsonl"))[:len(main_journal)] == main_journal
    assert not any(e["op"] == "run" for e in entries(rb))
    assert [e["command"].split()[-1] for e in entries(rb, cwd=wt) if e["op"] == "run"] == ["5"]
