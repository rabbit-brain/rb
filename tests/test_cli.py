"""End to end through the command line, the way an agent would drive it."""
import json
import os
import re
from pathlib import Path

import pytest

from rabbit_brain.cli import main
from rabbit_brain.models import Envelope


def run(capsys, *argv):
    code = main(list(argv))
    out = capsys.readouterr()
    return code, out.out, out.err


def run_json(capsys, *argv):
    code, out, err = run(capsys, *argv, "--json")
    env = Envelope.model_validate_json(out)  # exactly one JSON object on stdout
    return code, env, err


def test_agent_flow(workdir, capsys):
    code, env, _ = run_json(capsys, "example")
    assert code == 0 and env.ok and env.command == "example" and env.run_id
    run_id = env.run_id
    assert env.data["summary"]["regressions"] == 3 and env.data["summary"]["flagged"] == 5
    assert env.data["verdict"]["start"] == "aisle-042" and env.next[0].startswith(f"rb findings {run_id}")
    for name in ("bundle.json", "record.json", "findings.json", "report.md"):
        assert (workdir / "rb-runs" / run_id / name).exists()

    code, env, _ = run_json(capsys, "findings", run_id, "--top", "5")
    assert code == 0 and [q["id"] for q in env.data["queue"]] == ["aisle-042", "crossing-007", "loading-018", "aisle-011", "turn-026"]
    code, env, _ = run_json(capsys, "findings", run_id, "--filter", "improved-unstable")
    assert [q["id"] for q in env.data["queue"]] == ["aisle-011", "turn-026"]
    code, env, _ = run_json(capsys, "findings", run_id, "--filter", "settled-regressions")
    assert [q["id"] for q in env.data["queue"]] == ["loading-018"]

    code, env, _ = run_json(capsys, "case", run_id, "turn-026")
    assert code == 0 and env.data["flags"] == ["improved", "unstable", "improved_unstable"]
    assert env.data["why"].startswith("Error fell by 1.10 px on this case. The candidate made 32% of its refinement")
    assert env.data["why"].endswith("The error looks fine; the answer is not settled.")

    code, env, _ = run_json(capsys, "check", "save", run_id, "aisle-042")
    assert code == 0 and env.data["check"]["max_error"] == 3.7 and env.data["check"]["max_late_share"] == 0.25 and env.data["status_now"]["status"] == "failing"
    saved = json.loads((workdir / "checks.json").read_text())
    assert saved["version"] == 2 and saved["project"] == "Warehouse perception" and saved["checks"][0]["case_id"] == "aisle-042"

    code, env, _ = run_json(capsys, "check", "run", run_id)
    assert code == 1 and env.ok and env.data["failed"] is True and env.data["checks"][0]["status"] == "failing" and "aisle-042" in env.data["flagged"]
    code, env, _ = run_json(capsys, "check", "run", run_id, "--fail-on", "checks", "--case", "pallet-003")
    assert code == 0 and env.data["failed"] is False
    code, env, _ = run_json(capsys, "check", "run", run_id, "--fail-on", "flags", "--case", "pallet-003")
    assert code == 0

    code, env, _ = run_json(capsys, "report", run_id)
    md = (workdir / "rb-runs" / run_id / "report.md").read_text()
    assert code == 0 and md.startswith("# Warehouse perception: model comparison\n\nRun: ") and "## Verdict" in md and "## Reproduce" in md and "1 saved check failing" in md
    assert "Command: `rb example" in md

    code, env, _ = run_json(capsys, "check", "list")
    assert env.data["checks"][0]["case_id"] == "aisle-042"
    code, env, _ = run_json(capsys, "check", "rm", "aisle-042")
    assert code == 0 and env.data["remaining"] == 0
    code, env, _ = run_json(capsys, "runs")
    assert env.data["runs"] == [run_id]


def test_human_output_and_next(workdir, capsys):
    code, out, err = run(capsys, "example")
    assert code == 0 and "Verdict: Not ready: 3 error regressions, 2 unstable cases that pass on error. Start with aisle-042." in out
    assert "\nNext: rb findings " in out
    run_id = re.search(r"rb-runs/(\S+)/", out).group(1)
    code, out, _ = run(capsys, "findings", run_id)
    assert "5 of 12 cases shown" in out and "aisle-042" in out
    code, out, _ = run(capsys, "check", "run", run_id)
    lines = out.strip().splitlines()
    assert code == 1 and any(l.startswith("OK           pallet-003") for l in lines) and lines[-1].startswith("Next:") and "CHECKS NEED ATTENTION" in out


def test_v1_file_read_in_place(workdir, capsys):
    code, env, _ = run_json(capsys, "example", "--write", "ex.json")
    assert code == 0 and Path("ex.json").exists()
    code, env, _ = run_json(capsys, "findings", "ex.json", "--top", "1")
    assert code == 0 and env.data["queue"][0]["id"] == "aisle-042" and env.next[0] == "rb case ex.json aisle-042"
    assert not (workdir / "rb-runs").exists(), "reading a file in place writes nothing"
    code, env, _ = run_json(capsys, "import", "ex.json")
    assert code == 0 and (workdir / "rb-runs").exists()
    record = json.loads((workdir / "rb-runs" / env.run_id / "record.json").read_text())
    assert record["input"]["format"] == "json-v1" and len(record["input"]["sha256"]) == 64 and record["command"].startswith("rb ")


def test_limits_override_rederives(workdir, capsys):
    run_json(capsys, "example", "--write", "ex.json")
    code, env, _ = run_json(capsys, "findings", "ex.json", "--max-regression", "1.5", "--filter", "regressions")
    assert code == 0 and [q["id"] for q in env.data["queue"]] == ["aisle-042"] and env.data["limits"]["max_regression"] == 1.5
    code, env, _ = run_json(capsys, "findings", "ex.json", "--max-late-share", "2")
    assert code == 2 and env.errors[0].code == "E_LIMITS_INVALID"


def test_errors_are_structured(workdir, capsys):
    Path("bad.json").write_text('{"not": "valid"}')
    code, env, err = run_json(capsys, "import", "bad.json")
    assert code == 2 and not env.ok and env.errors[0].code == "E_IMPORT_INVALID"
    problems = env.errors[0].model_dump()["problems"]
    assert problems[0] == "version must be the number 1." and problems[-1] == "cases is missing: add a list of 1–500 cases."
    code, env, _ = run_json(capsys, "findings", "nope")
    assert code == 2 and env.errors[0].code == "E_RUN_NOT_FOUND"
    code, env, _ = run_json(capsys, "rerun")
    assert code == 2 and env.errors[0].code == "E_NOT_AVAILABLE"
    code, out, err = run(capsys, "rerun")
    assert code == 2 and "not part of" in err
    code, env, _ = run_json(capsys, "import", "missing.json")
    assert code == 2 and env.errors[0].code == "E_FILE_NOT_FOUND"


def test_csv_import_with_foreign_checks_file_warns(workdir, capsys):
    Path("checks.json").write_text(json.dumps({"version": 2, "project": "Other", "checks": [{"case_id": "x", "max_error": 1}]}))
    Path("m.csv").write_text("case_id,baseline_error,candidate_error\ns1,1.0,2.0\ns2,1.0,0.5\n")
    code, env, err = run_json(capsys, "import", "m.csv", "--project", "P", "--baseline-name", "a", "--candidate-name", "b", "--dataset", "d", "--metric", "abs_error", "--unit", "cm")
    assert code == 0 and env.data["metric"]["unit"] == "cm" and env.data["summary"]["regressions"] == 1 and "ignored" in env.data["warnings"][0]
    code, env, _ = run_json(capsys, "check", "run", env.run_id, "--checks", "checks.json")
    assert code == 2 and env.errors[0].code == "E_CHECKS_PROJECT_MISMATCH"


def test_schema_and_docs(workdir, capsys):
    for name in ("bundle", "record", "findings", "checks", "envelope", "comparison-v1", "limits"):
        code, env, _ = run_json(capsys, "schema", name)
        assert code == 0 and env.data.get("title") or env.data.get("properties")
    code, env, _ = run_json(capsys, "schema", "example")
    assert env.data["version"] == 1
    code, out, _ = run(capsys, "docs")
    assert out.startswith("# Rabbit Brain: AGENTS.md")
    code, env, _ = run_json(capsys, "docs", "--errors")
    assert "E_NOT_AVAILABLE" in env.data["errors"]
    code, out, _ = run(capsys, "version")
    assert out.startswith("rabbit-brain 0.")
    code, out, _ = run(capsys)
    assert code == 0 and "usage: rb" in out


def test_runs_dir_env(workdir, capsys, monkeypatch):
    monkeypatch.setenv("RB_RUNS_DIR", str(workdir / "elsewhere"))
    code, env, _ = run_json(capsys, "example")
    assert code == 0 and (workdir / "elsewhere" / env.run_id / "bundle.json").exists()
    code, env, _ = run_json(capsys, "findings", env.run_id, "--top", "1")
    assert code == 0
