"""The research-state command surface (v2), end to end, as a researcher and an agent drive it with --json.

Promises pinned here:

- `rb init "<title>"` creates `.rb/` (with its .gitignore and a union-merge .gitattributes for log.jsonl), refuses a
  second one, and every later command finds `.rb/` from any subdirectory; without one, commands say how to start.
- Ids are the kind's prefix plus four random base32 characters, or the id given; links (question, hypothesis,
  experiment) must name objects that exist.
- Experiments hold variants with roles, what they vary on purpose, and settings; `<variant>.<setting>` is one variant's
  own value, and an undeclared difference between variants is a blocking confound.
- `rb spec set` records a setting from every source form (YAML/JSON/TOML key path, a YAML line turned into its key path,
  a quote, a run's JSON pointer, a prose line with --term) and verifies it when the source can be checked; a url or a
  note stays provisional without failing; a comment line never verifies; a source stating another value is a conflict
  that re-verifying never clears; a source outside the project is refused.
- `rb claim add` takes exactly one comparator; metric references (epe, int8.epe, candidate.epe, change.epe, aliases)
  read the right number from the evidence; verdict words are untested / supported / refuted / mixed for own claims
  and reproduced / not_reproduced / not_comparable for cited ones.
- `rb evidence attach` records numbers with a receipt, reads --from files, per-run values, refuses a silent duplicate
  and flags a deliberate one.
- Only a person freezes, decides, amends or retracts what something rests on; an agent is refused with a handoff.
- `rb status --fail-on` gates exit 1 only for the gates that trip; `rb compare`, `rb show`, `rb log`, `rb context` and
  `rb schema` return what they say; bad arguments are E_USAGE envelopes; every command a hint suggests exists.

Every test runs as a person (human:t) whatever environment runs pytest; agent cases set CLAUDECODE=1 themselves.
"""
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

from rabbit_brain.cli import build_parser, main
from rabbit_brain.models import Envelope

HAS_GIT = shutil.which("git") is not None
needs_git = pytest.mark.skipif(not HAS_GIT, reason="git is not installed")
AGENT_VARS = ("CLAUDECODE", "AI_AGENT", "GITHUB_ACTIONS", "RB_ACTOR")
PERSON = "human:t"

TRAIN_YAML = ("# training config\n"       # 1
              "model: raft\n"             # 2
              "optim:\n"                  # 3
              "  lr: 1e-4\n"              # 4
              "  betas: [0.9, 0.999]\n"   # 5
              "batch_size: 6\n"           # 6
              "small: false\n"            # 7
              "log_every: 50\n")          # 8
TRAIN_PY = ("import argparse\n"
            "# lr = 0.1 is too high\n"
            "parser.add_argument('--lr', default=0.0001)\n"
            "warmup_steps = 500\n")
PAPER = ("We train with a learning rate of 0.0001 and batch 6.\n"
         "Ours achieves 1.43 EPE on KITTI (Table 3).\n")


# ---------------------------------------------------------------- fixtures and helpers


def git(*args):
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout.strip()


def commit_all(message="work"):
    if HAS_GIT:
        git("add", "-A")
        git("-c", "commit.gpgsign=false", "commit", "-qm", message)


@pytest.fixture
def lab(workdir, monkeypatch):
    """An empty project that rb records as the person human:t, whatever environment runs the tests."""
    for key in list(os.environ):
        if key in AGENT_VARS or key.startswith("CODEX_"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("LOGNAME", "t")   # the fallback when git is missing still names the person t
    monkeypatch.setenv("USER", "t")
    if HAS_GIT:
        git("init", "-q")
        git("config", "user.email", "t@example.com")
        git("config", "user.name", "t")
    return workdir


@pytest.fixture
def project(lab):
    """A project with the files settings and cited numbers come from, committed when git is available."""
    Path("configs").mkdir()
    Path("configs/train.yaml").write_text(TRAIN_YAML, encoding="utf-8")
    Path("configs/eval.json").write_text(json.dumps({"data": {"crop": [368, 768]}, "iters": 12}), encoding="utf-8")
    Path("configs/train.toml").write_text("[train]\nepochs = 100\n", encoding="utf-8")
    Path("train.py").write_text(TRAIN_PY, encoding="utf-8")
    Path("paper.txt").write_text(PAPER, encoding="utf-8")
    Path("run1").mkdir()
    Path("run1/record.json").write_text(json.dumps({"seeds": {"torch": 1234}, "environment": {"cuda": "12.4"}}), encoding="utf-8")
    commit_all("sources")
    return lab


def rb(capsys, *argv):
    """Run rb with --json; return the exit code and the parsed envelope (exactly one JSON object on stdout)."""
    code = main([*argv, "--json"])
    return code, Envelope.model_validate_json(capsys.readouterr().out)


def ok(capsys, *argv) -> dict:
    code, env = rb(capsys, *argv)
    assert code == 0 and env.ok, (argv, [e.model_dump() for e in env.errors])
    return env.data


def refused(capsys, exit_code, error_code, *argv):
    code, env = rb(capsys, *argv)
    assert code == exit_code and not env.ok and env.errors[0].code == error_code, (argv, code, [e.model_dump() for e in env.errors])
    return env.errors[0].model_dump()


def start(capsys):
    return ok(capsys, "init", "INT8 quality")


def int8_experiment(capsys, *extra):
    """An investigation with experiment e1: fp32 (baseline) vs int8 (candidate), varying precision on purpose."""
    start(capsys)
    return ok(capsys, "experiment", "add", "INT8 vs FP32", "--id", "e1", "--baseline", "fp32", "--candidate", "int8",
              "--varies", "precision", *extra)["object"]


def verdict(capsys, claim_id) -> dict:
    return ok(capsys, "show", claim_id)["verdict"]


def log_entries(root: Path) -> list[dict]:
    return [json.loads(line) for line in (root / ".rb" / "log.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------------------------------------------------------------- init and finding .rb/


def test_init_creates_rb_with_its_git_files_and_logs_the_write(lab, capsys):
    data = start(capsys)
    inv = data["object"]
    assert inv["kind"] == "investigation" and inv["id"] == "int8-quality" and inv["title"] == "INT8 quality"
    assert inv["created_by"] == PERSON and data["actor"]["id"] == PERSON
    rbdir = lab / ".rb"
    assert set((rbdir / ".gitignore").read_text().split()) >= {".lock", "*.tmp"}
    assert "log.jsonl merge=union" in (rbdir / ".gitattributes").read_text()
    entry = log_entries(lab)[0]
    assert entry["op"] == "init" and entry["actor"] == PERSON and entry["via"] == "cli" and entry["actor_via"]
    assert entry["sha256"] == sha256_file(rbdir / "investigation.json")


def test_a_second_init_is_refused_here_and_below(lab, capsys, monkeypatch):
    start(capsys)
    refused(capsys, 2, "E_INVESTIGATION_EXISTS", "init", "again")
    Path("src/deep").mkdir(parents=True)
    monkeypatch.chdir("src/deep")
    refused(capsys, 2, "E_INVESTIGATION_EXISTS", "init", "nested")
    assert not (lab / "src" / "deep" / ".rb").exists()


def test_commands_from_a_subdirectory_find_rb(lab, capsys, monkeypatch):
    start(capsys)
    Path("src/deep").mkdir(parents=True)
    monkeypatch.chdir("src/deep")
    q = ok(capsys, "question", "add", "Does INT8 hold up?")["object"]
    assert (lab / ".rb" / "questions" / f"{q['id']}.json").exists()


def test_without_rb_a_command_says_how_to_start_one(lab, capsys):
    err = refused(capsys, 2, "E_NO_INVESTIGATION", "status")
    assert "rb init" in err["fix"] and "rb investigation" not in err["fix"], err["fix"]


# ---------------------------------------------------------------- ids and links


def test_ids_are_the_kind_prefix_and_four_random_characters(lab, capsys):
    int8_experiment(capsys)
    ids = {
        "q": ok(capsys, "question", "add", "q?")["object"]["id"],
        "h": ok(capsys, "hypothesis", "add", "h")["object"]["id"],
        "a": ok(capsys, "assumption", "add", "a")["object"]["id"],
        "e": ok(capsys, "experiment", "add", "x")["object"]["id"],
        "c": ok(capsys, "claim", "add", "c", "-e", "e1", "--metric", "int8.epe", "--at-most", "1")["object"]["id"],
        "ev": ok(capsys, "evidence", "attach", "e1", "int8.epe=0.5")["object"]["id"],
    }
    ids["d"] = ok(capsys, "decide", ids["h"], "investigate", "-m", "not sure yet")["object"]["id"]
    for prefix, id_ in ids.items():
        assert re.fullmatch(rf"{prefix}[a-z2-7]{{4}}", id_), (prefix, id_)
    assert len(set(ids.values())) == len(ids)


def test_a_chosen_id_is_kept_and_a_taken_one_is_refused(lab, capsys):
    start(capsys)
    assert ok(capsys, "question", "add", "q?", "--id", "q1")["object"]["id"] == "q1"
    err = refused(capsys, 2, "E_OBJECT_INVALID", "experiment", "add", "x", "--id", "q1")
    assert "already taken" in err["message"]


def test_a_hypothesis_links_to_its_question_and_a_missing_one_is_refused(lab, capsys):
    start(capsys)
    q = ok(capsys, "question", "add", "Can INT8 ship?")["object"]["id"]
    h = ok(capsys, "hypothesis", "add", "INT8 keeps quality", "--question", q, "-m", "quantisation noise is small")["object"]
    assert h["question"] == q and h["why"] == "quantisation noise is small" and h["status"] == "proposed"
    refused(capsys, 2, "E_OBJECT_NOT_FOUND", "hypothesis", "add", "h", "--question", "qnone")


def test_an_experiment_testing_a_hypothesis_makes_it_active(lab, capsys):
    start(capsys)
    h = ok(capsys, "hypothesis", "add", "INT8 keeps quality")["object"]["id"]
    e = ok(capsys, "experiment", "add", "INT8 vs FP32", "--hypothesis", h)["object"]
    assert e["hypotheses"] == [h]
    assert ok(capsys, "show", h)["object"]["status"] == "active"


def test_an_assumption_applies_to_existing_experiments_only(lab, capsys):
    int8_experiment(capsys)
    a = ok(capsys, "assumption", "add", "KITTI is not in the pretraining set", "-e", "e1")["object"]
    assert a["applies_to"] == ["e1"] and a["status"] == "open"
    refused(capsys, 2, "E_OBJECT_NOT_FOUND", "assumption", "add", "x", "-e", "enone")


# ---------------------------------------------------------------- experiments, variants, metrics


def test_experiment_add_records_variants_with_roles_and_what_varies(lab, capsys):
    start(capsys)
    e = ok(capsys, "experiment", "add", "INT8 vs FP32", "--id", "e1", "--baseline", "fp32", "--candidate", "int8",
           "--candidate", "int4", "--varies", "precision,bits")["object"]
    assert e["kind"] == "experiment" and e["id"] == "e1"
    assert [(v["name"], v["role"]) for v in e["variants"]] == [("fp32", "baseline"), ("int8", "candidate"), ("int4", "candidate")]
    assert e["varies"] == ["precision", "bits"]


def test_experiment_like_copies_shared_settings_as_provisional_and_what_it_varies(project, capsys):
    int8_experiment(capsys)
    ok(capsys, "spec", "set", "e1", "lr", "--source", "configs/train.yaml#optim.lr")
    e2 = ok(capsys, "experiment", "add", "rerun", "--id", "e2", "--like", "e1")["object"]
    [lr] = e2["settings"]
    assert lr["name"] == "lr" and lr["value"] == 0.0001 and lr["status"] == "provisional"
    assert lr["source"]["key"] == "optim.lr" and lr["source"]["resolved"] is None
    assert e2["varies"] == ["precision"]


def test_variant_add_adds_an_arm_with_its_role(lab, capsys):
    int8_experiment(capsys)
    v = ok(capsys, "variant", "add", "e1", "no-quant-head", "--role", "ablation")["object"]
    assert v["kind"] == "variant" and v["experiment_id"] == "e1" and v["name"] == "no-quant-head" and v["role"] == "ablation"
    variants = ok(capsys, "show", "e1")["object"]["variants"]
    assert ("no-quant-head", "ablation") in [(x["name"], x["role"]) for x in variants]


def test_an_experiment_compares_against_one_baseline(lab, capsys):
    int8_experiment(capsys)
    err = refused(capsys, 2, "E_OBJECT_INVALID", "variant", "add", "e1", "fp16", "--role", "baseline")
    assert "already has a baseline" in err["message"]


def test_metric_add_records_unit_direction_and_aliases(lab, capsys):
    start(capsys)
    m = ok(capsys, "metric", "add", "epe", "--unit", "px", "--minimize", "--alias", "endpoint_error")["object"]
    assert (m["kind"], m["id"], m["unit"], m["direction"], m["aliases"]) == ("metric", "epe", "px", "minimize", ["endpoint_error"])
    assert ok(capsys, "metric", "add", "acc", "--maximize")["object"]["direction"] == "maximize"
    assert ok(capsys, "metric", "add", "gpu_hours")["object"]["direction"] == "none"
    refused(capsys, 2, "E_OBJECT_INVALID", "metric", "add", "epe2", "--alias", "endpoint_error")


def test_a_claim_reads_evidence_reported_under_a_metric_alias(lab, capsys):
    int8_experiment(capsys)
    ok(capsys, "metric", "add", "epe", "--unit", "px", "--minimize", "--alias", "endpoint_error")
    ok(capsys, "claim", "add", "INT8 EPE under 1.5", "-e", "e1", "--metric", "int8.epe", "--at-most", "1.5", "--id", "c1")
    ok(capsys, "evidence", "attach", "e1", "int8.endpoint_error=1.2")
    [obs] = verdict(capsys, "c1")["observations"]
    assert obs["value"] == 1.2 and obs["role"] == "confirmatory"


# ---------------------------------------------------------------- spec set: every source form


@pytest.mark.parametrize("name, source, value, line", [
    ("lr", "configs/train.yaml#optim.lr", 0.0001, 4),          # YAML's 1e-4 is read as a number, not text
    ("crop", "configs/eval.json#data.crop", [368, 768], None),
    ("epochs", "configs/train.toml#train.epochs", 100, None),
], ids=["yaml", "json", "toml"])
def test_spec_set_reads_a_key_path_and_verifies_it(project, capsys, name, source, value, line):
    int8_experiment(capsys)
    data = ok(capsys, "spec", "set", "e1", name, "--source", source)     # no value given: rb reads it at the key
    s = data["object"]
    assert s["kind"] == "setting" and s["address"] == f"e1/{name}"
    assert s["value"] == value and s["status"] == "verified" and data["outcome"] == {"passed": True, "failures": []}
    assert s["source"]["resolved"]["line"] == line
    if HAS_GIT:
        assert s["source"]["resolved"]["commit"] == git("rev-parse", "HEAD")   # a committed, unmodified file is pinned


def test_spec_set_turns_a_yaml_line_into_its_key_path(project, capsys):
    int8_experiment(capsys)
    s = ok(capsys, "spec", "set", "e1", "batch_size", "6", "--source", "configs/train.yaml:6")["object"]
    assert s["source"]["key"] == "batch_size" and s["source"]["line"] is None and s["status"] == "verified"


def test_spec_set_verifies_a_quote_on_its_line(project, capsys):
    int8_experiment(capsys)
    s = ok(capsys, "spec", "set", "e1", "small", "false", "--source", "configs/train.yaml", "--quote", "small: false")["object"]
    assert s["value"] is False and s["status"] == "verified" and s["source"]["resolved"]["line"] == 7


@pytest.mark.parametrize("name, value, source, expected", [
    ("seeds.torch", None, "run:run1#/seeds/torch", 1234),                   # a run directory means its record.json
    ("cuda", '"12.4"', "run:run1/record.json#/environment/cuda", "12.4"),   # text that looks like a number stays text
], ids=["number", "text"])
def test_spec_set_verifies_a_value_at_a_run_pointer(project, capsys, name, value, source, expected):
    int8_experiment(capsys)
    code, env = rb(capsys, "spec", "set", "e1", name, *([value] if value else []), "--source", source)
    s = env.data["object"]
    assert s["value"] == expected and s["source"]["kind"] == "run"
    assert code == 0 and s["status"] == "verified" and s["conflict"] is None, env.data["outcome"]


def test_a_prose_line_verifies_only_with_the_settings_word(project, capsys):
    int8_experiment(capsys)
    code, env = rb(capsys, "spec", "set", "e1", "learning_rate", "0.0001", "--source", "paper.txt:1")
    assert code == 1 and env.data["object"]["status"] == "provisional"
    s = ok(capsys, "spec", "set", "e1", "learning_rate", "0.0001", "--source", "paper.txt:1", "--term", "learning rate")["object"]
    assert s["status"] == "verified" and s["source"]["term"] == "learning rate"


def test_a_comment_line_never_verifies(project, capsys):
    int8_experiment(capsys)
    code, env = rb(capsys, "spec", "set", "e1", "lr", "0.1", "--source", "train.py:2", "--term", "lr")
    [failure] = env.data["outcome"]["failures"]
    assert code == 1 and env.data["object"]["status"] == "provisional"
    assert failure["code"] == "E_SOURCE_UNRESOLVED" and "comment" in failure["reason"]
    assert 3 in [c["line"] for c in failure["candidates"]]     # it points at the line that does set lr


@pytest.mark.parametrize("source, kind", [("https://example.com/env", "url"), ("note:the authors said so in an issue", "note")], ids=["url", "note"])
def test_url_and_note_sources_stay_provisional_without_failing(project, capsys, source, kind):
    int8_experiment(capsys)
    data = ok(capsys, "spec", "set", "e1", "gpu", "A5000", "--source", source)
    assert data["object"]["status"] == "provisional" and data["object"]["source"]["kind"] == kind
    assert data["outcome"]["passed"]
    data = ok(capsys, "spec", "verify", "e1", "gpu")
    [row] = data["results"]
    assert row["skipped"] and row["code"] == "E_SOURCE_UNVERIFIABLE" and row["after"] == "provisional" and data["outcome"]["passed"]


def test_spec_set_unknown_records_a_setting_nobody_has_found(lab, capsys):
    int8_experiment(capsys)
    s = ok(capsys, "spec", "set", "e1", "seed", "--unknown")["object"]
    assert s["status"] == "unknown" and s["value"] is None and s["required"]
    st = ok(capsys, "status")
    assert st["gate"]["unknown"] and ("unknown", "e1/seed") in [(o["code"], o["subject"]) for o in st["open"]]


def test_a_per_run_setting_takes_its_value_from_each_evidence(lab, capsys):
    int8_experiment(capsys)
    s = ok(capsys, "spec", "set", "e1", "seed", "--per-run")["object"]
    assert s["per_run"] and s["value"] is None
    ev = ok(capsys, "evidence", "attach", "e1", "int8.epe=1.4", "--set", "seed=2")["object"]
    assert ev["per_run"] == {"seed": 2}
    err = refused(capsys, 2, "E_OBJECT_INVALID", "evidence", "attach", "e1", "int8.epe=1.4", "--set", "lr=2")
    assert "rb spec set e1 lr --per-run" in err["message"]


def test_spec_set_from_a_config_records_only_the_chosen_keys(project, capsys):
    int8_experiment(capsys)
    data = ok(capsys, "spec", "set", "e1", "--from", "configs/train.yaml", "--keys", "optim.*,model")
    got = {s["address"]: (s["value"], s["status"]) for s in data["object"]["settings"]}
    assert got == {"e1/optim.lr": (0.0001, "verified"), "e1/optim.betas": ([0.9, 0.999], "verified"), "e1/model": ("raft", "verified")}
    err = refused(capsys, 2, "E_OBJECT_INVALID", "spec", "set", "e1", "--from", "configs/train.yaml")
    assert "--keys" in err["message"]


def test_a_variant_setting_overrides_the_shared_one_for_that_variant(lab, capsys):
    int8_experiment(capsys)
    s = ok(capsys, "spec", "set", "e1", "int8.precision", "int8", "--source", "note:quantised export")["object"]
    assert s["address"] == "e1/int8.precision" and s["name"] == "precision"
    assert ok(capsys, "spec", "set", "e1", "precision", "fp16", "--variant", "fp32")["object"]["address"] == "e1/fp32.precision"
    exp = ok(capsys, "show", "e1")["object"]
    own = {v["name"]: {x["name"]: x["value"] for x in v["settings"]} for v in exp["variants"]}
    assert own == {"fp32": {"precision": "fp16"}, "int8": {"precision": "int8"}} and exp["settings"] == []
    assert ok(capsys, "show", "e1/int8.precision")["object"]["value"] == "int8"


def test_a_difference_between_variants_not_declared_in_varies_is_a_blocking_confound(lab, capsys):
    int8_experiment(capsys)
    ok(capsys, "spec", "set", "e1", "int8.precision", "int8", "--source", "note:x")
    ok(capsys, "spec", "set", "e1", "fp32.precision", "fp32", "--source", "note:x")
    assert not [c for c in ok(capsys, "show", "e1")["caveats"] if c["code"] == "confound"]    # declared: the question itself
    ok(capsys, "spec", "set", "e1", "int8.calib_batches", "64", "--source", "note:x")
    [confound] = [c for c in ok(capsys, "show", "e1")["caveats"] if c["code"] == "confound"]
    assert confound["blocks"] and "calib_batches" in confound["subject"]


# ---------------------------------------------------------------- spec verify


def test_spec_set_no_verify_hints_spec_verify_which_verifies_it(project, capsys):
    int8_experiment(capsys)
    code, env = rb(capsys, "spec", "set", "e1", "warmup_steps", "500", "--source", "train.py:4", "--no-verify")
    assert code == 0 and env.data["object"]["status"] == "provisional" and env.next == ["rb spec verify e1 warmup_steps"]
    data = ok(capsys, "spec", "verify", "e1", "warmup_steps")
    [row] = data["results"]
    assert row["after"] == "verified" and row["read"] == "warmup_steps = 500" and row["line"] == 4 and data["verified_count"] == 1


def test_spec_verify_exits_1_and_keeps_a_conflict_until_the_value_changes(project, capsys):
    int8_experiment(capsys)
    code, env = rb(capsys, "spec", "set", "e1", "batch_size", "16", "--source", "configs/train.yaml#batch_size")
    assert code == 1 and env.data["object"]["conflict"] is not None
    code, env = rb(capsys, "spec", "verify", "e1", "batch_size")
    [row] = env.data["results"]
    assert code == 1 and env.ok and not env.data["outcome"]["passed"]
    assert row["conflict"] and row["after"] == "provisional" and "is 6, not 16" in row["reason"]
    s = ok(capsys, "spec", "set", "e1", "batch_size", "6", "--source", "configs/train.yaml#batch_size")["object"]
    assert s["status"] == "verified" and s["conflict"] is None


def test_a_source_outside_the_project_is_refused(lab, capsys, tmp_path_factory):
    int8_experiment(capsys)
    elsewhere = tmp_path_factory.mktemp("elsewhere") / "cfg.yaml"
    elsewhere.write_text("lr: 0.1\n", encoding="utf-8")
    code, env = rb(capsys, "spec", "set", "e1", "lr", "0.1", "--source", f"{elsewhere}#lr")
    [failure] = env.data["outcome"]["failures"]
    assert code == 1 and failure["code"] == "E_SOURCE_OUTSIDE" and env.data["object"]["status"] == "provisional"


# ---------------------------------------------------------------- claims


@pytest.mark.parametrize("flags, comparator, criterion", [
    (["--at-most", "1.5"], "at_most", "int8.epe ≤ 1.5"),
    (["--at-least", "1"], "at_least", "int8.epe ≥ 1"),
    (["--equals", "1.43", "--tolerance", "0.05"], "equals", "int8.epe = 1.43 ± 0.05"),
    (["--at-most", "-1e-3"], "at_most", "int8.epe ≤ -0.001"),     # a negative number is a value, not an option
], ids=["at-most", "at-least", "equals", "negative-target"])
def test_claim_add_records_each_comparator(lab, capsys, flags, comparator, criterion):
    int8_experiment(capsys)
    data = ok(capsys, "claim", "add", "INT8 EPE", "-e", "e1", "--metric", "int8.epe", *flags)
    assert data["object"]["kind"] == "claim" and data["object"]["comparator"] == comparator and data["object"]["origin"] == "own"
    assert data["verdict"]["criterion"] == criterion and data["verdict"]["status"] == "untested"


@pytest.mark.parametrize("flags", [["--at-most", "1", "--at-least", "0"], ["--equals", "1"], ["--at-most", "1", "--tolerance", "0.1"], []],
                         ids=["two-criteria", "equals-without-tolerance", "tolerance-without-equals", "none"])
def test_a_claim_needs_exactly_one_criterion(lab, capsys, flags):
    int8_experiment(capsys)
    refused(capsys, 2, "E_OBJECT_INVALID", "claim", "add", "x", "-e", "e1", "--metric", "int8.epe", *flags)
    assert not (lab / ".rb" / "claims").exists() or not list((lab / ".rb" / "claims").glob("*.json"))


@pytest.mark.parametrize("metric, expected", [("epe", 1.42), ("int8.epe", 1.42), ("candidate.epe", 1.42),
                                              ("baseline.epe", 1.38), ("change.epe", 1.42 - 1.38)])
def test_a_metric_reference_reads_the_right_number(lab, capsys, metric, expected):
    int8_experiment(capsys)
    ok(capsys, "claim", "add", "reads", "-e", "e1", "--metric", metric, "--at-most", "5", "--id", "c1")
    ok(capsys, "evidence", "attach", "e1", "fp32.epe=1.38", "int8.epe=1.42")
    [obs] = verdict(capsys, "c1")["observations"]
    assert obs["value"] == pytest.approx(expected)


# ---------------------------------------------------------------- verdict words


@pytest.mark.parametrize("cited, values, word", [
    (False, [], "untested"),
    (False, ["1.2"], "supported"),
    (False, ["1.8"], "refuted"),
    (False, ["1.2", "1.8"], "mixed"),
    (True, [], "untested"),
    (True, ["1.44"], "reproduced"),
    (True, ["1.60"], "not_reproduced"),
])
def test_the_verdict_word_for_own_and_cited_claims(project, capsys, cited, values, word):
    int8_experiment(capsys)
    if cited:
        data = ok(capsys, "claim", "add", "Ours: 1.43 EPE on KITTI", "-e", "e1", "--metric", "int8.epe", "--equals", "1.43",
                  "--tolerance", "0.05", "--source", "paper.txt", "--quote", "achieves 1.43 EPE", "--locator", "Table 3", "--id", "c1")
        assert data["object"]["origin"] == "cited" and data["object"]["source"]["resolved"]["line"] == 2
    else:
        ok(capsys, "claim", "add", "INT8 EPE under 1.5", "-e", "e1", "--metric", "int8.epe", "--at-most", "1.5", "--id", "c1")
    for x in values:
        ok(capsys, "evidence", "attach", "e1", f"int8.epe={x}")
    v = verdict(capsys, "c1")
    assert v["status"] == word and v["n"] == len(values)


def test_a_supported_claim_a_person_wrote_on_a_clean_experiment_is_established(lab, capsys):
    int8_experiment(capsys)
    ok(capsys, "claim", "add", "INT8 EPE under 1.5", "-e", "e1", "--metric", "int8.epe", "--at-most", "1.5", "--id", "c1")
    data = ok(capsys, "evidence", "attach", "e1", "int8.epe=1.2")
    [v] = data["verdicts"]
    assert v["status"] == "supported" and v["established"] and v["not_established_because"] == []


def test_a_cited_claim_whose_file_does_not_state_the_target_is_refused(project, capsys):
    int8_experiment(capsys)
    refused(capsys, 2, "E_SOURCE_UNRESOLVED", "claim", "add", "Ours: 1.5", "-e", "e1", "--metric", "int8.epe", "--equals", "1.5",
            "--tolerance", "0.05", "--source", "paper.txt", "--quote", "achieves 1.43 EPE")


def test_a_cited_value_that_differs_from_ours_makes_the_cited_claim_not_comparable(project, capsys):
    int8_experiment(capsys)
    ok(capsys, "spec", "set", "e1", "iters", "--source", "configs/eval.json#iters", "--cited", "32")
    ok(capsys, "claim", "add", "Ours: 1.43", "-e", "e1", "--metric", "int8.epe", "--equals", "1.43", "--tolerance", "0.05",
       "--source", "paper.txt", "--quote", "achieves 1.43 EPE", "--id", "c1")
    ok(capsys, "evidence", "attach", "e1", "int8.epe=1.44")
    v = verdict(capsys, "c1")
    assert v["status"] == "not_comparable" and "not_comparable" in v["not_established_because"]


# ---------------------------------------------------------------- evidence


@needs_git
def test_evidence_records_its_numbers_and_a_receipt(lab, capsys):
    Path("README.md").write_text("x\n", encoding="utf-8")
    commit_all()
    int8_experiment(capsys)
    Path("out.txt").write_text("predictions\n", encoding="utf-8")
    ev = ok(capsys, "evidence", "attach", "e1", "fp32.epe=1.38", "int8.epe=1.42", "--command", "python eval.py --int8",
            "--commit", "abc1234", "--artifact", "out.txt", "--link", "wandb=https://wandb.ai/x/y")["object"]
    assert ev["experiment"] == "e1" and ev["metrics"] == {"fp32.epe": 1.38, "int8.epe": 1.42} and ev["basis"] == "typed"
    r = ev["receipt"]
    assert r["actor"] == PERSON and r["command"] == "python eval.py --int8"
    assert r["produced"] == {"commit": "abc1234", "from": "flag"}
    assert r["attached"]["commit"] == git("rev-parse", "HEAD") and r["attached"]["branch"]
    assert ev["files"][0]["path"] == "out.txt" and ev["files"][0]["sha256"] == sha256_file(lab / "out.txt")
    assert ev["links"] == {"wandb": "https://wandb.ai/x/y"}


def test_evidence_variant_names_bare_numbers_for_that_variant(lab, capsys):
    int8_experiment(capsys)
    assert ok(capsys, "evidence", "attach", "e1", "epe=1.2", "--variant", "int8")["object"]["metrics"] == {"int8.epe": 1.2}
    refused(capsys, 2, "E_OBJECT_NOT_FOUND", "evidence", "attach", "e1", "epe=1.2", "--variant", "int4")


def test_evidence_from_a_json_file_reads_nested_numbers_and_names_the_rest(lab, capsys):
    int8_experiment(capsys)
    Path("metrics.json").write_text(json.dumps({"int8": {"epe": 1.3, "fl_all": 5.1}, "model": "raft"}), encoding="utf-8")
    data = ok(capsys, "evidence", "attach", "e1", "--from", "metrics.json")
    ev = data["object"]
    assert ev["metrics"] == {"int8.epe": 1.3, "int8.fl_all": 5.1} and ev["basis"] == "file"
    assert ev["files"][0]["path"] == "metrics.json" and ev["files"][0]["sha256"] == sha256_file(lab / "metrics.json")
    assert any("model" in w for w in data["warnings"])


def test_identical_evidence_is_recognised_not_attached_twice(lab, capsys):
    int8_experiment(capsys)
    first = ok(capsys, "evidence", "attach", "e1", "int8.epe=1.4")["object"]["id"]
    data = ok(capsys, "evidence", "attach", "e1", "int8.epe=1.4")
    assert data["duplicate_of"] == first
    assert len(list((lab / ".rb" / "evidence").glob("*.json"))) == 1
    assert [e["op"] for e in log_entries(lab)].count("attach") == 1


def test_a_deliberate_repeat_needs_a_reason_and_is_marked(lab, capsys):
    int8_experiment(capsys)
    first = ok(capsys, "evidence", "attach", "e1", "int8.epe=1.4")["object"]["id"]
    refused(capsys, 2, "E_OBJECT_INVALID", "evidence", "attach", "e1", "int8.epe=1.4", "--again")
    ev = ok(capsys, "evidence", "attach", "e1", "int8.epe=1.4", "--again", "-m", "rerun on another GPU")["object"]
    assert ev["id"] != first and ev["note"].startswith("attached again: rerun on another GPU")


def test_evidence_attach_names_as_unclaimed_only_the_numbers_no_claim_reads(lab, capsys):
    int8_experiment(capsys)
    ok(capsys, "metric", "add", "epe", "--alias", "endpoint_error")
    ok(capsys, "claim", "add", "INT8 costs under 0.1 EPE", "-e", "e1", "--metric", "change.epe", "--at-most", "0.1")
    data = ok(capsys, "evidence", "attach", "e1", "fp32.endpoint_error=1.38", "int8.endpoint_error=1.42", "int8.latency_ms=25")
    status_says = [o["what"] for o in ok(capsys, "status")["open"] if o["code"] == "unclaimed_metric"]
    assert len(status_says) == 1 and "int8.latency_ms" in status_says[0] and "endpoint_error" not in status_says[0]
    assert data["unclaimed_metrics"] == ["int8.latency_ms"]     # change.epe reads both variants' epe, by alias


def test_evidence_numbers_are_name_equals_number(lab, capsys):
    int8_experiment(capsys)
    refused(capsys, 2, "E_USAGE", "evidence", "attach", "e1", "epe")
    refused(capsys, 2, "E_OBJECT_INVALID", "evidence", "attach", "e1", "epe=abc")
    refused(capsys, 2, "E_OBJECT_INVALID", "evidence", "attach", "e1")


# ---------------------------------------------------------------- people's calls, and agents


def _agent_refusal_setup(capsys, monkeypatch):
    int8_experiment(capsys)
    ok(capsys, "claim", "add", "INT8 EPE under 1.5", "-e", "e1", "--metric", "int8.epe", "--at-most", "1.5", "--id", "c1")
    ev = ok(capsys, "evidence", "attach", "e1", "int8.epe=1.2")["object"]["id"]
    monkeypatch.setenv("CLAUDECODE", "1")
    return ev


@pytest.mark.parametrize("argv", [["freeze", "e1", "-m", "pre-register"], ["decide", "c1", "accept", "-m", "looks fine"],
                                  ["retract", "{ev}", "-m", "wrong normalisation"]])
def test_an_agent_is_refused_a_persons_call_with_a_handoff(lab, capsys, monkeypatch, argv):
    ev = _agent_refusal_setup(capsys, monkeypatch)
    argv = [a.format(ev=ev) for a in argv]
    before = len(log_entries(lab))
    err = refused(capsys, 2, "E_HUMAN_ONLY", *argv)
    assert err["handoff"]["who"] == "person" and shlex.split(err["handoff"]["command"])[:len(argv) + 1] == ["rb", *argv]
    assert err["handoff"]["command"] in err["fix"] and "agent:claude-code" in err["message"]
    assert len(log_entries(lab)) == before


def test_an_agents_writes_are_recorded_as_the_agent(lab, capsys, monkeypatch):
    int8_experiment(capsys)
    monkeypatch.setenv("CLAUDECODE", "1")
    data = ok(capsys, "evidence", "attach", "e1", "int8.epe=1.2")
    assert data["actor"] == {"id": "agent:claude-code", "via": "detected:CLAUDECODE"}
    r = data["object"]["receipt"]
    assert r["actor"] == "agent:claude-code" and r["actor_via"] == "detected:CLAUDECODE"
    assert log_entries(lab)[-1]["actor"] == "agent:claude-code"


def test_a_person_named_inside_an_agent_session_is_still_the_agent(lab, capsys, monkeypatch):
    """A person's name typed inside an agent session is what an agent would type: rb ignores it, records the agent, and
    says so, so the person's call is handed over like any other."""
    _agent_refusal_setup(capsys, monkeypatch)
    monkeypatch.setenv("RB_ACTOR", "human:jh")
    err = refused(capsys, 2, "E_HUMAN_ONLY", "decide", "c1", "accept", "-m", "within budget")
    assert "RB_ACTOR=human:jh is ignored inside a Claude Code session" in err["message"]
    data = ok(capsys, "question", "add", "who wrote this?")
    assert data["actor"]["id"] == "agent:claude-code" and data["actor"]["ignored_rb_actor"] == "human:jh"
    assert log_entries(lab)[-1]["actor"] == "agent:claude-code" and log_entries(lab)[-1]["ignored_rb_actor"] == "human:jh"


def test_a_frozen_spec_changes_only_with_a_persons_amendment(lab, capsys):
    int8_experiment(capsys)
    ok(capsys, "spec", "set", "e1", "lr", "0.1")
    frozen = ok(capsys, "freeze", "e1", "-m", "pre-register")["object"]["frozen"]
    assert frozen["by"] == PERSON and frozen["why"] == "pre-register"
    refused(capsys, 2, "E_FROZEN", "spec", "set", "e1", "lr", "0.2")
    refused(capsys, 2, "E_FROZEN", "claim", "add", "late", "-e", "e1", "--metric", "int8.epe", "--at-most", "1")
    refused(capsys, 2, "E_OBJECT_INVALID", "spec", "set", "e1", "lr", "0.2", "--amend")
    ok(capsys, "spec", "set", "e1", "lr", "0.2", "--amend", "-m", "the config was fixed before any held-out run")
    [amendment] = ok(capsys, "show", "e1")["object"]["amendments"]
    assert amendment["why"] == "the config was fixed before any held-out run" and "0.1 -> 0.2" in amendment["change"]


@pytest.mark.parametrize("kind, outcome, status", [("hypothesis", "reject", "rejected"), ("question", "accept", "answered"),
                                                   ("assumption", "reject", "violated"), ("assumption", "accept", "assumed")])
def test_a_decision_moves_the_status_of_what_it_decides(lab, capsys, kind, outcome, status):
    start(capsys)
    subject = ok(capsys, kind, "add", "something")["object"]["id"]
    d = ok(capsys, "decide", subject, outcome, "-m", "because")["object"]
    assert d["kind"] == "decision" and d["subject"] == subject and d["by"] == PERSON
    assert ok(capsys, "show", subject)["object"]["status"] == status


def test_a_persons_accept_on_a_setting_vouches_for_it(lab, capsys):
    int8_experiment(capsys)
    ok(capsys, "spec", "set", "e1", "lr", "0.1")
    ok(capsys, "claim", "add", "INT8 EPE under 1.5", "-e", "e1", "--metric", "int8.epe", "--at-most", "1.5", "--id", "c1")
    ok(capsys, "evidence", "attach", "e1", "int8.epe=1.2")
    assert verdict(capsys, "c1")["not_established_because"] == ["provisional"]
    d = ok(capsys, "decide", "e1/lr", "accept", "-m", "read it in the launch script myself")["object"]
    assert d["subject"] == "e1/lr" and d["value"] == 0.1
    v = verdict(capsys, "c1")
    assert v["established"] and ("vouched", False) in [(c["code"], c["blocks"]) for c in v["caveats"]]


def test_retracting_evidence_keeps_it_on_record_and_stops_it_counting(lab, capsys):
    int8_experiment(capsys)
    ok(capsys, "claim", "add", "INT8 EPE under 1.5", "-e", "e1", "--metric", "int8.epe", "--at-most", "1.5", "--id", "c1")
    ev = ok(capsys, "evidence", "attach", "e1", "int8.epe=1.2")["object"]["id"]
    refused(capsys, 2, "E_USAGE", "retract", ev)
    data = ok(capsys, "retract", ev, "-m", "wrong input normalisation")
    assert data["object"] == {"kind": "evidence", "id": ev, "retracted": {"why": "wrong input normalisation"}}
    assert [ch["claim_id"] for ch in data["verdict_changes"]] == ["c1"]
    kept = json.loads((lab / ".rb" / "evidence" / f"{ev}.json").read_text(encoding="utf-8"))
    assert kept["retracted"]["why"] == "wrong input normalisation" and kept["retracted"]["by"] == PERSON
    assert verdict(capsys, "c1")["status"] == "untested"


# ---------------------------------------------------------------- status and its gates


def _gate_state(capsys):
    """c1 refuted and undecided, c2 untested, c3 a cited number not reproduced, seed unknown; no verified source."""
    int8_experiment(capsys)
    ok(capsys, "spec", "set", "e1", "seed", "--unknown")
    ok(capsys, "claim", "add", "INT8 EPE under 1.0", "-e", "e1", "--metric", "int8.epe", "--at-most", "1.0", "--id", "c1")
    ok(capsys, "claim", "add", "fast", "-e", "e1", "--metric", "int8.latency_ms", "--at-most", "10", "--id", "c2")
    ok(capsys, "claim", "add", "Ours: 1.43", "-e", "e1", "--metric", "int8.epe", "--equals", "1.43", "--tolerance", "0.01",
       "--source", "paper.txt", "--quote", "achieves 1.43 EPE", "--id", "c3")
    ok(capsys, "evidence", "attach", "e1", "int8.epe=1.5")


@pytest.mark.parametrize("gate, exit_code", [("unestablished", 1), ("untested", 1), ("refuted", 1), ("not_reproduced", 1),
                                             ("undecided", 1), ("unknown", 1), ("stale", 0)])
def test_status_fail_on_exits_1_only_for_a_gate_that_trips(project, capsys, gate, exit_code):
    _gate_state(capsys)
    code, env = rb(capsys, "status", "--fail-on", gate)
    assert code == exit_code and env.ok and env.data["outcome"]["passed"] == (exit_code == 0)
    assert env.data["gate"][gate] == (exit_code == 1)


def test_status_fail_on_takes_several_gates_and_refuses_an_unknown_one(project, capsys):
    _gate_state(capsys)
    code, env = rb(capsys, "status", "--fail-on", "stale,refuted")
    assert code == 1 and [f["code"] for f in env.data["outcome"]["failures"]] == ["refuted"]
    refused(capsys, 2, "E_OBJECT_INVALID", "status", "--fail-on", "bogus")


def test_status_without_fail_on_reports_and_exits_0(project, capsys):
    _gate_state(capsys)
    st = ok(capsys, "status")
    assert st["counts"]["claim_count"] == 3 and st["counts"]["experiment_count"] == 1 and st["counts"]["evidence_count"] == 1
    assert st["tally"] == {"refuted": 1, "untested": 1, "not_reproduced": 1}
    assert ("person", "undecided", "c1") in [(o["who"], o["code"], o["subject"]) for o in st["open"]]
    assert st["actor"]["id"] == PERSON


def test_status_fail_on_stale_trips_when_a_verified_source_changes(project, capsys):
    int8_experiment(capsys)
    ok(capsys, "spec", "set", "e1", "lr", "--source", "configs/train.yaml#optim.lr")
    assert rb(capsys, "status", "--fail-on", "stale")[0] == 0
    Path("configs/train.yaml").write_text(TRAIN_YAML.replace("1e-4", "3e-4"), encoding="utf-8")
    code, env = rb(capsys, "status", "--fail-on", "stale")
    assert code == 1 and ("conflict", "e1/lr") in [(o["code"], o["subject"]) for o in env.data["open"]]


def test_status_for_one_experiment_shows_only_its_claims(lab, capsys):
    int8_experiment(capsys)
    ok(capsys, "experiment", "add", "other", "--id", "e2")
    ok(capsys, "claim", "add", "on e1", "-e", "e1", "--metric", "int8.epe", "--at-most", "1", "--id", "c1")
    ok(capsys, "claim", "add", "on e2", "-e", "e2", "--metric", "epe", "--at-most", "1", "--id", "c2")
    st = ok(capsys, "status", "e1")
    assert st["experiment_id"] == "e1" and [c["claim"] for c in st["claims"]] == ["c1"] and [e["id"] for e in st["experiments"]] == ["e1"]


# ---------------------------------------------------------------- compare, show, log, context, schema


def test_compare_gives_means_and_changes_against_the_baseline_in_each_metrics_direction(lab, capsys):
    int8_experiment(capsys)
    ok(capsys, "metric", "add", "epe", "--unit", "px", "--minimize")
    ok(capsys, "metric", "add", "acc", "--maximize")
    ok(capsys, "evidence", "attach", "e1", "fp32.epe=1.30", "int8.epe=1.50", "fp32.acc=0.90", "int8.acc=0.95", "gpu_hours=3")
    ok(capsys, "evidence", "attach", "e1", "fp32.epe=1.34", "int8.epe=1.54")
    t = ok(capsys, "compare", "e1")
    assert t["baseline"] == "fp32" and t["evidence_count"] == 2
    rows = {r["variant"]: r["metrics"] for r in t["rows"]}
    assert rows["fp32"]["epe"] == {"mean": pytest.approx(1.32), "n": 2}
    assert rows["int8"]["epe"]["mean"] == pytest.approx(1.52) and rows["int8"]["epe"]["delta"] == pytest.approx(0.2)
    assert rows["int8"]["epe"]["better"] is False                 # a higher EPE is worse
    assert rows["int8"]["acc"]["delta"] == pytest.approx(0.05) and rows["int8"]["acc"]["better"] is True
    assert t["overall"] == {"gpu_hours": {"mean": 3.0, "n": 1}}
    assert {m["name"]: m["unit"] for m in t["metrics"]} == {"epe": "px", "acc": ""}


def _one_of_each(capsys) -> dict:
    int8_experiment(capsys)
    ids = {"experiment": "e1"}
    ids["question"] = ok(capsys, "question", "add", "Can INT8 ship?")["object"]["id"]
    ids["hypothesis"] = ok(capsys, "hypothesis", "add", "INT8 keeps quality")["object"]["id"]
    ids["assumption"] = ok(capsys, "assumption", "add", "KITTI is clean", "-e", "e1")["object"]["id"]
    ids["metric"] = ok(capsys, "metric", "add", "epe", "--minimize")["object"]["id"]
    ids["claim"] = ok(capsys, "claim", "add", "INT8 EPE under 1.5", "-e", "e1", "--metric", "int8.epe", "--at-most", "1.5")["object"]["id"]
    ids["evidence"] = ok(capsys, "evidence", "attach", "e1", "int8.epe=1.2")["object"]["id"]
    ids["decision"] = ok(capsys, "decide", ids["claim"], "accept", "-m", "ship it")["object"]["id"]
    return ids


@pytest.mark.parametrize("kind", ["question", "hypothesis", "assumption", "experiment", "metric", "claim", "evidence", "decision"])
def test_show_returns_each_kind_of_object_named_by_its_kind(lab, capsys, kind):
    ids = _one_of_each(capsys)
    data = ok(capsys, "show", ids[kind])
    assert data["object"]["kind"] == kind and data["object"]["id"] == ids[kind]
    if kind == "claim":
        assert data["verdict"]["status"] == "supported" and data["decisions"][0]["id"] == ids["decision"]
    if kind == "experiment":
        assert data["spec_sha256"] and data["evidence_ids"] == [ids["evidence"]] and data["claims"][0]["claim"] == ids["claim"]


def test_show_a_setting_address_gives_the_setting_and_its_history(project, capsys):
    int8_experiment(capsys)
    ok(capsys, "spec", "set", "e1", "lr", "--source", "configs/train.yaml#optim.lr")
    ok(capsys, "spec", "set", "e1", "lr", "3e-4")
    data = ok(capsys, "show", "e1/lr")
    s = data["object"]
    assert s["kind"] == "setting" and s["address"] == "e1/lr" and s["experiment_id"] == "e1"
    assert s["value"] == 0.0003 and s["status"] == "provisional" and s["source"] is None   # the old source stated the old value
    assert [h["op"] for h in data["history"]] == ["spec_set", "spec_verify", "spec_set"]
    assert "0.0001 -> 0.0003" in data["history"][-1]["detail"]["change"]


@pytest.mark.parametrize("argv", [["show", "zzz"], ["show", "e1/nope"], ["spec", "set", "enone", "lr", "1"],
                                  ["claim", "add", "x", "-e", "enone", "--metric", "m", "--at-most", "1"],
                                  ["evidence", "attach", "enone", "m=1"], ["decide", "cnone", "accept", "-m", "x"]])
def test_an_id_that_does_not_exist_is_E_OBJECT_NOT_FOUND(lab, capsys, argv):
    int8_experiment(capsys)
    before = len(log_entries(lab))
    refused(capsys, 2, "E_OBJECT_NOT_FOUND", *argv)
    assert len(log_entries(lab)) == before


@pytest.mark.parametrize("argv", [["variant", "add", "e1", "x", "--role", "boss"], ["claim", "add", "x", "--metric", "m", "--at-most", "abc"],
                                  ["decide", "e1", "accept"], ["frobnicate"], ["spec"]])
def test_a_bad_argument_is_an_E_USAGE_envelope(lab, capsys, argv):
    int8_experiment(capsys)
    err = refused(capsys, 2, "E_USAGE", *argv)
    assert err["message"] and "--help" in err["fix"]


def test_log_records_every_write_with_its_actor_and_the_hash_written(lab, capsys):
    int8_experiment(capsys)
    ok(capsys, "claim", "add", "INT8 EPE under 1.5", "-e", "e1", "--metric", "int8.epe", "--at-most", "1.5", "--id", "c1")
    ev = ok(capsys, "evidence", "attach", "e1", "int8.epe=1.2")["object"]["id"]
    entries = ok(capsys, "log", "-n", "2")["entries"]
    assert [(e["op"], e["kind"], e["id"]) for e in entries] == [("add", "claim", "c1"), ("attach", "evidence", ev)]
    for e in entries:
        assert e["actor"] == PERSON and e["actor_via"] and e["via"] == "cli"
    assert entries[-1]["sha256"] == sha256_file(lab / ".rb" / "evidence" / f"{ev}.json")


def test_log_filters_by_object_and_by_setting(lab, capsys):
    int8_experiment(capsys)
    ok(capsys, "spec", "set", "e1", "lr", "0.1")
    ok(capsys, "spec", "set", "e1", "seed", "--unknown")
    ok(capsys, "claim", "add", "INT8 EPE under 1.5", "-e", "e1", "--metric", "int8.epe", "--at-most", "1.5", "--id", "c1")
    assert [(e["op"], e["id"]) for e in ok(capsys, "log", "c1")["entries"]] == [("add", "c1")]
    about_lr = ok(capsys, "log", "e1/lr")["entries"]
    assert [(e["op"], e["detail"]["setting"]) for e in about_lr] == [("spec_set", "lr")]
    assert [e["op"] for e in ok(capsys, "log", "e1")["entries"]] == ["add", "spec_set", "spec_set"]


def test_context_is_the_markdown_handoff_with_its_sections(project, capsys):
    int8_experiment(capsys)
    ok(capsys, "question", "add", "Can INT8 ship?")
    ok(capsys, "spec", "set", "e1", "lr", "--source", "configs/train.yaml#optim.lr")
    ok(capsys, "claim", "add", "INT8 EPE under 1.5", "-e", "e1", "--metric", "int8.epe", "--at-most", "1.5", "--id", "c1")
    ok(capsys, "claim", "add", "INT8 EPE under 1.0", "-e", "e1", "--metric", "int8.epe", "--at-most", "1.0", "--id", "c2")
    ok(capsys, "evidence", "attach", "e1", "int8.epe=1.2")
    data = ok(capsys, "context")
    md = data["markdown"]
    assert md.startswith("# Research state: INT8 quality")
    sections = {s.split("\n", 1)[0]: s for s in md.split("\n## ")[1:]}
    for heading in ("Rules", "Established", "Not established", "Needs a person", "Agent can do", "Experiment e1: INT8 vs FP32", "Recent activity"):
        assert heading in sections, heading
    assert "c1" in sections["Established"] and "c2" not in sections["Established"]
    assert "c2" in sections["Not established"] and "rb decide c2" in sections["Needs a person"]
    assert "Can INT8 ship?" in sections["Agent can do"]
    assert "| setting | value | status | checked in |" in sections["Experiment e1: INT8 vs FP32"]
    assert "| lr | 0.0001 | verified | configs/train.yaml#optim.lr |" in sections["Experiment e1: INT8 vs FP32"]
    assert data["state"]["claims"] and data["state"]["open"]


def test_doctor_in_a_project_with_rb_checks_the_research_state(lab, capsys):
    int8_experiment(capsys)
    data = ok(capsys, "doctor")
    assert data["checks"][0]["check"] == "actor" and PERSON in data["checks"][0]["detail"]
    assert data["outcome"] == {"passed": True, "failures": []}


def test_schema_prints_each_research_objects_json_schema(lab, capsys):
    for name in ("investigation", "question", "hypothesis", "assumption", "experiment", "metric", "claim", "evidence", "decision", "verdict"):
        data = ok(capsys, "schema", name)
        assert data["title"] and ("status" if name == "verdict" else "id") in data["properties"], name


# ---------------------------------------------------------------- every suggested command exists


RB_COMMAND = re.compile(r"(?<![\w./-])rb ([a-z][a-z-]*)(?: ([a-z][a-z-]*))?")


def _subparsers(parser) -> dict:
    return {n: p for a in (parser._subparsers._group_actions if parser._subparsers else []) for n, p in a.choices.items()}


def _check_commands(texts: list[str]) -> int:
    """Each `rb <command> [<sub>]` named in a text exists, and each --flag after it is one that command takes."""
    top = _subparsers(build_parser())
    seen = 0
    for text in texts:
        matches = list(RB_COMMAND.finditer(text))
        for i, m in enumerate(matches):
            cmd, sub = m.group(1), m.group(2)
            assert cmd in top, f"{text!r} names rb {cmd}"
            leaf = top[cmd]
            subs = _subparsers(leaf)
            if subs:
                assert sub in subs, f"{text!r} names rb {cmd} {sub}"
                leaf = subs[sub]
            segment = text[m.start():matches[i + 1].start() if i + 1 < len(matches) else len(text)]
            options = {o for action in leaf._actions for o in action.option_strings}
            for flag in re.findall(r"(?<![\w-])(--[a-z][a-z-]*)", segment):
                assert flag in options, f"{text!r}: rb {cmd} {sub or ''} has no {flag}"
            seen += 1
    return seen


def test_every_command_status_context_and_next_suggest_exists(project, capsys, monkeypatch):
    nexts = []

    def keep(*argv):
        code, env = rb(capsys, *argv)
        assert env.ok, (argv, [e.model_dump() for e in env.errors])
        nexts.extend(env.next)
        return env.data

    keep("init", "INT8 quality")
    keep("question", "add", "Can INT8 ship?")
    h = keep("hypothesis", "add", "INT8 keeps quality")["object"]["id"]
    keep("assumption", "add", "KITTI is clean")
    keep("experiment", "add", "INT8 vs FP32", "--id", "e1", "--baseline", "fp32", "--candidate", "int8", "--varies", "precision")
    keep("experiment", "add", "again", "--id", "e2", "--like", "e1")
    keep("variant", "add", "e1", "int4", "--role", "candidate")
    keep("spec", "set", "e1", "lr", "0.0001", "--source", "configs/train.yaml:4", "--no-verify")   # the rb spec verify hint
    keep("spec", "set", "e1", "wd", "0.01")                                                     # provisional, no source
    keep("spec", "set", "e1", "seed", "--unknown")
    keep("spec", "set", "e1", "batch_size", "16", "--source", "configs/train.yaml#batch_size")  # a conflict
    keep("spec", "set", "e1", "int8.calib", "64")                                               # a confound
    keep("spec", "set", "e1", "trial", "--per-run")
    keep("claim", "add", "INT8 EPE under 1.0", "-e", "e1", "--metric", "int8.epe", "--at-most", "1.0", "--hypothesis", h)
    keep("claim", "add", "twice", "-e", "e1", "--metric", "int8.epe", "--at-most", "5", "--min-n", "3")
    keep("claim", "add", "untested", "-e", "e1", "--metric", "int8.latency_ms", "--at-most", "10")
    keep("evidence", "attach", "e1", "int8.epe=1.5", "fp32.fl_all=5.1", "--set", "trial=1")
    monkeypatch.setenv("CLAUDECODE", "1")
    keep("claim", "add", "agent's", "-e", "e1", "--metric", "int8.epe", "--at-most", "2")   # criterion not fixed by a person
    monkeypatch.delenv("CLAUDECODE")
    keep("freeze", "e2", "-m", "pre-register")
    st = keep("status")
    ctx = keep("context")
    hints = [o["do"] for o in st["open"]] + nexts
    assert "rb spec verify e1 lr" in hints
    codes = {o["code"] for o in st["open"]}
    assert {"unknown", "provisional", "conflict", "confound", "undecided", "untested", "too_few_runs", "open_question",
            "unchecked_assumption", "criterion_not_fixed_by_person", "unclaimed_metric"} <= codes, codes
    backticked = re.findall(r"`(rb [^`]*)`", ctx["markdown"])
    assert _check_commands(hints + backticked) >= 25


@pytest.mark.parametrize("frozen", [False, True])
def test_the_confound_hint_run_as_written_clears_the_confound(lab, capsys, frozen):
    int8_experiment(capsys)
    ok(capsys, "spec", "set", "e1", "int8.calib", "64", "--source", "note:export script")
    ok(capsys, "claim", "add", "INT8 EPE under 1.5", "-e", "e1", "--metric", "int8.epe", "--at-most", "1.5", "--id", "c1")
    if frozen:
        ok(capsys, "freeze", "e1", "-m", "pre-register")
    [item] = [o for o in ok(capsys, "status")["open"] if o["code"] == "confound"]
    pattern = r'rb decide \S+ accept --why "\.\.\."' if frozen else r"rb spec vary \S+ \S+"
    hint = re.search(pattern, item["do"])
    assert hint, item["do"]
    command = hint.group(0).replace('"..."', '"calibration does not change what is compared"')
    ok(capsys, *shlex.split(command)[1:])
    blocking = [c for c in ok(capsys, "show", "e1")["caveats"] if c["code"] == "confound" and c["blocks"]]
    assert blocking == [], (command, blocking)


def test_evidence_from_a_results_file_takes_only_the_keys_asked_for(project, capsys):
    """A results file also holds per-case numbers; --keys takes the summary a claim reads, and the file is hashed whole."""
    int8_experiment(capsys)
    Path("results.json").write_text(json.dumps({"summary": {"delta_epe": 0.0486, "n": 100, "ok": True},
                                                "cases": {"a": {"epe": 1.0}, "b": {"epe": 2.0}}}), encoding="utf-8")
    data = ok(capsys, "evidence", "attach", "e1", "--from", "results.json", "--keys", "summary.*")
    assert data["object"]["metrics"] == {"summary.delta_epe": 0.0486, "summary.n": 100}
    assert data["object"]["files"][0]["path"] == "results.json"
    refused(capsys, 2, "E_OBJECT_INVALID", "evidence", "attach", "e1", "--from", "results.json", "--keys", "nothing.*")


def test_a_code_line_gives_its_value_when_none_is_typed(project, capsys):
    int8_experiment(capsys)
    Path("rule.py").write_text("THETA = 0.7017  # frozen on the configuration half\nZ = 1.645\n", encoding="utf-8")
    s = ok(capsys, "spec", "set", "e1", "stop.theta", "--source", "rule.py:1")["object"]
    assert s["value"] == 0.7017 and s["status"] == "verified"
    refused(capsys, 2, "E_SOURCE_UNRESOLVED", "spec", "set", "e1", "stop.alpha", "--source", "rule.py:2")


def test_rb_output_piped_into_head_exits_quietly(project, capsys):
    int8_experiment(capsys)
    import subprocess, sys
    r = subprocess.run(f"{sys.executable} -m rabbit_brain.cli status | head -1", shell=True, capture_output=True, text=True)
    assert "Traceback" not in r.stderr and r.stdout.strip()
