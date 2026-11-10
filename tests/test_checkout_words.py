"""How the checkout holds the record, said in one phrase on every command that says it (checkout.rs makes the words).

Pinned here, through rb's own commands in git repositories in scratch folders:

- One wording: walking one repository through its states (no commit yet, all record files committed, 2 record files
  not committed after a note, no branch, ignored, not in git, git not found, rb could not read git), the same phrase is
  rb status's line under its title and data.repository.said, the first bullet of rb context's "## The repository" and of
  rb://context, rb remove's count, and rb page's "The page says".
- data.repository.branch names the branch with no commit yet, and is null on no branch.
- Receipt agreement: with rb's page and an untracked skill folder rb wrote, data.repository.changes_outside_record is
  false and the next run's receipt is not dirty; a tracked file edited flips both, and a flip of the journal's own flag
  writes no rb journal text line.
- rb journal's text: a git line only for a move of HEAD ("new commit", "2 new commits", "another commit checked out"),
  " · at commit <12>", every time "HH:MM UTC", a ran line "(exit 0)" with no seconds; --json keeps every entry.
- rb doctor says record files ignored by git (with the rule), git not found, and rb could not read git.
- A page's About says the same words, with the commit, on a line under its snapshot line (rb page per state, rb
  export --with-record of the checkout, never its copy), and the owner's span ends the Overview's line.
"""
import html
import json
import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

from rbtest import Mcp, as_the_user, command, environment, now

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="the checkout is git's")
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


def rb(cwd: Path, *argv: str, env: dict = None, code: int = 0) -> str:
    r = subprocess.run([*command(), *argv], cwd=cwd, env=env or environment(), capture_output=True, text=True, stdin=subprocess.DEVNULL)
    assert code is None or r.returncode == code, (argv, r.returncode, r.stdout, r.stderr)
    return r.stdout


def rbj(cwd: Path, *argv: str, env: dict = None, code: int = 0) -> dict:
    return json.loads(rb(cwd, *argv, "--json", env=env, code=code))["data"]


def git(cwd: Path, *argv: str) -> str:
    return subprocess.run([GIT, *argv], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def under_title(text: str) -> list:
    """rb status's lines under its title, to the first blank line."""
    lines = text.splitlines()
    return lines[1:lines.index("") if "" in lines else len(lines)]


def first_bullet(briefing: str) -> str:
    part = briefing.split("## The repository", 1)[1].lstrip("\n")
    return part.splitlines()[0]


def head_of(line: str) -> str:
    """rb context's first bullet from data.repository.line: the line without what one step down adds, which the nested
    bullets say (PARITY 7)."""
    head = line.split(": ", 1)[0]
    return head.replace(", which does not hold the record", "")


def said_everywhere(root: Path, words: str, line: str = None, branch="?", env: dict = None) -> None:
    """The phrase on rb status (text and JSON), rb context, rb://context and rb page's "The page says"."""
    st = rbj(root, "status", env=env)
    repo = st["repository"]
    assert repo["said"] == words.split(" \u00b7 ")[-1], repo
    assert words in under_title(rb(root, "status", env=env)), rb(root, "status", env=env)
    if branch != "?":
        assert repo["branch"] == branch and repo["on_branch"] is (branch is not None), repo
    whole = line or repo["line"]
    assert first_bullet(rb(root, "context", env=env)) == f"- {head_of(whole)}"
    assert repo["line"] == whole
    mcp = Mcp(root=root)
    try:
        res = mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "resources/read", "params": {"uri": "rb://context"}})
        assert first_bullet(res["result"]["contents"][0]["text"]) == f"- {head_of(whole)}"
    finally:
        mcp.close()
    page = rbj(root, "page", "--out", str(root.parent / f"{root.name}-page.html"), env=env)
    assert page["page_says"].startswith(f"The page says: {repo['said']}"), page
    evidence_everywhere(root, env=env)


def evidence_everywhere(root: Path, env: dict = None) -> list:
    """PARITY 14.2-3: part 1's words after its time are the same on rb status's text (the line after the scope
    sentence), in data.repository.evidence, in rb context's first nested bullet and rb://context, and on rb page's
    block (in the forms a page shares); and the command the evidence gives agrees with git, run from another folder."""
    repo = rbj(root, "status", env=env)["repository"]
    ev = repo["evidence"]
    found = ev[0]
    assert found["kind"] == "found" and found["when"].startswith("at ") and found["at"] == repo["checked_at"], found
    after = found["text"].split(found["when"], 1)[1]
    text = rb(root, "status", env=env).splitlines()
    scope = next(i for i, l in enumerate(text) if l.startswith("rb looks in "))
    assert text[scope + 1].endswith(after), (text[scope + 1], after)
    plain = lambda t: t.replace("`", "")
    nested = rb(root, "context", env=env).split("## The repository", 1)[1].lstrip("\n").splitlines()[1]
    assert nested.startswith("  - rb ") and plain(nested).endswith(after), (nested, after)
    if env is None:   # the MCP server runs in this process's environment
        mcp = Mcp(root=root)
        try:
            res = mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "resources/read", "params": {"uri": "rb://context"}})
            assert res["result"]["contents"][0]["text"].split("## The repository", 1)[1].lstrip("\n").splitlines()[1] == nested
        finally:
            mcp.close()
    out = root.parent / f"{root.name}-evidence.html"
    rb(root, "page", "--out", str(out), env=env)
    block = re.search(r'<div id="in-git" tabindex="-1"><p>(.*?)</p>', out.read_text(encoding="utf-8"), re.S).group(1)
    shared = html.unescape(re.sub(r"<[^>]+>", "", block))
    if "rule outside" not in shared and not (repo["ignored_by"] or "").startswith("/"):
        assert after in shared, (shared, after)
    # the command agrees with git: its lines are the files rb counts, nothing where all is committed
    if repo["check"] and env is None:
        ran = subprocess.run(repo["check"], shell=True, cwd=root.parent.parent, capture_output=True, text=True)
        lines = [l for l in ran.stdout.splitlines() if l.strip()]
        if " status " in repo["check"]:
            assert len(lines) == repo["record_files_not_committed"], (lines, repo)
            assert [l[3:] for l in lines][:len(repo["not_committed_files"])] == [f["path"] for f in repo["not_committed_files"]], (lines, repo)
        else:
            assert lines and lines[0].split("\t")[0] == repo["ignored_by"], (lines, repo)
    return ev


def test_one_wording_in_every_state(home):
    root = home / "int8-resnet50"
    root.mkdir()
    git(root, "init", "-q")
    rb(root, "init", "INT8 ResNet-50")
    said_everywhere(root, "no commit yet", "no commit yet", branch="main")
    assert "The repository, no commit yet: " in rb(root, "reconcile")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "the record")
    head = git(root, "rev-parse", "HEAD")
    said_everywhere(root, "all record files committed", f"all record files committed \u00b7 at commit {head[:12]}", branch="main")
    assert rbj(root, "page")["page_says"] == f"The page says: all record files committed \u00b7 at commit {head[:12]}."
    assert f"The page says: all record files committed \u00b7 at commit {head[:12]}." in rb(root, "page")
    # a note: its file and the log
    rb(root, "note", "add", "the held-out set is not in the pretraining data")
    said_everywhere(root, "2 record files not committed", f"2 record files not committed \u00b7 at commit {head[:12]}")
    assert "2 record files not committed. To keep a copy first" in rb(root, "remove", "--dry-run")
    # no branch: said first on rb status's line, never "HEAD"
    git(root, "checkout", "-q", "--detach")
    said_everywhere(root, "no branch \u00b7 2 record files not committed", f"no branch \u00b7 2 record files not committed \u00b7 at commit {head[:12]}", branch=None)
    # rb status asks no one to commit
    assert not any("git commit" in n for n in json.loads(rb(root, "status", "--json"))["next"])


def test_a_record_git_ignores_and_a_record_outside_git(home):
    root = home / "ignored"
    root.mkdir()
    git(root, "init", "-q")
    (root / ".gitignore").write_text(".rb/\n", encoding="utf-8")
    rb(root, "init", "INT8 ResNet-50")
    st = rbj(root, "status")["repository"]
    n = st["record_files_ignored"]
    assert n > 0 and st["state"] == "ignored" and st["said"] == f"{n} record files ignored by git" and st["ignored_by"] == ".gitignore:1:.rb/"
    said_everywhere(root, f"{n} record files ignored by git")
    assert first_bullet(rb(root, "context")) == f"- {head_of(st['line'])}" and ": " not in first_bullet(rb(root, "context"))
    ev = evidence_everywhere(root)
    assert ev[0]["text"].endswith(f": the repository has no commit yet, and git ignores {n} record files (.gitignore:1:.rb/), so a commit leaves them out."), ev
    assert ev[0]["code"] == [".gitignore:1:.rb/"] and ev[1]["text"].startswith("Ignored by git: .rb/.gitattributes, .rb/.gitignore")
    doctor = rb(root, "doctor", code=1)
    assert f"FAIL git_ignored: {n} record files ignored by git: git ignores {n} record files (.gitignore:1:.rb/), so a commit leaves them out" in doctor
    # outside git: the receipt has no commit, said in the same words
    plain = home / "plain"
    plain.mkdir()
    rb(plain, "init", "INT8 ResNet-50")
    said_everywhere(plain, "not in git", "not in git", branch=None)
    assert rbj(plain, "status")["repository"]["state"] == "not_in_git"
    rb(plain, "experiment", "add", "INT8 against FP32", "--id", "e1", "--baseline", "fp32", "--candidate", "int8")
    assert "Not in git: the receipt has no commit." in rb(plain, "run", "add", "e1", "int8.top1=75.1", "fp32.top1=76.0", "--produced-at", now())
    assert "git_" not in rb(plain, "doctor")
    assert "The repository, not in git: " in rb(plain, "reconcile")


SHELL_GIT = pytest.mark.skipif(os.name == "nt", reason="the git put first on PATH here is a shell script")


def committed_record(home):
    root = home / "int8-resnet50"
    root.mkdir()
    git(root, "init", "-q")
    rb(root, "init", "INT8 ResNet-50")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "the record")
    return root


def test_git_not_found(home, tmp_path_factory):
    root = committed_record(home)
    # a PATH with no git on it, where a .git is
    empty = tmp_path_factory.mktemp("nogit")
    env = {**environment(), "PATH": str(empty)}
    st = rbj(root, "status", env=env)
    assert (st["repository"]["state"], st["repository"]["said"]) == ("git_not_found", "git not found")
    assert "git not found" in under_title(rb(root, "status", env=env))
    assert [e["text"].split(" at ", 1)[0] for e in evidence_everywhere(root, env=env)] == ["rb could not find git"]
    assert "FAIL git_not_found: git not found: rb cannot find git, so it cannot say what is committed" in rb(root, "doctor", env=env, code=1)
    rb(root, "experiment", "add", "INT8 against FP32", "--id", "e1", "--baseline", "fp32", "--candidate", "int8", env=env)
    assert "git not found: the receipt has no commit." in rb(root, "run", "add", "e1", "int8.top1=75.1", "fp32.top1=76.0", "--produced-at", now(), env=env)


@SHELL_GIT
def test_git_rb_could_not_read(home, tmp_path_factory):
    root = committed_record(home)
    # a git that answers with an error: its first line
    fake = tmp_path_factory.mktemp("badgit")
    (fake / "git").write_text("#!/bin/sh\necho \"fatal: detected dubious ownership in repository at '$PWD'\" >&2\nexit 128\n", encoding="utf-8")
    (fake / "git").chmod((fake / "git").stat().st_mode | stat.S_IEXEC)
    env = {**environment(), "PATH": f"{fake}{os.pathsep}{os.environ['PATH']}"}
    st = rbj(root, "status", env=env)["repository"]
    assert st["state"] == "unreadable" and st["said"] == "rb could not read git" and st["why"].startswith("fatal: detected dubious ownership")
    assert first_bullet(rb(root, "context", env=env)) == "- rb could not read git"
    ev = evidence_everywhere(root, env=env)
    assert [e["kind"] for e in ev] == ["found", "git", "check"] and ev[1]["text"].startswith("fatal: detected dubious ownership"), ev
    assert ev[2]["text"] == "To ask git yourself, in your terminal:" and ev[2]["says"] is None
    nested = rb(root, "context", env=env).split("## The repository", 1)[1].lstrip("\n").splitlines()[2]
    assert nested.startswith("  - `fatal: detected dubious ownership"), nested
    assert "FAIL git_unreadable: rb could not read git: fatal: detected dubious ownership" in rb(root, "doctor", env=env, code=1)


@SHELL_GIT
@pytest.mark.parametrize("fails", ["rev-parse --verify -q HEAD", "symbolic-ref --short -q HEAD"])
def test_a_failed_read_of_head_is_no_state(home, tmp_path_factory, fails):
    """Codex S5 should 2: a git that failed only to say HEAD's commit (or its branch) read as "no commit yet" (or "no
    branch") where the record's own status still answered. A failure says rb could not read git, with git's line."""
    root = committed_record(home)
    real = shutil.which("git")
    fake = tmp_path_factory.mktemp("failhead")
    (fake / "git").write_text(f'#!/bin/sh\ncase "$*" in *"{fails}"*) echo "fatal: test read failure" >&2; exit 128;; esac\nexec {real} "$@"\n', encoding="utf-8")
    (fake / "git").chmod((fake / "git").stat().st_mode | stat.S_IEXEC)
    env = {**environment(), "PATH": f"{fake}{os.pathsep}{os.environ['PATH']}"}
    st = rbj(root, "status", env=env)["repository"]
    assert (st["state"], st["said"], st["why"]) == ("unreadable", "rb could not read git", "fatal: test read failure"), st
    assert "The page says: rb could not read git." in rb(root, "page", "--out", str(home / "p.html"), env=env)


def test_a_submodule_with_only_new_files_is_a_change_outside_the_record(home):
    """Codex S5 should 1: rb status read changes outside the record from lists that hold no submodule whose only change
    is a new file, where the receipt's status and rb open's fold count it."""
    dep = home / "dep"
    dep.mkdir()
    git(dep, "init", "-q")
    (dep / "quant.py").write_text("x = 1\n", encoding="utf-8")
    git(dep, "add", "-A")
    git(dep, "commit", "-qm", "dep")
    root = home / "int8-resnet50"
    root.mkdir()
    git(root, "init", "-q")
    git(root, "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(dep), "dep")
    rb(root, "init", "INT8 ResNet-50")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "the record")
    assert rbj(root, "status")["repository"]["changes_outside_record"] is False
    (root / "dep" / "new.txt").write_text("calibration notes\n", encoding="utf-8")
    st = rbj(root, "status")["repository"]
    assert st["changes_outside_record"] is True and st["line"].endswith(", with changes outside the record not committed"), st


def test_the_checkout_and_the_next_receipt_agree(home):
    root = home / "int8-resnet50"
    root.mkdir()
    git(root, "init", "-q")
    (root / "eval.py").write_text("print('top1')\n", encoding="utf-8")
    rb(root, "init", "INT8 ResNet-50")
    rb(root, "experiment", "add", "INT8 against FP32", "--id", "e1", "--baseline", "fp32", "--candidate", "int8")
    rb(root, "spec", "set", "e1", "seed", "--per-run")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "the record")
    # rb's own outputs: its page, and a skill folder rb wrote that no commit holds
    rb(root, "page")
    rb(root, "skill", "add", "--claude", "--project", "--yes")
    assert (root / "rb-page.html").exists() and any((root / ".claude").rglob("*"))
    assert git(root, "status", "--porcelain", "--untracked-files=all", "--", ":(exclude).rb")
    assert rbj(root, "status")["repository"]["changes_outside_record"] is False
    rb(root, "journal", "scan")
    run = rbj(root, "run", "add", "e1", "int8.top1=75.1", "fp32.top1=76.0", "--set", "seed=1", "--produced-at", now())
    assert run["object"]["receipt"]["attached"]["dirty"] is False
    journal = rb(root, "journal", "-n", "0")
    # a tracked file changed: both flip
    (root / "eval.py").write_text("print('top1', 'top5')\n", encoding="utf-8")
    repo = rbj(root, "status")["repository"]
    assert repo["changes_outside_record"] is True and repo["line"].endswith(", with changes outside the record not committed")
    said = rb(root, "run", "add", "e1", "int8.top1=75.2", "fp32.top1=76.0", "--set", "seed=2", "--produced-at", now(), "--json")
    run = json.loads(said)["data"]
    assert run["object"]["receipt"]["attached"]["dirty"] is True
    assert "Changes outside the record not committed when recorded" in rb(root, "run", "add", "e1", "int8.top1=75.3", "fp32.top1=76.0", "--set", "seed=3", "--produced-at", now())
    assert "repository when recorded: commit " in rb(root, "show", run["object"]["id"])
    assert ", with changes outside the record not committed" in rb(root, "show", run["object"]["id"])
    # the journal's own flag flipped (a tracked file changed): --json keeps the entry, the text writes no line for it
    entries = rbj(root, "journal", "-n", "0")["entries"]
    flips = [e for e in entries if e["op"] == "git" and e.get("dirty")]
    assert flips and all(e["said"] is None for e in flips)
    assert [l for l in rb(root, "journal", "-n", "0").splitlines() if " git " in l] == [l for l in journal.splitlines() if " git " in l]


def test_rb_journal_lists_moves_of_head_in_words(home):
    root = home / "int8-resnet50"
    root.mkdir()
    git(root, "init", "-q")
    rb(root, "init", "INT8 ResNet-50")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "one")
    first = git(root, "rev-parse", "HEAD")
    rb(root, "journal", "scan")
    for n in ("two", "three"):
        (root / f"{n}.py").write_text(n, encoding="utf-8")
        git(root, "add", "-A")
        git(root, "commit", "-qm", n)
    third = git(root, "rev-parse", "HEAD")
    rb(root, "journal", "scan")
    git(root, "checkout", "-q", "--detach", first)
    rb(root, "journal", "scan")
    git(root, "checkout", "-q", "main")
    rb(root, "journal", "scan")
    rb(root, "journal", "run", "--", "python3", "-c", "print(1)")
    text = rb(root, "journal", "-n", "0")
    git_lines = [l for l in text.splitlines() if "  git  " in l]
    assert re.fullmatch(rf"  \d\d:\d\d UTC  git      new commit \u00b7 at commit {first[:12]}", git_lines[0]), git_lines
    assert git_lines[1].endswith(f"2 new commits \u00b7 at commit {third[:12]} \u00b7 2 paths changed since commit {first[:12]}"), git_lines
    assert git_lines[2].endswith(f"another commit checked out \u00b7 at commit {first[:12]} \u00b7 2 paths changed since commit {third[:12]}"), git_lines
    # back to a commit HEAD named before: no new commit, though it descends from where HEAD was
    assert git_lines[3].endswith(f"another commit checked out \u00b7 at commit {third[:12]} \u00b7 2 paths changed since commit {first[:12]}"), git_lines
    assert len(git_lines) == 4 and "dirty" not in text and "HEAD" not in text
    ran = [l for l in text.splitlines() if "  ran  " in l]
    assert re.fullmatch(r"  \d\d:\d\d UTC  ran      .*print.* \(exit 0\)", ran[0]), ran
    assert all(re.match(r"  \d\d:\d\d UTC  ", l) for l in text.splitlines() if l.startswith("  ") and not l.startswith("   ")), text
    entries = rbj(root, "journal", "-n", "0")["entries"]
    assert all("secs" in e for e in entries if e["op"] == "run")
    gits = [e for e in entries if e["op"] == "git"]
    assert [e["said"] for e in gits if e["said"]] == [l.split("git      ", 1)[1].split(" \u00b7 2 paths")[0] for l in git_lines]


PAGE_MARK = "made with rb page · a snapshot of the record at "


def about_lines(page: str) -> list:
    """About's footer lines on a page: its snapshot line, then the checkout's line where there is one."""
    foot = page.split('<footer class="foot">', 1)[1].split("<details", 1)[0]
    return [html.unescape(re.sub(r"<[^>]+>", "", s)) for s in re.findall(r"<span(?: class=\"held\")?>(.*?)</span>", foot)]


def test_a_page_says_in_about_how_its_checkout_held_the_record(home):
    """About's line 2, per state, under its snapshot line; the owner's span at the end of the Overview's line; and the
    phrase nowhere else in the page's bytes. rb export --with-record, built inside the work tree, says the checkout it
    was made from, never its copy."""
    root = home / "int8-resnet50"
    root.mkdir()
    git(root, "init", "-q")
    rb(root, "init", "INT8 ResNet-50")
    out = home / "page.html"

    def page() -> str:
        rb(root, "page", "--out", str(out))
        return out.read_text(encoding="utf-8")

    first = about_lines(page())
    assert first[0].startswith(PAGE_MARK) and first[1:] == ["no commit yet"], first
    git(root, "add", "-A")
    git(root, "commit", "-qm", "the record")
    head = git(root, "rev-parse", "HEAD")
    assert about_lines(page())[1:] == [f"all record files committed · at commit {head[:12]}"]
    rb(root, "experiment", "add", "INT8 against FP32", "--id", "e1", "--baseline", "fp32", "--candidate", "int8")
    rb(root, "run", "add", "e1", "int8.top1=75.1", "fp32.top1=76.0", "--produced-at", now())
    git(root, "add", "-A")
    git(root, "commit", "-qm", "a run")
    head = git(root, "rev-parse", "HEAD")
    rb(root, "note", "add", "the held-out set is not in the pretraining data")
    said = f"2 record files not committed · at commit {head[:12]}"
    text = page()
    assert about_lines(text)[1:] == [said]
    # the owner's span ends the Overview's line, its separator inside it; readers and no viewer get nothing there
    ovh = text.split('<p class="counts ovh">', 1)[1].split("</p>", 1)[0]
    assert ovh.endswith('<span class="held" data-vw="owner"> · <a href="#in-git">2 record files not committed</a></span>'), ovh
    assert text.count("record files not committed") == 2
    # no branch on a detached HEAD: the page's line names none
    git(root, "checkout", "-q", "--detach")
    assert about_lines(page())[1:] == [said]
    git(root, "checkout", "-q", "main")
    # rb export --with-record at its default place, inside the work tree: the checkout's words, never the copy's
    rb(root, "export", "--with-record")
    bundle = (root / "rb-export" / "page.html").read_text(encoding="utf-8")
    assert about_lines(bundle)[1:] == [said], about_lines(bundle)
    rb(root, "export", "--out", str(home / "plain-export"))
    assert about_lines((home / "plain-export" / "page.html").read_text(encoding="utf-8"))[1:] == [said]
    # outside git
    plain = home / "plain"
    plain.mkdir()
    rb(plain, "init", "INT8 ResNet-50")
    rb(plain, "page", "--out", str(out))
    assert about_lines(out.read_text(encoding="utf-8"))[1:] == ["not in git"]


def test_the_not_held_line_says_the_commit_s_words_before_the_changes(home):
    """MP-1109-0930 later 4: "which" can only mean the commit: the commit's words come before the changes'."""
    root = home / "int8-resnet50"
    root.mkdir()
    git(root, "init", "-q")
    rb(root, "init", "INT8 ResNet-50")
    (root / "eval.py").write_text("print(1)\n", encoding="utf-8")
    git(root, "add", "eval.py")
    git(root, "commit", "-qm", "code")
    (root / "eval.py").write_text("print(2)\n", encoding="utf-8")
    c = git(root, "rev-parse", "HEAD")
    line = json.loads(rb(root, "status", "--json"))["data"]["repository"]["line"]
    assert line.endswith(f"· at commit {c[:12]}, which does not hold the record, with changes outside the record not committed"), line
    # rb context's head leaves it to the nested bullet, part 1, which says the commit's words after the commit
    bullet, nested = rb(root, "context").split("## The repository", 1)[1].lstrip("\n").splitlines()[:2]
    assert bullet.endswith(f"· at commit {c[:12]}, with changes outside the record not committed"), bullet
    assert nested.endswith(f"since commit {c[:12]}, which does not hold the record."), nested
