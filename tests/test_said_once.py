"""rb status and rb show say each thing once, and in one way.

Promises pinned here:

- One reading, one set of digits: a reading's estimate and the same runs read unpaired beside it take the digits the
  rule needs, and a float's last-bit noise (-0.6500000000000021) adds none (-0.65, never -0.6500).
- A rule's timing and its question link's are said once where they read alike ("rule and question link declared after
  the evidence existed"), and each by its name where they differ.
- The items that ask for nothing are listed as what they are, never under a heading that says there is nothing to do.

Every name and number here is made up.
"""
import shutil
import subprocess

import pytest

from rbtest import Recorder, command

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="rb dates runs by git")

FP32 = (76.12, 75.94, 76.31, 76.05)
INT8 = (75.46, 75.30, 75.66, 75.40)


def record(tmp_path, *, link_later: bool = False) -> Recorder:
    """int8 against fp32, four seeds each in runs of their own, made in May and recorded now; c1 reads change.top1
    = -0.65 ± 0.005, filed to q1 with the rule, or (link_later) declared before the runs and filed to q1 after them."""
    d = Recorder(tmp_path, "int8")
    d.start()
    d.write("README.md", "# INT8 ResNet-50 on ImageNet\n")
    d.git("add", "-A")
    d.git("commit", "-qm", "start")
    rb = d.rb
    rb("init", "INT8 ResNet-50", "--no-agents")
    rb("question", "add", "Does INT8 cost accuracy on ImageNet?", "--id", "q1")
    rb("experiment", "add", "INT8 vs FP32", "--id", "int8", "-q", "q1", "--baseline", "fp32", "--candidate", "int8", "--varies", "precision")
    rb("metric", "add", "top1", "--unit", "points", "--maximize")
    rb("spec", "set", "int8", "seed", "--per-run")
    rule = ["criterion", "add", "INT8 loses 0.65 points", "-e", "int8", "--metric", "change.top1", "--equals", "-0.65", "--tolerance", "0.005", "--id", "c1"]
    if link_later:
        rb(*rule)
    else:
        rb(*rule, "-q", "q1")
    for seed, (f, i) in enumerate(zip(FP32, INT8), 1):
        # made in May, or (link_later) as each is recorded, after the rule
        extra = [] if link_later else ["--produced-at", f"2026-05-0{seed}T10:00:00Z"]
        rb("run", "add", "int8", f"top1={f}", "--variant", "fp32", "--set", f"seed={seed}", *extra)
        rb("run", "add", "int8", f"top1={i}", "--variant", "int8", "--set", f"seed={seed}", *extra)
    if link_later:
        rb("criterion", "file", "c1", "q1", "--why", "it answers q1")
    return d


def text(d: Recorder, *argv: str) -> str:
    return subprocess.run([*command(), *argv], cwd=d.root, env=d.env, capture_output=True, encoding="utf-8", stdin=subprocess.DEVNULL).stdout


def test_one_reading_one_set_of_digits(tmp_path):
    d = record(tmp_path)
    shown = text(d, "show", "c1")
    assert "reads:     change.top1: -0.65 [" in shown, shown
    assert "unpaired, the same runs read -0.65 [" in shown, shown
    assert "-0.6500" not in shown
    status = text(d, "status")
    assert "unpaired, the same runs read -0.65 [" in status and "-0.6500" not in status


def test_a_timing_alike_is_said_once(tmp_path):
    d = record(tmp_path)
    [line] = [x for x in text(d, "status").splitlines() if x.startswith("  INT8 loses 0.65 points.")]
    assert line.count("declared after the evidence existed") == 1, line
    assert "rule and question link declared after the evidence existed: all 4 units were produced before it was declared" in line
    assert "question link declared after" not in line.replace("rule and question link declared after", "")


def test_timings_that_differ_are_each_named(tmp_path):
    d = record(tmp_path, link_later=True)
    [line] = [x for x in text(d, "status").splitlines() if x.startswith("  INT8 loses 0.65 points.")]
    assert "; rule declared before the evidence" in line, line
    assert "question link declared after the evidence existed" in line, line
    assert "rule and question link" not in line


def test_items_that_ask_for_nothing_are_named_for_what_they_are(tmp_path):
    d = record(tmp_path)
    status = text(d, "status")
    assert "Nothing to do" not in status
    lines = status.splitlines()
    at = lines.index("  - int8: 1 criterion was declared after its evidence existed")
    assert lines[at - 1] == "Facts that ask for nothing (1)"
    context = text(d, "context")
    assert "## Facts that ask for nothing\n\n  - int8: 1 criterion was declared after its evidence existed" in context
    assert "Nothing to do" not in context


def test_a_bound_rule_says_its_estimate_in_one_way(tmp_path):
    """A --bound rule reads the interval's bounds; the estimate beside them is filled in their digits, and noise adds
    none there either: the paired reading and the unpaired figure say it alike."""
    d = record(tmp_path)
    d.rb("criterion", "add", "INT8's interval stays within its band", "-e", "int8", "--metric", "change.top1", "--equals", "-0.65", "--tolerance",
         "0.2658", "--bound", "--id", "c2")
    shown = text(d, "show", "c2")
    paired = shown.split("reads:     change.top1: ")[1].split(" [")[0]
    unpaired = shown.split("unpaired, the same runs read ")[1].split(" [")[0]
    assert paired == unpaired == "-0.65", (paired, unpaired)
