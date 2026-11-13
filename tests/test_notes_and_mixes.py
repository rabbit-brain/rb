"""Agent notes and the grader badge (owner, Nov 13 2026: "how to read and understand these ai slop?").

rb shows what it computes itself: a per-run setting whose words differ across an experiment's runs (a grader that changed
from one checker to another) is a badge on the experiment, "2 checkers: answer_tag (3 runs) · last_number (2 runs)", and
on every comparison whose counted runs mix them, with how rb reads it ("arms differ", "pooled"). A note reads as one
line: a "source" chip where it cites a file's line, its opening clause and a "not confirmed" chip for a session's note,
the whole note under it on a click, its paths and citations as chips that open them.
"""
import json
import re
import subprocess
from pathlib import Path

import pytest

from rabbit_brain.models import Envelope
from rbtest import as_the_user, command, environment, main


@pytest.fixture
def rb(tmp_path, monkeypatch, capsys):
    as_the_user(monkeypatch)
    monkeypatch.setenv("RB_ACTOR", "human:ana")
    root = tmp_path / "int8"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    monkeypatch.chdir(root)

    def run(*argv):
        code = main([*argv, "--json"], typed=True)
        env = Envelope.model_validate_json(capsys.readouterr().out)
        assert code in (0, 1) and env.ok, (argv, code, env.errors)
        return env

    run.root = root
    return run


def _record(rb):
    """INT8 against FP32, three seeds each, the first five runs graded by answer_tag and the last by last_number; a
    second per-run setting, the number of images, a number."""
    rb("init", "INT8 ResNet-50", "--no-agents")
    rb("metric", "add", "top1", "--maximize")
    rb("experiment", "add", "INT8 against FP32", "--id", "e1", "--purpose", "the checkers", "--baseline", "fp32", "--candidate", "int8")
    for s in ("seed", "checker", "images"):
        rb("spec", "set", "e1", s, "--per-run")
    rb("spec", "set", "e1", "population.unit", "seed", "--no-verify")
    rb("criterion", "add", "INT8 keeps top1 under one checker", "-e", "e1", "--metric", "change.top1", "--at-least", "-1",
       "--where", "checker=answer_tag", "--id", "c1")
    rb("criterion", "add", "INT8 keeps top1 across checkers", "-e", "e1", "--metric", "top1", "--subject", "int8", "--reference", "fp32",
       "--subject-where", "checker=last_number", "--reference-where", "checker=answer_tag", "--at-least", "-1", "--id", "c2")
    rows = [("fp32", 1, "answer_tag", 76.1), ("fp32", 2, "answer_tag", 76.0), ("fp32", 3, "answer_tag", 76.2),
            ("int8", 1, "answer_tag", 75.8), ("int8", 2, "answer_tag", 75.9), ("int8", 1, "last_number", 79.0),
            ("int8", 2, "last_number", 79.2)]
    for arm, seed, checker, v in rows:
        rb("run", "add", "e1", f"{arm}.top1={v}", "--set", f"seed={seed}", "--set", f"checker={checker}", "--set", f"images={500 if seed == 1 else 250}",
           "--produced-at", "2026-05-01")


def _page(root, tmp_path):
    env = {**environment(), "RB_TEST_SHELL": "1", "RB_TEST_SHELL_DUMP": str(tmp_path / "doc.json")}
    got = subprocess.run([*command(), "page", "--out", str(tmp_path / "p.html")], cwd=root, env=env, capture_output=True, text=True)
    assert got.returncode == 0, got.stderr
    return (tmp_path / "p.html").read_text(encoding="utf-8"), json.loads((tmp_path / "doc.json").read_text(encoding="utf-8"))


def test_a_grader_that_changed_is_a_badge_on_the_experiment_and_on_the_comparison_that_mixes_it(rb, tmp_path):
    _record(rb)
    page, doc = _page(rb.root, tmp_path)
    [x] = doc["experiments"]
    # words only, never the unit (seed) or a number (images)
    assert x["mixes"] == [{"setting": "checker", "values": [{"value": "answer_tag", "runs": 5}, {"value": "last_number", "runs": 2}]}], x["mixes"]
    view = page[page.index('id="x-e1"'):page.index("</article>", page.index('id="x-e1"'))]
    # closed form (owner, Nov 13 2026): the badge's head is a count too, and opens the runs it counts
    assert ('<span class="mixb" title="per-run setting checker"><b><a href="#x-e1:runs">2 checkers:</a></b> answer_tag (<a href="#x-e1:runs">5 runs</a>) · '
            'last_number (<a href="#x-e1:runs">2 runs</a>)</span>') in view
    # c1 reads one checker: no badge; c2's arms are graded apart: its card says so
    assert doc["findings"]["c1"]["mixes"] == []
    [m] = doc["findings"]["c2"]["mixes"]
    assert m["setting"] == "checker" and [v["value"] for v in m["values"]] == ["answer_tag", "last_number"] and m["how"] == "arms_differ", m
    card = page[page.index('id="c-c2"'):page.index('id="c-c2:rule"')]
    assert re.search(r'<span class="mixb"[^>]*><b><a href="#c-c2:runs">2 checkers:</a></b> answer_tag \(<a href="#c-c2:runs">\d runs?</a>\) · last_number \(<a href="#c-c2:runs">\d runs?</a>\) · arms differ</span>', card), card
    assert "mixb" not in page[page.index('id="c-c1"'):page.index('id="c-c1:rule"')]


def test_a_note_is_one_line_its_whole_a_click_away(rb, tmp_path, monkeypatch):
    _record(rb)
    Path("results").mkdir()
    Path("results/r004").mkdir()
    Path("results/r004/metrics.json").write_text('{"top1": 75.8}\n', encoding="utf-8")
    Path("notes.md").write_text("# Calls\n\nThe checker changed on 2026-05-02.\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-c", "user.email=ana@example.org", "-c", "user.name=ana", "commit", "-qm", "files"], check=True, capture_output=True)
    rb("run", "add", "e1", "--from", "results/r004/metrics.json", "--variant", "int8", "--set", "seed=3", "--set", "checker=answer_tag",
       "--set", "images=250", "--produced-at", "2026-05-01")
    monkeypatch.setenv("CLAUDECODE", "1")
    rb("note", "add", "The grader changed between runs: notes.md:3 says when, and results/r004 holds the first run graded by answer_tag.", "-e", "e1")
    monkeypatch.delenv("CLAUDECODE")
    rb("note", "add", "Short note.", "-e", "e1")
    page, _ = _page(rb.root, tmp_path)
    notes = page[page.index('data-fold="x-e1:notes"'):]
    notes = notes[:notes.index("</ul>")]
    items = re.findall(r'<li class="note nl"[^>]*>(.*?)</li>', notes, re.S)
    assert len(items) == 2, items
    long = next(i for i in items if "grader" in i)
    summary = long[long.index("<summary>"):long.index("</summary>")]
    # the line: rb's label, a source chip, the opening clause, not confirmed; the whole under it
    assert '<span class="fch fch-use nk">source</span>' in summary and '<span class="nop">The grader changed between runs:</span>' in summary
    assert '<span class="fch fch-warn">not confirmed</span>' in summary and "notes.md" not in summary
    whole = long[long.index('<div class="nall">'):]
    assert 'href="#cite-notes.md:3"' in whole and '<a class="fch fch-use nchip" href="#files" data-folder="results/r004/">results/r004</a>' in whole, whole
    # a short note is the line alone, and one written in the user's terminal carries no chip
    short = next(i for i in items if "Short note." in i)
    assert "<details" not in short and "not confirmed" not in short
