"""The agents' session hooks: rb init puts a hook in each agent's project file that runs `rb journal scan --hook <agent>`
as a session starts and as each turn ends, so rb looks at the repository whether the agent reads AGENTS.md, the skill
and the MCP server or not. What the look finds is observation (the journal), never the record. The hook says nothing
and exits 0 whatever happens; rb remove takes out exactly what rb added and leaves every other hook.

Every repository here is synthetic: the INT8 ResNet-50 work on ImageNet, recorded by human:ana at their own terminal.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from rbtest import AGENT_VARS, command, environment

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="the journal and the ledger live in git's folder")

# a team's Claude Code settings: their permissions and a hook of their own, in four spaces
THEIRS = (
    '{\n'
    '    "permissions": {"allow": ["Bash(python eval.py *)"]},\n'
    '    "hooks": {\n'
    '        "PreToolUse": [\n'
    '            {"matcher": "Bash", "hooks": [{"type": "command", "command": "python tools/check_cmd.py"}]}\n'
    '        ]\n'
    '    }\n'
    '}\n'
)


class Repo:
    """A repository at base/int8, rb run there as human:ana with no terminal, HOME of the test's own."""

    def __init__(self, base: Path) -> None:
        self.root = base / "int8"
        self.home = base / "home"
        self.root.mkdir()
        self.home.mkdir()
        cfg = base / "gitconfig"
        cfg.write_text("[user]\n\temail = ana@example.org\n\tname = ana\n[init]\n\tdefaultBranch = main\n", encoding="utf-8")
        self.env = {k: v for k, v in environment().items() if k not in AGENT_VARS}
        self.env.update(GIT_CONFIG_GLOBAL=str(cfg), GIT_CONFIG_NOSYSTEM="1", RB_TELEMETRY="0", DO_NOT_TRACK="1",
                        HOME=str(self.home), USERPROFILE=str(self.home), RB_AUTO_UPDATE="0")
        self.git("init", "-q")

    def git(self, *args: str) -> str:
        r = subprocess.run(["git", *args], cwd=self.root, env=self.env, capture_output=True, text=True)
        assert r.returncode == 0, (args, r.stderr)
        return r.stdout

    def run(self, *argv: str, env: dict | None = None) -> subprocess.CompletedProcess:
        return subprocess.run([*command(), *argv], cwd=self.root, env={**self.env, **(env or {})}, capture_output=True,
                              encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL)

    def rb(self, *argv: str) -> dict:
        r = self.run(*argv, "--json")
        out = json.loads(r.stdout)
        assert out["ok"], (argv, out.get("errors"), r.stderr)
        return out["data"]

    def journal(self) -> list:
        common = Path(self.git("rev-parse", "--path-format=absolute", "--git-common-dir").strip())
        return [json.loads(line) for p in sorted((common / "rb" / "journal").glob("*/journal.jsonl"))
                for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]


@pytest.fixture
def repo(tmp_path):
    return Repo(tmp_path)


def items(path: Path, event: str) -> list:
    return json.loads(path.read_text(encoding="utf-8"))["hooks"][event]


def rb_commands(hook: dict) -> list:
    """The commands of rb's handlers in a hook file's lists (Claude Code's and Codex's in a matcher group)."""
    return [h["command"] for group in hook for h in group.get("hooks", [group])]


def test_init_puts_a_session_hook_in_each_agent_s_project_file(repo):
    """Claude Code, Codex and Cursor each get the hook in their project file: on the event that starts a session and the
    one that ends a turn, with a time limit; init says each file and what rb added, as it says the skill and .mcp.json."""
    for d in (".claude", ".codex", ".cursor"):
        (repo.root / d).mkdir()
    data = repo.rb("init", "INT8 ResNet-50")
    said = "\n".join(data["agents"]["said"])
    claude, codex, cursor = (repo.root / ".claude/settings.json", repo.root / ".codex/hooks.json", repo.root / ".cursor/hooks.json")
    for path, start, stop, name in ((claude, "SessionStart", "Stop", "claude-code"), (codex, "SessionStart", "Stop", "codex"),
                                    (cursor, "sessionStart", "stop", "cursor")):
        rel = path.relative_to(repo.root).as_posix()
        assert f"rb's session hook in a new {rel} ({start} and {stop} run rb journal scan" in said, said
        assert rel in [Path(w).relative_to(repo.root).as_posix() for w in data["agents"]["written"]]
        for event in (start, stop):
            (command,) = rb_commands(items(path, event))
            assert f"rb journal scan --hook {name}" in command
        handler = items(path, start)[0]
        handler = handler.get("hooks", [handler])[0]
        assert handler["timeout"] == 10
    assert json.loads(cursor.read_text(encoding="utf-8"))["version"] == 1
    assert items(codex, "Stop")[0]["hooks"][0]["commandWindows"] == "rb journal scan --hook codex"
    assert "Codex runs it once it is trusted in /hooks" in said
    # no writer, no em dash in what rb says
    assert "—" not in said


def test_a_second_init_or_skill_add_adds_nothing(repo):
    """Written twice, the hook is there once: rb skill add (and rb reconcile --apply's install) find it and write nothing."""
    (repo.root / ".claude").mkdir()
    (repo.root / ".claude/settings.json").write_text(THEIRS, encoding="utf-8")
    repo.rb("init", "INT8 ResNet-50", "--yes")
    settings = repo.root / ".claude/settings.json"
    once = settings.read_bytes()
    data = repo.rb("skill", "add", "--claude", "--project", "--yes")
    assert settings.read_bytes() == once
    assert all(not w.endswith("settings.json") for w in data["written"])
    r = repo.run("skill", "add", "--claude", "--project", "--yes")
    assert ".claude/settings.json holds rb's session hook already." in r.stdout
    assert len(items(settings, "Stop")) == 1 and len(items(settings, "SessionStart")) == 1


def test_a_settings_file_with_other_hooks_keeps_every_byte_and_rb_remove_takes_only_rb_s(repo):
    """A team's settings file: off a terminal rb leaves it and says the command; with --yes rb's handlers go in beside
    theirs, every byte of theirs kept. rb remove puts the file back byte for byte while it is as rb left it, and once
    the team has added a hook of their own after rb's, takes out rb's alone and keeps theirs."""
    (repo.root / ".claude").mkdir()
    settings = repo.root / ".claude/settings.json"
    settings.write_text(THEIRS, encoding="utf-8")
    data = repo.rb("init", "INT8 ResNet-50")
    assert settings.read_text(encoding="utf-8") == THEIRS
    left = {x["path"]: x for x in data["agents"]["left"]}[".claude/settings.json"]
    assert left["command"] == "rb skill add --claude --project --yes"
    assert '"SessionStart"' in left["adds"] and '"Stop"' in left["adds"]
    repo.rb("skill", "add", "--claude", "--project", "--yes")
    after = settings.read_text(encoding="utf-8")
    assert after.startswith(THEIRS[:THEIRS.index("        ]\n    }\n}")])
    cfg = json.loads(after)
    assert cfg["permissions"] == {"allow": ["Bash(python eval.py *)"]}
    assert cfg["hooks"]["PreToolUse"] == json.loads(THEIRS)["hooks"]["PreToolUse"]
    plan = repo.rb("remove", "--dry-run")
    (hook,) = [x for x in plan["remove"] if x["path"] == ".claude/settings.json"]
    assert "byte for byte" in hook["why"]
    # the team adds a hook of their own after rb's
    cfg["hooks"]["Stop"].append({"hooks": [{"type": "command", "command": "python tools/notify.py"}]})
    settings.write_text(json.dumps(cfg, indent=4) + "\n", encoding="utf-8")
    repo.rb("remove", "--yes")
    back = json.loads(settings.read_text(encoding="utf-8"))
    assert back["hooks"]["Stop"] == [{"hooks": [{"type": "command", "command": "python tools/notify.py"}]}]
    assert "SessionStart" not in back["hooks"]            # rb made that list, and nothing else is in it
    assert back["hooks"]["PreToolUse"] == json.loads(THEIRS)["hooks"]["PreToolUse"] and back["permissions"] == cfg["permissions"]


def test_rb_remove_takes_a_hook_file_rb_made_and_its_folder(repo):
    """A hook file rb made, as rb wrote it, goes, with the folder rb made for it; a .codex/ that was there stays."""
    (repo.root / ".codex").mkdir()
    (repo.root / ".cursor").mkdir()
    repo.rb("init", "INT8 ResNet-50")
    assert (repo.root / ".codex/hooks.json").is_file() and (repo.root / ".cursor/hooks.json").is_file()
    repo.rb("remove", "--yes")
    assert not (repo.root / ".codex/hooks.json").exists() and not (repo.root / ".cursor/hooks.json").exists()
    assert (repo.root / ".codex").is_dir()


def test_a_hook_rb_did_not_add_here_stays(repo):
    """rb's handler in a settings file the ledger has no line for (a teammate's commit) is kept, and said."""
    (repo.root / ".claude").mkdir()
    repo.rb("init", "INT8 ResNet-50", "--no-agents")
    (repo.root.parent / "other").mkdir()
    other = Repo(repo.root.parent / "other")
    (other.root / ".claude").mkdir()
    other.rb("init", "INT8 ResNet-50")
    shutil.copy(other.root / ".claude/settings.json", repo.root / ".claude/settings.json")
    plan = repo.rb("remove", "--dry-run")
    (kept,) = [x for x in plan["keep"] if x["path"] == ".claude/settings.json"]
    assert "rb did not add it here, so it stays" in kept["why"]


def sh_command(repo: Repo) -> str:
    return items(repo.root / ".claude/settings.json", "Stop")[0]["hooks"][0]["command"]


@pytest.mark.skipif(sys.platform == "win32", reason="the POSIX shell form; Windows takes the plain form below")
def test_the_hook_exits_0_and_says_nothing_whatever_happens(repo, tmp_path):
    """No rb anywhere, an rb that fails, rb outside a record, a record rb cannot read: the hook exits 0 and prints
    nothing, on standard output (which an agent reads as context) or standard error."""
    (repo.root / ".claude").mkdir()
    repo.rb("init", "INT8 ResNet-50")
    cmd = sh_command(repo)
    bare = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path / "nohome"), "CLAUDE_PROJECT_DIR": str(repo.root)}
    r = subprocess.run(["sh", "-c", cmd], cwd=repo.root, env=bare, capture_output=True, text=True)
    assert (r.returncode, r.stdout, r.stderr) == (0, "", "")
    failing = tmp_path / "failing"
    failing.mkdir()
    # an older rb with no --hook, or one that fails: whatever it prints, on either stream, stays out of the agent's view
    (failing / "rb").write_text("#!/bin/sh\necho 'usage: rb ...'\necho 'rb: something went wrong' >&2\nexit 3\n", encoding="utf-8")
    (failing / "rb").chmod(0o755)
    r = subprocess.run(["sh", "-c", cmd], cwd=repo.root, env={**bare, "PATH": f"{failing}:/usr/bin:/bin"}, capture_output=True, text=True)
    assert (r.returncode, r.stdout, r.stderr) == (0, "", "")
    # Cursor's form, in a POSIX shell with no rb: it exits 0 too
    (repo.root / ".cursor").mkdir()
    repo.rb("skill", "add", "--cursor")
    r = subprocess.run(["sh", "-c", items(repo.root / ".cursor/hooks.json", "stop")[0]["command"]], cwd=repo.root, env=bare, capture_output=True, text=True)
    assert (r.returncode, r.stdout) == (0, "")
    # rb itself, with --hook: outside a record, with a name that is not one, with a record it cannot read
    for argv, cwd in ((["--hook", "claude-code"], tmp_path), (["--hook", "Claude Code; rm -rf /"], repo.root), (["--hook", "claude-code", "--json"], repo.root)):
        r = subprocess.run([*command(), "journal", "scan", *argv], cwd=cwd, env=repo.env, capture_output=True, text=True)
        assert (r.returncode, r.stdout, r.stderr) == (0, "", ""), argv
    log = repo.root / ".rb" / "log.jsonl"
    log.write_text("not json\n", encoding="utf-8")
    r = repo.run("journal", "scan", "--hook", "claude-code")
    assert (r.returncode, r.stdout, r.stderr) == (0, "", "")


@pytest.mark.skipif(sys.platform == "win32", reason="the POSIX shell form")
def test_the_hook_finds_rb_where_uv_tool_puts_it_under_a_home_with_a_space(repo, tmp_path):
    """rb not on the agent's PATH (uv tool and pipx put it in ~/.local/bin, which a shell may not have on its PATH): the
    hook finds it there, under a home whose name has a space, every path quoted."""
    (repo.root / ".claude").mkdir()
    repo.rb("init", "INT8 ResNet-50")
    home = tmp_path / "Ana Lopez"
    (home / ".local" / "bin").mkdir(parents=True)
    exe = shutil.which(command()[0]) or command()[0]
    if len(command()) > 1:
        pytest.skip("rb runs through an interpreter here")
    os.symlink(exe, home / ".local" / "bin" / "rb")
    (repo.root / "out" / "seed1").mkdir(parents=True)
    (repo.root / "out" / "seed1" / "metrics.json").write_text('{"int8": {"top1": 75.4}}\n', encoding="utf-8")
    env = {k: v for k, v in repo.env.items() if k not in ("PATH",)}
    env.update(PATH="/usr/bin:/bin", HOME=str(home))
    r = subprocess.run(["sh", "-c", sh_command(repo)], cwd=repo.root, env=env, capture_output=True, text=True)
    assert (r.returncode, r.stdout, r.stderr) == (0, "", "")
    seen = [e for e in repo.journal() if e.get("path") == "out/seed1/metrics.json"]
    assert seen and seen[0]["by"] == "agent:claude-code"


def test_the_windows_forms_have_nothing_to_quote(repo):
    """Where the hook may run in PowerShell or cmd (Codex's commandWindows, Cursor everywhere), it is one plain word
    list, the same in every shell; the POSIX form quotes every path it builds from a variable."""
    for d in (".claude", ".codex", ".cursor"):
        (repo.root / d).mkdir()
    repo.rb("init", "INT8 ResNet-50")
    plain = [items(repo.root / ".codex/hooks.json", "Stop")[0]["hooks"][0]["commandWindows"],
             items(repo.root / ".cursor/hooks.json", "stop")[0]["command"]]
    # Cursor's runs in PowerShell on Windows and a POSIX shell elsewhere: `; exit 0` reads the same in both
    assert plain == ["rb journal scan --hook codex", "rb journal scan --hook cursor; exit 0"]
    for p in plain:
        assert all(ch.isalnum() or ch in " -;" for ch in p), p
    for cmd in (sh_command(repo), items(repo.root / ".codex/hooks.json", "Stop")[0]["hooks"][0]["command"]):
        for var in ("$HOME", "$CLAUDE_PROJECT_DIR", "$(git"):
            if var in cmd:
                assert f'"{var}' in cmd, (var, cmd)


def test_the_look_is_the_agent_s_observation_and_tells_the_session_nothing(repo):
    """The hook's look goes in the journal as the agent's (never the user's, whatever RB_ACTOR says), writes nothing to
    the record, and leaves what the session has not been told for its own next command."""
    (repo.root / ".claude").mkdir()
    repo.rb("init", "INT8 ResNet-50")
    log = (repo.root / ".rb" / "log.jsonl").read_bytes()
    session = {"CLAUDECODE": "1", "CLAUDE_CODE_SESSION_ID": "sess-hooks-4b1d"}
    # the session's first command: what is outstanding is told then, and remembered as told
    repo.run("status", env=session)
    (repo.root / "out" / "seed1").mkdir(parents=True)
    (repo.root / "out" / "seed1" / "metrics.json").write_text('{"int8": {"top1": 75.4}}\n', encoding="utf-8")
    # someone else writes to the record: a fact the session has not been told
    repo.rb("note", "add", "the held-out set is ImageNet's validation split")
    r = repo.run("journal", "scan", "--hook", "claude-code", env={**session, "RB_ACTOR": "human:ana"})
    assert (r.returncode, r.stdout, r.stderr) == (0, "", "")
    seen = [e for e in repo.journal() if e.get("path") == "out/seed1/metrics.json"]
    assert [e["by"] for e in seen] == ["agent:claude-code"]
    assert (repo.root / ".rb" / "log.jsonl").read_bytes().startswith(log)
    lines = (repo.root / ".rb" / "log.jsonl").read_text(encoding="utf-8").splitlines()
    assert not any(json.loads(x).get("op") == "journal" for x in lines)
    told = json.loads(repo.run("log", "--json", env=session).stdout)["rb_delta"]
    assert any(f["kind"] == "write" and "held-out set" in f["text"] for f in told), told


def test_init_dry_run_says_what_rb_adds_to_a_settings_file_that_was_there(repo):
    """rb init --dry-run --yes names the settings file it would change and how: rb's hook under its hooks, nothing else."""
    (repo.root / ".claude").mkdir()
    (repo.root / ".claude/settings.json").write_text(THEIRS, encoding="utf-8")
    data = repo.rb("init", "INT8 ResNet-50", "--dry-run", "--yes")
    (entry,) = [p for p in data["plan"] if p["path"] == ".claude/settings.json"]
    assert (entry["how"], entry["what"]) == ("change", "rb's session hook added under its hooks; nothing else in it changes")
    assert (repo.root / ".claude/settings.json").read_text(encoding="utf-8") == THEIRS


def test_a_repository_holding_only_what_rb_installs_has_no_work_before_the_record(repo):
    """The hook files rb init writes are no work: committed with the record, the skill and .mcp.json, they leave the
    repository with no history, so rb asks for no start protocol."""
    for d in (".claude", ".codex", ".cursor"):
        (repo.root / d).mkdir()
    repo.rb("init", "INT8 ResNet-50")
    repo.git("add", "-A")
    repo.git("-c", "commit.gpgsign=false", "commit", "-qm", "rb init")
    data = repo.rb("status")
    codes = [i["code"] for i in data["open"]]
    assert "no_question" not in codes, codes
    # a commit of anything else is work, and asks for the start protocol: the check reads commits
    (repo.root / "train.py").write_text("lr = 0.1\n", encoding="utf-8")
    repo.git("add", "-A")
    repo.git("-c", "commit.gpgsign=false", "commit", "-qm", "the training script")
    assert "no_question" in [i["code"] for i in repo.rb("status")["open"]]


@pytest.mark.skipif(sys.platform == "win32", reason="the lock is held here with fcntl")
def test_the_hook_waits_on_no_one(repo):
    """While another rb holds the record's lock, the hook's look is left to the next one: it returns at once."""
    import fcntl
    import time
    repo.rb("init", "INT8 ResNet-50", "--no-agents")
    (repo.root / "out" / "seed1").mkdir(parents=True)
    (repo.root / "out" / "seed1" / "metrics.json").write_text('{"int8": {"top1": 75.4}}\n', encoding="utf-8")
    with open(repo.root / ".rb" / ".lock", "a+") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        start = time.monotonic()
        r = subprocess.run([*command(), "journal", "scan", "--hook", "claude-code"], cwd=repo.root, env=repo.env, capture_output=True, text=True, timeout=30)
        took = time.monotonic() - start
    assert (r.returncode, r.stdout, r.stderr) == (0, "", "") and took < 5, took
    assert not [e for e in repo.journal() if e.get("path") == "out/seed1/metrics.json"]


def test_rb_remove_leaves_rb_s_handler_that_was_there_before_rb(repo):
    """A settings file holding rb's handler under SessionStart already (a teammate's commit of rb's): rb adds it to Stop
    alone, and rb remove, once the file changed since, takes out that one and leaves the SessionStart one, which was
    not rb's to take."""
    other_base = repo.root.parent / "other"
    other_base.mkdir()
    other = Repo(other_base)
    (other.root / ".claude").mkdir()
    other.rb("init", "INT8 ResNet-50")
    theirs = json.loads((other.root / ".claude/settings.json").read_text(encoding="utf-8"))
    del theirs["hooks"]["Stop"]
    (repo.root / ".claude").mkdir()
    settings = repo.root / ".claude/settings.json"
    settings.write_text(json.dumps(theirs, indent=2) + "\n", encoding="utf-8")
    repo.rb("init", "INT8 ResNet-50", "--yes")
    assert len(items(settings, "SessionStart")) == 1 and len(items(settings, "Stop")) == 1
    cfg = json.loads(settings.read_text(encoding="utf-8"))
    cfg["permissions"] = {"allow": ["Bash(python eval.py *)"]}
    settings.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    repo.rb("remove", "--yes")
    back = json.loads(settings.read_text(encoding="utf-8"))
    assert back["hooks"] == theirs["hooks"] and back["permissions"] == cfg["permissions"]


def test_a_hook_added_again_keeps_what_rb_added_before(repo):
    """rb made the settings file; the user deleted rb's Stop handler, and rb skill add put it back. rb remove still knows
    both handlers as rb's: with the file changed since, it takes both out, and the file rb made goes once only rb's
    part was in it; with a key of the user's beside them, that key alone stays."""
    (repo.root / ".claude").mkdir()
    settings = repo.root / ".claude/settings.json"
    repo.rb("init", "INT8 ResNet-50")
    for theirs in (None, {"allow": ["Bash(python eval.py *)"]}):
        cfg = json.loads(settings.read_text(encoding="utf-8"))
        del cfg["hooks"]["Stop"]
        settings.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
        repo.rb("skill", "add", "--claude", "--project", "--yes")
        assert len(items(settings, "Stop")) == 1
        if theirs is None:
            # as rb left it: rb's handlers both go, and so does the file rb made
            repo.rb("remove", "--yes")
            assert not settings.exists()
            repo.rb("init", "INT8 ResNet-50")
            continue
        cfg = json.loads(settings.read_text(encoding="utf-8"))
        cfg["permissions"] = theirs
        settings.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
        repo.rb("remove", "--yes")
        assert json.loads(settings.read_text(encoding="utf-8")) == {"permissions": theirs}


# ---------------------------------------------------------------- an earlier rb's handler, brought up to date

# each agent's file: its folder and name, its two events, and a hook of the team's own in the agent's shape
AGENTS = {
    "claude": (".claude/settings.json", ("SessionStart", "Stop"), ["--claude", "--project"],
               {"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "python tools/check_cmd.py"}]}]}}),
    "codex": (".codex/hooks.json", ("SessionStart", "Stop"), ["--agents-md"],
              {"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "python tools/check_cmd.py"}]}]}}),
    "cursor": (".cursor/hooks.json", ("sessionStart", "stop"), ["--cursor"],
               {"version": 1, "hooks": {"beforeShellExecution": [{"command": "python tools/check_cmd.py"}]}}),
}


def earlier(command: str) -> str:
    """The command the first rb that wrote a session hook put there: each POSIX try quiet on stderr alone, and Cursor's
    with no `exit 0` after it."""
    return command.replace(" >/dev/null 2>&1", " 2>/dev/null").replace("; exit 0", "")


def to_earlier(path: Path) -> str:
    """The file with each of rb's handlers in the earlier form, by its own bytes; returns the file as it was."""
    text = path.read_text(encoding="utf-8")
    cmds = {c for e in json.loads(text)["hooks"].values() for c in rb_commands(e) if "journal scan --hook" in c}
    was = text
    for c in cmds:
        assert earlier(c) != c
        was = was.replace(json.dumps(c), json.dumps(earlier(c)))
    path.write_text(was, encoding="utf-8")
    return text


def as_an_earlier_rb_left_it(repo: Repo, path: Path) -> None:
    """The ledger's last line for the file says rb's write left it as it reads now, as the earlier rb's line said."""
    import hashlib
    common = Path(repo.git("rev-parse", "--path-format=absolute", "--git-common-dir").strip())
    ledger = common / "rb" / "owned.jsonl"
    lines = [json.loads(x) for x in ledger.read_text(encoding="utf-8").splitlines() if x.strip()]
    rel = path.relative_to(repo.root).as_posix()
    (last,) = [i for i, x in enumerate(lines) if str(x.get("path", "")).endswith(rel) and x.get("how") in ("hook_entry", "created")][-1:]
    lines[last]["sha256_after"] = hashlib.sha256(path.read_bytes()).hexdigest()
    ledger.write_text("".join(json.dumps(x) + "\n" for x in lines), encoding="utf-8")


def setup_agent(repo: Repo, agent: str, theirs: bool) -> tuple[Path, tuple, list]:
    rel, events, flags, own = AGENTS[agent]
    path = repo.root / rel
    path.parent.mkdir()
    if theirs:
        path.write_text(json.dumps(own, indent=4) + "\n", encoding="utf-8")
    return path, events, flags


@pytest.mark.parametrize("agent", sorted(AGENTS))
@pytest.mark.parametrize("theirs", [True, False], ids=["file-was-there", "file-rb-made"])
def test_an_upgrade_brings_rb_s_earlier_handler_up_to_date_in_place(repo, agent, theirs):
    """An earlier rb wrote its handler (in a file that was there, or one it made); this rb's skill add puts today's in
    its place, one handler under each event, and changes no other byte. The ledger reads it as rb's: rb remove takes it
    out, and the file goes back to what it was before any rb (or goes, where rb made it)."""
    path, events, flags = setup_agent(repo, agent, theirs)
    before = path.read_bytes() if theirs else None
    repo.rb("init", "INT8 ResNet-50", "--yes")
    today = to_earlier(path)
    as_an_earlier_rb_left_it(repo, path)
    r = repo.run("skill", "add", *flags, "--yes")
    assert r.returncode == 0, r.stderr
    assert path.read_text(encoding="utf-8") == today, "today's form, in place, every other byte as it was"
    for e in events:
        assert len(rb_commands(items(path, e))) == 1
    plan = repo.rb("remove", "--dry-run")
    assert any(x["path"] == path.relative_to(repo.root).as_posix() for x in plan["remove"]), plan
    repo.rb("remove", "--yes")
    if theirs:
        assert path.read_bytes() == before
    else:
        assert not path.exists()


@pytest.mark.parametrize("agent", sorted(AGENTS))
def test_init_brings_an_earlier_rb_s_handler_up_to_date_in_a_clone(repo, agent):
    """A clone holds a file an earlier rb wrote (the team's hook beside it) and no ledger line for it: rb's form is
    rb's, so this rb's init puts today's in its place, once each event. rb remove puts the file back as it came, byte
    for byte; once the file changed since, it puts the earlier handler back in place of rb's and keeps the change."""
    rel, events, flags, own = AGENTS[agent]
    other_base = repo.root.parent / "other"
    other_base.mkdir()
    other = Repo(other_base)
    path_o, _, _ = setup_agent(other, agent, True)
    other.rb("init", "INT8 ResNet-50", "--yes")
    today = to_earlier(path_o)
    came = path_o.read_bytes()
    path = repo.root / rel
    path.parent.mkdir()
    path.write_bytes(came)
    data = repo.rb("init", "INT8 ResNet-50", "--yes")
    assert path.read_text(encoding="utf-8") == today
    assert any(f"rb's session hook in {rel} brought up to date in place" in s for s in data["agents"]["said"]), data["agents"]["said"]
    plan = repo.rb("remove", "--dry-run")
    (it,) = [x for x in plan["remove"] if x["path"] == rel]
    assert "byte for byte" in it["why"]
    cfg = json.loads(path.read_text(encoding="utf-8"))
    cfg["model"] = "opus"
    path.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    repo.rb("remove", "--yes")
    back = json.loads(path.read_text(encoding="utf-8"))
    want = json.loads(came)
    want["model"] = "opus"
    assert back == want


@pytest.mark.parametrize("agent", sorted(AGENTS))
def test_rb_remove_after_an_upgrade_takes_the_earlier_rb_s_handler(repo, agent):
    """rb was upgraded and init not run again: the file holds the earlier rb's handler, changed since by the team. rb
    remove knows that form as rb's and takes it out, keeping the change."""
    path, events, flags = setup_agent(repo, agent, True)
    before = json.loads(path.read_text(encoding="utf-8"))
    repo.rb("init", "INT8 ResNet-50", "--yes")
    to_earlier(path)
    as_an_earlier_rb_left_it(repo, path)
    cfg = json.loads(path.read_text(encoding="utf-8"))
    cfg["model"] = "opus"
    path.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    repo.rb("remove", "--yes")
    assert json.loads(path.read_text(encoding="utf-8")) == {**before, "model": "opus"}


@pytest.mark.parametrize("agent", sorted(AGENTS))
def test_a_handler_edited_by_hand_stays_and_nothing_goes_beside_it(repo, agent):
    """rb's handler with its timeout changed by hand is the user's: init leaves the file byte for byte, adds nothing
    beside it, and says so in one line."""
    rel, events, flags, own = AGENTS[agent]
    other_base = repo.root.parent / "other"
    other_base.mkdir()
    other = Repo(other_base)
    path_o, _, _ = setup_agent(other, agent, True)
    other.rb("init", "INT8 ResNet-50", "--yes")
    edited = path_o.read_text(encoding="utf-8").replace('"timeout": 10', '"timeout": 30')
    path = repo.root / rel
    path.parent.mkdir()
    path.write_text(edited, encoding="utf-8")
    r = repo.run("init", "INT8 ResNet-50", "--yes")
    assert r.returncode == 0, r.stderr
    assert path.read_text(encoding="utf-8") == edited
    lines = [x for x in r.stdout.splitlines() if "edited by hand" in x]
    assert len(lines) == 1 and f"{rel}: the {events[0]} and {events[1]} handlers that run rb journal scan were edited by hand or written by a newer rb, so rb leaves them as they are and adds nothing beside them" in lines[0], r.stdout


def beside(path: Path, events: tuple, agent: str) -> None:
    """An earlier rb's handler added beside today's under each event (an rb that added its own beside an earlier one)."""
    cfg = json.loads(path.read_text(encoding="utf-8"))
    for e in events:
        old = json.loads(json.dumps(cfg["hooks"][e][0]))
        handler = old if agent == "cursor" else old["hooks"][0]
        handler["command"] = earlier(handler["command"])
        cfg["hooks"][e].append(old)
    path.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")


def rb_handlers(path: Path, events: tuple) -> int:
    cfg = json.loads(path.read_text(encoding="utf-8"))
    return sum("journal scan --hook" in c for e in events for c in rb_commands(cfg["hooks"].get(e, [])))


@pytest.mark.parametrize("agent", sorted(AGENTS))
def test_rb_remove_takes_every_form_of_rb_s_handler(repo, agent):
    """Today's handler and an earlier rb's both under each event, as rb left them, then a change of the team's: rb remove
    takes both, so no earlier rb's handler is left running."""
    path, events, flags = setup_agent(repo, agent, True)
    repo.rb("init", "INT8 ResNet-50", "--yes")
    beside(path, events, agent)
    as_an_earlier_rb_left_it(repo, path)
    cfg = json.loads(path.read_text(encoding="utf-8"))
    cfg["model"] = "opus"
    path.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    repo.rb("remove", "--yes")
    assert rb_handlers(path, events) == 0
    assert json.loads(path.read_text(encoding="utf-8"))["model"] == "opus"


@pytest.mark.parametrize("agent", sorted(AGENTS))
def test_a_clone_s_two_forms_of_rb_s_handler_come_back_after_rb_remove(repo, agent):
    """A clone's file holds today's handler and an earlier rb's under each event, and no ledger line: init takes the
    earlier one out, leaving one, and rb remove, after a change of the team's, puts it back beside today's."""
    rel = AGENTS[agent][0]
    other_base = repo.root.parent / "other"
    other_base.mkdir()
    other = Repo(other_base)
    path_o, events, _ = setup_agent(other, agent, True)
    other.rb("init", "INT8 ResNet-50", "--yes")
    beside(path_o, events, agent)
    path = repo.root / rel
    path.parent.mkdir()
    path.write_bytes(path_o.read_bytes())
    repo.rb("init", "INT8 ResNet-50", "--yes")
    assert rb_handlers(path, events) == 2
    cfg = json.loads(path.read_text(encoding="utf-8"))
    cfg["model"] = "opus"
    path.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    plan = repo.rb("remove", "--dry-run")
    assert any(x["path"] == rel for x in plan["remove"]), plan
    repo.rb("remove", "--yes")
    back = json.loads(path.read_text(encoding="utf-8"))
    want = json.loads(path_o.read_text(encoding="utf-8"))
    for e in events:
        assert sorted(map(json.dumps, back["hooks"][e])) == sorted(map(json.dumps, want["hooks"][e]))
    assert back["model"] == "opus"


def drop_rb_s(path: Path, events: tuple) -> None:
    """The user takes rb's own handler out of each event, leaving the rest of the list."""
    cfg = json.loads(path.read_text(encoding="utf-8"))
    for e in events:
        cfg["hooks"][e] = [g for g in cfg["hooks"][e] if not any("journal scan --hook" in c for c in rb_commands([g]))]
    path.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")


@pytest.mark.parametrize("agent", sorted(AGENTS))
def test_a_clone_s_cut_handler_comes_back_once_rb_s_own_is_gone(repo, agent):
    """A clone's file holds today's handler and an earlier rb's; init takes the earlier one out. The user then deletes
    rb's own: no form of rb's is left, and rb remove still lists the file and puts the earlier handler back."""
    rel = AGENTS[agent][0]
    other_base = repo.root.parent / "other"
    other_base.mkdir()
    other = Repo(other_base)
    path_o, events, _ = setup_agent(other, agent, True)
    other.rb("init", "INT8 ResNet-50", "--yes")
    beside(path_o, events, agent)
    path = repo.root / rel
    path.parent.mkdir()
    path.write_bytes(path_o.read_bytes())
    repo.rb("init", "INT8 ResNet-50", "--yes")
    drop_rb_s(path, events)
    assert rb_handlers(path, events) == 0
    plan = repo.rb("remove", "--dry-run")
    (it,) = [x for x in plan["remove"] if x["path"] == rel]
    assert "goes back" in it["why"], it
    repo.rb("remove", "--yes")
    back = json.loads(path.read_text(encoding="utf-8"))
    want = json.loads(path_o.read_text(encoding="utf-8"))
    for e in events:
        assert [earlier(c) for c in rb_commands(want["hooks"][e][:1])] == [c for c in rb_commands(back["hooks"][e])], back
        assert back["hooks"][e] == want["hooks"][e][1:]


def their_handler(agent: str) -> dict:
    return {"command": "python tools/log_session.py"} if agent == "cursor" else {"hooks": [{"type": "command", "command": "python tools/log_session.py"}]}


@pytest.mark.parametrize("agent", sorted(AGENTS))
def test_a_cut_handler_comes_back_at_its_place_in_the_team_s_order(repo, agent):
    """A clone's list holds an earlier rb's handler, then the team's own, then today's. init takes the earlier one out;
    after a change of the team's, rb remove puts it back first, so the list reads in the team's order again."""
    rel = AGENTS[agent][0]
    other_base = repo.root.parent / "other"
    other_base.mkdir()
    other = Repo(other_base)
    path_o, events, _ = setup_agent(other, agent, True)
    other.rb("init", "INT8 ResNet-50", "--yes")
    cfg = json.loads(path_o.read_text(encoding="utf-8"))
    for e in events:
        (today,) = cfg["hooks"][e]
        old = json.loads(json.dumps(today))
        handler = old if agent == "cursor" else old["hooks"][0]
        handler["command"] = earlier(handler["command"])
        cfg["hooks"][e] = [old, their_handler(agent), today]
    path = repo.root / rel
    path.parent.mkdir()
    path.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    repo.rb("init", "INT8 ResNet-50", "--yes")
    for e in events:
        assert items(path, e)[0] == their_handler(agent), "the earlier form is taken out"
    now = json.loads(path.read_text(encoding="utf-8"))
    now["model"] = "opus"
    path.write_text(json.dumps(now, indent=2) + "\n", encoding="utf-8")
    repo.rb("remove", "--yes")
    back = json.loads(path.read_text(encoding="utf-8"))
    assert back == {**cfg, "model": "opus"}


@pytest.mark.parametrize("agent", sorted(AGENTS))
def test_a_cut_handler_the_user_put_back_leaves_nothing_for_rb_remove(repo, agent):
    """init took a clone's earlier handler out; the user put it back and deleted rb's own. Nothing of rb's is left to
    take or give back, so rb remove does not list the file, and leaves it byte for byte."""
    rel = AGENTS[agent][0]
    other_base = repo.root.parent / "other"
    other_base.mkdir()
    other = Repo(other_base)
    path_o, events, _ = setup_agent(other, agent, True)
    other.rb("init", "INT8 ResNet-50", "--yes")
    beside(path_o, events, agent)
    path = repo.root / rel
    path.parent.mkdir()
    path.write_bytes(path_o.read_bytes())
    repo.rb("init", "INT8 ResNet-50", "--yes")
    cfg = json.loads(path_o.read_text(encoding="utf-8"))
    for e in events:
        cfg["hooks"][e] = cfg["hooks"][e][1:]
    path.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    was = path.read_bytes()
    plan = repo.rb("remove", "--dry-run")
    assert not any(x["path"] == rel for x in plan["remove"]), plan
    repo.rb("remove", "--yes")
    assert path.read_bytes() == was


@pytest.mark.parametrize("agent", sorted(AGENTS))
def test_a_cut_handler_the_user_put_back_beside_rb_s_comes_back_at_its_place(repo, agent):
    """init took a clone's earlier handler out; the user later left only their own handler, rb's skill add put rb's back,
    and the user added the earlier handler again at the end. rb remove takes rb's out and the earlier handler stays,
    back at its place before the user's, rather than going as a form of rb's."""
    rel, events, flags, _ = AGENTS[agent]
    other_base = repo.root.parent / "other"
    other_base.mkdir()
    other = Repo(other_base)
    path_o, _, _ = setup_agent(other, agent, True)
    other.rb("init", "INT8 ResNet-50", "--yes")
    cfg = json.loads(path_o.read_text(encoding="utf-8"))
    old = {}
    for e in events:
        (today,) = cfg["hooks"][e]
        old[e] = json.loads(json.dumps(today))
        handler = old[e] if agent == "cursor" else old[e]["hooks"][0]
        handler["command"] = earlier(handler["command"])
        cfg["hooks"][e] = [old[e], their_handler(agent), today]
    path = repo.root / rel
    path.parent.mkdir()
    path.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    repo.rb("init", "INT8 ResNet-50", "--yes")
    now = json.loads(path.read_text(encoding="utf-8"))
    for e in events:
        now["hooks"][e] = [their_handler(agent)]
    path.write_text(json.dumps(now, indent=2) + "\n", encoding="utf-8")
    r = repo.run("skill", "add", *flags, "--yes")
    assert r.returncode == 0, r.stderr
    now = json.loads(path.read_text(encoding="utf-8"))
    for e in events:
        now["hooks"][e].append(old[e])
    path.write_text(json.dumps(now, indent=2) + "\n", encoding="utf-8")
    plan = repo.rb("remove", "--dry-run")
    assert any(x["path"] == rel for x in plan["remove"]), plan
    repo.rb("remove", "--yes")
    back = json.loads(path.read_text(encoding="utf-8"))
    for e in events:
        assert back["hooks"][e] == [old[e], their_handler(agent)], back


def clone_with(repo: Repo, agent: str, order) -> tuple[Path, tuple, list, dict, dict]:
    """A clone's hook file whose lists hold, in `order`, the earlier rb's handler ("old"), the team's own ("theirs") and
    today's ("today"); returns its path, events, flags, the file as it came and the earlier handlers."""
    rel, events, flags, _ = AGENTS[agent]
    other_base = repo.root.parent / "other"
    other_base.mkdir()
    other = Repo(other_base)
    path_o, _, _ = setup_agent(other, agent, True)
    other.rb("init", "INT8 ResNet-50", "--yes")
    cfg = json.loads(path_o.read_text(encoding="utf-8"))
    old = {}
    for e in events:
        (today,) = cfg["hooks"][e]
        old[e] = json.loads(json.dumps(today))
        handler = old[e] if agent == "cursor" else old[e]["hooks"][0]
        handler["command"] = earlier(handler["command"])
        cfg["hooks"][e] = [{"old": old[e], "theirs": their_handler(agent), "today": today}[x] for x in order]
    path = repo.root / rel
    path.parent.mkdir()
    path.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    return path, events, flags, cfg, old


@pytest.mark.parametrize("agent", sorted(AGENTS))
def test_a_cut_handler_comes_back_after_rb_s_hook_was_added_again_as_it_left_it(repo, agent):
    """init took a clone's earlier handler out; the user deleted rb's own, and skill add put it back. rb remove right
    after, with the file as that write left it, still puts the earlier handler back at its place: that write's own
    `before` no longer held it."""
    path, events, flags, cfg, old = clone_with(repo, agent, ("old", "theirs", "today"))
    repo.rb("init", "INT8 ResNet-50", "--yes")
    drop_rb_s(path, events)
    r = repo.run("skill", "add", *flags, "--yes")
    assert r.returncode == 0, r.stderr
    repo.rb("remove", "--yes")
    back = json.loads(path.read_text(encoding="utf-8"))
    for e in events:
        assert back["hooks"][e] == [old[e], their_handler(agent)], back


@pytest.mark.parametrize("agent", sorted(AGENTS))
def test_the_handler_rb_s_took_the_place_of_comes_back_once_rb_s_own_is_gone(repo, agent):
    """init put today's handler in place of a clone's earlier one; the user deleted rb's own. rb remove lists the file
    and puts the earlier handler back where it was, before the team's."""
    path, events, flags, cfg, old = clone_with(repo, agent, ("old", "theirs"))
    repo.rb("init", "INT8 ResNet-50", "--yes")
    drop_rb_s(path, events)
    plan = repo.rb("remove", "--dry-run")
    rel = path.relative_to(repo.root).as_posix()
    assert any(x["path"] == rel for x in plan["remove"]), plan
    repo.rb("remove", "--yes")
    back = json.loads(path.read_text(encoding="utf-8"))
    for e in events:
        assert back["hooks"][e] == cfg["hooks"][e], back
