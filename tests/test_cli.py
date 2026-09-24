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
    assert env.data["verdict"]["start"] == "aisle-042" and env.next[0].startswith(f"rb review findings {run_id}")
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
    assert "\nNext: rb review findings " in out
    run_id = re.search(r"rb-runs/(\S+)/", out).group(1)
    code, out, _ = run(capsys, "findings", run_id)
    assert "5 of 12 cases shown" in out and "aisle-042" in out
    code, out, _ = run(capsys, "check", "run", run_id)
    lines = out.strip().splitlines()
    assert code == 1 and not any("pallet-003" in l for l in lines) and "cases within limits and not shown" in out and lines[-1].startswith("Next:") and "CHECKS NEED ATTENTION" in out
    code, out, _ = run(capsys, "check", "run", run_id, "--all")
    assert code == 1 and any(l.startswith(("OK", "IMPROVED")) and "pallet-003" in l for l in out.strip().splitlines())


def test_v1_file_read_in_place(workdir, capsys):
    code, env, _ = run_json(capsys, "example", "--write", "ex.json")
    assert code == 0 and Path("ex.json").exists()
    code, env, _ = run_json(capsys, "findings", "ex.json", "--top", "1")
    assert code == 0 and env.data["queue"][0]["id"] == "aisle-042" and env.next[0] == "rb review case ex.json aisle-042"
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


def test_csv_import_with_column_map_note_and_row_errors(workdir, capsys):
    """An evaluator's own CSV imports through --columns without a converter; errors name the row and column; the receipt carries the note."""
    Path("eval.csv").write_text(
        "frame,epe_a,epe_b,updates_b\n"
        "f-001,2.10,2.55,4.3 2.2 1.3 0.9 0.7 0.5 0.4 0.35 0.3 0.28 0.25 0.24\n"
        "f-002,1.40,1.05,3.6;1.7;0.8;0.4;0.25;0.15;0.1;0.07;0.05;0.04;0.03;0.03\n", encoding="utf-8")
    common = ["--project", "p", "--baseline-name", "a", "--candidate-name", "b", "--dataset", "d", "--metric", "endpoint_error", "--unit", "px"]
    code, env, _ = run_json(capsys, "import", "eval.csv", *common)
    assert code == 2 and env.errors[0].code == "E_CSV_INVALID" and "the file has: frame, epe_a, epe_b, updates_b" in env.errors[0].message and "--columns" in env.errors[0].message
    code, env, _ = run_json(capsys, "import", "eval.csv", *common, "--columns", "case_id=frame,baseline_error=epe_a,candidate_error=epe_b,candidate_trajectory=updates_b", "--note", "from eval.csv")
    assert code == 0 and env.data["summary"]["cases"] == 2 and env.data["summary"]["with_trajectories"] == 2, env.errors
    record = json.loads((workdir / "rb-runs" / env.run_id / "record.json").read_text())
    assert record["notes"] == "from eval.csv" and "--note 'from eval.csv'" in record["command"]   # quoted, so the receipt's command pastes back
    report = (workdir / "rb-runs" / env.run_id / "report.md").read_text()
    assert "Notes: from eval.csv" in report and "## Convergence on this case set" in report
    findings = json.loads((workdir / "rb-runs" / env.run_id / "findings.json").read_text())
    assert all("baseline_error" in q and "late_update_change" in q for q in findings["queue"])  # nulls written explicitly
    # rendering an imported run says why it cannot, instead of pointing at rb init for a project that does not exist
    code, env, _ = run_json(capsys, "case", env.run_id, "f-001", "--render")
    assert code == 2 and env.errors[0].code == "E_CONFIG_MISSING" and "was imported" in env.errors[0].message
    code, env, _ = run_json(capsys, "case", record["run_id"], "f-001")
    assert code == 0 and not any("--render" in n for n in env.next)
    # a bad series names the row and the column
    Path("bad.csv").write_text("case_id,baseline_error,candidate_error,candidate_trajectory\nx,1,2,1;2;three\n", encoding="utf-8")
    code, env, _ = run_json(capsys, "import", "bad.csv", *common)
    assert code == 2 and "row 2 (x), column candidate_trajectory" in env.errors[0].message and "separate values with ';'" in env.errors[0].message
    code, env, _ = run_json(capsys, "schema", "csv")
    assert code == 0 and env.data["csv"].startswith("case_id,name,baseline_error,candidate_error")
    code, env, _ = run_json(capsys, "runs")
    assert code == 0 and env.data["details"][0]["verdict"] and env.data["details"][0]["candidate"] == "b"


def test_check_defaults_unit_and_flagged_only_output(workdir, capsys):
    code, env, _ = run_json(capsys, "example")
    run_id = env.run_id
    # default max_error: the better of the two models on the case + max_regression, and the unit travels with the check
    code, env, _ = run_json(capsys, "check", "save", run_id, "aisle-042")     # a regression: current 3.4 → candidate 5.2
    assert code == 0 and env.data["check"]["max_error"] == 3.7 and env.data["check"]["unit"] == "px"
    code, env, _ = run_json(capsys, "check", "save", run_id, "pallet-003")    # improved: the candidate's level is kept
    bundle = json.loads((workdir / "rb-runs" / run_id / "bundle.json").read_text())
    c = next(c for c in bundle["cases"] if c["id"] == "pallet-003")
    assert env.data["check"]["max_error"] == round(min(c["baseline_error"], c["candidate_error"]) + 0.3, 2)
    code, out, _ = run(capsys, "check", "list")
    assert code == 0 and "px" in out and "units" not in out


def test_share_contains_statistics_and_no_identities(workdir, capsys):
    code, env, _ = run_json(capsys, "example")
    run_id = env.run_id
    code, env, _ = run_json(capsys, "share", run_id)
    assert code == 0 and env.data["sent"] is False and env.data["cases"] == 12, env.errors
    share = json.loads(Path(env.data["path"]).read_text())
    text = json.dumps(share)
    assert share["schema_version"] == "rb-share-1" and len(share["cases"]) == 12 and share["cases"][0]["index"] == 0
    assert all(k in share["cases"][0] for k in ("baseline_error", "candidate_error", "candidate_trajectory", "candidate_stability", "flags"))
    bundle = json.loads((workdir / "rb-runs" / run_id / "bundle.json").read_text())
    for c in bundle["cases"]:
        assert c["id"] not in text and c["name"] not in text
    assert bundle["project"] not in text and run_id not in text and "rb example" not in text and "Start with" not in text
    code, env, _ = run_json(capsys, "schema", "share")
    assert code == 0 and env.data["properties"]["cases"]
    code, env, _ = run_json(capsys, "share", run_id, "--print")
    assert code == 0 and env.data["schema_version"] == "rb-share-1"


def test_report_writes_an_html_report_beside_the_markdown(workdir, capsys):
    """`rb report --html` is the local viewer: one file, opened from disk, nothing uploaded."""
    code, env, _ = run_json(capsys, "example")
    run_id = env.run_id
    code, env, _ = run_json(capsys, "report", run_id, "--html")
    assert code == 0
    html = workdir / "rb-runs" / run_id / "report.html"
    assert Path(env.data["html"]).resolve() == html.resolve() and html.exists()
    text = html.read_text(encoding="utf-8")
    assert '<script id="rb-data"' in text
    # Everything it needs is in the file: on file:// a fetch of a sibling is blocked.
    assert not re.search(r'<(script|link)[^>]*\s(src|href)=', text)
    assert "rb-runs" not in text.split('<script id="rb-data"')[0]


def test_report_without_html_writes_only_the_markdown(workdir, capsys):
    code, env, _ = run_json(capsys, "example")
    run_id = env.run_id
    code, env, _ = run_json(capsys, "report", run_id)
    assert code == 0 and env.data["html"] is None
    assert not (workdir / "rb-runs" / run_id / "report.html").exists()


def test_report_html_elsewhere_carries_its_evidence_with_it(workdir, capsys):
    """Evidence is referenced by relative path, which only resolves inside the run directory. A file
    written anywhere else has to carry the images or it renders broken ones."""
    code, env, _ = run_json(capsys, "example")
    run_id = env.run_id
    evidence = workdir / "rb-runs" / run_id / "evidence" / "aisle-042"
    evidence.mkdir(parents=True)
    (evidence / "error.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 16)

    inside = workdir / "rb-runs" / run_id / "report.html"
    code, _, _ = run(capsys, "report", run_id, "--html")
    assert "evidence/aisle-042/error.png" in inside.read_text(encoding="utf-8")

    outside = workdir / "elsewhere" / "report.html"
    code, env, out = run_json(capsys, "report", run_id, "--html", str(outside))
    assert code == 0 and outside.exists()
    text = outside.read_text(encoding="utf-8")
    assert "data:image/png;base64," in text and '"evidence/aisle-042/error.png"' not in text


def test_report_embed_inlines_evidence_even_beside_the_run(workdir, capsys):
    code, env, _ = run_json(capsys, "example")
    run_id = env.run_id
    evidence = workdir / "rb-runs" / run_id / "evidence" / "aisle-042"
    evidence.mkdir(parents=True)
    (evidence / "error.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 16)
    code, env, _ = run_json(capsys, "report", run_id, "--embed")
    assert code == 0
    text = Path(env.data["html"]).read_text(encoding="utf-8")
    assert "data:image/png;base64," in text and '"evidence/aisle-042/error.png"' not in text


def test_report_html_states_the_limits_the_run_was_checked_at(workdir, capsys):
    """The viewer computes its own verdict. Handed the wrong limits it would contradict report.md."""
    code, env, _ = run_json(capsys, "example")
    run_id = env.run_id
    code, env, _ = run_json(capsys, "report", run_id, "--html", "--max-regression", "0.9")
    data = json.loads(Path(env.data["html"]).read_text(encoding="utf-8")
                      .split('<script id="rb-data" type="application/json">')[1].split("</script>")[0]
                      .replace("<\\/script", "</script"))
    assert data["limits"]["threshold"] == 0.9
    assert data["run"]["project"] and data["run"]["cases"]
