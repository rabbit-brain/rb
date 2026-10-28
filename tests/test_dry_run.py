"""--dry-run: a write rb previews. Every check the write makes runs, and nothing is written.

Promises pinned here:

- The 25 commands AGENTS.md lists take --dry-run (dry_run in MCP, dry_run=True in the SDK), and the manual says how the
  bulk writes preview in their plain form (rb reconcile, rb doctor, rb fmt --check). A preview runs every check
  the write runs (sources read and checked, spans verified, refusals) and exits as the write would, with the same
  refusal or the same warnings, and writes nothing: no record file, no log line, no cache, no lock file, no session
  line, no git object, ref or index, and no file or directory with another time than before.
- What it says it would write is what the write then writes: each record file with its content, each file it would
  remove, each line it would add to the log (data.would_write, with data.dry_run true); rb's text says the same without
  who made the write.
- A failed check leaves no mark when it is previewed, and the write made after it is the write the preview showed.
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import warnings
from pathlib import Path

import pytest

import rabbit_brain
from rabbit_brain import RBError, RBWarning
from rbtest import AGENT_VARS, Mcp, command, environment

HAS_GIT = shutil.which("git") is not None
BEFORE = Path(__file__).resolve().parent / "fixtures" / "shared-files-record"   # a record from before parts had files
pytestmark = pytest.mark.skipif(not HAS_GIT, reason="the receipts and the snapshots of the code are git's")

NOW = "2026-10-11T08:00:00.000+00:00"
AGENT = {"CLAUDECODE": "1", "AI_AGENT": "claude-code_2-1-295_agent", "CLAUDE_CODE_SESSION_ID": "sess-dry-4f1c"}


# ---------------------------------------------------------------- a project, and what is on disk

class Project:
    """A git repository with a record, where rb runs as a human (human:ana) in no chat session, the clock and the ids
    fixed, so a write previewed and then made draw the same times and ids."""

    def __init__(self, root: Path, home: Path) -> None:
        self.root = root
        gitconfig = home / "gitconfig"
        gitconfig.write_text("", encoding="utf-8")
        self.env = {k: v for k, v in environment().items() if k not in AGENT_VARS}
        self.env.update(GIT_CONFIG_GLOBAL=str(gitconfig), GIT_CONFIG_NOSYSTEM="1", RB_TELEMETRY="0", DO_NOT_TRACK="1",
                        RB_TEST_NOW=NOW, RB_TEST_SEED="dry-run", HOME=str(home), USERPROFILE=str(home))

    def git(self, *args: str) -> str:
        return subprocess.run(["git", *args], cwd=self.root, env=self.env, check=True, capture_output=True, text=True).stdout

    def rb(self, *argv: str, env: dict | None = None) -> tuple[int, dict, str]:
        """rb <argv> --json here: (exit code, envelope, what it said on stderr)."""
        r = subprocess.run([*command(), *argv, "--json"], cwd=self.root, env={**self.env, **(env or {})}, capture_output=True,
                           stdin=subprocess.DEVNULL, encoding="utf-8", errors="replace")
        return r.returncode, json.loads(r.stdout), r.stderr

    def text(self, *argv: str, env: dict | None = None) -> tuple[int, str, str]:
        r = subprocess.run([*command(), *argv], cwd=self.root, env={**self.env, **(env or {})}, capture_output=True,
                           stdin=subprocess.DEVNULL, encoding="utf-8", errors="replace")
        return r.returncode, r.stdout, r.stderr

    def ok(self, *argv: str, env: dict | None = None) -> dict:
        code, envelope, err = self.rb(*argv, env=env)
        assert code == 0 and envelope["ok"], (argv, code, envelope["errors"], err)
        return envelope

    def disk(self) -> dict:
        """Everything a write could leave: each file and directory of the project with its time (and a file's bytes),
        outside .git, and in .git its refs and the names of its objects. Git's own index is git's to refresh on a read."""
        seen: dict = {}
        for dirpath, dirnames, filenames in os.walk(self.root):
            rel = Path(dirpath).relative_to(self.root).as_posix()
            if rel == ".git" or rel.startswith(".git/"):
                if rel == ".git":
                    dirnames[:] = [d for d in dirnames if d in ("refs", "objects", "rb")]
                for name in filenames:
                    path = f"{rel}/{name}"
                    if path.startswith(".git/refs/") or path.startswith(".git/rb/") or path in (".git/packed-refs",):
                        seen[path] = hashlib.sha256(Path(dirpath, name).read_bytes()).hexdigest()
                    elif path.startswith(".git/objects/"):
                        seen[path] = "object"
                continue
            seen[rel + "/"] = os.stat(dirpath).st_mtime_ns
            for name in filenames:
                p = Path(dirpath, name)
                st = p.stat()
                seen[(Path(rel) / name).as_posix()] = (hashlib.sha256(p.read_bytes()).hexdigest(), st.st_mtime_ns, st.st_size)
        return seen

    def log(self) -> list:
        return [json.loads(line) for line in (self.root / ".rb" / "log.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]

    def argv(self, argv: tuple) -> tuple:
        """A write's words with the ids only the record knows: RUN1, the first run, LASTRUN, the last one recorded, and
        PROPOSED, the call an agent session proposed."""
        last = [line["id"] for line in self.log() if (line.get("op"), line.get("kind")) == ("record", "run")][-1]
        return tuple({"RUN1": getattr(self, "run1", ""), "LASTRUN": last, "PROPOSED": getattr(self, "proposed", "")}.get(a, a) for a in argv)


def changed(before: dict, after: dict) -> set:
    return {k for k in set(before) | set(after) if before.get(k) != after.get(k)}


@pytest.fixture
def project(tmp_path, tmp_path_factory):
    root = tmp_path / "work"
    root.mkdir()
    rec = Project(root, tmp_path_factory.mktemp("home"))
    rec.git("init", "-q")
    rec.git("config", "user.email", "ana@example.org")
    rec.git("config", "user.name", "ana")
    (root / "configs").mkdir()
    (root / "configs" / "train.yaml").write_text("optim:\n  lr: 0.1\nbatch_size: 6\nmodel:\n  depth: 50\ntags: []\n", encoding="utf-8")
    (root / "train.py").write_text("STEPS = 48_200\n", encoding="utf-8")
    (root / "paper.txt").write_text("Ours achieves 1.43 validation loss on the held-out set (Table 3).\n", encoding="utf-8")
    (root / "results.md").write_text("| int8 vs fp32 | val_loss 0.03 [0.01, 0.05] |\n", encoding="utf-8")
    (root / "out" / "seed2").mkdir(parents=True)
    (root / "out" / "seed2" / "metrics.json").write_text(json.dumps({"fp32": {"val_loss": 5.60}, "int8": {"val_loss": 5.66}}), encoding="utf-8")
    rec.git("add", "-A")
    rec.git("commit", "-qm", "the work")
    rec.ok("init", "INT8 ResNet-50", "--no-agents")
    rec.ok("question", "add", "Can we ship the INT8 model?", "--id", "q-ship")
    rec.ok("question", "add", "Does INT8 cost accuracy?", "--id", "q-acc", "--parent", "q-ship")
    rec.ok("metric", "add", "val_loss", "--unit", "nats", "--minimize")
    rec.ok("experiment", "add", "INT8 vs FP32 on the held-out set", "--id", "int8", "-q", "q-acc", "--baseline", "fp32", "--candidate", "int8",
           "--varies", "precision")
    rec.ok("experiment", "add", "A later look", "--id", "later", "--baseline", "fp32", "--candidate", "int8")
    rec.ok("spec", "set", "int8", "seed", "--per-run")
    rec.ok("spec", "set", "int8", "optim.lr", "--source", "configs/train.yaml#optim.lr")
    rec.ok("criterion", "add", "INT8 adds at most 0.05 validation loss", "-e", "int8", "-q", "q-acc", "--metric", "change.val_loss",
           "--at-most", "0.05", "--id", "c1")
    rec.ok("criterion", "add", "INT8 stays within 0.1", "-e", "int8", "-q", "q-acc", "--metric", "change.val_loss", "--at-most", "0.1", "--id", "c2",
           "--role", "secondary")
    rec.run1 = rec.ok("run", "add", "int8", "fp32.val_loss=5.61", "int8.val_loss=5.64", "--set", "seed=1")["data"]["object"]["id"]
    rec.ok("resource", "add", "holdout", "--type", "dataset", "--name", "the held-out set")
    # a call an agent session proposed, which the user confirms in their own terminal (rb confirm)
    rec.proposed = rec.ok("decide", "c2", "investigate", "--why", "one more seed", env={**AGENT, "CLAUDE_CODE_SESSION_ID": "sess-dry-earlier"})["data"]["object"]["id"]
    (root / "eval.py").write_text("print('an uncommitted change')\n", encoding="utf-8")   # the code a run's receipt keeps a tree of
    return rec


# every command that writes the record, each as a write that goes through here, in an order where each one's real
# write is what the next stands on
WRITES = [
    ("project", "title", "INT8 ResNet-50 study", "--why", "a clearer theme"),
    ("question", "add", "Is INT8 faster?", "--id", "q-fast", "--parent", "q-ship"),
    ("question", "move", "q-fast", "--top", "--why", "it stands on its own"),
    ("question", "reword", "q-acc", "Does INT8 cost accuracy on the held-out images?", "--why", "plainer words"),
    ("experiment", "add", "INT8 again", "--id", "again", "-q", "q-acc", "--baseline", "fp32", "--candidate", "int8", "--note", "a first note"),
    ("experiment", "file", "later", "q-acc"),
    ("experiment", "relate", "later", "follows_from", "int8", "--note", "the later look"),
    ("experiment", "gate", "int8", "--if-holds", "ship INT8", "--if-not", "keep FP32", "--why", "plainer words"),
    ("note", "add", "the held-out set is not in the pretraining data", "-e", "int8"),
    ("variant", "add", "int8", "int8-pc", "--role", "ablation"),
    ("metric", "add", "latency_ms", "--unit", "ms", "--minimize"),
    ("spec", "set", "int8", "steps", "48200", "--source", "train.py:1", "--term", "STEPS"),
    ("spec", "set", "int8", "--from", "configs/train.yaml", "--keys", "model.*,batch_size"),
    ("spec", "verify", "int8"),
    ("spec", "vary", "int8", "dropout"),
    ("criterion", "add", "INT8 is not slower", "-e", "int8", "-q", "q-fast", "--metric", "change.latency_ms", "--at-most", "0", "--id", "c3"),
    ("criterion", "file", "c1", "q-ship", "--when-met", "INT8 is fine to ship", "--why", "the parent asks it too"),
    ("criterion", "relate", "c2", "reverses", "c1", "--orientation", "same", "--why", "one change, read twice"),
    ("analysis", "add", "int8", "--compare", "int8/fp32:val_loss=0.03[0.01,0.05]", "--method", "paired bootstrap", "--unit", "seed",
     "--units", "3", "--source", "results.md", "--id", "a1"),
    ("run", "add", "int8", "--from", "out/seed2/metrics.json", "--set", "seed=2", "--input", "holdout:eval"),
    ("run", "add", "int8", "fp32.val_loss=5.60", "int8.val_loss=5.65", "--set", "seed=2", "--replaces", "LASTRUN", "--why", "it read the wrong file"),
    ("resource", "add", "calib", "--type", "dataset", "--name", "the calibration set"),
    ("resource", "locate", "holdout", "local", "data/holdout"),
    ("resource", "expose", "holdout", "evaluated", "--why", "scored it once"),
    ("retract", "RUN1", "--why", "seed 1 read the wrong split"),
    ("freeze", "int8", "-m", "before the next runs"),
    ("decide", "c1", "accept", "--why", "within budget"),
    ("confirm", "PROPOSED"),
    ("spec", "set", "int8", "warmup", "100", "--no-verify", "--amend", "--why", "a longer warmup"),
    ("criterion", "add", "INT8 within 0.2", "-e", "int8", "--metric", "change.val_loss", "--at-most", "0.2", "--id", "c4", "--amend", "--why", "a looser rule"),
]


def test_each_single_write_takes_dry_run_and_no_other_command_does():
    """The command line as declared: each of the 26 writes has --dry-run, and no read does, nor a bulk write, which
    previews in its plain form (rb reconcile, rb doctor, rb fmt --check)."""
    from rbtest import cli_parser
    parser = cli_parser.build_parser()
    commands = {" ".join(w[:2]) if w[0] not in ("retract", "freeze", "decide", "confirm") else w[0] for w in WRITES}
    assert len(commands) == 26
    for words in commands:
        assert parser.parse_args([*words.split(), "--dry-run", *_minimal(words)]).dry_run
    for words in ("status", "show c1", "log", "context", "compile", "reconcile --apply", "doctor --restore", "fmt", "page", "export",
                  "journal scan"):
        with pytest.raises(cli_parser.UsageError):
            parser.parse_args([*words.split(), "--dry-run"])


def test_the_manual_lists_the_commands_that_take_dry_run_and_how_the_rest_preview():
    """AGENTS.md names exactly the commands that take --dry-run, and says how each command that writes the record in
    bulk is previewed instead: rb reconcile without --apply, rb doctor without --restore or --adopt, rb fmt --check."""
    from rbtest import ROOT
    manual = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    [said] = [p for p in manual.split("\n\n") if p.startswith("**Previewing a write.**")]
    listed, group = set(), ""
    for span in re.findall(r"`([^`]+)`", said.split(" take `--dry-run`: ", 1)[1].split(". ", 1)[0]):
        words = span.split()
        if words[0] == "rb":
            group = words[1]
            listed.add(" ".join(words[1:]))
        else:
            listed.add(f"{group} {span}")
    assert listed == {" ".join(w[:2]) if w[0] not in ("retract", "freeze", "decide", "confirm") else w[0] for w in WRITES}
    for bulk in ("`rb reconcile` without `--apply`", "`rb doctor` without `--restore` or `--adopt`", "`rb fmt --check`"):
        assert bulk in said, bulk
    assert "preview and make each write in turn" in said


def test_a_preview_reads_the_record_as_it_stands(project):
    """Previews do not chain: a write that rests on one only previewed (a setting of an experiment not yet added) is
    refused in its own preview, as its write would be, and previews once that one is made."""
    add = ("experiment", "add", "Long run", "--id", "long", "--baseline", "fp32", "--candidate", "int8")
    steps = ("spec", "set", "long", "steps", "48200", "--source", "train.py:1", "--term", "STEPS")
    assert project.rb(*add, "--dry-run")[0] == 0
    code, dry, _ = project.rb(*steps, "--dry-run")
    assert code == 2 and [e["code"] for e in dry["errors"]] == ["E_OBJECT_NOT_FOUND"]
    project.ok(*add)
    assert project.rb(*steps, "--dry-run")[0] == 0


def _minimal(words: str) -> list:
    """The fewest arguments each write's command line parses with."""
    return {
        "project title": ["t", "--why", "w"], "question add": ["q"], "question move": ["q", "--top"], "question reword": ["q", "t", "--why", "w"], "experiment add": ["e"],
        "experiment file": ["e", "q"], "experiment relate": ["e", "extends", "f"], "experiment gate": ["e"], "note add": ["n"],
        "variant add": ["e", "v", "--role", "ablation"], "metric add": ["m"], "spec set": ["e", "n", "1"], "spec verify": ["e"],
        "spec vary": ["e", "n"], "criterion add": ["s", "-e", "e", "--metric", "m", "--at-most", "1"], "criterion file": ["c", "q", "--why", "w"],
        "criterion relate": ["c", "reverses", "d", "--why", "w"], "analysis add": ["e"], "run add": ["e", "m=1"],
        "resource add": ["r", "--type", "dataset", "--name", "n"], "resource locate": ["r", "local", "x"], "resource expose": ["r", "inspected"],
        "retract": ["x", "--why", "w"], "freeze": ["e"], "decide": ["c", "accept", "--why", "w"], "confirm": ["d"],
    }[words]


def previewed_then_made(project: Project, argv: tuple, env: dict | None = None) -> tuple[dict, dict]:
    """rb <argv> --dry-run, then rb <argv>: the two envelopes, each with its exit code. The preview leaves the disk as
    it was; the write then leaves what the preview said it would."""
    before = project.disk()
    log_before = project.log()
    code, dry, _ = project.rb(*argv, "--dry-run", env=env)
    assert project.disk() == before, (argv, changed(before, project.disk()))
    code2, real, _ = project.rb(*argv, env=env)
    after = project.disk()
    dry["exit"], real["exit"] = code, code2
    # what the write wrote is what the preview said: each record file, whole, and the lines of the log
    wrote = {k for k in changed(before, after) if k.startswith(".rb/") and not k.endswith("/") and not k.startswith(".rb/cache/")
             and k not in (".rb/log.jsonl", ".rb/.lock")}
    would = dry["data"]["would_write"]
    assert {f["path"] for f in would["files"]} | set(would["removed"]) == wrote, argv
    for f in would["files"]:
        assert (project.root / f["path"]).read_text(encoding="utf-8") == f["content"], (argv, f["path"])
        assert f["new"] == (f["path"] not in before)
    assert all(not (project.root / p).exists() for p in would["removed"])
    assert project.log()[len(log_before):] == would["log"], argv
    return dry, real


@pytest.mark.parametrize("n", range(len(WRITES)), ids=[" ".join(w[:2]) for w in WRITES])
def test_a_preview_writes_nothing_and_says_what_the_write_then_writes(project, n):
    """Every write before this one is made, then this one is previewed and made: the preview leaves every file and its
    time as they were, exits as the write does with the same answer, and says what the write then writes."""
    for argv in WRITES[:n]:
        project.ok(*project.argv(argv))
    dry, real = previewed_then_made(project, project.argv(WRITES[n]))
    assert dry["exit"] == real["exit"] == 0 and dry["ok"] and real["ok"], (dry["errors"], real["errors"])
    assert dry["data"].pop("dry_run") is True and dry["data"].pop("would_write")["log"]
    assert dry["data"] == real["data"] and dry["next"] == real["next"]


@pytest.mark.parametrize("argv, code, error", [
    (("question", "add", "Twice", "--id", "q-ship"), 2, "E_OBJECT_INVALID"),
    (("spec", "set", "int8", "seed", "3"), 2, "E_OBJECT_INVALID"),
    (("decide", "c9", "accept", "--why", "w"), 2, "E_OBJECT_NOT_FOUND"),
    (("run", "add", "int8", "fp32.val_loss=5.6", "int8.val_loss=5.7"), 2, "E_OBJECT_INVALID"),
    (("criterion", "add", "ours", "-e", "int8", "--metric", "val_loss", "--equals", "1.4", "--tolerance", "0.05", "--source", "paper.txt",
      "--quote", "achieves 1.4"), 2, "E_SOURCE_UNRESOLVED"),
    (("spec", "set", "int8", "optim.lr", "0.2", "--source", "configs/train.yaml#optim.lr"), 1, None),
    (("spec", "set", "int8", "steps", "30", "--source", "train.py:1", "--term", "STEPS"), 1, None),
])
def test_a_preview_refuses_and_fails_checks_as_the_write_does(project, argv, code, error):
    """A refusal is the same refusal, and nothing is written for it; a check that fails exits 1 in both, and the
    preview leaves no mark of it: no conflict and no failed check on the setting, and no line in the log."""
    dry, real = previewed_then_made(project, argv)
    assert dry["exit"] == real["exit"] == code and dry["ok"] == real["ok"] == (error is None)
    assert dry["errors"] == real["errors"] and [e["code"] for e in dry["errors"]] == ([error] if error else [])
    assert dry["data"]["dry_run"] is True
    if error:
        assert dry["data"]["would_write"] == {"files": [], "removed": [], "log": []}
    else:
        assert dry["data"]["outcome"] == real["data"]["outcome"] and not dry["data"]["outcome"]["passed"]
        assert [line["op"] for line in dry["data"]["would_write"]["log"]] == ["spec_set", "spec_verify"]


@pytest.mark.skipif(os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0),
                    reason="a directory's write permission is the system's own on Unix, and keeps no one out as root")
def test_a_preview_fails_where_the_system_refuses_the_write(project):
    """A directory rb may not write to (a read-only checkout or mount) fails the write before it changes anything. The
    preview fails the same way, with the same error and exit code, and writes nothing either."""
    questions = project.root / ".rb" / "questions"
    questions.chmod(0o555)
    try:
        dry, real = previewed_then_made(project, ("question", "add", "Is INT8 faster?", "--id", "q-fast"))
    finally:
        questions.chmod(0o755)
    assert dry["exit"] == real["exit"] == 2 and not dry["ok"] and dry["errors"] == real["errors"]
    assert [e["code"] for e in dry["errors"]] == ["E_WRITE_FAILED"]
    assert dry["data"]["would_write"] == {"files": [], "removed": [], "log": []}


def test_a_preview_of_the_write_that_gives_a_record_from_before_a_file_per_part(tmp_path, tmp_path_factory):
    """A record from before parts had files moves on its next write to its experiment: the preview says each part's
    new file and the criteria file the write would remove, and leaves the record as it was."""
    root = tmp_path / "before"
    root.mkdir()
    rec = Project(root, tmp_path_factory.mktemp("home"))
    rec.git("init", "-q")
    rec.git("config", "user.email", "t@example.com")
    rec.git("config", "user.name", "t")
    shutil.copytree(BEFORE / ".rb", root / ".rb")
    rec.git("add", "-A")
    rec.git("commit", "-qm", "a record from before")
    dry, real = previewed_then_made(rec, ("note", "add", "third note", "-e", "e1"))
    would = dry["data"]["would_write"]
    assert dry["exit"] == real["exit"] == 0 and would["removed"] == [".rb/experiments/e1/criteria.yaml"]
    assert {f["path"] for f in would["files"]} >= {".rb/experiments/e1/criteria/c1.yaml", ".rb/experiments/e1/variants/base.yaml",
                                                    ".rb/experiments/e1/experiment.yaml"}


def test_a_preview_keeps_the_exact_code_under_a_path_git_splits(tmp_path, tmp_path_factory):
    """Git splits a list of object directories at ':' (';' on Windows). A project under a directory whose name holds one
    still gets, in a preview, the tree of its uncommitted code, the one the write then records."""
    root = tmp_path / f"col{os.pathsep}on" / "work"
    root.mkdir(parents=True)
    rec = Project(root, tmp_path_factory.mktemp("home"))
    rec.git("init", "-q")
    rec.git("config", "user.email", "ana@example.org")
    rec.git("config", "user.name", "ana")
    (root / "eval.py").write_text("print('the first version')\n", encoding="utf-8")
    rec.git("add", "-A")
    rec.git("commit", "-qm", "the work")
    rec.ok("init", "INT8 ResNet-50", "--no-agents")
    rec.ok("metric", "add", "val_loss", "--minimize")
    rec.ok("experiment", "add", "INT8 vs FP32", "--id", "int8", "--baseline", "fp32", "--candidate", "int8")
    (root / "eval.py").write_text("print('an uncommitted change')\n", encoding="utf-8")
    dry, real = previewed_then_made(rec, ("run", "add", "int8", "fp32.val_loss=5.6", "int8.val_loss=5.7"))
    [run] = [f for f in dry["data"]["would_write"]["files"] if "/runs/" in f["path"]]
    assert dry["exit"] == real["exit"] == 0 and "dirty: true" in run["content"]
    assert re.search(r"\btree: [0-9a-f]{40}\n", run["content"]), run["content"]


def test_a_preview_in_a_fresh_clone_keeps_no_tree_and_no_cache(project):
    """Where the code is all committed and nothing is cached yet, as in a fresh clone, a run's preview names the
    commit's tree and asks for the environment afresh, and keeps neither: no ref under refs/rb/snapshots/, nothing in
    .rb/cache/. The write then keeps both."""
    project.git("add", "-A")
    project.git("commit", "-qm", "the record and the code")
    shutil.rmtree(project.root / ".rb" / "cache")
    tree = project.git("rev-parse", "HEAD^{tree}").strip()
    dry, real = previewed_then_made(project, ("run", "add", "int8", "fp32.val_loss=5.6", "int8.val_loss=5.7", "--set", "seed=3"))
    [run] = [f for f in dry["data"]["would_write"]["files"] if "/runs/" in f["path"]]
    assert dry["exit"] == real["exit"] == 0 and f"tree: {tree}\n" in run["content"]
    assert project.git("for-each-ref", "--format=%(objectname)", f"refs/rb/snapshots/{tree}").strip() == tree
    assert (project.root / ".rb" / "cache" / "environment.json").is_file()


def test_a_preview_leaves_git_s_index_as_it_was(project):
    """A read of git that finds a tracked file's time moved refreshes git's index and writes .git/index. A preview asks
    git not to: the index and its directory keep their bytes and their times."""
    project.git("add", "-A")
    project.git("commit", "-qm", "the record and the code")
    train = project.root / "train.py"
    st = train.stat()
    os.utime(train, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))   # the same bytes, a later time
    git_dir = project.root / ".git"
    seen = lambda: ((git_dir / "index").read_bytes(), (git_dir / "index").stat().st_mtime_ns, git_dir.stat().st_mtime_ns)
    before = seen()
    for argv in [("run", "add", "int8", "fp32.val_loss=5.6", "int8.val_loss=5.7", "--set", "seed=3"),
                 ("analysis", "add", "int8", "--compare", "int8/fp32:val_loss=0.03[0.01,0.05]", "--method", "paired bootstrap",
                  "--source", "results.md")]:
        code, dry, _ = project.rb(*argv, "--dry-run")
        assert code == 0 and dry["data"]["dry_run"] is True, (argv, dry["errors"])
        assert seen() == before, argv


def test_a_preview_warns_as_the_write_does(project):
    """What a write says beside it (numbers typed with no document, keys that are not values, LaTeX in an agent's new
    text) is said by its preview too; an agent's first write in its session adds the session's line, in both."""
    for argv in [("analysis", "add", "int8", "--compare", "int8/fp32:val_loss=0.03[0.01,0.05]", "--method", "paired bootstrap", "--id", "a9"),
                 ("spec", "set", "int8", "--from", "configs/train.yaml", "--keys", "model.depth,tags")]:
        dry, real = previewed_then_made(project, argv)
        assert dry["data"]["warnings"] == real["data"]["warnings"] and dry["data"]["warnings"], argv
    dry, real = previewed_then_made(project, ("note", "add", "INT8 gains $\\Delta \\geq 0.3$", "-e", "int8"), env=AGENT)
    assert dry["data"]["warnings"] == real["data"]["warnings"] and "LaTeX" in dry["data"]["warnings"][0]
    assert [line["op"] for line in dry["data"]["would_write"]["log"]] == ["session", "note"]


def test_a_preview_in_text_says_what_it_would_write_and_names_no_writer(project):
    """rb's text gives the command's own lines, then what the write would write: a new file whole, a changed file as
    the lines that change, the log's new line, without who makes the write; and it exits as the write would."""
    before = project.disk()
    code, out, err = project.text("spec", "set", "int8", "optim.lr", "0.2", "--source", "configs/train.yaml#optim.lr", "--dry-run")
    assert code == 1 and project.disk() == before
    assert out.startswith("Dry run: nothing was written. The write would say:\nint8/optim.lr = 0.2 · provisional")
    assert "CONFLICT, not verified (E_SOURCE_UNRESOLVED): the record says 0.2, but configs/train.yaml#optim.lr says 0.1" in out
    assert "\n\nThe write would make these changes:\n" in out
    assert "  change .rb/experiments/int8/experiment.yaml:" in out and "    -    value: 0.1" in out and "    +    value: 0.2" in out
    assert "  add 2 lines to .rb/log.jsonl:" in out
    code, out, err = project.text("question", "add", "Is INT8 faster?", "--id", "q-fast", "--dry-run", env=AGENT)
    assert code == 0 and project.disk() == before
    assert "  add .rb/questions/q-fast.yaml:\n    rb: 1\n    kind: question\n    id: q-fast\n    text: Is INT8 faster?\n" in out
    for mark in ("human:ana", "agent:", "claude-code", "sess-dry-4f1c", "created_by", "set_by", '"actor"', '"via"', "via: cli"):
        assert mark not in out, mark
    code, out, err = project.text("decide", "c9", "accept", "--why", "w", "--dry-run")
    assert code == 2 and out == "Dry run: nothing was written.\nThe write would be refused before it changed anything.\n"
    assert "rb decide: " in err and project.disk() == before


def test_a_failed_check_previewed_leaves_no_mark_and_the_check_made_is_the_one_shown(project):
    """The brownfield trials ran their checks in throwaway copies of the record, since a check that failed left a mark.
    A preview of one leaves none: the setting stays as it was, verified, with nothing in its history."""
    lr = lambda: project.ok("show", "int8/optim.lr")["data"]
    was = lr()
    code, dry, _ = project.rb("spec", "set", "int8", "optim.lr", "0.2", "--source", "configs/train.yaml#optim.lr", "--dry-run")
    assert code == 1 and dry["data"]["object"]["conflict"]["read"] == 0.1
    assert lr() == was and was["object"]["status"] == "verified" and was["object"]["conflict"] is None


def test_the_sdk_previews_every_write_with_dry_run(project, monkeypatch):
    """dry_run=True on each write of the SDK: the answer is a DryRun, with what the write would give back and what it
    would write; nothing is written; a refusal is raised as the write raises it, and a warning warned."""
    monkeypatch.chdir(project.root)
    for k, v in project.env.items():
        monkeypatch.setenv(k, v)
    for k in AGENT_VARS:
        monkeypatch.delenv(k, raising=False)
    state = rabbit_brain.open(project.root)
    exp = state.experiment("int8")
    before = project.disk()
    previews = {
        "set_project_title": state.set_project_title("A study", why="clearer", dry_run=True),
        "add_question": state.add_question("Is INT8 faster?", id="q-fast", dry_run=True),
        "move_question": state.move_question("q-acc", why="its own line", dry_run=True),
        "file_criterion": state.file_criterion("c1", "q-ship", why="the parent too", dry_run=True),
        "relate_criterion": state.relate_criterion("c2", "reverses", "c1", orientation="same", why="one change", dry_run=True),
        "expose": state.expose("holdout", "inspected", why="looked", dry_run=True),
        "add_metric": state.add_metric("latency_ms", unit="ms", direction="minimize", dry_run=True),
        "add_experiment": state.add_experiment("INT8 again", id="again", baseline="fp32", candidates=["int8"], note="first", dry_run=True),
        "add_note": state.add_note("a project note", dry_run=True),
        "add_resource": state.add_resource("calib", type="dataset", name="the calibration set", dry_run=True),
        "locate_resource": state.locate_resource("holdout", "local", "data/holdout", dry_run=True),
        "decide": state.decide("c1", "accept", why="within budget", dry_run=True),
        "retract": state.retract("c2", why="a second rule is one too many", dry_run=True),
        "add_variant": exp.add_variant("int8-pc", role="ablation", dry_run=True),
        "note": exp.note("an experiment note", dry_run=True),
        "gate": exp.gate(if_holds="ship", if_not="keep FP32", dry_run=True),
        "relate": exp.relate("extends", "later", dry_run=True),
        "criterion": exp.criterion("INT8 within 0.2", metric="change.val_loss", at_most=0.2, id="c9", dry_run=True),
        "analysis": exp.analysis([{"subject": "int8", "reference": "fp32", "metric": "val_loss", "estimate": 0.03, "lo": 0.01, "hi": 0.05}],
                                 source="results.md", method="paired bootstrap", dry_run=True),
        "record": exp.record({"fp32.val_loss": 5.6, "int8.val_loss": 5.7}, per_run={"seed": 3}, dry_run=True),
        "record_from": exp.record_from("out/seed2/metrics.json", per_run={"seed": 2}, dry_run=True),
        "freeze": exp.freeze(why="before the next runs", dry_run=True),
        "set": exp.spec.set("steps", 48200, source="train.py:1", term="STEPS", dry_run=True),
        "verify": exp.spec.verify(dry_run=True),
        "vary": exp.spec.vary("dropout", dry_run=True),
    }
    with exp.run(seed=4, dry_run=True) as run:
        run.log({"fp32.val_loss": 5.6, "int8.val_loss": 5.65})
    previews["run"] = run.record
    assert project.disk() == before
    for name, got in previews.items():
        assert isinstance(got, rabbit_brain.DryRun) and got.would_write["log"], name
        assert got.would_write["files"] or name == "verify", name
    assert previews["add_question"].value == "q-fast" and previews["criterion"].value == "c9" and previews["add_experiment"].value == "again"
    assert previews["set"].value.status == "verified" and previews["record"].value.metrics == {"fp32.val_loss": 5.6, "int8.val_loss": 5.7}
    with pytest.raises(RBError) as refused:
        state.add_question("Twice", id="q-ship", dry_run=True)
    assert refused.value.code == "E_OBJECT_INVALID" and project.disk() == before
    with warnings.catch_warnings(record=True) as said:
        warnings.simplefilter("always")
        exp.analysis([{"subject": "int8", "reference": "fp32", "metric": "val_loss", "estimate": 0.03, "lo": 0.01, "hi": 0.05}],
                     method="paired bootstrap", dry_run=True)
    assert [w.category for w in said] == [RBWarning] and project.disk() == before
    made = state.add_question("Is INT8 faster?", id="q-fast")
    assert made == "q-fast" and json.loads((project.root / ".rb" / "log.jsonl").read_text(encoding="utf-8").splitlines()[-1]) == \
        previews["add_question"].would_write["log"][0]


def test_only_a_write_to_the_record_takes_dry_run_from_python(project, tmp_path, monkeypatch):
    """The SDK sends dry_run only with a write to the record. Any other request that carries it (starting a record,
    looking for new results, an export, a read) is refused before it runs, so nothing it writes outside the record is
    written under a preview."""
    from rabbit_brain import _rb
    for k, v in project.env.items():
        monkeypatch.setenv(k, v)
    for k in AGENT_VARS:
        monkeypatch.delenv(k, raising=False)
    bare = tmp_path / "bare"
    bare.mkdir()
    with pytest.raises(RBError) as refused:
        _rb.call("init", {"title": "A new record"}, root=bare, dry_run=True)
    assert refused.value.code == "E_USAGE" and not (bare / ".rb").exists()
    before = project.disk()
    for op in ("scan", "reconcile", "export", "mark_seen", "status"):
        with pytest.raises(RBError) as refused:
            _rb.call(op, {}, root=project.root, dry_run=True)
        assert refused.value.code == "E_USAGE", op
    assert project.disk() == before


def test_each_mcp_write_tool_previews_with_dry_run(project):
    """dry_run on an MCP write tool: the answer carries data.dry_run and data.would_write, and nothing is written."""
    server = Mcp(root=project.root, env={**project.env, "RB_SESSION": "sess-dry-mcp"})
    try:
        server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18",
                                                                                      "clientInfo": {"name": "dry-client", "version": "1.0"}}})
        server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"})
        tools = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})["result"]["tools"]
        writes = sorted(t["name"] for t in tools if "dry_run" in t["inputSchema"]["properties"])
        assert len(writes) == 25 and "status" not in writes and "run_add" in writes
        before = project.disk()
        for n, (name, arguments) in enumerate([
            ("question_add", {"text": "Is INT8 faster?", "id": "q-fast"}),
            ("question_reword", {"question": "q-acc", "text": "Does INT8 cost accuracy on the held-out images?", "why": "plainer words"}),
            ("spec_set", {"experiment": "int8", "name": "steps", "value": 48200, "source": "train.py:1", "term": "STEPS"}),
            ("run_add", {"experiment": "int8", "metrics": {"fp32.val_loss": 5.6, "int8.val_loss": 5.7}, "set": {"seed": 3}}),
            ("decide", {"subject": "c1", "action": "accept", "why": "within budget"}),
        ], start=3):
            answer = server.handle({"jsonrpc": "2.0", "id": n, "method": "tools/call",
                                    "params": {"name": name, "arguments": {**arguments, "dry_run": True}}})["result"]
            data = answer["structuredContent"]["data"]
            assert not answer["isError"] and data["dry_run"] is True and data["would_write"]["files"], (name, answer)
            assert project.disk() == before, name
    finally:
        server.close()


# ---------------------------------------------------------------- rb init --dry-run: the plan of what init writes

def _tree(root: Path) -> dict:
    """Each file of a repository with its bytes and time: outside .git all of it, in .git rb's own folder and refs."""
    seen = {}
    for dirpath, dirnames, filenames in os.walk(root):
        rel = Path(dirpath).relative_to(root).as_posix()
        if rel == ".git":
            dirnames[:] = [d for d in dirnames if d in ("refs", "rb")]
        for name in filenames:
            p = Path(dirpath, name)
            seen[(Path(rel) / name).as_posix().removeprefix("./")] = (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns)
        seen[rel + "/"] = os.stat(dirpath).st_mtime_ns
    return seen


@pytest.mark.parametrize("signs, argv", [(False, ()), (True, ()), (True, ("--yes",))], ids=["no agent files", "agent files, create-only", "agent files, --yes"])
def test_init_dry_run_writes_nothing_and_lists_exactly_what_init_then_writes(tmp_path, tmp_path_factory, signs, argv):
    """Item 16: rb init --dry-run changes no file, directory time or ref, and the paths its plan lists are the paths the
    real init then writes (a folder's path, ending /, standing for what it holds), with and without an agent's files
    there: every one it lists is made, and nothing it does not list is."""
    root = tmp_path / "work"
    root.mkdir()
    rec = Project(root, tmp_path_factory.mktemp("home"))
    rec.git("init", "-q")
    (root / "train.py").write_text("lr = 0.1\n", encoding="utf-8")
    if signs:
        (root / ".claude").mkdir()
        (root / "AGENTS.md").write_text("# House rules\n", encoding="utf-8")
        (root / ".mcp.json").write_text('{\n    "mcpServers": {}\n}\n', encoding="utf-8")
    rec.git("add", "-A")
    rec.git("-c", "user.name=ana", "-c", "user.email=ana@example.org", "commit", "-qm", "start")
    before = _tree(root)
    preview = rec.ok("init", "INT8 ResNet-50", "--dry-run", *argv)
    assert _tree(root) == before and preview["data"]["dry_run"]
    plan = [p["path"] for p in preview["data"]["plan"]]
    assert {".rb/project.yaml", ".rb/log.jsonl", ".rb/.gitignore", ".rb/.gitattributes", ".rb/.lock", ".rb/cache/"} <= set(plan)
    later = ["refs/rb/snapshots/<tree>", ".git/rb/views/", ".git/rb-last-seen.json"]
    assert [x["path"] for x in preview["data"]["later"]] == later + ["rb-page.html", "rb-export/", "rb-export.zip"]
    code, text, _ = rec.text("init", "INT8 ResNet-50", "--dry-run", *argv)
    assert code == 0 and "rb init would write:\n  .rb/: the record" in text and "\nLater commands add:\n" in text
    rec.ok("init", "INT8 ResNet-50", *argv)
    after = _tree(root)
    made = sorted(p for p in set(before) | set(after) if not p.endswith("/") and before.get(p) != after.get(p))
    unlisted = [p for p in made if not any(p == q or (q.endswith("/") and p.startswith(q)) for q in plan)]
    unmade = [q for q in plan if not any(p == q or (q.endswith("/") and p.startswith(q)) for p in made)]
    assert unlisted == [] and unmade == [], (unlisted, unmade)
    expected = {"AGENTS.md", ".mcp.json", ".claude/skills/rabbit-brain/SKILL.md", ".git/rb/owned.jsonl"} if argv else {".claude/skills/rabbit-brain/SKILL.md", ".git/rb/owned.jsonl"}
    # the journal is the look rb init makes as the record begins, in git's folder, on rb's list of what it writes there
    assert {p for p in plan if not p.startswith(".rb/")} == (expected if signs else set()) | {".git/rb/journal/", ".git/rb/owned.jsonl"}


@pytest.mark.skipif(sys.platform == "win32", reason="a symbolic link takes a privilege there")
@pytest.mark.parametrize("argv", [(), ("--yes",)], ids=["create-only", "--yes"])
def test_init_dry_run_names_the_file_a_link_takes_each_write_to(tmp_path, tmp_path_factory, argv):
    """AGENTS.md is a link to CLAUDE.md and .claude/ a link to a folder outside the repository: the plan names the file
    each write lands in (`real_path` in --json), a new file a link takes outside the repository waits for the yes as a
    change to a file does, and the files the plan names, followed through their links, are exactly the files init then
    writes, wherever they are."""
    root, outside = tmp_path / "work", tmp_path / "team-claude"
    root.mkdir()
    outside.mkdir()
    rec = Project(root, tmp_path_factory.mktemp("home"))
    rec.git("init", "-q")
    (root / "train.py").write_text("lr = 0.1\n", encoding="utf-8")
    (root / "CLAUDE.md").write_text("# House rules\n", encoding="utf-8")
    (root / "AGENTS.md").symlink_to("CLAUDE.md")
    (root / ".claude").symlink_to(outside, target_is_directory=True)
    rec.git("add", "-A")
    rec.git("-c", "user.name=ana", "-c", "user.email=ana@example.org", "commit", "-qm", "start")
    skill = (outside / "skills" / "rabbit-brain" / "SKILL.md").resolve().as_posix()
    before, before_out = _tree(root), _tree(outside)
    preview = rec.ok("init", "INT8 ResNet-50", "--dry-run", *argv)
    assert _tree(root) == before and _tree(outside) == before_out
    planned = {p["path"]: p.get("real_path") for p in preview["data"]["plan"]}
    code, text, _ = rec.text("init", "INT8 ResNet-50", "--dry-run", *argv)
    if argv:
        assert planned["AGENTS.md"] == (root / "CLAUDE.md").resolve().as_posix() and planned[".claude/skills/rabbit-brain/SKILL.md"] == skill
        assert "\n  AGENTS.md (a link to CLAUDE.md): rb's section added at its end; nothing else in it changes\n" in text
        assert f"\n  .claude/skills/rabbit-brain/SKILL.md (through a link, at {skill}, outside this repository): new\n" in text
    else:
        assert "AGENTS.md" not in planned and ".claude/skills/rabbit-brain/SKILL.md" not in planned
        assert f"rb wrote nothing to .claude/skills/rabbit-brain/SKILL.md (through a link, at {skill}, outside this repository)" in text
        assert "AGENTS.md (a link to CLAUDE.md) was here before rb, so rb left it as it is." in text
    rec.ok("init", "INT8 ResNet-50", *argv)
    after, after_out = _tree(root), _tree(outside)
    made = {(base / p).resolve().as_posix() for base, b, a in ((root, before, after), (outside, before_out, after_out))
            for p in set(b) | set(a) if not p.endswith("/") and b.get(p) != a.get(p)}
    listed = [real or (root / path).resolve().as_posix() + ("/" if path.endswith("/") else "") for path, real in planned.items()]
    unlisted = [p for p in made if not any(p == q or (q.endswith("/") and p.startswith(q)) for q in listed)]
    unmade = [q for q in listed if not any(p == q or (q.endswith("/") and p.startswith(q)) for p in made)]
    assert unlisted == [] and unmade == [], (unlisted, unmade)
    assert (skill in made) is bool(argv) and ((root / "CLAUDE.md").resolve().as_posix() in made) is bool(argv)


def test_a_name_the_file_system_matches_in_another_case_is_not_said_to_be_a_link(tmp_path, tmp_path_factory):
    """agents.md and .Claude/ where rb writes AGENTS.md and .claude/: on a file system that matches names in any case,
    rb writes to them, and says nothing of a link, since there is none."""
    root = tmp_path / "work"
    root.mkdir()
    (root / "agents.md").write_text("# House rules\n", encoding="utf-8")
    if not (root / "AGENTS.md").exists():
        pytest.skip("this file system tells names apart by case")
    (root / ".Claude").mkdir()
    rec = Project(root, tmp_path_factory.mktemp("home"))
    rec.git("init", "-q")
    preview = rec.ok("init", "INT8 ResNet-50", "--dry-run", "--yes")
    assert [p for p in preview["data"]["plan"] if "real_path" in p] == []
    code, text, _ = rec.text("init", "INT8 ResNet-50", "--dry-run", "--yes")
    assert code == 0 and "link" not in text
