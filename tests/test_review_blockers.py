"""Regression tests for the blocker findings of the adversarial review of the research state.

Every test names the finding it pins (by its title) in its docstring and asserts the fixed behaviour. Findings that
describe the same defect are pinned together; the docstring lists each title it covers.

Played the way tests/test_adversarial.py plays it: a git repository whose person is human:t, inside a Claude Code
session (CLAUDECODE=1, RB_ACTOR unset). A plain call is the agent's. A person's call drops CLAUDECODE, the way
`env -u CLAUDECODE rb ...` does. `RB_ACTOR=human:jh` typed inside the session is what an agent would type.
"""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from rabbit_brain.cli import main
from rabbit_brain.ledger import Ledger
from rabbit_brain.ledger_cli import GATES
from rabbit_brain.mcp import Server, argv_for
from rabbit_brain.models import Envelope
from rabbit_brain.sources import now

HAS_GIT = shutil.which("git") is not None
pytestmark = pytest.mark.skipif(not HAS_GIT, reason="played in a git repository")
AGENT_VARS = ("CLAUDECODE", "AI_AGENT", "GITHUB_ACTIONS", "RB_ACTOR")
ASSERTED = {"RB_ACTOR": "human:jh"}
IGNORED = "RB_ACTOR=human:jh is ignored"


# ---------------------------------------------------------------- the session


def git(*args) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def session(workdir, monkeypatch, tmp_path_factory):
    """A git repository whose person is human:t, inside a Claude Code session, with RB_ACTOR unset."""
    for name in list(os.environ):
        if name in AGENT_VARS or name.startswith("CODEX_"):
            monkeypatch.delenv(name)
    cfg = tmp_path_factory.mktemp("gitconfig") / "config"
    cfg.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(cfg))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    git("init", "-q")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    git("commit", "-q", "--allow-empty", "-m", "init")
    monkeypatch.setenv("CLAUDECODE", "1")
    return workdir


class RB:
    """rb in-process with --json. A plain call is the agent's (CLAUDECODE=1); person=True drops CLAUDECODE for that call;
    env= adds variables and unset= removes them for that call only."""

    def __init__(self, root: Path, capsys, monkeypatch) -> None:
        self.root, self.capsys, self.mp = root, capsys, monkeypatch

    def __call__(self, *argv, person: bool = False, env: dict | None = None, unset: tuple = ()) -> tuple[int, Envelope]:
        with self.mp.context() as m:
            if person:
                m.delenv("CLAUDECODE", raising=False)
            for k in unset:
                m.delenv(k, raising=False)
            for k, v in (env or {}).items():
                m.setenv(k, v)
            code = main([*argv, "--json"])
        return code, Envelope.model_validate_json(self.capsys.readouterr().out)

    def ok(self, *argv, **kw) -> Envelope:
        code, env = self(*argv, **kw)
        assert code in (0, 1) and env.ok, (argv, code, env.errors)
        return env

    def person(self, *argv) -> Envelope:
        return self.ok(*argv, person=True)

    def refused(self, error: str, *argv, **kw) -> Envelope:
        code, env = self(*argv, **kw)
        assert code == 2 and not env.ok and env.errors[0].code == error, (argv, code, env.errors)
        return env

    def attach(self, exp: str, *argv, **kw) -> str:
        return self.ok("evidence", "attach", exp, *argv, **kw).data["object"]["id"]

    def verdict(self, claim: str = "c1") -> dict:
        return self.ok("show", claim).data["verdict"]

    def gate(self, *gates: str) -> int:
        code, _ = self("status", "--fail-on", ",".join(gates))
        return code

    def doctor(self, *argv, **kw) -> tuple[int, Envelope]:
        return self("doctor", *argv, **kw)

    @property
    def log(self) -> str:
        return (self.root / ".rb" / "log.jsonl").read_text(encoding="utf-8")

    def ledger(self) -> Ledger:
        return Ledger(self.root)


@pytest.fixture
def rb(session, capsys, monkeypatch) -> RB:
    return RB(session, capsys, monkeypatch)


def edit_json(path: Path, **changes) -> None:
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc.update(changes)
    path.write_text(json.dumps(doc, indent=2), encoding="utf-8")


def failures(env: Envelope) -> list[str]:
    return [f["detail"] for f in env.data["outcome"]["failures"]]


def observed(v: dict) -> list[float]:
    return [round(o["value"], 6) for o in v["observations"]]


# ================================================================ asserted retraction (finding 0)


@pytest.fixture
def mixed(rb) -> str:
    """A person froze c1 (c.loss <= 1); the agent attached a run that meets it and one that misses. Returns the miss."""
    rb.person("init", "t")
    rb.person("experiment", "add", "x", "--id", "e1", "--baseline", "b", "--candidate", "c")
    rb.person("claim", "add", "c at most 1", "-e", "e1", "--metric", "c.loss", "--at-most", "1", "--id", "c1")
    rb.person("freeze", "e1", "-m", "locked")
    rb.attach("e1", "c.loss=0.5")
    bad = rb.attach("e1", "c.loss=1.5")
    assert rb.verdict()["status"] == "mixed"
    return bad


RUNTIMES = {
    "claude-code": {"CLAUDECODE": "1"},
    "ai-agent": {"AI_AGENT": "cursor"},
    "codex": {"CODEX_SANDBOX": "seatbelt"},
    "codex-network": {"CODEX_SANDBOX_NETWORK_DISABLED": "1"},
    "codex-ci": {"CODEX_CI": "1"},
    "github-actions": {"GITHUB_ACTIONS": "true"},
}


@pytest.mark.parametrize("runtime", list(RUNTIMES))
def test_asserted_retraction_of_counted_evidence_is_refused_in_every_agent_runtime(mixed, rb, runtime):
    """Finding: 'An asserted retraction (RB_ACTOR=human:x inside Claude Code) of evidence a frozen claim counts succeeds
    and counts'. Inside a detected agent runtime RB_ACTOR=human:x is ignored: the retraction is a person's call, it is
    refused with E_HUMAN_ONLY, nothing is written, the refuting run still counts and the gate still fails."""
    before = rb.log
    code, env = rb("retract", mixed, "-m", "outlier", unset=("CLAUDECODE",), env={**RUNTIMES[runtime], **ASSERTED})
    assert code == 2 and env.errors[0].code == "E_HUMAN_ONLY"
    assert IGNORED in env.errors[0].message
    assert rb.log == before
    assert rb.ok("show", mixed).data["object"]["retracted"] is None
    v = rb.verdict()
    assert v["status"] == "mixed" and not v["established"]
    assert rb.gate("unestablished", "refuted", "undecided") == 1


def test_the_persons_own_retraction_of_that_evidence_is_taken(mixed, rb):
    """Finding: 'An asserted retraction (RB_ACTOR=human:x inside Claude Code) of evidence a frozen claim counts succeeds
    and counts'. The control: from the person's own terminal the same retraction is taken, so the refusal above is the
    runtime rule, not a broken retract."""
    rb.person("retract", mixed, "-m", "outlier")
    assert rb.verdict()["status"] == "supported"
    rows = [json.loads(line) for line in rb.log.splitlines()]
    assert rows[-1]["op"] == "retract" and rows[-1]["actor"] == "human:t"


# ================================================================ asserted amendments (findings 1 and 12)


@pytest.fixture
def frozen_unknown(rb) -> None:
    """A person froze e1 with cuda recorded as unknown (required), and a claim c1 on it."""
    rb.person("init", "t")
    rb.person("experiment", "add", "x", "--id", "e1", "--baseline", "b", "--candidate", "c")
    rb.person("spec", "set", "e1", "cuda", "--unknown")
    rb.person("claim", "add", "c at most 1", "-e", "e1", "--metric", "c.loss", "--at-most", "1", "--id", "c1")
    rb.person("freeze", "e1", "-m", "locked")


def test_asserted_amend_does_not_make_a_required_unknown_setting_optional(frozen_unknown, rb):
    """Finding: 'An asserted --amend changes a frozen spec with no drift or caveat, and the claim becomes established'.
    The agent's plain change is E_FROZEN; with RB_ACTOR=human:jh and --amend it is E_HUMAN_ONLY (the name is ignored),
    no amendment is recorded, cuda stays required and unknown, and the claim is not established."""
    rb.refused("E_FROZEN", "spec", "set", "e1", "cuda", "--optional")
    before = rb.log
    env = rb.refused("E_HUMAN_ONLY", "spec", "set", "e1", "cuda", "--optional", "--amend", "--why", "does not matter", env=ASSERTED)
    assert IGNORED in env.errors[0].message
    assert rb.log == before
    exp = rb.ledger().load("experiment", "e1")
    assert exp.amendments == [] and exp.lookup("cuda").required and exp.lookup("cuda").value is None
    rb.attach("e1", "c.loss=0.5")
    v = rb.verdict()
    assert not v["established"] and "unknown" in v["not_established_because"]
    assert rb.gate("unestablished") == 1
    assert rb.ok("show", "e1").data["spec_drift"] is None


@pytest.mark.parametrize("argv", [
    ("spec", "set", "e1", "cuda", "12.1", "--amend", "--why", "x"),
    ("spec", "vary", "e1", "cuda", "--amend", "--why", "x"),
    ("variant", "add", "e1", "d", "--role", "candidate", "--amend", "--why", "x"),
    ("claim", "add", "looser", "-e", "e1", "--metric", "c.loss", "--at-most", "9", "--amend", "--why", "x"),
    ("freeze", "e1", "--amend", "--why", "adopt it"),
    ("retract", "e1/cuda", "-m", "x"),
], ids=["spec-set", "spec-vary", "variant-add", "claim-add", "freeze-amend", "retract-setting"])
def test_every_asserted_amendment_of_a_frozen_experiment_is_refused(frozen_unknown, rb, argv):
    """Findings: 'An asserted --amend changes a frozen spec with no drift or caveat, and the claim becomes established'
    and 'An asserted --amend (RB_ACTOR=human:x inside an agent session) changes a frozen spec and counts; the claim
    becomes established'. Every way of amending a frozen experiment is a person's call, and a person's name typed inside
    the session is ignored: E_HUMAN_ONLY, nothing written, the freeze record untouched."""
    sha = rb.ledger().load("experiment", "e1").frozen.sha256
    before = rb.log
    env = rb.refused("E_HUMAN_ONLY", *argv, env=ASSERTED)
    assert IGNORED in env.errors[0].message
    assert rb.log == before
    exp = rb.ledger().load("experiment", "e1")
    assert exp.amendments == [] and exp.frozen.sha256 == sha and rb.ledger().spec_drift(exp) is None


def test_asserted_spec_vary_amend_does_not_clear_a_confound(rb):
    """Finding: 'An asserted --amend (RB_ACTOR=human:x inside an agent session) changes a frozen spec and counts; the
    claim becomes established'. RB_ACTOR=human:alice rb spec vary e batch_size --amend is refused; batch_size is still
    a confound, and the next run leaves the claim not established with the gate failing."""
    rb.person("init", "t")
    rb.person("experiment", "add", "x", "--id", "e", "--baseline", "fp32", "--candidate", "int8", "--varies", "precision")
    rb.person("spec", "set", "e", "fp32.batch_size", "8", "--no-verify")
    rb.person("spec", "set", "e", "int8.batch_size", "16", "--no-verify")
    rb.person("decide", "e/fp32.batch_size", "accept", "-m", "ok")
    rb.person("decide", "e/int8.batch_size", "accept", "-m", "ok")
    rb.person("claim", "add", "c", "-e", "e", "--metric", "change.epe", "--at-most", "0.05", "--id", "c1")
    rb.person("freeze", "e", "-m", "go")
    rb.person("evidence", "attach", "e", "fp32.epe=5.61", "int8.epe=5.64")
    assert "confound" in rb.verdict()["not_established_because"]
    before = rb.log
    env = rb.refused("E_HUMAN_ONLY", "spec", "vary", "e", "batch_size", "--amend", "--why", "x", env={"RB_ACTOR": "human:alice"})
    assert "RB_ACTOR=human:alice is ignored" in env.errors[0].message
    assert rb.log == before
    exp = rb.ledger().load("experiment", "e")
    assert exp.varies == ["precision"] and exp.amendments == []
    rb.attach("e", "fp32.epe=5.60", "int8.epe=5.62")
    v = rb.verdict()
    assert not v["established"] and "confound" in v["not_established_because"]
    assert rb.gate("unestablished", "stale") == 1


# ================================================================ a frozen setting's source and cited value (findings 2 and 11)


@pytest.fixture
def raft(rb) -> None:
    """Finding 2's state: lr verified from configs/train.yaml, iters cited as 24 in the paper but 12 here, a person froze
    it all, then train.yaml changed lr to 0.02, then a run was attached. c1 is not comparable and lr is in conflict."""
    Path("configs").mkdir()
    Path("configs/train.yaml").write_text("optim:\n  lr: 0.01\n", encoding="utf-8")
    Path("configs/eval.yaml").write_text("eval:\n  iters: 12\n", encoding="utf-8")
    Path("paper.txt").write_text("Table 3: RAFT achieves 1.43 EPE with 24 update iterations.\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-qm", "x")
    rb.person("init", "t")
    rb.person("experiment", "add", "x", "--id", "e1", "--baseline", "fp32", "--candidate", "raft")
    rb.person("spec", "set", "e1", "lr", "--source", "configs/train.yaml#optim.lr")
    rb.person("spec", "set", "e1", "iters", "--source", "configs/eval.yaml#eval.iters", "--cited", "24", "--cited-source", "paper.txt")
    rb.person("claim", "add", "RAFT reproduces 1.43", "-e", "e1", "--metric", "raft.epe", "--equals", "1.43", "--tolerance", "0.05",
              "--source", "paper.txt", "--quote", "achieves 1.43 EPE", "--id", "c1")
    rb.person("freeze", "e1", "-m", "locked")
    Path("configs/train.yaml").write_text("optim:\n  lr: 0.02\n", encoding="utf-8")
    git("commit", "-qam", "lr")
    rb.attach("e1", "raft.epe=1.45")
    v = rb.verdict()
    assert v["status"] == "not_comparable" and "conflict" in v["not_established_because"]


def test_re_sourcing_a_frozen_setting_to_clear_a_conflict_is_frozen(raft, rb):
    """Finding: 'Freeze does not lock a setting's source or cited value, so an agent can clear a conflict or
    not_comparable after the freeze' (a). Pointing lr at another file that says 0.01 changes the frozen source: E_FROZEN,
    nothing written, the conflict stays and the stale gate fails."""
    Path("configs/old.yaml").write_text("lr: 0.01\n", encoding="utf-8")
    before = rb.log
    rb.refused("E_FROZEN", "spec", "set", "e1", "lr", "--source", "configs/old.yaml#lr")
    assert rb.log == before
    assert rb.ledger().load("experiment", "e1").lookup("lr").source.label().startswith("configs/train.yaml")
    assert "conflict" in rb.verdict()["not_established_because"]
    assert rb.gate("unestablished", "stale") == 1
    assert rb.gate("stale") == 1


def test_changing_a_frozen_cited_value_to_make_a_claim_comparable_is_frozen(raft, rb):
    """Finding: 'Freeze does not lock a setting's source or cited value, so an agent can clear a conflict or
    not_comparable after the freeze' (b). rb spec set e1 iters 12 --cited 12 on the frozen experiment is E_FROZEN, and
    c1 stays not comparable."""
    before = rb.log
    rb.refused("E_FROZEN", "spec", "set", "e1", "iters", "12", "--cited", "12")
    assert rb.log == before
    assert rb.ledger().load("experiment", "e1").lookup("iters").cited.value == 24
    v = rb.verdict()
    assert v["status"] == "not_comparable" and not v["established"]
    assert rb.gate("unestablished", "stale") == 1


def test_a_person_can_amend_a_frozen_cited_value_and_the_amendment_starts_from_the_recorded_hash(raft, rb):
    """Finding: 'Freeze does not lock a setting's source or cited value, so an agent can clear a conflict or
    not_comparable after the freeze'. The control: a person's --amend changes the cited value, records an amendment
    whose 'before' is the freeze's hash, and leaves no drift."""
    frozen = rb.ledger().load("experiment", "e1").frozen.sha256
    rb.person("spec", "set", "e1", "iters", "12", "--cited", "12", "--amend", "--why", "the paper's 24 is a typo")
    exp = rb.ledger().load("experiment", "e1")
    assert len(exp.amendments) == 1 and exp.amendments[0].before == frozen and exp.amendments[0].by == "human:t"
    assert rb.ledger().spec_drift(exp) is None
    assert exp.lookup("iters").cited.value == 12


@pytest.fixture
def cited_lr(rb) -> None:
    """Finding 11's state: lr 0.0001 here and 0.0004 in the paper; a person froze it; the cited claim is not comparable."""
    Path("paper.txt").write_text("Table 3: RAFT reaches an EPE of 5.64 on KITTI.\nWe use a learning rate of 0.0004.\n", encoding="utf-8")
    Path("train.yaml").write_text("lr: 0.0001\n", encoding="utf-8")
    Path("other.yaml").write_text("lr: 0.0001\n", encoding="utf-8")
    rb.person("init", "t")
    rb.person("experiment", "add", "x", "--id", "e", "--baseline", "fp32", "--candidate", "int8")
    rb.person("spec", "set", "e", "lr", "--source", "train.yaml#lr", "--cited", "0.0004", "--cited-source", "paper.txt:2")
    rb.person("claim", "add", "r", "-e", "e", "--metric", "int8.epe", "--equals", "5.64", "--tolerance", "0.05",
              "--source", "paper.txt", "--quote", "EPE of 5.64", "--id", "c1")
    rb.person("freeze", "e", "-m", "go")
    rb.person("evidence", "attach", "e", "int8.epe=5.66")
    assert rb.verdict()["status"] == "not_comparable"


@pytest.mark.parametrize("argv", [
    ("spec", "set", "e", "lr", "--cited", "0.0001"),
    ("spec", "set", "e", "lr", "--cited", "0.0004", "--cited-source", "paper.txt:1"),
    ("spec", "set", "e", "lr", "--source", "other.yaml#lr"),
], ids=["cited-value", "cited-source", "source"])
def test_an_agent_cannot_change_a_frozen_settings_cited_value_or_source(cited_lr, rb, argv):
    """Finding: 'An agent can change a setting's --cited value on a frozen experiment without an amendment, turning
    not_comparable into reproduced · established'. The cited value, where it comes from, and the setting's own source
    are in the freeze: changing any of them is E_FROZEN, and the claim stays not comparable with the gate failing."""
    before = rb.log
    rb.refused("E_FROZEN", *argv)
    assert rb.log == before
    v = rb.verdict()
    assert v["status"] == "not_comparable" and not v["established"]
    assert rb.gate("unestablished") == 1


def test_an_asserted_amend_of_a_cited_value_is_refused(cited_lr, rb):
    """Finding: 'An agent can change a setting's --cited value on a frozen experiment without an amendment, turning
    not_comparable into reproduced · established'. With --amend it is a person's call, and RB_ACTOR=human:jh inside
    the session does not make it one."""
    before = rb.log
    env = rb.refused("E_HUMAN_ONLY", "spec", "set", "e", "lr", "--cited", "0.0001", "--amend", "--why", "x", env=ASSERTED)
    assert IGNORED in env.errors[0].message and rb.log == before
    assert rb.verdict()["status"] == "not_comparable"


# ================================================================ the catalogue under a frozen claim (findings 3 and 10)


def two_epes(rb, catalogued: bool) -> None:
    """A person froze 'change.epe <= 0.05'; the agent attached a file reporting epe (a change of 0.3) and epe_noc (0.02)."""
    rb.person("init", "t")
    rb.person("experiment", "add", "x", "--id", "e1", "--baseline", "fp32", "--candidate", "int8")
    if catalogued:
        rb.person("metric", "add", "epe", "--unit", "px", "--minimize")
    rb.person("claim", "add", "int8 costs at most 0.05 px", "-e", "e1", "--metric", "change.epe", "--at-most", "0.05", "--id", "c1")
    rb.person("freeze", "e1", "-m", "locked")
    Path("out").mkdir()
    Path("out/m.json").write_text(json.dumps({"fp32": {"epe": 5.60, "epe_noc": 3.00}, "int8": {"epe": 5.90, "epe_noc": 3.02}}), encoding="utf-8")
    git("add", "-A")
    git("commit", "-qm", "x")
    rb.attach("e1", "--from", "out/m.json")
    v = rb.verdict()
    assert v["status"] == "refuted" and observed(v) == [0.3]


def test_an_agent_cannot_retract_a_metric_a_frozen_claim_reads_or_alias_another_to_its_name(rb):
    """Finding: 'After a freeze an agent can redefine a claim's metric through the catalogue (retract a metric, alias
    another to its name)'. Retracting epe, which c1 reads, is a person's call; aliasing epe_noc to the catalogued epe
    is refused; c1 stays refuted on the 0.3 it read."""
    two_epes(rb, catalogued=True)
    before = rb.log
    rb.refused("E_HUMAN_ONLY", "retract", "epe", "-m", "rename")
    rb.refused("E_OBJECT_INVALID", "metric", "add", "epe_noc", "--minimize", "--alias", "epe")
    assert rb.log == before
    v = rb.verdict()
    assert v["status"] == "refuted" and observed(v) == [0.3]
    assert rb.gate("refuted") == 1


def test_aliasing_an_uncatalogued_metric_under_a_frozen_claim_does_not_redefine_it(rb):
    """Finding: 'After a freeze an agent can redefine a claim's metric through the catalogue (retract a metric, alias
    another to its name)', when epe is not catalogued and the alias step alone does it. The catalogue entries a frozen
    claim reads are in the freeze, so the alias shows as drift; and epe and epe_noc now read as one metric and
    disagree, so neither is read. c1 never reads 0.02 and is not established."""
    two_epes(rb, catalogued=False)
    rb.ok("metric", "add", "epe_noc", "--minimize", "--alias", "epe")
    v = rb.verdict()
    assert 0.02 not in observed(v)
    assert v["status"] != "supported" and not v["established"]
    assert "drift" in v["not_established_because"]
    assert "not_read" in {c["code"] for c in v["caveats"]}
    assert rb.gate("unestablished") == 1
    assert rb.gate("stale") == 1


def test_adding_an_alias_that_collides_with_a_reported_name_does_not_flip_a_refuted_claim(rb):
    """Finding: 'Adding a metric alias after the fact flips a frozen, refuted claim to established (metric names that
    collide silently keep the last value)'. After rb metric add epe --alias latency_ms, int8.epe (6) and
    int8.latency_ms (3) read as the same metric and disagree: neither is read (never last-one-wins), the catalogue
    change shows as drift, and the gate fails."""
    rb.person("init", "t")
    rb.person("experiment", "add", "x", "--id", "e", "--baseline", "fp32", "--candidate", "int8")
    rb.person("claim", "add", "c", "-e", "e", "--metric", "int8.epe", "--at-most", "5", "--id", "c1")
    rb.person("freeze", "e", "-m", "go")
    rb.attach("e", "int8.epe=6", "int8.latency_ms=3")
    v = rb.verdict()
    assert v["status"] == "refuted" and observed(v) == [6]
    rb.ok("metric", "add", "epe", "--alias", "latency_ms")
    v = rb.verdict()
    assert 3 not in observed(v) and v["status"] != "supported" and not v["established"]
    assert "not_read" in {c["code"] for c in v["caveats"]}
    assert "drift" in v["not_established_because"]
    assert rb.gate("unestablished", "refuted") == 1


# ================================================================ a file deleted under .rb/ (finding 4)


@pytest.fixture
def refuting_run(rb) -> str:
    """A person froze c1 (change.epe <= 0.05); the agent attached a refuting run, then a supporting one. Returns the
    refuting run's id."""
    rb.person("init", "t")
    rb.person("experiment", "add", "x", "--id", "e1", "--baseline", "fp32", "--candidate", "int8")
    rb.person("claim", "add", "int8 costs at most 0.05", "-e", "e1", "--metric", "change.epe", "--at-most", "0.05", "--id", "c1")
    rb.person("freeze", "e1", "-m", "locked")
    bad = rb.attach("e1", "fp32.epe=5.60", "int8.epe=5.90")
    rb.attach("e1", "fp32.epe=5.60", "int8.epe=5.62")
    assert rb.verdict()["status"] == "mixed" and rb.gate("unestablished", "stale") == 1
    return bad


def test_deleting_refuting_evidence_fails_the_gate_and_doctor(refuting_run, rb):
    """Finding: 'Deleting a file under .rb/ goes unnoticed: rm of refuting evidence establishes the claim, and gate and
    doctor both pass'. An id log.jsonl records as written whose file is gone is edited outside rb: c1 is not
    established, every gate fails, and rb doctor lists it as deleted outside rb."""
    assert refuting_run in rb.log
    (rb.root / ".rb" / "evidence" / f"{refuting_run}.json").unlink()
    v = rb.verdict()
    assert not v["established"] and "edited_outside_rb" in v["not_established_because"]
    assert rb.gate("unestablished", "stale") == 1
    for g in GATES:
        assert rb.gate(g) == 1, g
    code, env = rb.doctor()
    assert code == 1
    assert any(refuting_run in d and "deleted outside rb" in d for d in failures(env))


def test_doctor_restore_puts_deleted_evidence_back(refuting_run, rb):
    """Finding: 'Deleting a file under .rb/ goes unnoticed: rm of refuting evidence establishes the claim, and gate and
    doctor both pass'. rb doctor --restore puts back rb's last write from log.jsonl: the refuting run counts again."""
    path = rb.root / ".rb" / "evidence" / f"{refuting_run}.json"
    path.unlink()
    code, env = rb.doctor("--restore")
    assert path.exists() and [r["id"] for r in env.data["restored"]] == [refuting_run]
    assert rb.verdict()["status"] == "mixed"
    assert rb.doctor()[0] == 0


def test_deleting_a_claim_on_an_unfrozen_experiment_fails_the_gate(rb):
    """Finding: 'Deleting a file under .rb/ goes unnoticed: rm of refuting evidence establishes the claim, and gate and
    doctor both pass' (a deleted claim on an unfrozen experiment vanishes the same way). The refuted claim's file is
    removed; the refuted gate still fails and rb doctor lists it."""
    rb.person("init", "t")
    rb.person("experiment", "add", "x", "--id", "e1", "--baseline", "fp32", "--candidate", "int8")
    rb.ok("claim", "add", "ok", "-e", "e1", "--metric", "change.epe", "--at-most", "0.05", "--id", "c1")
    rb.ok("claim", "add", "tight", "-e", "e1", "--metric", "change.epe", "--at-most", "0.01", "--id", "c2")
    rb.attach("e1", "fp32.epe=5.60", "int8.epe=5.62")
    assert rb.verdict("c2")["status"] == "refuted" and rb.gate("refuted") == 1
    (rb.root / ".rb" / "claims" / "c2.json").unlink()
    assert rb.gate("refuted") == 1
    assert "edited_outside_rb" in rb.verdict("c1")["not_established_because"]
    code, env = rb.doctor()
    assert code == 1 and any("c2" in d and "deleted outside rb" in d for d in failures(env))


# ================================================================ files written or edited into .rb/ by hand (findings 5, 8, 14, 15)


@pytest.fixture
def provisional_cuda(rb) -> Path:
    """cuda recorded from a note (so provisional), a person froze c1, the agent attached a supporting run. The only
    thing between c1 and established is a person vouching for cuda. Returns .rb/."""
    rb.person("init", "t")
    rb.person("experiment", "add", "x", "--id", "e1", "--baseline", "fp32", "--candidate", "int8")
    rb.person("spec", "set", "e1", "cuda", "12.1", "--source", "note:cluster image")
    rb.person("claim", "add", "int8 costs at most 0.05", "-e", "e1", "--metric", "change.epe", "--at-most", "0.05", "--id", "c1")
    rb.person("freeze", "e1", "-m", "locked")
    rb.attach("e1", "fp32.epe=5.60", "int8.epe=5.62")
    v = rb.verdict()
    assert v["status"] == "supported" and v["not_established_because"] == ["provisional"]
    return rb.root / ".rb"


def hand_decision(rb, did: str = "dvouch", outcome: str = "accept", by: str = "human:jh", **extra) -> Path:
    """A valid decision file accepting e1/cuda at the value it has, written into .rb/ without rb."""
    value = rb.ledger().load("experiment", "e1").lookup("cuda").value
    doc = {"id": did, "subject": "e1/cuda", "outcome": outcome, "why": "x", "by": by, "at": now(), "via": "cli",
           "verdict": None, "evidence": [], "value": value, "across": None, "retracted": None, **extra}
    d = rb.root / ".rb" / "decisions"
    d.mkdir(exist_ok=True)
    path = d / f"{did}.json"
    path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    return path


def test_a_hand_written_decision_does_not_vouch_for_a_setting(provisional_cuda, rb):
    """Findings: 'Hand-written decision or metric files (no log entry at all) count in verdicts and pass the gate; only
    rb doctor notices', 'A decision file written into .rb/ by hand counts as a person's vouch; the claim becomes
    established and every gate passes', 'A decision file written into .rb/ by hand counts as a person's vouch and
    passes the CI gate, ...' and 'A decision file written by hand counts as a person's vouch: an agent can establish a
    claim without a log entry, and every gate passes'. The file has no log entry: c1 is not established and has a
    blocking edited_outside_rb caveat, and rb doctor lists the file as not written by rb."""
    hand_decision(rb)
    v = rb.verdict()
    assert not v["established"] and "edited_outside_rb" in v["not_established_because"]
    assert rb.ok("status").data["tally"].get("established", 0) == 0
    code, env = rb.doctor()
    assert code == 1 and any("dvouch" in d and "not written by rb" in d for d in failures(env))


@pytest.mark.parametrize("gate", GATES)
def test_a_hand_written_decision_fails_every_gate(provisional_cuda, rb, gate):
    """Findings: 'A decision file written into .rb/ by hand counts as a person's vouch; the claim becomes established
    and every gate passes' and 'A decision file written by hand counts as a person's vouch: an agent can establish a
    claim without a log entry, and every gate passes'. An object written outside rb fails any --fail-on gate."""
    hand_decision(rb)
    code, env = rb("status", "--fail-on", gate)
    assert code == 1 and not env.data["outcome"]["passed"]


def test_doctor_restore_moves_a_hand_written_decision_aside(provisional_cuda, rb):
    """Finding: 'Hand-written decision or metric files (no log entry at all) count in verdicts and pass the gate; only
    rb doctor notices'. rb doctor --restore (anyone may run it) moves a file rb never wrote aside to *.outside-rb; the
    state is then rb's own again and cuda is back to provisional."""
    path = hand_decision(rb)
    code, env = rb.doctor("--restore")
    assert not path.exists() and path.with_name(path.name + ".outside-rb").exists()
    assert rb.verdict()["not_established_because"] == ["provisional"]
    assert rb.doctor()[0] == 0


def test_adopting_a_hand_written_decision_is_a_persons_call(provisional_cuda, rb):
    """Finding: 'A decision file written by hand counts as a person's vouch: an agent can establish a claim without a
    log entry, and every gate passes'. rb doctor --adopt is a person's call: the agent is refused, with RB_ACTOR=human:jh
    too, and the file keeps failing the gate."""
    hand_decision(rb)
    before = rb.log
    rb.refused("E_HUMAN_ONLY", "doctor", "--adopt", "--why", "it is right")
    env = rb.refused("E_HUMAN_ONLY", "doctor", "--adopt", "--why", "it is right", env=ASSERTED)
    assert IGNORED in env.errors[0].message
    assert rb.log == before and rb.gate("unestablished") == 1


def test_hand_editing_a_persons_reject_into_an_accept_is_caught(provisional_cuda, rb):
    """Finding: 'A decision file written by hand counts as a person's vouch: an agent can establish a claim without a
    log entry, and every gate passes' (changing a person's existing 'reject' decision to 'accept' by hand works the same
    way). The edited decision differs from rb's write: c1 is not established and every gate fails."""
    did = rb.person("decide", "e1/cuda", "reject", "-m", "wrong image").data["object"]["id"]
    assert "rejected_setting" in rb.verdict()["not_established_because"]
    edit_json(rb.root / ".rb" / "decisions" / f"{did}.json", outcome="accept")
    v = rb.verdict()
    assert not v["established"] and "edited_outside_rb" in v["not_established_because"]
    assert rb.gate("unestablished") == 1 and rb.gate("stale") == 1


def test_a_hand_written_decision_in_an_invalid_shape_still_fails_the_gate_and_doctor_lists_it(provisional_cuda, rb):
    """Finding: 'A decision file written into .rb/ by hand counts as a person's vouch; the claim becomes established and
    every gate passes', with the review's own file (it carries asserted_from, which a decision no longer has). The gate
    does not pass, and rb doctor lists the file instead of crashing."""
    hand_decision(rb, did="dforge", asserted_from=None)
    rb.refused("E_STATE_CORRUPT", "status", "--fail-on", "unestablished")
    code, env = rb.doctor()
    assert code == 1 and any("dforge" in d and "not a valid decision" in d for d in failures(env))
    rb.doctor("--restore")
    assert rb.verdict()["not_established_because"] == ["provisional"]


def test_a_hand_written_metric_file_does_not_redirect_a_claim(rb):
    """Finding: 'Hand-written decision or metric files (no log entry at all) count in verdicts and pass the gate; only
    rb doctor notices' (a hand-written .rb/metrics/*.json with an alias). The metric is not rb's write: c1 does not read
    3 as established, and every gate fails."""
    rb.person("init", "t")
    rb.person("experiment", "add", "x", "--id", "e", "--baseline", "fp32", "--candidate", "int8")
    rb.person("claim", "add", "c", "-e", "e", "--metric", "int8.epe", "--at-most", "5", "--id", "c1")
    rb.person("freeze", "e", "-m", "go")
    rb.attach("e", "int8.epe=6", "int8.latency_ms=3")
    assert rb.verdict()["status"] == "refuted"
    d = rb.root / ".rb" / "metrics"
    d.mkdir(exist_ok=True)
    (d / "epe.json").write_text(json.dumps({"id": "epe", "unit": "", "direction": "none", "aliases": ["latency_ms"], "description": "",
                                            "created_by": "human:jh", "created_at": now(), "via": "cli", "retracted": None}), encoding="utf-8")
    v = rb.verdict()
    assert not v["established"] and "edited_outside_rb" in v["not_established_because"]
    for g in GATES:
        assert rb.gate(g) == 1, g
    code, env = rb.doctor()
    assert code == 1 and any("epe" in d_ and "not written by rb" in d_ for d_ in failures(env))


def test_hand_edited_evidence_fails_the_stale_and_refuted_gates(provisional_cuda, rb):
    """Finding: 'A decision file written into .rb/ by hand counts as a person's vouch and passes the CI gate, and
    hand-edited evidence or hypotheses fail no gate'. Evidence edited by hand stops counting and fails every gate,
    --fail-on stale and --fail-on refuted included."""
    assert rb.gate("stale") == 0 and rb.gate("refuted") == 0
    ev = next((rb.root / ".rb" / "evidence").glob("*.json"))
    edit_json(ev, note="x")
    obs = rb.verdict()["observations"]
    assert [o["role"] for o in obs] == ["not_counted"]
    assert rb.gate("stale") == 1
    assert rb.gate("refuted") == 1


def test_a_hand_edited_hypothesis_fails_every_gate(rb):
    """Finding: 'A decision file written into .rb/ by hand counts as a person's vouch and passes the CI gate, and
    hand-edited evidence or hypotheses fail no gate'. A hypothesis hand-edited to accepted is edited outside rb: every
    gate fails and rb doctor lists it."""
    rb.person("init", "t")
    rb.ok("hypothesis", "add", "h", "--id", "h1")
    for g in GATES:
        assert rb.gate(g) == 0, g
    edit_json(rb.root / ".rb" / "hypotheses" / "h1.json", status="accepted")
    for g in GATES:
        assert rb.gate(g) == 1, g
    code, env = rb.doctor()
    assert code == 1 and any("h1" in d for d in failures(env))


# ================================================================ repeats toward --min-n (finding 6)


@pytest.fixture
def per_run_seed(rb):
    """seed is per-run; a person writes c1 (change.epe <= 0.05) with the given --min-n and freezes it."""
    def build(min_n: int) -> None:
        rb.person("init", "t")
        rb.person("experiment", "add", "x", "--id", "e1", "--baseline", "fp32", "--candidate", "int8")
        rb.person("spec", "set", "e1", "seed", "--per-run")
        rb.person("claim", "add", "int8 costs at most 0.05", "-e", "e1", "--metric", "change.epe", "--at-most", "0.05",
                  "--min-n", str(min_n), "--id", "c1")
        rb.person("freeze", "e1", "-m", "locked")
    return build


def test_the_same_seed_reported_again_with_an_extra_metric_counts_once(per_run_seed, rb):
    """Finding: 'Repeats count toward --min-n: rb warns 'not an independent run' and counts it anyway; one file under
    seed=1,2,3 counts 3 times with noise 0' (a). seed=1 attached twice (the second time with one extra metric) is one
    run: n stays 1 and --min-n 2 is not met."""
    per_run_seed(2)
    rb.attach("e1", "fp32.epe=5.60", "int8.epe=5.64", "--set", "seed=1")
    env = rb.ok("evidence", "attach", "e1", "fp32.epe=5.60", "int8.epe=5.63", "latency_ms=1", "--set", "seed=1")
    assert "not an independent run" in json.dumps(env.model_dump())
    v = rb.verdict()
    assert v["n"] == 1 and not v["established"] and "too_few_runs" in v["not_established_because"]
    assert rb.gate("unestablished") == 1


def test_the_same_seed_reported_again_counts_once(per_run_seed, rb):
    """Finding: 'Repeats count toward --min-n: rb warns 'not an independent run' and counts it anyway; one file under
    seed=1,2,3 counts 3 times with noise 0' (a, with the same metrics). The repeat is not counted."""
    per_run_seed(2)
    rb.attach("e1", "fp32.epe=5.60", "int8.epe=5.64", "--set", "seed=1")
    rb.attach("e1", "fp32.epe=5.60", "int8.epe=5.63", "--set", "seed=1")
    v = rb.verdict()
    assert v["n"] == 1 and "too_few_runs" in v["not_established_because"]


def test_one_file_relabelled_with_three_seeds_counts_once(per_run_seed, rb):
    """Finding: 'Repeats count toward --min-n: rb warns 'not an independent run' and counts it anyway; one file under
    seed=1,2,3 counts 3 times with noise 0' (b). Identical numbers count once whatever per-run values they are
    labelled with: n is 1, the other two are not counted as 'the same numbers as' the first, and --min-n 3 is not met."""
    per_run_seed(3)
    Path("m.json").write_text(json.dumps({"fp32": {"epe": 5.60}, "int8": {"epe": 5.6499}}), encoding="utf-8")
    first = rb.attach("e1", "--from", "m.json", "--set", "seed=1")
    rb.attach("e1", "--from", "m.json", "--set", "seed=2")
    rb.attach("e1", "--from", "m.json", "--set", "seed=3")
    v = rb.verdict()
    assert v["n"] == 1 and not v["established"] and "too_few_runs" in v["not_established_because"]
    repeats = [o for o in v["observations"] if o["role"] == "not_counted"]
    assert len(repeats) == 2 and all(f"the same numbers as {first}" in o["reason"] for o in repeats)


def test_evidence_must_give_every_declared_per_run_value(per_run_seed, rb):
    """Finding: 'Repeats count toward --min-n: rb warns 'not an independent run' and counts it anyway; one file under
    seed=1,2,3 counts 3 times with noise 0'. Without its seed rb cannot tell a new run from the same one again, so
    evidence that leaves out a declared per-run value is refused."""
    per_run_seed(2)
    before = rb.log
    rb.refused("E_OBJECT_INVALID", "evidence", "attach", "e1", "fp32.epe=5.60", "int8.epe=5.64")
    assert rb.log == before


# ================================================================ asserted decisions (findings 7 and 13)


@pytest.fixture
def refuted_undecided(rb) -> None:
    """h1 is tested by e1; a person froze c1 (c.loss <= 1); the agent attached 1.5, so c1 is refuted and undecided."""
    rb.person("init", "t")
    rb.person("hypothesis", "add", "c is good", "--id", "h1")
    rb.person("assumption", "add", "the eval set is clean", "--id", "a1")
    rb.person("question", "add", "is c good?", "--id", "q1")
    rb.person("experiment", "add", "x", "--id", "e1", "--baseline", "b", "--candidate", "c", "--hypothesis", "h1")
    rb.person("claim", "add", "c at most 1", "-e", "e1", "--metric", "c.loss", "--at-most", "1", "--id", "c1")
    rb.person("freeze", "e1", "-m", "locked")
    rb.attach("e1", "c.loss=1.5")
    assert rb.verdict()["status"] == "refuted" and rb.gate("undecided") == 1


@pytest.mark.parametrize("subject", ["c1", "h1", "a1", "q1"])
def test_an_asserted_decision_is_refused(refuted_undecided, rb, subject):
    """Findings: 'Asserted decisions clear the undecided gate and set hypothesis status, and show unstamped in status'
    and 'Asserted decisions take effect: they clear the undecided gate and set hypotheses accepted and assumptions
    assumed'. RB_ACTOR=human:jh rb decide ... inside the session is E_HUMAN_ONLY: no decision is written, statuses do
    not change, and the undecided gate still fails with the item for the person."""
    before = rb.log
    env = rb.refused("E_HUMAN_ONLY", "decide", subject, "accept", "--why", "x", env=ASSERTED)
    assert IGNORED in env.errors[0].message
    assert rb.log == before
    assert not (rb.root / ".rb" / "decisions").exists() or not list((rb.root / ".rb" / "decisions").glob("*.json"))
    led = rb.ledger()
    assert led.load("hypothesis", "h1").status == "active"
    assert led.load("assumption", "a1").status == "open"
    assert led.load("question", "q1").status == "open"
    code, st = rb("status", "--fail-on", "undecided")
    assert code == 1
    assert any(i["who"] == "person" and ((i["code"] == "undecided" and i["subject"] == "c1") or (i["code"] == "request" and "decide c1" in i["what"]))
               for i in st.data["open"])      # the refused decision is queued for the person, in place of the undecided item
    assert rb.verdict()["decision"] is None


def test_the_persons_decision_clears_the_undecided_gate(refuted_undecided, rb):
    """Finding: 'Asserted decisions take effect: they clear the undecided gate and set hypotheses accepted and
    assumptions assumed'. The control: the person's own decisions do take effect."""
    rb.person("decide", "c1", "accept", "--why", "within budget after all")
    rb.person("decide", "h1", "accept", "--why", "x")
    assert rb.gate("undecided") == 0
    assert rb.ledger().load("hypothesis", "h1").status == "accepted"


# ================================================================ a forged freeze laundered by an allowed write (finding 9)


@pytest.fixture
def forged_freeze(rb) -> Path:
    """The agent wrote the experiment and the claim, then hand-added a person's freeze to e.json. Returns e.json."""
    rb.person("init", "t")
    Path("train.yaml").write_text("lr: 0.0001\n", encoding="utf-8")
    rb.ok("experiment", "add", "x", "--id", "e", "--baseline", "fp32", "--candidate", "int8")
    rb.ok("spec", "set", "e", "lr", "--source", "train.yaml#lr")
    rb.ok("claim", "add", "c", "-e", "e", "--metric", "change.epe", "--at-most", "0.05", "--id", "c1")
    led = rb.ledger()
    path = rb.root / ".rb" / "experiments" / "e.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["frozen"] = {"sha256": led.freeze_sha(led.load("experiment", "e")), "at": now(), "by": "human:t", "why": "go", "after_evidence": []}
    path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    code, env = rb.doctor()
    assert code == 1 and any("differs from what rb wrote" in d for d in failures(env))
    return path


def test_an_allowed_write_does_not_launder_a_forged_freeze(forged_freeze, rb):
    """Finding: 'An agent can forge a person's freeze: a hand edit to the experiment file is logged as rb's own write by
    the next allowed write (rb spec verify)'. rb will not write over a file changed outside rb: rb spec verify is
    E_STATE_EDITED, the file keeps the hand edit, log.jsonl has no write of it, rb doctor still fails, and the claim is
    not established."""
    forged = forged_freeze.read_text(encoding="utf-8")
    before = rb.log
    env = rb.refused("E_STATE_EDITED", "spec", "verify", "e")
    assert "edited outside rb" in env.errors[0].message
    assert rb.log == before and forged_freeze.read_text(encoding="utf-8") == forged
    assert rb.doctor()[0] == 1
    rb.attach("e", "fp32.epe=5.61", "int8.epe=5.64")
    v = rb.verdict()
    assert not v["established"] and "edited_outside_rb" in v["not_established_because"]
    assert rb.gate("unestablished") == 1
    assert not any(json.loads(line).get("op") == "freeze" for line in rb.log.splitlines())


def test_doctor_restore_removes_a_forged_freeze(forged_freeze, rb):
    """Finding: 'An agent can forge a person's freeze: a hand edit to the experiment file is logged as rb's own write by
    the next allowed write (rb spec verify)'. rb doctor --restore puts back rb's last write: the experiment is not
    frozen, and the agent's claim is again waiting for a person to fix its criterion."""
    rb.doctor("--restore")
    assert rb.ledger().load("experiment", "e").frozen is None
    assert rb.doctor()[0] == 0
    rb.attach("e", "fp32.epe=5.61", "int8.epe=5.64")
    assert "criterion_not_fixed_by_person" in rb.verdict()["not_established_because"]


@pytest.mark.parametrize("argv", [
    ("spec", "set", "e", "batch_size", "8", "--no-verify"),
    ("spec", "vary", "e", "precision"),
    ("variant", "add", "e", "int4", "--role", "candidate"),
    ("spec", "verify", "e"),
], ids=["spec-set", "spec-vary", "variant-add", "spec-verify"])
def test_no_write_goes_over_an_experiment_edited_outside_rb(rb, argv):
    """Finding: 'An agent can forge a person's freeze: a hand edit to the experiment file is logged as rb's own write by
    the next allowed write (rb spec verify)'. Whatever the hand edit is, every write that would save the experiment is
    E_STATE_EDITED and leaves both the file and log.jsonl as they were."""
    rb.person("init", "t")
    Path("train.yaml").write_text("lr: 0.0001\n", encoding="utf-8")
    rb.ok("experiment", "add", "x", "--id", "e", "--baseline", "fp32", "--candidate", "int8")
    rb.ok("spec", "set", "e", "lr", "--source", "train.yaml#lr")
    path = rb.root / ".rb" / "experiments" / "e.json"
    edit_json(path, note="edited by hand")
    edited, before = path.read_text(encoding="utf-8"), rb.log
    rb.refused("E_STATE_EDITED", *argv)
    assert path.read_text(encoding="utf-8") == edited and rb.log == before


# ================================================================ MCP spec_set with cited=false (finding 16)


def mcp_calls(*calls: tuple[str, dict]) -> list[dict]:
    server = Server()
    server.handle({"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {"clientInfo": {"name": "T"}}})
    replies = []
    for n, (name, arguments) in enumerate(calls, 1):
        replies.append(server.handle({"jsonrpc": "2.0", "id": n, "method": "tools/call", "params": {"name": name, "arguments": arguments}}))
    return replies


@pytest.mark.parametrize("cited", [False, True], ids=["false", "true"])
def test_mcp_passes_a_boolean_cited_value(cited):
    """Finding: 'MCP spec_set silently drops cited=false, so a claim that should be not_comparable reads 'reproduced ·
    established''. cited is a value option, not a switch: false (and true) reach the CLI as --cited <value>, with the
    cited source after it."""
    argv = argv_for("spec_set", {"experiment": "e1", "name": "use_amp", "source": "run.yaml#use_amp", "cited": cited, "cited_source": "paper.txt"})
    i = argv.index("--cited")
    assert argv[i + 1] == ("true" if cited else "false")
    assert argv[argv.index("--cited-source") + 1] == "paper.txt"
    assert "--no-verify" not in argv and "--optional" not in argv


def test_mcp_spec_set_with_cited_false_makes_the_cited_claim_not_comparable(rb):
    """Finding: 'MCP spec_set silently drops cited=false, so a claim that should be not_comparable reads 'reproduced ·
    established''. Over MCP, use_amp true here and false in the paper is recorded, and the cited claim is not
    comparable, exactly as the CLI's --cited false makes it."""
    Path("paper.txt").write_text("Table 2: EPE of 5.6, trained without AMP.\n", encoding="utf-8")
    Path("run.yaml").write_text("use_amp: true\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-qm", "files")
    rb.person("init", "c")
    rb.person("experiment", "add", "repro", "--id", "e1", "--baseline", "paper", "--candidate", "ours")
    rb.person("claim", "add", "reproduces EPE", "-e", "e1", "--metric", "ours.epe", "--equals", "5.6", "--tolerance", "0.1",
              "--source", "paper.txt", "--quote", "EPE of 5.6", "--id", "c1")
    replies = mcp_calls(
        ("spec_set", {"experiment": "e1", "name": "use_amp", "source": "run.yaml#use_amp", "cited": False, "cited_source": "paper.txt"}),
        ("evidence_attach", {"experiment": "e1", "metrics": {"ours.epe": 5.62}}),
    )
    rb.capsys.readouterr()
    assert all(not r["result"]["isError"] for r in replies), [r["result"]["structuredContent"].get("errors") for r in replies]
    s = rb.ledger().load("experiment", "e1").lookup("use_amp")
    assert s.value is True and s.cited is not None and s.cited.value is False
    assert s.cited.source is not None and s.cited.source.label().startswith("paper.txt")
    v = rb.verdict()
    assert v["status"] == "not_comparable" and not v["established"]
    assert rb.gate("unestablished") == 1
