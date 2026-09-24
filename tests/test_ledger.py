"""Research state (.rb/): the rules the objects cannot apply to themselves, driven through the CLI the way an agent drives it.

The seams pinned here are the product's promises: only the tool verifies, a changed source stops being verified, an
unknown required setting keeps a claim from reading as established, a frozen spec changes only with a recorded reason,
an agent cannot decide, freeze or retract, and nothing is dropped silently."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from rabbit_brain.cli import main
from rabbit_brain.investigation import Setting, Source
from rabbit_brain.ledger import Ledger
from rabbit_brain.models import Envelope
from rabbit_brain.sources import states

HAS_GIT = shutil.which("git") is not None


@pytest.fixture
def lab(workdir, monkeypatch):
    """An empty project with a config file, as a git repository when git is available."""
    monkeypatch.delenv("RB_ACTOR", raising=False)
    monkeypatch.setenv("USER", "jhet")
    Path("configs").mkdir()
    Path("configs/train.yaml").write_text("model: raft\nlr: 1e-4\nbatch_size: 6\nsmall: false\n", encoding="utf-8")
    if HAS_GIT:
        for cmd in (["init", "-q"], ["config", "user.email", "t@example.com"], ["config", "user.name", "t"], ["add", "."], ["commit", "-qm", "init"]):
            subprocess.run(["git", *cmd], check=True, capture_output=True)
    return workdir


def rbj(capsys, *argv):
    code = main([*argv, "--json"])
    out = capsys.readouterr().out
    return code, Envelope.model_validate_json(out)


def started(capsys, *extra):
    code, env = rbj(capsys, "investigation", "init", "INT8 quality", *extra)
    assert code == 0 and env.ok, env.errors
    return env


def experiment(capsys):
    started(capsys)
    code, env = rbj(capsys, "experiment", "add", "INT8 vs FP32", "--id", "int8", "--baseline", "fp32", "--candidate", "int8")
    assert code == 0 and env.data["experiment"]["id"] == "int8"


# ---------------------------------------------------------------- the store


def test_init_writes_plain_files_and_refuses_a_second_one(lab, capsys):
    env = started(capsys)
    assert env.command == "investigation init" and (lab / ".rb" / "investigation.json").exists()
    inv = json.loads((lab / ".rb" / "investigation.json").read_text())
    assert inv["id"] == "int8-quality" and inv["created_by"] == "human:jhet"
    code, env = rbj(capsys, "investigation", "init", "again")
    assert code == 2 and env.errors[0].code == "E_INVESTIGATION_EXISTS"
    log = [json.loads(line) for line in (lab / ".rb" / "log.jsonl").read_text().splitlines()]
    assert log[0]["op"] == "init" and log[0]["actor"] == "human:jhet"


def test_commands_find_the_investigation_from_a_subdirectory(lab, capsys, monkeypatch):
    started(capsys)
    Path("src/deep").mkdir(parents=True)
    monkeypatch.chdir("src/deep")
    code, env = rbj(capsys, "question", "add", "Does INT8 hold up?")
    assert code == 0 and (lab / ".rb" / "questions" / "q1.json").exists()


def test_no_investigation_says_how_to_start_one(workdir, capsys):
    code, env = rbj(capsys, "status")
    assert code == 2 and env.errors[0].code == "E_NO_INVESTIGATION" and "rb investigation init" in env.errors[0].fix


def test_ids_count_up_per_kind_and_never_collide(lab, capsys):
    started(capsys)
    for _ in range(2):
        rbj(capsys, "question", "add", "q")
    code, env = rbj(capsys, "hypothesis", "add", "h", "--question", "q2")
    assert env.data["hypothesis"]["id"] == "h1" and env.data["hypothesis"]["question"] == "q2"
    code, env = rbj(capsys, "experiment", "add", "x", "--id", "q1")
    assert code == 2 and env.errors[0].code == "E_OBJECT_INVALID" and "already taken" in env.errors[0].message
    code, env = rbj(capsys, "hypothesis", "add", "h", "--question", "q9")
    assert code == 2 and env.errors[0].code == "E_OBJECT_NOT_FOUND"


def test_an_experiment_testing_a_hypothesis_makes_it_active(lab, capsys):
    started(capsys)
    rbj(capsys, "hypothesis", "add", "INT8 keeps quality", "--expect", "delta <= 0.05")
    rbj(capsys, "experiment", "add", "INT8 vs FP32", "--tests", "h1")
    code, env = rbj(capsys, "show", "h1")
    assert env.data["hypothesis"]["status"] == "active"


def test_a_hand_edited_verified_knob_is_refused(lab, capsys):
    experiment(capsys)
    p = lab / ".rb" / "experiments" / "int8.json"
    doc = json.loads(p.read_text())
    doc["settings"] = [{"name": "lr", "value": 0.0001, "status": "verified", "required": True, "source": {"kind": "file", "path": "configs/train.yaml", "line": 2},
                     "note": "", "set_by": "agent:x", "set_at": "2026-09-24T00:00:00+00:00"}]
    p.write_text(json.dumps(doc))
    code, env = rbj(capsys, "show", "int8")
    assert code == 2 and env.errors[0].code == "E_LEDGER_CORRUPT" and any("Only `rb spec verify` sets verified" in pr for pr in env.errors[0].model_dump()["problems"])


# ---------------------------------------------------------------- settings: only the tool verifies


def test_a_proposed_value_is_inferred_and_verify_reads_the_line(lab, capsys):
    experiment(capsys)
    code, env = rbj(capsys, "spec", "set", "int8", "lr", "0.0001", "--source", "configs/train.yaml:2")
    assert code == 0 and env.data["setting"]["status"] == "provisional" and env.next == ["rb spec verify int8 lr"]
    code, env = rbj(capsys, "spec", "verify", "int8", "lr")
    row = env.data["results"][0]
    assert code == 0 and row["ok"] and row["after"] == "verified" and row["read"] == "lr: 1e-4" and row["line"] == 2
    setting = Ledger(lab).load("experiment", "int8").setting("lr")
    assert setting.status == "verified" and setting.source.resolved.sha256
    if HAS_GIT:
        assert len(setting.source.resolved.commit) == 40   # a tracked, unmodified file is pinned to HEAD


def test_a_source_that_does_not_state_the_value_leaves_it_inferred(lab, capsys):
    experiment(capsys)
    code, env = rbj(capsys, "spec", "set", "int8", "batch_size", "8", "--source", "configs/train.yaml:3", "--verify")
    row = env.data["verify"][0]
    assert code == 1 and not row["ok"] and row["code"] == "E_SOURCE_UNRESOLVED" and "reads: batch_size: 6" in row["reason"]
    assert env.data["setting"]["status"] == "provisional"


def test_quote_search_and_optional_commit(lab, capsys):
    experiment(capsys)
    code, env = rbj(capsys, "spec", "set", "int8", "small", "false", "--source", "configs/train.yaml", "--quote", "small: false", "--verify")
    assert code == 0 and env.data["setting"]["status"] == "verified" and env.data["verify"][0]["line"] == 4
    code, env = rbj(capsys, "spec", "set", "int8", "model", "raft", "--source", "configs/train.yaml", "--verify")
    assert code == 1 and env.data["verify"][0]["code"] == "E_SOURCE_UNVERIFIABLE"
    if HAS_GIT:
        head = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
        Path("configs/train.yaml").write_text("lr: 3e-4\n", encoding="utf-8")   # the working tree moves on; the commit does not
        code, env = rbj(capsys, "spec", "set", "int8", "lr", "1e-4", "--source", "configs/train.yaml:2", "--commit", head[:10], "--verify")
        assert code == 0 and env.data["setting"]["status"] == "verified" and env.data["setting"]["source"]["resolved"]["commit"] == head


def test_url_and_note_sources_are_recorded_but_cannot_verify(lab, capsys):
    experiment(capsys)
    code, env = rbj(capsys, "spec", "set", "int8", "cuda", "12.4", "--source", "https://example.com/env", "--verify")
    assert code == 1 and env.data["setting"]["status"] == "provisional" and env.data["verify"][0]["code"] == "E_SOURCE_UNVERIFIABLE"
    code, env = rbj(capsys, "spec", "set", "int8", "gpu", "A5000", "--source", "note:the authors said so in an issue")
    assert code == 0 and env.data["setting"]["source"]["kind"] == "note"


def test_a_run_source_reads_a_json_pointer(lab, capsys):
    experiment(capsys)
    Path("run1").mkdir()
    Path("run1/record.json").write_text(json.dumps({"seeds": {"torch": 1234}, "environment": {"cuda": "12.4"}}), encoding="utf-8")
    code, env = rbj(capsys, "spec", "set", "int8", "seed", "1234", "--source", "run:run1#/seeds/torch", "--verify")
    assert code == 0 and env.data["setting"]["status"] == "verified"
    code, env = rbj(capsys, "spec", "set", "int8", "cuda", '"12.4"', "--source", "run:run1/record.json#/environment/cuda", "--verify")
    assert code == 0 and env.data["setting"]["value"] == "12.4" and env.data["setting"]["status"] == "verified"
    code, env = rbj(capsys, "spec", "set", "int8", "seed", "99", "--source", "run:run1#/seeds/torch", "--verify")
    assert code == 1 and "is 1234, not 99" in env.data["verify"][0]["reason"]


def test_changing_a_value_or_its_file_ends_verified(lab, capsys):
    experiment(capsys)
    rbj(capsys, "spec", "set", "int8", "lr", "1e-4", "--source", "configs/train.yaml:2", "--verify")
    code, env = rbj(capsys, "spec", "set", "int8", "lr", "1e-4", "--note", "same value, same source")
    assert env.data["setting"]["status"] == "verified"          # nothing about the value changed
    code, env = rbj(capsys, "spec", "set", "int8", "lr", "3e-4")
    assert env.data["setting"]["status"] == "provisional" and env.data["setting"]["source"] is None   # the old source stated the old value
    rbj(capsys, "spec", "set", "int8", "lr", "1e-4", "--source", "configs/train.yaml:2", "--verify")
    Path("configs/train.yaml").write_text("model: raft\nlr: 1e-4 \nbatch_size: 6\nsmall: false\n", encoding="utf-8")
    code, env = rbj(capsys, "status")
    assert env.data["experiments"][0]["stale"] == ["lr"] and env.data["gate"]["stale"]
    assert any("no longer checks out" in o["what"] for o in env.data["open"])
    Path("configs/train.yaml").write_text("lr: 3e-4\n", encoding="utf-8")
    code, env = rbj(capsys, "spec", "verify", "int8")
    assert code == 1 and env.data["results"][0]["after"] == "provisional"   # re-verification that fails demotes it


def test_value_parsing_and_numeric_matching():
    assert states("lr: 0.0001", 1e-4) and states("lr = 1e-4", 0.0001) and not states("lr: 1e-3", 1e-4)
    assert states("batch_size: 6", 6) and not states("batch_size: 16", 6)
    assert states("amp: True", True) and not states("amp: false", True)
    assert states("betas: [0.9, 0.999]", [0.9, 0.999]) and states("optimizer: adamw", "adamw")
    with pytest.raises(ValueError):
        Setting(name="lr", value=None, status="provisional", set_by="human:x", set_at="t")
    with pytest.raises(ValueError):
        Source(kind="run", path="x")


# ---------------------------------------------------------------- claims: computed, never asserted


def claim_setup(capsys):
    experiment(capsys)
    code, env = rbj(capsys, "claim", "add", "INT8 keeps quality", "--experiment", "int8", "--metric", "delta_epe", "--at-most", "0.05")
    assert code == 0 and env.data["verdict"]["status"] == "not_tested"


def test_verdict_from_evidence_with_borderline(lab, capsys):
    claim_setup(capsys)
    code, env = rbj(capsys, "evidence", "attach", "int8", "--metric", "delta_epe=0.0486", "--command", "python eval.py --int8")
    assert code == 0 and env.data["verdicts"][0]["status"] == "supported" and env.data["verdicts"][0]["established"]
    obs = env.data["verdicts"][0]["observations"][0]
    assert obs["holds"] and obs["borderline"] and abs(obs["margin"] - 0.0014) < 1e-9
    ev = env.data["evidence"]
    assert ev["receipt"]["command"] == "python eval.py --int8" and ev["receipt"]["actor"] == "human:jhet"
    if HAS_GIT:
        assert ev["receipt"]["git"]["dirty"] is False   # .rb/ itself does not make the tree dirty
    rbj(capsys, "evidence", "attach", "int8", "--metric", "delta_epe=0.09")
    code, env = rbj(capsys, "show", "c1")
    assert env.data["verdict"]["status"] == "contested" and not env.data["verdict"]["established"]


def test_an_unknown_required_knob_keeps_a_passing_claim_unestablished(lab, capsys):
    claim_setup(capsys)
    rbj(capsys, "spec", "set", "int8", "seed", "--unknown")
    rbj(capsys, "evidence", "attach", "int8", "--metric", "delta_epe=0.01")
    code, env = rbj(capsys, "show", "c1")
    v = env.data["verdict"]
    assert v["status"] == "supported" and not v["established"] and v["blocking"] == ["seed"]
    code, env = rbj(capsys, "status", "--fail-on", "unestablished")
    assert code == 1 and env.data["failed"]
    rbj(capsys, "spec", "set", "int8", "seed", "--unknown", "--optional")
    code, env = rbj(capsys, "status", "--fail-on", "unestablished,unknown")
    assert code == 0 and env.data["claims"][0]["established"] and "seed unknown (optional)" in env.data["claims"][0]["conditions"]


def test_source_claims_reproduce_or_diverge(lab, capsys):
    experiment(capsys)
    Path("paper.txt").write_text("Table 3. Ours achieves 1.43 EPE on KITTI.\n", encoding="utf-8")
    code, env = rbj(capsys, "claim", "add", "Method obtains EPE 1.43 on KITTI", "--experiment", "int8", "--metric", "epe", "--within", "1.43",
                    "--tolerance", "0.05", "--source", "paper.txt", "--quote", "achieves 1.43 EPE", "--locator", "Table 3")
    assert code == 0 and env.data["claim"]["origin"] == "source"
    rbj(capsys, "evidence", "attach", "int8", "--metric", "epe=1.462")
    code, env = rbj(capsys, "show", "c1")
    assert env.data["verdict"]["status"] == "reproduced"
    rbj(capsys, "evidence", "attach", "int8", "--metric", "epe=1.60")
    code, env = rbj(capsys, "evidence", "retract", "ev1", "--reason", "wrong input normalisation")
    code, env = rbj(capsys, "show", "c1")
    assert env.data["verdict"]["status"] == "diverged" and env.data["verdict"]["retracted"] == ["ev1"]
    assert json.loads((lab / ".rb" / "evidence" / "ev1.json").read_text())["retracted"]["reason"] == "wrong input normalisation"   # kept, not deleted


def test_claims_need_exactly_one_criterion(lab, capsys):
    experiment(capsys)
    code, env = rbj(capsys, "claim", "add", "x", "--metric", "m", "--at-most", "1", "--at-least", "0")
    assert code == 2 and env.errors[0].code == "E_OBJECT_INVALID"
    code, env = rbj(capsys, "claim", "add", "x", "--metric", "m", "--within", "1")
    assert code == 2 and "tolerance" in env.errors[0].message


def test_evidence_from_an_rb_run_and_from_a_json_file(lab, capsys):
    claim_setup(capsys)
    code, env = rbj(capsys, "example")
    run_id = env.run_id
    code, env = rbj(capsys, "evidence", "attach", "int8", "--run", run_id)
    ev = env.data["evidence"]
    assert code == 0 and ev["kind"] == "rb_run" and ev["synthetic"] and ev["run"] == f"rb-runs/{run_id}"
    assert "candidate.mean_endpoint_error" in ev["metrics"] and ev["metrics"]["regressions"] == 3
    assert ev["receipt"]["run_record"]["record_sha256"] and ev["receipt"]["run_record"]["source"] == "example"
    rbj(capsys, "claim", "add", "No regressions", "--experiment", "int8", "--metric", "regressions", "--at-most", "0")
    code, env = rbj(capsys, "show", "c2")
    assert env.data["verdict"]["status"] == "refuted" and any("synthetic" in c for c in env.data["verdict"]["conditions"])
    Path("m.json").write_text(json.dumps({"kitti": {"epe": 1.46, "fl_all": 5.1}, "model": "raft", "ok": True}), encoding="utf-8")
    code, env = rbj(capsys, "evidence", "attach", "int8", "--from", "m.json", "--link", "wandb=https://wandb.ai/x/y")
    ev = env.data["evidence"]
    assert ev["metrics"] == {"kitti.epe": 1.46, "kitti.fl_all": 5.1} and ev["files"][0]["path"] == "m.json" and ev["links"]["wandb"].startswith("https://")
    assert "model" in " ".join(env.data.get("warnings", [])) and env.data["metrics_no_claim_reads"] == ["kitti.epe", "kitti.fl_all"]


# ---------------------------------------------------------------- freezing and people's calls


def test_freeze_blocks_spec_changes_until_amended(lab, capsys):
    claim_setup(capsys)
    rbj(capsys, "spec", "set", "int8", "lr", "1e-4", "--source", "configs/train.yaml:2")
    rbj(capsys, "evidence", "attach", "int8", "--metric", "delta_epe=0.01")
    code, env = rbj(capsys, "freeze", "int8")
    frozen = env.data["frozen"]["sha256"]
    assert code == 0
    code, env = rbj(capsys, "spec", "set", "int8", "lr", "3e-4")
    assert code == 2 and env.errors[0].code == "E_FROZEN"
    code, env = rbj(capsys, "claim", "add", "tighter", "--experiment", "int8", "--metric", "delta_epe", "--at-most", "0.02")
    assert code == 2 and env.errors[0].code == "E_FROZEN" and not (lab / ".rb" / "claims" / "c2.json").exists()
    code, env = rbj(capsys, "spec", "verify", "int8", "lr")
    assert code == 0   # verification does not change the spec, so it needs no amendment
    code, env = rbj(capsys, "spec", "set", "int8", "lr", "3e-4", "--amend", "the config was updated before any held-out run")
    exp = Ledger(lab).load("experiment", "int8")
    assert code == 0 and exp.frozen.sha256 == frozen and len(exp.amendments) == 1 and exp.amendments[0].before == frozen
    assert "the config was updated" in exp.amendments[0].reason
    code, env = rbj(capsys, "show", "c1")
    assert env.data["verdict"]["observations"][0]["spec"] == "before_freeze"
    rbj(capsys, "evidence", "attach", "int8", "--metric", "delta_epe=0.02")
    code, env = rbj(capsys, "show", "c1")
    assert [o["spec"] for o in env.data["verdict"]["observations"]] == ["before_freeze", "current"]


def test_agents_propose_and_people_decide(lab, capsys, monkeypatch):
    claim_setup(capsys)
    rbj(capsys, "evidence", "attach", "int8", "--metric", "delta_epe=0.2")
    monkeypatch.setenv("RB_ACTOR", "agent:claude-code")
    code, env = rbj(capsys, "spec", "set", "int8", "seed", "--unknown")
    assert code == 0 and env.data["setting"]["set_by"] == "agent:claude-code"
    for argv in (["decide", "c1", "accept", "--why", "looks fine"], ["freeze", "int8"], ["evidence", "retract", "ev1", "--reason", "x"]):
        code, env = rbj(capsys, *argv)
        assert code == 2 and env.errors[0].code == "E_HUMAN_ONLY", argv
    monkeypatch.setenv("RB_ACTOR", "robot")
    code, env = rbj(capsys, "status")
    assert code == 2 and env.errors[0].code == "E_ACTOR_INVALID"
    monkeypatch.delenv("RB_ACTOR")
    code, env = rbj(capsys, "decide", "c1", "accept", "--why", "the regression is inside our release budget")
    assert code == 0 and env.data["decision"]["verdict"] == "refuted" and env.data["decision"]["by"] == "human:jhet"
    code, env = rbj(capsys, "status")
    assert not any(o["id"] == "c1" and "nobody has decided" in o["what"] for o in env.data["open"])
    assert env.data["claims"][0]["decision"].startswith("accept by human:jhet")


def test_decisions_move_hypotheses_questions_and_assumptions(lab, capsys):
    started(capsys)
    rbj(capsys, "question", "add", "q")
    rbj(capsys, "hypothesis", "add", "h")
    rbj(capsys, "assumption", "add", "KITTI train is not in the pretraining set")
    for subject, outcome, field, expected in (("h1", "reject", "hypothesis", "rejected"), ("q1", "accept", "question", "answered"), ("a1", "reject", "assumption", "violated")):
        rbj(capsys, "decide", subject, outcome, "--why", "because")
        code, env = rbj(capsys, "show", subject)
        assert env.data[field]["status"] == expected


# ---------------------------------------------------------------- handoff


def test_status_and_context_are_the_handoff(lab, capsys):
    claim_setup(capsys)
    rbj(capsys, "question", "add", "Can INT8 ship?")
    rbj(capsys, "spec", "set", "int8", "seed", "--unknown")
    rbj(capsys, "spec", "set", "int8", "cuda", "12.4", "--source", "https://example.com/env")
    code, env = rbj(capsys, "status", "--fail-on", "untested")
    assert code == 1 and env.data["gate"]["untested"] and env.data["gate"]["unknown"]
    whats = [o["what"] for o in env.data["open"]]
    assert "q1 is open: Can INT8 ship?" in whats and "int8: seed is unknown and required" in whats
    assert any("cuda = 12.4 is provisional; find a file or run" in w for w in whats) and any("c1 is not tested" in w for w in whats)
    code, env = rbj(capsys, "context")
    md = env.data["markdown"]
    for heading in ("## Rules", "## Established", "## Not established", "## Open: work on these", "## Experiments", "## Recent activity"):
        assert heading in md
    assert "Only `rb spec verify` makes a setting verified" in md and "RB_ACTOR=agent:" in md
    code, env = rbj(capsys, "status", "--fail-on", "bogus")
    assert code == 2 and env.errors[0].code == "E_OBJECT_INVALID"


def test_every_write_is_logged_with_its_actor(lab, capsys, monkeypatch):
    claim_setup(capsys)
    monkeypatch.setenv("RB_ACTOR", "agent:codex")
    rbj(capsys, "evidence", "attach", "int8", "--metric", "delta_epe=0.01")
    log = Ledger(lab).log_entries()
    assert [e["op"] for e in log] == ["init", "add", "add", "attach"]
    assert log[-1]["actor"] == "agent:codex" and log[-1]["detail"]["metrics"] == ["delta_epe"]


def test_schemas_for_every_object(capsys):
    for name in ("investigation", "question", "hypothesis", "assumption", "experiment", "claim", "evidence", "decision", "verdict"):
        code = main(["schema", name, "--json"])
        env = Envelope.model_validate_json(capsys.readouterr().out)
        assert code == 0 and env.data.get("title"), name


def test_the_documented_walkthrough_runs(lab, capsys):
    """The research-state block in AGENTS.md, run line by line, does what the comments next to each line say."""
    import re
    import shlex
    doc = (Path(__file__).resolve().parents[1] / "AGENTS.md").read_text(encoding="utf-8")
    section = doc.split("## Research state:", 1)[1]
    block = re.search(r"```sh\n(.*?)```", section, re.S).group(1)
    Path("configs/train.yaml").write_text("".join(f"# {i}\n" for i in range(1, 17)) + "lr: 1e-4\n", encoding="utf-8")
    expected_exit = {"spec verify": 1, "status": 1}   # the url-sourced setting cannot verify; seed is unknown, so the gate fails
    ran = 0
    for line in block.splitlines():
        cmd = line.split("  #")[0].strip()
        if not cmd or "<run_id>" in cmd:
            continue
        argv = shlex.split(cmd)[1:]
        code = main(argv)
        capsys.readouterr()
        key = " ".join(argv[:2]) if argv[0] in ("spec",) else argv[0]
        assert code == expected_exit.get(key, 0), f"{cmd} exited {code}"
        ran += 1
    assert ran >= 14
    v = Ledger(lab).verdict(Ledger(lab).load("claim", "c1"))
    assert v.status == "supported" and not v.established and v.blocking == ["seed"] and any(o.borderline for o in v.observations)
