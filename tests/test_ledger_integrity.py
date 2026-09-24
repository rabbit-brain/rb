"""Regression tests for the ways the research state could read as more settled than it is. Each one was found by an
adversarial review of the first version and reproduced before it was fixed."""
import json
import multiprocessing
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from rabbit_brain.cli import main
from rabbit_brain.ledger import Ledger
from rabbit_brain.models import Envelope
from rabbit_brain.sources import pointer_get, split_lines, states

HAS_GIT = shutil.which("git") is not None
needs_git = pytest.mark.skipif(not HAS_GIT, reason="git is not installed")


def git(*args, cwd=None):
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True, cwd=cwd).stdout.strip()


def git_repo(path: Path):
    for cmd in (["init", "-q"], ["config", "user.email", "t@example.com"], ["config", "user.name", "t"]):
        git(*cmd, cwd=path)


@pytest.fixture
def lab(workdir, monkeypatch):
    monkeypatch.delenv("RB_ACTOR", raising=False)
    monkeypatch.setenv("USER", "jhet")
    return workdir


def rbj(capsys, *argv):
    code = main([*argv, "--json"])
    return code, Envelope.model_validate_json(capsys.readouterr().out)


def start(capsys, exp="e1"):
    assert rbj(capsys, "investigation", "init", "t")[0] == 0
    assert rbj(capsys, "experiment", "add", "x", "--id", exp)[0] == 0


def verify(capsys, exp, name, value, source, *extra):
    code, env = rbj(capsys, "spec", "set", exp, name, value, "--source", source, "--verify", *extra)
    return env.data["setting"]["status"] if env.ok else env.errors[0].code


# ---------------------------------------------------------------- "states" is strict


def test_numbers_are_standalone_tokens_compared_exactly():
    assert not states("seed: 1700000001", 1700000000)
    assert not states("seed: 1234567890123457", 1234567890123456)
    assert states("seed: 1700000001", 1700000001) and states("steps: 6.0", 6)
    assert states("lr: 0.0001", 1e-4) and states("lr=1e-4,", 0.0001)
    assert not states("backbone: resnet50", 50) and not states("precision: fp32", 32) and not states("conv3d", 3)
    assert not states("date: 2024-01-05", -1) and not states("date: 2024-01-05", 1)
    assert not states("steps: 1,000", 1) and states("steps: 1,000", 1000)
    assert states("wd: -1e-4", -1e-4) and states("latency 28.9ms", 28.9)


def test_text_and_lists_are_whole_and_ordered():
    assert not states("optimizer: adamw", "adam") and states("optimizer: adam", "adam")
    assert not states("batch: 16", "6") and not states("name: resnet", "res")
    assert not states("anything at all", "") and not states("anything", "  ")
    assert states("betas: [0.9, 0.999]", [0.9, 0.999]) and not states("betas: [0.999, 0.9]", [0.9, 0.999])
    assert not states("kernel_size: [3, 5]", [3, 3]) and states("kernel_size: [3,5]", [3, 5])
    assert states("amp: True", True) and not states("amp: 1", True) and not states("amp: True", 1)


def test_lines_are_numbered_like_git_and_sed():
    assert split_lines("a\n\x0c\nlr = 3e-4\r\nwd\n") == ["a", "\x0c", "lr = 3e-4", "wd"]
    assert split_lines("x y\nz") == ["x y", "z"]


def test_json_pointer_follows_rfc_6901():
    doc = {"": {"seed": 7}, "seed": 3, "l": [10, 20]}
    assert pointer_get(doc, "//seed") == 7 and pointer_get(doc, "/seed") == 3 and pointer_get(doc, "/l/1") == 20
    for bad in ("/l/²", "/l/01", "/l/2", "seed"):
        with pytest.raises(KeyError):
            pointer_get(doc, bad)


def test_verification_through_the_cli_refuses_near_misses(lab, capsys):
    start(capsys)
    Path("train.yaml").write_text("seed: 1700000001\noptimizer: adamw\nbatch: 16\nbackbone: resnet50\n", encoding="utf-8")
    assert verify(capsys, "e1", "seed", "1700000000", "train.yaml:1") == "provisional"
    assert verify(capsys, "e1", "opt", "adam", "train.yaml:2") == "provisional"
    assert verify(capsys, "e1", "b", '"6"', "train.yaml:3") == "provisional"
    assert verify(capsys, "e1", "depth", "50", "train.yaml:4") == "provisional"
    code, env = rbj(capsys, "spec", "set", "e1", "k", "")
    assert code == 2 and env.errors[0].code == "E_OBJECT_INVALID"


def test_a_changed_value_is_never_kept_verified(lab, capsys, monkeypatch):
    start(capsys)
    Path("train.yaml").write_text("seed: 1700000001\nlr: 0.001\n", encoding="utf-8")
    assert verify(capsys, "e1", "seed", "1700000001", "train.yaml:1") == "verified"
    monkeypatch.setenv("RB_ACTOR", "agent:x")
    code, env = rbj(capsys, "spec", "set", "e1", "seed", "1700000002")
    assert env.data["setting"]["status"] == "provisional" and env.data["setting"]["source"] is None
    monkeypatch.delenv("RB_ACTOR")
    assert verify(capsys, "e1", "lr", "0.001", "train.yaml:2") == "verified"
    code, env = rbj(capsys, "spec", "set", "e1", "lr", "0.0010000000001")
    assert env.data["setting"]["status"] == "provisional"


def test_a_hand_edited_value_on_a_verified_setting_is_stale(lab, capsys):
    start(capsys)
    Path("cfg.yaml").write_text("lr: 1e-4\n", encoding="utf-8")
    assert verify(capsys, "e1", "lr", "1e-4", "cfg.yaml:1") == "verified"
    p = lab / ".rb" / "experiments" / "e1.json"
    doc = json.loads(p.read_text())
    doc["settings"][0]["value"] = 0.03
    p.write_text(json.dumps(doc))
    code, env = rbj(capsys, "status", "--fail-on", "stale")
    assert code == 1 and env.data["experiments"][0]["stale"] == ["lr"]


@needs_git
def test_a_forged_commit_pinned_resolution_is_stale(lab, capsys):
    start(capsys)
    git_repo(lab)
    Path("cfg.yaml").write_text("lr: 1e-4\n", encoding="utf-8")
    git("add", "cfg.yaml")
    git("commit", "-qm", "c")
    head = git("rev-parse", "HEAD")
    assert verify(capsys, "e1", "lr", "1e-4", "cfg.yaml:1", "--commit", head) == "verified"
    p = lab / ".rb" / "experiments" / "e1.json"
    doc = json.loads(p.read_text())
    doc["settings"][0]["source"]["resolved"]["sha256"] = "0" * 64
    p.write_text(json.dumps(doc))
    code, env = rbj(capsys, "status", "--fail-on", "stale")
    assert code == 1


@needs_git
def test_commit_pinned_sources_work_in_a_subdirectory_and_another_repo(lab, capsys, monkeypatch):
    git_repo(lab)
    Path("configs").mkdir()
    Path("configs/train.yaml").write_text("lr: 1e-4\n", encoding="utf-8")
    Path("model").mkdir()
    git_repo(lab / "model")
    Path("model/train.py").write_text("iters = 12\n", encoding="utf-8")
    git("add", ".", cwd=lab / "model")
    git("commit", "-qm", "m", cwd=lab / "model")
    git("add", "configs")
    git("commit", "-qm", "c")
    Path("sub").mkdir()
    monkeypatch.chdir("sub")
    start(capsys)
    head = git("rev-parse", "HEAD")
    assert verify(capsys, "e1", "lr", "1e-4", "../configs/train.yaml:1", "--commit", head) == "verified"
    code, env = rbj(capsys, "spec", "set", "e1", "lr2", "1e-4", "--source", "../configs/train.yaml:1", "--verify")
    assert env.data["setting"]["source"]["resolved"]["commit"] == head          # a committed file is pinned, whatever the root
    model_head = git("rev-parse", "HEAD", cwd=lab / "model")
    assert verify(capsys, "e1", "iters", "12", "../model/train.py:1", "--commit", model_head) == "verified"


def test_a_symlinked_source_keeps_the_name_it_was_given(lab, capsys):
    start(capsys)
    Path("configs").mkdir()
    Path("configs/v3.yaml").write_text("lr: 0.001\n", encoding="utf-8")
    Path("configs/v4.yaml").write_text("lr: 0.002\n", encoding="utf-8")
    os.symlink("v3.yaml", "configs/latest.yaml")
    code, env = rbj(capsys, "spec", "set", "e1", "lr", "0.001", "--source", "configs/latest.yaml:1", "--verify")
    assert env.data["setting"]["source"]["path"] == "configs/latest.yaml"
    os.remove("configs/latest.yaml")
    os.symlink("v4.yaml", "configs/latest.yaml")
    code, env = rbj(capsys, "status", "--fail-on", "stale")
    assert code == 1


# ---------------------------------------------------------------- claims and evidence


def test_an_unlabelled_run_attaches_no_error_numbers(lab, capsys):
    start(capsys)
    code, env = rbj(capsys, "example")
    bundle = json.loads((lab / "rb-runs" / env.run_id / "bundle.json").read_text())
    bundle["source"] = "run"
    for c in bundle["cases"]:
        c["baseline_error"] = c["candidate_error"] = None
        c["has_gt"] = False
    Path("nogt").mkdir()
    Path("nogt/bundle.json").write_text(json.dumps(bundle), encoding="utf-8")
    rbj(capsys, "claim", "add", "no worse", "--experiment", "e1", "--metric", "change.mean_endpoint_error", "--at-most", "0.05")
    code, env = rbj(capsys, "evidence", "attach", "e1", "--run", "nogt/bundle.json")
    metrics = env.data["evidence"]["metrics"]
    assert code == 0 and metrics["with_gt"] == 0 and not any(k.endswith("mean_endpoint_error") for k in metrics)
    assert env.data["verdicts"][0]["status"] == "not_tested"


def test_synthetic_adapter_runs_are_synthetic(lab, capsys):
    start(capsys)
    assert main(["init", "--demo"]) == 0
    capsys.readouterr()
    code = main(["run", "--baseline", "ckpt/synth-current.json", "--candidate", "ckpt/synth-candidate.json", "--evidence", "none", "--quiet", "--json"])
    run_id = Envelope.model_validate_json(capsys.readouterr().out).run_id
    assert code == 0 and run_id
    rbj(capsys, "claim", "add", "ok", "--experiment", "e1", "--metric", "candidate.mean_endpoint_error", "--at-most", "1000")
    code, env = rbj(capsys, "evidence", "attach", "e1", "--run", run_id)
    assert env.data["evidence"]["synthetic"] and not env.data["verdicts"][0]["established"]


def test_a_results_file_read_in_place_is_named_and_hashed(lab, capsys):
    start(capsys)
    main(["example", "--write", "cmp.json"])
    capsys.readouterr()
    code, env = rbj(capsys, "evidence", "attach", "e1", "--run", "cmp.json")
    ev = env.data["evidence"]
    assert code == 0 and ev["run"] == "cmp.json" and ev["files"][0]["path"] == "cmp.json" and len(ev["files"][0]["sha256"]) == 64


def test_evidence_attach_finds_runs_from_a_subdirectory(lab, capsys, monkeypatch):
    start(capsys)
    code, env = rbj(capsys, "example")
    Path("configs").mkdir()
    monkeypatch.chdir("configs")
    code, env2 = rbj(capsys, "evidence", "attach", "e1", "--run", env.run_id)
    assert code == 0 and env2.data["evidence"]["run"] == f"rb-runs/{env.run_id}"


def test_non_finite_and_repeated_numbers_are_refused(lab, capsys):
    start(capsys)
    code, env = rbj(capsys, "claim", "add", "x", "--experiment", "e1", "--metric", "m", "--within", "1", "--tolerance", "inf")
    assert code == 2 and env.errors[0].code == "E_OBJECT_INVALID"
    code, env = rbj(capsys, "evidence", "attach", "e1", "--metric", "m=1", "--metric", "m=2")
    assert code == 2 and "given twice" in env.errors[0].message
    Path("m.json").write_text(json.dumps({"a.b": 1, "a": {"b": 2}}), encoding="utf-8")
    code, env = rbj(capsys, "evidence", "attach", "e1", "--from", "m.json")
    assert code == 2 and env.errors[0].code == "E_OBJECT_INVALID"
    Path("m2.json").write_text(json.dumps({"x": 1, "d": {}, "g": {"i": {}}}), encoding="utf-8")
    code, env = rbj(capsys, "evidence", "attach", "e1", "--from", "m2.json")
    assert code == 0 and "d" in env.data["warnings"][0] and "g.i" in env.data["warnings"][0]
    Path("adir").mkdir()
    code, env = rbj(capsys, "evidence", "attach", "e1", "--from", "adir")
    assert code == 2 and env.errors[0].code == "E_FILE_NOT_FOUND"


def test_a_claim_written_after_its_evidence_says_so(lab, capsys):
    start(capsys)
    rbj(capsys, "evidence", "attach", "e1", "--metric", "m=0.5")
    code, env = rbj(capsys, "claim", "add", "fits the number", "--experiment", "e1", "--metric", "m", "--at-most", "0.6")
    obs = env.data["verdict"]["observations"][0]
    assert obs["post_hoc"] and any("written after" in c for c in env.data["verdict"]["conditions"])


def test_setup_changes_are_labelled_without_blaming_other_claims(lab, capsys):
    start(capsys)
    rbj(capsys, "claim", "add", "c", "--experiment", "e1", "--metric", "m", "--at-most", "1")
    rbj(capsys, "evidence", "attach", "e1", "--metric", "m=0.5", "--metric", "n=3")
    rbj(capsys, "claim", "add", "d", "--experiment", "e1", "--metric", "n", "--at-least", "1")
    code, env = rbj(capsys, "show", "c1")
    assert env.data["verdict"]["observations"][0]["spec"] == "current"     # a new claim does not change the setup
    rbj(capsys, "spec", "set", "e1", "bs", "8")
    code, env = rbj(capsys, "show", "c1")
    assert env.data["verdict"]["observations"][0]["spec"] == "changed_since"   # not "amended": nothing was frozen


def test_a_claim_source_must_state_its_number(lab, capsys):
    start(capsys)
    Path("paper.txt").write_text("Ours: 1.43 EPE on KITTI.\n", encoding="utf-8")
    code, env = rbj(capsys, "claim", "add", "p", "--experiment", "e1", "--metric", "epe", "--within", "1.5", "--tolerance", "0.1",
                    "--source", "paper.txt", "--quote", "1.43 EPE")
    assert code == 2 and env.errors[0].code == "E_SOURCE_UNRESOLVED"
    code, env = rbj(capsys, "claim", "add", "p", "--experiment", "e1", "--metric", "epe", "--within", "1.43", "--tolerance", "0.05",
                    "--source", "https://example.com/paper")
    assert code == 0 and any("did not check" in c for c in env.data["verdict"]["conditions"])


# ---------------------------------------------------------------- freezing, people's calls, the record


def test_a_frozen_spec_changed_by_hand_is_reported_and_blocks(lab, capsys):
    start(capsys)
    rbj(capsys, "claim", "add", "budget", "--experiment", "e1", "--metric", "delta", "--at-most", "0.05")
    rbj(capsys, "freeze", "e1")
    p = lab / ".rb" / "claims" / "c1.json"
    doc = json.loads(p.read_text())
    doc["target"] = 0.5
    p.write_text(json.dumps(doc))
    rbj(capsys, "evidence", "attach", "e1", "--metric", "delta=0.3")
    code, env = rbj(capsys, "status", "--fail-on", "unestablished")
    assert code == 1 and env.data["experiments"][0]["spec_drift"] and env.data["gate"]["stale"]
    assert not env.data["claims"][0]["established"]


def test_imported_settings_need_a_parent(lab, capsys):
    start(capsys)
    rbj(capsys, "spec", "set", "e1", "seed", "7")
    p = lab / ".rb" / "experiments" / "e1.json"
    doc = json.loads(p.read_text())
    doc["settings"][0]["status"] = "imported"
    p.write_text(json.dumps(doc))
    code, env = rbj(capsys, "status", "--fail-on", "unknown")
    assert code == 1 and "imported, but this investigation has no parent" in env.data["experiments"][0]["blocking"][0]
    doc["settings"][0]["value"] = None
    p.write_text(json.dumps(doc))
    code, env = rbj(capsys, "status")
    assert code == 2 and env.errors[0].code == "E_LEDGER_CORRUPT"


def test_amend_is_a_persons_call_and_only_when_it_amends(lab, capsys, monkeypatch):
    start(capsys)
    code, env = rbj(capsys, "spec", "set", "e1", "lr", "2e-4", "--amend", "because")
    assert code == 2 and "nothing to amend" in env.errors[0].message
    monkeypatch.setenv("RB_ACTOR", "agent:bot")
    code, env = rbj(capsys, "spec", "set", "e1", "lr", "2e-4", "--amend", "because")
    assert code == 2 and env.errors[0].code == "E_HUMAN_ONLY"
    monkeypatch.delenv("RB_ACTOR")
    rbj(capsys, "spec", "set", "e1", "seed", "--unknown")
    rbj(capsys, "freeze", "e1")
    code, env = rbj(capsys, "spec", "set", "e1", "seed", "--optional", "--amend", "not needed for this comparison")
    exp = Ledger(lab).load("experiment", "e1")
    assert code == 0 and "required -> optional" in exp.amendments[0].change
    entry = Ledger(lab).log_entries()[-1]
    assert entry["detail"]["amendment"]["reason"] == "not needed for this comparison" and "required -> optional" in entry["detail"]["change"]


def test_bad_input_is_exit_2_not_a_crash(lab, capsys):
    start(capsys)
    rbj(capsys, "evidence", "attach", "e1", "--metric", "m=1")
    for argv in (["evidence", "retract", "ev1", "--reason", ""], ["evidence", "retract", "ev1", "--reason", "r" * 1001],
                 ["show", "../investigation"], ["decide", "../claims/c1", "accept", "--why", "x"],
                 ["spec", "set", "e1", "q", "7", "--source", "run:x#/a", "--quote", "seven"]):
        code, env = rbj(capsys, *argv)
        assert code == 2 and env.errors[0].code in ("E_OBJECT_INVALID", "E_OBJECT_NOT_FOUND"), argv


def test_usage_errors_are_envelopes_and_negative_numbers_are_values(lab, capsys):
    start(capsys)
    for argv in (["spec"], ["decide", "c1", "maybe", "--why", "x"], ["decide", "c1", "accept"], ["evidence", "retract", "ev1"]):
        code, env = rbj(capsys, *argv)
        assert code == 2 and env.errors[0].code == "E_USAGE", argv
    code, env = rbj(capsys, "spec", "set", "e1", "wd", "-1e-4")
    assert code == 0 and env.data["setting"]["value"] == -1e-4
    code, env = rbj(capsys, "claim", "add", "x", "--experiment", "e1", "--metric", "change.m", "--at-most", "-1e-3")
    assert code == 0 and env.data["claim"]["target"] == -1e-3


def test_status_with_a_bad_gate_says_nothing_else(lab, capsys):
    start(capsys)
    code = main(["status", "--fail-on", "bogus"])
    out = capsys.readouterr()
    assert code == 2 and out.out == "" and "--fail-on takes" in out.err


def test_a_corrupt_log_is_reported_as_corrupt(lab, capsys):
    start(capsys)
    with (lab / ".rb" / "log.jsonl").open("a") as f:
        f.write("<<<<<<< HEAD\n")
    code, env = rbj(capsys, "context")
    assert code == 2 and env.errors[0].code == "E_LEDGER_CORRUPT" and "line" in env.errors[0].message


def _attach(args):
    root, i = args
    os.environ["RB_ACTOR"] = "agent:worker"
    return Ledger(Path(root)).attach_evidence("e1", metrics={"m": float(i)}).id


def test_concurrent_writers_lose_nothing(lab, capsys):
    start(capsys)
    ctx = multiprocessing.get_context("fork") if hasattr(os, "fork") else multiprocessing.get_context()
    with ctx.Pool(6) as pool:
        ids = pool.map(_attach, [(str(lab), i) for i in range(24)])
    assert len(set(ids)) == 24 and len(list((lab / ".rb" / "evidence").glob("*.json"))) == 24
    assert sum(1 for e in Ledger(lab).log_entries() if e["op"] == "attach") == 24
    assert not list((lab / ".rb").rglob("*.tmp"))


@needs_git
def test_receipts_before_the_first_commit_and_with_untracked_changes(lab, capsys):
    git_repo(lab)
    Path("f.txt").write_text("a", encoding="utf-8")
    start(capsys)
    code, env = rbj(capsys, "evidence", "attach", "e1", "--metric", "m=1")
    g = env.data["evidence"]["receipt"]["git"]
    assert g["commit"] is None and g["dirty"] and g["diff_sha256"]
    git("add", "f.txt")
    git("commit", "-qm", "c")
    Path("new_eval.py").write_text("v1", encoding="utf-8")
    first = rbj(capsys, "evidence", "attach", "e1", "--metric", "m=1")[1].data["evidence"]["receipt"]["git"]["diff_sha256"]
    Path("new_eval.py").write_text("v2", encoding="utf-8")
    second = rbj(capsys, "evidence", "attach", "e1", "--metric", "m=1")[1].data["evidence"]["receipt"]["git"]["diff_sha256"]
    assert first != second


def test_every_command_rb_suggests_exists(lab, capsys):
    """Open items, next hints and error fixes name commands an agent will run as written."""
    import re
    from rabbit_brain.cli import build_parser
    start(capsys)
    Path("cfg.yaml").write_text("lr: 1e-4\n", encoding="utf-8")
    rbj(capsys, "question", "add", "q")
    rbj(capsys, "spec", "set", "e1", "lr", "1e-4", "--source", "cfg.yaml:1")
    rbj(capsys, "spec", "set", "e1", "seed", "--unknown")
    rbj(capsys, "spec", "set", "e1", "b", "2", "--source", "cfg.yaml:1", "--verify")
    rbj(capsys, "claim", "add", "c", "--experiment", "e1", "--metric", "m", "--at-most", "1")
    code, env = rbj(capsys, "status")
    texts = [o["what"] for o in env.data["open"]] + list(env.next)
    for argv in (["experiment", "add", "x2"], ["claim", "add", "y", "--experiment", "e1", "--metric", "m", "--at-most", "1"], ["question", "add", "z"]):
        texts += rbj(capsys, *argv)[1].next
    parser = build_parser()
    names = {n for a in parser._subparsers._group_actions for n in a.choices}
    groups = {n: {s for a in (sub._subparsers._group_actions if sub._subparsers else []) for s in a.choices}
              for a in parser._subparsers._group_actions for n, sub in a.choices.items()}
    seen = 0
    for text in texts:
        for cmd, sub in re.findall(r"`?rb ([a-z-]+)(?: ([a-z-]+))?", text):
            seen += 1
            assert cmd in names, f"{text!r} names rb {cmd}"
            if groups.get(cmd):
                assert sub in groups[cmd], f"{text!r} names rb {cmd} {sub}"
    assert seen >= 5
