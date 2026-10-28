"""rb skill: the discipline as an installable agent skill. Printing shows the bundled SKILL.md;
`add` writes it where each agent looks, idempotently; the text itself is small and carries what the
product is built on (when to record, the user's words kept verbatim, findings computed not
asserted), in the record's own words, and holds no call back for anyone."""
import os
import re
import sys
from pathlib import Path

import pytest

from rabbit_brain.models import Envelope
from rbtest import as_the_user, at_home, cli_parser, main, rbyaml


@pytest.fixture
def rb(workdir, monkeypatch, tmp_path_factory, capsys):
    home = tmp_path_factory.mktemp("home")
    at_home(monkeypatch, home)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

    def call(*argv):
        code = main([*argv, "--json"])
        return code, Envelope.model_validate_json(capsys.readouterr().out)

    call.home = home
    call.cwd = workdir
    return call


def test_skill_prints_the_package_and_its_boundaries(rb):
    code, env = rb("skill")
    assert code == 0
    text = env.data["markdown"]
    assert text.startswith("---\n")
    assert "name: rabbit-brain" in text
    assert "rb context" in text and "rb docs" in text
    assert "Never edit `.rb/`" in text
    assert "--words" in text and "RB_HUB_TOKEN" in text
    for retired in ("E_HUMAN_ONLY", "rb grant", "rb approve", "rb publish", ".rabbitbrain"):
        assert retired not in text, retired     # rb holds no call for anyone, and never publishes
    assert len(text.splitlines()) <= 60         # small: everything else is in rb docs
    assert "\u2014" not in text  # the skill is written without em dashes, like everything else here
    # rb init copies the skill into AGENTS.md: a miss is said in rb's words, a hypothesis weakened, never "contradicts"
    assert "contradict" not in text.lower() and "weakens the warmup hypothesis" in text


def test_the_skill_speaks_the_records_words_and_names_only_real_commands(rb):
    code, env = rb("skill")
    assert code == 0
    text = env.data["markdown"]
    # the frontmatter reads as Rabbit Brain YAML, rb's own strict subset, so every agent's YAML parser reads it the same way
    front = rbyaml.loads(text.split("---\n")[1])
    assert front["name"] == "rabbit-brain" and front["description"].startswith("Rabbit Brain (rb)")
    for write in ("rb question add", "rb experiment add", "rb criterion add", "rb run add", "rb journal run", "rb analysis add",
                  "rb note add", "rb decide", "rb retract", "rb reconcile"):
        assert write in text
    for old in ("rb claim add", "rb evidence attach", "rb hypothesis add", "rb assumption add", "verdict"):
        assert old not in text
    groups: dict[str, set[str]] = {}
    for action in cli_parser.build_parser()._subparsers._group_actions:
        for name, sub in action.choices.items():
            subs = getattr(sub, "_subparsers", None)
            groups[name] = {n for a in subs._group_actions for n in a.choices} if subs else set()
    named = re.findall(r"`rb ([a-z][a-z-]*)(?: ([a-z][a-z-]*))?", text)
    assert len(named) > 20
    for top, second in named:
        assert top in groups, f"SKILL.md names `rb {top}`, which does not exist"
        assert not (groups[top] and second) or second in groups[top], f"SKILL.md names `rb {top} {second}`, which does not exist"


def test_add_defaults_to_claude_in_the_home_directory(rb):
    code, env = rb("skill", "add")
    assert code == 0
    dest = rb.home / ".claude" / "skills" / "rabbit-brain" / "SKILL.md"
    assert dest.as_posix() in env.data["written"]
    assert "name: rabbit-brain" in dest.read_text(encoding="utf-8")
    # rerunning overwrites in place rather than failing or duplicating
    code, env = rb("skill", "add", "--claude")
    assert code == 0 and dest.as_posix() in env.data["written"]


def test_add_project_writes_into_this_repository(rb):
    code, env = rb("skill", "add", "--claude", "--project")
    assert code == 0
    dest = rb.cwd / ".claude" / "skills" / "rabbit-brain" / "SKILL.md"
    assert dest.exists() and dest.as_posix() in env.data["written"]


def test_add_cursor_writes_a_rule_with_mdc_frontmatter(rb):
    code, env = rb("skill", "add", "--cursor")
    assert code == 0
    dest = rb.cwd / ".cursor" / "rules" / "rabbit-brain.mdc"
    text = dest.read_text(encoding="utf-8")
    assert text.startswith("---\ndescription: ")
    assert "alwaysApply: false" in text
    assert "name: rabbit-brain" not in text  # the Claude frontmatter is replaced, not nested
    assert "rb context" in text


def test_add_agents_md_is_a_marked_section_that_updates_in_place(rb):
    existing = "# My project\n\nHouse rules the team already wrote.\n"
    (rb.cwd / "AGENTS.md").write_bytes(existing.encode())
    # off a terminal, an AGENTS.md that was here is left as it is, with the one command that adds rb's section
    code, env = rb("skill", "add", "--agents-md")
    assert code == 0 and (rb.cwd / "AGENTS.md").read_bytes() == existing.encode() and env.data["written"] == []
    assert env.data["left"] == [{"path": "AGENTS.md", "adds": env.data["left"][0]["adds"], "command": "rb skill add --agents-md --yes"}]
    assert env.data["left"][0]["adds"].startswith("<!-- rabbit-brain skill: begin") and env.data["left"][0]["adds"].endswith("<!-- rabbit-brain skill: end -->")
    code, _ = rb("skill", "add", "--agents-md", "--yes")
    assert code == 0
    text = (rb.cwd / "AGENTS.md").read_text(encoding="utf-8")
    assert text.startswith("# My project")
    assert "House rules the team already wrote." in text
    assert text.count("rabbit-brain skill: begin") == 1
    # rerunning replaces the section rather than appending a second copy
    code, _ = rb("skill", "add", "--codex", "--yes")
    assert code == 0
    text = (rb.cwd / "AGENTS.md").read_text(encoding="utf-8")
    assert text.count("rabbit-brain skill: begin") == 1
    assert text.count("## When to record") == 1
    assert "House rules the team already wrote." in text


def test_add_can_write_every_target_in_one_call(rb):
    code, env = rb("skill", "add", "--claude", "--cursor", "--agents-md")
    assert code == 0
    assert len(env.data["written"]) == 3


def test_init_puts_rb_where_the_agents_found_here_look_and_keeps_what_is_there(rb, monkeypatch):
    import json
    as_the_user(monkeypatch)
    (rb.cwd / ".claude").mkdir()
    (rb.cwd / "AGENTS.md").write_text("# House rules\n\nKeep it tidy.\n", encoding="utf-8")
    (rb.cwd / ".mcp.json").write_text(json.dumps({"mcpServers": {"other": {"command": "other-server"}}}), encoding="utf-8")
    code, env = rb("init", "the study", "--yes")
    assert code == 0
    assert (rb.cwd / ".claude" / "skills" / "rabbit-brain" / "SKILL.md").is_file()
    servers = json.loads((rb.cwd / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]
    assert servers == {"other": {"command": "other-server"}, "rabbit-brain": {"command": "rb", "args": ["mcp"]}}
    agents = (rb.cwd / "AGENTS.md").read_text(encoding="utf-8")
    assert agents.startswith("# House rules") and agents.count("rabbit-brain skill: begin") == 1
    said = env.data["agents"]["said"]
    assert any(s.startswith("Claude Code (.claude/)") and "added to .mcp.json" in s for s in said)
    assert any("AGENTS.md" in s for s in said)
    # doing it again changes nothing it already did: the record taken away, the same repository started once more
    import shutil
    shutil.rmtree(rb.cwd / ".rb")
    code, env = rb("init", "the study")
    assert code == 0 and any("already in .mcp.json" in s for s in env.data["agents"]["said"]) and "left" not in env.data["agents"]
    assert json.loads((rb.cwd / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"] == servers
    assert (rb.cwd / "AGENTS.md").read_text(encoding="utf-8").count("rabbit-brain skill: begin") == 1


@pytest.mark.parametrize("runtime, later, left", [
    ({"CLAUDECODE": "1"}, "Claude Code: rb skill add --claude --project, and claude mcp add --scope project rabbit-brain -- rb mcp", ".mcp.json"),
    ({"CODEX_SANDBOX": "seatbelt"}, "Codex and the agents that read AGENTS.md: rb skill add --agents-md", "AGENTS.md"),
    ({"CURSOR_AGENT": "1"}, "Cursor: rb skill add --cursor", ".cursor/rules/rabbit-brain.mdc"),
])
def test_init_in_an_agent_session_names_that_agent_for_later_sessions(rb, monkeypatch, runtime, later, left):
    """The agent whose session runs rb init is named for its later sessions, with no session among its signs: rb's
    words never say who ran it. The running session would load nothing, so nothing is installed."""
    as_the_user(monkeypatch)
    for k, v in runtime.items():
        monkeypatch.setenv(k, v)
    code, env = rb("init", "the study")
    assert code == 0 and not (rb.cwd / left).exists()
    assert env.data["agents"]["later"] == [later] and env.data["agents"]["written"] == []


def test_init_with_no_agent_here_writes_nothing_and_says_how(rb, monkeypatch):
    as_the_user(monkeypatch)
    code, env = rb("init", "the study")
    assert code == 0 and not (rb.cwd / ".claude").exists() and not (rb.cwd / ".mcp.json").exists() and not (rb.cwd / "AGENTS.md").exists()
    assert "rb skill add" in env.data["agents"]["said"][0] and env.data["agents"]["written"] == []


def test_init_no_agents_leaves_every_instruction_file_alone(rb, monkeypatch):
    as_the_user(monkeypatch)
    (rb.cwd / "AGENTS.md").write_text("# House rules\n", encoding="utf-8")
    (rb.cwd / ".claude").mkdir()
    code, env = rb("init", "the study", "--no-agents")
    assert code == 0 and "agents" not in env.data
    assert (rb.cwd / "AGENTS.md").read_text(encoding="utf-8") == "# House rules\n" and not (rb.cwd / ".mcp.json").exists()


def test_an_mcp_config_rb_cannot_read_is_left_as_it_is(rb, monkeypatch):
    as_the_user(monkeypatch)
    (rb.cwd / ".claude").mkdir()
    (rb.cwd / ".mcp.json").write_text("{ not json", encoding="utf-8")
    code, env = rb("init", "the study")
    assert code == 0 and (rb.cwd / ".mcp.json").read_text(encoding="utf-8") == "{ not json"
    assert any("left as it is" in s for s in env.data["agents"]["said"])


def test_the_skill_and_mcp_say_a_text_names_no_user_or_chat_as_its_source():
    """Round 7: a session wrote "the researcher's rule ... stated in this chat" as a gate's reason. The skill a session
    loads and the reason MCP's tools take both say a text rests on a file and line, never on the user or the chat."""
    import json
    root = Path(__file__).resolve().parents[1]
    skill = " ".join((root / "SKILL.md").read_text(encoding="utf-8").split())
    assert "nor the user or the chat as what a text rests on (\"the researcher's rule\", \"stated in this chat\"): their words go in `--words`" in skill
    tools = {t["name"]: t for t in json.loads((root / "rust" / "src" / "assets" / "mcp.json").read_text(encoding="utf-8"))["tools"]}
    assert tools["decide"]["props"]["why"]["description"].endswith("what it rests on (a file and line), never the user or the chat")


# ---------------------------------------------------------------- a file that was here before rb (owner decision 3, Oct 12)

FOUR = b'{\n    "mcpServers": {\n        "other": {\n            "command": "other-server"\n        }\n    }\n}\n'
HOUSE = b"# House rules\r\n\r\nRun the tests before a commit.\r\n"


def _ledger(root):
    import json
    import subprocess
    common = Path(subprocess.run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"], cwd=root, capture_output=True, text=True).stdout.strip())
    path = common / "rb" / "owned.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()] if path.is_file() else []


@pytest.fixture
def team(rb, monkeypatch):
    """A repository whose team keeps .claude/, an AGENTS.md written with \\r\\n and a .mcp.json in four spaces holding
    another server, committed: the user, with no terminal attached (a script)."""
    import subprocess
    as_the_user(monkeypatch)
    cfg = rb.home / "gitconfig"
    cfg.write_text("[user]\n\temail = ana@example.org\n\tname = ana\n", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(cfg))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    subprocess.run(["git", "init", "-q"], cwd=rb.cwd, check=True)
    (rb.cwd / ".claude").mkdir()
    (rb.cwd / "AGENTS.md").write_bytes(HOUSE)
    (rb.cwd / ".mcp.json").write_bytes(FOUR)
    subprocess.run(["git", "add", "-A"], cwd=rb.cwd, check=True)
    subprocess.run(["git", "-c", "commit.gpgsign=false", "commit", "-qm", "the team's files"], cwd=rb.cwd, check=True)
    return rb


def test_init_off_a_terminal_changes_no_file_that_was_here_and_says_what_to_add(team):
    """With no terminal (a script, a pipe), rb init makes the files that are not here (the skill) and leaves AGENTS.md
    and .mcp.json byte for byte, saying for each what to add and the one command that adds it."""
    code, env = team("init", "INT8 ResNet-50")
    assert code == 0
    assert (team.cwd / "AGENTS.md").read_bytes() == HOUSE and (team.cwd / ".mcp.json").read_bytes() == FOUR
    assert (team.cwd / ".claude" / "skills" / "rabbit-brain" / "SKILL.md").is_file()
    left = {x["path"]: x for x in env.data["agents"]["left"]}
    assert left["AGENTS.md"]["command"] == "rb skill add --agents-md --yes"
    assert left[".mcp.json"]["command"] == "claude mcp add --scope project rabbit-brain -- rb mcp"
    assert left[".mcp.json"]["adds"] == '"rabbit-brain": {\n            "command": "rb",\n            "args": [\n                "mcp"\n            ]\n        }'.replace('"rabbit', '        "rabbit', 1)
    said = " ".join(env.data["agents"]["said"])
    assert "AGENTS.md was here before rb, so rb left it as it is. rb skill add --agents-md --yes adds rb's section at its end" in said
    assert '.mcp.json was here before rb, so rb left it as it is. To add rb\'s MCP server, put "rabbit-brain": {"command": "rb", "args": ["mcp"]} in its mcpServers' in said
    assert [line["how"] for line in _ledger(team.cwd)] == ["dir", "created", "dir"]       # rb's folder in git, the skill, the journal


def test_yes_adds_to_the_files_that_were_here_byte_for_byte(team):
    """--yes, the answer given up front for a script: rb's section goes at the end of AGENTS.md in its own \\r\\n, and
    rb's server into .mcp.json's mcpServers in its own four spaces. Every byte that was there stays; the ledger keeps
    each file's sha256 before and after and where rb's bytes went, and the files read back as before plus rb's part."""
    import hashlib
    import json
    code, env = team("init", "INT8 ResNet-50", "--yes")
    assert code == 0 and "left" not in env.data["agents"]
    agents, mcp = (team.cwd / "AGENTS.md").read_bytes(), (team.cwd / ".mcp.json").read_bytes()
    assert agents.startswith(HOUSE + b"\r\n<!-- rabbit-brain skill: begin") and agents.endswith(b"<!-- rabbit-brain skill: end -->\r\n")
    assert b"\n" not in agents.replace(b"\r\n", b"")
    assert json.loads(mcp) == {"mcpServers": {"other": {"command": "other-server"}, "rabbit-brain": {"command": "rb", "args": ["mcp"]}}}
    lines = {Path(line["path"]).name: line for line in _ledger(team.cwd)}
    for name, before, after in (("AGENTS.md", HOUSE, agents), (".mcp.json", FOUR, mcp)):
        line = lines[name]
        assert line["sha256_before"] == hashlib.sha256(before).hexdigest() and line["sha256_after"] == hashlib.sha256(after).hexdigest()
        cut = after[:line["offset"]] + after[line["offset"] + line["length"]:]
        assert cut == before, name                              # rb's bytes, in one place, and nothing else
    assert lines["AGENTS.md"]["how"] == "section" and lines[".mcp.json"]["how"] == "mcp_entry"
    # a second look changes nothing: the section and the server are there as rb writes them
    import shutil
    shutil.rmtree(team.cwd / ".rb")
    code, env = team("init", "INT8 ResNet-50", "--yes")
    assert code == 0 and (team.cwd / "AGENTS.md").read_bytes() == agents and (team.cwd / ".mcp.json").read_bytes() == mcp


@pytest.mark.skipif(sys.platform == "win32", reason="needs a pseudo-terminal")
@pytest.mark.parametrize("answer, changed", [("", False), ("n", False), ("whatever", False), ("y", True), ("YES", True)])
def test_init_in_the_user_s_own_terminal_asks_once_and_no_is_the_default(team, answer, changed):
    """In the user's own terminal, rb init asks once, showing exactly what it would add to each file that was here
    (rb's section, rb's member), and only y or yes changes them: Enter, or anything else, keeps them as they are."""
    import pty
    import subprocess
    from rbtest import command, environment
    main_side, user_side = pty.openpty()
    proc = subprocess.Popen([*command(), "init", "INT8 ResNet-50"], cwd=team.cwd, env={**environment(), "TERM": "xterm"}, stdin=user_side, stdout=user_side,
                            stderr=user_side, close_fds=True)
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
    question = text[text.index("For the agents here:"):text.index("[y/N] ")]
    assert question.count("[y/N]") == 0 and text.count("[y/N]") == 1
    assert question.splitlines()[-1] == "Add rb's server to .mcp.json and rb's section to AGENTS.md? "     # the prompt names the files
    assert ".mcp.json and AGENTS.md were here before rb. rb would add this to them, and change nothing else in them:" in question
    assert '.mcp.json, in mcpServers:\n        "rabbit-brain": {\n            "command": "rb",' in question
    assert "AGENTS.md, at its end:\n<!-- rabbit-brain skill: begin" in question and "<!-- rabbit-brain skill: end -->" in question
    assert ((team.cwd / "AGENTS.md").read_bytes() != HOUSE) is changed and ((team.cwd / ".mcp.json").read_bytes() != FOUR) is changed
    assert (team.cwd / ".claude" / "skills" / "rabbit-brain" / "SKILL.md").is_file()         # what was not here is made either way
    assert ("was here before rb, so rb left it as it is" in text) is not changed


def test_an_agent_session_s_init_changes_nothing_that_was_here(team, monkeypatch):
    """In an agent session rb init installs nothing (the session would load none of it), --yes or not, and names the
    commands for later sessions."""
    monkeypatch.setenv("CLAUDECODE", "1")
    code, env = team("init", "INT8 ResNet-50", "--yes")
    assert code == 0 and env.data["agents"]["written"] == []
    assert (team.cwd / "AGENTS.md").read_bytes() == HOUSE and (team.cwd / ".mcp.json").read_bytes() == FOUR
    assert not (team.cwd / ".claude" / "skills").exists()


def test_reconcile_apply_follows_init_s_rule_for_the_agents_files(team):
    """rb reconcile --apply where there is no record starts it as rb init does: a file that was here is left as it is
    off a terminal, --yes adds to it, and --no-agents leaves every instruction file alone."""
    code, env = team("reconcile", "--apply")
    assert code == 0 and (team.cwd / "AGENTS.md").read_bytes() == HOUSE and (team.cwd / ".mcp.json").read_bytes() == FOUR
    assert {x["path"] for x in env.data["agents"]["left"]} == {"AGENTS.md", ".mcp.json"}
    import shutil
    shutil.rmtree(team.cwd / ".rb")
    shutil.rmtree(team.cwd / ".claude" / "skills")
    code, env = team("reconcile", "--apply", "--no-agents")
    assert code == 0 and "agents" not in env.data and not (team.cwd / ".claude" / "skills").exists()
    shutil.rmtree(team.cwd / ".rb")
    code, env = team("reconcile", "--apply", "--yes")
    assert code == 0 and (team.cwd / "AGENTS.md").read_bytes().startswith(HOUSE) and (team.cwd / "AGENTS.md").read_bytes() != HOUSE


# ---------------------------------------------------------------- a skill or rule at rb's path that rb did not write

OURS_SKILL = b"---\nname: rabbit-brain\ndescription: our team's own take\n---\n\nRecord every run with its seed.\n"
OURS_RULE = b"---\nalwaysApply: true\n---\n\nOur own rule.\n"


@pytest.fixture
def customised(team):
    """The team also keeps its own .claude/skills/rabbit-brain/SKILL.md and .cursor/rules/rabbit-brain.mdc, committed."""
    import subprocess
    for rel, data in ((".claude/skills/rabbit-brain/SKILL.md", OURS_SKILL), (".cursor/rules/rabbit-brain.mdc", OURS_RULE)):
        (team.cwd / rel).parent.mkdir(parents=True, exist_ok=True)
        (team.cwd / rel).write_bytes(data)
    subprocess.run(["git", "add", "-A"], cwd=team.cwd, check=True)
    subprocess.run(["git", "-c", "commit.gpgsign=false", "commit", "-qm", "our skill and rule"], cwd=team.cwd, check=True)
    return team


@pytest.mark.parametrize("yes", [False, True], ids=["off a terminal", "--yes"])
def test_init_never_writes_over_a_skill_or_rule_rb_did_not_write(customised, yes):
    """rb init makes the files that are not there: a skill or a Cursor rule at rb's path that rb did not write stays
    byte for byte, --yes or not (--yes answers what init asks, about AGENTS.md and .mcp.json), and init says which
    command puts rb's in its place. rb remove then leaves both as they are."""
    import subprocess
    code, env = customised("init", "INT8 ResNet-50", *(["--yes"] if yes else []))
    assert code == 0
    skill, rule = customised.cwd / ".claude/skills/rabbit-brain/SKILL.md", customised.cwd / ".cursor/rules/rabbit-brain.mdc"
    assert skill.read_bytes() == OURS_SKILL and rule.read_bytes() == OURS_RULE
    left = {x["path"]: x["command"] for x in env.data["agents"]["left"]}
    assert left[".claude/skills/rabbit-brain/SKILL.md"] == "rb skill add --claude --project --yes" and left[".cursor/rules/rabbit-brain.mdc"] == "rb skill add --cursor --yes"
    said = " ".join(env.data["agents"]["said"])
    assert ".claude/skills/rabbit-brain/SKILL.md was here before rb and is not rb's, so rb left it as it is." in said
    assert not any(p.endswith(("SKILL.md", "rabbit-brain.mdc")) for p in env.data["agents"]["written"])
    assert {Path(line["path"]).name for line in _ledger(customised.cwd)} & {"SKILL.md", "rabbit-brain.mdc"} == set()
    code, _ = customised("remove", "--yes")
    assert code == 0 and skill.read_bytes() == OURS_SKILL and rule.read_bytes() == OURS_RULE
    status = subprocess.run(["git", "status", "--porcelain", "--", ".claude", ".cursor"], cwd=customised.cwd, capture_output=True, text=True).stdout
    assert status == ""


def test_skill_add_puts_rb_s_own_in_place_of_one_rb_did_not_write_only_once_agreed(customised):
    """rb skill add --claude --project (or --cursor) over a file rb did not write: off a terminal it is left as it is,
    with the command; --yes puts rb's text there and the ledger keeps what was there, so rb remove puts it back, byte
    for byte and with its time."""
    skill = customised.cwd / ".claude/skills/rabbit-brain/SKILL.md"
    was = skill.stat().st_mtime_ns
    code, env = customised("skill", "add", "--claude", "--project")
    assert code == 0 and skill.read_bytes() == OURS_SKILL and env.data["written"] == []
    assert env.data["left"][0]["command"] == "rb skill add --claude --project --yes"
    code, _ = customised("init", "INT8 ResNet-50")
    code, env = customised("skill", "add", "--claude", "--project", "--yes")
    assert code == 0 and skill.read_bytes() != OURS_SKILL and skill.read_text(encoding="utf-8").startswith("---\nname: rabbit-brain")
    [line] = [x for x in _ledger(customised.cwd) if Path(x["path"]).name == "SKILL.md"]
    assert line["how"] == "replaced" and line["before"] == OURS_SKILL.decode() and "sha256_before" in line
    code, env = customised("remove", "--dry-run")
    [item] = [i for i in env.data["remove"] if i["path"] == ".claude/skills/rabbit-brain/SKILL.md"]
    assert item["why"].endswith("that file goes back to what it was, byte for byte")
    code, _ = customised("remove", "--yes")
    assert code == 0 and skill.read_bytes() == OURS_SKILL and skill.stat().st_mtime_ns == was


@pytest.mark.parametrize("runtime", [{"CLAUDECODE": "1"}, {"CODEX_THREAD_ID": "thread-skill-7"}], ids=["claude code", "codex"])
def test_an_agent_session_s_yes_changes_no_file_that_was_here(customised, monkeypatch, runtime):
    """In an agent session, rb skill add --agents-md (or --claude --project) leaves a file that was there as it is, even
    with --yes: agreeing is the user's, in their own terminal. rb names the command without --yes, for them to run."""
    for k, v in runtime.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("RB_ACTOR", raising=False)
    for argv in (("--agents-md",), ("--agents-md", "--yes"), ("--claude", "--project", "--yes")):
        code, env = customised("skill", "add", *argv)
        assert code == 0 and env.data["written"] == [], argv
        assert (customised.cwd / "AGENTS.md").read_bytes() == HOUSE and (customised.cwd / ".claude/skills/rabbit-brain/SKILL.md").read_bytes() == OURS_SKILL
        commands = [x["command"] for x in env.data["left"]]
        assert commands and not any("--yes" in c for c in commands), commands
        import subprocess
        from rbtest import command, environment
        said = subprocess.run([*command(), "skill", "add", *argv], cwd=customised.cwd, env=environment(), capture_output=True, text=True, stdin=subprocess.DEVNULL)
        assert said.returncode == 0 and "--yes" not in said.stdout and "in an agent session rb changes no file that was there" in said.stdout, said.stdout


@pytest.fixture
def linked(rb, monkeypatch, tmp_path_factory):
    """A repository whose .claude/ is a link to the team's folder outside it, and nothing else of an agent's."""
    import subprocess
    as_the_user(monkeypatch)
    cfg = rb.home / "gitconfig"
    cfg.write_text("[user]\n\temail = ana@example.org\n\tname = ana\n", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(cfg))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    subprocess.run(["git", "init", "-q"], cwd=rb.cwd, check=True)
    rb.outside = tmp_path_factory.mktemp("team-claude")
    (rb.cwd / ".claude").symlink_to(rb.outside, target_is_directory=True)
    rb.skill = (rb.outside / "skills" / "rabbit-brain" / "SKILL.md").resolve()
    return rb


@pytest.mark.skipif(sys.platform == "win32", reason="a symbolic link takes a privilege there")
def test_a_link_out_of_the_repository_takes_no_write_without_the_yes(linked, capsys):
    """rb skill add --claude --project, and rb init, where .claude/ links outside the repository: off a terminal they
    write nothing there and say so, naming where the link goes and the command that writes it; --yes writes it, and
    says where it went."""
    note = f"(through a link, at {linked.skill.as_posix()}, outside this repository)"
    code, env = linked("skill", "add", "--claude", "--project")
    assert code == 0 and env.data["written"] == [] and not linked.skill.exists()
    assert [x["command"] for x in env.data["left"]] == ["rb skill add --claude --project --yes"]
    main(["skill", "add", "--claude", "--project"])
    assert f"rb wrote nothing to .claude/skills/rabbit-brain/SKILL.md {note}, since rb writes outside this repository only once agreed." in capsys.readouterr().out
    code, env = linked("init", "INT8 ResNet-50")
    assert code == 0 and not linked.skill.exists() and (linked.cwd / ".mcp.json").is_file()      # what is in the repository is made
    assert f"rb wrote nothing to .claude/skills/rabbit-brain/SKILL.md {note}" in " ".join(env.data["agents"]["said"])
    main(["skill", "add", "--claude", "--project", "--yes"])
    assert f"wrote {(linked.cwd / '.claude/skills/rabbit-brain/SKILL.md').as_posix()} {note}\n" in capsys.readouterr().out
    assert linked.skill.is_file()


@pytest.mark.skipif(sys.platform == "win32", reason="needs a pseudo-terminal")
@pytest.mark.parametrize("answer", ["", "y"])
def test_init_asks_before_a_write_a_link_takes_out_of_the_repository(linked, answer):
    """In the user's own terminal, rb init asks once before the skill goes where .claude/ links, outside the repository,
    naming that place, and No is the default."""
    import pty
    import subprocess
    from rbtest import command, environment
    main_side, user_side = pty.openpty()
    proc = subprocess.Popen([*command(), "init", "INT8 ResNet-50"], cwd=linked.cwd, env={**environment(), "TERM": "xterm"}, stdin=user_side,
                            stdout=user_side, stderr=user_side, close_fds=True)
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
    name = f".claude/skills/rabbit-brain/SKILL.md (through a link, at {linked.skill.as_posix()}, outside this repository)"
    question = text[text.index("For the agents here:"):text.index("[y/N] ")]
    assert f"rb would write outside this repository, where a link takes it:\n\n{name}: a new file, with rb's own text (what `rb skill` prints).\n" in question
    assert question.splitlines()[-1] == f"Write {name}? "
    assert linked.skill.is_file() is (answer == "y")
