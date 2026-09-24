"""Regression tests for the adversarial review's major findings and its behavioural minor ones.

Each test names the finding it pins, by its title, in its docstring, and asserts what rb does now that it is fixed. The
state is played in a git repository, inside a Claude Code session (CLAUDECODE=1) as the agent; a person's steps run with
CLAUDECODE removed, so rb names the person from git (human:t).
"""
import inspect
import io
import json
import os
import shlex
import shutil
import subprocess
from contextlib import contextmanager
from pathlib import Path

import pytest

import rabbit_brain as rbsdk
from rabbit_brain import RBError
from rabbit_brain.cli import main
from rabbit_brain.ledger import Ledger
from rabbit_brain.ledger_cli import GATES
from rabbit_brain.mcp import Server, serve
from rabbit_brain.models import Envelope

HAS_GIT = shutil.which("git") is not None
pytestmark = pytest.mark.skipif(not HAS_GIT, reason="played in a git repository")
AGENT_VARS = ("CLAUDECODE", "AI_AGENT", "GITHUB_ACTIONS", "RB_ACTOR")
ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------- the session


def git(*args) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout.strip()


def commit_all(msg: str = "state") -> None:
    git("add", "-A")
    git("commit", "-qm", msg, "--allow-empty")


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
    monkeypatch.setenv("CLAUDECODE", "1")
    return workdir


def rbj(capsys, *argv):
    code = main([*argv, "--json"])
    return code, Envelope.model_validate_json(capsys.readouterr().out)


def ok(capsys, *argv) -> Envelope:
    code, env = rbj(capsys, *argv)
    assert code in (0, 1) and env.ok, (argv, env.errors)
    return env


def refused(capsys, *argv, code: str):
    rc, env = rbj(capsys, *argv)
    assert rc == 2 and not env.ok and env.errors[0].code == code, (argv, rc, env.errors)
    return env.errors[0]


def text(capsys, *argv) -> tuple[int, str]:
    rc = main(list(argv))
    cap = capsys.readouterr()
    return rc, cap.out + cap.err


def as_person(monkeypatch) -> None:
    monkeypatch.delenv("CLAUDECODE", raising=False)


def as_agent(monkeypatch) -> None:
    monkeypatch.setenv("CLAUDECODE", "1")


@contextmanager
def person(monkeypatch):
    as_person(monkeypatch)
    try:
        yield
    finally:
        as_agent(monkeypatch)


def P(capsys, monkeypatch, *argv) -> Envelope:
    """A step a person takes, in their own terminal."""
    with person(monkeypatch):
        return ok(capsys, *argv)


def start(capsys, monkeypatch, *variants: str, exp: str = "e1") -> None:
    """The person starts the state and an experiment (default fp32 vs int8)."""
    variants = variants or ("--baseline", "fp32", "--candidate", "int8")
    P(capsys, monkeypatch, "init", "t")
    P(capsys, monkeypatch, "experiment", "add", "x", "--id", exp, *variants)


def attach(capsys, *argv, exp: str = "e1") -> str:
    env = ok(capsys, "evidence", "attach", exp, *argv)
    return env.data["object"]["id"]


def verdict(capsys, claim: str = "c1") -> dict:
    return ok(capsys, "show", claim).data["verdict"]


def caveat_codes(v: dict) -> set:
    return {c["code"] for c in v["caveats"]}


def observation(v: dict, evidence: str) -> dict:
    return next(o for o in v["observations"] if o["evidence"] == evidence)


def setting(capsys, address: str) -> dict:
    return ok(capsys, "show", address).data["object"]


def open_items(capsys, *argv) -> list:
    return ok(capsys, "status", *argv).data["open"]


def items_for(capsys, subject: str) -> list:
    return [i for i in open_items(capsys) if subject in i["subject"].split(", ")]


def gate(capsys, *gates) -> int:
    return rbj(capsys, "status", "--fail-on", ",".join(gates))[0]


def write(path: str, body: str) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return p


def edit_json(path: Path, fn) -> None:
    doc = json.loads(path.read_text(encoding="utf-8"))
    fn(doc)
    path.write_text(json.dumps(doc, indent=2), encoding="utf-8")


def source_of(do: str) -> str:
    """The --source an open item's command gives, as the shell reads it."""
    argv = shlex.split(do.split("   (", 1)[0])
    return argv[argv.index("--source") + 1]


def run_do(capsys, monkeypatch, do: str, who: str = "agent", **subst) -> tuple[int, Envelope]:
    """Run an open item's command as written: its command part, with its placeholders filled in."""
    cmd = do.split("   (", 1)[0]
    for k, v in subst.items():
        cmd = cmd.replace(k, v)
    argv = shlex.split(cmd)
    assert argv[0] == "rb", do
    if who == "person":
        with person(monkeypatch):
            return rbj(capsys, *argv[1:])
    return rbj(capsys, *argv[1:])


# ================================================================ counting evidence


def test_numbers_seen_before_the_claim_stay_exploratory_when_reattached_with_another_artifact(session, capsys, monkeypatch):
    """Numbers seen before the claim become confirmatory by re-attaching the same results file with any extra --artifact"""
    start(capsys, monkeypatch)
    write("out/m.json", '{"fp32":{"epe":5.60},"int8":{"epe":5.64}}\n')
    write("out/log.txt", "notes\n")
    commit_all()
    seen = attach(capsys, "--from", "out/m.json")
    P(capsys, monkeypatch, "claim", "add", "int8 costs at most 0.05", "-e", "e1", "--metric", "change.epe", "--at-most", "0.05", "--id", "c1")
    P(capsys, monkeypatch, "freeze", "e1", "-m", "locked")
    again = ok(capsys, "evidence", "attach", "e1", "--from", "out/m.json")
    assert again.data["duplicate_of"] == seen
    second = attach(capsys, "--from", "out/m.json", "--artifact", "out/log.txt")
    assert second != seen
    v = verdict(capsys)
    assert observation(v, seen)["role"] == "exploratory"
    o = observation(v, second)
    assert o["role"] == "not_counted" and f"the same numbers as {seen}" in o["reason"]
    assert v["status"] == "untested" and v["n"] == 0 and not v["established"]


def test_a_run_after_the_freeze_is_not_a_repeat_of_an_exploratory_run_with_the_same_seed(session, capsys, monkeypatch):
    """A confirmatory run after the freeze is 'not counted: repeats seed=1' of a pre-freeze exploratory run with different numbers"""
    start(capsys, monkeypatch)
    P(capsys, monkeypatch, "spec", "set", "e1", "seed", "--per-run")
    ok(capsys, "claim", "add", "c", "-e", "e1", "--metric", "change.epe", "--at-most", "0.05", "--id", "c1")   # the agent's claim
    early = attach(capsys, "fp32.epe=5.61", "int8.epe=5.70", "--set", "seed=1")
    P(capsys, monkeypatch, "freeze", "e1", "-m", "pre-register")
    late = attach(capsys, "fp32.epe=5.60", "int8.epe=5.63", "--set", "seed=1")
    v = verdict(capsys)
    assert observation(v, early)["role"] == "exploratory"
    assert observation(v, late)["role"] == "confirmatory"
    assert v["status"] == "supported" and v["n"] == 1 and v["established"]
    # a confirmatory seed=1 is still counted once: the same seed again with other numbers is a repeat of it
    third = attach(capsys, "fp32.epe=5.60", "int8.epe=5.64", "--set", "seed=1")
    o = observation(verdict(capsys), third)
    assert o["role"] == "not_counted" and "repeats seed=1" in o["reason"] and late in o["reason"]


def test_min_n_is_not_met_by_the_same_numbers_attached_again(session, capsys, monkeypatch):
    """--min-n is met by the same numbers attached again with no per-run value, even when the experiment declares one"""
    start(capsys, monkeypatch, "--baseline", "a", "--candidate", "b")
    P(capsys, monkeypatch, "spec", "set", "e1", "seed", "--per-run")
    P(capsys, monkeypatch, "claim", "add", "c", "-e", "e1", "--metric", "change.epe", "--at-most", "0.05", "--min-n", "3",
      "--noise", "0.001", "--id", "c1")
    P(capsys, monkeypatch, "freeze", "e1", "-m", "f")
    for i in (1, 2, 3):
        write(f"log{i}.txt", f"log {i}\n")
    err = refused(capsys, "evidence", "attach", "e1", "a.epe=1", "b.epe=1.02", "--artifact", "log1.txt", code="E_OBJECT_INVALID")
    assert "--set seed=" in err.message
    ids = [attach(capsys, "a.epe=1", "b.epe=1.02", "--set", f"seed={i}", "--artifact", f"log{i}.txt") for i in (1, 2, 3)]
    v = verdict(capsys)
    assert v["n"] == 1 and not v["established"] and "too_few_runs" in v["not_established_because"]
    for later in ids[1:]:
        o = observation(v, later)
        assert o["role"] == "not_counted" and f"the same numbers as {ids[0]}" in o["reason"]


REVIEW = {"version": 1, "project": "p", "baseline": "fp32", "candidate": "int8", "dataset": "d", "metric": "epe", "unit": "px",
          "cases": [{"id": "a", "name": "A", "baseline_error": 2.0, "candidate_error": 2.01},
                    {"id": "b", "name": "B", "baseline_error": 3.0, "candidate_error": 3.01}]}


def test_one_review_run_attached_by_its_id_and_by_its_results_file_counts_once(session, capsys, monkeypatch):
    """--min-n is met by the same numbers attached again with no per-run value, even when the experiment declares one"""
    start(capsys, monkeypatch)
    P(capsys, monkeypatch, "claim", "add", "c", "-e", "e1", "--metric", "change.epe", "--at-most", "0.05", "--min-n", "2", "--id", "c1")
    write("r.json", json.dumps(REVIEW))
    run_id = ok(capsys, "review", "import", "r.json").run_id
    first = attach(capsys, "--run", run_id)
    got = ok(capsys, "evidence", "attach", "e1", "--run", "r.json")
    v = verdict(capsys)
    assert v["n"] == 1 and "too_few_runs" in v["not_established_because"]
    if got.data.get("duplicate_of") is None:
        o = observation(v, got.data["object"]["id"])
        assert o["role"] == "not_counted" and first in o["reason"]


def test_rb_compare_averages_only_counted_evidence(session, capsys, monkeypatch):
    """rb compare averages evidence that does not count: --again repeats and runs whose config disagrees with the spec
    (also: rb compare averages evidence that rb has said will not count)"""
    write("train.yaml", "lr: 0.0001\n")
    write("run2.yaml", "lr: 0.01\n")
    start(capsys, monkeypatch)
    P(capsys, monkeypatch, "metric", "add", "epe", "--minimize")
    P(capsys, monkeypatch, "spec", "set", "e1", "lr", "--source", "train.yaml#lr")
    P(capsys, monkeypatch, "spec", "set", "e1", "seed", "--per-run")
    P(capsys, monkeypatch, "claim", "add", "c", "-e", "e1", "--metric", "change.epe", "--at-most", "0.05", "--id", "c1")
    attach(capsys, "fp32.epe=5.00", "int8.epe=5.02", "--set", "seed=1")
    attach(capsys, "fp32.epe=5.00", "int8.epe=5.02", "--set", "seed=1", "--again", "-m", "rerun")
    attach(capsys, "fp32.epe=5.00", "int8.epe=9.00", "--set", "seed=2", "--config", "run2.yaml")
    v = verdict(capsys)
    assert v["n"] == 1 and v["mean"] == pytest.approx(0.02)
    t = ok(capsys, "compare", "e1").data
    row = next(r for r in t["rows"] if r["variant"] == "int8")
    assert row["metrics"]["epe"]["n"] == 1 and row["metrics"]["epe"]["mean"] == pytest.approx(5.02)
    assert row["metrics"]["epe"]["delta"] == pytest.approx(0.02) and t["evidence_count"] == 1


def test_reattaching_the_same_numbers_with_a_corrected_config_is_not_a_duplicate(session, capsys, monkeypatch):
    """Re-attaching the same numbers with a corrected --config is refused as a duplicate of evidence that does not count"""
    write("train.yaml", "lr: 0.0001\n")
    write("bad.yaml", "lr: 0.001\n")
    write("good.yaml", "lr: 0.0001\n")
    start(capsys, monkeypatch, "--baseline", "a", "--candidate", "b")
    P(capsys, monkeypatch, "spec", "set", "e1", "lr", "--source", "train.yaml#lr")
    P(capsys, monkeypatch, "claim", "add", "c", "-e", "e1", "--metric", "b.epe", "--at-most", "6", "--id", "c1")
    bad = attach(capsys, "b.epe=5", "--config", "bad.yaml")
    env = ok(capsys, "evidence", "attach", "e1", "b.epe=5", "--config", "good.yaml")
    assert env.data.get("duplicate_of") is None
    good = env.data["object"]["id"]
    v = verdict(capsys)
    assert observation(v, bad)["role"] == "not_counted" and observation(v, good)["role"] == "confirmatory"
    assert v["status"] == "supported"


def test_freezing_after_a_persons_claim_and_its_runs_keeps_it_established(session, capsys, monkeypatch):
    """Freezing after the runs silently turns an established claim into untested, and the message says the evidence 'stays' exploratory"""
    start(capsys, monkeypatch, "--baseline", "a", "--candidate", "b")
    P(capsys, monkeypatch, "claim", "add", "c", "-e", "e1", "--metric", "change.epe", "--at-most", "0.05", "--id", "c1")
    ev = attach(capsys, "a.epe=1", "b.epe=1.02")
    assert verdict(capsys)["established"]
    with person(monkeypatch):
        rc, out = text(capsys, "freeze", "e1", "-m", "late")
    assert rc == 0 and "stays exploratory" not in out
    v = verdict(capsys)
    assert v["established"] and observation(v, ev)["role"] == "confirmatory"


# ================================================================ reading a metric


def test_a_literal_change_that_disagrees_with_the_variants_is_not_read(session, capsys, monkeypatch):
    """change.<m> is read from a literal change key when evidence has one, even when the variants' own numbers contradict it"""
    start(capsys, monkeypatch)
    P(capsys, monkeypatch, "claim", "add", "c", "-e", "e1", "--metric", "change.epe", "--at-most", "0.05", "--id", "c1")
    P(capsys, monkeypatch, "freeze", "e1", "-m", "locked")
    write("out/m.json", '{"fp32":{"epe":5.60},"int8":{"epe":5.90},"change":{"epe":0.01}}\n')
    ev = attach(capsys, "--from", "out/m.json")
    v = verdict(capsys)
    assert v["status"] == "untested" and not v["established"] and not v["observations"]
    assert any(c["code"] == "not_read" and c["subject"] == ev and "0.01" in c["text"] for c in v["caveats"])
    # with only the variants' numbers, change.epe is computed from them
    ok_ev = attach(capsys, "fp32.epe=5.60", "int8.epe=5.62")
    assert observation(verdict(capsys), ok_ev)["value"] == pytest.approx(0.02)


def test_the_borderline_check_uses_the_runs_own_spread_whatever_noise_the_claim_states(session, capsys, monkeypatch):
    """An agent's --noise 0 switches off the borderline check against the runs' own spread, and noise/min-n are shown nowhere a person reads before freezing"""
    start(capsys, monkeypatch)
    P(capsys, monkeypatch, "spec", "set", "e1", "seed", "--per-run")
    rc, out = text(capsys, "claim", "add", "int8 costs at most 0.05", "-e", "e1", "--metric", "change.epe", "--at-most", "0.05",
                   "--noise", "0", "--id", "c1")
    assert rc == 0 and "change.epe ≤ 0.05 (noise 0)" in out
    with person(monkeypatch):
        rc, out = text(capsys, "freeze", "e1", "-m", "looks fine")
    assert rc == 0 and "change.epe ≤ 0.05 (noise 0)" in out
    for seed, int8 in ((1, "5.60"), (2, "5.649"), (3, "5.40")):
        attach(capsys, "fp32.epe=5.6", f"int8.epe={int8}", "--set", f"seed={seed}")
    v = verdict(capsys)
    assert v["n"] == 3 and v["status"] == "supported" and not v["established"]
    assert "borderline" in v["not_established_because"]
    assert v["criterion"] == "change.epe ≤ 0.05 (noise 0)"
    P(capsys, monkeypatch, "claim", "add", "c2", "-e", "e1", "--metric", "change.epe", "--at-most", "0.05", "--min-n", "3",
      "--noise", "0.01", "--id", "c2", "--amend", "--why", "a second criterion")
    assert verdict(capsys, "c2")["criterion"] == "change.epe ≤ 0.05 (n ≥ 3, noise 0.01)"


# ================================================================ sources must name the setting, and state it


def test_rbs_own_state_file_cannot_be_a_source(session, capsys, monkeypatch):
    """A setting can be 'verified' against rb's own state file (.rb/experiments/<id>.json): circular, any value"""
    start(capsys, monkeypatch)
    for name, value, src in (("cuda", "99.9", ".rb/experiments/e1.json#/settings/0/value"),
                             ("dataset", "whatever-i-say", "run:.rb/experiments/e1.json#/settings/1/value")):
        code, env = rbj(capsys, "spec", "set", "e1", name, value, "--source", src)
        assert code in (1, 2), (name, env)
        if code == 1:
            assert env.data["object"]["status"] != "verified"
            assert env.data["outcome"]["failures"][0]["code"] == "E_SOURCE_OUTSIDE"
        else:
            assert env.errors[0].code == "E_SOURCE_OUTSIDE"
    rbj(capsys, "spec", "verify", "e1")
    shown = ok(capsys, "show", "e1").data["object"]
    assert all(s["status"] != "verified" for s in shown["settings"])


@pytest.mark.parametrize("name, value, extra", [
    ("lr", "0.0001", ["--source", "configs/train.yaml#optim.weight_decay"]),      # weight_decay's key
    ("batch_size", "90", ["--source", "paper.txt", "--quote", "90"]),             # 90 epochs
    ("dropout", "16", ["--source", "paper.txt", "--quote", "16"]),                # the batch size
    ("weight_decay", "100", ["--source", "paper.txt", "--quote", "for 100 epochs"]),
    ("lr", "7", ["--source", "run:runs/r1#/seeds/torch"]),                     # the run's seed
], ids=["key-of-another-setting", "quote-90-epochs", "quote-batch-size", "quote-epochs", "run-pointer-of-another-setting"])
def test_a_key_or_quote_for_another_setting_does_not_verify(session, capsys, monkeypatch, name, value, extra):
    """The 'setting must be named' rule applies only to file:LINE: file#key, --quote and --term verify a value stated for a different setting
    (also: With --quote, a setting verifies even when neither its name nor --term appears)"""
    write("configs/train.yaml", "optim:\n  lr: 0.01\n  weight_decay: 0.0001\n")
    write("paper.txt", "We train for 90 epochs with batch size 256 and a learning rate of 3e-4 for 100 epochs.\n"
                       "The batch size is 16.\n")
    write("runs/r1/record.json", '{"seeds": {"torch": 7}, "lr": 0.1}\n')
    commit_all()
    start(capsys, monkeypatch)
    rbj(capsys, "spec", "set", "e1", name, value, *extra)
    assert setting(capsys, f"e1/{name}")["status"] != "verified"


def test_a_term_that_is_not_a_word_for_the_setting_does_not_verify(session, capsys, monkeypatch):
    """The 'setting must be named' rule applies only to file:LINE: file#key, --quote and --term verify a value stated for a different setting"""
    write("paper.txt", "We train for 90 epochs with batch size 256.\nThe learning rate is 0.01 and weight decay 0.0001 for all runs\n")
    commit_all()
    start(capsys, monkeypatch)
    rbj(capsys, "spec", "set", "e1", "wd", "0.01", "--source", "paper.txt:2", "--term", "The")   # 0.01 is the learning rate
    assert setting(capsys, "e1/wd")["status"] != "verified"


@pytest.mark.parametrize("line, name, value, term", [
    ("The learning rate is 0.0001.", "lr", "0.0001", "learning rate"),
    ("We train with a learning rate of 0.0004.", "lr", "0.0004", "learning rate"),
    ("Weight decay is 1e-4.", "weight_decay", "1e-4", "weight decay"),
])
def test_a_decimal_at_the_end_of_a_sentence_is_read(session, capsys, monkeypatch, line, name, value, term):
    """A decimal at the end of a sentence ('0.0001.') never verifies and is recorded as a CONFLICT that nothing can clear
    (also: A decimal at the end of a sentence is not read, so rb records a false CONFLICT that re-verifying cannot clear)"""
    write("paper.txt", line + "\n")
    commit_all()
    start(capsys, monkeypatch)
    code, env = rbj(capsys, "spec", "set", "e1", name, value, "--source", "paper.txt:1", "--term", term)
    s = env.data["object"]
    assert code == 0 and s["status"] == "verified" and s["conflict"] is None
    code, env = rbj(capsys, "spec", "set", "e1", name + "_q", value, "--source", "paper.txt", "--quote", line, "--term", term)
    assert code == 0 and env.data["object"]["status"] == "verified"


def test_text_in_a_config_that_looks_like_a_number_stays_text(session, capsys, monkeypatch):
    """Text in a config that looks like a number is read as a number: "11.10" becomes 11.1, "0012" becomes 12, "1e3" becomes 1000.0, and a later change is not caught"""
    write("env.json", '{"cuda": "11.10", "ckpt": "0012", "tag": "1e3"}\n')
    commit_all()
    start(capsys, monkeypatch)
    ok(capsys, "spec", "set", "e1", "cuda", "--source", "env.json#cuda")
    ok(capsys, "spec", "set", "e1", "--from", "env.json", "--keys", "ckpt,tag")
    got = {n: setting(capsys, f"e1/{n}") for n in ("cuda", "ckpt", "tag")}
    assert {n: s["value"] for n, s in got.items()} == {"cuda": "11.10", "ckpt": "0012", "tag": "1e3"}
    assert all(s["status"] == "verified" for s in got.values())
    write("env.json", '{"cuda": "11.1", "ckpt": "12", "tag": "1000"}\n')
    code, env = rbj(capsys, "spec", "verify", "e1")
    assert code == 1 and len(env.data["outcome"]["failures"]) == 3
    assert all(setting(capsys, f"e1/{n}")["status"] != "verified" for n in ("cuda", "ckpt", "tag"))


def test_a_yaml_list_in_scientific_notation_is_numbers_so_a_resolved_config_matches(session, capsys, monkeypatch):
    """--from stores YAML list elements written in scientific notation as text, so a run whose config lists the same numbers does not count"""
    write("cfg.yaml", "optim:\n  lr: 1e-4\n  lrs: [1e-4, 1e-3]\n")
    write("hydra_config.yaml", "optim:\n  lr: 0.0001\n  lrs:\n  - 0.0001\n  - 0.001\n")
    start(capsys, monkeypatch, "--baseline", "a", "--candidate", "b")
    P(capsys, monkeypatch, "spec", "set", "e1", "--from", "cfg.yaml", "--keys", "optim.*")
    assert setting(capsys, "e1/optim.lrs")["value"] == [0.0001, 0.001]
    P(capsys, monkeypatch, "claim", "add", "c", "-e", "e1", "--metric", "b.epe", "--at-most", "6", "--id", "c1")
    rc, out = text(capsys, "evidence", "attach", "e1", "b.epe=5", "--config", "hydra_config.yaml")
    assert rc == 0 and "Ran with a different spec" not in out and "Ran with the spec" in out
    v = verdict(capsys)
    assert v["status"] == "supported" and [o["role"] for o in v["observations"]] == ["confirmatory"]


@pytest.mark.parametrize("lineno, name, wrong, right", [
    (2, "lr", "3e-4", "1e-4"),
    (3, "batch_size", "16", "8"),
    (4, "lr2", "0.0004", "0.001"),
])
def test_a_number_in_a_trailing_comment_does_not_verify(session, capsys, monkeypatch, lineno, name, wrong, right):
    """A number in a trailing comment verifies a setting: 'lr = 1e-4  # the paper used 3e-4' verifies lr = 3e-4
    (also: A trailing comment or help text verifies a value the line does not set)"""
    write("train.py", "import torch\nlr = 1e-4  # the paper used 3e-4\nbatch_size = 8  # 16 did not fit\n"
                      'p.add_argument("--lr2", type=float, default=0.001)  # the paper used 0.0004\n')
    commit_all()
    start(capsys, monkeypatch)
    code, env = rbj(capsys, "spec", "set", "e1", name, wrong, "--source", f"train.py:{lineno}")
    assert code == 1 and env.data["object"]["status"] == "provisional"
    code, env = rbj(capsys, "spec", "set", "e1", name, right, "--source", f"train.py:{lineno}")
    assert code == 0 and env.data["object"]["status"] == "verified"


def test_a_number_in_help_text_does_not_verify(session, capsys, monkeypatch):
    """A trailing comment or help text verifies a value the line does not set"""
    write("train.py", 'p.add_argument("--wd", type=float, default=0.01, help="try 0.05 for large models")\n')
    commit_all()
    start(capsys, monkeypatch)
    rbj(capsys, "spec", "set", "e1", "wd", "0.05", "--source", "train.py:1")      # the line sets 0.01
    assert setting(capsys, "e1/wd")["status"] != "verified"


def test_a_yaml_date_is_text_and_spec_set_from_never_half_writes(session, capsys, monkeypatch):
    """--from aborts partway on a YAML date and leaves the earlier keys written; the error names no key"""
    write("cfg.yaml", "optim:\n  lr: 1e-4\ndata:\n  batch_size: 8\n  snapshot: 2024-01-15\n  empty:\n  tags: []\n")
    start(capsys, monkeypatch, "--baseline", "a", "--candidate", "b")
    code, env = rbj(capsys, "spec", "set", "e1", "--from", "cfg.yaml", "--keys", "optim.*,data.*")
    assert code == 0 and env.ok
    snap = setting(capsys, "e1/data.snapshot")
    assert snap["value"] == "2024-01-15" and snap["status"] == "verified"
    assert setting(capsys, "e1/optim.lr")["value"] == 0.0001
    assert {s["name"] for s in ok(capsys, "show", "e1").data["object"]["settings"]} == {"optim.lr", "data.batch_size", "data.snapshot"}
    warned = " ".join(env.data.get("warnings") or [])
    assert "data.empty" in warned and "data.tags" in warned


# ================================================================ rejections, vouches and hints


def test_a_rejection_is_of_the_value_a_person_saw(session, capsys, monkeypatch):
    """A rejected setting stays rejected for any later value; status says the new value was rejected and suggests an agent command that cannot clear it
    (also: After a person rejects a setting, the agent's hint (set it again) never clears the rejection, and rb then says the new value was rejected)"""
    write("env.yaml", "cuda: 12.2\n")
    write("old.yaml", "cuda: 12.1\n")
    commit_all()
    start(capsys, monkeypatch, "--baseline", "a", "--candidate", "b")
    P(capsys, monkeypatch, "spec", "set", "e1", "cuda", "12.1")
    P(capsys, monkeypatch, "claim", "add", "c", "-e", "e1", "--metric", "b.epe", "--at-most", "6", "--id", "c1")
    P(capsys, monkeypatch, "decide", "e1/cuda", "reject", "-m", "not what the cluster runs")
    hint = next(i for i in items_for(capsys, "e1/cuda") if i["code"] == "rejected_setting")
    assert hint["who"] == "agent"
    code, env = run_do(capsys, monkeypatch, hint["do"], **{"<value>": "12.2", "<file#key>": "env.yaml#cuda"})
    assert code == 0 and env.data["object"]["status"] == "verified"
    attach(capsys, "b.epe=5")
    v = verdict(capsys)
    assert "rejected_setting" not in caveat_codes(v) and v["established"]
    ok(capsys, "spec", "set", "e1", "cuda", "12.1", "--source", "old.yaml#cuda")      # back to the value the person rejected
    v = verdict(capsys)
    rej = [c for c in v["caveats"] if c["code"] == "rejected_setting"]
    assert rej and rej[0]["blocks"] and "cuda = 12.1 was rejected" in rej[0]["text"]


def test_on_a_frozen_experiment_the_agents_list_holds_no_command_that_fails_with_e_frozen(session, capsys, monkeypatch):
    """On a frozen experiment, 'Agent can do' lists commands that fail with E_FROZEN
    (also: 'Agent can do' offers person-only commands on a frozen experiment (unknown setting, unclaimed metric))"""
    write("c.yaml", "depth: 12\n")
    commit_all()
    start(capsys, monkeypatch, "--baseline", "a", "--candidate", "b")
    P(capsys, monkeypatch, "spec", "set", "e1", "depth", "--unknown")
    P(capsys, monkeypatch, "spec", "set", "e1", "cuda", "12.1")
    P(capsys, monkeypatch, "decide", "e1/cuda", "reject", "-m", "wrong image")
    P(capsys, monkeypatch, "claim", "add", "c", "-e", "e1", "--metric", "change.epe", "--at-most", "1", "--id", "c1")
    P(capsys, monkeypatch, "freeze", "e1", "-m", "pre-register")
    env = ok(capsys, "evidence", "attach", "e1", "change.epe=0.5", "latency_ms=20")
    assert not [n for n in env.next if n.startswith(("rb claim add", "rb spec set")) and "--amend" not in n]
    items = open_items(capsys)
    for code, subject in (("unknown", "e1/depth"), ("rejected_setting", "e1/cuda"), ("unclaimed_metric", "e1")):
        it = next(i for i in items if i["code"] == code and i["subject"] == subject)
        assert it["who"] == "person" and '--amend --why "..."' in it["do"], it
    for it in items:
        if it["who"] != "person":
            assert not it["do"].startswith(("rb spec set e1", "rb claim add", "rb spec vary e1", "rb variant add e1")), it
    unknown = next(i for i in items if i["code"] == "unknown")
    code, _ = run_do(capsys, monkeypatch, unknown["do"], **{"<value>": "12", "<file#key>": "c.yaml#depth", '"..."': "found-it"})
    assert code == 2                                          # the agent cannot: it is a person's amendment
    code, env = run_do(capsys, monkeypatch, unknown["do"], who="person", **{"<value>": "12", "<file#key>": "c.yaml#depth", '"..."': "found-it"})
    assert code == 0 and env.data["object"]["status"] == "verified"


def test_the_stale_and_conflict_fixes_keep_the_source_and_lead_back_to_verified(session, capsys, monkeypatch):
    """The status hints for a stale or conflicting source do not lead back to verified
    (also: The conflict fix `rb spec set <exp> <name> <the value the source states>` drops the source and leaves the setting provisional, so it still blocks)"""
    write("train.py", 'p.add_argument("--wd", default=0.01)\n')
    write("cfg.yaml", "lr: 0.001\n")
    write("train.yaml", "optim:\n  lr: 0.0001\n")
    commit_all()
    start(capsys, monkeypatch, "--baseline", "a", "--candidate", "b")
    ok(capsys, "spec", "set", "e1", "wd", "0.01", "--source", "train.py:1")
    ok(capsys, "spec", "set", "e1", "lr", "--source", "cfg.yaml#lr")
    ok(capsys, "spec", "set", "e1", "--from", "train.yaml", "--keys", "optim.*")
    write("train.py", 'p.add_argument("--decay", default=0.01)\n')
    write("cfg.yaml", "lr: 0.002\n")
    write("train.yaml", "optim:\n  lr: 0.0002\n")
    commit_all("changed")
    items = open_items(capsys)
    stale = next(i for i in items if i["subject"] == "e1/wd")
    assert stale["code"] == "stale" and not stale["do"].startswith("rb spec verify") and source_of(stale["do"]) == "train.py:1"
    for subject, src, value in (("e1/lr", "cfg.yaml#lr", "0.002"), ("e1/optim.lr", "train.yaml#optim.lr", "0.0002")):
        conflict = next(i for i in items if i["subject"] == subject)
        assert conflict["code"] == "conflict" and source_of(conflict["do"]) == src, conflict
        code, env = run_do(capsys, monkeypatch, conflict["do"], **{"<the value the source states>": value})
        assert code == 0 and env.data["object"]["status"] == "verified" and env.data["object"]["source"] is not None
        assert not [i for i in open_items(capsys) if i["subject"] == subject]


def test_a_failed_verify_is_not_answered_with_the_same_verify_again(session, capsys, monkeypatch):
    """The stale fix `rb spec verify` cannot succeed once the key is gone, and status then gives the same command again"""
    write("train.yaml", "optim:\n  lr: 0.0001\n  wd: 0.01\n")
    commit_all()
    start(capsys, monkeypatch, "--baseline", "a", "--candidate", "b")
    ok(capsys, "spec", "set", "e1", "--from", "train.yaml", "--keys", "optim.*")
    write("train.yaml", "optim:\n  lr: 0.0001\n")
    commit_all("drop wd")
    stale = next(i for i in items_for(capsys, "e1/optim.wd"))
    assert stale["code"] == "stale" and not stale["do"].startswith("rb spec verify")
    assert rbj(capsys, "spec", "verify", "e1", "optim.wd")[0] == 1
    after = items_for(capsys, "e1/optim.wd")
    assert after and all(i["do"] != "rb spec verify e1 optim.wd" for i in after), after


def test_a_provisional_settings_command_quotes_its_value(session, capsys, monkeypatch):
    """The provisional-setting 'do' command leaves the value unquoted, so it fails for values with spaces or lists"""
    write("env.yaml", "cuda: 12.1 with cudnn 8\ntags: [a b, c]\n")
    commit_all()
    start(capsys, monkeypatch, "--baseline", "a", "--candidate", "b")
    ok(capsys, "spec", "set", "e1", "cuda", "12.1 with cudnn 8", "--source", "note:cluster")
    ok(capsys, "spec", "set", "e1", "tags", '["a b", "c"]', "--source", "note:tags")
    for name in ("cuda", "tags"):
        it = next(i for i in items_for(capsys, f"e1/{name}") if i["code"] == "provisional")
        code, env = run_do(capsys, monkeypatch, it["do"], **{"<file#key that states it>": f"env.yaml#{name}"})
        assert code == 0, (it["do"], env.errors)
        assert env.data["object"]["status"] == "verified"


def test_the_person_is_given_a_command_that_readopts_a_claim_written_after_the_freeze(session, capsys, monkeypatch):
    """'Needs a person' tells the person `rb freeze <exp>` for a claim written after the freeze, and that command fails with 'already frozen'"""
    start(capsys, monkeypatch, "--baseline", "a", "--candidate", "b")
    P(capsys, monkeypatch, "experiment", "add", "y", "--id", "e2", "--baseline", "a", "--candidate", "b")
    P(capsys, monkeypatch, "freeze", "e1", "-m", "pre-register")
    ok(capsys, "claim", "add", "late claim", "-e", "e2", "--metric", "change.x", "--at-most", "1", "--id", "c0")
    claims = Path(".rb/claims")
    edit = json.loads((claims / "c0.json").read_text())
    edit.update(id="c1", experiment="e1")
    (claims / "c1.json").write_text(json.dumps(edit, indent=2))
    P(capsys, monkeypatch, "doctor", "--adopt", "--why", "the claim belongs on e1")
    it = next(i for i in items_for(capsys, "c1") if i["code"] == "criterion_not_fixed_by_person")
    assert it["who"] == "person" and "--amend" in it["do"]
    code, _ = run_do(capsys, monkeypatch, it["do"], who="person", **{'"..."': "re-adopt"})
    assert code == 0
    assert not [i for i in open_items(capsys) if i["code"] in ("criterion_not_fixed_by_person", "drift") and i["subject"] in ("c1", "e1")]
    assert "criterion_not_fixed_by_person" not in verdict(capsys)["not_established_because"]


def test_a_decision_is_outdated_whenever_the_verdict_it_was_made_on_changes(session, capsys, monkeypatch):
    """A decision made on 'supported' stays shown as 'decided: accept' after the claim falls to 'untested'; no decision_outdated item, and --fail-on undecided passes"""
    start(capsys, monkeypatch, "--baseline", "a", "--candidate", "b")
    P(capsys, monkeypatch, "claim", "add", "x at most 1", "-e", "e1", "--metric", "change.x", "--at-most", "1", "--id", "c1")
    P(capsys, monkeypatch, "evidence", "attach", "e1", "change.x=0.5")
    P(capsys, monkeypatch, "decide", "c1", "accept", "-m", "ship it")
    assert gate(capsys, "undecided") == 0
    ok(capsys, "spec", "set", "e1", "batch", "16", "--source", "note:launcher")
    assert verdict(capsys)["status"] == "untested"
    it = [i for i in items_for(capsys, "c1") if i["code"] == "decision_outdated"]
    assert it and it[0]["who"] == "person" and "supported" in it[0]["what"]
    assert gate(capsys, "undecided") == 1


def test_a_changed_cited_source_is_an_open_item_and_fails_the_stale_gate(session, capsys, monkeypatch):
    """A changed cited source blocks the claim, but rb status lists no action and --fail-on stale passes"""
    write("paper.txt", "Table 3: RAFT reaches an EPE of 5.64 on KITTI.\n")
    start(capsys, monkeypatch)
    P(capsys, monkeypatch, "claim", "add", "r", "-e", "e1", "--metric", "int8.epe", "--equals", "5.64", "--tolerance", "0.05",
      "--source", "paper.txt", "--quote", "EPE of 5.64", "--id", "c1")
    attach(capsys, "int8.epe=5.66")
    assert gate(capsys, "stale") == 0
    write("paper.txt", "Table 3: RAFT reaches an EPE of 5.94 on KITTI.\n")
    assert "cited_source_changed" in verdict(capsys)["not_established_because"]
    assert [i for i in items_for(capsys, "c1") if i["code"] == "cited_source_changed"]
    assert gate(capsys, "stale") == 1


def change_claim(capsys) -> Envelope:
    ok(capsys, "init", "x")
    ok(capsys, "experiment", "add", "t", "--id", "int8", "--baseline", "fp32", "--candidate", "int8")
    ok(capsys, "spec", "set", "int8", "seed", "--per-run")
    return ok(capsys, "claim", "add", "c", "-e", "int8", "--metric", "change.epe", "--at-most", "0.05", "--id", "c1")


def assert_attach_hint(h: str) -> None:
    assert h.startswith("rb evidence attach int8 "), h
    assert "change.epe=" not in h and "fp32.epe=<value>" in h and "int8.epe=<value>" in h and "--set seed=<value>" in h, h


def test_status_hints_ask_for_the_variants_numbers_not_a_change_typed_by_the_agent(session, capsys):
    """Hints tell the agent to type change.epe=<value> itself, against the manual's 'do not compute regressions yourself'"""
    change_claim(capsys)
    hints = [i["do"] for i in open_items(capsys) if i["subject"] == "c1" and i["do"].startswith("rb evidence attach")]
    assert hints
    for h in hints:
        assert_attach_hint(h)


def test_the_claim_add_next_hint_asks_for_the_variants_numbers_not_a_change_typed_by_the_agent(session, capsys):
    """Hints tell the agent to type change.epe=<value> itself, against the manual's 'do not compute regressions yourself'"""
    env = change_claim(capsys)
    hints = [h for h in env.next if h.startswith("rb evidence attach")]
    assert hints
    for h in hints:
        assert_attach_hint(h)


def test_grouped_agent_items_print_each_ones_own_command(session, capsys):
    """Grouped 'Agent can do' lines print only the first subject's command, which does not apply to the rest"""
    ok(capsys, "init", "g")
    ok(capsys, "experiment", "add", "e", "--id", "x", "--baseline", "a", "--candidate", "b")
    for n in ("s1", "s2", "s3"):
        ok(capsys, "spec", "set", "x", n, "1", "--source", "note:n")
    write("k.yaml", "k: 1\n")
    ok(capsys, "spec", "set", "x", "k", "1", "--source", "k.yaml#k", "--no-verify")
    items = [i for i in open_items(capsys) if i["code"] == "provisional"]
    assert len(items) == 4
    rc, out = text(capsys, "status")
    agent = out.split("Agent can do", 1)[1].split("\nClaims", 1)[0].split("\nExperiments", 1)[0]
    for it in items:
        assert it["do"] in agent, it["do"]


# ================================================================ retracting


def test_an_agent_cannot_retract_what_a_person_wrote(session, capsys, monkeypatch):
    """An agent can retract a claim a person wrote (refuted, not frozen), and the gate passes with 'Claims: none yet'
    (also: An agent can retract a claim a person wrote, which hides a refutation from the CI gate)"""
    start(capsys, monkeypatch)
    P(capsys, monkeypatch, "claim", "add", "INT8 costs at most 0.05", "-e", "e1", "--metric", "change.epe", "--at-most", "0.05", "--id", "c1")
    P(capsys, monkeypatch, "hypothesis", "add", "h", "--id", "h1")
    P(capsys, monkeypatch, "question", "add", "q", "--id", "q1")
    attach(capsys, "fp32.epe=5.61", "int8.epe=5.90")
    assert gate(capsys, "refuted") == 1
    for subject in ("c1", "h1", "q1"):
        err = refused(capsys, "retract", subject, "-m", "not relevant", code="E_HUMAN_ONLY")
        assert err.handoff["command"] == shlex.join(["rb", "retract", subject, "-m", "not relevant"])
    assert gate(capsys, "refuted") == 1 and gate(capsys, "refuted", "unestablished") == 1
    # what the agent wrote itself, it may take back while nothing rests on it
    ok(capsys, "claim", "add", "mine", "-e", "e1", "--metric", "int8.epe", "--at-most", "9", "--id", "c2")
    ok(capsys, "retract", "c2", "-m", "a typo")


def test_retracting_an_experiment_blocks_its_claims_and_refuses_writes(session, capsys, monkeypatch):
    """Retracting an experiment leaves its claims established, and it keeps accepting evidence, settings and claims"""
    start(capsys, monkeypatch)
    P(capsys, monkeypatch, "claim", "add", "c", "-e", "e1", "--metric", "int8.epe", "--at-most", "6", "--id", "c1")
    attach(capsys, "int8.epe=5")
    assert verdict(capsys)["established"]
    assert refused(capsys, "retract", "e1", "-m", "wrong setup", code="E_HUMAN_ONLY")
    P(capsys, monkeypatch, "retract", "e1", "-m", "wrong setup")
    v = verdict(capsys)
    assert not v["established"] and "experiment_retracted" in v["not_established_because"]
    assert gate(capsys, "unestablished") == 1
    refused(capsys, "evidence", "attach", "e1", "int8.epe=4", code="E_OBJECT_INVALID")
    refused(capsys, "spec", "set", "e1", "lr", "0.1", "--source", "note:x", code="E_OBJECT_INVALID")
    refused(capsys, "claim", "add", "c3", "-e", "e1", "--metric", "int8.epe", "--at-most", "6", code="E_OBJECT_INVALID")
    assert len(list(Path(".rb/evidence").glob("*.json"))) == 1


def test_a_person_retracting_a_claim_on_a_frozen_experiment_is_an_amendment_not_drift(session, capsys, monkeypatch):
    """A person retracting a claim on a frozen experiment is reported as unamended drift, which blocks every other claim; the suggested fix does nothing"""
    start(capsys, monkeypatch, "--baseline", "a", "--candidate", "b")
    P(capsys, monkeypatch, "claim", "add", "c", "-e", "e1", "--metric", "b.epe", "--at-most", "6", "--id", "c1")
    P(capsys, monkeypatch, "claim", "add", "bad", "-e", "e1", "--metric", "b.epe", "--at-most", "1", "--id", "c2")
    P(capsys, monkeypatch, "freeze", "e1", "-m", "go")
    attach(capsys, "b.epe=5")
    commit_all()
    assert verdict(capsys)["established"]
    refused(capsys, "retract", "c2", "-m", "typo", code="E_HUMAN_ONLY")
    P(capsys, monkeypatch, "retract", "c2", "-m", "typo")
    v = verdict(capsys)
    assert v["established"] and "drift" not in caveat_codes(v)
    exp = ok(capsys, "show", "e1").data
    assert exp["spec_drift"] is None and "c2" in exp["object"]["amendments"][-1]["change"]
    assert not [i for i in open_items(capsys) if i["code"] in ("drift", "edited_outside_rb")]


def test_a_variant_can_be_retracted(session, capsys, monkeypatch):
    """Variants cannot be retracted, though the docs say rb retract 'takes anything back'
    (also: A variant cannot be retracted by anyone; a mistyped candidate leaves candidate.* claims untested for good)"""
    start(capsys, monkeypatch)
    P(capsys, monkeypatch, "claim", "add", "int8 under 6", "-e", "e1", "--metric", "candidate.epe", "--at-most", "6", "--id", "c1")
    P(capsys, monkeypatch, "variant", "add", "e1", "in8", "--role", "candidate")          # a typo
    refused(capsys, "retract", "e1/in8", "-m", "typo", code="E_HUMAN_ONLY")                 # the person added it
    env = P(capsys, monkeypatch, "retract", "e1/in8", "-m", "typo")
    assert env.data["object"]["kind"] == "variant" and env.data["object"]["id"] == "e1/in8"
    assert [v["name"] for v in ok(capsys, "show", "e1").data["object"]["variants"] if v.get("retracted") is None] == ["fp32", "int8"]
    attach(capsys, "int8.epe=5", "fp32.epe=5.1")
    v = verdict(capsys)
    assert v["status"] == "supported" and v["observations"][0]["value"] == 5
    # an agent may take back a variant it added while nothing rests on it
    ok(capsys, "variant", "add", "e1", "int4", "--role", "ablation")
    ok(capsys, "retract", "e1/int4", "-m", "not testing int4")


def test_retracting_a_variant_of_a_frozen_experiment_is_a_persons_amendment(session, capsys, monkeypatch):
    """Variants cannot be retracted, though the docs say rb retract 'takes anything back'"""
    start(capsys, monkeypatch)
    ok(capsys, "variant", "add", "e1", "int4", "--role", "ablation")
    P(capsys, monkeypatch, "claim", "add", "c", "-e", "e1", "--metric", "int8.epe", "--at-most", "6", "--id", "c1")
    P(capsys, monkeypatch, "freeze", "e1", "-m", "go")
    refused(capsys, "retract", "e1/int4", "-m", "not testing int4", code="E_HUMAN_ONLY")
    P(capsys, monkeypatch, "retract", "e1/int4", "-m", "not testing int4")
    exp = ok(capsys, "show", "e1").data
    assert exp["spec_drift"] is None and "int4" in exp["object"]["amendments"][-1]["change"]


# ================================================================ freezing and amending


def test_an_amendment_records_the_hash_the_freeze_record_had(session, capsys, monkeypatch):
    """An amendment's 'before' hash is the current (drifted) spec, so an unamended change is folded into the next unrelated amendment and the drift disappears"""
    start(capsys, monkeypatch, "--baseline", "a", "--candidate", "b")
    P(capsys, monkeypatch, "claim", "add", "c", "-e", "e1", "--metric", "b.epe", "--at-most", "6", "--id", "c1")
    P(capsys, monkeypatch, "freeze", "e1", "-m", "go")
    frozen = ok(capsys, "show", "e1").data["object"]["frozen"]["sha256"]
    ok(capsys, "metric", "add", "epe", "--alias", "endpoint_error")     # the catalogue entry c1 reads changes after the freeze
    assert "drift" in verdict(capsys)["not_established_because"]
    P(capsys, monkeypatch, "claim", "add", "c2", "-e", "e1", "--metric", "b.epe", "--at-most", "7", "--id", "c2", "--amend", "--why", "looser bound")
    am = ok(capsys, "show", "e1").data["object"]["amendments"][-1]
    assert am["before"] == frozen and "also adopts" in am["change"]


def test_a_hand_edit_of_a_frozen_spec_cannot_be_folded_into_an_amendment(session, capsys, monkeypatch):
    """An amendment's 'before' hash is the current (drifted) spec, so an unamended change is folded into the next unrelated amendment and the drift disappears"""
    write("train.yaml", "lr: 0.0001\nbs: 8\n")
    start(capsys, monkeypatch, "--baseline", "a", "--candidate", "b")
    P(capsys, monkeypatch, "spec", "set", "e1", "--from", "train.yaml", "--keys", "lr,bs")
    P(capsys, monkeypatch, "claim", "add", "c", "-e", "e1", "--metric", "b.epe", "--at-most", "6", "--id", "c1")
    P(capsys, monkeypatch, "freeze", "e1", "-m", "go")

    def bs16(doc):
        s = next(s for s in doc["settings"] if s["name"] == "bs")
        s.update(value=16, status="provisional")
        s["source"]["resolved"] = None
    edit_json(Path(".rb/experiments/e1.json"), bs16)
    with person(monkeypatch):
        refused(capsys, "claim", "add", "c2", "-e", "e1", "--metric", "b.epe", "--at-most", "7", "--id", "c2", "--amend", "--why", "looser bound",
                code="E_STATE_EDITED")
    assert not Path(".rb/claims/c2.json").exists()
    assert gate(capsys, "unestablished") == 1


def test_the_fix_for_a_hand_edit_restores_what_rb_last_wrote_and_keeps_an_uncommitted_freeze(session, capsys, monkeypatch):
    """The drift/edited fix `git checkout .rb/experiments/<id>.json` throws away uncommitted rb writes (a person's freeze), and the item stays open"""
    start(capsys, monkeypatch, "--baseline", "a", "--candidate", "b")
    commit_all()
    P(capsys, monkeypatch, "spec", "set", "e1", "wd", "0.01", "--source", "note:x")
    P(capsys, monkeypatch, "freeze", "e1", "-m", "pre-register")                  # not committed

    def wd(doc):
        doc["settings"][0]["value"] = 0.02
    edit_json(Path(".rb/experiments/e1.json"), wd)
    it = next(i for i in open_items(capsys) if i["code"] == "edited_outside_rb")
    assert "git checkout" not in it["do"] and it["do"].startswith("rb doctor --restore")
    code, _ = run_do(capsys, monkeypatch, it["do"])
    assert code == 0
    exp = ok(capsys, "show", "e1").data
    assert exp["object"]["frozen"] is not None and exp["object"]["settings"][0]["value"] == 0.01 and exp["spec_drift"] is None
    assert not [i for i in open_items(capsys) if i["code"] in ("edited_outside_rb", "drift")]


# ================================================================ edits outside rb, and rb doctor


@pytest.mark.parametrize("how", ["edit", "delete"])
def test_evidence_changed_outside_rb_fails_every_gate(session, capsys, monkeypatch, how):
    """Evidence edited outside rb does not fail --fail-on refuted/undecided/unknown/stale
    (also: A hand-edited evidence file does not fail --fail-on gates: editing away a refutation makes `--fail-on refuted` pass)"""
    start(capsys, monkeypatch, "--baseline", "a", "--candidate", "b")
    P(capsys, monkeypatch, "claim", "add", "y at most 1", "-e", "e1", "--metric", "change.y", "--at-most", "1", "--id", "c1")
    ev = attach(capsys, "change.y=2")
    assert gate(capsys, "refuted") == 1 and gate(capsys, "unknown") == 0
    path = Path(f".rb/evidence/{ev}.json")
    if how == "edit":
        edit_json(path, lambda d: d["metrics"].update({"change.y": 0.2}))
    else:
        path.unlink()
    for g in GATES:
        assert gate(capsys, g) == 1, g
    code, env = rbj(capsys, "doctor")
    assert code == 1 and any(c["check"] == "edited_outside_rb" and ev in c["detail"] for c in env.data["checks"])


def test_a_hand_edited_hypothesis_fails_every_gate(session, capsys, monkeypatch):
    """A hand-edited evidence file does not fail --fail-on gates: editing away a refutation makes `--fail-on refuted` pass"""
    start(capsys, monkeypatch, "--baseline", "a", "--candidate", "b")
    P(capsys, monkeypatch, "hypothesis", "add", "h", "--id", "h1")
    assert gate(capsys, "undecided") == 0
    edit_json(Path(".rb/hypotheses/h1.json"), lambda d: d.update(statement="h edited"))
    for g in GATES:
        assert gate(capsys, g) == 1, g


def test_doctor_names_real_paths_and_restores_edited_objects(session, capsys, monkeypatch):
    """rb doctor's fix for an edited hypothesis or evidence names directories that do not exist (.rb/hypothesiss/, .rb/evidences/)"""
    start(capsys, monkeypatch, "--baseline", "a", "--candidate", "b")
    P(capsys, monkeypatch, "hypothesis", "add", "h", "--id", "h1")
    ev = attach(capsys, "a.x=1")
    commit_all()
    edit_json(Path(".rb/hypotheses/h1.json"), lambda d: d.update(statement="h edited"))
    edit_json(Path(f".rb/evidence/{ev}.json"), lambda d: d["metrics"].update({"a.x": 2.0}))
    rc, out = text(capsys, "doctor")
    assert rc == 1 and "hypothesiss" not in out and "evidences" not in out and "rb doctor --restore" in out
    assert ".rb/hypotheses/h1.json" in out and f".rb/evidence/{ev}.json" in out
    assert rbj(capsys, "doctor", "--restore")[0] == 0
    assert json.loads(Path(".rb/hypotheses/h1.json").read_text())["statement"] == "h"
    assert json.loads(Path(f".rb/evidence/{ev}.json").read_text())["metrics"]["a.x"] == 1.0


def test_doctor_lists_corrupt_and_conflicted_files_instead_of_failing_with_them(session, capsys, monkeypatch):
    """rb doctor cannot list corrupt or conflicted files, and its fix commands name directories that do not exist
    (also: E_STATE_CORRUPT sends the user to `rb doctor`, which fails with the same error and never reaches its merge-leftover check)"""
    start(capsys, monkeypatch, "--baseline", "a", "--candidate", "b")
    P(capsys, monkeypatch, "question", "add", "q1", "--id", "q1")
    P(capsys, monkeypatch, "question", "add", "q2", "--id", "q2")
    write(".rb/questions/q1.json", '{"broken\n')
    write(".rb/questions/q2.json", "<<<<<<< HEAD\n{}\n=======\n{}\n>>>>>>> other\n")
    err = refused(capsys, "status", code="E_STATE_CORRUPT")
    assert "rb doctor" in (err.fix or "")
    code, env = rbj(capsys, "doctor")
    assert code == 1 and env.ok
    checks = {(c["check"], c["detail"].split(" ")[0]) for c in env.data["checks"] if not c["ok"]}
    assert ("corrupt", ".rb/questions/q1.json") in checks and ("merge_conflict", ".rb/questions/q2.json") in checks
    assert rbj(capsys, "doctor", "--restore")[0] == 0
    assert rbj(capsys, "doctor")[0] == 0 and rbj(capsys, "status")[0] == 0


# ================================================================ who is a person


def test_a_codex_home_in_a_persons_profile_does_not_make_them_an_agent(session, capsys, monkeypatch, tmp_path):
    """Any CODEX_* variable (e.g. CODEX_HOME in a shell profile) makes a person an agent, and they can never make a person's call that counts"""
    start(capsys, monkeypatch, "--baseline", "a", "--candidate", "b")
    P(capsys, monkeypatch, "experiment", "add", "y", "--id", "e2", "--baseline", "a", "--candidate", "b")
    with person(monkeypatch):
        monkeypatch.setenv("CODEX_HOME", str(tmp_path / ".codex"))
        env = ok(capsys, "freeze", "e1", "-m", "mine")
        assert env.data["actor"]["id"] == "human:t"
        monkeypatch.setenv("CODEX_SANDBOX", "seatbelt")               # a command Codex itself runs
        err = refused(capsys, "freeze", "e2", "-m", "mine", code="E_HUMAN_ONLY")
        assert "agent:codex" in err.message


def test_a_persons_name_typed_in_an_agent_session_is_not_shown_as_a_persons_call(session, capsys, monkeypatch):
    """A person's call asserted from an agent session is shown without its stamp in rb show and rb context"""
    ok(capsys, "init", "x")
    ok(capsys, "experiment", "add", "t", "--id", "e", "--baseline", "a", "--candidate", "b")
    monkeypatch.setenv("RB_ACTOR", "human:alice")
    err = refused(capsys, "freeze", "e", "-m", "asserted", code="E_HUMAN_ONLY")
    assert "RB_ACTOR=human:alice is ignored" in err.message
    err = refused(capsys, "claim", "add", "late", "-e", "e", "--metric", "change.x", "--at-most", "1", "--amend", "--why", "add it",
                  code="E_HUMAN_ONLY")
    assert ok(capsys, "show", "e").data["object"]["frozen"] is None
    assert "human:alice" not in ok(capsys, "context").data["markdown"]
    assert not [r for r in Ledger(session).log_entries() if r["actor"].startswith("human:")]


def test_rb_log_text_shows_how_rb_knew_the_actor_and_through_what(session, capsys):
    """rb log text shows neither 'through what' (via) nor how rb knew the actor"""
    ok(capsys, "init", "x")
    rbsdk.open().add_question("from sdk")
    rc, out = text(capsys, "log")
    assert rc == 0 and "detected:CLAUDECODE" in out and "via sdk" in out and "via cli" in out


# ================================================================ run configs


def test_a_config_that_mentions_no_setting_is_not_reported_as_ran_with_the_spec(session, capsys, monkeypatch):
    """A --config (or --run) that mentions none of the spec's settings prints 'Ran with the spec' and drops the config_unchecked caveat
    (also: --config that mentions none of the spec's settings is reported as 'Ran with the spec', and the config_unchecked caveat disappears)"""
    write("train.yaml", "optim:\n  lr: 1e-4\n  bs: 8\n")
    write("unrelated.yaml", "trainer:\n  max_epochs: 10\n")
    start(capsys, monkeypatch, "--baseline", "a", "--candidate", "b")
    P(capsys, monkeypatch, "spec", "set", "e1", "--from", "train.yaml", "--keys", "optim.*")
    P(capsys, monkeypatch, "claim", "add", "c", "-e", "e1", "--metric", "b.epe", "--at-most", "6", "--id", "c1")
    rc, out = text(capsys, "evidence", "attach", "e1", "b.epe=5", "--config", "unrelated.yaml")
    assert rc == 0 and "Ran with the spec" not in out and "mentions none of the spec's settings" in out
    assert "config_unchecked" in caveat_codes(verdict(capsys))


def test_a_review_run_whose_record_holds_no_setting_is_config_unchecked(session, capsys, monkeypatch):
    """The receipt says 'Ran with the spec' when no setting was compared, and prints 'commit unkno'"""
    start(capsys, monkeypatch)
    ok(capsys, "spec", "set", "e1", "lr", "0.1", "--no-verify")
    write("results.json", json.dumps(REVIEW))
    rc, out = text(capsys, "evidence", "attach", "e1", "--run", "results.json")
    assert rc == 0 and "Ran with the spec" not in out and "mentions none of the spec's settings" in out
    ev = next(Path(".rb/evidence").glob("*.json")).stem
    rc, out = text(capsys, "show", ev)
    assert "commit unknown" in out and "commit unkno (" not in out


@pytest.mark.parametrize("how", ["config", "run"])
def test_context_does_not_list_evidence_as_ran_with_the_spec_when_nothing_was_compared(session, capsys, monkeypatch, how):
    """--config that mentions none of the spec's settings is reported as 'Ran with the spec', and the config_unchecked caveat disappears
    (also: The receipt says 'Ran with the spec' when no setting was compared, and prints 'commit unkno')"""
    write("unrelated.yaml", "trainer:\n  max_epochs: 10\n")
    write("results.json", json.dumps(REVIEW))
    start(capsys, monkeypatch)
    ok(capsys, "spec", "set", "e1", "lr", "0.1", "--no-verify")
    attach(capsys, *(["int8.epe=5", "--config", "unrelated.yaml"] if how == "config" else ["--run", "results.json"]))
    line = [x for x in section(ok(capsys, "context").data["markdown"], "Experiment e1: x").splitlines() if x.startswith("- ev")]
    assert len(line) == 1
    assert "ran with the spec" not in line[0] and "config unchecked" in line[0], line[0]


def test_a_per_run_value_is_compared_with_the_runs_own_config(session, capsys, monkeypatch):
    """--set seed=N is not compared with the seed in the run's own --config"""
    write("train.yaml", "lr: 0.0001\n")
    write("cfg.yaml", "lr: 0.0001\nseed: 3\n")
    start(capsys, monkeypatch, "--baseline", "a", "--candidate", "b")
    P(capsys, monkeypatch, "spec", "set", "e1", "lr", "--source", "train.yaml#lr")
    P(capsys, monkeypatch, "spec", "set", "e1", "seed", "--per-run")
    P(capsys, monkeypatch, "claim", "add", "c", "-e", "e1", "--metric", "b.epe", "--at-most", "6", "--id", "c1")
    rc, out = text(capsys, "evidence", "attach", "e1", "b.epe=5", "--set", "seed=2", "--config", "cfg.yaml")
    assert rc == 0 and "Ran with a different spec" in out and "seed" in out
    v = verdict(capsys)
    assert v["status"] == "untested" and v["observations"][0]["role"] == "not_counted"


def test_a_review_run_is_not_mapped_onto_the_wrong_variants(session, capsys, monkeypatch):
    """evidence attach --run maps the run's baseline and candidate onto the experiment's roles without checking names, so numbers can be recorded against the wrong variant"""
    start(capsys, monkeypatch)
    P(capsys, monkeypatch, "claim", "add", "c", "-e", "e1", "--metric", "change.epe", "--at-most", "0.05", "--id", "c1")
    write("r.json", json.dumps({**REVIEW, "baseline": "int8", "candidate": "fp32"}))
    refused(capsys, "evidence", "attach", "e1", "--run", "r.json", code="E_OBJECT_INVALID")
    P(capsys, monkeypatch, "experiment", "add", "y", "--id", "e2", "--baseline", "fp32", "--candidate", "int8", "--candidate", "int4")
    write("ok.json", json.dumps(REVIEW))
    refused(capsys, "evidence", "attach", "e2", "--run", "ok.json", code="E_OBJECT_INVALID")   # which candidate is it?
    assert not list(Path(".rb/evidence").glob("*.json"))


# ================================================================ the docs' examples


def test_the_readme_quick_start_runs_as_written(session, capsys, monkeypatch):
    """The README quick start, the AGENTS.md worked example and the SDK example all fail at the evidence step: seed is never declared per-run"""
    block = (ROOT / "README.md").read_text(encoding="utf-8").split("```sh\n", 1)[1].split("```", 1)[0]
    write("configs/train.yaml", "optim:\n  lr: 0.0001\ndata:\n  batch_size: 8\n")
    write("out/.hydra/config.yaml", "optim:\n  lr: 0.0001\ndata:\n  batch_size: 8\n")
    write("out/metrics.json", '{"fp32": {"epe": 5.61}, "int8": {"epe": 5.64}}\n')
    commit_all()
    ran = 0
    for line in block.splitlines():
        argv = shlex.split(line, comments=True)
        if not argv or argv[0] != "rb":
            continue
        if argv[1] == "freeze":
            with person(monkeypatch):
                code = main(argv[1:])
        else:
            code = main(argv[1:])
        capsys.readouterr()
        assert code == 0, line
        ran += 1
    assert ran >= 8


def test_the_sdk_example_runs_as_written(session, capsys, monkeypatch):
    """The README quick start, the AGENTS.md worked example and the SDK example all fail at the evidence step: seed is never declared per-run"""
    doc = (ROOT / "AGENTS.md").read_text(encoding="utf-8").split("### From Python, and over MCP", 1)[1]
    code = doc.split("```python\n", 1)[1].split("```", 1)[0]
    lines = []
    for line in code.splitlines():
        if 'state.experiment("int8")' in line and "# or " in line:      # the path that starts the experiment
            line = line.split("#", 1)[0].replace('state.experiment("int8")', line.split("# or ", 1)[1].strip())
        lines.append(line)
    write("configs/train.yaml", "optim:\n  lr: 0.0001\n")
    write("outputs/2/.hydra/config.yaml", "optim:\n  lr: 0.0001\n")
    commit_all()
    ok(capsys, "init", "Does INT8 keep RAFT's accuracy on KITTI?")
    exec(compile("\n".join(lines), "AGENTS.md", "exec"), {"fp32_epe": 5.61, "int8_epe": 5.64})
    capsys.readouterr()
    assert len(list(Path(".rb/evidence").glob("*.json"))) == 1


# ================================================================ the SDK


def test_sdk_amend_refusals_carry_the_handoff(session, capsys, monkeypatch):
    """SDK: amend= calls from an agent raise E_HUMAN_ONLY without err.extra['handoff']"""
    start(capsys, monkeypatch, "--baseline", "a", "--candidate", "b")
    P(capsys, monkeypatch, "freeze", "e1", "-m", "pre-register")
    exp = rbsdk.open().experiment("e1")
    calls = {
        "freeze": lambda: exp.freeze(why="x"),
        "spec.set": lambda: exp.spec.set("lr", 0.1, source="note:x", amend="why"),
        "claim": lambda: exp.claim("c", metric="change.x", at_most=1, amend="why"),
        "add_variant": lambda: exp.add_variant("c", role="ablation", amend="why"),
        "spec.vary": lambda: exp.spec.vary("p", amend="why"),
    }
    for name, call in calls.items():
        with pytest.raises(RBError) as e:
            call()
        assert e.value.code == "E_HUMAN_ONLY", name
        handoff = e.value.extra.get("handoff") or {}
        assert handoff.get("who") == "person" and handoff.get("command", "").startswith("rb "), name
        if name != "freeze":
            assert "--amend" in handoff["command"], name


def test_sdk_accepts_numpy_scalars(session, capsys, monkeypatch):
    """SDK: numpy float32 metrics are refused by run.log ('got float32') and crash exp.attach with a raw TypeError"""
    np = pytest.importorskip("numpy")
    start(capsys, monkeypatch)
    exp = rbsdk.open().experiment("e1")
    with exp.run() as run:
        run.log(**{"int8.epe": np.float32(5.5)})
    assert run.evidence is not None and run.evidence.metrics == {"int8.epe": 5.5}
    ev = exp.attach({"int8.epe": np.float32(5.25), "fp32.epe": np.float64(5.0)})
    assert ev is not None and ev.metrics == {"int8.epe": 5.25, "fp32.epe": 5.0}


def test_sdk_attach_does_not_let_the_caller_choose_the_basis(session, capsys, monkeypatch):
    """The SDK lets the caller label typed numbers as basis 'run' or 'file', which hides the 'typed in' caveat"""
    start(capsys, monkeypatch)
    exp = rbsdk.open().experiment("e1")
    assert "basis" not in inspect.signature(exp.attach).parameters
    with pytest.raises(TypeError):
        exp.attach({"int8.epe": 5.6}, basis="run")
    assert exp.attach({"int8.epe": 5.6}).basis == "logged"


# ================================================================ the command line and MCP surfaces


def test_a_claim_needs_an_experiment(session, capsys):
    """A claim with no experiment is blocked for good, yet status says 'Nothing open.' and the claim add output says 'not on an experiment yet'"""
    ok(capsys, "init", "n")
    code, env = rbj(capsys, "claim", "add", "floating claim", "--metric", "epe", "--at-most", "1", "--id", "c1")
    assert code == 2 and env.errors[0].code == "E_USAGE"
    assert not Path(".rb/claims/c1.json").exists()


def test_json_envelopes_name_every_object_and_the_actor(session, capsys):
    """--json envelope: data.object has no id for settings and variants, and the same setting is 'address' in spec set but 'id' in retract; a duplicate attach has no data.actor"""
    ok(capsys, "init", "j")
    ok(capsys, "experiment", "add", "e", "--id", "e1", "--baseline", "a", "--candidate", "b")
    obj = ok(capsys, "spec", "set", "e1", "zz", "1").data["object"]
    assert obj["kind"] == "setting" and obj["id"] == "e1/zz"
    obj = ok(capsys, "variant", "add", "e1", "v9", "--role", "ablation").data["object"]
    assert obj["kind"] == "variant" and obj["id"] == "e1/v9"
    obj = ok(capsys, "retract", "e1/zz", "-m", "x").data["object"]
    assert obj["kind"] == "setting" and obj["id"] == "e1/zz"
    ok(capsys, "evidence", "attach", "e1", "a.x=1")
    env = ok(capsys, "evidence", "attach", "e1", "a.x=1")
    assert env.data["duplicate_of"] and env.data["actor"]["id"] == "agent:claude-code"


def mcp_call(server: Server, name: str, arguments: dict, mid: int = 2) -> dict:
    reply = server.handle({"jsonrpc": "2.0", "id": mid, "method": "tools/call", "params": {"name": name, "arguments": arguments}})
    return reply["result"]["structuredContent"]


def test_mcp_passes_a_value_that_starts_with_a_dash(session, capsys, monkeypatch):
    """MCP cannot pass a positional value that starts with '-' (a setting value '-O2', a statement '-10%'); the E_USAGE fix does not apply over MCP"""
    start(capsys, monkeypatch, "--baseline", "a", "--candidate", "b")
    server = Server()
    server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"clientInfo": {"name": "T"}}})
    env = mcp_call(server, "spec_set", {"experiment": "e1", "name": "nvcc_flags", "value": "-O2", "source": "note:build.sh"})
    assert env["ok"], env["errors"]
    assert env["data"]["object"]["value"] == "-O2"
    env = mcp_call(server, "hypothesis_add", {"statement": "-10%"}, mid=3)
    assert env["ok"], env["errors"]
    assert env["data"]["object"]["statement"] == "-10%"


def test_mcp_answers_invalid_json_rpc(session):
    """rb mcp does not answer invalid JSON-RPC requests (empty batch, non-object entries, a bare string) and gives -32601 for a missing method"""
    lines = ["[]", "[1]", '"hello"', '{"jsonrpc":"2.0","id":7}', '{"jsonrpc":"2.0","id":8,"method":"ping"}']
    out = io.StringIO()
    serve(io.StringIO("\n".join(lines) + "\n"), out, log=lambda s: None)
    replies = [json.loads(x) for x in out.getvalue().splitlines()]
    assert len(replies) == 5
    empty, batch, bare, no_method, ping = replies
    assert empty["error"]["code"] == -32600 and empty["id"] is None
    assert isinstance(batch, list) and len(batch) == 1 and batch[0]["error"]["code"] == -32600
    assert bare["error"]["code"] == -32600
    assert no_method["id"] == 7 and no_method["error"]["code"] == -32600
    assert ping == {"jsonrpc": "2.0", "id": 8, "result": {}}


def section(markdown: str, title: str) -> str:
    """The body of one `## title` section of rb context."""
    body = markdown.split(f"\n## {title}\n", 1)[1]
    return body.split("\n## ", 1)[0]
