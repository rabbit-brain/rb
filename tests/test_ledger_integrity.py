"""The integrity rules of the record (.rb/, format 1): what a finding's timing and integrity rest on, and what cannot
be faked.

Promises pinned here:

- Who: the actor is RB_ACTOR, else a detected agent runtime (CLAUDECODE, AI_AGENT, CODEX_*, CURSOR_AGENT, GITHUB_ACTIONS,
  in that order), else a human named from git (email, then name), else the login. A malformed RB_ACTOR is refused. A human
  named inside an agent session is ignored: the agent is recorded, and the name it gave is kept as ignored.
- No call is refused for who makes it: a ruling or a vouch with decide, retracting what something rests on, freezing
  and amending all go through for an agent, recorded as the agent's, with the user's words it acted on (--words)
  kept verbatim on the record and in the log.
- Verification is strict: a comment never verifies; a prose line needs the setting's name or --term; numbers are
  standalone tokens compared exactly (1e-4 is 0.0001; resnet50 does not state 50; 1700000001 is not 1700000000);
  text is a whole token (adamw is not adam); lists are ordered; a YAML line is read as its key path and must be the
  setting's own line.
- A source stating another value is a conflict that re-verifying does not clear while the source states it or cannot be
  read, and a check that reads something else replaces; a setting's history has every check of it; a verified source
  is re-checked on every read, a population.* or a rejected one too, and when rb spec set gives it its value again
  (moved: a note; stale or conflict: a problem); a commit-pinned source never goes stale, and a hand-made resolution
  does not pass the re-check.
- Variants differing in anything not declared in --varies is a confound: an integrity problem for a finding that
  compares them.
- A frozen spec changes only by --amend, kept with who made it and the hashes before and after; a spec edited by hand
  is drift.
- Timing: a criterion is declared when it is written, by whoever wrote it, an agent included, with no human's freeze
  needed. A run recorded before it still counts, and the timing says the criterion came after the evidence. Synthetic
  runs, runs under an earlier spec, and runs whose config differs from the spec are not counted.
- Integrity: a required unknown setting, a confound, drift, a stale or contradicting source, and for a cited criterion
  a setting that differs from the source's are problems. A provisional setting is a note, and a vouch marks it
  vouched until the value changes. A rule with fewer units than min_n is undetermined, a margin within the stated
  noise is noted as borderline, an interval that crosses the threshold a rule reads on the estimate is noted, and the
  noise of the runs is read as their interval.
- A retracted run stops counting and stays on record.
- Every record is checked against the content hash rb logged: a hand edit that changes a value is an integrity problem
  for the findings that depend on it, and only those, trips any --fail-on gate over them, and fails the unscoped
  integrity gate and rb doctor; a comment or a reformat changes no content and is no edit; an unparseable file is
  E_STATE_CORRUPT; a source outside the project is E_SOURCE_OUTSIDE. Every write is logged with actor, actor_via, via,
  path and sha256, concurrent writers lose nothing, and a rewritten log is a problem for every finding.
"""
import hashlib
import html
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from rabbit_brain.errors import RBError
from rabbit_brain.models import Envelope
from rbtest import AGENT_SIGNS, AGENT_VARS, as_the_user, main, rbyaml, ROOT, SESSION_LEFTOVERS, SESSION_SIGNS, state

HAS_GIT = shutil.which("git") is not None
needs_git = pytest.mark.skipif(not HAS_GIT, reason="git is not installed")


# ---------------------------------------------------------------- fixtures and helpers


def git(*args, cwd=None) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True, cwd=cwd).stdout.strip()


def _isolate_git(monkeypatch, tmp_path_factory) -> None:
    """No global or system git config (signing, another email) leaks into the tests."""
    cfg = tmp_path_factory.mktemp("gitconfig") / "config"
    cfg.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(cfg))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")


@pytest.fixture
def human(workdir, monkeypatch, tmp_path_factory):
    """The project root, where rb records the caller as human:t whatever environment runs pytest (which may
    itself be an agent session)."""
    as_the_user(monkeypatch)
    _isolate_git(monkeypatch, tmp_path_factory)
    monkeypatch.setenv("LOGNAME", "t")          # the fallback when git is missing
    if HAS_GIT:
        git("init", "-q")
        git("config", "user.email", "t@example.com")
        git("config", "user.name", "t")
    return workdir


def who(monkeypatch, root: Path, env: dict) -> dict:
    """Who rb takes a process at `root` for when its environment says `env`: {"id", "via", "runtime", "ignored"}, as
    the SDK's who() answers. The record there is started first, as whoever the test's own environment makes it."""
    if not (root / ".rb").exists():
        here = Path.cwd()
        os.chdir(root)
        try:
            assert main(["init", "who", "--no-agents"]) == 0
        finally:
            os.chdir(here)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    return state(root).who()


def decisions_on(rec, subject: str) -> list:
    """The calls on a subject, in order, the retracted ones aside."""
    return [d for d in rec.records("decision", live=True) if d.subject == subject]


def rbj(capsys, *argv):
    code = main([*argv, "--json"])
    return code, Envelope.model_validate_json(capsys.readouterr().out)


def ok(capsys, *argv) -> Envelope:
    code, env = rbj(capsys, *argv)
    assert code == 0, (argv, env.errors, env.data.get("outcome"))
    return env


def start(capsys, *experiment_args) -> None:
    ok(capsys, "init", "integrity")
    ok(capsys, "experiment", "add", "x", "--id", "e1", *experiment_args)


def record(capsys, *pairs, exp="e1") -> str:
    """rb run add; returns the run's id."""
    return ok(capsys, "run", "add", exp, *pairs).data["object"]["id"]


def finding(capsys, criterion="c1") -> dict:
    return ok(capsys, "show", criterion).data["finding"]


def caveats(capsys, subject, exp="e1") -> list:
    """The experiment's caveats about one subject (e1/lr), as (code, problem)."""
    return [(c["code"], c["problem"]) for c in ok(capsys, "show", exp).data["caveats"] if c["subject"] == subject]


def problems(f: dict) -> list:
    """A finding's integrity problems, as (code, subject)."""
    return [(p["code"], p["subject"]) for p in f["integrity"]]


def notes(f: dict, code: str) -> list:
    """A finding's caveats with one code, as (subject, problem)."""
    return [(c["subject"], c["problem"]) for c in f["caveats"] if c["code"] == code]


def counted(f: dict) -> list:
    """Each run a finding reads, as (run, counted, produced before the criterion was declared)."""
    return [(o["run"], o["counted"], o["before_declared"]) for o in f["observations"]]


def setting(capsys, address) -> dict:
    return ok(capsys, "show", address).data["object"]


def spec_set(capsys, exp, name, value, *extra):
    """rb spec set; returns (exit code, the setting's status)."""
    code, env = rbj(capsys, "spec", "set", exp, name, value, *extra)
    assert env.ok, env.errors
    return code, env.data["object"]["status"]


def experiment_file(root: Path, exp: str = "e1") -> Path:
    return root / ".rb" / "experiments" / exp / "experiment.yaml"


def criterion_file(root: Path, id: str = "c1", exp: str = "e1") -> Path:
    """Each criterion of an experiment has a file of its own in the experiment's criteria/."""
    return root / ".rb" / "experiments" / exp / "criteria" / f"{id}.yaml"


def edit_criterion(root: Path, id: str, exp: str = "e1", **changes) -> None:
    """A hand edit that changes values of one criterion in its file, written back as Rabbit Brain YAML."""
    p = criterion_file(root, id, exp)
    doc = rbyaml.loads(p.read_text(encoding="utf-8"))
    doc.update(changes)
    p.write_text(rbyaml.dumps(doc), encoding="utf-8")


# ---------------------------------------------------------------- who: actor detection


def test_rb_actor_names_the_agent_inside_a_detected_runtime(human, monkeypatch):
    a = who(monkeypatch, human, {"RB_ACTOR": "agent:bot", "CLAUDECODE": "1"})
    assert (a["id"], a["via"], a["runtime"]) == ("agent:bot", "RB_ACTOR in a Claude Code session", "claude-code")


@pytest.mark.parametrize("env,expected,via", [
    ({"CLAUDECODE": "1", "AI_AGENT": "cursor", "CODEX_HOME": "x", "GITHUB_ACTIONS": "true"}, "agent:claude-code", "detected:CLAUDECODE"),
    ({"AI_AGENT": "cursor_1.2/agent", "CODEX_HOME": "x", "GITHUB_ACTIONS": "true"}, "agent:cursor", "detected:AI_AGENT"),
    ({"CODEX_SANDBOX": "seatbelt", "GITHUB_ACTIONS": "true"}, "agent:codex", "detected:CODEX_SANDBOX"),
    ({"CODEX_THREAD_ID": "thread-test-41b2", "CURSOR_AGENT": "1", "GITHUB_ACTIONS": "true"}, "agent:codex", "detected:CODEX_THREAD_ID"),
    ({"CODEX_SESSION_ID": "sess-test-41b2", "CURSOR_AGENT": "1"}, "agent:codex", "detected:CODEX_SESSION_ID"),
    ({"CURSOR_AGENT": "1", "CURSOR_TRACE_ID": "trace-test-9d1e", "GITHUB_ACTIONS": "true"}, "agent:cursor", "detected:CURSOR_AGENT"),
    ({"GITHUB_ACTIONS": "true"}, "agent:github-actions", "detected:GITHUB_ACTIONS"),
])
def test_agent_runtimes_are_detected_in_order(human, monkeypatch, env, expected, via):
    a = who(monkeypatch, human, env)
    assert (a["id"], a["via"]) == (expected, via) and a["runtime"] == expected.split(":", 1)[1]


def test_the_tests_and_the_benchmark_clear_every_variable_rb_reads_an_agent_or_a_session_from():
    """A test that plays the user clears AGENT_VARS (as_the_user), and the agent contract clears its own copies
    (bench/agent/drive.py) before it plays the user or the agent and before a live agent's turn, so each reads the same
    in an agent session and in CI, where GitHub sets GITHUB_RUN_ID: each AGENT_SIGNS names the variables
    rust/src/actor.rs reads an agent or an actor from, each SESSION_SIGNS those it reads a chat session from, and only
    those."""
    src = (ROOT / "rust" / "src" / "actor.rs").read_text(encoding="utf-8")

    def table(name: str) -> set:
        return set(re.findall(r'"([A-Z_]+)"', re.search(rf"pub const {name}: &\[&str\] = &\[(.*?)\];", src, re.S).group(1)))

    agent, session = set(re.findall(r'\benv\("([A-Z_]+)"\)', src)) | table("CODEX_SIGNS") | table("CLAUDE_CODE_ELSEWHERE"), table("SESSION_SIGNS")
    spec = importlib.util.spec_from_file_location("rb_bench_drive", ROOT / "bench" / "agent" / "drive.py")
    bench = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bench)
    for where, agents, sessions, left in (("tests/rbtest.py", AGENT_SIGNS, SESSION_SIGNS, SESSION_LEFTOVERS),
                                          ("bench/agent/drive.py", bench.AGENT_SIGNS, bench.SESSION_SIGNS, bench.SESSION_LEFTOVERS)):
        assert set(agents) == agent, (where, sorted(set(agents) ^ agent))
        assert set(sessions) == session, (where, sorted(set(sessions) ^ session))
        assert set(left) == table("SESSION_LEFTOVERS") - agent - session, where
    # and what else a runtime leaves, on which the user's own controls fail closed (SESSION_LEFTOVERS)
    leftovers = table("SESSION_LEFTOVERS")
    assert set(AGENT_VARS) == agent | session | leftovers, sorted(set(AGENT_VARS) ^ (agent | session | leftovers))


def test_a_users_agent_team_settings_are_no_sign_of_a_session(workdir, monkeypatch, capsys):
    """Claude Code's users export CLAUDE_CODE_TEAMMATE_MODE, CLAUDE_CODE_SPAWN_BACKEND,
    CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS and CLAUDE_CONFIG_DIR in their shell to set up agent teams, and Claude Code
    sets none of them only in the commands it runs, so none says a session runs: a terminal with all four writes as
    the user, and rb confirm and rb ui work there. A staging pass in such a terminal recorded every write as
    agent:claude-code, and rb confirm and rb ui refused the user."""
    as_the_user(monkeypatch)
    subprocess.run(["git", "init", "-q"], check=True)
    subprocess.run(["git", "config", "user.email", "ana@example.com"], check=True)
    subprocess.run(["git", "config", "user.name", "ana"], check=True)
    for k, v in {"CLAUDE_CODE_TEAMMATE_MODE": "auto", "CLAUDE_CODE_SPAWN_BACKEND": "in-process",
                 "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1", "CLAUDE_CONFIG_DIR": str(workdir / ".claude-config")}.items():
        monkeypatch.setenv(k, v)

    def rb(*argv: str) -> tuple:
        code = main([*argv, "--json"])
        return code, Envelope.model_validate_json(capsys.readouterr().out)
    assert rb("init", "INT8 ResNet-50 on ImageNet", "--no-agents")[0] == 0
    assert rb("question", "add", "Does INT8 cost top-1 on ImageNet?", "--id", "q1")[0] == 0
    monkeypatch.setenv("CLAUDECODE", "1")                  # a session proposes a call, for the user to confirm here
    code, env = rb("decide", "q1", "park", "--why", "the FP32 baseline runs come first")
    assert code == 0, env.errors
    proposed = env.data["object"]["id"]
    monkeypatch.delenv("CLAUDECODE")
    code, env = rb("confirm", proposed)
    assert code == 0, env.errors
    code, env = rb("ui")                                    # pytest has no terminal: refused for that, never as a session
    assert code != 0 and "interactive terminal" in env.errors[0].message and "agent session" not in env.errors[0].message, env.errors
    lines = [json.loads(line) for line in Path(".rb/log.jsonl").read_text(encoding="utf-8").splitlines()]
    sessions = [line for line in lines if line["actor"] != "human:ana"]
    assert [line["op"] for line in sessions if line["op"] != "session"] == ["decide"], sessions
    assert lines[-1]["actor"] == "human:ana" and lines[-1]["op"] == "decide"


@pytest.mark.skipif(not (ROOT / "rust" / "tests_py" / "golden.py").is_file(), reason="the golden harness stays in the repository")
def test_the_golden_harness_runs_rb_in_no_agent_and_no_chat_session(monkeypatch, tmp_path):
    """What the goldens hold is rb as the user runs it at their own terminal, whatever session records them."""
    monkeypatch.setattr(sys, "path", list(sys.path))              # the harness puts its own folder first
    spec = importlib.util.spec_from_file_location("rb_golden", ROOT / "rust" / "tests_py" / "golden.py")
    golden = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(golden)
    for name in AGENT_VARS:
        monkeypatch.setenv(name, "1")
    left = sorted(set(AGENT_VARS) & set(golden.environment(str(tmp_path), {})))     # names only: values stay out of a log
    assert not left, f"the golden harness runs rb with {left} set"


@pytest.mark.skipif(not (ROOT / "rust" / "tests_py" / "golden.py").is_file() or os.name != "posix",
                    reason="the golden harness stays in the repository, and the goldens are held on Linux")
def test_the_golden_harness_plays_a_scenario_where_it_played_it_before(monkeypatch):
    """A record names a linked worktree by its whole path, so a run's identity and a commit that holds the record hash
    the directory a scenario runs in: the harness plays each scenario in the same directory every time."""
    monkeypatch.setattr(sys, "path", list(sys.path))
    spec = importlib.util.spec_from_file_location("rb_golden", ROOT / "rust" / "tests_py" / "golden.py")
    golden = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(golden)
    scenario = {"steps": [("sh", "pwd | cksum > here.txt")]}
    first, second = (golden.played("harness-probe", scenario, ["true"])["files"]["here.txt"] for _ in range(2))
    assert first == second


@pytest.mark.parametrize("env", [{"CLAUDECODE": "0"}, {"GITHUB_ACTIONS": "false"}, {"AI_AGENT": ""}])
def test_a_variable_that_does_not_name_an_agent_is_not_one(human, monkeypatch, env):
    a = who(monkeypatch, human, env)
    assert a["id"] == "human:t" and a["runtime"] is None


@needs_git
def test_a_human_is_named_from_the_git_email(human, monkeypatch):
    a = who(monkeypatch, human, {})
    assert a == {"id": "human:t", "via": "git-email", "runtime": None, "ignored": None, "agent_session": False}


@needs_git
def test_a_human_is_named_from_the_git_name_when_there_is_no_email(human, tmp_path_factory, monkeypatch):
    repo = tmp_path_factory.mktemp("named")
    git("init", "-q", cwd=repo)
    git("config", "user.name", "Jane Doe", cwd=repo)
    a = who(monkeypatch, repo, {})
    assert (a["id"], a["via"]) == ("human:jane-doe", "git-name")


def test_a_human_is_named_from_the_login_when_git_names_nobody(human, tmp_path_factory, monkeypatch):
    bare = tmp_path_factory.mktemp("nogit")
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(bare.parent))
    monkeypatch.setenv("LOGNAME", "ana")
    a = who(monkeypatch, bare, {})
    assert (a["id"], a["via"]) == ("human:ana", "login")


@pytest.mark.parametrize("raw", ["bob", "human:", "robot:x", "human:two words", ""])
def test_a_malformed_rb_actor_is_refused(human, monkeypatch, raw):
    with pytest.raises(RBError) as err:
        who(monkeypatch, human, {"RB_ACTOR": raw})
    assert err.value.code == "E_ACTOR_INVALID"


def test_a_malformed_rb_actor_is_refused_on_any_command(human, capsys, monkeypatch):
    start(capsys)
    monkeypatch.setenv("RB_ACTOR", "bob")
    code, env = rbj(capsys, "status")
    assert code == 2 and env.errors[0].code == "E_ACTOR_INVALID"


def test_a_human_named_inside_an_agent_session_is_ignored(human, monkeypatch):
    """A human's name typed inside an agent session is exactly what an agent would type, so rb does not take it."""
    a = who(monkeypatch, human, {"RB_ACTOR": "human:x", "CLAUDECODE": "1"})
    assert (a["id"], a["via"], a["ignored"]) == ("agent:claude-code", "detected:CLAUDECODE", "human:x")


def test_a_freeze_with_a_humans_name_inside_an_agent_session_is_recorded_as_the_agent(human, capsys, monkeypatch):
    """The freeze is taken and is the agent's: the name RB_ACTOR gave is not who froze it, and is kept only as ignored,
    in the command's output and in log.jsonl."""
    start(capsys)
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("RB_ACTOR", "human:x")
    env = ok(capsys, "freeze", "e1", "-m", "go")
    assert env.data["actor"] == {"id": "agent:claude-code", "via": "detected:CLAUDECODE", "ignored_rb_actor": "human:x",
                                 "agent": {"name": "claude-code", "provider": "anthropic", "provider_from": "inferred"}}
    assert state(human).get("e1", "experiment").frozen.by == "agent:claude-code"
    last = state(human).log(None)[-1]
    assert (last["op"], last["actor"], last["actor_via"], last["ignored_rb_actor"]) == ("freeze", "agent:claude-code", "detected:CLAUDECODE", "human:x")


def test_a_codex_home_in_a_shell_profile_does_not_make_a_human_an_agent(human, monkeypatch):
    a = who(monkeypatch, human, {"CODEX_HOME": "/home/me/.codex"})
    assert a["id"] == "human:t" and a["runtime"] is None


# ---------------------------------------------------------------- calls: taken from anyone, recorded as whoever made them


@pytest.fixture
def frozen_candidate(human, capsys, monkeypatch):
    """e1 with a vouchable setting e1/lr, a criterion c1 on it, a run the criterion counts, and a second experiment e2
    open for freezing. Returns the run id."""
    start(capsys)
    ok(capsys, "spec", "set", "e1", "lr", "0.1")
    ok(capsys, "criterion", "add", "s", "-e", "e1", "--metric", "top1", "--at-least", "0.7", "--id", "c1")
    ok(capsys, "freeze", "e1", "-m", "locked")
    run = record(capsys, "top1=0.76")
    ok(capsys, "experiment", "add", "y", "--id", "e2")
    return run


WORDS = "yes, go ahead with that"      # what the user said in the chat, which the agent acts on


@pytest.mark.parametrize("argv,made", [
    (["freeze", "e2", "-m", "lock the spec"], lambda rec, run: rec.get("e2", "experiment").frozen),
    (["decide", "c1", "accept", "-m", "looks right"], lambda rec, run: decisions_on(rec, "c1")[-1]),
    (["decide", "e1/lr", "accept", "-m", "I checked it"], lambda rec, run: decisions_on(rec, "e1/lr")[-1]),
    (["spec", "set", "e1", "lr", "0.2", "--amend", "-m", "typo in the spec"], lambda rec, run: rec.get("e1", "experiment").amendments[-1]),
    (["retract", "{run}", "-m", "bad run"], lambda rec, run: rec.get(run, "run").retracted),
], ids=["freeze", "ruling", "vouch", "amendment", "retraction"])
def test_an_agents_call_is_taken_and_recorded_as_the_agents_with_the_users_words(human, frozen_candidate, capsys, monkeypatch, argv, made):
    """A freeze, a ruling, a vouch, an amendment of a frozen spec and the retraction of a run a criterion counts all go
    through for an agent. The record of each (the freeze, the decision, the amendment, the retraction) is the agent's,
    and keeps the user's words the agent acted on verbatim, as the write's log.jsonl entry does."""
    argv = [a.replace("{run}", frozen_candidate) for a in argv]
    monkeypatch.setenv("CLAUDECODE", "1")
    ok(capsys, *argv, "--words", WORDS)
    rec = state(human)
    call = made(rec, frozen_candidate)
    assert (call.by, call.words) == ("agent:claude-code", WORDS)
    last = rec.log(None)[-1]
    assert (last["actor"], last["actor_via"], last["words"]) == ("agent:claude-code", "detected:CLAUDECODE", WORDS)


# ---------------------------------------------------------------- verification is strict


def test_a_comment_line_never_verifies(human, capsys):
    start(capsys)
    Path("train.py").write_text("# lr = 0.0001\n", encoding="utf-8")
    Path("cfg.yaml").write_text("# lr: 0.0001\nlr: 0.2\n", encoding="utf-8")
    assert spec_set(capsys, "e1", "lr", "0.0001", "--source", "train.py:1") == (1, "provisional")
    assert spec_set(capsys, "e1", "lr", "0.0001", "--source", "train.py:1", "--term", "lr") == (1, "provisional")
    assert spec_set(capsys, "e1", "lr", "0.0001", "--source", "cfg.yaml:1") == (1, "provisional")


def test_a_prose_line_needs_the_settings_name_or_a_term(human, capsys):
    start(capsys)
    Path("paper.txt").write_text("We train with a learning rate of 0.0001 for 90 epochs.\n", encoding="utf-8")
    assert spec_set(capsys, "e1", "lr", "1e-4", "--source", "paper.txt:1") == (1, "provisional")
    assert spec_set(capsys, "e1", "lr", "1e-4", "--source", "paper.txt:1", "--term", "learning rate") == (0, "verified")


def test_a_quote_verifies_the_value_it_contains(human, capsys):
    start(capsys)
    Path("paper.txt").write_text("We train with a learning rate of 0.0001 for 90 epochs.\n", encoding="utf-8")
    assert spec_set(capsys, "e1", "lr", "1e-4", "--source", "paper.txt", "--quote", "learning rate of 0.0001", "--term", "learning rate") == (0, "verified")


def test_a_quote_that_does_not_name_the_setting_does_not_verify_it(human, capsys):
    """'for 90 epochs' states 90, but not for weight_decay: a quote has to be about the setting it verifies."""
    start(capsys)
    Path("paper.txt").write_text("We train with a learning rate of 0.0001 for 90 epochs.\n", encoding="utf-8")
    assert spec_set(capsys, "e1", "weight_decay", "90", "--source", "paper.txt", "--quote", "for 90 epochs") == (1, "provisional")
    assert spec_set(capsys, "e1", "lr", "1e-4", "--source", "paper.txt", "--quote", "learning rate of 0.0001") == (1, "provisional")


def test_a_quote_states_only_the_words_and_numbers_it_holds_whole(human, capsys):
    """A quote is read as its file has it: where it begins or ends inside a word or a number there, it states nothing,
    for a setting, a cited number and an analysis alike. A number the file changed past the end of a quote that rb
    verified is a stale source, not one that still states the value; one an earlier rb verified at such a place is
    stale as that rb's own reading, never as a record rb did not make."""
    start(capsys, "--baseline", "fp32", "--candidate", "int8")
    Path("paper.txt").write_text("We use a batch size of 32 per GPU.\nThe offset is \u22120.1 here.\nOurs achieves 1.43 validation loss on the held-out set.\n"
                                 "momentum: 0.9\n| int8 vs fp32 | top1 0.021 [0.012, 0.030] |\n", encoding="utf-8")
    code, env = rbj(capsys, "spec", "set", "e1", "batch", "3", "--source", "paper.txt", "--quote", "batch size of 3", "--term", "batch size")
    assert (code, env.data["object"]["status"]) == (1, "provisional") and env.data["outcome"]["failures"][0]["reason"] == (
        "the quote is in paper.txt only as part of a longer word or number; paper.txt:1 reads: We use a batch size of 32 per GPU.")
    assert spec_set(capsys, "e1", "batch", "32", "--source", "paper.txt", "--quote", "batch size of 32", "--term", "batch size") == (0, "verified")
    assert spec_set(capsys, "e1", "offset", "0.1", "--source", "paper.txt", "--quote", "0.1 here", "--term", "offset") == (1, "provisional")
    code, env = rbj(capsys, "criterion", "add", "ours", "-e", "e1", "--metric", "val_loss", "--equals", "1.4", "--tolerance", "0.05",
                    "--source", "paper.txt", "--quote", "achieves 1.4")
    assert code == 2 and env.errors[0].code == "E_SOURCE_UNRESOLVED"
    code, env = rbj(capsys, "analysis", "add", "e1", "--compare", "int8/fp32:top1=0.021[0.012,0.03]", "--source", "paper.txt",
                    "--quote", "top1 0.021 [0.012, 0.03", "--method", "bootstrap")
    assert code == 2 and env.errors[0].message.endswith("The quote is in paper.txt only as part of a longer word or number.")
    sha = hashlib.sha256(Path("paper.txt").read_bytes()).hexdigest()
    as_an_earlier_rb_wrote_it(human, ".rb/experiments/e1/experiment.yaml", "spec", lambda doc: doc["settings"].append(
        {"name": "batch_then", "value": 3, "status": "verified", "required": True, "per_run": False,
         "source": {"kind": "file", "path": "paper.txt", "quote": "batch size of 3", "term": "batch size",
                    "resolved": {"at": "2026-09-01T10:00:00.000+00:00", "sha256": sha, "line": 1, "text": "We use a batch size of 32 per GPU."}},
         "set_by": "human:t", "set_at": "2026-09-01T10:00:00.000+00:00", "via": "cli"}))
    [then] = [c for c in ok(capsys, "show", "e1").data["caveats"] if c["subject"] == "e1/batch_then"]
    assert (then["code"], then["problem"], then["text"]) == ("stale", True, 'batch_then: paper.txt "batch size of 3" is unchanged since an earlier rb '
                                                             "verified it, and this rb reads the quote otherwise: the quote is in paper.txt only as "
                                                             "part of a longer word or number; paper.txt:1 reads: We use a batch size of 32 per GPU.")
    assert spec_set(capsys, "e1", "momentum", "0.9", "--source", "paper.txt", "--quote", "momentum: 0.9") == (0, "verified")
    Path("paper.txt").write_text(Path("paper.txt").read_text(encoding="utf-8").replace("momentum: 0.9", "- momentum: 0.95"), encoding="utf-8")
    assert caveats(capsys, "e1/momentum") == [("stale", True)]
    assert rbj(capsys, "spec", "verify", "e1", "momentum")[0] == 1


@pytest.mark.parametrize("file,text,source,value", [
    ("train.py", "lr = 0.0001\n", "train.py:1", "1e-4"),
    ("cfg.yaml", "lr: 0.0001\n", "cfg.yaml#lr", "1e-4"),
    ("cfg.yaml", "lr: 1e-4\n", "cfg.yaml#lr", "0.0001"),
])
def test_scientific_notation_states_the_same_number(human, capsys, file, text, source, value):
    start(capsys)
    Path(file).write_text(text, encoding="utf-8")
    assert spec_set(capsys, "e1", "lr", value, "--source", source) == (0, "verified")


@pytest.mark.parametrize("name,line,value", [
    ("backbone", "backbone = 'resnet50'", "50"),
    ("seed", "seed = 1700000001", "1700000000"),
    ("optimizer", "optimizer = 'adamw'", "adam"),
    ("betas", "betas = [0.999, 0.9]", "[0.9, 0.999]"),
    ("batch", "batch = 16", '"6"'),
])
def test_a_near_miss_does_not_verify(human, capsys, name, line, value):
    start(capsys)
    Path("train.py").write_text(line + "\n", encoding="utf-8")
    code, status = spec_set(capsys, "e1", name, value, "--source", "train.py:1")
    assert (code, status) == (1, "provisional")


@pytest.mark.parametrize("name,line,value", [
    ("seed", "seed = 1700000001", "1700000001"),
    ("optimizer", "optimizer = 'adam'", "adam"),
    ("betas", "betas = [0.9, 0.999]", "[0.9, 0.999]"),
])
def test_the_exact_value_verifies(human, capsys, name, line, value):
    start(capsys)
    Path("train.py").write_text(line + "\n", encoding="utf-8")
    assert spec_set(capsys, "e1", name, value, "--source", "train.py:1") == (0, "verified")


@pytest.mark.parametrize("file,text,source", [
    ("consts.py", "NUM_STEPS = 48_200\n", ["consts.py:1", "--term", "NUM_STEPS"]),
    ("paper.tex", "Training runs for 48{,}200 steps.\n", ["paper.tex", "--quote", "for 48{,}200 steps", "--term", "steps"]),
    ("paper.tex", "Training runs for 48\\,200 steps.\n", ["paper.tex:1", "--term", "steps"]),
])
def test_digit_separators_state_the_number_they_group(human, capsys, file, text, source):
    """48_200 (Python, Rust, TOML), 48{,}200 and 48\\,200 (LaTeX) state 48200, as 48,200 does: never 48 or 200, so the
    check neither fails nor keeps a conflict on the setting."""
    start(capsys)
    Path(file).write_text(text, encoding="utf-8")
    code, env = rbj(capsys, "spec", "set", "e1", "steps", "48200", "--source", *source)
    assert (code, env.data["object"]["status"], env.data["object"]["conflict"]) == (0, "verified", None)
    assert spec_set(capsys, "e1", "steps", "48", "--source", *source) == (1, "provisional")


def test_an_escaped_underscore_names_the_setting(human, capsys):
    """LaTeX and Markdown write seq_len as seq\\_len: the line names the setting, and its number is read beside it."""
    start(capsys)
    Path("paper.tex").write_text("Every run uses a seq\\_len of 512 tokens and the adamw\\_8bit optimizer.\n", encoding="utf-8")
    assert spec_set(capsys, "e1", "seq_len", "512", "--source", "paper.tex:1") == (0, "verified")
    assert ok(capsys, "spec", "set", "e1", "train.seq_len", "--source", "paper.tex:1").data["object"]["value"] == 512
    assert spec_set(capsys, "e1", "optimizer", "adamw_8bit", "--source", "paper.tex:1") == (0, "verified")


def test_a_yaml_line_is_recorded_as_its_key_path(human, capsys):
    start(capsys)
    Path("cfg.yaml").write_text("optim:\n  lr: 0.0001\n", encoding="utf-8")
    env = ok(capsys, "spec", "set", "e1", "lr", "0.0001", "--source", "cfg.yaml:2")
    src = env.data["object"]["source"]
    assert env.data["object"]["status"] == "verified" and (src["key"], src["line"]) == ("optim.lr", None)


def test_a_yaml_line_for_a_different_key_does_not_verify(human, capsys):
    """weight_decay: 0.0001 happens to hold lr's value; the line does not set lr."""
    start(capsys)
    Path("cfg.yaml").write_text("lr: 0.1\nweight_decay: 0.0001\n", encoding="utf-8")
    code, status = spec_set(capsys, "e1", "lr", "0.0001", "--source", "cfg.yaml:2")
    assert (code, status) == (1, "provisional")


def test_a_line_for_a_different_setting_does_not_verify(human, capsys):
    start(capsys)
    Path("train.py").write_text("weight_decay = 0.0001\n", encoding="utf-8")
    assert spec_set(capsys, "e1", "lr", "0.0001", "--source", "train.py:1") == (1, "provisional")


# ---------------------------------------------------------------- conflicts and re-checking


def test_a_source_stating_another_value_records_a_conflict(human, capsys):
    start(capsys)
    Path("cfg.yaml").write_text("lr: 0.1\n", encoding="utf-8")
    code, env = rbj(capsys, "spec", "set", "e1", "lr", "0.2", "--source", "cfg.yaml#lr")
    s = env.data["object"]
    assert code == 1 and s["status"] == "provisional" and s["conflict"] is not None
    assert env.data["outcome"]["passed"] is False


def test_reverifying_does_not_clear_a_conflict(human, capsys):
    start(capsys)
    Path("cfg.yaml").write_text("lr: 0.1\n", encoding="utf-8")
    rbj(capsys, "spec", "set", "e1", "lr", "0.2", "--source", "cfg.yaml#lr")
    code, env = rbj(capsys, "spec", "verify", "e1", "lr")
    assert code == 1 and env.data["results"][0]["after"] == "provisional"
    assert setting(capsys, "e1/lr")["conflict"] is not None
    assert ("conflict", True) in caveats(capsys, "e1/lr")


def test_a_check_that_finds_something_else_replaces_the_conflict_kept_before(human, capsys):
    """A kept conflict says what the source stated when rb last checked it. Checked again once the line no longer names
    the setting, rb says that, and keeps no conflict that quotes a line the file no longer has; checked once the source
    states the value, the setting is verified."""
    start(capsys)
    Path("train.py").write_text("lr = 0.1\n", encoding="utf-8")
    assert spec_set(capsys, "e1", "lr", "0.2", "--source", "train.py:1") == (1, "provisional")
    assert setting(capsys, "e1/lr")["conflict"]["text"] == "the record says 0.2, but train.py:1 says 0.1; it reads: lr = 0.1"
    Path("train.py").write_text("batch = 4\n", encoding="utf-8")
    code, env = rbj(capsys, "spec", "verify", "e1", "lr")
    [row] = env.data["results"]
    assert code == 1 and row["conflict"] is False and row["reason"].startswith("train.py:1 does not name 'lr'; it reads: batch = 4.")
    s = setting(capsys, "e1/lr")
    assert s["conflict"] is None and s["failed"] == row["reason"] and caveats(capsys, "e1/lr") == [("provisional", False)]
    assert main(["show", "e1/lr"]) == 0 and "CONFLICT" not in capsys.readouterr().out
    Path("train.py").write_text("lr = 0.2\n", encoding="utf-8")
    assert ok(capsys, "spec", "verify", "e1", "lr").data["results"][0]["after"] == "verified"
    assert setting(capsys, "e1/lr")["status"] == "verified" and caveats(capsys, "e1/lr") == []


def test_a_check_that_cannot_read_the_source_keeps_the_conflict(human, capsys):
    """A check that finds the source gone learns nothing of what it states: the conflict stays, every read shows it, and
    the check says why it could not verify. Setting the same value from the same source keeps it too, checked or not.
    Back with yet another value, the source is checked and that is the conflict."""
    start(capsys)
    Path("cfg.yaml").write_text("lr: 0.1\n", encoding="utf-8")
    assert spec_set(capsys, "e1", "lr", "0.2", "--source", "cfg.yaml#lr") == (1, "provisional")
    kept = setting(capsys, "e1/lr")["conflict"]
    assert kept["read"] == 0.1
    Path("cfg.yaml").rename("away.yaml")
    code, env = rbj(capsys, "spec", "verify", "e1", "lr")
    [row] = env.data["results"]
    assert code == 1 and row["conflict"] is True and row["reason"] == "cfg.yaml does not exist"
    s = setting(capsys, "e1/lr")
    assert s["conflict"] == kept and s["failed"] == "cfg.yaml does not exist" and ("conflict", True) in caveats(capsys, "e1/lr")
    assert spec_set(capsys, "e1", "lr", "0.2", "--source", "cfg.yaml#lr") == (1, "provisional")
    assert setting(capsys, "e1/lr")["conflict"] == kept
    Path("away.yaml").rename("cfg.yaml")
    assert spec_set(capsys, "e1", "lr", "0.2", "--no-verify") == (0, "provisional")
    assert setting(capsys, "e1/lr")["conflict"] == kept and ("conflict", True) in caveats(capsys, "e1/lr")
    Path("cfg.yaml").write_text("lr: 0.3\n", encoding="utf-8")
    assert ("conflict", True) in caveats(capsys, "e1/lr")
    assert rbj(capsys, "spec", "verify", "e1", "lr")[0] == 1 and setting(capsys, "e1/lr")["conflict"]["read"] == 0.3


def test_a_setting_s_history_shows_every_check_of_it(human, capsys):
    """rb spec verify checks several settings in one write: each one's history, in rb show and rb log, has that check
    with its own outcome, so a check that passes after one that failed is the history's last word."""
    start(capsys)
    Path("cfg.yaml").write_text("lr: 0.1\nwd: 0.0\n", encoding="utf-8")
    assert spec_set(capsys, "e1", "lr", "0.2", "--source", "cfg.yaml#lr") == (1, "provisional")
    assert spec_set(capsys, "e1", "wd", "0.0", "--source", "cfg.yaml#wd") == (0, "verified")
    Path("cfg.yaml").write_text("lr: 0.2\nwd: 0.0\n", encoding="utf-8")
    assert rbj(capsys, "spec", "verify", "e1")[0] == 0
    history = ok(capsys, "show", "e1/lr").data["history"]
    assert [h["op"] for h in history] == ["spec_set", "spec_verify", "spec_verify"]
    assert main(["show", "e1/lr"]) == 0
    shown = [line.strip().split(" ", 1)[1] for line in capsys.readouterr().out.splitlines() if line.startswith("    20")]
    assert shown == ["spec set: setting lr: new, 0.2", "spec check: provisional", "spec check: verified"]
    assert main(["log", "e1/lr"]) == 0
    assert [line.split(" ", 2)[2] for line in capsys.readouterr().out.splitlines()] == [
        "spec set experiment e1: setting lr: new, 0.2", "spec check experiment e1: provisional", "spec check experiment e1: checked as stated"]


def test_a_verified_line_that_moves_is_shown_and_does_not_block(human, capsys):
    start(capsys)
    Path("train.py").write_text("lr = 0.1\n", encoding="utf-8")
    spec_set(capsys, "e1", "lr", "0.1", "--source", "train.py:1")
    Path("train.py").write_text("import os\nlr = 0.1\n", encoding="utf-8")
    assert caveats(capsys, "e1/lr") == [("source_changed", False)]


def test_a_verified_line_that_ends_in_a_comment_is_found_where_it_moved(human, capsys):
    """rb keeps a line it verified without the comment that ends it. A line added above it (a dependency in sorted
    order, an intro to a README, a row taken out of a paper's table) moves it, and it is found where it moved, still
    stating its value: never read off whatever line now has its old number, nor in conflict with a setting that line
    names."""
    start(capsys)
    files = {
        "requirements.txt": ("numpy==1.26.4\ntorch==2.4.1  # CUDA 12.1 build\n", "accelerate==0.30.1\nnumpy==1.26.4\ntorch==2.4.1  # CUDA 12.1 build\n"),
        "README.md": ("Train with:\n\n```yaml\nlr: 3e-4  # peak, after warmup\n```\n", "A small study.\n\nTrain with:\n\n```yaml\nlr: 3e-4  # peak, after warmup\n```\n"),
        "paper.tex": ("Learning rate & 3e-4 \\\\\nBatch size & 256 \\\\ % 8 GPUs x 32\nWarmup batch size & 64 \\\\\n",
                      "Batch size & 256 \\\\ % 8 GPUs x 32\nWarmup batch size & 64 \\\\\n"),
        "HP.md": ("**Optimizer:** adamw <!-- sgd diverged -->\n", "All runs share these.\n\n**Optimizer:** adamw <!-- sgd diverged -->\n"),
    }
    for path, (before, _) in files.items():
        Path(path).write_text(before, encoding="utf-8")
    for name, value, source in (("env.torch", "2.4.1", ["requirements.txt:2"]), ("lr", "3e-4", ["README.md:4"]),
                                ("batch_size", "256", ["paper.tex:2", "--term", "batch size"]), ("optimizer", "adamw", ["HP.md:1"])):
        assert spec_set(capsys, "e1", name, value, "--source", *source) == (0, "verified"), name
    for path, (_, after) in files.items():
        Path(path).write_text(after, encoding="utf-8")
    for name in ("env.torch", "lr", "batch_size", "optimizer"):
        assert caveats(capsys, f"e1/{name}") == [("source_changed", False)], name
    assert rbj(capsys, "status", "--fail-on", "stale")[0] == 0
    code, env = rbj(capsys, "spec", "verify", "e1")
    assert code == 0 and [r["after"] for r in env.data["results"]] == ["verified"] * 4


def test_a_verified_source_that_no_longer_states_the_value_is_stale_and_blocks(human, capsys):
    start(capsys)
    Path("train.py").write_text("lr = 0.1\n", encoding="utf-8")
    spec_set(capsys, "e1", "lr", "0.1", "--source", "train.py:1")
    Path("train.py").write_text("batch = 4\n", encoding="utf-8")
    assert caveats(capsys, "e1/lr") == [("stale", True)]
    assert rbj(capsys, "status", "--fail-on", "stale")[0] == 1


def test_a_verified_source_that_now_states_another_value_is_a_conflict(human, capsys):
    start(capsys)
    Path("cfg.yaml").write_text("lr: 0.1\n", encoding="utf-8")
    spec_set(capsys, "e1", "lr", "0.1", "--source", "cfg.yaml#lr")
    Path("cfg.yaml").write_text("lr: 0.3\n", encoding="utf-8")
    assert caveats(capsys, "e1/lr") == [("conflict", True)]


def test_a_verified_population_or_rejected_setting_is_re_read_too(human, capsys):
    """Declaring population.* is never work to do, and a rejected value is a problem already; a verified one of either
    is still read against its source again, and a file that now states another value is a conflict as well."""
    start(capsys)
    Path("cfg.yaml").write_text("photos: 2\nwd: 0.01\n", encoding="utf-8")
    spec_set(capsys, "e1", "population.photos", "2", "--source", "cfg.yaml#photos")
    spec_set(capsys, "e1", "wd", "0.01", "--source", "cfg.yaml#wd")
    ok(capsys, "decide", "e1/wd", "reject", "--why", "not the one we ran")
    assert caveats(capsys, "e1/population.photos") == []
    assert caveats(capsys, "e1/wd") == [("rejected_setting", True)]
    Path("cfg.yaml").write_text("photos: 5\nwd: 0.02\n", encoding="utf-8")
    assert caveats(capsys, "e1/population.photos") == [("conflict", True)]
    assert caveats(capsys, "e1/wd") == [("rejected_setting", True), ("conflict", True)]
    assert rbj(capsys, "status", "--fail-on", "stale")[0] == 1


def test_giving_a_verified_setting_its_value_again_checks_the_source_on_the_spot(human, capsys):
    """rb spec set keeps a verified setting verified while its source states the value; once the file says otherwise,
    the same value given again, with its source or with none, is checked as any new value is."""
    start(capsys)
    Path("cfg.yaml").write_text("lr: 0.1\nwd: 0.01\n", encoding="utf-8")
    spec_set(capsys, "e1", "lr", "0.1", "--source", "cfg.yaml#lr")
    spec_set(capsys, "e1", "wd", "0.01", "--source", "cfg.yaml#wd")
    assert spec_set(capsys, "e1", "lr", "0.1", "--source", "cfg.yaml#lr") == (0, "verified")
    Path("cfg.yaml").write_text("lr: 0.3\n", encoding="utf-8")
    assert spec_set(capsys, "e1", "lr", "0.1", "--source", "cfg.yaml#lr") == (1, "provisional")
    assert setting(capsys, "e1/lr")["conflict"]["text"] == "the record says 0.1, but cfg.yaml#lr now says 0.3"   # it said 0.1
    for _ in range(2):                      # checked again, the conflict on record still says the source changed
        assert rbj(capsys, "spec", "verify", "e1", "lr")[0] == 1
        assert setting(capsys, "e1/lr")["conflict"]["text"] == "the record says 0.1, but cfg.yaml#lr now says 0.3"
    assert spec_set(capsys, "e1", "wd", "0.01", "--optional") == (1, "provisional")
    assert caveats(capsys, "e1/wd") == [("provisional", False)]


@pytest.mark.parametrize("path,source,before,after,said", [
    ("cfg.yaml", ["cfg.yaml#lr"], "lr: 0.1\n", "lr: 0.3\n", "the record says 0.1, but cfg.yaml#lr now says 0.3"),
    ("out/record.json", ["run:out/record.json#/lr"], '{"lr": 0.1}', '{"lr": 0.3}', "the record says 0.1, but out/record.json#/lr now says 0.3"),
    ("train.py", ["train.py:1"], "lr = 0.1\n", "lr = 0.3\n", "the record says 0.1, but train.py:1 now says 0.3; it reads: lr = 0.3"),
    ("notes.md", ["notes.md:1"], "the lr is 0.1\n", "the lr is 0.3, down from 0.1\n",
     "the record says 0.1, but notes.md:1 now says 0.3; it reads: the lr is 0.3, down from 0.1"),
    ("train.py", ["train.py", "--quote", "lr = 0.1"], "lr = 0.1\n", "lr = 0.3  # was lr = 0.1\n",     # the line the quote is on
     "the record says 0.1, but train.py:1 now says 0.3; it reads: lr = 0.3"),
    ("cfg.yaml", ["cfg.yaml#lr"], "lr: 0.1\n", 'lr: "0.1"\n', 'the record says 0.1, but cfg.yaml#lr now says "0.1"'),
    ("out/record.json", ["run:out/record.json#/lr"], '{"lr": 0.1}', '{"lr": "0.1"}', 'the record says 0.1, but out/record.json#/lr now says "0.1"'),
])
def test_a_conflict_names_its_source_and_both_values_once(human, capsys, path, source, before, after, said):
    """What a source now states is said once, after the value the record holds; a line keeps where it is and what it
    reads. Text and a number that would read alike are told apart by quoting them. rb status names the setting by its
    address alone. rb spec verify says the conflict it keeps on the setting the same way, and so does every read after."""
    start(capsys)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(before, encoding="utf-8")
    assert spec_set(capsys, "e1", "lr", "0.1", "--source", *source) == (0, "verified")
    Path(path).write_text(after, encoding="utf-8")
    for kept in (False, True):
        if kept:
            code, env = rbj(capsys, "spec", "verify", "e1", "lr")
            assert code == 1 and env.data["results"][0]["reason"] == said
            assert ok(capsys, "show", "e1/lr").data["object"]["conflict"]["text"] == said
        [c] = [c for c in ok(capsys, "show", "e1").data["caveats"] if c["subject"] == "e1/lr"]
        assert (c["code"], c["text"]) == ("conflict", f"lr: {said}")
        [item] = [i for i in ok(capsys, "status").data["open"] if i["subject"] == "e1/lr"]
        assert (item["code"], item["what"]) == ("conflict", f"e1/lr: {said}")


def test_a_quote_in_the_comment_that_ends_a_line_of_code_reads_that_code_on_every_read(human, capsys):
    """A quote found only in the comment that ends a line of code reads the code it ends, on every read as on rb spec
    set: the value that code states stays verified, so the command rb status gives for the conflict, run with that
    value, leaves nothing stale."""
    start(capsys)
    Path("train.py").write_text("lr = 0.1\n", encoding="utf-8")
    assert spec_set(capsys, "e1", "lr", "0.1", "--source", "train.py", "--quote", "lr = 0.1") == (0, "verified")
    Path("train.py").write_text("lr = 0.3  # was lr = 0.1\n", encoding="utf-8")
    [item] = [i for i in ok(capsys, "status").data["open"] if i["subject"] == "e1/lr"]
    assert (item["code"], item["do"]) == ("conflict", "rb spec set e1 lr <the value the source states> --source train.py --quote 'lr = 0.1'")
    assert spec_set(capsys, "e1", "lr", "0.3", "--source", "train.py", "--quote", "lr = 0.1") == (0, "verified")
    assert caveats(capsys, "e1/lr") == [] and rbj(capsys, "status", "--fail-on", "stale")[0] == 0
    assert rbj(capsys, "spec", "verify", "e1", "lr")[0] == 0 and caveats(capsys, "e1/lr") == []


@needs_git
def test_a_commit_pinned_source_does_not_go_stale_when_the_working_tree_changes(human, capsys):
    Path("cfg.yaml").write_text("lr: 0.1\n", encoding="utf-8")
    git("add", "cfg.yaml")
    git("commit", "-qm", "config")
    head = git("rev-parse", "HEAD")
    start(capsys)
    assert spec_set(capsys, "e1", "lr", "0.1", "--source", "cfg.yaml#lr", "--commit", head) == (0, "verified")
    Path("cfg.yaml").write_text("lr: 0.5\n", encoding="utf-8")
    assert caveats(capsys, "e1/lr") == []
    os.remove("cfg.yaml")
    assert caveats(capsys, "e1/lr") == []


@needs_git
def test_a_hand_made_resolution_of_a_commit_pinned_source_does_not_pass_the_recheck(human, capsys):
    """The setting is rewritten by hand to say 0.2, and its resolution to say the file read 0.2, with the right hash;
    the file at that commit says 0.1. The setting is read against the file again, so it does not stand as verified, and
    an adoption cannot make the forged reading rb's."""
    Path("cfg.yaml").write_text("lr: 0.1\n", encoding="utf-8")
    git("add", "cfg.yaml")
    git("commit", "-qm", "config")
    head = git("rev-parse", "HEAD")
    start(capsys)
    assert spec_set(capsys, "e1", "lr", "0.1", "--source", "cfg.yaml#lr", "--commit", head) == (0, "verified")
    p = experiment_file(human)
    doc = rbyaml.loads(p.read_text(encoding="utf-8"))
    [lr] = [x for x in doc["settings"] if x["name"] == "lr"]
    assert lr["source"]["resolved"]["read"] == 0.1 and lr["source"]["resolved"]["commit"] == head
    lr["value"] = 0.2
    lr["source"]["resolved"].update(text="lr: 0.2", read=0.2)                 # the hash of the file's bytes stays as it was
    p.write_text(rbyaml.dumps(doc), encoding="utf-8")
    [caveat] = [c for c in ok(capsys, "show", "e1").data["caveats"] if c["subject"] == "e1/lr"]
    assert caveat["code"] in ("stale", "conflict") and caveat["problem"] is True
    assert "does not state 0.2" in caveat["text"] and "was not made by rb" in caveat["text"]
    # and what rb read is not anyone's to rewrite: the file is not taken into the record as it stands
    code, env = rbj(capsys, "doctor", "--adopt", "-m", "taken as it stands")
    assert code == 2 and env.errors[0].code == "E_ADOPT_AUTHORITY"
    assert any(pr.startswith("e1.settings[lr].source.resolved: ") for pr in env.errors[0].model_dump()["problems"])


def as_an_earlier_rb_wrote_it(root: Path, rel: str, op: str, change) -> None:
    """Change a record the way an earlier rb wrote it: its file, and the log line every write gets."""
    import json
    from datetime import datetime, timezone
    p = root / rel
    doc = rbyaml.loads(p.read_text(encoding="utf-8"))
    change(doc)
    p.write_text(rbyaml.dumps(doc), encoding="utf-8")
    entry = {"at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"), "actor": "human:t", "actor_via": "git-email",
             "via": "cli", "op": op, "kind": doc["kind"], "id": doc["id"], "path": rel,
             "sha256": rbyaml.content_sha({k: v for k, v in doc.items() if k not in ("rb", "kind")})}
    with (root / ".rb" / "log.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def test_what_an_earlier_rb_read_in_bytes_unchanged_since_stands(human, capsys):
    """rb reads a line inside a Markdown HTML comment and pip's trailing # as comments; an earlier rb read a line
    opening with no mark as text, cut nothing from a document, and read no comment in an analysis's source. What it
    verified, and the line it read an analysis's numbers off, stand on every read while the file's bytes have not
    changed: a setting's note says how this rb reads it now, no gate trips, and nothing says the record was not made by
    rb. A reading no rb would make of the same bytes still was not rb's, and once a file's bytes change it is read by
    this rb's comments."""
    start(capsys, "--baseline", "base", "--candidate", "cand")
    Path("NOTES.md").write_text("Training notes.\n<!--\nlr: 0.1 for the first runs\n-->\n", encoding="utf-8")
    Path("requirements.txt").write_text("torch==2.5.0  # 2.4.1 broke the export\n", encoding="utf-8")
    sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()

    def verified(name, value, path, line, text):
        return {"name": name, "value": value, "status": "verified", "required": True, "per_run": False,
                "source": {"kind": "file", "path": path, "line": line,
                           "resolved": {"at": "2026-09-01T10:00:00.000+00:00", "sha256": sha(path), "line": line, "text": text}},
                "set_by": "human:t", "set_at": "2026-09-01T10:00:00.000+00:00", "via": "cli"}

    exp = ".rb/experiments/e1/experiment.yaml"
    as_an_earlier_rb_wrote_it(human, exp, "spec", lambda doc: doc.setdefault("settings", []).extend([
        verified("lr", 0.1, "NOTES.md", 3, "lr: 0.1 for the first runs"),
        verified("env.torch", "2.4.1", "requirements.txt", 1, "torch==2.5.0  # 2.4.1 broke the export")]))
    Path("EVAL.md").write_text("Epoch 89, loss 0.91\n\nAcc@1 76.130 Acc@5 92.862\n", encoding="utf-8")
    ok(capsys, "criterion", "add", "s", "-e", "e1", "--metric", "cand.acc1", "--at-least", "76", "--id", "c1")
    a = ok(capsys, "analysis", "add", "e1", "--compare", "cand:acc1=76.13", "--source", "EVAL.md", "--method", "val").data["object"]["id"]
    Path("EVAL.md").write_text("Epoch 89, loss 0.91\n\n<!-- Acc@1 76.130 Acc@5 92.862 -->\n", encoding="utf-8")
    as_an_earlier_rb_wrote_it(human, f".rb/experiments/e1/analyses/{a}.yaml", "analyze", lambda doc: doc["comparisons"][0]["source"]["resolved"].update(
        sha256=sha("EVAL.md"), text="<!-- Acc@1 76.130 Acc@5 92.862 -->"))
    assert rbj(capsys, "status", "--fail-on", "stale")[0] == 0 and rbj(capsys, "status", "--fail-on", "integrity")[0] == 0
    notes_ = {c["subject"]: c for c in ok(capsys, "show", "e1").data["caveats"]}
    for subject, line in (("e1/lr", "NOTES.md:3"), ("e1/env.torch", "requirements.txt:1")):
        assert (notes_[subject]["code"], notes_[subject]["problem"]) == ("source_changed", False)
        assert f"{line} is unchanged since an earlier rb verified it, and this rb reads comments otherwise: " in notes_[subject]["text"]
    assert problems(finding(capsys)) == []

    as_an_earlier_rb_wrote_it(human, exp, "spec", lambda doc: doc["settings"].append(verified("batch", 64, "NOTES.md", 1, "batch 64")))
    [forged] = [c for c in ok(capsys, "show", "e1").data["caveats"] if c["subject"] == "e1/batch"]
    assert forged["problem"] and "the record of what was read was not made by rb" in forged["text"]
    with Path("EVAL.md").open("a", encoding="utf-8") as f:
        f.write("done\n")
    assert f"{a}'s numbers: EVAL.md:3 is a comment, and a comment states nothing" in [p["text"] for p in finding(capsys)["integrity"]]


# ---------------------------------------------------------------- confounds


def test_an_undeclared_difference_between_variants_is_a_blocking_confound(human, capsys):
    start(capsys, "--baseline", "fp32", "--candidate", "int8", "--varies", "precision")
    ok(capsys, "spec", "set", "e1", "lr", "0.1")
    ok(capsys, "spec", "set", "e1", "int8.lr", "0.2")
    assert ("confound", True) in caveats(capsys, "e1/lr")


def test_a_difference_declared_with_varies_is_not_a_confound(human, capsys):
    start(capsys, "--baseline", "fp32", "--candidate", "int8", "--varies", "precision")
    ok(capsys, "spec", "set", "e1", "fp32.precision", "fp32")
    ok(capsys, "spec", "set", "e1", "int8.precision", "int8")
    assert not [c for c in ok(capsys, "show", "e1").data["caveats"] if c["code"].startswith("confound")]


def test_a_confound_a_human_accepts_no_longer_blocks(human, capsys):
    start(capsys, "--baseline", "fp32", "--candidate", "int8", "--varies", "precision")
    ok(capsys, "spec", "set", "e1", "lr", "0.1")
    ok(capsys, "spec", "set", "e1", "int8.lr", "0.2")
    ok(capsys, "decide", "e1/lr", "accept", "-m", "int8 needs the higher rate; it is part of the recipe")
    assert ("confound_accepted", False) in caveats(capsys, "e1/lr") and ("confound", True) not in caveats(capsys, "e1/lr")


def test_a_confound_between_the_compared_arms_is_an_integrity_problem_of_the_finding(human, capsys):
    """bs differs between fp32 and int8, and e1 varies only precision. c1 compares int8 with fp32 (change.val_loss): its rule
    still reads, and its integrity names the confound. c2 reads int8's level alone, which compares no arms, so the
    difference is not a confound for it."""
    start(capsys, "--baseline", "fp32", "--candidate", "int8", "--varies", "precision")
    ok(capsys, "spec", "set", "e1", "fp32.bs", "8", "--optional")
    ok(capsys, "spec", "set", "e1", "int8.bs", "16", "--optional")
    ok(capsys, "criterion", "add", "s", "-e", "e1", "--metric", "change.val_loss", "--at-most", "1", "--id", "c1")
    ok(capsys, "criterion", "add", "s", "-e", "e1", "--metric", "int8.val_loss", "--at-most", "1", "--id", "c2")
    record(capsys, "int8.val_loss=0.5", "fp32.val_loss=0.4")
    f = finding(capsys)
    assert f["rule_outcome"] == "meets" and problems(f) == [("confound", "e1/bs")]
    assert "undeclared difference: bs" in f["says"]["integrity"]
    level = finding(capsys, "c2")
    assert level["rule_outcome"] == "meets" and level["integrity"] == []


def test_a_comparison_held_at_one_value_of_a_varied_setting_is_not_said_to_vary_nothing(human, capsys):
    """e1 varies width and loops, and c1 compares two loop counts at one width: holding width fixed is how that
    comparison is made, so nothing says every variant has the same width while a third variant has another. Once every
    variant has the same width, the experiment and the finding say so."""
    start(capsys, "--baseline", "r2", "--candidate", "r4", "--varies", "width,loops")
    ok(capsys, "variant", "add", "e1", "wide", "--role", "candidate")
    for v, width, loops in (("r2", "256", "2"), ("r4", "256", "4"), ("wide", "512", "4")):
        ok(capsys, "spec", "set", "e1", f"{v}.width", width, "--no-verify")
        ok(capsys, "spec", "set", "e1", f"{v}.loops", loops, "--no-verify")
    ok(capsys, "criterion", "add", "s", "-e", "e1", "--metric", "ppl", "--subject", "r4", "--reference", "r2", "--at-most", "0", "--id", "c1")
    record(capsys, "r2.ppl=20.5", "r4.ppl=20.3")
    assert notes(finding(capsys), "varies_unchanged") == [] and ("varies_unchanged", False) not in caveats(capsys, "e1/width")
    ok(capsys, "spec", "set", "e1", "wide.width", "256", "--no-verify")
    assert notes(finding(capsys), "varies_unchanged") == [("e1/width", False)] and ("varies_unchanged", False) in caveats(capsys, "e1/width")


# ---------------------------------------------------------------- freezing and amending


@pytest.mark.parametrize("argv", [
    ["spec", "set", "e1", "lr", "0.2"],
    ["criterion", "add", "later", "-e", "e1", "--metric", "top1", "--at-least", "0.8"],
    ["variant", "add", "e1", "v2", "--role", "candidate"],
])
def test_a_frozen_spec_refuses_changes_without_amend(human, capsys, argv):
    start(capsys)
    ok(capsys, "spec", "set", "e1", "lr", "0.1")
    ok(capsys, "freeze", "e1", "-m", "locked")
    code, env = rbj(capsys, *argv)
    assert code == 2 and env.errors[0].code == "E_FROZEN"


def test_a_humans_amendment_is_kept_with_the_hashes_before_and_after(human, capsys):
    start(capsys)
    ok(capsys, "spec", "set", "e1", "lr", "0.1")
    frozen = ok(capsys, "freeze", "e1", "-m", "locked").data["object"]["frozen"]
    ok(capsys, "spec", "set", "e1", "lr", "0.2", "--amend", "-m", "the spec had a typo")
    shown = ok(capsys, "show", "e1").data
    [am] = shown["object"]["amendments"]
    assert (am["by"], am["why"], am["before"], am["after"]) == ("human:t", "the spec had a typo", frozen["sha256"], shown["freeze_sha256"])
    assert "0.1 -> 0.2" in am["change"] and shown["spec_drift"] is None
    logged = state(human).log(None)[-1]["detail"]["amendment"]
    assert (logged["before"], logged["after"], logged["why"]) == (am["before"], am["after"], am["why"])


def test_amend_with_nothing_to_amend_is_refused(human, capsys):
    start(capsys)
    code, env = rbj(capsys, "spec", "set", "e1", "lr", "0.1", "--amend", "-m", "why not")
    assert code == 2 and env.errors[0].code == "E_OBJECT_INVALID"


def test_a_frozen_spec_edited_by_hand_is_drift_and_blocks(human, capsys):
    start(capsys)
    ok(capsys, "spec", "set", "e1", "lr", "0.1")
    ok(capsys, "decide", "e1/lr", "accept", "-m", "checked")
    ok(capsys, "criterion", "add", "s", "-e", "e1", "--metric", "top1", "--at-least", "0.7", "--id", "c1")
    ok(capsys, "freeze", "e1", "-m", "locked")
    record(capsys, "top1=0.76")
    p = experiment_file(human)
    doc = rbyaml.loads(p.read_text(encoding="utf-8"))
    doc["settings"][0]["value"] = 0.5
    p.write_text(rbyaml.dumps(doc), encoding="utf-8")
    assert ok(capsys, "show", "e1").data["spec_drift"]
    assert ("drift", "e1") in problems(finding(capsys))
    assert rbj(capsys, "status", "--fail-on", "stale")[0] == 1


def test_a_criterions_rule_edited_after_the_freeze_is_drift(human, capsys):
    start(capsys)
    ok(capsys, "criterion", "add", "s", "-e", "e1", "--metric", "top1", "--at-least", "0.7", "--id", "c1")
    ok(capsys, "freeze", "e1", "-m", "locked")
    edit_criterion(human, "c1", target=0.5)
    assert ok(capsys, "show", "e1").data["spec_drift"]


# ---------------------------------------------------------------- which runs count, and the timing


def test_a_run_recorded_before_the_criterion_counts_and_the_timing_says_so(human, capsys):
    """Evidence that existed before the criterion is read, never hidden: the run counts, the timing says the criterion
    was declared after the evidence, and a primary criterion declared late fails the late gate."""
    start(capsys)
    run = record(capsys, "top1=0.76")
    ok(capsys, "criterion", "add", "s", "-e", "e1", "--metric", "top1", "--at-least", "0.7", "--id", "c1")
    f = finding(capsys)
    assert counted(f) == [(run, True, True)]
    assert (f["rule_outcome"], f["timing"]) == ("meets", "after_evidence")
    assert "after the evidence existed" in f["says"]["timing"]
    assert rbj(capsys, "status", "--fail-on", "late")[0] == 1


def test_the_timing_counts_the_units_the_reading_rests_on_and_says_unpaired_runs_as_runs(human, capsys):
    """Three seeds of the candidate and one of the baseline pair on one seed, so the reading rests on 1 unit: the
    timing counts the only unit, never 3 of 1, and says the candidate's other two seeds as runs (a rule's timing is
    read on its subject's runs); the units line counts the runs its unit came from. Unpaired runs alone before the declaration are said the same way, and rb status says
    that every unit the reading rests on came after it. A run the paired reading leaves out is shown as not counted,
    and never read as the part produced after the declaration."""
    start(capsys, "--baseline", "base", "--candidate", "wide")
    ok(capsys, "spec", "set", "e1", "seed", "--per-run")
    lone = [record(capsys, f"wide.ppl={x}", "--set", f"seed={s}") for s, x in ((2, "31.3"), (3, "31.32"))]
    ok(capsys, "criterion", "add", "s", "-e", "e1", "--metric", "ppl", "--subject", "wide", "--reference", "base",
       "--at-most", "-0.16", "--id", "c2")
    pair = [record(capsys, f"{arm}.ppl={x}", "--set", "seed=1") for arm, x in (("base", "31.45"), ("wide", "30.07"))]
    ok(capsys, "criterion", "add", "s", "-e", "e1", "--metric", "ppl", "--subject", "wide", "--reference", "base",
       "--at-most", "-0.16", "--id", "c1")
    f = finding(capsys)
    assert [o["run"] for o in f["observations"] if o["before_declared"]] == lone + pair
    assert f["says"]["timing"] == ("declared after the evidence existed: the only unit was produced before it was declared, "
                                   f"and so were 2 unpaired runs ({lone[0]}, {lone[1]})")
    assert (f["units"]["n"], f["units"]["runs"], f["says"]["units"]) == (1, 2, "1 seed from 2 runs")
    early = finding(capsys, "c2")
    assert early["timing"] == "after_evidence" and early["says"]["timing"].startswith(
        f"declared after the evidence existed: 0 of 1 units were produced before it was declared, but 2 unpaired runs were "
        f"({lone[0]}, {lone[1]}); produced after it: -1.38 ")
    later = [record(capsys, f"{arm}.ppl={x}", "--set", f"seed={s}") for arm, x, s in (("base", "31.4", 4), ("wide", "30.3", 5))]
    f = finding(capsys)
    assert f["prospective"] is None and f["says"]["timing"].endswith(f"and so were 2 unpaired runs ({lone[0]}, {lone[1]})")
    assert {o["run"]: (o["counted"], o["reason"]) for o in f["observations"] if o["run"] in later} == {
        later[0]: (False, "its unit has no wide run, so the paired reading leaves it out"),
        later[1]: (False, "its unit has no base run, so the paired reading leaves it out")}
    open_ = ok(capsys, "status").data["open"]
    late = {o["subject"]: o["what"] for o in open_ if o["code"] == "late"}
    # a late criterion with no reading of its own is folded into its experiment's one item, which asks for its subject's runs after the declaration
    assert late["e1"] == "e1: 1 criterion was declared after its evidence existed: a reading of its own needs its subject's runs made after the declaration"
    assert [o["criteria"] for o in open_ if o["code"] == "late" and o["subject"] == "e1"] == [["c1"]]
    assert late["c2"].startswith("c2 was declared after runs it could have been fitted to (")
    assert late["c2"].endswith(", though every unit its reading rests on came after it")


def test_a_run_the_paired_reading_leaves_out_moves_the_timing_only_when_known_to_come_before(human, capsys):
    """A run the paired reading leaves out (its unit has one arm, or a run of its unit gives the difference by itself)
    is not counted, counts toward no unit, and moves the timing only when it is known to have come before the
    declaration; a unit produced at a time nobody knows is never read as produced after it. An analysis that re-reads
    the runs the reading rests on says so, and units that are runs come from themselves and their repeats."""
    start(capsys, "--baseline", "base", "--candidate", "wide")
    ok(capsys, "spec", "set", "e1", "seed", "--per-run")
    ok(capsys, "metric", "add", "ppl", "--minimize")
    rule = ("--metric", "ppl", "--subject", "wide", "--reference", "base", "--at-most", "0")
    ok(capsys, "criterion", "add", "s", "-e", "e1", *rule, "--id", "c1")
    pair = [record(capsys, f"{arm}.ppl={x}", "--set", "seed=1") for arm, x in (("base", "10"), ("wide", "9"))]
    record(capsys, "base.ppl=10.2", "--set", "seed=2", "--produced-at", "unknown")
    f = finding(capsys)
    assert (f["timing"], f["says"]["units"]) == ("before", "1 seed from 2 runs")
    assert rbj(capsys, "status", "--fail-on", "late")[0] == 0
    ok(capsys, "analysis", "add", "e1", "--compare", "wide/base:ppl=-1[-1.5,-0.5]", "--method", "m", "--reads", ",".join(pair))
    assert finding(capsys)["says"]["history"].endswith("which re-reads these runs (no new runs)")

    ok(capsys, "experiment", "add", "x", "--id", "e2", "--baseline", "base", "--candidate", "wide")
    ok(capsys, "spec", "set", "e2", "seed", "--per-run")
    early = record(capsys, "wide.ppl=9.2", "--set", "seed=2", exp="e2")
    ok(capsys, "criterion", "add", "s", "-e", "e2", *rule, "--id", "c2")
    for arm, x in (("base", "10"), ("wide", "9")):
        record(capsys, f"{arm}.ppl={x}", "--set", "seed=1", "--produced-at", "unknown", exp="e2")
    f = finding(capsys, "c2")
    assert f["prospective"] is None and f["says"]["timing"] == (
        "declared after the evidence existed: 0 of 1 units were produced before it was declared "
        f"(when 1 was produced is unknown), but 1 unpaired run was ({early})")
    late = {o["subject"]: o["what"] for o in ok(capsys, "status").data["open"] if o["code"] == "late"}
    assert late["e2"] == "e2: 1 criterion was declared after its evidence existed: a reading of its own needs its subject's runs made after the declaration"

    ok(capsys, "experiment", "add", "x", "--id", "e3", "--baseline", "base", "--candidate", "wide")
    ok(capsys, "spec", "set", "e3", "seed", "--per-run")
    one = record(capsys, "wide.ppl=8.5", "--set", "seed=1", exp="e3")
    ok(capsys, "criterion", "add", "s", "-e", "e3", "--metric", "change.ppl", "--at-most", "0", "--id", "c3")
    both = record(capsys, "base.ppl=10", "wide.ppl=9", "--set", "seed=1", exp="e3")
    f = finding(capsys, "c3")
    assert counted(f) == [(one, False, True), (both, True, False)]
    assert f["observations"][0]["reason"] == "its unit has a run that gives the difference by itself, so the paired reading leaves it out"
    assert (f["units"]["runs"], f["says"]["units"]) == (1, "1 seed")
    assert f["says"]["timing"].startswith(f"declared after the evidence existed: 0 of 1 units were produced before it was declared, but 1 unpaired run was ({one}); produced after it: -1 ")

    ok(capsys, "experiment", "add", "x", "--id", "e4", "--baseline", "base", "--candidate", "wide")
    ok(capsys, "criterion", "add", "s", "-e", "e4", "--metric", "change.ppl", "--at-most", "0", "--id", "c4")
    first = record(capsys, "base.ppl=10", "wide.ppl=9", exp="e4")
    record(capsys, "base.ppl=10", "wide.ppl=9.1", "--repeats", first, exp="e4")
    record(capsys, "base.ppl=10.2", "wide.ppl=9.3", exp="e4")
    assert finding(capsys, "c4")["says"]["units"] == "2 runs, 1 repeat"


def test_a_run_recorded_before_the_declaration_came_before_it_whenever_it_was_produced(human, capsys):
    """Nobody may know when a run was produced (--produced-at unknown), but it was by the time it was recorded: one
    recorded before the criterion was declared came before the declaration, whether the paired reading leaves it out
    (its unit has one arm, or another run of its unit gives the difference by itself), it is under an earlier spec, or
    the reading counts it. The timing says so, as it does when no time is given, and the late gate fails."""
    unknown = ("--produced-at", "unknown")
    rule = ("--metric", "change.ppl", "--at-most", "0")
    after = (("20.2", "19.0"), ("20.1", "19.3"), ("20.0", "19.4"))
    start(capsys, "--baseline", "base", "--candidate", "cand")
    ok(capsys, "spec", "set", "e1", "seed", "--per-run")
    ok(capsys, "metric", "add", "ppl", "--minimize")
    lone = record(capsys, "cand.ppl=19.5", "--set", "seed=4", *unknown)
    ok(capsys, "criterion", "add", "s", "-e", "e1", *rule, "--id", "c1")
    for seed, (b, c) in enumerate(after, 1):
        record(capsys, f"base.ppl={b}", f"cand.ppl={c}", "--set", f"seed={seed}")
    f = finding(capsys)
    assert (lone, False, True) in counted(f) and f["timing"] == "after_evidence"
    assert f["says"]["timing"].startswith("declared after the evidence existed: 0 of 3 units were produced before it was "
                                          f"declared, but 1 unpaired run was ({lone}); produced after it: ")
    assert rbj(capsys, "status", "--fail-on", "late")[0] == 1

    ok(capsys, "experiment", "add", "x", "--id", "e2", "--baseline", "base", "--candidate", "cand")
    ok(capsys, "spec", "set", "e2", "seed", "--per-run")
    arm = record(capsys, "cand.ppl=19.5", "--set", "seed=1", *unknown, exp="e2")
    ok(capsys, "criterion", "add", "s", "-e", "e2", *rule, "--id", "c2")
    for seed, (b, c) in enumerate(after, 1):
        record(capsys, f"base.ppl={b}", f"cand.ppl={c}", "--set", f"seed={seed}", exp="e2")
    assert finding(capsys, "c2")["says"]["timing"].startswith(
        f"declared after the evidence existed: 0 of 3 units were produced before it was declared, but 1 unpaired run was ({arm})")

    ok(capsys, "experiment", "add", "x", "--id", "e3", "--baseline", "base", "--candidate", "cand")
    ok(capsys, "spec", "set", "e3", "seed", "--per-run")
    old = record(capsys, "base.ppl=20.0", "cand.ppl=19.0", "--set", "seed=9", *unknown, exp="e3")
    ok(capsys, "spec", "set", "e3", "optim.lr", "0.001", "--no-verify")
    ok(capsys, "criterion", "add", "s", "-e", "e3", *rule, "--id", "c3")
    for seed, (b, c) in enumerate(after, 1):
        record(capsys, f"base.ppl={b}", f"cand.ppl={c}", "--set", f"seed={seed}", exp="e3")
    f = finding(capsys, "c3")
    assert f["timing"] == "after_evidence" and f"{old} was recorded " in f["says"]["timing"]
    assert ", before it was declared, and is not counted (under an earlier spec: optim.lr unknown -> 0.001); produced after it: " in f["says"]["timing"]

    ok(capsys, "experiment", "add", "x", "--id", "e4", "--baseline", "base", "--candidate", "cand")
    ok(capsys, "spec", "set", "e4", "seed", "--per-run")
    first = record(capsys, "base.ppl=20.2", "cand.ppl=19.0", "--set", "seed=1", *unknown, exp="e4")
    ok(capsys, "criterion", "add", "s", "-e", "e4", *rule, "--id", "c4")
    for seed, (b, c) in enumerate(after[1:], 2):
        record(capsys, f"base.ppl={b}", f"cand.ppl={c}", "--set", f"seed={seed}", exp="e4")
    f = finding(capsys, "c4")
    assert (first, True, True) in counted(f)
    assert f["says"]["timing"].startswith("declared after the evidence existed: 1 of 3 units was produced before it was declared; produced after it: ")


def test_output_given_a_time_after_it_was_recorded_was_produced_by_then(human, capsys, monkeypatch):
    """A run or an analysis was produced by the time it was recorded, whatever later time it was given (a local time
    typed with no offset reads as UTC, hours ahead): recorded before the criterion was declared, it came before the
    declaration, and the late gate fails."""
    rule = ("--metric", "change.ppl", "--at-most", "0")
    monkeypatch.setenv("RB_TEST_NOW", "2026-10-01T10:00:00Z")
    start(capsys, "--baseline", "base", "--candidate", "cand")
    ok(capsys, "spec", "set", "e1", "seed", "--per-run")
    ok(capsys, "experiment", "add", "x", "--id", "e2", "--baseline", "base", "--candidate", "cand")
    monkeypatch.setenv("RB_TEST_NOW", "2026-10-01T11:00:00Z")
    for seed, (b, c) in enumerate((("20.2", "19.0"), ("20.1", "19.3"), ("20.0", "19.4")), 1):
        record(capsys, f"base.ppl={b}", f"cand.ppl={c}", "--set", f"seed={seed}", "--produced-at", f"2026-10-01T18:{seed}0")
    ok(capsys, "analysis", "add", "e2", "--compare", "cand/base:ppl=-0.9[-1.5,-0.3]", "--method", "bootstrap", "--produced-at", "2026-10-01T18:00")
    monkeypatch.setenv("RB_TEST_NOW", "2026-10-01T12:00:00Z")
    for i, e in enumerate(("e1", "e2"), 1):
        ok(capsys, "criterion", "add", "s", "-e", e, *rule, "--id", f"c{i}")
    f = finding(capsys)
    assert f["timing"] == "after_evidence" and f["says"]["timing"].startswith(
        "declared after the evidence existed: all 3 units were produced before it was declared")
    assert finding(capsys, "c2")["timing"] == "after_evidence"
    assert rbj(capsys, "status", "--fail-on", "late")[0] == 1


def test_two_units_read_as_both_and_one_as_the_only_unit(human, capsys, monkeypatch):
    """A reading on two units says both of them, never 'all 2' or '2 of 2'; on one, the only unit."""
    monkeypatch.setenv("RB_TEST_NOW", "2026-10-01T10:00:00Z")
    start(capsys, "--baseline", "base", "--candidate", "cand")
    ok(capsys, "spec", "set", "e1", "seed", "--per-run")
    monkeypatch.setenv("RB_TEST_NOW", "2026-10-01T11:00:00Z")
    record(capsys, "base.ppl=20.2", "cand.ppl=19.0", "--set", "seed=1")
    monkeypatch.setenv("RB_TEST_NOW", "2026-10-01T12:00:00Z")
    ok(capsys, "criterion", "add", "s", "-e", "e1", "--metric", "change.ppl", "--at-most", "0", "--each", "--id", "c1")
    f = finding(capsys)
    assert f["says"]["timing"].startswith("declared after the evidence existed: the only unit was produced before it was declared")
    assert "the only unit ≤ 0" in f["says"]["rule"]
    monkeypatch.setenv("RB_TEST_NOW", "2026-10-01T12:30:00Z")
    record(capsys, "base.ppl=20.1", "cand.ppl=19.3", "--set", "seed=2", "--produced-at", "2026-10-01T11:30")
    f = finding(capsys)
    assert f["says"]["timing"].startswith("declared after the evidence existed: both units were produced before it was declared")
    assert "both units ≤ 0" in f["says"]["rule"] and "all 2" not in repr(f) and "2 of 2" not in repr(f)


def test_retracting_a_run_the_timing_reads_says_it_rested_there_and_moves_the_timing(human, capsys):
    """A run the paired reading leaves out counts toward no unit, yet came before the declaration, so the timing reads
    it. Taking it back says the criterion read it in its timing, and that the finding moved: declared after the evidence
    existed, now before it, with the late gate passing."""
    start(capsys, "--baseline", "base", "--candidate", "cand")
    ok(capsys, "spec", "set", "e1", "seed", "--per-run")
    stray = record(capsys, "cand.ppl=19.5", "--set", "seed=0")
    ok(capsys, "criterion", "add", "s", "-e", "e1", "--metric", "change.ppl", "--at-most", "0", "--id", "c1")
    for seed, (b, c) in enumerate((("20.2", "19.0"), ("20.1", "19.3"), ("20.0", "19.4")), 1):
        record(capsys, f"base.ppl={b}", f"cand.ppl={c}", "--set", f"seed={seed}")
    assert rbj(capsys, "status", "--fail-on", "late")[0] == 1
    data = ok(capsys, "retract", stray, "--why", "a sanity check, not the comparison").data
    assert data["rested_on"] == ["criterion c1 reads it in its timing"]
    [change] = data["finding_changes"]
    assert change["criterion_id"] == "c1" and change["before"].endswith(" · declared after the evidence existed")
    assert change["after"].endswith(" · declared before the evidence")
    assert finding(capsys)["timing"] == "before" and rbj(capsys, "status", "--fail-on", "late")[0] == 0
    ok(capsys, "experiment", "add", "x", "--id", "e2")
    ok(capsys, "criterion", "add", "s", "-e", "e2", "--metric", "acc", "--at-least", "0.5", "--id", "c2")
    only = record(capsys, "acc=0.7", exp="e2")
    [change] = ok(capsys, "retract", only, "--why", "the wrong split").data["finding_changes"]
    assert change["after"] == "criterion untested · untested: acc: no reading yet"          # the reading gone, nothing more to say


def test_a_run_no_longer_counted_that_came_first_leaves_the_reading_produced_after_it(human, capsys):
    """A pilot whose config disagreed with the spec, a run under an earlier spec, or a measurement superseded since,
    recorded before the criterion was declared, is a run it could have been fitted to, and the late gate stays. Every
    unit the reading rests on came after it all the same: the timing gives that reading as produced after the
    declaration, and rb status says so, never that the reading is not prospective."""
    rule = ("--metric", "change.ppl", "--at-most", "0")
    Path("configs").mkdir()
    Path("configs/train.yaml").write_text("optim:\n  lr: 0.0003\n", encoding="utf-8")
    Path("pilot.yaml").write_text("optim:\n  lr: 0.001\n", encoding="utf-8")
    ok(capsys, "init", "integrity")
    for e in ("e1", "e2", "e3"):
        ok(capsys, "experiment", "add", "x", "--id", e, "--baseline", "base", "--candidate", "cand")
        ok(capsys, "spec", "set", e, "seed", "--per-run")
    ok(capsys, "spec", "set", "e1", "optim.lr", "--source", "configs/train.yaml#optim.lr")
    record(capsys, "base.ppl=20.3", "cand.ppl=19.9", "--set", "seed=0", "--config", "pilot.yaml")
    record(capsys, "base.ppl=20.3", "cand.ppl=19.9", "--set", "seed=0", exp="e2")
    ok(capsys, "spec", "set", "e2", "optim.lr", "0.001", "--no-verify")
    early = record(capsys, "base.ppl=20.3", "cand.ppl=19.9", "--set", "seed=0", exp="e3")
    ok(capsys, "retract", early, "--scope", "ppl", "--why", "the perplexity script read the wrong split")
    for i, e in enumerate(("e1", "e2", "e3"), 1):
        ok(capsys, "criterion", "add", "s", "-e", e, *rule, "--id", f"c{i}")
        for seed, (b, c) in enumerate((("20.2", "19.0"), ("20.1", "19.3"), ("20.0", "19.4")), 1):
            record(capsys, f"base.ppl={b}", f"cand.ppl={c}", "--set", f"seed={seed}", exp=e)
        f = finding(capsys, f"c{i}")
        read = lambda r: r and (r["estimate"], r["lo"], r["hi"], r["n"])
        assert f["timing"] == "after_evidence" and read(f["prospective"]) == read(f["comparison"]), e
        assert "; produced after it: -0.8667 [" in f["says"]["timing"], e
    late = {o["subject"]: o["what"] for o in ok(capsys, "status").data["open"] if o["code"] == "late"}
    for c in ("c1", "c2", "c3"):
        assert late[c].endswith(", though every unit its reading rests on came after it"), late[c]
    assert rbj(capsys, "status", "--fail-on", "late")[0] == 1


def test_a_finding_with_no_reading_yet_says_what_its_runs_are(human, capsys):
    """One arm alone gives no reading of a comparison: the notes on its runs still read every run it counts, so a run
    whose config was compared with the spec is never said to have none, and a run typed in is said as one."""
    start(capsys, "--baseline", "a", "--candidate", "b")
    ok(capsys, "spec", "set", "e1", "lr", "0.1", "--no-verify")
    Path("ran.yaml").write_text("lr: 0.1\n", encoding="utf-8")
    ok(capsys, "criterion", "add", "s", "-e", "e1", "--metric", "ppl", "--subject", "b", "--reference", "a", "--at-most", "0", "--id", "c1")
    run = record(capsys, "a.ppl=10", "--config", "ran.yaml")
    f = finding(capsys)
    codes = {c["code"]: c["text"] for c in f["caveats"]}
    assert f["comparison"] is None and "config_unchecked" not in codes and codes["typed"].endswith(run)


def test_for_an_agents_criterion_a_run_after_it_counts_even_before_the_freeze(human, capsys, monkeypatch):
    """An agent's criterion is declared when the agent writes it, attributed to the agent: a run after it is evidence
    produced after the declaration, and a human freezing the experiment later does not move the declaration."""
    start(capsys)
    monkeypatch.setenv("CLAUDECODE", "1")
    ok(capsys, "criterion", "add", "s", "-e", "e1", "--metric", "top1", "--at-least", "0.7", "--id", "c1")
    run = record(capsys, "top1=0.76")
    monkeypatch.delenv("CLAUDECODE")
    ok(capsys, "freeze", "e1", "-m", "locked")
    f = finding(capsys)
    assert counted(f) == [(run, True, False)] and f["timing"] == "before"
    assert (f["declared"]["by"], f["declared"]["frozen_by"]) == ("agent:claude-code", "human:t")


def test_for_a_humans_criterion_a_run_after_it_counts_even_before_the_freeze(human, capsys):
    """A human declared the criterion when they wrote it, so freezing later does not change how the runs that came
    after it are read."""
    start(capsys)
    ok(capsys, "criterion", "add", "s", "-e", "e1", "--metric", "top1", "--at-least", "0.7", "--id", "c1")
    run = record(capsys, "top1=0.76")
    ok(capsys, "freeze", "e1", "-m", "locked")
    f = finding(capsys)
    assert counted(f) == [(run, True, False)] and f["timing"] == "before"


def test_a_run_recorded_after_the_criterion_and_the_freeze_is_evidence_after_the_declaration(human, capsys):
    start(capsys)
    ok(capsys, "criterion", "add", "s", "-e", "e1", "--metric", "top1", "--at-least", "0.7", "--id", "c1")
    ok(capsys, "freeze", "e1", "-m", "locked")
    run = record(capsys, "top1=0.76")
    f = finding(capsys)
    assert counted(f) == [(run, True, False)] and f["timing"] == "before"


def test_a_run_recorded_as_synthetic_is_not_counted(human, capsys):
    """A run an older record marks synthetic (example data, not a measurement of any model) is shown, never counted."""
    start(capsys)
    ok(capsys, "criterion", "add", "s", "-e", "e1", "--metric", "top1", "--at-least", "0.7", "--id", "c1")
    run_id = record(capsys, "top1=0.76")
    p = human / ".rb" / "experiments" / "e1" / "runs" / f"{run_id}.yaml"
    doc = rbyaml.loads(p.read_text(encoding="utf-8"))
    doc["synthetic"] = True
    p.write_text(rbyaml.dumps(doc), encoding="utf-8")
    ok(capsys, "doctor", "--adopt", "-m", "the mark an older record carried")       # the file as it stands is the record
    assert state().get(run_id, "run").synthetic is True
    f = finding(capsys)
    [o] = f["observations"]
    assert not o["counted"] and o["reason"].startswith("synthetic")
    assert (f["n"], f["rule_outcome"], f["comparison"]) == (0, "untested", None)


def test_a_run_config_that_differs_from_the_spec_is_not_counted(human, capsys):
    start(capsys)
    Path("cfg.yaml").write_text("lr: 0.1\n", encoding="utf-8")
    Path("ran.yaml").write_text("lr: 0.2\n", encoding="utf-8")
    ok(capsys, "spec", "set", "e1", "lr", "--source", "cfg.yaml#lr")
    ok(capsys, "criterion", "add", "s", "-e", "e1", "--metric", "top1", "--at-least", "0.7", "--id", "c1")
    record(capsys, "top1=0.76", "--config", "ran.yaml")
    [o] = finding(capsys)["observations"]
    assert not o["counted"] and "lr" in o["reason"]


def test_a_run_config_that_agrees_with_the_spec_says_how_many_settings_it_holds(human, capsys):
    """rb run add --config says how many of the spec's settings the run's config holds: the one, matching, or all of
    them matching."""
    start(capsys)
    Path("cfg.yaml").write_text("lr: 0.1\nwd: 0.01\n", encoding="utf-8")
    Path("one.yaml").write_text("lr: 0.1\n", encoding="utf-8")
    ok(capsys, "spec", "set", "e1", "lr", "--source", "cfg.yaml#lr")
    assert main(["run", "add", "e1", "top1=0.76", "--config", "one.yaml"]) == 0
    assert "  Ran with the spec: 1 of its settings found in one.yaml, matching." in capsys.readouterr().out.splitlines()
    ok(capsys, "spec", "set", "e1", "wd", "--source", "cfg.yaml#wd")
    assert main(["run", "add", "e1", "top1=0.77", "--config", "cfg.yaml"]) == 0
    assert "  Ran with the spec: 2 of its settings found in cfg.yaml, all matching." in capsys.readouterr().out.splitlines()


def test_a_population_setting_is_never_compared_with_a_run_config(human, capsys):
    """population.* says how the runs are counted, not what a run was given: the config's d_model is not
    population.d_model, so a run made at one of the declared widths counts, and so does one an earlier rb marked as made
    with such a config, which no surface says differed. An env.* setting is compared with the config's key of its own
    full name only: env.name differs from an RL config's env.name, but env.cuda is not its trainer.cuda, nor a per-run
    env.gpu its trainer.gpu, and a per-run population.* value is never compared."""
    start(capsys)
    Path("ran.yaml").write_text("model:\n  d_model: 512\ntrainer:\n  cuda: true\n  gpu: 1\n  photo: 3\nenv:\n  name: Hopper-v4\n",
                                encoding="utf-8")
    ok(capsys, "spec", "set", "e1", "population.d_model", "[256, 512, 1024]", "--no-verify")
    ok(capsys, "spec", "set", "e1", "env.cuda", "12.1", "--no-verify")
    ok(capsys, "criterion", "add", "s", "-e", "e1", "--metric", "top1", "--at-least", "0.7", "--id", "c1")
    run = ok(capsys, "run", "add", "e1", "top1=0.76", "--config", "ran.yaml").data["object"]
    assert run["receipt"]["config"]["mismatches"] == [] and run["receipt"]["config"]["absent"] == []
    [o] = finding(capsys)["observations"]
    assert o["counted"], o["reason"]
    p = human / ".rb" / "experiments" / "e1" / "runs" / f"{run['id']}.yaml"
    doc = rbyaml.loads(p.read_text(encoding="utf-8"))
    doc["receipt"]["config"]["mismatches"] = [{"name": "population.d_model", "spec": [256, 512, 1024], "ran": 512}]
    p.write_text(rbyaml.dumps(doc), encoding="utf-8")
    ok(capsys, "doctor", "--adopt", "-m", "the check an earlier rb kept")
    [o] = finding(capsys)["observations"]
    assert o["counted"], o["reason"]
    assert ok(capsys, "compare", "e1").data["run_count"] == 1
    assert ok(capsys, "show", run["id"]).data["config"]["mismatches"] == []
    assert main(["show", run["id"]]) == 0 and "1 differ" not in capsys.readouterr().out
    for name in ("env.gpu", "population.photo"):
        assert ok(capsys, "spec", "set", "e1", name, "--per-run").data["object"]["per_run"]
    assert main(["spec", "set", "e1", "env.gpu", "--per-run"]) == 0 and "--set env.gpu=<value>" in capsys.readouterr().out
    run = ok(capsys, "run", "add", "e1", "top1=0.75", "--set", "env.gpu=A100", "--set", "population.photo=7", "--config", "ran.yaml")
    assert run.data["object"]["receipt"]["config"]["mismatches"] == []
    ok(capsys, "spec", "set", "e1", "env.name", "HalfCheetah-v4", "--no-verify")
    run = ok(capsys, "run", "add", "e1", "top1=0.74", "--set", "env.gpu=A100", "--set", "population.photo=8", "--config", "ran.yaml")
    assert run.data["object"]["receipt"]["config"]["mismatches"] == [{"name": "env.name", "spec": "HalfCheetah-v4", "ran": "Hopper-v4"}]
    [o] = [o for o in finding(capsys)["observations"] if o["run"] == run.data["object"]["id"]]
    assert not o["counted"] and o["reason"] == "ran with env.name Hopper-v4 (the spec says HalfCheetah-v4)"


def test_a_run_under_a_spec_that_changed_since_is_not_counted(human, capsys):
    """It stops counting, and what it read stays on the finding as history."""
    start(capsys)
    ok(capsys, "spec", "set", "e1", "lr", "0.1")
    ok(capsys, "criterion", "add", "s", "-e", "e1", "--metric", "top1", "--at-least", "0.7", "--id", "c1")
    record(capsys, "top1=0.76")
    ok(capsys, "spec", "set", "e1", "lr", "0.2")
    f = finding(capsys)
    [o] = f["observations"]
    assert not o["counted"] and "0.1 -> 0.2" in o["reason"]
    [was] = f["history"]
    assert was["estimate"] == 0.76 and "0.1 -> 0.2" in was["superseded"] and f["rule_outcome"] == "untested"


# ---------------------------------------------------------------- who declared the criterion


def test_a_human_written_criterion_on_a_later_run_passes_the_unestablished_gate(human, capsys):
    """A human's criterion and a run after it that meets the rule: the rule meets, the criterion came before the
    evidence, nothing is in question, and --fail-on unestablished passes."""
    start(capsys)
    ok(capsys, "criterion", "add", "s", "-e", "e1", "--metric", "top1", "--at-least", "0.7", "--id", "c1")
    record(capsys, "top1=0.76")
    f = finding(capsys)
    assert (f["rule_outcome"], f["timing"], f["integrity"]) == ("meets", "before", [])
    assert f["declared"]["by"] == "human:t"
    assert rbj(capsys, "status", "--fail-on", "unestablished")[0] == 0


def test_an_agent_written_criterion_passes_the_unestablished_gate_without_a_humans_freeze(human, capsys, monkeypatch):
    """An agent's criterion counts as declared, attributed to the agent: no human has to freeze it first."""
    start(capsys)
    monkeypatch.setenv("CLAUDECODE", "1")
    ok(capsys, "criterion", "add", "s", "-e", "e1", "--metric", "top1", "--at-least", "0.7", "--id", "c1")
    record(capsys, "top1=0.76")
    f = finding(capsys)
    assert (f["rule_outcome"], f["timing"], f["integrity"]) == ("meets", "before", [])
    assert (f["declared"]["by"], f["declared"]["frozen_by"]) == ("agent:claude-code", None)
    assert f["says"]["timing"].startswith("declared before the evidence")
    assert rbj(capsys, "status", "--fail-on", "unestablished")[0] == 0


def test_a_humans_freeze_of_an_agent_written_criterion_is_on_the_finding(human, capsys, monkeypatch):
    """The criterion stays the agent's; the freeze is the human's, and the finding says both."""
    start(capsys)
    monkeypatch.setenv("CLAUDECODE", "1")
    ok(capsys, "criterion", "add", "s", "-e", "e1", "--metric", "top1", "--at-least", "0.7", "--id", "c1")
    monkeypatch.delenv("CLAUDECODE")
    ok(capsys, "freeze", "e1", "-m", "the criterion is right")
    monkeypatch.setenv("CLAUDECODE", "1")
    record(capsys, "top1=0.76")
    f = finding(capsys)
    assert (f["rule_outcome"], f["timing"], f["integrity"]) == ("meets", "before", [])
    assert (f["declared"]["by"], f["declared"]["frozen_by"]) == ("agent:claude-code", "human:t")


def test_an_agent_cannot_add_a_criterion_to_a_frozen_experiment(human, capsys, monkeypatch):
    start(capsys)
    ok(capsys, "freeze", "e1", "-m", "locked")
    monkeypatch.setenv("CLAUDECODE", "1")
    code, env = rbj(capsys, "criterion", "add", "s", "-e", "e1", "--metric", "top1", "--at-least", "0.7", "--id", "c1")
    assert code == 2 and env.errors[0].code == "E_FROZEN"


# ---------------------------------------------------------------- settings a criterion rests on


@pytest.fixture
def one_setting(human, capsys):
    """Returns a function: record lr with the given spec-set arguments, then a criterion and one run after it."""
    def make(*spec_args):
        start(capsys)
        ok(capsys, "spec", "set", "e1", "lr", *spec_args)
        ok(capsys, "criterion", "add", "s", "-e", "e1", "--metric", "top1", "--at-least", "0.7", "--id", "c1")
        record(capsys, "top1=0.76")
        return finding(capsys)
    return make


def test_a_required_unknown_setting_blocks(one_setting, capsys):
    """The rule still reads; the finding's integrity names the setting nobody has found, and the unknown and integrity
    gates fail."""
    f = one_setting("--unknown")
    assert f["rule_outcome"] == "meets" and problems(f) == [("unknown", "e1/lr")]
    gate = rbj(capsys, "status")[1].data["gate"]
    assert gate["unknown"] and gate["integrity"] and gate["unestablished"]


def test_a_required_provisional_setting_is_a_note_not_an_integrity_problem(one_setting, capsys):
    """A value nobody has checked yet is said on the finding, and left to verify or vouch for; it is not a problem with
    what the finding rests on."""
    f = one_setting("0.1")
    assert f["rule_outcome"] == "meets" and f["integrity"] == [] and notes(f, "provisional") == [("e1/lr", False)]
    code, env = rbj(capsys, "status", "--fail-on", "unestablished")
    assert code == 0 and ("provisional", "e1/lr") in [(i["code"], i["subject"]) for i in env.data["open"]]


def test_an_optional_unknown_setting_does_not_block(one_setting):
    f = one_setting("--unknown", "--optional")
    assert f["integrity"] == [] and notes(f, "unknown_optional") == [("e1/lr", False)]


def test_a_humans_vouch_turns_a_provisional_setting_into_a_vouched_one(one_setting, capsys):
    """The vouch is said on the finding in place of the provisional note, and nothing is left open about the setting."""
    one_setting("0.1")
    ok(capsys, "decide", "e1/lr", "accept", "-m", "read it in the launch script")
    f = finding(capsys)
    assert f["integrity"] == [] and notes(f, "vouched") == [("e1/lr", False)] and notes(f, "provisional") == []
    assert [i for i in rbj(capsys, "status")[1].data["open"] if i["subject"] == "e1/lr"] == []


def test_a_vouch_lapses_when_the_value_changes(human, capsys, monkeypatch):
    start(capsys)
    ok(capsys, "spec", "set", "e1", "lr", "0.1")
    ok(capsys, "decide", "e1/lr", "accept", "-m", "read it in the launch script")
    assert caveats(capsys, "e1/lr") == [("vouched", False)]
    monkeypatch.setenv("CLAUDECODE", "1")
    ok(capsys, "spec", "set", "e1", "lr", "0.3")
    assert caveats(capsys, "e1/lr") == [("provisional", False)]


def test_a_vouch_given_while_the_setting_was_unknown_does_not_cover_a_value_set_later(human, capsys, monkeypatch):
    """There is nothing to vouch for until the setting has a value, so the accept is refused, and a value an agent sets
    later is provisional like any other."""
    start(capsys)
    ok(capsys, "spec", "set", "e1", "seed", "--unknown")
    code, env = rbj(capsys, "decide", "e1/seed", "accept", "-m", "we will find it")
    assert code == 2 and env.errors[0].code == "E_OBJECT_INVALID" and "no value to vouch for" in env.errors[0].message
    monkeypatch.setenv("CLAUDECODE", "1")
    ok(capsys, "spec", "set", "e1", "seed", "12345")
    assert caveats(capsys, "e1/seed") == [("provisional", False)]


# ---------------------------------------------------------------- runs, noise, cited numbers


def test_fewer_units_than_min_n_leave_the_rule_undetermined(human, capsys):
    start(capsys)
    ok(capsys, "criterion", "add", "s", "-e", "e1", "--metric", "top1", "--at-least", "0.7", "--min-n", "3", "--noise", "0.001", "--id", "c1")
    record(capsys, "top1=0.76")
    record(capsys, "top1=0.77")
    f = finding(capsys)
    assert (f["n"], f["units"]["n"], f["rule_outcome"]) == (2, 2, "undetermined")
    assert f["says"]["rule"] == "criterion undetermined: 2 units; the criterion needs 3"
    code, env = rbj(capsys, "status", "--fail-on", "unmet")
    assert code == 1 and ("too_few_units", "c1") in [(i["code"], i["subject"]) for i in env.data["open"]]
    record(capsys, "top1=0.78")
    f = finding(capsys)
    assert (f["n"], f["rule_outcome"]) == (3, "meets")


def test_a_margin_within_the_stated_noise_is_noted_as_borderline(human, capsys):
    """0.705 meets at least 0.7 by less than the stated noise of 0.01: the rule reads meets, and the finding notes that
    another unit may land on the other side. Borderline is a note, not an integrity problem."""
    start(capsys)
    ok(capsys, "criterion", "add", "s", "-e", "e1", "--metric", "top1", "--at-least", "0.7", "--noise", "0.01", "--id", "c1")
    record(capsys, "top1=0.705")
    f = finding(capsys)
    assert f["rule_outcome"] == "meets" and f["integrity"] == [] and notes(f, "borderline") == [("c1", False)]


def test_the_noise_of_three_runs_puts_the_interval_of_a_close_one_across_the_rule(human, capsys):
    """0.70, 0.75 and 0.80 average 0.75, above 0.7, but their spread gives a t interval of about [0.63, 0.87]: a
    threshold read on the estimate meets, and the same threshold read on the interval's lower bound misses."""
    start(capsys)
    ok(capsys, "criterion", "add", "s", "-e", "e1", "--metric", "top1", "--at-least", "0.7", "--id", "c1")
    ok(capsys, "criterion", "add", "s", "-e", "e1", "--metric", "top1", "--at-least", "0.7", "--bound", "--id", "c2")
    for x in ("0.70", "0.75", "0.80"):
        record(capsys, f"top1={x}")
    on_estimate, on_bound = finding(capsys, "c1"), finding(capsys, "c2")
    r = on_bound["comparison"]
    assert (r["n"], r["level"]) == (3, 0.95) and r["lo"] < 0.7 < r["estimate"] < r["hi"]
    assert (on_estimate["result"], on_estimate["rule_outcome"]) == ("measured", "meets")
    assert (on_bound["result"], on_bound["rule_outcome"]) == ("measured", "misses")
    assert on_bound["says"]["rule"].startswith("criterion not met: lower bound 0.6258")
    assert on_estimate["says"]["result"] == "measured: one arm on its own, with its interval, so neither better nor worse"
    assert crossings(on_estimate) == ["the interval [0.6258, 0.8742] crosses 0.7: met on the estimate, not across the whole interval"]
    assert crossings(on_bound) == []


def crossings(f: dict) -> list:
    """A finding's notes that its interval crosses the threshold its rule read on the estimate, as their text."""
    return [c["text"] for c in f["caveats"] if c["code"] == "straddles"]


@pytest.mark.parametrize("rule,outcome,said", [
    (["--at-most", "21"], "meets", "crosses 21: met on the estimate"),
    (["--at-most", "21", "--role", "gate"], "meets", "crosses 21: gate met on the estimate"),
    (["--at-most", "20.5"], "misses", "crosses 20.5: missed on the estimate"),
    (["--equals", "21", "--tolerance", "0.5"], "meets", "crosses 20.5: met on the estimate"),
    (["--equals", "20.5", "--tolerance", "0.2", "--source", "paper.txt", "--quote", "20.5 ms"], "meets", "crosses 20.3 and 20.7: reproduced on the estimate"),
    (["--at-least", "20.5"], "meets", "crosses 20.5: met on the estimate"),
    (["--at-least", "21"], "misses", "crosses 21: missed on the estimate"),
    (["--at-most", "20.5", "--role", "gate"], "misses", "crosses 20.5: gate missed on the estimate"),
    (["--equals", "20.5", "--tolerance", "0.1", "--source", "paper.txt", "--quote", "20.5 ms"], "misses", "crosses 20.4 and 20.6: not reproduced on the estimate"),
    (["--at-most", "22"], "meets", None),                 # the whole interval is under it
    (["--at-least", "20"], "meets", None),                # and over it
    (["--at-most", "21", "--bound"], "misses", None),     # it reads the interval's upper bound already
    (["--at-most", "21", "--each"], "meets", None),       # it reads every unit
])
def test_a_rule_read_on_the_estimate_notes_an_interval_that_crosses_its_threshold(human, capsys, rule, outcome, said):
    """Step times of 20.4, 20.6 and 20.9 ms read 20.63 [20.01, 21.26]: at most 21 is met on the estimate, not across
    the whole interval, and the finding notes it, for any role and origin, in the rule axis's words (a gate met, a cited
    number not reproduced). A rule that reads the bound or each unit, or
    a reading with no interval yet, has nothing to note. The note is not a problem and changes no outcome."""
    start(capsys)
    Path("paper.txt").write_text("Table 3: a step takes 20.5 ms.\n", encoding="utf-8")
    ok(capsys, "criterion", "add", "s", "-e", "e1", "--metric", "step_ms", *rule, "--id", "c1")
    record(capsys, "step_ms=20.4")
    record(capsys, "step_ms=20.6")
    assert crossings(finding(capsys)) == []                                   # point only: no interval yet
    record(capsys, "step_ms=20.9")
    f = finding(capsys)
    assert (f["rule_outcome"], f["integrity"]) == (outcome, []) and not any(c["problem"] for c in f["caveats"])
    wanted = [f"the interval [20.01, 21.26] {said}, not across the whole interval"] if said else []
    assert crossings(f) == wanted


def test_a_level_names_the_arm_it_reads(human, capsys):
    """A level is one arm on its own, so neither better nor worse: its result names the arm when it has a name."""
    start(capsys, "--baseline", "none", "--candidate", "p01")
    ok(capsys, "criterion", "add", "s", "-e", "e1", "--metric", "p01.step_ms", "--at-most", "21", "--id", "c1")
    for x in ("20.4", "20.6", "20.9"):
        record(capsys, f"p01.step_ms={x}")
    f = finding(capsys)
    assert (f["result"], f["says"]["result"]) == ("measured", "measured: p01 on its own, with its interval, so neither better nor worse")


def test_what_a_call_was_made_on_is_signed_only_when_it_compares_two_arms(human, capsys):
    """rb decide and the decision's review say the reading a call was made on as the reading says it: rb's reading of
    the runs as rb's, its result in words, a level's numbers bare, a comparison's with their signs. So does each run's
    number under rb show."""
    start(capsys, "--baseline", "none", "--candidate", "p01")
    ok(capsys, "criterion", "add", "s", "-e", "e1", "--metric", "p01.step_ms", "--at-most", "21", "--id", "c1")
    ok(capsys, "criterion", "add", "s", "-e", "e1", "--metric", "change.val_loss", "--at-most", "0", "--id", "c2")
    for none, p01, ms in (("1.31", "1.27", "20.4"), ("1.33", "1.24", "20.6"), ("1.29", "1.24", "20.9")):
        record(capsys, f"none.val_loss={none}", f"p01.val_loss={p01}", f"p01.step_ms={ms}")
    assert main(["decide", "e1", "investigate", "-m", "one more seed"]) == 0
    said = capsys.readouterr().out
    assert "  made on c1: 20.63 [20.01, 21.26], n 3 (rb): measured, estimate meets criterion" in said
    assert "  made on c2: -0.06 [-0.1257, +0.005724], n 3 (rb): unresolved, estimate meets criterion" in said
    assert main(["show", "c1"]) == 0 and ": p01.step_ms 20.4 (typed) · unit " in capsys.readouterr().out
    assert main(["show", "c2"]) == 0 and ": difference -0.04 (typed) · unit " in capsys.readouterr().out
    ok(capsys, "decide", "c1", "accept", "-m", "fast enough")
    record(capsys, "none.val_loss=1.30", "p01.val_loss=1.25", "p01.step_ms=20.5")
    assert finding(capsys)["says"]["review"].endswith("made on an earlier reading (20.63 [20.01, 21.26], n 3 (rb): measured, estimate meets criterion)")


def test_a_run_s_difference_keeps_its_sign_after_it_stops_counting(human, capsys):
    """A run's number under a comparison is a difference, signed by what the criterion compares, so it keeps its sign
    when nothing counts any more; a level is bare. rb show and the criterion's card say it alike."""
    from rbtest import object_card
    start(capsys, "--baseline", "a", "--candidate", "b")
    ok(capsys, "criterion", "add", "s", "-e", "e1", "--metric", "change.m", "--at-most", "1", "--id", "c1")
    ok(capsys, "criterion", "add", "s", "-e", "e1", "--metric", "b.m", "--at-most", "2", "--id", "c2")
    run = record(capsys, "a.m=1", "b.m=1.3")
    ok(capsys, "spec", "set", "e1", "lr", "0.1", "--no-verify")              # the run is now under an earlier spec
    for c, shown, carded in (("c1", "change.m +0.3", "+0.3"), ("c2", "b.m 1.3", "1.3")):
        assert finding(capsys, c)["comparison"] is None
        assert main(["show", c]) == 0 and f"  {run}: {shown} (typed) · unit {run} · not counted: " in capsys.readouterr().out
        assert any(f.startswith(f"{run}: {carded} · unit {run} · not counted: ") for f in object_card(c).facts)


def test_a_signed_interval_that_crosses_the_threshold_is_said_with_its_signs(human, capsys):
    start(capsys, "--baseline", "none", "--candidate", "p01")
    ok(capsys, "criterion", "add", "s", "-e", "e1", "--metric", "change.val_loss", "--at-most", "0", "--id", "c1")
    for none, p01 in (("1.31", "1.27"), ("1.33", "1.24"), ("1.29", "1.24")):
        record(capsys, f"none.val_loss={none}", f"p01.val_loss={p01}")
    assert crossings(finding(capsys)) == ["the interval [-0.1257, +0.005724] crosses 0: met on the estimate, not across the whole interval"]


def test_a_crossing_is_said_with_the_digits_that_keep_its_numbers_in_order(human, capsys):
    """At 4 digits the interval [20.0082, 21.2585] reads [20.01, 21.26], which cannot be seen to cross 20.01, and an
    equals band of 0.0001 reads as one number at 6: each is said with the digits that keep its numbers apart."""
    start(capsys)
    ok(capsys, "criterion", "add", "s", "-e", "e1", "--metric", "step_ms", "--at-least", "20.01", "--id", "c1")
    ok(capsys, "criterion", "add", "s", "-e", "e1", "--metric", "step_ms", "--equals", "20.5", "--tolerance", "0.49", "--id", "c2")
    for x in ("20.4", "20.6", "20.9"):
        record(capsys, f"step_ms={x}")
    assert crossings(finding(capsys, "c1")) == ["the interval [20.0082, 21.2585] crosses 20.01: met on the estimate, not across the whole interval"]
    assert crossings(finding(capsys, "c2")) == [
        "the interval [20.0082, 21.2585] crosses 20.01 and 20.99: met on the estimate, not across the whole interval"]
    ok(capsys, "criterion", "add", "s", "-e", "e1", "--metric", "lat", "--subject", "b", "--equals", "1234.5678", "--tolerance", "0.0001", "--id", "c3")
    ok(capsys, "analysis", "add", "e1", "--compare", "b:lat=1234.5678[1234.4,1234.7]", "--method", "bootstrap", "--unit", "image", "--units", "200")
    assert crossings(finding(capsys, "c3")) == [
        "the interval [1234.4, 1234.7] crosses 1234.5677 and 1234.5679: met on the estimate, not across the whole interval"]


@pytest.mark.parametrize("value,rule,said", [
    ("3846.6046", ["--equals", "3846.6", "--tolerance", "0.05"], "estimate meets criterion: estimate 3846.60 within 3846.6 ± 0.05"),
    ("241.6489", ["--equals", "241.7", "--tolerance", "0.05", "--source", "paper.txt", "--quote", "241.7"],
     "not reproduced: estimate 241.649 outside 241.7 ± 0.05"),
    ("412.761", ["--equals", "412.8", "--tolerance", "0.05"], "estimate meets criterion: estimate 412.761 within 412.8 ± 0.05"),
    ("0.050004", ["--at-most", "0.05"], "criterion not met: estimate 0.050004 > 0.05"),
    ("0.026667", ["--at-most", "0.05"], "estimate meets criterion: estimate 0.02667 ≤ 0.05"),                 # 4 digits keep it apart
    ("0.15000000000000002", ["--at-most", "0.15"], "estimate meets criterion: estimate 0.15 ≤ 0.15"),         # the rule reads it as on 0.15
])
def test_a_rule_says_its_estimate_with_the_digits_that_keep_it_on_its_side_of_the_rule(human, capsys, value, rule, said):
    """3846.6046 is within 3846.6 ± 0.05, and 3847 reads outside it; 241.6489 misses 241.7 ± 0.05 by 0.001, and 241.6
    stands for numbers up to the edge: a rule sentence, and the reads line beside it, say the estimate with the fewest
    digits that stand only for numbers on its own side of the rule's edges, keeping a trailing zero that says so."""
    start(capsys)
    Path("paper.txt").write_text("The paper reports 241.7 at batch 64.\n", encoding="utf-8")
    ok(capsys, "criterion", "add", "s", "-e", "e1", "--metric", "ppl", *rule, "--id", "c1")
    record(capsys, f"ppl={value}")
    f = finding(capsys)
    assert f["says"]["rule"] == said
    assert f["says"]["comparison"].startswith(f"ppl: {said.split(': estimate ', 1)[1].split()[0]} · mean")


def test_a_rule_beside_a_reading_says_its_numbers_as_declared(human, capsys):
    """1234.5679 misses at most 1234.5678: the rule beside it, in the finding's rule (rb show's header, rb status, the
    page) and the page's row, says 1234.5678, never 1234.57, which reads as met. A band says its target as declared:
    99.5002 is outside 100.0004 ± 0.5, and inside 100 ± 0.5. An estimate the rule reads as on its threshold of 0 is said
    as 0, never with 17 digits, and leaves no margin."""
    start(capsys, "--baseline", "b", "--candidate", "c")
    ok(capsys, "criterion", "add", "s", "-e", "e1", "--metric", "ppl", "--at-most", "1234.5678", "--id", "c1")
    ok(capsys, "criterion", "add", "s", "-e", "e1", "--metric", "m1", "--equals", "100.0004", "--tolerance", "0.5", "--id", "c2")
    ok(capsys, "criterion", "add", "s", "-e", "e1", "--metric", "change.m", "--at-most", "0", "--id", "c3")
    record(capsys, "ppl=1234.5679", "m1=99.5002", "change.m=0.1")
    for x in ("0.2", "-0.3"):
        record(capsys, f"change.m={x}")
    f = finding(capsys)
    assert (f["rule"], f["says"]["rule"]) == ("ppl ≤ 1234.5678", "criterion not met: estimate 1234.5679 > 1234.5678")
    f = finding(capsys, "c2")
    assert (f["rule"], f["says"]["rule"]) == ("m1 = 100.0004 ± 0.5", "criterion not met: estimate 99.5002 outside 100.0004 ± 0.5")
    f = finding(capsys, "c3")
    assert f["says"]["rule"] == "estimate meets criterion: estimate +0 ≤ 0" and f["says"]["comparison"].startswith("change.m: +0 [-0.6572, +0.6572]")
    ok(capsys, "page", "--out", "page.html")
    raw = html.unescape(Path("page.html").read_text(encoding="utf-8"))
    # each finding's card says its estimate and its rule in the digits they were declared and computed in, and its margin
    # (rb's line without its state lead: the card says its verdict once, under its title, FIX2)
    for cid, said, rule, margin in (("c1", "estimate 1234.5679 > 1234.5678", "ppl ≤ 1234.5678", "-0.0001"),
                                     ("c2", "estimate 99.5002 outside 100.0004 ± 0.5", "m1 = 100.0004 ± 0.5", "-0.0002"),
                                     ("c3", "estimate +0 ≤ 0", "change.m ≤ 0", "0.00")):
        card = raw.split(f'id="c-{cid}"', 1)[1].split('<div class="card', 1)[0]
        # rb's own rule and its reading are the card's Source details rows, in those digits (DESIGN-PASS-1 P8)
        assert f'<dt>rule</dt><dd><span class="mono">{rule}</span></dd><dt>rb reads</dt><dd>{said}</dd>' in card, card
        assert f'<dt>margin</dt><dd><a href="#c-{cid}:reading">{margin}</a></dd>' in card, card
    # and c3's reading, with its interval, on its own card
    assert "change.m: +0 [-0.6572, +0.6572]" in raw.split('id="c-c3"', 1)[1].split('<div class="card', 1)[0]


def test_a_rule_says_its_own_numbers_as_declared_and_every_reading_beside_it_as_its_sentence_does(human, capsys):
    """A rule's own numbers are said as declared wherever the rule is (its finding, the log, an amendment, the result
    of an --equivalent rule), never 1234.57 for 1234.5678 nor ±0.1 for ±0.10000004. Every reading said beside a rule
    takes the digits its rule sentence does: what a call was made on, the part produced after the declaration, an
    earlier reading, rb compare's line naming what the primary criteria read, and an analysis's own lines; an interval
    the rule does not read is said with as many digits as its estimate."""
    start(capsys, "--baseline", "b", "--candidate", "c")
    ok(capsys, "spec", "set", "e1", "seed", "--per-run")
    ok(capsys, "metric", "add", "mm", "--minimize")
    ok(capsys, "criterion", "add", "s", "-e", "e1", "--metric", "ppl", "--at-most", "1234.5678", "--id", "c1")
    ok(capsys, "criterion", "add", "s", "-e", "e1", "--metric", "change.mm", "--equivalent", "0.10000004", "--id", "c2")
    assert finding(capsys)["rule"] == "ppl ≤ 1234.5678"
    ok(capsys, "freeze", "e1", "-m", "lock")
    ok(capsys, "criterion", "add", "s", "-e", "e1", "--metric", "c.r2", "--equals", "3846.6", "--tolerance", "0.05", "--id", "c3",
       "--amend", "--why", "add")
    assert ok(capsys, "show", "e1").data["object"]["amendments"][-1]["change"] == "new criterion c3: c.r2 = 3846.6 ± 0.05"
    assert [e["detail"]["rule"] for e in ok(capsys, "log", "-n", "50").data["entries"] if e["op"] == "add" and e["kind"] == "criterion"][0] == "ppl ≤ 1234.5678"
    for seed, x in ((11, "0.071948908507"), (12, "0.08"), (13, "0.088051091493")):
        record(capsys, f"change.mm={x}", "--set", f"seed={seed}")
    assert finding(capsys, "c2")["says"]["result"] == "equivalent within ±0.10000004"
    first = record(capsys, "c.r2=3846.6046", "--set", "seed=1")
    ok(capsys, "decide", "c3", "accept", "--why", "holds")
    record(capsys, "c.r2=3846.62", "--set", "seed=2")
    assert finding(capsys, "c3")["says"]["review"].endswith("made on an earlier reading (3846.60, n 1 (rb): point only, estimate meets criterion)")
    outdated = [o["what"] for o in ok(capsys, "status").data["open"] if o["code"] == "decision_outdated"]
    assert outdated[0].split("; ")[0].endswith("was made on 3846.60, n 1 (rb): point only, estimate meets criterion")
    ok(capsys, "criterion", "add", "s", "-e", "e1", "--metric", "c.r2", "--equals", "3846.6", "--tolerance", "0.05", "--id", "c4",
       "--amend", "--why", "add")
    record(capsys, "c.r2=3846.6046", "--set", "seed=3")
    assert finding(capsys, "c4")["says"]["timing"].endswith("produced after it: 3846.60 · mean, 1 seed (rb)")
    ok(capsys, "analysis", "add", "e1", "--compare", "c:r2=3846.6046", "--method", "re-eval", "--reads", first, "--id", "a1")
    assert finding(capsys, "c3")["says"]["history"].startswith("was 3846.61 [3846.59, 3846.63] · mean, t interval, 3 seeds")
    assert {p["criterion"]: p.get("reads") for p in ok(capsys, "compare", "e1").data["primary"]}["c3"] == "3846.60 · re-eval (a1)"
    assert "- analysis a1 (reported, re-eval): r2 c 3846.60" in ok(capsys, "context").data["markdown"]


def test_a_reading_says_no_number_coarser_than_one_its_rule_says(human, capsys):
    """A rule that reads an interval's bound says it with the digits that keep it on its side, and the reading beside
    it says its estimate and its other bound with as many, trailing zeros kept: 3846.60 [3846.57, 3846.64], never 3847
    [3847, 3846.64] beside an upper bound of 3846.64, in the finding, in what a call was made on, and in an analysis's
    own lines. The edges of a band an interval crosses are said as its target and tolerance make them, 3846.55, never
    3846.5499999999997."""
    start(capsys)
    ok(capsys, "spec", "set", "e1", "seed", "--per-run")
    ok(capsys, "criterion", "add", "s", "-e", "e1", "--metric", "ppl", "--at-most", "3846.6", "--bound", "--id", "c1")
    ok(capsys, "criterion", "add", "s", "-e", "e1", "--metric", "ppl", "--equals", "3846.6", "--tolerance", "0.05", "--bound", "--id", "c2")
    ok(capsys, "criterion", "add", "s", "-e", "e1", "--metric", "wide", "--equals", "3846.6", "--tolerance", "0.05", "--id", "c3")
    for seed, x, w in ((1, "3846.59", "3846.58"), (2, "3846.60", "3846.50"), (3, "3846.62", "3846.66")):
        record(capsys, f"ppl={x}", f"wide={w}", "--set", f"seed={seed}")
    for c in ("c1", "c2"):
        assert finding(capsys, c)["says"]["comparison"].startswith("ppl: 3846.60 [3846.57, 3846.64] · mean, t interval, 3 seeds"), c
    assert main(["decide", "c2", "accept", "-m", "within"]) == 0
    assert "  made on c2: 3846.60 [3846.57, 3846.64], n 3 (rb): measured, criterion met" in capsys.readouterr().out
    assert crossings(finding(capsys, "c3")) == [
        "the interval [3846.38, 3846.78] crosses 3846.55 and 3846.65: met on the estimate, not across the whole interval"]
    ok(capsys, "experiment", "add", "x", "--id", "e2", "--baseline", "r1", "--candidate", "r2")
    ok(capsys, "criterion", "add", "s", "-e", "e2", "--metric", "r2.ppl", "--equals", "3846.6", "--tolerance", "0.05", "--id", "c4")
    ok(capsys, "criterion", "add", "s", "-e", "e2", "--metric", "r2.ppl", "--at-most", "3846.6", "--bound", "--id", "c5")
    assert main(["analysis", "add", "e2", "--compare", "r2:ppl=3846.6046[3846.58,3846.63]", "--method", "re-eval"]) == 0
    said = capsys.readouterr().out
    assert "c4 criterion met · measured: r2.ppl: 3846.60 [3846.58, 3846.63]" in said and "c5 criterion not met · measured: r2.ppl: 3846.60 [3846.58, 3846.63]" in said


def test_a_reading_beside_a_rule_said_to_4_digits_is_said_as_any_reading_is(human, capsys):
    """Four significant digits are how a reading says a number, so a rule that says one with 4 leaves the reading's
    other numbers as a reading says them, never padded with zeros: the criteria of one comparison, the note that its
    interval crosses a threshold and rb compare say it alike (+0.04 [-0.003027, +0.08303], never +0.04000), and a
    number typed or read off a document is said as it was given (+0.1, [0.89, 0.93])."""
    start(capsys, "--baseline", "fp32", "--candidate", "int8")
    ok(capsys, "spec", "set", "e1", "seed", "--per-run")
    for m, d in (("val_loss", "--minimize"), ("score", "--maximize"), ("acc", "--maximize")):
        ok(capsys, "metric", "add", m, d)
    for i, rule in enumerate((["--at-most", "0.05"], ["--noninferior", "0.05"], ["--equivalent", "0.05"], ["--at-most", "0.05", "--bound"]), start=1):
        ok(capsys, "criterion", "add", "s", "-e", "e1", "--metric", "change.val_loss", *rule, "--id", f"c{i}")
    for seed, fp32, int8 in ((1, "5.61", "5.64"), (2, "5.62", "5.65"), (3, "5.60", "5.66")):
        record(capsys, f"fp32.val_loss={fp32}", f"int8.val_loss={int8}", "--set", f"seed={seed}")
    for i in range(1, 5):
        assert finding(capsys, f"c{i}")["says"]["comparison"].startswith("change.val_loss: +0.04 [-0.003027, +0.08303] · "), i
    [row] = [r for r in ok(capsys, "compare", "e1").data["rows"] if r["variant"] == "int8"]
    assert row["metrics"]["val_loss"]["said"] == "+0.04"
    ok(capsys, "experiment", "add", "x", "--id", "e2", "--baseline", "old", "--candidate", "new")
    ok(capsys, "criterion", "add", "s", "-e", "e2", "--metric", "change.score", "--noninferior", "0.05", "--id", "c5")
    ok(capsys, "criterion", "add", "s", "-e", "e2", "--metric", "new.acc", "--at-least", "0.9", "--id", "c6")
    Path("REPORT.md").write_text("| comparison | Δ score | 95% CI |\n|---|---|---|\n| new vs old | +0.1 | [-0.04873, +0.2488] |\n", encoding="utf-8")
    ok(capsys, "analysis", "add", "e2", "--compare", "new/old:score=0.1[-0.04873,0.2488]", "--source", "REPORT.md", "--method", "paired bootstrap")
    ok(capsys, "analysis", "add", "e2", "--compare", "new:acc=0.9117[0.89,0.93]", "--method", "bootstrap")
    assert ": +0.1 [-0.04873, +0.2488] · paired bootstrap" in finding(capsys, "c5")["says"]["comparison"]
    assert ": 0.9117 [0.89, 0.93] · bootstrap" in finding(capsys, "c6")["says"]["comparison"]
    ok(capsys, "experiment", "add", "x", "--id", "e3")
    ok(capsys, "spec", "set", "e3", "seed", "--per-run")
    ok(capsys, "criterion", "add", "s", "-e", "e3", "--metric", "acc", "--at-least", "0.9", "--id", "c7")
    for seed, x in ((1, "0.895"), (2, "0.896"), (3, "0.925")):
        record(capsys, f"acc={x}", "--set", f"seed={seed}", exp="e3")
    f = finding(capsys, "c7")
    assert f["says"]["comparison"].startswith("acc: 0.9053 [0.863, 0.9477] · ")
    assert crossings(f) == ["the interval [0.863, 0.9477] crosses 0.9: met on the estimate, not across the whole interval"]


@pytest.mark.parametrize("compare,rule,said", [
    ("new/old:score=0.01[-0.049996,0.03]", ["--equivalent", "0.05"], "criterion met: the interval [-0.049996, +0.03] inside ±0.05"),
    ("new/old:score=0.01[-0.1,0.049996]", ["--noninferior", "0.05"], "criterion met: the interval's worse bound +0.049996 inside 0.05"),
    ("new:score=0.04[0.03,0.050004]", ["--at-most", "0.05", "--bound"], "criterion not met: upper bound 0.050004 > 0.05"),
    ("new:score=100.2[99.5002,100.3]", ["--equals", "100.0004", "--tolerance", "0.5", "--bound"],
     "criterion not met: interval [99.5002, 100.3] outside 100.0004 ± 0.5"),           # never outside 100 ± 0.5, which holds it
])
def test_a_rule_read_on_the_interval_says_its_bounds_with_the_digits_that_keep_them_apart(human, capsys, compare, rule, said):
    """-0.049996 is inside ±0.05 and reads -0.05 at 4 digits: a rule read on the interval says the bounds it reads as a
    rule read on the estimate says the estimate, and the reads line says them alike."""
    start(capsys)
    ok(capsys, "metric", "add", "score", "--minimize")
    arms = ["--subject", "new"] + (["--reference", "old"] if "/" in compare else [])
    ok(capsys, "criterion", "add", "s", "-e", "e1", "--metric", "score", *arms, *rule, "--id", "c1")
    ok(capsys, "analysis", "add", "e1", "--compare", compare, "--method", "bootstrap", "--unit", "image", "--units", "200")
    f = finding(capsys)
    assert f["says"]["rule"] == said
    bound = re.search(r"[-+]?(0\.0(49996|50004)|99\.5002)", said).group(0)
    assert bound in f["says"]["comparison"]


def test_an_each_rule_that_misses_names_the_units_that_missed(human, capsys):
    """An --each rule that misses names the units that missed by their unit values, up to three and then how many
    more, and the finding lists every one in missed_units, so no surface reads them out of the sentence."""
    start(capsys)
    ok(capsys, "spec", "set", "e1", "seed", "--per-run")
    ok(capsys, "criterion", "add", "s", "-e", "e1", "--metric", "step_ms", "--at-most", "21", "--each", "--id", "c1")
    for seed, ms in enumerate(("20.4", "21.2", "20.6", "21.5"), start=1):
        record(capsys, f"step_ms={ms}", "--set", f"seed={seed}")
    f = finding(capsys)
    assert (f["says"]["rule"], f["missed_units"]) == ("criterion not met: 2 of 4 units are above 21 (seed=2, seed=4)", ["seed=2", "seed=4"])
    for seed, ms in ((5, "21.1"), (6, "22"), (7, "21.3")):
        record(capsys, f"step_ms={ms}", "--set", f"seed={seed}")
    f = finding(capsys)
    assert f["says"]["rule"] == "criterion not met: 5 of 7 units are above 21 (seed=2, seed=4, seed=5 and 2 more)"
    assert f["missed_units"] == ["seed=2", "seed=4", "seed=5", "seed=6", "seed=7"]


def test_an_each_rule_that_misses_on_every_unit_says_the_only_one_both_or_all(human, capsys):
    """An --each rule missed on every unit said 1 of 1 units and 2 of 2 units, where its met side says the only unit
    and both units: one unit reads as the only unit, two as both, more as all of them."""
    start(capsys)
    ok(capsys, "spec", "set", "e1", "seed", "--per-run")
    ok(capsys, "criterion", "add", "s", "-e", "e1", "--metric", "step_ms", "--at-most", "21", "--each", "--id", "c1")
    said = []
    for seed in (1, 2, 3):
        record(capsys, "step_ms=21.4", "--set", f"seed={seed}")
        said.append(finding(capsys)["says"]["rule"])
    assert said == ["criterion not met: the only unit is above 21 (seed=1)", "criterion not met: both units are above 21 (seed=1, seed=2)",
                    "criterion not met: all 3 units are above 21 (seed=1, seed=2, seed=3)"]


def test_an_each_rule_says_its_threshold_as_declared_and_each_value_on_its_own_side(human, capsys):
    """An --each rule says its threshold as declared, and the observations it sends the reader to say each value with
    the digits that keep it on its own side of the threshold: 21.0000004 misses at most 21, so it never reads 21."""
    start(capsys)
    ok(capsys, "spec", "set", "e1", "seed", "--per-run")
    ok(capsys, "criterion", "add", "s", "-e", "e1", "--metric", "ppl", "--at-most", "1234.5678", "--each", "--id", "c1")
    ok(capsys, "criterion", "add", "s", "-e", "e1", "--metric", "step_ms", "--at-most", "21", "--each", "--id", "c2")
    record(capsys, "ppl=1234.561", "step_ms=20.4", "--set", "seed=1")
    record(capsys, "ppl=1234.5679", "step_ms=21.0000004", "--set", "seed=2")
    f = finding(capsys)
    assert (f["rule"], f["says"]["rule"]) == ("each unit's ppl ≤ 1234.5678", "criterion not met: 1 of 2 units is above 1234.5678 (seed=2)")
    for c, said in (("c1", "ppl 1234.5679 (typed) · unit seed=2"), ("c2", "step_ms 21.0000004 (typed) · unit seed=2")):
        assert main(["show", c]) == 0 and said in capsys.readouterr().out


def test_an_each_rule_on_arms_that_never_share_a_unit_says_so(human, capsys):
    """Arms recorded at different seeds are compared unpaired, so no unit has a difference for an --each rule to judge:
    it says that, never that an analysis reports none."""
    start(capsys, "--baseline", "a", "--candidate", "b")
    ok(capsys, "spec", "set", "e1", "seed", "--per-run")
    ok(capsys, "criterion", "add", "s", "-e", "e1", "--metric", "ppl", "--subject", "b", "--reference", "a", "--at-most", "0",
       "--each", "--id", "c1")
    for arm, seeds in (("a", (1, 2, 3)), ("b", (4, 5, 6))):
        for s in seeds:
            record(capsys, f"{arm}.ppl={10 + s / 10}", "--set", f"seed={s}")
    f = finding(capsys)
    assert f["says"]["rule"] == "criterion undetermined: the arms never share a unit, so no unit has a difference to judge"


def test_an_each_rule_names_a_unit_of_several_values_by_all_of_them(human, capsys):
    """A unit that is a cell of two per-run settings is named by both, and units are told apart by a semicolon. A rule
    met by every unit misses none."""
    start(capsys)
    for name in ("width", "loops"):
        ok(capsys, "spec", "set", "e1", name, "--per-run")
    ok(capsys, "criterion", "add", "s", "-e", "e1", "--metric", "ppl", "--at-most", "100", "--each", "--id", "c1")
    ok(capsys, "criterion", "add", "s", "-e", "e1", "--metric", "ppl", "--at-most", "110", "--each", "--id", "c2")
    for ppl, width, loops in (("99", "256", "2"), ("101", "512", "2"), ("102", "512", "4")):
        record(capsys, f"ppl={ppl}", "--set", f"width={width}", "--set", f"loops={loops}")
    f = finding(capsys)
    assert f["says"]["rule"] == "criterion not met: 2 of 3 units are above 100 (loops=2, width=512; loops=4, width=512)"
    assert f["says"]["units"] == "3 units" and f["comparison"]["unit"] == "unit"     # a cell, never a run
    assert ok(capsys, "compare", "e1").data["unit"] == "unit"                          # as rb compare averages it
    assert f["missed_units"] == ["loops=2 · width=512", "loops=4 · width=512"]
    met = finding(capsys, "c2")
    assert (met["rule_outcome"], met["missed_units"]) == ("meets", [])


def test_an_analysis_that_crosses_the_threshold_is_noted_as_the_runs_are(human, capsys):
    """An analysis's reading is read on the estimate the same way: -0.05 [-0.08, +0.01] meets at most -0.01 on the
    estimate, not across the whole interval. Read on the bound, or on each unit (an analysis gives none), nothing."""
    start(capsys, "--baseline", "none", "--candidate", "p01")
    for c, extra in (("c1", []), ("c2", ["--bound"]), ("c3", ["--each"])):
        ok(capsys, "criterion", "add", "s", "-e", "e1", "--metric", "change.val_loss", "--at-most", "-0.01", *extra, "--id", c)
    ok(capsys, "analysis", "add", "e1", "--compare", "p01/none:val_loss=-0.05[-0.08,0.01]", "--method", "paired bootstrap",
       "--unit", "image", "--units", "200")
    f = finding(capsys, "c1")
    assert (f["rule_outcome"], f["result"]) == ("meets", "unresolved")
    assert crossings(f) == ["the interval [-0.08, +0.01] crosses -0.01: met on the estimate, not across the whole interval"]
    assert [(g["rule_outcome"], crossings(g)) for g in (finding(capsys, "c2"), finding(capsys, "c3"))] == [("misses", []), ("undetermined", [])]


def test_a_cited_criterion_is_not_comparable_when_a_setting_differs_from_the_cited_value(human, capsys):
    start(capsys)
    Path("paper.txt").write_text("Table 3: ResNet-50 reaches 76.1 top-1 accuracy.\n", encoding="utf-8")
    Path("cfg.yaml").write_text("lr: 0.1\n", encoding="utf-8")
    ok(capsys, "spec", "set", "e1", "lr", "--source", "cfg.yaml#lr", "--cited", "0.2")
    env = ok(capsys, "criterion", "add", "the paper's number", "-e", "e1", "--metric", "top1", "--equals", "76.1", "--tolerance", "0.2",
             "--source", "paper.txt", "--quote", "76.1", "--id", "c1")
    assert env.data["object"]["origin"] == "cited"
    record(capsys, "top1=76.05")
    f = finding(capsys)
    assert f["rule_outcome"] == "undetermined" and f["says"]["rule"].startswith("criterion undetermined: not comparable: lr 0.1 here vs 0.2")
    assert problems(f) == [("not_comparable", "c1")] and notes(f, "cited_differs") == [("e1/lr", False)]


def test_a_cited_criterion_whose_file_does_not_state_the_number_is_refused(human, capsys):
    start(capsys)
    Path("paper.txt").write_text("Ours: 1.43 validation loss on ImageNet.\n", encoding="utf-8")
    code, env = rbj(capsys, "criterion", "add", "p", "-e", "e1", "--metric", "val_loss", "--equals", "1.5", "--tolerance", "0.1",
                    "--source", "paper.txt", "--quote", "1.43 validation loss")
    assert code == 2 and env.errors[0].code == "E_SOURCE_UNRESOLVED"


# ---------------------------------------------------------------- retracting


def test_a_retracted_run_stops_counting_and_stays_on_record(human, capsys):
    start(capsys)
    ok(capsys, "criterion", "add", "s", "-e", "e1", "--metric", "top1", "--at-least", "0.7", "--id", "c1")
    run = record(capsys, "top1=0.76")
    env = ok(capsys, "retract", run, "-m", "the eval set leaked")
    assert env.data["rested_on"] == ["criterion c1 counts it"]
    f = finding(capsys)
    assert (f["rule_outcome"], f["n"], f["observations"]) == ("untested", 0, [])
    shown = ok(capsys, "show", run).data["object"]
    assert shown["retracted"]["why"] == "the eval set leaked" and shown["retracted"]["by"] == "human:t"
    assert (human / ".rb" / "experiments" / "e1" / "runs" / f"{run}.yaml").is_file()


def test_an_agent_may_retract_what_nothing_rests_on(human, capsys, monkeypatch):
    start(capsys)
    monkeypatch.setenv("CLAUDECODE", "1")
    run = record(capsys, "top1=0.76")
    ok(capsys, "retract", run, "-m", "typed the wrong number")
    assert state(human).get(run, "run").retracted.by == "agent:claude-code"


# ---------------------------------------------------------------- edits outside rb


@pytest.fixture
def two_findings(human, capsys):
    """c1 on e1 and c2 on e2, each meeting its rule on one run of its own experiment with nothing in question. Returns
    the project root."""
    start(capsys)
    ok(capsys, "criterion", "add", "s", "-e", "e1", "--metric", "top1", "--at-least", "0.7", "--id", "c1")
    record(capsys, "top1=0.76")
    ok(capsys, "experiment", "add", "y", "--id", "e2")
    ok(capsys, "criterion", "add", "t", "-e", "e2", "--metric", "top1", "--at-least", "0.7", "--id", "c2")
    record(capsys, "top1=0.8", exp="e2")
    for c in ("c1", "c2"):
        f = finding(capsys, c)
        assert (f["rule_outcome"], f["integrity"]) == ("meets", []), c
    return human


def test_a_hand_edited_criterion_is_a_problem_for_its_own_finding_only(two_findings, capsys):
    """An edit outside rb is local: c1's finding names it and keeps its reading, and c2, which depends on nothing that
    changed, reads as before."""
    edit_criterion(two_findings, "c1", statement="something stronger")
    f = finding(capsys)
    assert problems(f) == [("edited_outside_rb", "c1")] and f["rule_outcome"] == "meets"
    assert "edited outside rb" in f["says"]["integrity"]
    other = finding(capsys, "c2")
    assert (other["rule_outcome"], other["integrity"]) == ("meets", [])


def test_a_comment_or_a_reformat_is_not_an_edit(two_findings, capsys):
    """rb compares content, not bytes: someone's comment in a record, or the record laid out another way (here as its
    canonical JSON, which is Rabbit Brain YAML too), changes no value, so nothing reads as edited outside rb."""
    p = criterion_file(two_findings)
    doc = rbyaml.loads(p.read_text(encoding="utf-8"))
    p.write_text("# agreed with the team before the run\n" + rbyaml.canonical_json(doc) + "\n", encoding="utf-8")
    f = finding(capsys)
    assert (f["rule_outcome"], f["integrity"]) == ("meets", [])
    code, env = rbj(capsys, "doctor")
    assert code == 0 and env.data["outcome"]["passed"] is True


def test_a_hand_edited_run_file_is_not_counted(two_findings, capsys):
    """The edited number is not read, the finding that read the run names it, and c2, which never read it, is as before."""
    [p] = list((two_findings / ".rb" / "experiments" / "e1" / "runs").glob("*.yaml"))
    doc = rbyaml.loads(p.read_text(encoding="utf-8"))
    doc["metrics"]["top1"] = 0.99
    p.write_text(rbyaml.dumps(doc), encoding="utf-8")
    f = finding(capsys)
    [o] = f["observations"]
    assert (o["counted"], o["reason"]) == (False, "edited outside rb")
    assert problems(f) == [("edited_outside_rb", doc["id"])] and f["rule_outcome"] == "undetermined"
    assert "edited outside rb" in f["says"]["rule"] and "no evidence yet" not in f["says"]["timing"]     # evidence exists, unread
    other = finding(capsys, "c2")
    assert (other["rule_outcome"], other["integrity"]) == ("meets", [])


def test_a_hand_edit_a_finding_depends_on_fails_any_fail_on_gate(two_findings, capsys):
    """c1 depends on the edited criterion, so any gate over it trips; e2's findings depend on nothing that changed, so
    its gates pass."""
    edit_criterion(two_findings, "c1", statement="something stronger")
    code, env = rbj(capsys, "status", "--fail-on", "unmet")
    assert code == 1 and env.data["outcome"]["failures"] == [{"code": "edited_outside_rb", "reason": "--fail-on edited_outside_rb"}]
    code, env = rbj(capsys, "status", "e2", "--fail-on", "unmet,integrity")
    assert code == 0 and env.data["outcome"]["passed"] is True


def test_a_hand_edit_no_finding_depends_on_still_fails_the_integrity_gate(two_findings, capsys):
    """No finding reads a question, so editing one by hand leaves every finding as it was, and a gate over the findings
    passes; the record was still changed outside rb, so the whole record's integrity gate and rb doctor fail."""
    q = ok(capsys, "question", "add", "does int8 hold up?").data["object"]["id"]
    p = two_findings / ".rb" / "questions" / f"{q}.yaml"
    doc = rbyaml.loads(p.read_text(encoding="utf-8"))
    doc["text"] = "does int8 hold up on every benchmark?"
    p.write_text(rbyaml.dumps(doc), encoding="utf-8")
    assert [finding(capsys, c)["integrity"] for c in ("c1", "c2")] == [[], []]
    assert rbj(capsys, "status", "--fail-on", "unmet")[0] == 0
    code, env = rbj(capsys, "status", "--fail-on", "integrity")
    assert code == 1 and ("edited_outside_rb", q) in [(i["code"], i["subject"]) for i in env.data["open"]]
    code, env = rbj(capsys, "doctor")
    assert code == 1 and [f["check"] for f in env.data["outcome"]["failures"]] == ["edited_outside_rb"]


def test_doctor_reports_a_hand_edit(two_findings, capsys):
    edit_criterion(two_findings, "c1", statement="something stronger")
    code, env = rbj(capsys, "doctor")
    assert code == 1 and [f["check"] for f in env.data["outcome"]["failures"]] == ["edited_outside_rb"]


@pytest.mark.parametrize("where", [
    "criteria",
    "run",
])
def test_an_unparseable_record_is_state_corrupt(two_findings, capsys, where):
    """c1's own file, or the file of the run it counts."""
    runs = two_findings / ".rb" / "experiments" / "e1" / "runs"
    p = criterion_file(two_findings) if where == "criteria" else next(runs.glob("*.yaml"))
    p.write_text("{not yaml", encoding="utf-8")
    for argv in (["status"], ["show", "c1"], ["context"]):
        code, env = rbj(capsys, *argv)
        assert code == 2 and env.errors[0].code == "E_STATE_CORRUPT", argv


def test_a_record_that_breaks_its_own_rules_is_state_corrupt(human, capsys):
    """A setting marked verified by hand, with nothing rb read behind it."""
    start(capsys)
    ok(capsys, "spec", "set", "e1", "lr", "0.1")
    p = experiment_file(human)
    doc = rbyaml.loads(p.read_text(encoding="utf-8"))
    doc["settings"][0]["status"] = "verified"
    p.write_text(rbyaml.dumps(doc), encoding="utf-8")
    code, env = rbj(capsys, "status")
    assert code == 2 and env.errors[0].code == "E_STATE_CORRUPT"


def test_a_setting_source_outside_the_project_is_refused(human, capsys, tmp_path_factory):
    elsewhere = tmp_path_factory.mktemp("elsewhere") / "cfg.yaml"
    elsewhere.write_text("lr: 0.1\n", encoding="utf-8")
    start(capsys)
    code, env = rbj(capsys, "spec", "set", "e1", "lr", "0.1", "--source", f"{elsewhere}#lr")
    assert code == 1 and env.data["object"]["status"] == "provisional"
    assert [f["code"] for f in env.data["outcome"]["failures"]] == ["E_SOURCE_OUTSIDE"]


def test_a_cited_criterion_source_outside_the_project_is_refused(human, capsys, tmp_path_factory):
    elsewhere = tmp_path_factory.mktemp("elsewhere") / "paper.txt"
    elsewhere.write_text("We reach 76.1 top-1.\n", encoding="utf-8")
    start(capsys)
    code, env = rbj(capsys, "criterion", "add", "p", "-e", "e1", "--metric", "top1", "--equals", "76.1", "--tolerance", "0.1",
                    "--source", str(elsewhere), "--quote", "76.1")
    assert code == 2 and env.errors[0].code == "E_SOURCE_OUTSIDE"


# ---------------------------------------------------------------- the log, the files, concurrent writers


def test_init_keeps_the_lock_out_of_git_and_merges_the_log_by_line(human, capsys):
    ok(capsys, "init", "integrity")
    assert (human / ".rb" / ".gitignore").read_text(encoding="utf-8").split() == [".lock", "*.tmp", "cache/", "journal/"]
    assert (human / ".rb" / ".gitattributes").read_text(encoding="utf-8").splitlines() == ["* -text", "log.jsonl merge=union"]


def test_every_write_is_logged_with_its_actor_surface_path_and_hash(human, capsys, monkeypatch):
    start(capsys)
    ok(capsys, "criterion", "add", "s", "-e", "e1", "--metric", "top1", "--at-least", "0.7", "--id", "c1")
    monkeypatch.setenv("CLAUDECODE", "1")
    run = record(capsys, "top1=0.76")
    rows = state(human).log(None)
    assert [(r["op"], r["kind"], r["id"]) for r in rows] == [
        ("init", "project", "integrity"), ("add", "experiment", "e1"), ("add", "criterion", "c1"), ("record", "run", run)]
    for r in rows:
        assert {"at", "actor", "actor_via", "via", "op", "kind", "id", "sha256", "path"} <= set(r)
        assert r["via"] == "cli"
    assert [r["path"] for r in rows] == [".rb/project.yaml", ".rb/experiments/e1/experiment.yaml",
                                         ".rb/experiments/e1/criteria/c1.yaml", f".rb/experiments/e1/runs/{run}.yaml"]
    assert [(r["actor"], r["actor_via"]) for r in rows][-2:] == [("human:t", "git-email" if HAS_GIT else "login"), ("agent:claude-code", "detected:CLAUDECODE")]
    # The hash is of the record's content as stored: c1's file without its rb/kind header.
    entry = {k: v for k, v in rbyaml.loads(criterion_file(human).read_text(encoding="utf-8")).items() if k not in ("rb", "kind")}
    assert rows[2]["sha256"] == hashlib.sha256(rbyaml.canonical_json(entry).encode("utf-8")).hexdigest()


def test_ids_are_a_kind_prefix_and_six_random_characters(human, capsys):
    start(capsys)
    q = ok(capsys, "question", "add", "why").data["object"]["id"]
    e = ok(capsys, "experiment", "add", "x2").data["object"]["id"]
    c = ok(capsys, "criterion", "add", "s", "-e", e, "--metric", "m", "--at-most", "1").data["object"]["id"]
    run = record(capsys, "m=0.5", exp=e)
    d = ok(capsys, "decide", c, "investigate", "-m", "look again").data["object"]["id"]
    for prefix, id_ in (("q", q), ("e", e), ("c", c), ("run", run), ("d", d)):
        assert re.fullmatch(rf"{prefix}[a-z2-7]{{6}}", id_), id_


def test_concurrent_writers_lose_nothing(human, capsys):
    ok(capsys, "init", "integrity")
    ids, errors = [], []

    def write(i):
        try:
            rec = state(human)
            for j in range(5):
                ids.append(rec.add_question(f"question {i}.{j}"))
        except Exception as exc:  # noqa: BLE001 - collected and asserted below
            errors.append(exc)

    threads = [threading.Thread(target=write, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    rec = state(human)
    assert errors == [] and len(set(ids)) == 40
    assert len(list((human / ".rb" / "questions").glob("*.yaml"))) == 40
    assert sum(1 for r in rec.log(None) if r["kind"] == "question") == 40
    assert sorted(q.id for q in rec.records("question")) == sorted(ids)
    assert not [i for i in rec.status()["open"] if i["code"] == "edited_outside_rb"]      # every one is what rb wrote
    assert not list((human / ".rb").rglob("*.tmp"))


@pytest.mark.skipif(not HAS_GIT, reason="the committed log is read from git")
def test_rewriting_a_committed_log_line_is_a_problem_for_every_finding(human, capsys):
    """The log only grows. A line that was committed and is now gone or changed means the record was rewritten. Every
    finding rests on the log, so each names it and any --fail-on gate trips; the integrity gate and rb doctor fail.
    Restore and adopt leave it to git, and say so once."""
    start(capsys)
    ok(capsys, "criterion", "add", "s", "-e", "e1", "--metric", "top1", "--at-least", "0.7", "--id", "c1")
    record(capsys, "top1=0.76")
    git("add", "-A")
    git("commit", "-qm", "state")
    ok(capsys, "question", "add", "appending is fine")
    assert rbj(capsys, "status", "--fail-on", "unestablished")[0] == 0
    log = human / ".rb" / "log.jsonl"
    lines = log.read_text(encoding="utf-8").splitlines()
    lines[0] = lines[0].replace('"op": "init"', '"op": "inited"')
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert problems(finding(capsys)) == [("edited_outside_rb", "log.jsonl")]
    code, env = rbj(capsys, "status", "--fail-on", "unmet")
    assert code == 1 and any(i["code"] == "edited_outside_rb" and "log.jsonl" in i["what"] for i in env.data["open"])
    assert env.data["gate"]["integrity"] is True
    code, env = rbj(capsys, "doctor")
    assert code == 1 and any("the log was rewritten" in c["detail"] for c in env.data["checks"])
    for argv, verb in ((["--restore"], "restored"), (["--adopt", "--why", "keep it"], "adopted")):
        main(["doctor", *argv])
        said = [line for line in capsys.readouterr().out.splitlines() if line.startswith(("left", verb))]
        assert len(said) == 1 and said[0].startswith("left .rb/log.jsonl as it is: "), said
