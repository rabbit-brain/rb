"""The rulings of Nov 12 05:30 +08 (UX-DIRECTION.md) on rb status and in the JSON every surface draws from: a chart row
says its check's number (the zone's solid edge), the result's unit, and the chip `claim` where its check checks a claim
someone stated. rb status says the check's number once in its panel's head, after the zone's glyph (`░ from −1`), where
the panel's rows share it, and on the row where they do not."""
import os
import re
import subprocess

import pytest

from rbtest import command


def status_text(rec, columns: int = 100, lines: int = 400) -> str:
    env = {**rec.env, "COLUMNS": str(columns), "LINES": str(lines)}
    r = subprocess.run([*command(), "status"], cwd=rec.root, env=env, capture_output=True, encoding="utf-8", stdin=subprocess.DEVNULL)
    assert r.returncode in (0, 1), r.stderr
    return r.stdout


def with_unit(n: str, unit: str) -> str:
    return n if not unit else f"{n}%" if unit == "%" else f"{n} {unit}"


@pytest.mark.parametrize("which", ["int8_demo", "scale_record"])
def test_each_row_says_its_kind_its_unit_and_its_checks_number(which, request):
    rec = request.getfixturevalue(which)
    st = rec.rb("status")
    findings = {f["criterion"]: f for f in st["findings"]}
    units = {n: m.get("unit") or "" for m in st["metrics"] for n in [m["id"], *m.get("aliases", [])]}
    rows = st["overview"]["chart"]
    assert rows
    for row in rows:
        f = findings[row["id"]]
        # a check of a claim someone stated, as the record says it: its origin is cited
        assert row["claim"] == (f["origin"] == "cited"), row
        metric = f["comparison"]["metric"].removeprefix("change.")
        unit = units.get(metric, units.get(metric.split(".")[-1], ""))
        want = "points" if row["difference"] and unit in ("%", "percent") else ("%" if unit == "percent" else unit)
        assert row["measure_unit"] == want, (row, unit)
        assert row["result_text"] == with_unit(row["est_text"], want)
        # the check's number, with the side that passes
        if row["threshold"] is None:
            assert row["edge_text"] == ""
        else:
            word = {"ge": "from ", "le": "up to ", "gt": "above ", "lt": "below "}.get(row["side"], "")
            assert row["edge_text"].startswith(word), row
            if row["side"] == "eq" and row["tol"]:
                lo, hi = row["edge_text"].split(" to ")
                assert lo != hi, row                            # a band's two ends never read alike
    assert any(r["claim"] for r in rows) and any(r["measure_unit"] for r in rows)


@pytest.mark.parametrize("which", ["int8_demo", "scale_record"])
def test_rb_status_says_the_number_once_a_panel_and_each_rows_unit_and_chip(which, request):
    rec = request.getfixturevalue(which)
    rows = rec.rb("status")["overview"]["chart"]
    text = status_text(rec)
    lines = text.splitlines()
    panels: dict = {}
    for r in rows:
        panels.setdefault(r["group"], []).append(r)
    seen = 0
    for group, members in panels.items():
        heads = [i for i, line in enumerate(lines) if line.startswith(f"  {group}")]
        if not heads:
            continue                                            # past the rows the dashboard shows
        head = lines[heads[0]]
        edges = {r["edge_text"] for r in members}
        if len(edges) == 1 and members[0]["edge_text"]:
            assert head == f"  {group}  ░ {members[0]['edge_text']}", head
        else:
            assert "░" not in head, head
        for r in members:
            row = next((line for line in lines if line.rstrip().endswith(r["id"]) or line.rstrip().endswith(f"{r['id']} [claim]")), None)
            if row is None:
                continue
            seen += 1
            assert r["result_text"] in row, (r, row)
            assert row.rstrip().endswith("[claim]") == r["claim"], row
            if len(edges) > 1 and r["edge_text"]:
                assert f"░ {r['edge_text']}" in row or len(row) > 90, row
    assert seen


def test_rb_status_ascii_says_the_zone_as_its_ascii_mark(int8_demo):
    rows = int8_demo.rb("status")["overview"]["chart"]
    env = {**int8_demo.env, "COLUMNS": "100", "LINES": "400", "LC_ALL": "C", "LANG": "C"}
    r = subprocess.run([*command(), "status", "--ascii"], cwd=int8_demo.root, env=env, capture_output=True, encoding="utf-8", stdin=subprocess.DEVNULL)
    shared = [r_ for r_ in rows if r_["edge_text"]]
    assert shared and "░" not in r.stdout
    assert any(f"~ {x['edge_text'].replace(chr(0x2212), '-')}" in r.stdout for x in shared), r.stdout


def test_rb_status_of_one_experiment_draws_its_questions_story(int8_demo):
    """`rb status <exp>` (MP-1112-0514's should 2): the question the experiment is filed under, its strip and chart,
    never the title and Decisions alone."""
    st = int8_demo.rb("status")
    x = next(e for e in st["experiments"] if e.get("questions"))
    q = x["questions"][0]
    ov = int8_demo.rb("status", x["id"])["overview"]
    assert ov["question"] and ov["question"]["id"] == q, (ov["question"], q)
    env = {**int8_demo.env, "COLUMNS": "100", "LINES": "400"}
    r = subprocess.run([*command(), "status", x["id"]], cwd=int8_demo.root, env=env, capture_output=True, encoding="utf-8", stdin=subprocess.DEVNULL)
    lines = r.stdout.splitlines()
    assert lines[1].startswith("\u2503 "), r.stdout
    if ov["strip"]["groups"]:
        assert any(" \u00b7 " in line and any(g["label"] in line for g in ov["strip"]["groups"]) for line in lines[:6]), r.stdout


@pytest.mark.parametrize("which", ["int8_demo", "scale_record"])
def test_rb_status_says_n_of_m_and_two_spaces_between_groups(which, request):
    """The rulings of Nov 12 07:15 +08: `5 of 6` on every surface, never `5/6`; the strip's groups two spaces apart."""
    rec = request.getfixturevalue(which)
    text = status_text(rec, columns=80, lines=24)
    assert not re.search(r"\b\d+/\d+\b", text.split("Decisions", 1)[0]), text
    strip = next(line for line in text.splitlines() if line.startswith("  ") and " · " in line and any(w in line for w in ("passed", "failed", "can't tell yet", "no results yet")))
    groups = sum(1 for w in ("passed", "failed", "can't tell yet", "no results yet") if w in strip)
    if groups > 1 and len(strip) < 80:
        assert strip.count("  ") >= groups, strip


def test_rb_status_words_says_only_the_rows_it_draws_each_by_its_id(scale_record):
    """MP-1112-0514's 6b, item 7: --words says the rows the chart draws, each with its check's id, none of the rows
    "+n more" folds."""
    env = {**scale_record.env, "COLUMNS": "80", "LINES": "24"}
    r = subprocess.run([*command(), "status", "--words"], cwd=scale_record.root, env=env, capture_output=True, encoding="utf-8", stdin=subprocess.DEVNULL)
    rows = scale_record.rb("status")["overview"]["chart"]
    said = [line for line in r.stdout.splitlines() if any(line.strip() == f"({x['id']}: {x['said']})" for x in rows)]
    drawn = [x for x in rows if any(line.rstrip().endswith((x["id"], x["id"] + " [claim]")) for line in r.stdout.splitlines())]
    assert said and len(said) == len(drawn), (len(said), len(drawn))
    assert not any(line.strip().startswith("(") and line.strip().endswith(")") and ": " not in line for line in r.stdout.splitlines() if line.startswith("    ("))


def test_rb_status_json_holds_the_files_the_record_names(int8_demo):
    """MP-1112-0514's 6b, item 8 (DESIGN-UX5 2.6): `files` in rb status --json, each file the record names with its
    marks, as rb files --json gives it."""
    files = int8_demo.rb("status")["files"]
    assert files and all(f["named"] for f in files)
    whole = {f["path"]: f for f in int8_demo.rb("files")["files"]}
    for f in files:
        assert f["path"] in whole and [m["key"] for m in f["marks"]] == [m["key"] for m in whole[f["path"]]["marks"]], f["path"]
