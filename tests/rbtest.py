"""What the tests drive rb with, and what they read its record through.

`main` is rb's command line as a test calls it: it prints what rb printed and returns rb's exit code. The record is
read the way anything outside rb reads it: through the SDK (`state()`, where every read asks rb) and through the
views the user's screen draws (`home()`, `object_card()`, `found_card()`). `rbyaml` is the reference reader of the
files themselves, for the tests that write a record by hand or check its bytes; `cli_parser` is the command line as it
is declared, for the tests that hold the docs to the commands that exist.

Which rb: RB_BIN names it; without RB_BIN, the rb installed beside this Python (pip install -e . builds one).
"""
from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Optional

import rabbit_brain
import rbyaml  # noqa: F401 - the reference reader (tests/rbyaml.py), for `from rbtest import rbyaml`
from rabbit_brain import _rb

RB_BIN = os.environ.get("RB_BIN")
ROOT = Path(__file__).resolve().parents[1]
# the environment variables rb reads an agent from (detect_runtime and CODEX_SIGNS in rust/src/actor.rs), RB_ACTOR,
# which names whoever acts, RB_MODEL and RB_PROVIDER, which an agent gives its model and provider by for one command,
# and Claude Code's switches to another provider's API (CLAUDE_CODE_ELSEWHERE there), while which rb infers no provider
AGENT_SIGNS = ("CLAUDECODE", "AI_AGENT", "CODEX_SANDBOX", "CODEX_SANDBOX_NETWORK_DISABLED", "CODEX_CI", "CODEX_THREAD_ID", "CODEX_SESSION_ID",
               "CURSOR_AGENT", "GITHUB_ACTIONS", "RB_ACTOR", "RB_MODEL", "RB_PROVIDER", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX",
               "CLAUDE_CODE_USE_FOUNDRY", "CLAUDE_CODE_USE_ANTHROPIC_AWS", "CLAUDE_CODE_USE_ANTHROPIC_GOOGLE_CLOUD", "CLAUDE_CODE_USE_MANTLE",
               "CLAUDE_CODE_USE_GATEWAY")
# what else an agent's runtime leaves in its commands, on which rb confirm and rb ui fail closed (SESSION_LEFTOVERS there)
SESSION_LEFTOVERS = ("CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_CHILD_SESSION", "CLAUDE_CODE_BRIDGE_SESSION_ID", "CLAUDE_CODE_EXECPATH", "CLAUDE_PID",
                     "CLAUDE_CODE_MESSAGING_SOCKET", "CLAUDE_CODE_MESSAGING_TOKEN", "CLAUDE_CODE_SESSION_ATTENDED")
# the environment variables rb reads a chat session (or a CI run) from (SESSION_SIGNS there)
SESSION_SIGNS = ("RB_SESSION", "CLAUDE_CODE_SESSION_ID", "CODEX_SESSION_ID", "CODEX_THREAD_ID", "CURSOR_CONVERSATION_ID", "CURSOR_SESSION_ID",
                 "CURSOR_TRACE_ID", "GROK_SESSION_ID", "GITHUB_RUN_ID")
# every variable rb reads an agent, an actor or a chat session from: a test that plays the user at their own terminal
# clears them all (as_the_user), so it reads the same in an agent session and in CI, where GitHub sets GITHUB_RUN_ID
AGENT_VARS = (*AGENT_SIGNS, *SESSION_SIGNS, *SESSION_LEFTOVERS)
# the variables rb takes for CI, where analytics collects nothing (CI_VARIABLES in rust/src/telemetry.rs): a test that
# plays the user's terminal for analytics clears every one
CI_VARIABLES = ("CI", "GITHUB_ACTIONS", "GITLAB_CI", "BUILDKITE", "TF_BUILD", "JENKINS_URL", "TEAMCITY_VERSION", "CODEBUILD_BUILD_ARN",
                "bamboo_planKey", "BUILDER_OUTPUT")


def _declared() -> Any:
    """rb's command line as it is declared (rust/src/cli_parser.py): the argparse tree the binary's own is made from."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("rb_cli_parser", ROOT / "rust" / "src" / "cli_parser.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cli_parser = _declared()      # build_parser(), UsageError, COMMANDS, LEDGER_COMMANDS


def registered_commands() -> set:
    """Every command rb has, as its words: "status", "run add", "journal run"."""
    names = set()
    for action in cli_parser.build_parser()._subparsers._group_actions:
        for name, sub in action.choices.items():
            names.add(name)
            for a in sub._subparsers._group_actions if sub._subparsers else []:
                names.update(f"{name} {subname}" for subname in a.choices)
    return names


def command() -> list:
    """The command line that starts rb, for a test that runs it as its own process (a server, a pipe)."""
    return _rb.command()


def environment() -> dict:
    env = dict(os.environ)
    env.setdefault("RB_PYTHON", sys.executable)
    return env


def main(argv: Optional[list] = None) -> int:
    """`rb <argv>` here: prints what rb printed (stdout and stderr, each where it went), returns rb's exit code."""
    argv = list(sys.argv[1:] if argv is None else argv)
    r = subprocess.run([*command(), *argv], cwd=os.getcwd(), env=environment(), capture_output=True, stdin=subprocess.DEVNULL)
    sys.stdout.write(r.stdout.decode("utf-8", "replace"))
    sys.stderr.write(r.stderr.decode("utf-8", "replace"))
    return r.returncode


def as_the_user(monkeypatch: Any, *also: str) -> None:
    """The user at their own terminal, in no chat session, whatever runs the tests: every variable in AGENT_VARS is
    cleared, and each one named in `also`."""
    for name in (*AGENT_VARS, *also):
        monkeypatch.delenv(name, raising=False)


def confirm_designs(root: Any = None) -> list:
    """The user confirms, in their own terminal, every experiment design a session wrote any part of, each as rb confirm
    lists it (rb confirm <experiment>@<hash>), and nothing else: for a test about what else waits on a record a session
    set up. Returns the experiments whose design it confirmed."""
    env = {k: v for k, v in environment().items() if k not in AGENT_VARS}
    cwd = str(root) if root else os.getcwd()

    def rb(*argv: str) -> subprocess.CompletedProcess:
        return subprocess.run([*command(), *argv, "--json"], cwd=cwd, env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL)
    pending = json.loads(rb("confirm").stdout)["data"]["designs_pending"]
    for d in pending:
        r = rb(*d["confirm"].split()[1:])
        assert r.returncode == 0, r.stdout
    return [d["experiment"] for d in pending]


def at_home(monkeypatch: Any, home: Any) -> None:
    """The user's home directory is `home` for this test: HOME, and what Windows calls it."""
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))


def in_short(whole: str) -> str:
    """`whole`, a place's path as git lists it, as rb's screens name it in short when it is the main checkout or outside
    it (places.rs short_path): from ~ when it is under the home folder rb reads, else whole, wherever the tests run."""
    home = Path(os.path.expanduser("~")).resolve()
    try:
        rel = Path(whole).resolve().relative_to(home)
    except ValueError:
        return whole
    # a home folder at the top of the file system names nothing apart
    return whole if home == Path(home.anchor) else "~" if not rel.parts else f"~/{rel.as_posix()}"


def fake_rb(folder: Any, says: Optional[str]) -> str:
    """Something that is not rb where rb should be: whatever it is asked, it answers `says` (or nothing, and fails).
    A shell script, or on Windows the batch file that is its counterpart there."""
    if sys.platform == "win32":
        p = Path(folder) / ("answers.cmd" if says else "silent.cmd")
        p.write_bytes(("@echo off\r\n" + (f"echo {says}\r\n" if says else "exit /b 1\r\n")).encode("utf-8"))
        return str(p)
    p = Path(folder) / ("answers" if says else "silent")
    p.write_text("#!/bin/sh\ncat > /dev/null\n" + (f"echo '{says}'\n" if says else "exit 1\n"), encoding="utf-8")
    p.chmod(0o755)
    return str(p)


def state(root: Any = None, *, agent: Optional[str] = None) -> Any:
    """The record at `root` (default: the one here, or above), through the SDK: every read asks rb again."""
    return rabbit_brain.open(root, agent=agent)


def home(root: Any = None, since_baseline: Optional[int] = None) -> Any:
    """What the user's screen opens on: the questions, what rb found, the catalogue's rows."""
    from rabbit_brain import tui
    return tui.home_view(state(root).root, since_baseline)


def object_card(subject: str, root: Any = None) -> Any:
    """A record's card on the screen. KeyError when the catalogue holds nothing by that name."""
    from rabbit_brain import tui
    return tui.object_card(state(root).root, subject)


def found_card(request_id: str, root: Any = None) -> Any:
    """The card of something rb found in the repository. KeyError when nothing found goes by that id now."""
    from rabbit_brain import tui
    return tui.found_card(state(root).root, request_id)


def mark_seen(root: Any = None) -> tuple:
    """The user looked: (how many of the record's lines they had seen by their last look, or -1 for a first look; its
    length now)."""
    from rabbit_brain import tui
    old, lines = tui.ask(state(root).root, "mark_seen")
    return old, lines


class Mcp:
    """An agent's connection to `rb mcp` here, as an MCP client holds it: one JSON-RPC message a line, each way."""

    def __init__(self, agent: Optional[str] = None, root: Any = None, env: Optional[dict] = None) -> None:
        """`root` is where the server runs (default here), `env` its whole environment (default this process's)."""
        self._stderr = tempfile.TemporaryFile()
        self.proc = subprocess.Popen([*command(), "mcp", *(["--agent", agent] if agent else [])], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=self._stderr, cwd=root, env=env or environment())

    def handle(self, message: dict) -> Optional[dict]:
        """Send one message; return its answer. A notification (no id) has none, and the next answer read must be the
        next request's, so a stray line written for a notification fails the test that sent it."""
        self.proc.stdin.write((json.dumps(message) + "\n").encode("utf-8"))
        self.proc.stdin.flush()
        if "id" not in message:
            return None
        line = self.proc.stdout.readline()
        if not line:
            self._stderr.seek(0)
            raise AssertionError(f"rb mcp stopped answering: {self._stderr.read().decode('utf-8', 'replace')[-2000:]}")
        answer = json.loads(line)
        assert answer.get("id") == message["id"], (message, answer)
        return answer

    def close(self) -> int:
        """Hang up, as a client does: rb mcp ends when its input does."""
        self.proc.stdin.close()
        code = self.proc.wait(timeout=30)
        self.proc.stdout.close()
        self._stderr.close()
        return code


class Open:
    """`rb open` running here as its own process, the way the user starts it. `.url` is the address it printed
    (http://127.0.0.1:<port>), `.printed()` everything it has said so far."""

    def __init__(self, root: Any = None) -> None:
        handle, self._said = tempfile.mkstemp(prefix="rb-open-", suffix=".txt")
        os.close(handle)
        self._err = open(self._said, "ab")
        # Windows: a process group of its own, so the Ctrl-Break that stops it is not the test run's as well
        own_group = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if sys.platform == "win32" else {}
        self.proc = subprocess.Popen([*command(), "open", "--port", "0"], cwd=root, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                     stderr=self._err, env=environment(), **own_group)
        deadline = time.monotonic() + 60
        while True:
            said = self.printed()
            link = re.search(r"^rb open · (http://([0-9.]+):(\d+))/$", said, flags=re.M)
            # the address is the first line it prints and "Ctrl-C stops the server." the last: wait for all of it, so a
            # test that reads what it printed never reads half
            if link and said.rstrip().endswith("stops the server."):
                break
            if self.proc.poll() is not None or time.monotonic() > deadline:
                said, code = self.printed(), self.proc.poll()
                if code is None:            # it did not start in time: stop it, so nothing outlives the test
                    self.proc.kill()
                    self.proc.wait()
                self._err.close()
                raise AssertionError(f"rb open did not start (exit {code}): {said}")
            time.sleep(0.02)
        self.url, self.host, self.port = link.group(1), link.group(2), int(link.group(3))

    def printed(self) -> str:
        return Path(self._said).read_text(encoding="utf-8", errors="replace")

    def close(self) -> None:
        """Ctrl-C, as its last line says (on Windows Ctrl-Break, the one a process can send another)."""
        if self.proc.poll() is None:
            try:
                self.proc.send_signal(signal.CTRL_BREAK_EVENT if sys.platform == "win32" else signal.SIGINT)
                self.proc.wait(timeout=30)
            except (OSError, subprocess.TimeoutExpired):
                self.proc.kill()
                self.proc.wait()
        self._err.close()
        try:
            os.unlink(self._said)
        except OSError:
            pass


# ---------------------------------------------------------------- a record a human and three agents wrote

# Every name, id and session here is made up. The human is human:writer-ana, named from git at their own terminal.
CLAUDE_CODE = {"CLAUDECODE": "1", "AI_AGENT": "claude-code_2-1-295_agent", "CLAUDE_CODE_SESSION_ID": "sess-wf-claude-7c41"}
CODEX = {"CODEX_SANDBOX": "seatbelt", "CODEX_THREAD_ID": "thread-wf-codex-93b2"}
MCP_CLIENT = {"name": "wf-mcp-client", "version": "3.4.5"}
MCP_SESSION = "sess-wf-mcp-2d7f"
# the branch an agent's run was recorded on, and one it left unmerged, named as agents name theirs
AGENT_BRANCHES = ("claude/try-1", "codex/try-2")
# the worktree an agent printed a run in and removed before anyone recorded it, named as agents name theirs
FOUND_WORKTREE = "work-codex-wt"
# what says who wrote: each writer's name and id, each session id, how rb knew ("detected:"), the channel (" via ") and
# an agent's branch or worktree
WRITER_MARKS = ("writer-ana", "claude-code", "codex", "wf-mcp-client", "agent:", "human:", "sess-wf-claude-7c41", "thread-wf-codex-93b2",
                MCP_SESSION, "detected:", " via ", *AGENT_BRANCHES, FOUND_WORKTREE)
# the fields of rb's JSON that keep who made a write, how rb knew, through what, in which session, with which agent, and
# on which branch or worktree: the back end's, never a sentence
WRITER_FIELDS = ("actor", "by", "created_by", "set_by", "declared_by", "frozen_by", "gate_by", "session", "via", "actor_via", "ignored_rb_actor", "agent",
                 "branch", "worktree")
# what rb keeps of the agents here (agent{}): their models, providers, and Claude Code's version (from AI_AGENT)
AGENT_FACTS = ("test-model-wf-7b", "test-model-wf-mcp", "anthropic", "openai", "2.1.295")
# the words that name a role, which no screen uses: rb's terminal text alone may, to route work
ROLE_WORDS = ("agent", "human", "session")
# writers_record's own author text that names a role: shown verbatim wherever rb shows it, as everything authored is
ROLE_WORDS_IN_AUTHOR_TEXT = ("a note on e1 by an agent", "a project note by another agent", "typed in the agent's terminal")
# rb's own words that name the reader's own tool, never a writer: Rerun this research (REPRODUCE-WORDS.md, owner-approved
# Oct 26), on an export with a clone address
RB_WORDS_FOR_THE_READERS_AGENT = ("Copy this prompt into your coding agent",)
# the user's words writers_record passes with --words: rb's text, rb open and rb ui say them, and a page never does
USER_WORDS = ("call it a study", "ask about the held-out set", "make it -0.5", "make the warmup 200 steps", "check it again")


def writer_marks(text: str) -> list:
    """What in `text` says who wrote: WRITER_MARKS found there."""
    return [m for m in WRITER_MARKS if m in text]


def screen_form(node: Any, parent: str = "") -> Any:
    """rb's data as a screen carries it: every key kept, and "" under every field that keeps who wrote, whatever it held,
    so a null where another writer has a value tells nobody apart (a receipt's produced.existed.by says what an output's
    time rests on, and stays)."""
    if isinstance(node, dict):
        return {k: "" if k in WRITER_FIELDS and not (k == "by" and parent == "existed") else screen_form(v, k) for k, v in node.items()}
    if isinstance(node, list):
        return [screen_form(v, parent) for v in node]
    return node


def shown_strings(node: Any, path: tuple = ()) -> list:
    """Every text in a JSON answer that is not a field keeping who wrote: (path, text). A session's own log line keeps
    its session as its id, and an actor as rb reports it (`actor`: its id, how rb knew) is such a field too."""
    out: list = []
    if isinstance(node, dict):
        session_line = node.get("op") == "session"
        for k, v in node.items():
            if k in WRITER_FIELDS or (session_line and k == "id"):
                continue
            out.extend(shown_strings(v, (*path, k)))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            out.extend(shown_strings(v, (*path, i)))
    elif isinstance(node, str):
        out.append((path, node))
    return out


class Writers:
    """A record a human and three agents wrote, with a write of every kind rb has (`writers_record` makes it). `run`
    is rb there as one of them, `said` what every write printed (the command's words and its output), `ids` what the
    writes made, by name, and `mcp_writes` what each of the MCP client's tool calls answered. HUMAN is the human's
    environment (no agent variables), CLAUDE_CODE and CODEX the agents'."""

    HUMAN: dict = {}

    def __init__(self, root: Path) -> None:
        self.root = root
        self.said: list = []
        self.ids: dict = {}
        self.mcp_writes: list = []
        # beside the record, so nothing is left in the system's temporary folder
        gitconfig = root.parent / f"{root.name}.gitconfig"
        gitconfig.parent.mkdir(parents=True, exist_ok=True)
        gitconfig.write_text("", encoding="utf-8")
        self.env = {k: v for k, v in environment().items() if k not in AGENT_VARS}
        self.env.update(GIT_CONFIG_GLOBAL=str(gitconfig), GIT_CONFIG_NOSYSTEM="1", RB_TELEMETRY="0", DO_NOT_TRACK="1")

    def git(self, *args: str) -> str:
        return subprocess.run(["git", *args], cwd=self.root, env=self.env, check=True, capture_output=True, text=True).stdout.strip()

    def run(self, who: dict, *argv: str, cwd: Optional[Path] = None) -> subprocess.CompletedProcess:
        """rb <argv> here (or in `cwd`) as `who`; stdout and stderr as text."""
        return subprocess.run([*command(), *argv], cwd=cwd or self.root, env={**self.env, **who}, capture_output=True, stdin=subprocess.DEVNULL,
                              encoding="utf-8", errors="replace")

    def rb(self, who: dict, *argv: str, cwd: Optional[Path] = None) -> str:
        """rb <argv> as `who`, which must do its job (exit 0 or 1): what it printed, stdout then stderr."""
        r = self.run(who, *argv, cwd=cwd)
        assert r.returncode in (0, 1), (argv, r.returncode, r.stdout, r.stderr)
        return r.stdout + r.stderr

    def json(self, who: dict, *argv: str) -> dict:
        """rb <argv> --json as `who`: its envelope, which must be ok."""
        r = self.run(who, *argv, "--json")
        env = json.loads(r.stdout)
        assert r.returncode in (0, 1) and env["ok"], (argv, r.returncode, r.stdout, r.stderr)
        return env

    def write(self, who: dict, *argv: str, name: Optional[str] = None) -> str:
        """A write as `who`, kept in `said`; its log line's id, kept as `name` when one is given."""
        self.said.append((" ".join(argv), self.rb(who, *argv)))
        lines = [json.loads(line) for line in (self.root / ".rb" / "log.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
        made = [line for line in lines if line.get("op") != "session"][-1]["id"]
        if name:
            self.ids[name] = made
        return made

    def mcp(self) -> "WritersMcp":
        """A connection to rb mcp here as the MCP client (MCP_CLIENT), in its session (MCP_SESSION)."""
        return WritersMcp(self)


class WritersMcp:
    """The MCP client's connection: `tool` calls one tool and gives back its answer, `read` one resource's text."""

    def __init__(self, rec: Writers) -> None:
        self.server = Mcp(root=rec.root, env={**rec.env, "RB_SESSION": MCP_SESSION})
        self.n = 1
        self.answers: list = []
        self.server.handle({"jsonrpc": "2.0", "id": self.n, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "clientInfo": MCP_CLIENT}})
        self.server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def tool(self, name: str, **arguments: Any) -> dict:
        self.n += 1
        answer = self.server.handle({"jsonrpc": "2.0", "id": self.n, "method": "tools/call", "params": {"name": name, "arguments": arguments}})
        assert "error" not in answer and not answer["result"].get("isError"), answer
        self.answers.append((name, answer["result"]))
        return answer["result"]

    def read(self, uri: str) -> str:
        self.n += 1
        answer = self.server.handle({"jsonrpc": "2.0", "id": self.n, "method": "resources/read", "params": {"uri": uri}})
        return answer["result"]["contents"][0]["text"]

    def close(self) -> None:
        self.server.close()


def writers_record(root: Path) -> Writers:
    """A git repository at `root` holding a record four writers made: a human at their own terminal
    (human:writer-ana, named from git), Claude Code (CLAUDE_CODE), Codex (CODEX) and an MCP client (MCP_CLIENT, in
    MCP_SESSION). It has a write of every kind rb has, by more than one writer where a sentence could name them: a
    question moved, notes, settings with a call on each (vouched, rejected, a difference accepted), a fixed design
    factor, criteria with question links and a relation declared from both sides, a freeze with amendments, exposures
    (inspected, evaluated by a run, at a time nobody recorded), runs (one recorded on the branch claude/try-1), one
    retracted and one superseded in part, an analysis retracted, calls on criteria, an experiment and a question, a
    retraction an agent made of a primary criterion, an RB_ACTOR an agent session ignored, an rb reconcile --apply, an
    adoption, work left on the unmerged branch codex/try-2, and two runs nobody recorded that rb found (FOUND_WORKTREE):
    one Codex printed here, one it printed in a worktree it then removed. The calls the agents record are proposed, and
    the human confirms each but one (parked), which still waits; `investigating` names the confirmation of the agent's
    investigate, and `investigating_proposed` the call it confirms."""
    root.mkdir(parents=True, exist_ok=True)
    rec = Writers(root)
    P, CC, CX = Writers.HUMAN, CLAUDE_CODE, CODEX
    w = rec.write
    rec.git("init", "-q")
    rec.git("symbolic-ref", "HEAD", "refs/heads/main")
    rec.git("config", "user.email", "writer-ana@example.com")
    rec.git("config", "user.name", "writer ana")
    (root / "README.md").write_text("# Mixed writers\n", encoding="utf-8")
    (root / "configs").mkdir()
    (root / "configs" / "train.yaml").write_text("optim:\n  lr: 0.1\n", encoding="utf-8")
    rec.git("add", "-A")
    rec.git("commit", "-qm", "start")
    w(P, "init", "Mixed writers", "--no-agents")
    w(P, "project", "title", "Mixed writers study", "--why", "a clearer theme", "--words", "call it a study")
    rec.said.append(("context --model test-model-wf-7b", rec.rb(CC, "context", "--model", "test-model-wf-7b")))
    w(P, "question", "add", "Is the wide layer better?", "--id", "q1")
    w(CC, "question", "add", "Does it hold on the held-out set?", "--id", "q2", "--parent", "q1", "--words", "ask about the held-out set")
    w(CX, "question", "move", "q2", "--top", "--why", "it stands on its own")
    w(P, "metric", "add", "top1", "--maximize", "--unit", "acc", "--alias", "accuracy")
    w(CC, "experiment", "add", "Wide against base", "--id", "e1", "-q", "q1", "--baseline", "base", "--candidate", "wide",
      "--hypothesis", "the wide layer gains top1", "-m", "more capacity", "--note", "a first note on e1")
    w(CX, "experiment", "gate", "e1", "--if-holds", "ship the wide layer", "--if-not", "keep the base layer", "--why", "plainer words")
    w(CC, "experiment", "add", "Wide again, later", "--id", "e2", "--baseline", "base", "--candidate", "wide")
    w(CX, "experiment", "file", "e2", "q2")
    w(P, "variant", "add", "e1", "abl", "--role", "ablation")
    w(P, "note", "add", "a note on the project by the user")
    w(CC, "note", "add", "a note on e1 by an agent", "-e", "e1")
    w(CX, "note", "add", "a project note by another agent")
    w(P, "spec", "set", "e1", "optim.lr", "--source", "configs/train.yaml#optim.lr")
    w(CX, "spec", "verify", "e1", "optim.lr")
    w(CC, "spec", "set", "e1", "cuda", "12.1", "--no-verify")
    w(CC, "decide", "e1/cuda", "accept", "--why", "read it off the cluster image", name="vouched")
    w(CX, "spec", "set", "e1", "data.workers", "8", "--no-verify")
    w(CX, "decide", "e1/data.workers", "reject", "--why", "eight workers starve the loader", name="rejected")
    w(P, "spec", "set", "e1", "warmup", "100", "--no-verify")
    w(P, "spec", "set", "e1", "wide.dropout", "0.1", "--no-verify")
    w(P, "spec", "set", "e1", "base.dropout", "0.2", "--no-verify")
    for setting in ("seed", "width"):
        w(P, "spec", "set", "e1", setting, "--per-run")
    w(CX, "spec", "set", "e1", "width", "--design-factor", "--why", "a fixed grid of widths")
    w(CC, "spec", "vary", "e2", "dropout")
    w(P, "resource", "add", "holdout", "--type", "dataset", "--name", "the held-out set")
    w(CX, "resource", "locate", "holdout", "local", "data/holdout")
    w(CX, "resource", "expose", "holdout", "inspected", "--why", "looked at the held-out labels")
    w(P, "criterion", "add", "wide gains top1", "-e", "e1", "-q", "q1", "--metric", "change.top1", "--at-least", "0", "--id", "c1",
      "--when-met", "the wide layer is better")
    w(CC, "criterion", "add", "wide stays above base", "-e", "e1", "-q", "q1", "--metric", "change.top1", "--at-least", "-0.5", "--id", "c2",
      "--when-met", "the wide layer is no worse", "--words", "make it -0.5")
    w(CX, "criterion", "file", "c2", "q2", "--when-met", "it holds on the held-out set", "--why", "the same comparison")
    w(P, "criterion", "relate", "c1", "reverses", "c2", "--orientation", "same", "--why", "both read one change")
    w(CC, "criterion", "relate", "c2", "reverses", "c1", "--orientation", "same", "--why", "the same change, read from c2")
    w(P, "freeze", "e1", "-m", "before the runs")
    w(CC, "spec", "set", "e1", "warmup", "200", "--no-verify", "--amend", "--why", "a longer warmup", "--words", "make the warmup 200 steps")
    # a run recorded on a branch named as an agent names its own, then merged
    rec.git("checkout", "-q", "-b", AGENT_BRANCHES[0])
    w(CC, "run", "add", "e1", "wide.top1=0.71", "base.top1=0.70", "--set", "seed=1", "--set", "width=256", "--input", "holdout:eval",
      "--host", "gpu=A100", "--command", "python eval.py --seed 1", name="run_claude")
    rec.git("add", "-A")
    rec.git("commit", "-qm", "a run on the branch")
    rec.git("checkout", "-q", "main")
    rec.git("merge", "-q", "--ff-only", AGENT_BRANCHES[0])
    w(CX, "run", "add", "e1", "wide.top1=0.73", "base.top1=0.70", "--set", "seed=2", "--set", "width=512", name="run_codex")
    w(P, "run", "add", "e1", "wide.top1=0.69", "base.top1=0.70", "--set", "seed=3", "--set", "width=768", name="run_human")
    w(CX, "retract", rec.ids["run_human"], "--why", "seed 3 read the wrong split")
    w(CX, "run", "add", "e1", "wide.top1=0.75", "base.top1=0.70", "--set", "seed=4", "--set", "width=512", name="run_superseded")
    w(P, "retract", rec.ids["run_superseded"], "--scope", "wide.top1", "--why", "the wide number read the wrong split")
    w(CX, "criterion", "add", "wide gains a point", "-e", "e1", "--metric", "change.top1", "--at-least", "0.01", "--id", "c3",
      "--amend", "--why", "a stricter rule")
    w(CC, "run", "add", "e2", "wide.top1=0.72", "base.top1=0.70", "--produced-at", "unknown", name="run_unknown")
    w(CX, "criterion", "add", "e2 gains", "-e", "e2", "--metric", "change.top1", "--at-least", "0", "--id", "c4", "-q", "q2")
    w(P, "resource", "add", "devset", "--type", "dataset", "--name", "the dev set")
    client = rec.mcp()
    try:
        for name, arguments in [
            ("context", {"model": "test-model-wf-mcp"}),
            ("note_add", {"text": "a note by the MCP client", "experiment": "e2"}),
            ("criterion_add", {"statement": "e2 stays above base", "experiment": "e2", "metric": "change.top1", "at_least": -1, "id": "c5",
                               "question": ["q2"], "when_met": "the later run is no worse"}),
            ("run_add", {"experiment": "e2", "metrics": {"wide.top1": 0.74, "base.top1": 0.70}}),
            ("decide", {"subject": "e1/dropout", "action": "accept", "why": "the dropout differs on purpose"}),
            ("experiment_relate", {"experiment": "e2", "relation": "follows_from", "target": "e1", "note": "the later look"}),
            ("resource_expose", {"id": "devset", "how": "evaluated", "at": "unknown", "why": "scored it once"}),
        ]:
            client.tool(name, **arguments)
    finally:
        client.close()
    rec.mcp_writes = client.answers
    w(P, "confirm", "--all")                                  # the vouch, the rejected value and the MCP client's accept
    w(CX, "analysis", "add", "e2", "--compare", "wide/base:top1=0.02[0.01,0.03]", "--method", "paired bootstrap", "--unit", "seed",
      "--units", "3", "--id", "a1")
    w(CX, "retract", "a1", "--why", "the bootstrap read one seed twice")
    w(P, "decide", "c1", "accept", "--why", "within budget", name="accepted")
    w(CC, "decide", "c2", "investigate", "--why", "one more seed", "--words", "check it again", name="investigating_proposed")
    w(CX, "decide", "c3", "reject", "--why", "too strict", name="rejected_c3_proposed")
    w(CX, "decide", "e2", "park", "--why", "waiting on data", "--revisit", "when the new images are in", name="parked")
    w(P, "confirm", rec.ids["investigating_proposed"], name="investigating")
    w(P, "confirm", rec.ids["rejected_c3_proposed"], name="rejected_c3")
    w(P, "decide", "q2", "continue", "--why", "keep going", name="continued")
    w(CX, "retract", "c4", "--why", "the rule read the wrong arm")
    w({**CC, "RB_ACTOR": "human:writer-ana"}, "note", "add", "typed in the agent's terminal", "-e", "e1")
    # a criterion declared after another experiment's run evaluated the data it reads, and one whose data was looked at
    # when nobody recorded
    w(P, "resource", "add", "probe", "--type", "dataset", "--name", "the probe set")
    w(P, "experiment", "add", "Probe A", "--id", "e3", "--baseline", "base", "--candidate", "wide")
    w(CX, "run", "add", "e3", "wide.top1=0.70", "base.top1=0.69", "--input", "probe:eval", name="run_probe")
    w(P, "experiment", "add", "Probe B", "--id", "e4", "--baseline", "base", "--candidate", "wide")
    w(P, "criterion", "add", "probe B gains", "-e", "e4", "--metric", "change.top1", "--at-least", "0", "--id", "c6")
    w(CC, "run", "add", "e4", "wide.top1=0.71", "base.top1=0.69", "--input", "probe:eval")
    w(P, "experiment", "add", "Dev probe", "--id", "e5", "--baseline", "base", "--candidate", "wide")
    w(CC, "criterion", "add", "the dev probe gains", "-e", "e5", "--metric", "change.top1", "--at-least", "0", "--id", "c7")
    w(P, "run", "add", "e5", "wide.top1=0.72", "base.top1=0.69", "--input", "devset:eval")
    # what a results folder says, kept by an agent's rb reconcile --apply
    (root / "results" / "sweep").mkdir(parents=True)
    (root / "results" / "sweep" / "README.md").write_text("# Sweep\n\n## Results\n\nwide gains a little.\n\n## Next steps\n\nmore seeds.\n",
                                                           encoding="utf-8")
    rec.said.append(("reconcile --apply", rec.rb(CX, "reconcile", "--apply")))
    # a note reworded by hand, adopted by the human
    [note] = [p for p in (root / ".rb" / "notes").glob("*.yaml") if "by the user" in p.read_text(encoding="utf-8")]
    note.write_text(note.read_text(encoding="utf-8").replace("by the user", "by the user, reworded"), encoding="utf-8")
    rec.said.append(("doctor --adopt", rec.rb(P, "doctor", "--adopt", "--why", "the user reworded it")))
    rec.git("add", "-A")
    rec.git("commit", "-qm", "the record")
    # work an agent left on a branch not merged here
    rec.git("checkout", "-q", "-b", AGENT_BRANCHES[1])
    w(CX, "note", "add", "a note on a branch not merged", "-e", "e1")
    rec.git("add", "-A")
    rec.git("commit", "-qm", "an unmerged note")
    rec.git("checkout", "-q", "main")
    # runs nobody recorded, which only e3 reads: one printed here, one in a worktree named as an agent names its own,
    # thrown away before anyone recorded what it printed
    w(P, "metric", "add", "latency", "--minimize", "--unit", "ms")
    w(CC, "criterion", "add", "wide is not slower", "-e", "e3", "--metric", "change.latency", "--at-most", "0", "--id", "c8")
    printed = 'print(\'{"metrics": {"wide.latency": %s, "base.latency": 10.0}}\')'
    rec.said.append(("journal run", rec.rb(CX, "journal", "run", "--", "python", "-c", printed % "9.5")))
    gone = root.parent / FOUND_WORKTREE
    rec.git("worktree", "add", "-q", "--detach", str(gone), "main")
    rec.said.append(("journal run (worktree)", rec.rb(CX, "journal", "run", "--", "python", "-c", printed % "9.0", cwd=gone)))
    rec.git("worktree", "remove", "--force", str(gone))
    return rec


# ---------------------------------------------------------------- a clone with a place of every kind

# the session that records a run in the nested worktree: made up, as every id here is
PLACES_AGENT = {"CLAUDECODE": "1", "CLAUDE_CODE_SESSION_ID": "sess-places-5e21"}


class Places:
    """A clone with a place of every kind (`places_record` makes it). `root` is the main checkout, which holds the
    record; `tree` names each worktree's top by its branch. `rb` runs rb in a place as the user (human:ana), or with
    `who` as someone else, and gives back what its --json answered; `git` runs git there."""

    def __init__(self, base: Path) -> None:
        self.base = base
        self.root = base / "p"
        cfg = base / "gitconfig"
        cfg.write_text("[user]\n\temail = ana@example.org\n\tname = ana\n[init]\n\tdefaultBranch = main\n", encoding="utf-8")
        self.env = {k: v for k, v in environment().items() if k not in AGENT_VARS}
        self.env.update(GIT_CONFIG_GLOBAL=str(cfg), GIT_CONFIG_NOSYSTEM="1", RB_TELEMETRY="0", DO_NOT_TRACK="1", RB_ACTOR="human:ana")
        self.tree: dict = {"main": self.root}

    def git(self, *args: str, cwd: Optional[Path] = None) -> str:
        return subprocess.run(["git", *args], cwd=cwd or self.root, env=self.env, check=True, capture_output=True, text=True).stdout.strip()

    def rb(self, *argv: str, cwd: Optional[Path] = None, who: Optional[dict] = None) -> Any:
        env = {**self.env, **(who or {})}
        if who and "CLAUDECODE" in who:
            env.pop("RB_ACTOR", None)
        r = subprocess.run([*command(), *argv, "--json"], cwd=cwd or self.root, env=env, capture_output=True, encoding="utf-8", errors="replace",
                           stdin=subprocess.DEVNULL)
        out = json.loads(r.stdout)
        assert out["ok"] and r.returncode in (0, 1), (argv, r.returncode, out.get("errors"), r.stderr)
        return out["data"]

    def worktree(self, branch: str, path: Path, start: Optional[str] = None) -> Path:
        self.git("worktree", "add", "-q", "-b", branch, str(path), *([start] if start else []))
        self.tree[branch] = path.resolve()
        return path


def places_record(base: Path) -> Places:
    """A clone at base/p whose main checkout holds the record (INT8 ResNet-50 on ImageNet: e1, int8 against fp32, with
    c1 on change.top1 and a run), and a worktree of every other kind: .worktrees/b (exp-b), nested and forked before
    the record was committed, so it writes to this record, with a run an agent session recorded there on a changed
    train.py; ../wt-c (exp-c), forked after, with the record's own copy and a run of its own not committed; ../wt-s
    (exp-s), a separate project (rb init --new-project); ../wt-n (exp-n), with no record; and ../wt-g (exp-g), removed
    without git worktree prune. `start` is the commit before the record."""
    base.mkdir(parents=True, exist_ok=True)
    rec = Places(base)
    rec.root.mkdir()
    rec.git("init", "-q")
    (rec.root / "train.py").write_text("lr = 0.1\n", encoding="utf-8")
    rec.git("add", "train.py")
    rec.git("commit", "-qm", "start")
    rec.start = rec.git("rev-parse", "HEAD")
    rec.rb("init", "INT8 ResNet-50 on ImageNet", "--no-agents")
    rec.rb("experiment", "add", "int8 against fp32", "--id", "e1", "--baseline", "fp32", "--candidate", "int8")
    rec.rb("metric", "add", "top1", "--maximize")
    rec.rb("criterion", "add", "int8 holds top1", "-e", "e1", "--metric", "change.top1", "--at-least", "-0.01", "--id", "c1")
    rec.run_main = rec.rb("run", "add", "e1", "fp32.top1=0.761", "int8.top1=0.758")["object"]["id"]
    b = rec.worktree("exp-b", rec.root / ".worktrees" / "b", rec.start)
    (b / "train.py").write_text("lr = 0.25\n", encoding="utf-8")
    rec.run_nested = rec.rb("run", "add", "e1", "fp32.top1=0.760", "int8.top1=0.757", cwd=b, who=PLACES_AGENT)["object"]["id"]
    rec.git("add", ".rb")
    rec.git("commit", "-qm", "the record, as of the first two runs")
    c = rec.worktree("exp-c", base / "wt-c")
    rec.run_sibling = rec.rb("run", "add", "e1", "fp32.top1=0.762", "int8.top1=0.759", cwd=c)["object"]["id"]
    s = rec.worktree("exp-s", base / "wt-s", rec.start)
    rec.rb("init", "another study", "--no-agents", "--new-project", cwd=s)
    rec.worktree("exp-n", base / "wt-n", rec.start)
    g = rec.worktree("exp-g", base / "wt-g", rec.start)
    import shutil
    shutil.rmtree(g)
    return rec


# ---------------------------------------------------------------- the INT8 demo record

# the session that writes the demo's session-written parts, one command at a time: made up, as every id here is
DEMO_SESSION = {"CLAUDECODE": "1", "CLAUDE_CODE_SESSION_ID": "sess-int8-demo-4c1d"}
# a character a screen would hide (Unicode's Cf), which ends the reason of the call a session proposed that waits
HIDDEN = "\u200b"


class Recorder:
    """A git repository at base/<name> where rb runs as the user (human:ana, from the repository's own git config, so
    any rb run there later is ana too), or with `who` as a session, with HOME and git's global config kept beside it.
    `rb` gives back what its --json answered; `ids` keeps what the writes made, by the name each was given."""

    def __init__(self, base: Path, name: str) -> None:
        self.base = Path(base).resolve()
        self.root = self.base / name
        home = self.base / "home"
        home.mkdir(parents=True, exist_ok=True)
        cfg = self.base / "gitconfig"
        cfg.write_text("[user]\n\temail = ana@example.org\n\tname = ana\n[init]\n\tdefaultBranch = main\n[commit]\n\tgpgsign = false\n",
                       encoding="utf-8")
        self.env = {k: v for k, v in environment().items() if k not in AGENT_VARS}
        self.env.update(GIT_CONFIG_GLOBAL=str(cfg), GIT_CONFIG_NOSYSTEM="1", RB_TELEMETRY="0", DO_NOT_TRACK="1", HOME=str(home),
                        USERPROFILE=str(home), XDG_CONFIG_HOME=str(home / ".config"))
        self.ids: dict = {}
        self.worktree: Optional[Path] = None

    def start(self) -> None:
        self.root.mkdir(parents=True)
        self.git("init", "-q")
        self.git("config", "user.email", "ana@example.org")
        self.git("config", "user.name", "ana")

    def git(self, *args: str, cwd: Optional[Path] = None, when: str = "") -> str:
        env = {**self.env, **({"GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when} if when else {})}
        return subprocess.run(["git", *args], cwd=cwd or self.root, env=env, check=True, capture_output=True, text=True).stdout.strip()

    def write(self, path: str, text: str) -> Path:
        p = self.root / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8", newline="\n")
        return p

    def rb(self, *argv: str, who: Optional[dict] = None, cwd: Optional[Path] = None, name: Optional[str] = None) -> Any:
        """rb <argv> --json (before a `--`, so a watched command gets its own arguments), which must do its job."""
        cut = argv.index("--") if "--" in argv else len(argv)
        r = subprocess.run([*command(), *argv[:cut], "--json", *argv[cut:]], cwd=cwd or self.root, env={**self.env, **(who or {})},
                           capture_output=True, encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL)
        try:
            out = json.loads(r.stdout)
        except ValueError:
            raise AssertionError((argv, r.returncode, r.stdout, r.stderr)) from None
        assert out["ok"] and r.returncode in (0, 1), (argv, r.returncode, out.get("errors"), r.stderr)
        data = out["data"]
        if name:
            self.ids[name] = data["object"]["id"]
        return data


def int8_demo(base: Path) -> Recorder:
    """The INT8 demo record (INT8 ResNet-50 on ImageNet, human:ana, a session's parts each written with DEMO_SESSION set
    for that one command), at base/int8, with a second worktree at base/int8-onnx. Every part is made through rb's own
    commands. It holds:

    - q1, with the sub-questions q1a and q1b;
    - e1, int8 against fp32, three seeds per arm (seed 1 from its file, made int8-ckpt, whose recipe is recorded; 2 and
      3 typed), with the per-run setting one_cycle=1 and the metric onnx_top1 (the ONNX export's, named for no arm), so
      rb's own text says " one_cycle=1" and " onnx_top1=", which the hub's inert gate catches; e2, calibration set size,
      a fixed design factor in four cells, one (calib_size=256) outside c6's rule; e3, per-channel against per-tensor
      scales, two seeds, filed under q1b, which c7's interpretation answers;
    - c1 met; c2 a cited number not reproduced; c4 on a single seed; c3 read from the analysis a1; g1 a gate, missed;
      c7 met; c5 and g1 linked to no question, each with a call waiting on the user;
    - a call the user made (c1), one a session proposed that waits (c6, its reason ending in HIDDEN), one a session
      proposed that the user confirmed (c2, its reason the session's), and a past call a session quoted from
      notes/2026-05-07.md:3, committed in May (e2);
    - e2's design a session wrote, with its gate, never confirmed; e1's, confirmed by the user, then changed by a session;
      e3's, a session's, confirmed by the user and unchanged since;
    - the user's own interpretation of c1; a session's of c7, which the user confirmed with e3's design; and a session's
      of c6, not confirmed;
    - results/fp16-probe/, a folder no experiment reads; results/e1-seed1/onnx.json, found and placed on e1;
      configs/export.yaml, changed and cited by nothing; e2's 2048 cell, two rows of results.db made in May and recorded
      after the fact; e2's 4096 cell, a command run under rb journal run;
    - e2's 1024 cell, the same bytes as e1's seed 1, which made int8-ckpt; e2's 256 cell, retracted and replaced;
    - the worktree int8-onnx (branch onnx-seeds), where a session's call on c4 waits;
    - a note with " online=" and "javascript:" in it.

    `ids` names the runs and calls (e1_seed1, e2_1024, e2_2048_fp32, e2_256_bad, e2_256, e2_4096, e3_seed1, call_made,
    call_proposed, call_proposed_confirmed, call_past, call_elsewhere, note_odd)."""
    import shutil
    import sqlite3
    d = Recorder(base, "int8")
    S, rb = DEMO_SESSION, d.rb
    d.worktree = d.base / "int8-onnx"
    d.start()
    may = "2026-05-0{}T10:00:00+00:00"
    d.write("README.md", "# INT8 ResNet-50 on ImageNet\n\nPost-training quantization of ResNet-50, evaluated on ImageNet validation.\n")
    d.write("train.py", "# trains ResNet-50, or quantizes it with --precision int8; settings in configs/train.yaml\n")
    d.write("eval.py", "import argparse, json\n\np = argparse.ArgumentParser()\np.add_argument('--calib_size', type=int)\np.add_argument('--seed', type=int)\n"
            "p.parse_args()\nprint(json.dumps({'metrics': {'fp32.top1': 75.69, 'int8.top1': 75.61}}))\n")
    d.write("configs/train.yaml", "optim:\n  lr: 0.1\n  epochs: 90\n")
    d.write("configs/export.yaml", "opset: 17\n")
    d.write("docs/baseline.md", "# Baseline\n\nThe FP32 ResNet-50 baseline reaches 76.1 top-1 on ImageNet validation (Table 2).\n")
    d.git("add", "-A")
    d.git("commit", "-qm", "start", when=may.format(4))
    d.write("notes/2026-05-07.md", "# Notes, 7 May 2026\n\nCalibration set size: keep sweeping, and drop sets under 256 images.\n")
    con = sqlite3.connect(str(d.root / "results.db"))
    con.execute("create table calib (run_name text, precision text, calib_size integer, seed integer, top1 real, finished_at text)")
    con.executemany("insert into calib values (?, ?, ?, ?, ?, ?)", [("c2048-fp32", "fp32", 2048, 1, 75.69, "2026-05-06 09:00:00"),
                                                                     ("c2048-int8", "int8", 2048, 1, 75.57, "2026-05-06 10:30:00")])
    con.commit()
    con.close()
    d.git("add", "-A")
    d.git("commit", "-qm", "notes and the calibration database", when=may.format(7))

    # the user begins the record
    rb("init", "INT8 ResNet-50 on ImageNet", "--no-agents")
    rb("question", "add", "Does INT8 cost ResNet-50 top-1 on ImageNet?", "--id", "q1")
    rb("question", "add", "Does the calibration set size matter?", "--id", "q1a", "--parent", "q1")
    rb("question", "add", "Do per-channel scales keep more INT8 top-1 than per-tensor ones?", "--id", "q1b", "--parent", "q1")
    rb("metric", "add", "top1", "--maximize", "--unit", "%")
    rb("metric", "add", "latency_ms", "--minimize", "--unit", "ms")
    rb("metric", "add", "onnx_top1", "--maximize", "--unit", "%")
    rb("resource", "add", "imagenet-val", "--type", "dataset", "--name", "ImageNet-1k validation", "--url", "https://www.image-net.org/")

    # a session sets up e1; the user adds the rules they care about and confirms its design; a session then adds a rule
    rb("experiment", "add", "int8 against fp32", "--id", "e1", "-q", "q1", "--baseline", "fp32", "--candidate", "int8",
       "--hypothesis", "INT8 post-training quantization keeps top-1 within a point of FP32", "-m", "per-channel weights, 1024 calibration images", who=S)
    rb("spec", "set", "e1", "seed", "--per-run", who=S)
    rb("spec", "set", "e1", "one_cycle", "--per-run", who=S)
    rb("spec", "set", "e1", "optim.lr", "--source", "configs/train.yaml#optim.lr", who=S)
    rb("criterion", "add", "FP32 reproduces the cited 76.1 top-1", "-e", "e1", "-q", "q1", "--metric", "fp32.top1", "--equals", "76.1",
       "--tolerance", "0.05", "--source", "docs/baseline.md:3", "--quote", "reaches 76.1 top-1", "--id", "c2", who=S)
    rb("criterion", "add", "INT8 runs at least 20% faster than FP32", "-e", "e1", "--metric", "change.latency_ms", "--at-most", "-1.6",
       "--role", "gate", "--id", "g1", who=S)
    rb("criterion", "add", "INT8 is no worse than FP32 by half a point, pooled over images", "-e", "e1", "-q", "q1", "--metric", "change.top1",
       "--estimand", "pooled", "--noninferior", "0.5", "--id", "c3", who=S)
    rb("criterion", "add", "INT8 keeps top-1 within a point of FP32", "-e", "e1", "-q", "q1", "--metric", "change.top1", "--at-least", "-1",
       "--when-met", "INT8 is accurate enough to ship", "--id", "c1")
    rb("criterion", "add", "INT8 answers in 7 ms or less", "-e", "e1", "--metric", "int8.latency_ms", "--at-most", "7", "--id", "c5")
    [design] = rb("confirm")["designs_pending"]
    rb(*design["confirm"].split()[1:])
    rb("criterion", "add", "The ONNX export keeps 75.5 top-1", "-e", "e1", "-q", "q1", "--metric", "onnx_top1", "--at-least", "75.5", "--id", "c4",
       who=S)

    # a session sets up e2, the calibration sweep, with its gate, and records the checkpoint seed 1 makes
    rb("experiment", "add", "calibration set size", "--id", "e2", "-q", "q1a", "--baseline", "fp32", "--candidate", "int8",
       "--hypothesis", "INT8 top-1 holds from 1024 calibration images up", who=S)
    rb("spec", "set", "e2", "calib_size", "--design-factor", "--why", "a fixed grid of calibration set sizes", who=S)
    rb("spec", "set", "e2", "seed", "--per-run", who=S)
    rb("criterion", "add", "INT8 keeps top-1 within a point at every calibration size", "-e", "e2", "-q", "q1a", "--metric", "change.top1",
       "--at-least", "-1", "--each", "--when-met", "a 1024-image calibration set is enough", "--id", "c6", who=S)
    rb("experiment", "gate", "e2", "--if-holds", "calibrate on 1024 images", "--if-not", "keep calibrating on 4096 images", who=S)
    rb("resource", "add", "int8-ckpt", "--type", "checkpoint", "--name", "ResNet-50 INT8 weights", "--recipe", "python train.py --precision int8 --seed 1",
       who=S)

    # e1's runs: seed 1 from its file, which made the checkpoint; seeds 2 and 3 typed in; then the analysis and the call
    d.write("results/e1-seed1/metrics.json", json.dumps({"fp32": {"top1": 75.69, "latency_ms": 8.0}, "int8": {"top1": 75.53, "latency_ms": 7.1},
                                                         "onnx_top1": 75.38}, indent=1) + "\n")
    rb("run", "add", "e1", "--from", "results/e1-seed1/metrics.json", "--set", "seed=1", "--set", "one_cycle=1", "--input", "imagenet-val:eval",
       "--output", "int8-ckpt:checkpoint", "--command", "python train.py --precision int8 --seed 1", who=S, name="e1_seed1")
    rb("run", "add", "e1", "fp32.top1=75.71", "int8.top1=75.58", "fp32.latency_ms=8.1", "int8.latency_ms=7.0", "--set", "seed=2", "--set", "one_cycle=1",
       "--input", "imagenet-val:eval", who=S, name="e1_seed2")
    rb("run", "add", "e1", "fp32.top1=75.66", "int8.top1=75.48", "fp32.latency_ms=8.1", "int8.latency_ms=7.2", "--set", "seed=3", "--set", "one_cycle=1",
       "--input", "imagenet-val:eval", who=S, name="e1_seed3")
    rb("analysis", "add", "e1", "--compare", "int8/fp32:top1@pooled=-0.17[-0.29,-0.05]", "--method", "paired bootstrap over images", "--unit", "image",
       "--units", "50000", "--draws", "10000", "--reads", ",".join(d.ids[k] for k in ("e1_seed1", "e1_seed2", "e1_seed3")), "--id", "a1", who=S)
    rb("decide", "c1", "accept", "--why", "within a point on all three seeds", name="call_made")
    rb("note", "add", "the eval host ran with online=false, so nothing was fetched; its log drops javascript: links", "-e", "e1", name="note_odd")

    # a session sets up e3 with what meeting c7 would mean for q1b; the user confirms its design as listed, which confirms
    # that interpretation, and nobody changes it after; its two seeds are recorded before e2's cells
    rb("experiment", "add", "per-channel against per-tensor scales", "--id", "e3", "-q", "q1b", "--baseline", "per_tensor", "--candidate", "per_channel",
       "--hypothesis", "Per-channel weight scales keep more top-1 than per-tensor ones", who=S)
    rb("spec", "set", "e3", "seed", "--per-run", who=S)
    rb("criterion", "add", "Per-channel scales keep top-1 at least as high as per-tensor ones", "-e", "e3", "-q", "q1b", "--metric", "change.top1",
       "--at-least", "0", "--when-met", "per-channel scales stay the default", "--id", "c7", who=S)
    [design] = [x for x in rb("confirm")["designs_pending"] if x["experiment"] == "e3"]
    rb(*design["confirm"].split()[1:])
    rb("run", "add", "e3", "per_tensor.top1=75.21", "per_channel.top1=75.53", "--set", "seed=1", who=S, name="e3_seed1")
    rb("run", "add", "e3", "per_tensor.top1=75.18", "per_channel.top1=75.58", "--set", "seed=2", who=S, name="e3_seed2")

    # e2's cells: 256 recorded wrong and replaced; 1024 is e1's seed-1 file; 2048 two rows made in May; 4096 a watched command
    rb("run", "add", "e2", "fp32.top1=75.69", "int8.top1=72.90", "--set", "calib_size=256", "--set", "seed=1", who=S, name="e2_256_bad")
    rb("run", "add", "e2", "fp32.top1=75.69", "int8.top1=74.31", "--set", "calib_size=256", "--set", "seed=1", "--replaces", d.ids["e2_256_bad"],
       "--why", "the first read calibrated on training images", who=S, name="e2_256")
    rb("run", "add", "e2", "--from", "results/e1-seed1/metrics.json", "--set", "calib_size=1024", "--set", "seed=1", who=S, name="e2_1024")
    for arm in ("fp32", "int8"):
        rb("run", "add", "e2", "--variant", arm, "--from", "results.db", "--table", "calib", "--where", f"run_name=c2048-{arm}",
           "--set", "calib_size=@calib_size", "--set", "seed=@seed", "--produced-at", "@finished_at", name=f"e2_2048_{arm}")
    rb("journal", "run", "--", "python" if shutil.which("python") else sys.executable, "eval.py", "--calib_size", "4096", "--seed", "1", who=S)
    [ran] = [e for e in rb("journal")["entries"] if e["op"] == "run"]
    rb("run", "add", "e2", *(f"{k}={v}" for k, v in sorted(ran["metrics"].items())), "--set", "calib_size=4096", "--set", "seed=1",
       "--command", ran["command"], "--produced-at", ran["at"], "--commit", ran["code"]["commit"], "--tree", ran["code"]["tree"], who=S, name="e2_4096")

    # the calls: one a session proposed that the user confirmed; a past call a session quotes from the notes of 7 May; one
    # a session proposed that waits, its reason ending in a character a screen would hide
    rb("decide", "c2", "investigate", "--why", "FP32 reads 0.4 below the cited 76.1; compare the resize and crop with the paper's", who=S,
       name="call_proposed_confirmed")
    rb("confirm", d.ids["call_proposed_confirmed"])
    rb("decide", "e2", "continue", "--past", "--when", "2026-05-07", "--source", "notes/2026-05-07.md:3", "--quote",
       "Calibration set size: keep sweeping, and drop sets under 256 images", "--why", "the notes of 7 May narrow the sweep", who=S, name="call_past")
    rb("decide", "c6", "investigate", "--why", f"the 256-image cell reads 1.4 below FP32; rerun it with the fixed loader{HIDDEN}", who=S,
       name="call_proposed")
    d.git("add", "-A")
    d.git("commit", "-qm", "the record and its runs")

    # what rb finds that nobody recorded: a folder no experiment reads, a result that lands in a folder e1 reads, with its
    # config, and a config changed that nothing in the record cites
    d.write("results/fp16-probe/metrics.json", json.dumps({"fp16": {"top1": 75.66}}) + "\n")
    d.write("results/e1-seed1/config.yaml", "seed: 1\none_cycle: 1\n")
    d.write("results/e1-seed1/onnx.json", json.dumps({"onnx_top1": 75.41}) + "\n")
    d.write("configs/export.yaml", "opset: 18\n")
    d.git("commit", "-qm", "export at opset 18", "--", "configs/export.yaml")
    rb("status")

    # a second worktree of the record, where a session's call waits
    d.git("worktree", "add", "-q", "-b", "onnx-seeds", str(d.worktree))
    rb("decide", "c4", "investigate", "--why", "only seed 1 was exported; export seeds 2 and 3", who=S, cwd=d.worktree, name="call_elsewhere")
    return d


# ---------------------------------------------------------------- records at scale, for budgets and list caps

# the two sessions that write parts of the scale record, as the INT8 demo's does: made up
SCALE_SESSIONS = ({"CLAUDECODE": "1", "CLAUDE_CODE_SESSION_ID": "sess-scale-7a01"}, {"CODEX_SANDBOX": "seatbelt", "CODEX_THREAD_ID": "thread-scale-3b02"})
# what the scale record holds: the size of a long study, which the shell's page and rb open's reads are held to (the
# runs and criteria counted as rb status counts them, the writes as lines of the log)
SCALE = {"experiments": 5, "runs": 141, "criteria": 60, "analyses": 3, "resources": 10, "writes": 700}
# what a note in the scale record says, filled in from a seeded draw
SCALE_NOTES = ("seed {seed}: the eval took {m} minutes on the shared box", "the loss curve is flat after epoch {e}",
               "calibration draw {k} reused from the earlier sweep", "batch norm folded before export; top-1 moved by {d}",
               "seed {seed} restarted once after a preemption", "the eval box ran driver 550.{k}", "per-layer scales checked by hand for layer{k}",
               "the {e}th epoch checkpoint kept for the export", "ImageNet-V2 read from the local copy, {m} minutes", "seed {seed}: no NaN in the activations")


def scale_record(base: Path) -> Recorder:
    """A record the size of a long quantization study, at base/scale, made up from end to end with rb's own commands:
    SCALE's counts (5 experiments, 141 runs with x3's 64 over the 50 receipts a page gives in full, 60 criteria, 3
    analyses, 10 resources and about 700 writes), six questions in a tree, the user (human:ana) and two sessions
    writing, two designs confirmed (one then changed by a session) and two waiting, calls made, proposed, confirmed and
    past, runs replaced, a criterion and a note retracted, notes. Its numbers come from a seeded draw, so every build
    holds the same values; ids and times are rb's. `criteria` and `runs` name what it made."""
    import random
    rnd = random.Random(20261019)
    d = Recorder(base, "scale")
    CC, CX = SCALE_SESSIONS
    rb = d.rb
    d.start()
    d.write("README.md", "# Quantizing ResNet-50\n\nPost-training and quantization-aware INT8, INT6 and INT4 ResNet-50 on ImageNet.\n")
    d.write("docs/baseline.md", "# Baseline\n\nThe FP32 ResNet-50 baseline reaches 76.1 top-1 on ImageNet validation (Table 2).\n"
            "Its top-5 is 92.9 on the same split (Table 2).\n")
    configs = {
        "x1": {"optim.lr": 0.1, "optim.epochs": 90, "data.resize": 256, "data.crop": 224, "quant.scheme": "symmetric", "quant.observer": "minmax",
               "export.opset": 17, "eval.batch": 256},
        "x2": {"optim.lr": 0.1, "data.resize": 256, "data.crop": 224, "quant.scheme": "symmetric", "quant.observer": "histogram", "export.opset": 17,
               "eval.batch": 256, "calib.draw": "fixed"},
        "x3": {"optim.lr": 0.1, "data.resize": 256, "data.crop": 224, "quant.scheme": "asymmetric", "quant.observer": "percentile",
               "quant.percentile": 99.99, "export.opset": 17, "eval.batch": 128},
        "x4": {"optim.lr": 0.1, "data.resize": 256, "data.crop": 224, "quant.observer": "minmax", "export.opset": 17, "eval.batch": 256,
               "quant.bits": 8, "quant.symmetric": True},
        "x5": {"optim.lr": 0.001, "optim.epochs": 10, "data.resize": 256, "data.crop": 224, "quant.scheme": "symmetric", "qat.freeze_bn": 8,
               "export.opset": 17, "eval.batch": 256},
    }
    for x, flat in configs.items():
        tree: dict = {}
        for key, value in flat.items():
            top, leaf = key.split(".")
            tree.setdefault(top, {})[leaf] = str(value).lower() if isinstance(value, bool) else value
        d.write(f"configs/{x}.yaml", "".join(f"{top}:\n" + "".join(f"  {k}: {v}\n" for k, v in leaves.items()) for top, leaves in tree.items()))
    d.write("notes/2026-04-20.md", "# Notes, 20 April 2026\n\nPer-channel scales: we keep per-channel for every conv layer from now on.\n"
            "Bit width: we stop the int5 arm, it never trained stably.\n")
    d.git("add", "-A")
    d.git("commit", "-qm", "configs and notes", when="2026-04-20T10:00:00+00:00")

    rb("init", "Quantizing ResNet-50", "--no-agents")
    for qid, text, parent, who in (("q1", "Does INT8 cost ResNet-50 top-1 on ImageNet?", None, None),
                                   ("q1a", "Does the calibration set size matter?", "q1", None),
                                   ("q1b", "How far down can the bit width go?", "q1", CC),
                                   ("q2", "Do per-channel scales beat per-tensor scales?", None, None),
                                   ("q3", "Does quantization-aware training close the gap?", None, None),
                                   ("q3a", "Does the gain hold on ImageNet-V2?", "q3", CX)):
        rb("question", "add", text, "--id", qid, *(["--parent", parent] if parent else []), who=who)
    for metric, way, unit in (("top1", "--maximize", "%"), ("top5", "--maximize", "%"), ("latency_ms", "--minimize", "ms"),
                              ("size_mb", "--minimize", "MB"), ("v2_top1", "--maximize", "%")):
        rb("metric", "add", metric, way, "--unit", unit)
    for rid, kind, name, extra, who in (
            ("imagenet-train", "dataset", "ImageNet-1k training", ["--url", "https://www.image-net.org/"], None),
            ("imagenet-val", "dataset", "ImageNet-1k validation", ["--url", "https://www.image-net.org/"], None),
            ("imagenet-v2", "dataset", "ImageNet-V2 matched frequency", ["--url", "https://imagenetv2.org/"], CX),
            ("calib-4k", "dataset", "calibration draw", ["--subset", "4096 training images, one fixed draw"], None),
            ("resnet50-fp32", "checkpoint", "ResNet-50 FP32 weights", ["--recipe", "python train.py --precision fp32 --epochs 90"], None),
            ("int8-ptq", "checkpoint", "ResNet-50 INT8 post-training", ["--recipe", "python quantize.py --bits 8 --calib 1024"], None),
            ("int8-qat", "checkpoint", "ResNet-50 INT8 quantization-aware", ["--recipe", "python qat.py --bits 8 --epochs 10"], CX),
            ("int4-ptq", "checkpoint", "ResNet-50 INT4 post-training", ["--recipe", "python quantize.py --bits 4 --calib 1024"], None),
            ("quant-code", "code", "the quantization code", ["--source", "quant/ in this repository"], None),
            ("eval-image", "environment", "the evaluation container", ["--source", "the eval container image, tag eval-2026-10"], None)):
        rb("resource", "add", rid, "--type", kind, "--name", name, *extra, who=who)
    for rid, provider, ref in (("resnet50-fp32", "s3", "s3://example-bucket/resnet50/fp32.pt"), ("int8-ptq", "s3", "s3://example-bucket/resnet50/int8-ptq.pt"),
                               ("int8-qat", "hf", "example-org/resnet50-int8-qat"), ("imagenet-val", "local", "/data/imagenet/val"),
                               ("imagenet-v2", "local", "/data/imagenet-v2")):
        rb("resource", "locate", rid, provider, ref)
    rb("resource", "expose", "imagenet-v2", "inspected", "--why", "looked at twenty images per class before the QAT rules", who=CX)
    rb("resource", "expose", "calib-4k", "selected", "--why", "drew the 4096 images once, by class", "--at", "2026-04-18")

    def setup(x: str, title: str, q: str, who: Optional[dict], arms: tuple, factor: Optional[str], hypothesis: str) -> None:
        baseline, *candidates = arms
        rb("experiment", "add", title, "--id", x, "-q", q, "--baseline", baseline, *(a for c in candidates for a in ("--candidate", c)),
           "--hypothesis", hypothesis, who=who)
        rb("spec", "set", x, "--from", f"configs/{x}.yaml", "--keys", ",".join(sorted({k.split(".")[0] + ".*" for k in configs[x]})), who=who)
        rb("spec", "set", x, "seed", "--per-run", who=who)
        if factor:
            rb("spec", "set", x, factor, "--design-factor", "--why", f"a fixed grid of {factor} values", who=who)

    d.criteria = []

    def rule(x: str, statement: str, *argv: str, who: Optional[dict] = None) -> str:
        cid = f"{x}-c{sum(1 for c in d.criteria if c.startswith(x + '-')) + 1}"
        d.criteria.append(cid)
        rb("criterion", "add", statement, "-e", x, "--id", cid, *argv, who=who)
        return cid

    setup("x1", "int8 against fp32", "q1", CC, ("fp32", "int8"), None, "INT8 post-training quantization keeps top-1 within a point of FP32")
    rule("x1", "INT8 keeps top-1 within a point of FP32", "--metric", "change.top1", "--at-least", "-1", "-q", "q1", "--when-met",
         "INT8 is accurate enough to ship")
    rule("x1", "INT8 keeps top-5 within half a point", "--metric", "change.top5", "--at-least", "-0.5", "-q", "q1", "--role", "secondary", who=CC)
    rule("x1", "FP32 reproduces the cited 76.1 top-1", "--metric", "fp32.top1", "--equals", "76.1", "--tolerance", "0.05", "--source", "docs/baseline.md:3",
         "--quote", "reaches 76.1 top-1", "-q", "q1", who=CC)
    rule("x1", "FP32 reproduces the cited 92.9 top-5", "--metric", "fp32.top5", "--equals", "92.9", "--tolerance", "0.05", "--source", "docs/baseline.md:4",
         "--quote", "top-5 is 92.9", "--role", "secondary", who=CC)
    rule("x1", "INT8 is no worse by half a point, pooled over images", "--metric", "change.top1", "--estimand", "pooled", "--noninferior", "0.5", "-q", "q1",
         who=CC)
    rule("x1", "INT8 answers in 7 ms or less", "--metric", "int8.latency_ms", "--at-most", "7", "--role", "secondary")
    rule("x1", "INT8 runs at least 20% faster", "--metric", "change.latency_ms", "--at-most", "-1.6", "--role", "gate", who=CC)
    rule("x1", "INT8 weights fit in 26 MB", "--metric", "int8.size_mb", "--at-most", "26", "-q", "q1", "--role", "secondary", who=CC)
    rule("x1", "INT8 is at least 70 MB smaller", "--metric", "change.size_mb", "--at-most", "-70", "--role", "exploratory", who=CC)
    rule("x1", "Every INT8 seed keeps 75 top-1", "--metric", "int8.top1", "--at-least", "75", "--each", "-q", "q1", who=CC)
    rule("x1", "INT8 top-5 is no worse by a third of a point", "--metric", "change.top5", "--noninferior", "0.3", "--role", "secondary", who=CC)
    rule("x1", "INT8 does better than FP32 on top-1", "--metric", "change.top1", "--better", "--role", "exploratory", who=CC)

    setup("x2", "calibration set size", "q1a", CX, ("fp32", "int8"), "calib_size", "INT8 top-1 holds from 1024 calibration images up")
    sizes = (128, 256, 512, 1024, 2048, 4096)
    rule("x2", "INT8 keeps top-1 within a point at every size", "--metric", "change.top1", "--at-least", "-1", "--each", "-q", "q1a", "--when-met",
         "1024 calibration images are enough", who=CX)
    rule("x2", "INT8 keeps top-5 within half a point at every size", "--metric", "change.top5", "--at-least", "-0.5", "--each", "-q", "q1a", who=CX)
    for size in sizes:
        rule("x2", f"INT8 keeps top-1 within half a point at {size} images", "--metric", "change.top1", "--at-least", "-0.5", "--where",
             f"calib_size={size}", "-q", "q1a", who=CX)
    rule("x2", "128 images keep top-1 within two points", "--metric", "change.top1", "--at-least", "-2", "--where", "calib_size=128", "--role", "gate", who=CX)
    rule("x2", "INT8 keeps 75 top-1 at every size", "--metric", "int8.top1", "--at-least", "75", "--each", "--role", "secondary", who=CX)
    rule("x2", "INT8 loses under a fifth of a point at every size", "--metric", "change.top1", "--at-least", "-0.2", "--each", "--role", "exploratory", who=CX)
    rule("x2", "4096 images are no worse by a third of a point", "--metric", "change.top1", "--noninferior", "0.3", "--where", "calib_size=4096",
         "--role", "secondary", who=CX)

    setup("x3", "bit width", "q1b", CC, ("fp32", "int8", "int6", "int4"), "layers", "Six bits keep top-1 within a point; four bits do not")
    for arm, floor in (("int8", "-1"), ("int6", "-1"), ("int4", "-3")):
        rule("x3", f"{arm} keeps top-1 within {floor.lstrip('-')} of FP32", "--metric", "top1", "--subject", arm, "--reference", "fp32", "--at-least", floor,
             "--each", "-q", "q1b", who=CC)
        for layers in ("all", "inner"):
            rule("x3", f"{arm} with {layers} layers quantized keeps top-1 within {floor.lstrip('-')}", "--metric", "top1", "--subject", arm,
                 "--reference", "fp32", "--at-least", floor, "--where", f"layers={layers}", "-q", "q1b", who=CC)
        rule("x3", f"{arm} keeps 74 top-1 in every run", "--metric", f"{arm}.top1", "--at-least", "74", "--each", "--role", "secondary", who=CC)
    rule("x3", "int4 halves the INT8 size", "--metric", "int4.size_mb", "--at-most", "13", "--role", "secondary", who=CC)
    rule("x3", "int6 answers in 7.5 ms or less", "--metric", "int6.latency_ms", "--at-most", "7.5", "--role", "exploratory", who=CC)
    rule("x3", "int4 with inner layers is no worse than int6 by two points", "--metric", "top1", "--subject", "int4", "--reference", "int6",
         "--noninferior", "2", "--where", "layers=inner", "--role", "exploratory", who=CC)

    setup("x4", "per-channel against per-tensor", "q2", None, ("per_tensor", "per_channel"), None, "Per-channel scales gain top-1 over per-tensor ones")
    rule("x4", "Per-channel scales gain top-1", "--metric", "change.top1", "--better", "-q", "q2", "--when-met", "per-channel is the default from now on")
    rule("x4", "Per-channel is no worse by a tenth", "--metric", "change.top1", "--noninferior", "0.1", "-q", "q2")
    rule("x4", "Per-channel keeps top-5", "--metric", "change.top5", "--at-least", "0", "-q", "q2", "--role", "secondary")
    rule("x4", "Per-channel keeps 75.3 top-1", "--metric", "per_channel.top1", "--at-least", "75.3", "-q", "q2")
    rule("x4", "Per-tensor keeps 75 top-1", "--metric", "per_tensor.top1", "--at-least", "75", "--role", "secondary")
    rule("x4", "Every per-channel seed keeps 75 top-1", "--metric", "per_channel.top1", "--at-least", "75", "--each", "--role", "secondary")
    rule("x4", "Per-channel costs at most a tenth of a millisecond", "--metric", "change.latency_ms", "--at-most", "0.1", "--role", "secondary")
    rule("x4", "Per-channel gains a fifth of a point", "--metric", "change.top1", "--at-least", "0.2", "-q", "q2", "--role", "exploratory")
    rule("x4", "Per-channel gains on every seed", "--metric", "change.top1", "--at-least", "0", "--each", "--role", "exploratory")
    rb("freeze", "x4", "-m", "before the per-channel runs")
    rule("x4", "Per-channel top-5 is no worse by a tenth", "--metric", "change.top5", "--noninferior", "0.1", "--role", "secondary", "--amend",
         "--why", "top-5 was left out of the lock")

    setup("x5", "quantization-aware training", "q3", CX, ("ptq", "qat"), None, "Ten epochs of QAT recover what PTQ loses")
    rule("x5", "QAT gains top-1 over PTQ", "--metric", "change.top1", "--better", "-q", "q3", "--when-met", "QAT is worth its ten epochs", who=CX)
    rule("x5", "QAT keeps 75.6 top-1", "--metric", "qat.top1", "--at-least", "75.6", "-q", "q3", who=CX)
    rule("x5", "QAT gains top-1 on ImageNet-V2", "--metric", "change.v2_top1", "--better", "-q", "q3a", "--when-met", "the QAT gain holds off the test set",
         who=CX)
    rule("x5", "QAT keeps 63.5 on ImageNet-V2", "--metric", "qat.v2_top1", "--at-least", "63.5", "-q", "q3a", who=CX)
    rule("x5", "QAT is no worse than PTQ on top-5", "--metric", "change.top5", "--noninferior", "0.1", "--role", "secondary", who=CX)
    rule("x5", "QAT gains a fifth of a point", "--metric", "change.top1", "--at-least", "0.2", "-q", "q3", "--role", "secondary", who=CX)
    rule("x5", "Every QAT seed keeps 75.4", "--metric", "qat.top1", "--at-least", "75.4", "--each", "--role", "secondary", who=CX)
    rule("x5", "QAT gains on every seed on ImageNet-V2", "--metric", "change.v2_top1", "--at-least", "0", "--each", "--role", "exploratory", who=CX)
    rule("x5", "QAT costs no latency", "--metric", "change.latency_ms", "--at-most", "0.05", "--role", "secondary", who=CX)
    rule("x5", "QAT gains a point on ImageNet-V2", "--metric", "change.v2_top1", "--at-least", "1", "--role", "gate", who=CX)
    # a rule a session declared and then took back, which stays on record and stops counting
    gone = rule("x5", "QAT gains on the training split", "--metric", "change.top1", "--at-least", "0", "--role", "exploratory", who=CX)
    rb("retract", gone, "--why", "the training split says nothing about held-out top-1", who=CX)
    d.criteria.remove(gone)

    # the user confirms x1's and x3's designs; a session then changes x3's; x2's and x5's wait, x4 is the user's own
    for x in ("x1", "x3"):
        [design] = [p for p in rb("confirm")["designs_pending"] if p["experiment"] == x]
        rb(*design["confirm"].split()[1:])
    rule("x3", "int4 stays within 4 points with all layers", "--metric", "top1", "--subject", "int4", "--reference", "fp32", "--at-least", "-4",
         "--where", "layers=all", "--role", "gate", who=CC)
    rb("freeze", "x1", "-m", "before the seeds")
    rb("experiment", "gate", "x2", "--if-holds", "calibrate on 1024 images", "--if-not", "keep 4096 images", who=CX)
    rb("experiment", "gate", "x4", "--if-holds", "make per-channel the default", "--if-not", "keep per-tensor scales")
    rb("experiment", "relate", "x5", "follows_from", "x1", "--note", "QAT starts from the x1 checkpoints", who=CX)
    for cid, q in (("x1-c6", "q1"), ("x2-c9", "q1a"), ("x4-c5", "q2"), ("x5-c5", "q3")):
        rb("criterion", "file", cid, q, "--why", "it reads the comparison this question asks about", *(["--amend"] if cid[:2] in ("x1", "x4") else []))
    rb("criterion", "relate", "x2-c6", "reverses", "x1-c1", "--orientation", "same", "--why", "the 1024-image cell is the x1 setting", who=CX)

    def g(mean: float, spread: float) -> float:
        return round(rnd.gauss(mean, spread), 2)

    d.runs = {x: [] for x in configs}

    def run(x: str, values: dict, sets: dict, who: Optional[dict], *extra: str) -> str:
        argv = [f"{k}={v}" for k, v in values.items()]
        out = rb("run", "add", x, *argv, *(a for k, v in sets.items() for a in ("--set", f"{k}={v}")), *extra, who=who)
        d.runs[x].append(out["object"]["id"])
        return out["object"]["id"]

    for seed in range(1, 13):
        fp32, gap = g(75.7, 0.05), g(-0.15, 0.06)
        values = {"fp32.top1": fp32, "int8.top1": round(fp32 + gap, 2), "fp32.top5": g(92.85, 0.04), "int8.top5": g(92.7, 0.05),
                  "fp32.latency_ms": g(8.0, 0.1), "int8.latency_ms": g(7.05, 0.1), "fp32.size_mb": 97.8, "int8.size_mb": 24.6}
        if seed <= 3:
            path = f"results/x1/seed{seed}/metrics.json"
            arms = {arm: {k.split(".")[1]: v for k, v in values.items() if k.startswith(arm + ".")} for arm in ("fp32", "int8")}
            d.write(path, json.dumps(arms, indent=1) + "\n")
            extra = ("--output", "int8-ptq:checkpoint", "--command", "python quantize.py --bits 8 --calib 1024 --seed 1") if seed == 1 else ()
            out = rb("run", "add", "x1", "--from", path, "--set", f"seed={seed}", "--input", "imagenet-val:eval", "--input", "resnet50-fp32:model",
                     *extra, who=CC)
            d.runs["x1"].append(out["object"]["id"])
        else:
            run("x1", values, {"seed": seed}, None, "--input", "imagenet-val:eval")
    for size in sizes:
        for seed in (1, 2, 3):
            fp32 = g(75.7, 0.05)
            loss = {128: -1.6, 256: -1.2, 512: -0.6, 1024: -0.2, 2048: -0.15, 4096: -0.12}[size]
            run("x2", {"fp32.top1": fp32, "int8.top1": round(fp32 + g(loss, 0.08), 2), "fp32.top5": g(92.85, 0.04), "int8.top5": g(92.6, 0.08)},
                {"calib_size": size, "seed": seed}, CX if seed == 1 else None, "--input", "calib-4k:calibration")
    for layers in ("all", "inner"):
        for seed in range(1, 33):
            fp32 = g(75.7, 0.05)
            cut = {"all": (0.2, 1.1, 4.2), "inner": (0.15, 0.8, 2.9)}[layers]
            values = {"fp32.top1": fp32, "int8.top1": round(fp32 - g(cut[0], 0.05), 2), "int6.top1": round(fp32 - g(cut[1], 0.1), 2),
                      "int4.top1": round(fp32 - g(cut[2], 0.3), 2)}
            if seed <= 4:
                values.update({"int4.size_mb": 12.4, "int6.size_mb": 18.5, "int6.latency_ms": g(7.4, 0.1)})
            run("x3", values, {"layers": layers, "seed": seed}, None, *(("--output", "int4-ptq:checkpoint") if (layers, seed) == ("all", 1) else ()))
    for seed in range(1, 25):
        tensor = g(75.4, 0.06)
        run("x4", {"per_tensor.top1": tensor, "per_channel.top1": round(tensor + g(0.28, 0.05), 2), "per_tensor.top5": g(92.6, 0.05),
                   "per_channel.top5": g(92.7, 0.05), "per_tensor.latency_ms": g(7.0, 0.05), "per_channel.latency_ms": g(7.05, 0.05)},
            {"seed": seed}, None, "--input", "imagenet-val:eval")
    for seed in range(1, 24):
        ptq = g(75.5, 0.06)
        run("x5", {"ptq.top1": ptq, "qat.top1": round(ptq + g(0.18, 0.05), 2), "ptq.v2_top1": g(63.1, 0.15), "qat.v2_top1": g(63.6, 0.15),
                   "ptq.top5": g(92.65, 0.05), "qat.top5": g(92.7, 0.05), "ptq.latency_ms": g(7.05, 0.03), "qat.latency_ms": g(7.06, 0.03)},
            {"seed": seed}, CX if seed > 20 else None, "--input", "imagenet-v2:eval", *(("--output", "int8-qat:checkpoint") if seed == 1 else ()))
    # four runs read wrong, replaced: each replacement is another write, the run it replaces stays on record
    for x, i in (("x2", 4), ("x3", 10), ("x4", 7), ("x5", 2)):
        old = d.runs[x][i]
        shown = rb("show", old)["object"]
        values = {k: round(v + 0.01, 2) for k, v in shown["metrics"].items()}
        new = rb("run", "add", x, *(f"{k}={v}" for k, v in values.items()), *(a for k, v in shown["per_run"].items() for a in ("--set", f"{k}={v}")),
                 "--replaces", old, "--why", "the first read used the wrong eval resize")["object"]["id"]
        d.runs[x][i] = new

    rb("analysis", "add", "x1", "--compare", "int8/fp32:top1@pooled=-0.16[-0.24,-0.08]", "--method", "paired bootstrap over images", "--unit", "image",
       "--units", "50000", "--draws", "10000", "--reads", ",".join(d.runs["x1"]), "--id", "a1", who=CC)
    rb("analysis", "add", "x3", "--compare", "int4/fp32:top1@pooled=-3.6[-3.9,-3.3]", "--compare", "int6/fp32:top1@pooled=-0.95[-1.1,-0.8]",
       "--method", "paired bootstrap over images", "--unit", "image", "--units", "50000", "--reads", ",".join(d.runs["x3"][:8]), "--id", "a2", who=CC)
    rb("analysis", "add", "x4", "--compare", "per_channel/per_tensor:top1@pooled=0.27[0.21,0.33]", "--method", "paired bootstrap over images",
       "--unit", "image", "--units", "50000", "--reads", ",".join(d.runs["x4"]), "--id", "a3")

    # calls: the user's own; a session's, some confirmed; two past calls a session quotes from the notes of 20 April
    for subject, action, why in (("x1-c1", "accept", "within a point on twelve seeds"), ("x1-c3", "investigate", "FP32 reads below the cited 76.1"),
                                 ("x4-c1", "accept", "per-channel gains on every seed"), ("x4", "ship", "per-channel is the default now"),
                                 ("x2-c3", "reject", "128 and 256 images are too few"), ("x3-c11", "investigate", "int4 needs a better observer"),
                                 ("x5-c1", "accept", "QAT gains on top-1"), ("x1-c7", "accept", "the latency gate is a target, not a bar"),
                                 ("q2", "accept", "per-channel answers it"), ("x1", "continue", "export next"),
                                 ("x2-c4", "accept", "256 images hold within half a point on two seeds"), ("x5-c3", "investigate", "V2 is noisy")):
        rb("decide", subject, action, "--why", why)
    proposed = []
    for subject, action, why, who in (("x2-c1", "accept", "every size above 256 holds", CX), ("x3-c1", "accept", "int8 holds at every layer set", CC),
                                      ("x3-c7", "investigate", "int6 misses on the inner layers", CC), ("x5-c4", "accept", "QAT holds on V2", CX),
                                      ("x5", "continue", "try twenty epochs", CX), ("x2", "park", "wait for the new draw", CX),
                                      ("x3", "continue", "add an int5 arm back", CC), ("x1-c10", "reject", "the 75 floor is too strict", CC),
                                      ("x3-c13", "accept", "int4 is half the size", CC), ("x5-c9", "investigate", "latency moved by 0.01", CX)):
        proposed.append(rb("decide", subject, action, "--why", why, who=who)["object"]["id"])
    for pid in proposed[:5]:
        rb("confirm", pid)
    rb("decide", "x4", "continue", "--past", "--when", "2026-04-20", "--source", "notes/2026-04-20.md:3", "--quote",
       "Per-channel scales: we keep per-channel for every conv layer from now on", "--why", "the notes of 20 April", who=CC)
    rb("decide", "x3", "continue", "--past", "--when", "2026-04-20", "--source", "notes/2026-04-20.md:4", "--quote",
       "Bit width: we stop the int5 arm, it never trained stably", "--why", "the notes of 20 April", who=CC)

    # notes, until the log holds about SCALE's writes: most the user's, a few a session's, one taken back
    lines = lambda: sum(1 for line in (d.root / ".rb" / "log.jsonl").read_text(encoding="utf-8").splitlines() if line.strip())  # noqa: E731
    first = rb("note", "add", "the shared box was rebooted mid-sweep; nothing was lost", "-e", "x2")["object"]["id"]
    rb("retract", first, "--why", "it was the other box")
    i = 0
    while lines() < SCALE["writes"]:
        x = ("x1", "x2", "x3", "x4", "x5")[i % 5]
        text = SCALE_NOTES[i % len(SCALE_NOTES)].format(seed=i % 32 + 1, m=rnd.randint(4, 40), e=rnd.randint(3, 89), k=rnd.randint(1, 9),
                                                         d=round(rnd.uniform(0.01, 0.09), 2))
        rb("note", "add", text, "-e", x, who=(CC, CX)[i // 25 % 2] if i % 25 == 0 else None)
        i += 1
    d.git("add", "-A")
    d.git("commit", "-qm", "the record")
    return d


def found_folders_record(base: Path, folders: int = 600, found: int = 60) -> Recorder:
    """A record at base/sweep for the caps on what a screen lists: its repository holds `folders` results folders a
    sweep left before the record began (results/sweep-0001/ and on, each with its metrics), which no recorded run names,
    and `found` results that landed after rb first looked in results/e1/, the folder e1's one recorded run was read
    from, each a run rb proposes for e1."""
    import random
    rnd = random.Random(600)
    d = Recorder(base, "sweep")
    d.start()
    d.write("README.md", "# A calibration sweep\n\nINT8 ResNet-50 on ImageNet, one folder per configuration.\n")
    for i in range(1, folders + 1):
        fp32 = round(rnd.gauss(75.7, 0.05), 2)
        d.write(f"results/sweep-{i:04d}/metrics.json", json.dumps({"fp32": {"top1": fp32}, "int8": {"top1": round(fp32 - rnd.uniform(0.05, 1.8), 2)}}) + "\n")
    d.git("add", "-A")
    d.git("commit", "-qm", "the sweep", when="2026-05-04T10:00:00+00:00")
    d.rb("init", "INT8 ResNet-50 on ImageNet", "--no-agents")
    d.rb("question", "add", "Does INT8 cost ResNet-50 top-1 on ImageNet?", "--id", "q1")
    d.rb("metric", "add", "top1", "--maximize", "--unit", "%")
    d.rb("experiment", "add", "int8 against fp32", "--id", "e1", "-q", "q1", "--baseline", "fp32", "--candidate", "int8")
    d.rb("criterion", "add", "INT8 keeps top-1 within a point of FP32", "-e", "e1", "-q", "q1", "--metric", "change.top1", "--at-least", "-1", "--id", "c1")
    d.write("results/e1/metrics.json", json.dumps({"fp32": {"top1": 75.69}, "int8": {"top1": 75.53}}) + "\n")
    d.rb("run", "add", "e1", "--from", "results/e1/metrics.json")
    d.rb("status")
    for i in range(1, found + 1):
        fp32 = round(rnd.gauss(75.7, 0.05), 2)
        d.write(f"results/e1/rerun-{i:02d}.json", json.dumps({"fp32": {"top1": fp32}, "int8": {"top1": round(fp32 - rnd.uniform(0.05, 0.4), 2)}}) + "\n")
    d.rb("status")
    return d


RECORDS = {"int8-demo": int8_demo, "scale": scale_record, "found-folders": found_folders_record}


def make(argv: Optional[list] = None) -> int:
    """`python tests/rbtest.py <record> <folder>` makes one of RECORDS in a new or empty folder, with the rb RB_BIN names
    (else the rb installed beside this Python), and prints the checkout that holds it."""
    import argparse
    p = argparse.ArgumentParser(prog="python tests/rbtest.py", description="Make a record the tests use, in a folder of its own.")
    p.add_argument("record", choices=sorted(RECORDS))
    p.add_argument("folder", type=Path)
    a = p.parse_args(argv)
    if a.folder.exists() and any(a.folder.iterdir()):
        p.error(f"{a.folder} is not empty")
    print(RECORDS[a.record](a.folder).root)
    return 0


if __name__ == "__main__":
    sys.exit(make())
