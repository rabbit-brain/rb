"""rb remove takes rb out of a checkout: only what checks out as rb's, after showing the plan, and never a research
file, a git object, a worktree or history. Every name, id and number here is made up (INT8 ResNet-50 on ImageNet)."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from rbtest import AGENT_VARS, command, environment

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="the record and its refs are git's")

HOUSE = b"# House rules\n\nRun the tests before a commit.\n"
FOUR = b'{\n    "mcpServers": {\n        "other": {\n            "command": "other-server"\n        }\n    }\n}\n'


class Repo:
    """A git repository at base/p where rb runs as human:ana, with HOME and git's configuration of the test's own."""

    def __init__(self, base: Path) -> None:
        self.root = base / "p"
        self.root.mkdir(parents=True)
        home = base / "home"
        home.mkdir()
        cfg = base / "gitconfig"
        cfg.write_text("[user]\n\temail = ana@example.org\n\tname = ana\n[init]\n\tdefaultBranch = main\n[core]\n\tautocrlf = false\n", encoding="utf-8")
        self.env = {k: v for k, v in environment().items() if k not in AGENT_VARS}
        self.env.update(GIT_CONFIG_GLOBAL=str(cfg), GIT_CONFIG_NOSYSTEM="1", RB_TELEMETRY="0", DO_NOT_TRACK="1", RB_ACTOR="human:ana",
                        HOME=str(home), USERPROFILE=str(home))

    def git(self, *args: str, cwd: Path | None = None) -> str:
        r = subprocess.run(["git", *args], cwd=cwd or self.root, env=self.env, capture_output=True, text=True)
        assert r.returncode == 0, (args, r.stderr)
        return r.stdout

    def run(self, *argv: str, who: dict | None = None, cwd: Path | None = None) -> subprocess.CompletedProcess:
        env = {**self.env, **(who or {})}
        if who:
            env.pop("RB_ACTOR", None)
        return subprocess.run([*command(), *argv], cwd=cwd or self.root, env=env, capture_output=True, encoding="utf-8", errors="replace",
                              stdin=subprocess.DEVNULL)

    def rb(self, *argv: str, who: dict | None = None, cwd: Path | None = None) -> dict:
        r = self.run(*argv, "--json", who=who, cwd=cwd)
        out = json.loads(r.stdout)
        assert out["ok"] and r.returncode == 0, (argv, r.returncode, out.get("errors"), r.stderr)
        return out["data"]

    def files(self) -> dict:
        """Every file outside .git with its bytes, and .git's refs and rb's folder there."""
        seen = {}
        for p in sorted(self.root.rglob("*")):
            rel = p.relative_to(self.root).as_posix()
            if p.is_file() and (not rel.startswith(".git/") or rel.startswith((".git/refs/", ".git/rb/", ".git/rb-last-seen.json"))):
                seen[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
        return seen


@pytest.fixture
def repo(tmp_path):
    """INT8 ResNet-50 with rb in it: a team's .claude/, AGENTS.md and .mcp.json (another server, four spaces), all
    committed; rb init with --yes, a run on uncommitted code (a ref that keeps it), rb page and rb export at their
    defaults. `before` is every file as it was before rb."""
    rec = Repo(tmp_path)
    rec.git("init", "-q")
    (rec.root / "train.py").write_text("lr = 0.1\n", encoding="utf-8", newline="\n")
    (rec.root / ".claude").mkdir()
    (rec.root / "AGENTS.md").write_bytes(HOUSE)
    (rec.root / ".mcp.json").write_bytes(FOUR)
    rec.git("add", "-A")
    rec.git("-c", "commit.gpgsign=false", "commit", "-qm", "start")
    rec.before = rec.files()
    rec.times = {name: (rec.root / name).stat().st_mtime_ns for name in ("AGENTS.md", ".mcp.json")}
    rec.rb("init", "INT8 ResNet-50 on ImageNet", "--yes")
    rec.rb("experiment", "add", "int8 against fp32", "--id", "e1", "--baseline", "fp32", "--candidate", "int8")
    (rec.root / "train.py").write_text("lr = 0.2\n", encoding="utf-8", newline="\n")
    rec.rb("run", "add", "e1", "fp32.top1=0.761", "int8.top1=0.758")
    (rec.root / "train.py").write_text("lr = 0.1\n", encoding="utf-8", newline="\n")
    rec.rb("page")
    rec.rb("export")
    return rec


def _plan(rec: Repo) -> dict:
    return rec.rb("remove", "--dry-run")


def test_remove_takes_back_what_is_rb_s_and_leaves_the_rest_as_it_was(repo):
    """The plan says what goes and how rb knows it is its own; the removal takes exactly that, and every file is as it
    was before rb: AGENTS.md and .mcp.json byte for byte and with the times they had, no ref under refs/rb/, no rb
    folder in .git."""
    plan = _plan(repo)
    assert [i["path"] for i in plan["remove"]] == [".rb/", ".claude/skills/rabbit-brain/SKILL.md", "AGENTS.md", ".mcp.json", "rb-page.html", "rb-export/",
                                                    "refs/rb/ (1 ref)", ".git/rb/"]
    assert plan["keep"] == [] and plan["others"] == [] and plan["dry_run"]
    assert plan["untouched"].startswith("Not rb's to touch: your research files, worktrees and .git/worktrees, git history, git objects")
    assert plan["residue"] and all(len(o) == 40 for o in plan["residue"])
    copy = plan["copy_first"].removeprefix("rb export --with-record --zip --out ")
    assert plan["uncommitted"] > 0 and copy == (repo.root.parent.resolve() / "int8-resnet-50-on-imagenet-record.zip").as_posix()
    done = repo.rb("remove", "--yes")
    assert done["removed"] == [i["path"] for i in plan["remove"]] and done["failed"] == []
    assert done["put_back"] == ["AGENTS.md", ".mcp.json"]                 # rb's part taken out: the files stay
    assert repo.files() == repo.before
    assert {name: (repo.root / name).stat().st_mtime_ns for name in repo.times} == repo.times
    assert repo.git("for-each-ref", "refs/rb/") == "" and not (repo.root / ".git" / "refs" / "rb").exists()
    for o in plan["residue"]:                                   # no object is deleted: git's own gc takes them in its time
        assert subprocess.run(["git", "cat-file", "-e", o], cwd=repo.root, env=repo.env).returncode == 0
    assert subprocess.run(["git", "fsck", "--no-dangling", "--no-progress"], cwd=repo.root, env=repo.env, capture_output=True).returncode == 0


def test_dry_run_removes_nothing_and_its_text_names_no_writer(repo):
    before = repo.files()
    r = repo.run("remove", "--dry-run")
    assert r.returncode == 0 and repo.files() == before
    assert r.stdout.startswith("Dry run: nothing was removed. rb remove would do this in ")
    for part in ("Will remove:\n  .rb/: the record (", "\n  AGENTS.md: rb's section, as rb added it: the file goes back to what it was, byte for byte",
                 "\nNot rb's to touch: ", "\nListed, not removed:\n", "pip uninstall rabbit-brain", " files of the record have uncommitted changes. To keep a copy first, run: rb export --with-record "):
        assert part in r.stdout, part
    for writer in ("human:", "ana", "agent:"):
        assert writer not in r.stdout.split(" would do this in ", 1)[1].split("\n", 1)[1], writer


def test_a_user_s_edit_outside_rb_s_section_stays_and_the_section_goes(repo):
    """AGENTS.md changed since rb added its section: rb's section alone is cut out, by its own bytes, and the user's
    edit stays. An edit inside the section keeps the section, and the plan names its lines."""
    path = repo.root / "AGENTS.md"
    path.write_bytes(path.read_bytes() + b"\nOne more rule the team added.\n")
    plan = _plan(repo)
    [item] = [i for i in plan["remove"] if i["path"] == "AGENTS.md"]
    assert "the rest of the file, changed since, stays" in item["why"]
    repo.rb("remove", "--yes")
    assert path.read_bytes() == HOUSE + b"\nOne more rule the team added.\n"


def test_an_edit_inside_rb_s_section_keeps_it_and_the_plan_says_so(repo):
    path = repo.root / "AGENTS.md"
    path.write_bytes(path.read_bytes().replace(b"## When to record", b"## When to record (our way)"))
    edited = path.read_bytes()
    plan = _plan(repo)
    [kept] = [k for k in plan["keep"] if k["path"] == "AGENTS.md"]
    assert kept["why"].startswith("rb's section (lines 5 to ") and kept["why"].endswith("was changed since rb wrote it: delete those lines yourself if you want it gone")
    repo.rb("remove", "--yes")
    assert path.read_bytes() == edited


def test_rb_s_server_comes_out_of_a_mcp_json_changed_since_and_one_not_rb_s_stays(repo):
    """.mcp.json with a server the user added since: rb's member alone comes out. A "rabbit-brain" member that is not
    as rb writes it is kept and named."""
    path = repo.root / ".mcp.json"
    cfg = json.loads(path.read_bytes())
    cfg["mcpServers"]["later"] = {"command": "later-server"}
    path.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8", newline="\n")
    repo.rb("remove", "--yes")
    assert json.loads(path.read_bytes()) == {"mcpServers": {"other": {"command": "other-server"}, "later": {"command": "later-server"}}}
    path.write_text(json.dumps({"mcpServers": {"rabbit-brain": {"type": "stdio", "command": "rb", "args": ["mcp"]}}}), encoding="utf-8")
    plan = _plan(repo)
    assert [k["path"] for k in plan["keep"]] == [".mcp.json"] and plan["keep"][0]["why"].startswith('its "rabbit-brain" server')


def test_a_file_in_the_record_rb_did_not_write_is_kept_and_named(repo):
    (repo.root / ".rb" / "notes-to-self.txt").write_text("read the paper again\n", encoding="utf-8")
    plan = _plan(repo)
    assert {"path": ".rb/notes-to-self.txt", "why": "not rb's: rb did not write it"} in plan["keep"]
    assert plan["remove"][0]["why"].endswith("(1 other file kept, below)")
    repo.rb("remove", "--yes")
    assert sorted(p.relative_to(repo.root).as_posix() for p in (repo.root / ".rb").rglob("*")) == [".rb/notes-to-self.txt"]


def test_a_line_the_user_added_to_the_record_s_gitignore_keeps_it(repo):
    gi = repo.root / ".rb" / ".gitignore"
    gi.write_text(gi.read_text(encoding="utf-8") + "my-private-scratch/\n", encoding="utf-8", newline="\n")
    plan = _plan(repo)
    assert {"path": ".rb/.gitignore", "why": "changed since rb wrote it"} in plan["keep"]
    repo.rb("remove", "--yes")
    assert gi.read_text(encoding="utf-8").endswith("my-private-scratch/\n")


def test_an_export_folder_a_removal_stopped_midway_still_goes(repo):
    """rb remove stopped while it took rb-export/ away (its page gone, its manifest still there): the folder is still
    rb's export, every file left as its manifest says, and rb remove run again takes it."""
    (repo.root / "rb-export" / "page.html").unlink()
    plan = _plan(repo)
    assert [i["why"] for i in plan["remove"] if i["path"] == "rb-export/"] == ["rb's export, every file as its manifest says"]
    repo.rb("remove", "--yes")
    assert not (repo.root / "rb-export").exists()


def test_a_page_the_user_wrote_over_is_kept(repo):
    page = repo.root / "rb-page.html"
    page.write_text("<p>my own notes</p>\n", encoding="utf-8")
    plan = _plan(repo)
    assert {"path": "rb-page.html", "why": "rb's page, changed since rb page wrote it: delete it yourself if you want it gone"} in plan["keep"]
    repo.rb("remove", "--yes")
    assert page.read_text(encoding="utf-8") == "<p>my own notes</p>\n"


def test_refused_in_an_agent_session_and_off_a_terminal_without_yes(repo):
    """Taking rb out is the user's: in an agent session only --dry-run runs, and where rb cannot ask, --yes says it."""
    before = repo.files()
    r = repo.run("remove", "--yes", who={"CLAUDECODE": "1"})
    assert r.returncode == 2 and "this is an agent session" in r.stderr and repo.files() == before
    r = repo.run("remove", "--dry-run", who={"CODEX_THREAD_ID": "thread-remove-1"})
    assert r.returncode == 0 and repo.files() == before
    r = repo.run("remove")
    assert r.returncode == 2 and "there is no terminal here to ask in" in r.stderr and "rb remove --yes" in r.stderr and repo.files() == before


@pytest.mark.parametrize("sign, said", [({"GITHUB_ACTIONS": "true"}, "this is an agent session"), ({"CLAUDE_CODE_ENTRYPOINT": "cli"}, "this is an agent session"),
                                        ({"RB_ACTOR": "agent:codex"}, "RB_ACTOR names an agent here")], ids=["a CI job", "a chat's leftover", "RB_ACTOR"])
def test_refused_wherever_rb_confirm_is(repo, sign, said):
    """rb remove is refused where rb confirm is: a CI job, a chat's leftover variable, and RB_ACTOR naming an agent;
    --dry-run still shows the plan."""
    before = repo.files()
    env = {**repo.env, **sign}
    r = subprocess.run([*command(), "remove", "--yes"], cwd=repo.root, env=env, capture_output=True, encoding="utf-8", stdin=subprocess.DEVNULL)
    assert r.returncode == 2 and said in r.stderr and repo.files() == before, r.stderr
    r = subprocess.run([*command(), "remove", "--dry-run"], cwd=repo.root, env=env, capture_output=True, encoding="utf-8", stdin=subprocess.DEVNULL)
    assert r.returncode == 0 and repo.files() == before


@pytest.mark.skipif(sys.platform == "win32", reason="needs a pseudo-terminal")
@pytest.mark.parametrize("answer, removed", [("", False), ("y", True)])
def test_in_the_user_s_own_terminal_it_shows_the_plan_and_asks_once(repo, answer, removed):
    import pty
    main_side, user_side = pty.openpty()
    proc = subprocess.Popen([*command(), "remove"], cwd=repo.root, env={**repo.env, "TERM": "xterm"}, stdin=user_side, stdout=user_side, stderr=user_side,
                            close_fds=True)
    os.close(user_side)
    shown = b""
    while b"[y/N] " not in shown:
        chunk = os.read(main_side, 4096)
        assert chunk, shown
        shown += chunk
    os.write(main_side, answer.encode() + b"\n")
    try:
        while chunk := os.read(main_side, 4096):
            shown += chunk
    except OSError:
        pass
    os.close(main_side)
    assert proc.wait() == 0
    text = shown.decode("utf-8").replace("\r\n", "\n")
    assert "Will remove:\n  .rb/" in text and text.count("[y/N]") == 1
    assert (repo.files() == repo.before) is removed and ("Nothing was removed." in text) is not removed


def test_a_tracked_record_is_said_as_deletions_git_will_show(repo):
    repo.git("add", ".rb")
    repo.git("-c", "commit.gpgsign=false", "commit", "-qm", "the record")
    tracked = len(repo.git("ls-files", ".rb").split())
    plan = _plan(repo)
    assert plan["tracked_deleted"] == tracked and plan["uncommitted"] == 0 and plan["copy_first"] == ""
    r = repo.run("remove", "--dry-run")
    assert f"git will show {tracked} files deleted; history keeps them." in r.stdout and "uncommitted changes" not in r.stdout
    repo.rb("remove", "--yes")
    status = repo.git("status", "--porcelain")
    assert sorted(line[3:] for line in status.splitlines()) == sorted(repo.git("ls-files", ".rb").split()) and all(l.startswith(" D ") for l in status.splitlines())


def test_tracked_files_rb_deletes_or_changes_outside_the_record_are_counted_too(repo):
    """The team committed what rb init wrote (the skill, AGENTS.md with rb's section) and rb's page: rb remove deletes
    two tracked files and gives AGENTS.md back its bytes from before rb, which git shows as a change. The line counts
    each, and git status shows exactly those."""
    repo.git("add", ".claude/skills", "AGENTS.md", "rb-page.html")
    repo.git("-c", "commit.gpgsign=false", "commit", "-qm", "rb's files")
    plan = _plan(repo)
    assert (plan["tracked_deleted"], plan["tracked_changed"]) == (2, 1)
    assert "git will show 2 files deleted and 1 changed; history keeps them as they were." in repo.run("remove", "--dry-run").stdout
    repo.rb("remove", "--yes")
    assert sorted(repo.git("status", "--porcelain", "-uno").splitlines()) == [" D .claude/skills/rabbit-brain/SKILL.md", " D rb-page.html", " M AGENTS.md"]


def test_another_checkout_s_record_keeps_the_clone_s_part_and_names_its_command(repo, tmp_path):
    """While another worktree of the clone holds a record, rb remove here keeps what the clone holds for rb (its refs,
    rb's folder in .git) and names that place with the command to run there. Run there too, everything goes."""
    repo.git("add", ".rb")
    repo.git("-c", "commit.gpgsign=false", "commit", "-qm", "the record")
    wt = tmp_path / "wt-b"
    repo.git("worktree", "add", "-q", "-b", "exp-b", str(wt))
    plan = _plan(repo)
    [other] = plan["others"]
    assert other["command"].startswith("cd ") and other["command"].endswith("wt-b && rb remove") and other["place"].startswith("exp-b · ")
    assert "refs/rb/ (1 ref)" in [k["path"] for k in plan["keep"]] and not any(i["path"].startswith("refs/rb/") for i in plan["remove"])
    [why] = [k["why"] for k in plan["keep"] if k["path"] == "refs/rb/ (1 ref)"]
    assert why.endswith("(below): rb remove in the last checkout that holds one takes it"), why
    assert ".git/rb/" in [k["path"] for k in plan["keep"]]                 # a folder, said as Will remove says it
    repo.rb("remove", "--yes")
    assert repo.git("for-each-ref", "refs/rb/") != ""
    there = repo.rb("remove", "--yes", cwd=wt)
    assert "refs/rb/ (1 ref)" in there["removed"] and repo.git("for-each-ref", "refs/rb/") == ""


def test_a_locked_worktree_whose_folder_is_away_keeps_the_clone_s_part(repo, tmp_path):
    """A worktree locked by git while its folder is away (a removable drive) may hold a record with runs whose code
    only refs/rb/ keeps: rb remove here keeps the refs and rb's folder in .git, and names that place."""
    repo.git("add", ".rb")
    repo.git("-c", "commit.gpgsign=false", "commit", "-qm", "the record")
    wt = tmp_path / "wt-b"
    repo.git("worktree", "add", "-q", "-b", "exp-b", str(wt))
    repo.git("worktree", "lock", "--reason", "on a removable drive", str(wt))
    shutil.move(str(wt), str(tmp_path / "drive"))
    plan = _plan(repo)
    [other] = plan["others"]
    assert other["place"].startswith("exp-b · ") and other["place"].endswith("(locked, its folder not here)") and other["command"].endswith("wt-b && rb remove")
    assert "refs/rb/ (1 ref)" in [k["path"] for k in plan["keep"]] and not any(i["path"].startswith(("refs/rb/", ".git/rb")) for i in plan["remove"])
    repo.rb("remove", "--yes")
    assert repo.git("for-each-ref", "refs/rb/") != "" and (repo.root / ".git" / "rb").is_dir()


def test_a_staged_file_whose_blob_a_snapshot_holds_still_commits_after_removal(repo):
    """The objects rb's refs reached stay (rb deletes none): a file the user staged, whose blob the run's snapshot
    also holds, commits after rb remove."""
    (repo.root / "eval.py").write_text("print('top1')\n", encoding="utf-8", newline="\n")
    repo.rb("run", "add", "e1", "fp32.top1=0.760", "int8.top1=0.757")      # keeps the untracked eval.py in its snapshot
    repo.git("add", "eval.py")
    repo.rb("remove", "--yes")
    repo.git("-c", "commit.gpgsign=false", "commit", "-qm", "the eval script")
    assert repo.git("show", "HEAD:eval.py") == "print('top1')\n"
    assert subprocess.run(["git", "fsck", "--no-dangling", "--no-progress"], cwd=repo.root, env=repo.env, capture_output=True).returncode == 0


def test_rb_s_writes_staged_and_not_committed_are_named_with_the_command_that_unstages_them(repo):
    """git add -A after rb init stages the record, rb's section of AGENTS.md, its server in .mcp.json, the skill and the
    outputs, and no commit holds them: the plan says no history keeps them, names the staged files and the one command
    that takes rb's writes out of git's index (rb does not touch the index), and says it again once it is done.
    Followed, git status is as before rb, and the next commit adds none of rb's writes."""
    repo.git("add", "-A")
    plan = _plan(repo)
    staged = [line[3:] for line in repo.git("status", "--porcelain").splitlines()]
    assert sorted(plan["staged"]) == sorted(staged) and plan["tracked_deleted"] == 0 and plan["tracked_changed"] == 0
    assert plan["unstage"] == "git restore --staged -- .claude/skills/rabbit-brain/SKILL.md .rb rb-export rb-page.html && git add -- AGENTS.md .mcp.json"
    said = f"{len(staged)} staged files hold rb's writes, and rb does not touch git's index: "
    text = repo.run("remove", "--dry-run").stdout
    assert f"{said}after rb remove, run {plan['unstage']}, or your next commit adds them to history." in text and "history keeps them" not in text
    done = repo.run("remove", "--yes")
    assert done.returncode == 0 and done.stdout.rstrip().endswith(f"{said}run {plan['unstage']}, or your next commit adds them to history.")
    subprocess.run(plan["unstage"], shell=True, cwd=repo.root, env=repo.env, check=True)
    assert repo.git("status", "--porcelain") == "" and repo.files() == repo.before
    assert subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=repo.root, env=repo.env).returncode == 0


def test_a_staged_record_in_a_repository_with_no_commit_is_unstaged_with_git_rm_cached(tmp_path):
    rec = Repo(tmp_path)
    rec.git("init", "-q")
    (rec.root / "train.py").write_text("lr = 0.1\n", encoding="utf-8", newline="\n")
    rec.rb("init", "INT8 ResNet-50 on ImageNet")
    rec.git("add", "-A")
    plan = _plan(rec)
    assert plan["unstage"] == "git rm -r -q --cached -- .rb" and plan["tracked_deleted"] == 0
    rec.rb("remove", "--yes")
    subprocess.run(plan["unstage"], shell=True, cwd=rec.root, env=rec.env, check=True)
    assert rec.git("status", "--porcelain") == "A  train.py\n"


def test_a_record_git_ignores_holds_writes_no_commit_keeps(repo):
    """A .rb/ the repository's .gitignore keeps out of git is kept by no commit: the plan counts its files and names the
    command that keeps a copy outside the checkout, and git will show no file deleted."""
    (repo.root / ".gitignore").write_text(".rb/\n", encoding="utf-8", newline="\n")
    record = [p for p in (repo.root / ".rb").rglob("*") if p.is_file() and not p.relative_to(repo.root / ".rb").as_posix().startswith(("cache/", ".lock"))]
    plan = _plan(repo)
    assert plan["uncommitted"] == len(record) and plan["tracked_deleted"] == 0 and plan["copy_first"].startswith("rb export --with-record --zip --out ")


def test_a_checkout_moved_since_rb_wrote_is_still_put_back_as_it_was(repo, tmp_path):
    """The ledger names the checkout's files from its own folder, so a research folder moved since rb init (mv p q)
    still gets every file back as it was: AGENTS.md and .mcp.json byte for byte with their times, and no folder of
    rb's left (.claude/skills/)."""
    moved = tmp_path / "moved"
    shutil.move(str(repo.root), str(moved))
    repo.root = moved
    plan = _plan(repo)
    assert [i["why"] for i in plan["remove"] if i["path"] == "AGENTS.md"] == ["rb's section, as rb added it: the file goes back to what it was, byte for byte"]
    repo.rb("remove", "--yes")
    assert repo.files() == repo.before
    assert {name: (moved / name).stat().st_mtime_ns for name in repo.times} == repo.times
    assert not (moved / ".claude" / "skills").exists() and (moved / ".claude").is_dir()


def test_rb_s_parts_a_teammate_committed_stay_as_they_are(tmp_path):
    """A team committed rb's files before this checkout's rb init (rb's section in AGENTS.md, rb's server in .mcp.json,
    the skill and the Cursor rule, all as rb writes them): this checkout's ledger has no line for them, so rb remove
    keeps each and says so, and git shows nothing changed."""
    first = Repo(tmp_path / "a")
    first.git("init", "-q")
    for rec in (first,):
        (rec.root / ".claude").mkdir()
        (rec.root / ".cursor").mkdir()
        (rec.root / "AGENTS.md").write_bytes(HOUSE)
        (rec.root / ".mcp.json").write_bytes(FOUR)
    first.rb("init", "INT8 ResNet-50 on ImageNet", "--yes")
    rec = Repo(tmp_path / "b")
    rec.git("init", "-q")
    for rel in ("AGENTS.md", ".mcp.json", ".claude/skills/rabbit-brain/SKILL.md", ".cursor/rules/rabbit-brain.mdc"):
        (rec.root / rel).parent.mkdir(parents=True, exist_ok=True)
        (rec.root / rel).write_bytes((first.root / rel).read_bytes())
    rec.git("add", "-A")
    rec.git("-c", "commit.gpgsign=false", "commit", "-qm", "the team's rb files")
    before = rec.files()
    rec.rb("init", "INT8 ResNet-50 on ImageNet", "--yes")
    plan = _plan(rec)
    kept = {k["path"]: k["why"] for k in plan["keep"]}
    assert set(kept) == {"AGENTS.md", ".mcp.json", ".claude/skills/rabbit-brain/SKILL.md", ".cursor/rules/rabbit-brain.mdc"}, plan
    assert kept["AGENTS.md"].startswith("rb's section (lines 5 to ") and "rb did not add it here, so it stays" in kept["AGENTS.md"]
    assert "rb did not add it here, so it stays" in kept[".mcp.json"] and "rb did not write it here, so it stays" in kept[".cursor/rules/rabbit-brain.mdc"]
    rec.rb("remove", "--yes")
    assert rec.files() == before and rec.git("status", "--porcelain") == ""


def test_a_default_page_or_zip_changed_since_rb_wrote_it_stays(repo):
    """The researcher added their notes to rb-page.html (rb's own marks still in it) and a letter to rb-export.zip:
    the ledger keeps the bytes rb wrote, so both are kept, with their words."""
    import zipfile
    repo.rb("export", "--zip")
    page, zipped = repo.root / "rb-page.html", repo.root / "rb-export.zip"
    page.write_text(page.read_text(encoding="utf-8") + "<p>ana's own notes</p>\n", encoding="utf-8")
    with zipfile.ZipFile(zipped, "a") as z:
        z.writestr("rb-export/cover-letter.txt", "Dear reviewers,\n")
    plan = _plan(repo)
    kept = {k["path"]: k["why"] for k in plan["keep"]}
    assert kept["rb-page.html"].startswith("rb's page, changed since rb page wrote it") and kept["rb-export.zip"].startswith("rb's export, changed since rb export wrote it")
    edited = (page.read_bytes(), zipped.read_bytes())
    repo.rb("remove", "--yes")
    assert (page.read_bytes(), zipped.read_bytes()) == edited


def test_a_file_the_user_put_back_over_an_out_output_is_not_named(repo):
    """rb page --out README.md --replace, then the user puts their README back (git checkout): it is the user's file
    again, so rb remove names it nowhere."""
    (repo.root / "README.md").write_text("# INT8 ResNet-50\n", encoding="utf-8", newline="\n")
    repo.git("add", "README.md")
    repo.git("-c", "commit.gpgsign=false", "commit", "-qm", "readme")
    repo.rb("page", "--out", "README.md", "--replace")
    assert [k["path"] for k in _plan(repo)["keep"]] == ["README.md"]
    repo.git("checkout", "README.md")
    plan = _plan(repo)
    assert "README.md" not in [k["path"] for k in plan["keep"]] + [i["path"] for i in plan["remove"]]


BEGIN = "<!-- rabbit-brain skill: begin (installed by `rb skill add`; rerun it to update) -->"
END = "<!-- rabbit-brain skill: end -->"


def _agents_repo(tmp_path, text: str) -> Repo:
    rec = Repo(tmp_path)
    rec.git("init", "-q")
    (rec.root / "AGENTS.md").write_text(text, encoding="utf-8", newline="\n")
    rec.git("add", "-A")
    rec.git("-c", "commit.gpgsign=false", "commit", "-qm", "house rules")
    return rec


@pytest.mark.parametrize("edited", [False, True], ids=["as rb left it", "a line added since"])
def test_rb_s_markers_quoted_in_the_user_s_own_words_stay_theirs(tmp_path, edited):
    """A marker is rb's only as a line of its own: the user's sentence that quotes both markers, or the begin marker
    alone, stays word for word; rb's section goes at the end, and rb remove takes exactly it out again, by its own
    bytes once the file has changed."""
    house = f"# House rules\n\nrb puts its section between `{BEGIN}` and `{END}`; leave those lines alone.\nThe line `{BEGIN}` starts it.\n"
    rec = _agents_repo(tmp_path, house)
    rec.rb("init", "INT8 ResNet-50 on ImageNet", "--yes")
    path = rec.root / "AGENTS.md"
    now = path.read_text(encoding="utf-8")
    assert now.startswith(house + "\n" + BEGIN + "\n") and now.endswith(END + "\n")
    if edited:
        path.write_text(now + "\nOne more rule.\n", encoding="utf-8", newline="\n")
    plan = _plan(rec)
    assert "AGENTS.md" in [i["path"] for i in plan["remove"]], plan
    rec.rb("remove", "--yes")
    assert path.read_text(encoding="utf-8") == house + ("\nOne more rule.\n" if edited else "")


@pytest.mark.parametrize("edited", [False, True], ids=["as rb left it", "a line added since"])
def test_an_earlier_rb_section_is_replaced_in_place_and_comes_back(tmp_path, edited):
    """AGENTS.md holds an older rb section between rb's marker lines, then the team's own words. rb says it would put
    its section in place of that one (with its lines), and rb remove puts the older section back: the file is as it
    was, byte for byte, or with the line the user added since."""
    house = f"# House rules\n\n{BEGIN}\n\nAn older rb section the team kept.\n\n{END}\n\n## Our tail\n\nKeep this.\n"
    rec = _agents_repo(tmp_path, house)
    said = rec.rb("init", "INT8 ResNet-50 on ImageNet", "--dry-run", "--yes")
    [planned] = [p for p in said["plan"] if p["path"] == "AGENTS.md"]
    assert planned["what"] == "rb's section in place of the one between rb's markers (lines 3 to 7); nothing else in it changes"
    left = rec.rb("init", "INT8 ResNet-50 on ImageNet", "--dry-run")
    assert "in place of the one between rb's markers, lines 3 to 7." in " ".join(left["agents"]["said"])
    rec.rb("init", "INT8 ResNet-50 on ImageNet", "--yes")
    path = rec.root / "AGENTS.md"
    now = path.read_text(encoding="utf-8")
    assert now.startswith("# House rules\n\n" + BEGIN + "\n") and now.endswith(END + "\n\n## Our tail\n\nKeep this.\n") and "An older rb section" not in now
    if edited:
        path.write_text(now + "\nOne more rule.\n", encoding="utf-8", newline="\n")
    rec.rb("remove", "--yes")
    assert path.read_text(encoding="utf-8") == house + ("\nOne more rule.\n" if edited else "")
    assert rec.git("status", "--porcelain") == ("" if not edited else " M AGENTS.md\n")


needs_permissions = pytest.mark.skipif(sys.platform == "win32" or (hasattr(os, "geteuid") and os.geteuid() == 0),
                                       reason="a folder made read-only stops deletions only for a user that is not root, on a POSIX system")


@needs_permissions
@pytest.mark.parametrize("in_git", [True, False], ids=["in git", "outside git"])
def test_a_removal_stopped_midway_is_finished_by_a_second_one(tmp_path, in_git):
    """Something stops rb remove midway (a folder of the record it cannot delete from): the record keeps the files that
    say it is rb's (its log and project.yaml), and rb's ledger stays, so the second rb remove, once the user lets it,
    finishes: every record file is rb's still, and AGENTS.md goes back byte for byte, with its time."""
    import stat
    rec = Repo(tmp_path)
    if in_git:
        rec.git("init", "-q")
    (rec.root / "AGENTS.md").write_bytes(HOUSE)
    was = (rec.root / "AGENTS.md").stat().st_mtime_ns
    rec.rb("init", "INT8 ResNet-50 on ImageNet", "--yes")
    rec.rb("experiment", "add", "int8 against fp32", "--id", "e1", "--baseline", "fp32", "--candidate", "int8")
    rec.rb("run", "add", "e1", "fp32.top1=0.761", "int8.top1=0.758")
    held = rec.root / ".rb" / "experiments" / "e1"
    held.chmod(stat.S_IRUSR | stat.S_IXUSR)
    try:
        r = rec.run("remove", "--yes")
        assert r.returncode != 0 and "could not remove .rb/" in r.stdout, r.stdout
        assert (rec.root / ".rb" / "log.jsonl").is_file() and (rec.root / ".rb" / "project.yaml").is_file()
        ledger = (rec.root / ".git" / "rb" / "owned.jsonl") if in_git else (rec.root / ".rb" / "cache" / "owned.jsonl")
        assert ledger.is_file()
    finally:
        held.chmod(stat.S_IRWXU)
    plan = _plan(rec)
    assert plan["keep"] == [] and plan["remove"][0]["path"] == ".rb/", plan["keep"]
    rec.rb("remove", "--yes")
    assert not (rec.root / ".rb").exists() and (rec.root / "AGENTS.md").read_bytes() == HOUSE and (rec.root / "AGENTS.md").stat().st_mtime_ns == was


def test_the_last_line_says_which_files_rb_took_its_part_out_of(repo):
    """A file rb added to stays, with rb's part taken out: the result says so, and says removed only what is gone."""
    r = repo.run("remove", "--yes")
    assert r.returncode == 0
    last = r.stdout.rstrip("\n").splitlines()[-1]
    assert last == ("Removed .rb/, .claude/skills/rabbit-brain/SKILL.md, rb-page.html, rb-export/, refs/rb/ (1 ref), .git/rb/. "
                    "Took rb's part out of AGENTS.md, .mcp.json. Nothing else was changed."), last
    assert (repo.root / "AGENTS.md").read_bytes() == HOUSE


def test_with_every_ref_logged_the_residue_is_named_and_no_folder_of_rb_s_is_left(tmp_path):
    """With core.logAllRefUpdates=always git keeps a reflog of rb's refs too: the objects only rb's refs reached are
    still named, exactly those git no longer reaches after rb remove, and no empty folder of rb's reflogs stays."""
    rec = Repo(tmp_path)
    rec.git("init", "-q")
    rec.git("config", "core.logAllRefUpdates", "always")
    (rec.root / "train.py").write_text("lr = 0.1\n", encoding="utf-8", newline="\n")
    rec.git("add", "-A")
    rec.git("-c", "commit.gpgsign=false", "commit", "-qm", "start")
    rec.rb("init", "INT8 ResNet-50 on ImageNet")
    rec.rb("experiment", "add", "int8 against fp32", "--id", "e1", "--baseline", "fp32", "--candidate", "int8")
    (rec.root / "eval.py").write_text("print('top1')\n", encoding="utf-8", newline="\n")
    rec.rb("run", "add", "e1", "fp32.top1=0.761", "int8.top1=0.758")
    before = {line.split()[0] for line in rec.git("rev-list", "--objects", "--all", "--reflog", "--indexed-objects").splitlines()}
    plan = _plan(rec)
    assert plan["residue"], plan
    rec.rb("remove", "--yes")
    after = {line.split()[0] for line in rec.git("rev-list", "--objects", "--all", "--reflog", "--indexed-objects").splitlines()}
    assert set(plan["residue"]) == before - after
    assert not (rec.root / ".git" / "logs" / "refs" / "rb").exists() and not (rec.root / ".git" / "refs" / "rb").exists()


@pytest.mark.parametrize("touched", [False, True], ids=["as the tour left it", "changed since"])
def test_rb_tour_s_folder_is_in_the_ledger_and_goes_only_as_the_tour_left_it(tmp_path, touched):
    """rb --help sends a newcomer to rb tour, which makes ./rb-tour (a repository of its own) wherever they are: the
    ownership ledger keeps it, and rb remove takes it while every file is as the tour left it, and otherwise keeps it
    and says so."""
    rec = Repo(tmp_path)
    rec.git("init", "-q")
    (rec.root / "train.py").write_text("lr = 0.1\n", encoding="utf-8", newline="\n")
    rec.git("add", "-A")
    rec.git("-c", "commit.gpgsign=false", "commit", "-qm", "start")
    before = rec.files()
    r = rec.run("tour")
    assert r.returncode == 0 and (rec.root / "rb-tour" / ".rb" / "project.yaml").is_file(), r.stderr
    lines = [json.loads(line) for line in (rec.root / ".git" / "rb" / "owned.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    assert [(line["how"], line.get("kind"), line["command"]) for line in lines] == [("dir", None, "tour"), ("dir", "tour", "tour")]
    rec.rb("init", "INT8 ResNet-50 on ImageNet")
    if touched:
        (rec.root / "rb-tour" / "notes.txt").write_text("what I tried\n", encoding="utf-8")
    plan = _plan(rec)
    if touched:
        assert [k["why"] for k in plan["keep"] if k["path"] == "rb-tour/"] == [
            "rb tour's practice project, a repository of its own, changed since the tour made it: delete the folder yourself if you want it gone"]
    else:
        assert [i["why"] for i in plan["remove"] if i["path"] == "rb-tour/"] == ["rb tour's practice project, every file as the tour left it"]
    rec.rb("remove", "--yes")
    assert (rec.root / "rb-tour").is_dir() is touched
    if not touched:
        assert rec.files() == before


def test_a_record_outside_git_is_said_to_be_in_no_commit(tmp_path):
    rec = Repo(tmp_path)
    rec.rb("init", "INT8 ResNet-50 on ImageNet")
    rec.rb("experiment", "add", "int8 against fp32", "--id", "e1", "--baseline", "fp32", "--candidate", "int8")
    plan = _plan(rec)
    text = rec.run("remove", "--dry-run").stdout
    assert f"\n{plan['uncommitted']} files of the record are in no commit, since this folder is not in git. To keep a copy first, run: {plan['copy_first']}\n" in text


def test_a_checkout_at_the_home_folder_keeps_the_skill_every_project_reads(tmp_path):
    """A repository at the home folder (dotfiles): its .claude/skills/rabbit-brain/ is the skill rb skill add put there
    for every project, which rb remove lists and never removes."""
    rec = Repo(tmp_path)
    rec.env.update(HOME=str(rec.root), USERPROFILE=str(rec.root))
    rec.git("init", "-q")
    assert rec.run("skill", "add").returncode == 0
    skill = rec.root / ".claude" / "skills" / "rabbit-brain" / "SKILL.md"
    kept = skill.read_bytes()
    rec.rb("init", "dotfiles", "--yes")
    plan = _plan(rec)
    assert not any("skills" in i["path"] for i in plan["remove"]) and "~/.claude/skills/rabbit-brain/" in [k["path"] for k in plan["listed"]]
    rec.rb("remove", "--yes")
    assert skill.read_bytes() == kept


@pytest.mark.skipif(sys.platform == "win32", reason="a symbolic link takes a privilege there")
def test_a_claude_folder_linked_into_home_leaves_home_as_it_was(tmp_path):
    """.claude/ is a link to the home folder's .claude/, where the skill every project reads goes: a plain rb init writes
    nothing there; given the yes, it writes rb's skill there, which this checkout's ledger keeps as rb init's, and rb
    remove takes it out, naming where the link goes, so the home folder is as it was before rb either way."""
    rec = Repo(tmp_path)
    home = Path(rec.env["HOME"])
    (home / ".claude").mkdir()
    rec.git("init", "-q")
    (rec.root / ".claude").symlink_to(home / ".claude", target_is_directory=True)
    (rec.root / "train.py").write_text("lr = 0.1\n", encoding="utf-8", newline="\n")
    rec.git("add", "-A")
    rec.git("-c", "commit.gpgsign=false", "commit", "-qm", "start")
    at_home = lambda: sorted(p.relative_to(home).as_posix() for p in home.rglob("*"))   # noqa: E731
    before = at_home()
    rec.rb("init", "INT8 ResNet-50 on ImageNet")
    assert at_home() == before
    rec.rb("remove", "--yes")
    rec.rb("init", "INT8 ResNet-50 on ImageNet", "--yes")
    skill = home / ".claude" / "skills" / "rabbit-brain" / "SKILL.md"
    assert skill.is_file()
    plan = _plan(rec)
    [item] = [i for i in plan["remove"] if i["path"] == ".claude/skills/rabbit-brain/SKILL.md"]
    assert item["real_path"] == skill.resolve().as_posix() and "~/.claude/skills/rabbit-brain/" not in [k["path"] for k in plan["listed"]]
    assert ("\n  .claude/skills/rabbit-brain/SKILL.md (through a link, at ~/.claude/skills/rabbit-brain/SKILL.md, outside this repository): "
            "rb's skill for Claude Code, as rb wrote it\n") in rec.run("remove", "--dry-run").stdout
    rec.rb("remove", "--yes")
    assert at_home() == before


@pytest.mark.skipif(sys.platform == "win32", reason="a symbolic link takes a privilege there")
def test_a_link_to_a_file_not_there_yet_stays_and_the_file_rb_wrote_through_it_goes(tmp_path):
    """AGENTS.md and .mcp.json are committed links to files that are not there yet, one in the repository and one in a
    folder outside it: given the yes, rb init and rb skill add write rb's files where they point, and rb remove takes
    those files away and leaves the links as they were, so git has nothing to say and the folder outside is as it was."""
    rec = Repo(tmp_path)
    shared = Path(rec.env["HOME"]) / "shared"
    shared.mkdir()
    rec.git("init", "-q")
    (rec.root / "train.py").write_text("lr = 0.1\n", encoding="utf-8", newline="\n")
    (rec.root / ".claude").mkdir()
    (rec.root / "docs").mkdir()
    (rec.root / "docs" / "README.md").write_text("# Docs\n", encoding="utf-8", newline="\n")
    (rec.root / "AGENTS.md").symlink_to(Path("docs") / "AGENTS.md")
    (rec.root / ".mcp.json").symlink_to(shared / "mcp.json")
    rec.git("add", "-A")
    rec.git("-c", "commit.gpgsign=false", "commit", "-qm", "start")
    rec.rb("init", "INT8 ResNet-50 on ImageNet", "--yes")
    rec.rb("skill", "add", "--agents-md", "--yes")
    assert (rec.root / "docs" / "AGENTS.md").is_file() and (shared / "mcp.json").is_file()
    plan = _plan(rec)
    for name in ("AGENTS.md", ".mcp.json"):
        [item] = [i for i in plan["remove"] if i["path"] == name]
        assert item["why"] == "made by rb, and as rb wrote it; the link stays", item
    rec.rb("remove", "--yes")
    assert os.readlink(rec.root / "AGENTS.md") == str(Path("docs") / "AGENTS.md") and os.readlink(rec.root / ".mcp.json") == str(shared / "mcp.json")
    assert not (rec.root / "docs" / "AGENTS.md").exists() and list(shared.iterdir()) == []
    assert rec.git("status", "--porcelain") == ""


@pytest.mark.skipif(sys.platform == "win32", reason="a symbolic link takes a privilege there")
def test_a_record_folder_that_is_a_link_stays_with_its_folder_and_rb_s_files_go(tmp_path):
    """.rb/ is a link to a folder outside the repository, made before rb init: the plan names where it goes, rb remove
    takes rb's files out of that folder, leaves the link and the folder and says so, and run again finds nothing of
    rb's to remove."""
    rec = Repo(tmp_path)
    shared = tmp_path / "shared-record"
    shared.mkdir()
    rec.git("init", "-q")
    (rec.root / "train.py").write_text("lr = 0.1\n", encoding="utf-8", newline="\n")
    rec.git("add", "-A")
    rec.git("-c", "commit.gpgsign=false", "commit", "-qm", "start")
    (rec.root / ".rb").symlink_to(Path("..") / "shared-record", target_is_directory=True)
    rec.rb("init", "INT8 ResNet-50 on ImageNet", "--no-agents")
    rec.rb("question", "add", "does int8 hold top1")
    assert (shared / "project.yaml").is_file()
    [item] = [i for i in _plan(rec)["remove"] if i["path"] == ".rb/"]
    assert item["real_path"] == shared.resolve().as_posix() and item["why"].endswith("; the link and the folder it points to stay")
    assert f"\n  .rb/ (a link to {shared.resolve().as_posix()}, outside this repository): the record (" in rec.run("remove", "--dry-run").stdout
    done = rec.run("remove", "--yes")
    said = "Removed rb's files in .rb/ (the link and the folder it points to stay), .git/rb/. Nothing else was changed."   # rb init's look
    assert done.returncode == 0 and said in done.stdout, done.stdout
    assert (rec.root / ".rb").is_symlink() and shared.is_dir() and list(shared.iterdir()) == []
    again = rec.run("remove", "--yes")
    assert again.returncode == 0 and "Nothing of rb's to remove here." in again.stdout, again.stdout


@pytest.mark.skipif(sys.platform == "win32", reason="a symbolic link takes a privilege there")
def test_the_skill_init_s_advice_writes_through_a_claude_folder_linked_into_home_is_taken_back(tmp_path):
    """.claude/ is a link to the home folder's .claude/: rb init writes nothing there and names the command that does,
    rb skill add --claude --project --yes; what that writes is this checkout's own, so rb remove takes it out and the
    home folder is as it was before rb. One rb skill add --claude writes for every project stays listed."""
    rec = Repo(tmp_path)
    home = Path(rec.env["HOME"])
    (home / ".claude").mkdir()
    rec.git("init", "-q")
    (rec.root / ".claude").symlink_to(home / ".claude", target_is_directory=True)
    (rec.root / "train.py").write_text("lr = 0.1\n", encoding="utf-8", newline="\n")
    rec.git("add", "-A")
    rec.git("-c", "commit.gpgsign=false", "commit", "-qm", "start")
    at_home = lambda: sorted(p.relative_to(home).as_posix() for p in home.rglob("*"))   # noqa: E731
    before = at_home()
    assert "rb skill add --claude --project --yes writes it" in rec.run("init", "INT8 ResNet-50 on ImageNet").stdout
    rec.rb("skill", "add", "--claude", "--project", "--yes")
    assert (home / ".claude" / "skills" / "rabbit-brain" / "SKILL.md").is_file()
    plan = _plan(rec)
    assert ".claude/skills/rabbit-brain/SKILL.md" in [i["path"] for i in plan["remove"]] and "~/.claude/skills/rabbit-brain/" not in [k["path"] for k in plan["listed"]]
    rec.rb("remove", "--yes")
    assert at_home() == before
    rec.rb("init", "INT8 ResNet-50 on ImageNet")
    rec.rb("skill", "add", "--claude", "--yes")                       # for every project, from the home folder
    plan = _plan(rec)
    assert ".claude/skills/rabbit-brain/SKILL.md" not in [i["path"] for i in plan["remove"]] and "~/.claude/skills/rabbit-brain/" in [k["path"] for k in plan["listed"]]


@pytest.mark.parametrize("committed", [True, False])
def test_a_file_staged_in_part_is_named_and_no_command_takes_the_user_s_staging_out(tmp_path, committed):
    """AGENTS.md, new since the last commit (or with no commit yet), was staged before rb; rb added its section, git add
    -A staged it with rb's section, and a line added since is not staged: the index holds rb's part with the user's own
    staging, which no one command takes apart. The plan names the file apart, with no command, and the command it
    prints for rb's own files leaves it alone and succeeds: the file stays staged, never unstaged."""
    rec = Repo(tmp_path)
    rec.git("init", "-q")
    (rec.root / "train.py").write_text("lr = 0.1\n", encoding="utf-8", newline="\n")
    if committed:
        rec.git("add", "train.py")
        rec.git("-c", "commit.gpgsign=false", "commit", "-qm", "start")
    (rec.root / "AGENTS.md").write_bytes(HOUSE)
    rec.git("add", "AGENTS.md")
    rec.rb("init", "INT8 ResNet-50 on ImageNet", "--yes")
    rec.git("add", "-A")
    with (rec.root / "AGENTS.md").open("ab") as f:
        f.write(b"\nRun the linters too.\n")
    plan = _plan(rec)
    assert plan["staged_in_part"] == ["AGENTS.md"] and "AGENTS.md" in plan["staged"]
    assert plan["unstage"] == ("git restore --staged -- .rb" if committed else "git rm -r -q --cached -- .rb")
    said = ("AGENTS.md is staged in part, rb's part with other changes, and no one command takes out only rb's: after rb remove, "
            "stage it again as you want it, or your next commit adds rb's part to history.")
    assert said in rec.run("remove", "--dry-run").stdout
    rec.rb("remove", "--yes")
    subprocess.run(plan["unstage"], shell=True, cwd=rec.root, env=rec.env, check=True)
    status = {line[3:]: line[:2] for line in rec.git("status", "--porcelain").splitlines()}
    assert status["AGENTS.md"] == "AM" and not any(p.startswith(".rb") for p in status)


@pytest.mark.parametrize("before", ["committed", "made", "no-commit"])
def test_a_file_staged_with_rb_s_part_alone_then_changed_is_unstaged_by_the_one_command(tmp_path, before):
    """AGENTS.md and .mcp.json, committed before rb (or .mcp.json alone, made by rb init, with a commit or before a
    first one), are staged with rb's part by git add -A, then changed again and not staged. The index holds what the
    last commit holds with rb's part alone, so the command that unstages rb's files takes out only rb's part: the plan
    names them in it, not as staged in part, and followed, the index is as before rb and the change made since stays."""
    rec = Repo(tmp_path)
    rec.git("init", "-q")
    (rec.root / "train.py").write_text("lr = 0.1\n", encoding="utf-8", newline="\n")
    (rec.root / ".claude").mkdir()
    there = before == "committed"
    if there:
        (rec.root / "AGENTS.md").write_bytes(HOUSE)
        (rec.root / ".mcp.json").write_bytes(FOUR)
    if before != "no-commit":
        rec.git("add", "-A")
        rec.git("-c", "commit.gpgsign=false", "commit", "-qm", "start")
    rec.rb("init", "INT8 ResNet-50 on ImageNet", "--yes")
    rec.git("add", "-A")
    line = b"\nRun the linters too.\n"
    if there:
        with (rec.root / "AGENTS.md").open("ab") as f:
            f.write(line)
    mcp = json.loads((rec.root / ".mcp.json").read_text(encoding="utf-8"))
    mcp["mcpServers"]["lint"] = {"command": "lint-server"}
    (rec.root / ".mcp.json").write_text(json.dumps(mcp, indent=4) + "\n", encoding="utf-8", newline="\n")
    plan = _plan(rec)
    mine = ".mcp.json .rb AGENTS.md" if there else ".mcp.json .rb"
    how = "git rm -r -q -f --cached" if before == "no-commit" else "git restore --staged"
    assert plan["staged_in_part"] == [] and plan["unstage"] == f"{how} -- .claude/skills/rabbit-brain/SKILL.md {mine}"
    assert "staged in part" not in rec.run("remove", "--dry-run").stdout
    rec.rb("remove", "--yes")
    subprocess.run(plan["unstage"], shell=True, cwd=rec.root, env=rec.env, check=True)
    status = {line[3:]: line[:2] for line in rec.git("status", "--porcelain").splitlines()}
    left = {"lint": {"command": "lint-server"}, **({"other": {"command": "other-server"}} if there else {})}
    assert json.loads((rec.root / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"] == left
    if there:
        assert status == {"AGENTS.md": " M", ".mcp.json": " M"} and (rec.root / "AGENTS.md").read_bytes() == HOUSE + line
    elif before == "made":
        assert status == {".mcp.json": "??"}
    else:
        assert status == {"train.py": "A ", ".mcp.json": "??"}           # the user's own staging stays
    if before != "no-commit":
        assert rec.git("diff", "--cached", "--name-only") == ""


def test_rb_s_files_git_add_n_put_in_the_index_are_named_with_the_command(tmp_path):
    """git add -N after rb init puts rb's files in the index with no content (intent to add), which git diff --cached
    leaves out: the plan names them with the command that takes them out, and followed, git status is as before rb."""
    rec = Repo(tmp_path)
    rec.git("init", "-q")
    (rec.root / "train.py").write_text("lr = 0.1\n", encoding="utf-8", newline="\n")
    rec.git("add", "train.py")
    rec.git("-c", "commit.gpgsign=false", "commit", "-qm", "start")
    rec.rb("init", "INT8 ResNet-50 on ImageNet", "--no-agents")
    rec.git("add", "-N", "--", ".")
    plan = _plan(rec)
    assert plan["unstage"] == "git restore --staged -- .rb" and plan["staged"] and all(p.startswith(".rb/") for p in plan["staged"])
    rec.rb("remove", "--yes")
    subprocess.run(plan["unstage"], shell=True, cwd=rec.root, env=rec.env, check=True)
    assert rec.git("status", "--porcelain") == "" and rec.git("ls-files") == "train.py\n"
