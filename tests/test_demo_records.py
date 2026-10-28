"""The records the shell is built, tested, measured and shown on (tests/rbtest.py) hold what they say.

- The INT8 demo record (rbtest.int8_demo): each element the shell's plan lists for it shows in rb status --json, or in
  rb show where rb status does not carry it, so a demo that loses one fails here before a screen is drawn on it.
- Each record is made from the command line too, into a folder of its own, as the screenshot runner makes them.
- The scale record (rbtest.scale_record) holds SCALE's counts as rb counts them, and the found-folders record its
  folders and found results, so the budgets and caps measured on them measure what they claim.
- shell_baselines.measure reads the figures phase 1a's budget holds, in the shape BASELINES keeps them.

Every name, number and session id in them is made up."""
import json
import re
import shutil
import subprocess
import sys
import unicodedata
from pathlib import Path

import pytest

from rbtest import AGENT_VARS, SCALE, as_the_user, environment

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="every record here is a git repository")

PENDING = ("call_proposed", "past_call_unconfirmed")      # what waits for a confirmation, which is never a call on the user


def elements(demo) -> dict:
    """Each element of the demo, by name: whether rb's own reads show it."""
    st = demo.rb("status")
    shown = {r["id"]: demo.rb("show", r["id"])["object"] for r in st["runs"]}
    replaced = {o["replaces"]: o for o in shown.values() if o.get("replaces")}
    gone = {rid: demo.rb("show", rid)["object"] for rid in replaced}
    findings = list(st["findings"])
    links = [r for q in st["questions"] for r in q["inquiry"]["direct"] if r.get("link")]
    declared_by = {f["criterion"]: f["declared"]["by"] for f in findings}
    a_sessions = {f["experiment"] for f in findings if f["declared"]["by"].startswith("agent:")}
    unmapped = {r["criterion"] for q in st["questions"] for r in q["inquiry"]["unmapped"]}
    calls_waiting = {i["subject"] for i in st["open"] if i["who"] == "human" and i["code"] not in PENDING}
    designs = {e["id"]: e["design"] for e in st["experiments"]}
    variants = {e["id"]: {v["name"]: v["role"] for v in e["variants"]} for e in st["experiments"]}

    def seeds(exp: str, arm: str) -> set:
        return {r["per_run"].get("seed") for r in st["runs"] if r["experiment_id"] == exp and f"{arm}.top1" in r["metrics"]}

    def made(rid: str) -> bool:
        return bool(next(r for r in st["runs"] if r["id"] == rid)["outputs"])

    by_sha: dict = {}
    for rid, o in shown.items():
        for f in o.get("files") or []:
            by_sha.setdefault(f["sha256"], set()).add(rid)
    cells = [f for f in findings if (f.get("comparison") or {}).get("design")]
    analyses = {a["id"] for a in st["analyses"]}
    there = demo.rb("status", cwd=demo.worktree) if demo.worktree else {"pending": []}
    elsewhere = [d for row in st["elsewhere"] if row["same_project"] for d in row["added"].get("", {}).get("decision", [])]
    notes = [n["text"] for e in st["experiments"] for n in e["notes"]]
    return {
        "a question with a sub-question": any(q["parent"] for q in st["questions"]),
        "int8 against fp32, three seeds per arm": any(v == {"fp32": "baseline", "int8": "candidate"} and len(seeds(x, "fp32")) >= 3
                                                      and len(seeds(x, "int8")) >= 3 for x, v in variants.items()),
        "calibration set size in design cells, one outside the rule": any(
            len(f["comparison"]["design"]["cells"]) >= 3 and len(f["missed_units"]) == 1 and "calib_size" in f["comparison"]["design"]["factors"]
            for f in cells),
        "a met criterion": any(f["rule_outcome"] == "meets" and f["origin"] == "own" and f["role"] == "primary" and f["comparison"]["source"] == "runs"
                               for f in findings),
        "a cited number not reproduced": any(f["origin"] == "cited" and f["says"]["rule"].startswith("not reproduced") for f in findings),
        "a single-seed criterion": any(any(c["code"] == "single_unit" for c in f["caveats"]) for f in findings),
        "an analysis-backed reading": any((f.get("comparison") or {}).get("source") in analyses for f in findings),
        "a gate": any(f["role"] == "gate" for f in findings),
        "a call the user made": any(c["authority"]["state"] == "direct" and c["by"] == "human:ana" for c in st["decisions"]),
        "a call a session proposed": any(p["authority"]["state"] == "proposed" for p in st["pending"]),
        "a call a session proposed that the user confirmed, its reason the session's": any(
            c["authority"]["state"] == "confirmed" and c["by"] == "human:ana"
            and demo.rb("show", c["authority"]["confirms"])["object"]["by"].startswith("agent:")
            and demo.rb("show", c["authority"]["confirms"])["object"]["why"] == c["why"] for c in st["decisions"]),
        "a past call a session quoted from a dated notes file, with its file and line": any(
            p["authority"]["state"] == "past_unconfirmed" and p["by"].startswith("agent:") and (p["past"].get("source") or {}).get("line")
            and re.fullmatch(r"notes/\d{4}-\d\d-\d\d\.md", p["past"]["source"]["path"]) for p in st["pending"]),
        "a design a session wrote, not confirmed": any(d["standing"] == "not_confirmed" and x in a_sessions for x, d in designs.items()),
        "a design confirmed, then changed by a session": any(
            d["standing"] == "changed" and (d["confirmed"] or {}).get("by") == "human:ana" and d["changes"]
            and all(declared_by.get(c["key"].split(":")[1], "").startswith("agent:") for c in d["changes"]) for d in designs.values()),
        "a design a session wrote that the user confirmed, unchanged since": any(
            d["standing"] == "confirmed" and (d["confirmed"] or {}).get("by") == "human:ana" and not d["changes"] and x in a_sessions
            for x, d in designs.items()),
        "the user's own interpretation": any(r["link"]["declared_by"] == "human:ana" and r["link"]["when_met"]
                                             and "not confirmed" not in r["interpretation"] for r in links),
        "a session's interpretation the user confirmed with its design": any(
            r["link"]["declared_by"].startswith("agent:") and r["link"]["when_met"] and r["interpretation_confirmed"] is True
            and r["interpretation_active"] and "not confirmed" not in r["interpretation"] for r in links),
        "an interpretation not confirmed": any(r["link"]["declared_by"].startswith("agent:") and r["link"]["when_met"]
                                               and r["interpretation_confirmed"] is False and "not confirmed" in r["interpretation"] for r in links),
        "a criterion linked to no question, with a call on it": bool(unmapped & calls_waiting),
        "a found folder no experiment reads": bool(st["repository"]["unindexed"]),
        "a result found in the repository, not in the record": st["found_unrecorded"] >= 1,
        "a changed config nothing cites": any(u["path"].startswith("configs/") and not u["new"] and not u["gone"] for u in st["uncited_changes"]),
        "a run from a SQLite row, recorded after the fact": any(
            r["made_before_recorded"] and (shown[r["id"]].get("receipt") or {}).get("row", {}).get("store") == "sqlite" for r in st["runs"]),
        "a run under rb journal run": any((r.get("command") or {}).get("given") == "observed" for r in st["runs"]),
        "a checkpoint a run made, with its recipe": any(
            res["type"] == "checkpoint" and res["acquisition"]["recipe"]
            and any(o["resource"] == res["id"] for r in st["runs"] for o in r["outputs"]) for res in st["resources"]),
        "a run with the same bytes as the run that made it": any(len(rids) > 1 and any(made(rid) for rid in rids) for rids in by_sha.values()),
        "a retracted run and its replacement": any(gone[old].get("retracted") for old in gone),
        "the metric onnx_top1, named for no arm": any(m["id"] == "onnx_top1" for m in st["metrics"]) and any(
            "onnx_top1" in r["metrics"] for r in st["runs"]),
        "the per-run setting one_cycle=1": any(r["per_run"].get("one_cycle") == 1 for r in st["runs"]),
        "a second worktree with a call waiting there": bool(elsewhere) and any(
            p["id"] in elsewhere and p["authority"]["state"] == "proposed" for p in there["pending"]),
        "a session's reason ending in a hidden character": any(
            p["by"].startswith("agent:") and p["why"] and unicodedata.category(p["why"][-1]) == "Cf" for p in st["pending"]),
        'a note with " online=" and "javascript:"': any(" online=" in t and "javascript:" in t for t in notes),
    }


def test_int8_demo_has_every_element(int8_demo):
    shown = elements(int8_demo)
    assert [name for name, there in shown.items() if not there] == []
    assert len(shown) == 32


def test_each_question_of_the_int8_demo_reads_one_interpretation_at_most(int8_demo):
    """An interpretation says what meeting a criterion would mean for the question it is filed under, so no question of
    the demo has two in force: c7's, about per-channel scales, is under q1b, which it answers, not beside c1's on q1."""
    said = {q["id"]: sorted({r["link"]["when_met"] for r in q["inquiry"]["direct"]
                             if r.get("link") and r["link"]["when_met"] and r["interpretation_active"]}) for q in int8_demo.rb("status")["questions"]}
    assert {q: w for q, w in said.items() if w} == {"q1": ["INT8 is accurate enough to ship"], "q1b": ["per-channel scales stay the default"]}


def test_a_record_is_made_from_the_command_line_in_a_folder_of_its_own(tmp_path):
    env = {k: v for k, v in environment().items() if k not in AGENT_VARS}
    make = [sys.executable, str(Path(__file__).with_name("rbtest.py")), "int8-demo"]
    made = subprocess.run([*make, str(tmp_path / "demo")], env=env, capture_output=True, text=True)
    assert made.returncode == 0, made.stderr
    assert Path(made.stdout.strip()) == (tmp_path / "demo" / "int8").resolve() and (tmp_path / "demo" / "int8-onnx").is_dir()
    again = subprocess.run([*make, str(tmp_path / "demo")], env=env, capture_output=True, text=True)
    assert again.returncode == 2 and "is not empty" in again.stderr


def test_the_scale_record_holds_its_counts(scale_record):
    st = scale_record.rb("status")
    log = (scale_record.root / ".rb" / "log.jsonl").read_text(encoding="utf-8").splitlines()
    counts = {"experiments": len(st["experiments"]), "runs": len(st["runs"]), "criteria": len(st["findings"]), "analyses": len(st["analyses"]),
              "resources": len(st["resources"]), "writes": len(log)}
    assert {**counts, "writes": SCALE["writes"]} == SCALE and SCALE["writes"] <= counts["writes"] < SCALE["writes"] + 5
    per = {e["id"]: e["run_count"] for e in st["experiments"]}
    assert max(per.values()) > 50                                       # one experiment's receipts pass the page's cap
    assert {e["design"]["standing"] for e in st["experiments"]} == {"yours", "confirmed", "changed", "not_confirmed"}
    assert {json.loads(line)["actor"] for line in log} == {"human:ana", "agent:claude-code", "agent:codex"}
    states = {p["authority"]["state"] for p in st["pending"]} | {c["authority"]["state"] for c in st["decisions"]}
    assert states == {"direct", "confirmed", "proposed", "past_unconfirmed"}
    assert any(e["frozen"] for e in st["experiments"]) and len(st["questions"]) == 6


def test_the_found_folders_record_holds_its_folders_and_found_results(tmp_path):
    """As made by default, as the list caps are measured on it: 600 folders no recorded run names, and 60 found results."""
    from rbtest import found_folders_record
    rec = found_folders_record(tmp_path)
    st = rec.rb("status")
    assert len(st["repository"]["unindexed"]) == 600 and st["found_unrecorded"] == 60
    assert [i["code"] for i in st["open"] if i["who"] == "agent"] == ["unindexed_folder"]


def test_the_baselines_are_read_as_the_budget_reads_them(int8_demo, monkeypatch):
    from shell_baselines import BASELINES, measure
    as_the_user(monkeypatch)
    got = measure(int8_demo.root)
    figures = ("seconds_median", "seconds_max", "bytes_median", "bytes_max")
    for read in ("home", "status", "state_json"):
        assert set(got[read]) == set(figures) and got[read]["bytes_median"] > 10_000 and 0 < got[read]["seconds_median"] <= got[read]["seconds_max"]
        assert set(BASELINES[read]) == set(figures) and BASELINES[read]["bytes_median"] > 1_000_000
    assert json.loads(json.dumps(BASELINES)) == BASELINES
    from shell_baselines import BY_MACHINE, for_this_machine
    assert all(set(b) == set(BASELINES) and m == b["machine"] for m, b in BY_MACHINE.items()) and for_this_machine() in BY_MACHINE.values()
