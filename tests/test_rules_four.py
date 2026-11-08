"""Format 2's four rules, each on every surface: a count ("19 of 20 runs below 0.890"), a best run ("best of 5 runs
reaches 0.889"), a relative change ("improves val_loss by 2%") and a cited difference of means. Each is read the same
way by rb criterion add, rb show, rb status, rb context, the page, the SDK and MCP, beside --where, the tally, a call,
the timing and --fail-on. Every name and number is synthetic: an INT8 ResNet-50 on ImageNet, recorded by human:ana."""
import html
import json
import re
import subprocess
from pathlib import Path

import pytest

from rabbit_brain.models import Envelope
from rbtest import as_the_user, main


@pytest.fixture
def rb(tmp_path, monkeypatch, capsys):
    as_the_user(monkeypatch)
    monkeypatch.setenv("RB_ACTOR", "human:ana")
    root = tmp_path / "int8"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    monkeypatch.chdir(root)

    def run(*argv, ok=True, text=False):
        if text:
            code = main(list(argv), typed=True)
            return code, capsys.readouterr().out
        code = main([*argv, "--json"], typed=True)
        env = Envelope.model_validate_json(capsys.readouterr().out)
        if ok:
            assert code in (0, 1) and env.ok, (argv, code, env.errors)
        return env

    run("init", "INT8 ResNet-50", "--no-agents")
    run("metric", "add", "val_loss", "--minimize", "--unit", "nats")
    run("experiment", "add", "INT8 against FP32", "--id", "e1", "--purpose", "the sweep", "--baseline", "fp32", "--candidate", "int8")
    run("spec", "set", "e1", "seed", "--per-run")
    return run


def seeds(rb, int8, fp32=None, start=1, data=None):
    """One run per seed: INT8's val_loss (and FP32's, where given), each with its seed (and its data set)."""
    for i, x in enumerate(int8):
        s = start + i
        nums = [f"int8.val_loss={x}"] + ([f"fp32.val_loss={fp32[i]}"] if fp32 else [])
        sets = ["--set", f"seed={s}"] + (["--set", f"data={data}"] if data else [])
        rb("run", "add", "e1", *nums, *sets, "--produced-at", "2026-05-01")


def page(rb) -> str:
    rb("page", "--out", "page.html")
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", Path("page.html").read_text(encoding="utf-8"))))


def finding(rb, cid):
    return rb("show", cid).data["finding"]


# ---------------------------------------------------------------- N1: a count

TWENTY = [0.881 + 0.001 * i for i in range(20)]        # 0.881 .. 0.900: 10 at most 0.890


def test_a_count_names_both_numbers_and_misses_short_of_its_count(rb):
    env = rb("criterion", "add", "19 of 20 seeds below 0.890", "-e", "e1", "--metric", "int8.val_loss", "--at-most", "0.890", "--count", "19", "--of", "20", "--id", "cc")
    assert env.data["object"]["over"] == "count" and env.data["object"]["count"] == 19 and env.data["object"]["of"] == 20
    code, out = rb("criterion", "add", "the same, said", "-e", "e1", "--metric", "int8.val_loss", "--at-most", "0.890", "--count", "19", "--of", "20", "--id", "cc2", "--dry-run", text=True)
    assert "19 of 20 units' int8.val_loss ≤ 0.89" in out, out
    seeds(rb, TWENTY)
    f = finding(rb, "cc")
    assert f["rule_outcome"] == "misses", f
    assert f["says"]["rule"] == "criterion not met: 10 of 20 seeds at most 0.89; the rule asks for 19", f["says"]
    assert len(f["missed_units"]) == 10
    code, out = rb("show", "cc", text=True)
    assert "10 of 20 seeds at most 0.89; the rule asks for 19" in out
    code, out = rb("status", text=True)
    assert "10 of 20 seeds at most 0.89; the rule asks for 19" in out
    code, out = rb("context", text=True)
    assert "10 of 20 seeds at most 0.89" in out
    text = page(rb)
    assert "INT8: 10 of 20 seeds at most 0.89; the rule asks for 19" in text
    assert "Not met: INT8: 10 of 20 seeds at most 0.89" in text
    code, _ = rb("status", "--fail-on", "unmet", text=True)
    assert code == 1


def test_a_count_on_another_number_of_units_than_it_is_of_is_undetermined(rb):
    rb("criterion", "add", "19 of 20 seeds below 0.890", "-e", "e1", "--metric", "int8.val_loss", "--at-most", "0.890", "--count", "19", "--of", "20", "--id", "cc")
    seeds(rb, [0.88] * 18)
    f = finding(rb, "cc")
    assert f["rule_outcome"] == "undetermined" and f["says"]["rule"].endswith("the rule is of 20 seeds; the runs hold 18"), f["says"]
    seeds(rb, [0.88] * 3, start=19)
    assert finding(rb, "cc")["says"]["rule"].endswith("the rule is of 20 seeds; the runs hold 21")


def test_a_count_with_too_few_units_is_undetermined_and_one_with_enough_is_met(rb):
    rb("criterion", "add", "3 seeds below 0.890", "-e", "e1", "--metric", "int8.val_loss", "--at-most", "0.890", "--count", "3", "--id", "cc")
    seeds(rb, [0.88, 0.885])
    f = finding(rb, "cc")
    assert f["rule_outcome"] == "undetermined" and "the rule needs 3 of them, and the runs hold 2" in f["says"]["rule"], f["says"]
    seeds(rb, [0.95, 0.889], start=3)
    f = finding(rb, "cc")
    assert f["rule_outcome"] == "meets" and f["says"]["rule"] == "recorded values meet criterion: 3 of 4 seeds at most 0.89; the rule asks for 3", f["says"]
    code, out = rb("status", text=True)
    assert "Rules: 1 met" in out, out


def test_a_count_reads_the_runs_its_where_selects(rb):
    rb("spec", "set", "e1", "data", "--per-run")
    rb("criterion", "add", "2 of 3 holdout seeds below 0.890", "-e", "e1", "--metric", "int8.val_loss", "--at-most", "0.890", "--count", "2", "--of", "3", "--where", "data=holdout", "--id", "cc")
    seeds(rb, [0.88, 0.95, 0.885], data="holdout")
    seeds(rb, [0.99, 0.99], start=4, data="train")
    f = finding(rb, "cc")
    # units named by two per-run settings (seed and data) are units, as every reading names them
    assert f["rule_outcome"] == "meets" and f["says"]["rule"] == "recorded values meet criterion: 2 of 3 units at most 0.89; the rule asks for 2", f["says"]


def test_a_call_on_a_count_reads_as_made_on_an_earlier_reading_once_it_moves(rb):
    rb("criterion", "add", "2 of 3 seeds below 0.890", "-e", "e1", "--metric", "int8.val_loss", "--at-most", "0.890", "--count", "2", "--of", "3", "--id", "cc")
    seeds(rb, [0.88, 0.95, 0.885])
    rb("decide", "cc", "accept", "--why", "within budget")
    assert not [i for i in rb("status").data["open"] if i["code"] == "decision_outdated"]
    seeds(rb, [0.96], start=2)            # a second run of seed 2: its unit's mean moves
    assert [i for i in rb("status").data["open"] if i["code"] == "decision_outdated" and i["subject"] == "cc"]


def test_a_count_declared_before_its_runs_reads_so(rb):
    rb("criterion", "add", "2 of 3 seeds below 0.890", "-e", "e1", "--metric", "int8.val_loss", "--at-most", "0.890", "--count", "2", "--of", "3", "--id", "cc")
    for s, x in [(1, 0.88), (2, 0.95), (3, 0.885)]:
        Path(f"m{s}.json").write_text(json.dumps({"int8": {"val_loss": x}}), encoding="utf-8")
        rb("run", "add", "e1", "--from", f"m{s}.json", "--set", f"seed={s}")
    assert finding(rb, "cc")["timing"] == "before"


@pytest.mark.parametrize("argv,flag", [
    (["--count", "3", "--each"], "--each and --count"),
    (["--count", "3", "--best"], "--count and --best"),
])
def test_a_count_with_another_way_to_read_the_threshold_is_refused_by_name(rb, argv, flag):
    env = rb("criterion", "add", "x", "-e", "e1", "--metric", "int8.val_loss", "--at-most", "0.89", *argv, ok=False)
    assert not env.ok and env.errors[0].code == "E_OBJECT_INVALID" and flag in env.errors[0].message, env.errors
    assert "--each, --bound, --count K or --best" in env.errors[0].fix


@pytest.mark.parametrize("argv,said", [
    (["--better", "--count", "3"], "--each, --bound, --count and --best are for thresholds"),
    (["--at-most", "0.89", "--of", "3"], "--of says how many units a count or a best of N is of"),
    (["--at-most", "0.89", "--count", "4", "--of", "3"], "a count rule's count is at most the units it is of"),
    (["--equals", "0.89", "--tolerance", "0.01", "--best"], "--at-most or --at-least"),
])
def test_a_count_or_a_best_that_reads_no_threshold_is_refused(rb, argv, said):
    metric = "change.val_loss" if "--better" in argv else "int8.val_loss"
    env = rb("criterion", "add", "x", "-e", "e1", "--metric", metric, *argv, ok=False)
    assert not env.ok and env.errors[0].code == "E_OBJECT_INVALID" and said in env.errors[0].message, env.errors


# ---------------------------------------------------------------- N2: a best run

def test_a_best_run_is_named_as_the_best_with_its_unit_and_never_as_a_mean(rb):
    rb("criterion", "add", "best of 5 seeds reaches 0.889", "-e", "e1", "--metric", "int8.val_loss", "--at-most", "0.889", "--best", "--of", "5", "--id", "cb")
    seeds(rb, [0.8941, 0.8912, 0.8893, 0.8957, 0.8930])
    f = finding(rb, "cb")
    assert f["rule_outcome"] == "misses" and f["says"]["rule"] == "criterion not met: best of 5 seeds, seed 3: 0.8893 > 0.889", f["says"]
    assert f["comparison"]["estimate"] == pytest.approx(0.8893) and f["comparison"]["lo"] is None
    assert "best unit seed 3" in f["says"]["comparison"] and "mean" not in f["says"]["comparison"], f["says"]["comparison"]
    code, out = rb("show", "cb", text=True)
    assert "best of 5 seeds, seed 3: 0.8893 > 0.889" in out and "the best of 5 units' int8.val_loss ≤ 0.889" in out
    text = page(rb)
    assert "INT8: best of 5 seeds, seed 3: 0.8893 > 0.889" in text
    assert "INT8 is 0.89" not in text, re.findall(r".{30}INT8 is 0\.89.{30}", text)


def test_a_best_run_meets_its_rule_on_the_best_unit(rb):
    rb("criterion", "add", "best seed reaches 0.88", "-e", "e1", "--metric", "int8.val_loss", "--at-most", "0.880", "--best", "--id", "cb")
    seeds(rb, [0.8941, 0.8793, 0.8957])
    f = finding(rb, "cb")
    assert f["rule_outcome"] == "meets" and f["says"]["rule"] == "recorded values meet criterion: best of 3 seeds, seed 2: 0.8793 ≤ 0.88", f["says"]
    rb("criterion", "add", "best of 5", "-e", "e1", "--metric", "int8.val_loss", "--at-most", "0.880", "--best", "--of", "5", "--id", "cb5")
    assert finding(rb, "cb5")["says"]["rule"].endswith("the rule is of 5 seeds; the runs hold 3")


# ---------------------------------------------------------------- N3: a relative change

def test_a_relative_change_reads_in_percent_with_its_interval_said(rb):
    rb("criterion", "add", "INT8 adds at most 3% to the loss", "-e", "e1", "--metric", "change.val_loss", "--at-most", "3", "--relative", "--id", "cr")
    seeds(rb, [0.98, 0.99, 0.97, 0.98], fp32=[0.96, 0.96, 0.95, 0.96])
    f = finding(rb, "cr")
    s_mean, r_mean = (0.98 + 0.99 + 0.97 + 0.98) / 4, (0.96 * 3 + 0.95) / 4
    assert f["comparison"]["estimate"] == pytest.approx((s_mean - r_mean) / r_mean * 100)
    assert f["comparison"]["method"] == "relative change of the means, paired, delta-method t interval" and f["comparison"]["lo"] is not None
    assert "%" in f["says"]["comparison"] and "nats" not in f["says"]["comparison"], f["says"]["comparison"]
    assert f["rule_outcome"] == "meets" and f["says"]["rule"].endswith("≤ 3%"), f["says"]
    code, out = rb("show", "cr", text=True)
    assert "change.val_loss as a relative change ≤ 3%" in out and "delta-method t interval" in out
    text = page(rb)
    assert re.search(r"INT8 is 2\.\d+% higher than the FP32 baseline", text), re.findall(r"INT8 is [^,]{0,60}", text)
    assert not re.search(r"\d points", text), re.findall(r".{40}\d points.{20}", text)


def test_a_relative_change_with_no_interval_meets_on_its_estimate(rb):
    rb("criterion", "add", "INT8 adds at most 3% to the loss", "-e", "e1", "--metric", "change.val_loss", "--at-most", "3", "--relative", "--id", "cr")
    seeds(rb, [0.98], fp32=[0.96])
    f = finding(rb, "cr")
    assert f["rule_outcome"] == "meets" and f["says"]["rule"].startswith("estimate meets criterion"), f["says"]
    assert f["comparison"]["lo"] is None and f["comparison"]["method"] == "relative change of the means, paired"


def test_a_relative_change_of_a_level_is_refused(rb):
    env = rb("criterion", "add", "x", "-e", "e1", "--metric", "int8.val_loss", "--at-most", "3", "--relative", ok=False)
    assert not env.ok and "a 'relative' reading compares two arms" in env.errors[0].message, env.errors


# ---------------------------------------------------------------- N4: a cited difference of means

def lone_seed(rb):
    """INT8 on 3 seeds, FP32 on 2: rb's paired reading leaves the third INT8 run out."""
    for s, x in [(1, 0.96), (2, 0.97)]:
        rb("run", "add", "e1", f"fp32.val_loss={x}", "--set", f"seed={s}", "--produced-at", "2026-05-01")
    for s, x in [(1, 0.98), (2, 0.99), (3, 1.01)]:
        rb("run", "add", "e1", f"int8.val_loss={x}", "--set", f"seed={s}", "--produced-at", "2026-05-01")


def test_a_cited_difference_of_means_is_read_as_one_and_is_comparable(rb):
    # the arms' means differ by 0.0283 over all their units; the paired reading of seeds 1 and 2 reads 0.02
    Path("paper.md").write_text("# Paper\n\nINT8 adds 0.0283 to the validation loss.\n", encoding="utf-8")
    quote = ["--source", "paper.md", "--quote", "adds 0.0283 to the validation loss"]
    rb("criterion", "add", "the paper's difference, paired", "-e", "e1", "--metric", "change.val_loss", "--equals", "0.0283", "--tolerance", "0.00005", *quote, "--id", "cp")
    rb("criterion", "add", "the paper's difference of means", "-e", "e1", "--metric", "change.val_loss", "--equals", "0.0283", "--tolerance", "0.00005", "--means", *quote, "--id", "cm")
    lone_seed(rb)
    # without --means rb's paired reading is another estimand, and the cited number is not compared with it
    assert any(p["code"] == "not_comparable" for p in finding(rb, "cp")["integrity"])
    f = finding(rb, "cm")
    assert not [p for p in f["integrity"] if p["code"] == "not_comparable"], f["integrity"]
    assert not [c for c in f["caveats"] if c["code"] == "cited_differs"], f["caveats"]
    assert f["comparison"]["estimate"] == pytest.approx((0.98 + 0.99 + 1.01) / 3 - (0.96 + 0.97) / 2)
    assert f["comparison"]["method"].startswith("difference of means, as cited")
    assert f["rule_outcome"] == "meets" and f["says"]["headline"].startswith("reproduced"), f["says"]
    code, out = rb("show", "cm", text=True)
    assert "change.val_loss as a difference of means = 0.0283 ± 0.00005" in out and "difference of means, as cited" in out
    assert ", a difference of means, as cited" in page(rb)


def test_a_difference_of_means_is_only_a_cited_number_s(rb):
    env = rb("criterion", "add", "x", "-e", "e1", "--metric", "change.val_loss", "--at-most", "0.05", "--means", ok=False)
    assert not env.ok and "--source and --quote" in env.errors[0].message, env.errors
    env = rb("criterion", "add", "x", "-e", "e1", "--metric", "change.val_loss", "--at-most", "0.05", "--means", "--relative", ok=False)
    assert not env.ok and "--relative and --means" in env.errors[0].message, env.errors


# ---------------------------------------------------------------- the SDK and MCP

def test_the_sdk_declares_each_of_the_four(rb):
    import rabbit_brain as rbsdk
    state = rbsdk.open()
    exp = state.experiment("e1")
    cc = exp.criterion("19 of 20 below 0.890", metric="int8.val_loss", at_most=0.890, count=19, of=20)
    cb = exp.criterion("best of 5 reaches 0.889", metric="int8.val_loss", at_most=0.889, over="best", of=5)
    cr = exp.criterion("at most 3%", metric="change.val_loss", at_most=3, measure="relative")
    seeds(rb, [0.88] * 5, fp32=[0.87] * 5)
    assert cc.finding().says["rule"].endswith("the rule is of 20 seeds; the runs hold 5")
    assert cb.finding().says["rule"].startswith("recorded values meet criterion: best of 5 seeds")
    assert cr.finding().comparison.method.startswith("relative change of the means")


def test_mcp_declares_a_count(rb):
    from rbtest import Mcp
    server = Mcp("claude-code")
    try:
        got = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "criterion_add", "arguments": {
            "statement": "19 of 20 below 0.890", "experiment": "e1", "metric": "int8.val_loss", "at_most": 0.89, "count": 19, "of": 20}}})["result"]
    finally:
        server.close()
    obj = got["structuredContent"]["data"]["object"]
    assert obj["over"] == "count" and obj["count"] == 19 and obj["of"] == 20, got


# ---------------------------------------------------------------- Codex, round 6

@pytest.mark.parametrize("rule", [
    ["--metric", "int8.val_loss", "--at-most", "0.89", "--count", "2", "--of", "3"],
    ["--metric", "int8.val_loss", "--at-most", "0.89", "--best", "--of", "3"],
    ["--metric", "change.val_loss", "--at-most", "3", "--relative"],
])
def test_a_clone_keeps_each_rule_whole_and_in_format_2(rb, tmp_path, rule):
    rb("criterion", "add", "the rule", "-e", "e1", *rule, "--id", "c1")
    said = rb("show", "c1").data["object"]
    rb("export", "--with-record", "--out", "bundle")
    rb("clone", "bundle", str(tmp_path / "copy"))
    copied = rbyaml_load(tmp_path / "copy" / ".rb" / "experiments" / "e1" / "criteria" / "c1.yaml")
    assert copied["rb"] == 2, copied
    for key in ("over", "count", "of", "measure"):
        assert copied.get(key) == said.get(key) or (not copied.get(key) and not said.get(key)), (key, copied, said)


def test_a_cited_difference_of_means_is_cloned_whole(rb, tmp_path):
    Path("paper.md").write_text("INT8 adds at most 3 to the loss.\n", encoding="utf-8")
    rb("criterion", "add", "the paper's", "-e", "e1", "--metric", "change.val_loss", "--at-most", "3", "--means", "--source", "paper.md", "--quote", "at most 3", "--id", "c1")
    rb("export", "--with-record", "--out", "bundle")
    rb("clone", "bundle", str(tmp_path / "copy"))
    copied = rbyaml_load(tmp_path / "copy" / ".rb" / "experiments" / "e1" / "criteria" / "c1.yaml")
    assert copied["measure"] == "means" and copied["origin"] == "inherited" and copied["rb"] == 2, copied


def test_a_relative_interval_keeps_the_spread_of_arms_that_move_together(rb):
    """Codex: the expanded variance cancelled to 0 for arms that move together, a zero-width interval met a bound the
    true interval misses."""
    rb("criterion", "add", "bound", "-e", "e1", "--metric", "change.val_loss", "--at-most", "0.0000006", "--bound", "--relative", "--id", "cb")
    for s, x in [(1, "1"), (2, "2"), (3, "3")]:
        rb("run", "add", "e1", f"fp32.val_loss={x}", f"int8.val_loss={x}.00000001", "--set", f"seed={s}")
    f = finding(rb, "cb")
    assert f["comparison"]["lo"] == pytest.approx(-1.210344279609382e-7, rel=1e-6) and f["comparison"]["hi"] == pytest.approx(1.121034427960938e-6, rel=1e-6), f["comparison"]
    assert f["rule_outcome"] == "misses"


def test_no_sampling_interval_across_fixed_design_cells(rb):
    """Codex: a relative change and a difference of means pooled fixed design cells as independent samples."""
    rb("spec", "set", "e1", "seed", "--design-factor")
    Path("paper.md").write_text("at most 0.2\n", encoding="utf-8")
    rb("criterion", "add", "relative", "-e", "e1", "--metric", "change.val_loss", "--at-most", "20", "--relative", "--bound", "--id", "cr")
    rb("criterion", "add", "means", "-e", "e1", "--metric", "change.val_loss", "--at-most", "0.2", "--means", "--bound", "--source", "paper.md", "--quote", "at most 0.2", "--id", "cm")
    for s in (1, 2, 3):
        rb("run", "add", "e1", f"fp32.val_loss={s}", f"int8.val_loss={s}.1", "--set", f"seed={s}")
    for cid in ("cr", "cm"):
        f = finding(rb, cid)
        assert f["comparison"]["lo"] is None and f["rule_outcome"] == "undetermined", f
        assert "is not read across fixed design cells" in f["says"]["rule"], f["says"]


def rbyaml_load(p):
    from rbtest import rbyaml
    return rbyaml.loads(Path(p).read_text(encoding="utf-8"))


def test_across_disjoint_design_cells_nothing_is_reproduced(rb):
    """Codex pass 2: cells that each hold one arm gave a relative change and a difference of means their intervals."""
    rb("spec", "set", "e1", "seed", "--design-factor")
    Path("paper.md").write_text("at most 20\n", encoding="utf-8")
    rb("criterion", "add", "relative", "-e", "e1", "--metric", "change.val_loss", "--at-most", "20", "--bound", "--relative", "--id", "cr")
    rb("criterion", "add", "means", "-e", "e1", "--metric", "change.val_loss", "--at-most", "20", "--bound", "--means", "--source", "paper.md", "--quote", "at most 20", "--id", "cm")
    for s in (1, 2, 3):
        rb("run", "add", "e1", f"fp32.val_loss={s}", "--set", f"seed={s}")
        rb("run", "add", "e1", f"int8.val_loss={s}.1", "--set", f"seed={s + 3}")
    for cid in ("cr", "cm"):
        f = finding(rb, cid)
        assert f["rule_outcome"] not in ("meets", "misses") and (f["comparison"] or {}).get("lo") is None, f


def test_design_cells_keep_their_guards_under_a_relative_change(rb):
    """Codex pass 2: --min-n and an incomplete cell leave the rule undetermined, as for any rule across cells."""
    rb("spec", "set", "e1", "seed", "--design-factor")
    rb("criterion", "add", "relative", "-e", "e1", "--metric", "change.val_loss", "--at-most", "20", "--min-n", "3", "--relative", "--id", "cr")
    for s in (1, 2, 3):
        rb("run", "add", "e1", f"fp32.val_loss={s}", f"int8.val_loss={s}.1", "--set", f"seed={s}")
    f = finding(rb, "cr")
    assert f["rule_outcome"] == "undetermined" and f["comparison"]["lo"] is None, f["says"]


def test_a_count_across_design_cells_says_the_units_it_judges(rb):
    """Codex pass 2: the count said '4 of 6 design cells' where it judged six seeds in two cells."""
    rb("spec", "set", "e1", "width", "--per-run", "--design-factor")
    rb("criterion", "add", "5 of 6", "-e", "e1", "--metric", "int8.val_loss", "--at-most", "2", "--count", "5", "--of", "6", "--id", "cc")
    for w in (256, 512):
        for s in (1, 2, 3):
            rb("run", "add", "e1", f"int8.val_loss={s}", "--set", f"seed={s}", "--set", f"width={w}")
    f = finding(rb, "cc")
    assert f["says"]["rule"] == "criterion not met: 4 of 6 units at most 2; the rule asks for 5", f["says"]
    assert "cells meet" not in f["says"]["units"], f["says"]["units"]


# ---------------------------------------------------------------- N6: one cell is read on the rule's own estimand

def batches(rb, cells):
    """For each batch (a fixed design factor, declared first), (int8, fp32) val_loss per seed, both arms in one run."""
    for b, pairs in cells.items():
        for s, (x, y) in enumerate(pairs, 1):
            rb("run", "add", "e1", f"int8.val_loss={x}", f"fp32.val_loss={y}", "--set", f"seed={s}", "--set", f"batch={b}", "--produced-at", "2026-05-01")


# at batch 32 INT8 adds 0.0045 nats to a loss of 0.80: 0.5625% (a plain difference of 0.0045 would meet "at most 0.5");
# at batch 64 it adds 0.0008: 0.1%
CELLS = {32: [(0.8045, 0.80), (0.8145, 0.81), (0.7945, 0.79)], 64: [(0.8008, 0.80), (0.8108, 0.81), (0.7908, 0.79)]}


def test_a_relative_change_at_one_design_cell_is_read_as_a_relative_change(rb):
    """MP-1104-09's blocker: --where on a design factor read the plain paired difference, and met."""
    rb("spec", "set", "e1", "batch", "--per-run", "--design-factor")
    rb("criterion", "add", "INT8 adds at most 0.5% at batch 32", "-e", "e1", "--metric", "change.val_loss", "--at-most", "0.5", "--relative", "--where", "batch=32", "--id", "c32")
    batches(rb, CELLS)
    f = finding(rb, "c32")
    assert f["comparison"]["method"] == "relative change of the means, paired, delta-method t interval", f["comparison"]
    assert f["comparison"]["estimate"] == pytest.approx(0.0045 / 0.80 * 100), f["comparison"]
    assert f["rule_outcome"] == "misses" and f["says"]["rule"].startswith("criterion not met: estimate +0.5625% > 0.5%"), f["says"]
    assert "%" in f["says"]["comparison"] and "nats" not in f["says"]["comparison"], f["says"]["comparison"]
    text = page(rb)
    assert "INT8 is 0.5625% higher than the FP32 baseline" in text and "0.45" not in re.sub(r"\d0\.45", "", text).split("Comparison")[0], re.findall(r"INT8 is [^()]*\([^)]*\)", text)


def test_a_cited_difference_of_means_at_one_design_cell_is_read_as_one(rb):
    """The blocker's sibling: --means with --where on a design factor read the paired difference."""
    rb("spec", "set", "e1", "batch", "--per-run", "--design-factor")
    Path("paper.md").write_text("At batch 32 INT8 adds 0.0045 to the validation loss.\n", encoding="utf-8")
    rb("criterion", "add", "the paper's difference at batch 32", "-e", "e1", "--metric", "change.val_loss", "--equals", "0.0045", "--tolerance", "0.00005", "--means",
       "--source", "paper.md", "--quote", "INT8 adds 0.0045 to the validation loss", "--where", "batch=32", "--id", "cm")
    batches(rb, CELLS)
    f = finding(rb, "cm")
    assert f["comparison"]["method"] == "difference of means, as cited, Welch interval", f["comparison"]
    assert f["comparison"]["estimate"] == pytest.approx(0.0045)


def means_and_relative_at_32(rb):
    rb("spec", "set", "e1", "batch", "--per-run", "--design-factor")
    Path("paper.md").write_text("At batch 32 INT8 adds 0.0045 to the validation loss.\n", encoding="utf-8")
    rb("criterion", "add", "the paper's difference at batch 32", "-e", "e1", "--metric", "change.val_loss", "--equals", "0.0045", "--tolerance", "0.00005", "--means",
       "--source", "paper.md", "--quote", "INT8 adds 0.0045 to the validation loss", "--where", "batch=32", "--id", "cm")
    rb("criterion", "add", "INT8 adds at most 0.5% at batch 32", "-e", "e1", "--metric", "change.val_loss", "--at-most", "0.5", "--relative", "--where", "batch=32", "--id", "c32")
    batches(rb, CELLS)


def test_a_means_card_says_its_interval_in_the_digits_of_its_reading_line(rb):
    """MP-1106-2212 later 3: "(-0.61 to -0.19)" on the card beside "-0.4 [-0.6078, -0.1922]" on rb show's line."""
    means_and_relative_at_32(rb)
    said = finding(rb, "cm")["says"]["comparison"]
    lo, hi = re.search(r"\[([^,\]]+), ([^\]]+)\]", said).groups()
    assert len(lo.split(".")[1]) > 2, said
    assert f"({lo} to {hi})" in page(rb), (lo, hi)


def test_a_means_card_says_each_bound_as_its_reading_line_does(rb):
    """Each bound in its own digits, as rb show writes it: "+0.01004, +4.544", never "+0.01004 to +4.54396"."""
    Path("paper.md").write_text("INT8 adds 2.277 to the validation loss.\n", encoding="utf-8")
    rb("criterion", "add", "the paper's difference", "-e", "e1", "--metric", "change.val_loss", "--equals", "2.277", "--tolerance", "0.0005", "--means",
       "--source", "paper.md", "--quote", "INT8 adds 2.277 to the validation loss", "--id", "cm")
    seeds(rb, [3.277, 4.277, 5.277], [1, 2, 3])
    said = finding(rb, "cm")["says"]["comparison"]
    lo, hi = re.search(r"\[([^,\]]+), ([^\]]+)\]", said).groups()
    assert len(lo.split(".")[1]) != len(hi.split(".")[1]), said
    assert f"({lo} to {hi})" in page(rb), (lo, hi)


def test_a_relative_card_says_each_bound_as_its_reading_line_does(rb):
    """The orchestrator, Nov 7 10:05 (MP-1107-0840 later 4): rule-relative's page said "(-22.60 to -22.37)" where rb show
    says "[-22.6, -22.37]": a bound's decimals padded with a 0 its line does not write. Each in its own digits, on the
    card, its big reading and the list's sentence alike."""
    rb("criterion", "add", "INT8 improves val_loss by 22.5% over FP32", "-e", "e1", "--metric", "change.val_loss", "--at-most", "-22.5", "--relative", "--id", "c1")
    for i, (fp32, int8) in enumerate([(1.4635, 1.1346), (1.4602, 1.1311), (1.4651, 1.1362)], 1):
        rb("run", "add", "e1", f"fp32.val_loss={fp32}", f"int8.val_loss={int8}", "--set", f"seed={i}", "--produced-at", "2026-05-01")
    lo, hi = re.search(r"\[([^,\]]+), ([^\]]+)\]", finding(rb, "c1")["says"]["comparison"]).groups()
    assert len(lo.split(".")[1]) != len(hi.split(".")[1]), (lo, hi)   # the case: one bound written in fewer digits
    page_text = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", page(rb))))
    assert page_text.count(f"({lo} to {hi})") >= 2, (lo, hi)            # the card's sentence and the list's
    padded = lo + "0" if len(lo.split(".")[1]) < len(hi.split(".")[1]) else hi + "0"
    assert padded not in page_text, padded


@pytest.mark.parametrize("rule", ["means", "means in exponent form", "relative"])
def test_a_grid_cell_a_means_rule_owns_says_its_reading_as_the_card(rb, rule):
    """MP-1107-0443's blocker: rounding the model's reading.lo and hi to rb show's digits put the grid's cell out of step
    with the card ("0.030 nats higher (+0.021 to +0.039)" beside "0.02968 ... (+0.0205 to +0.03885)"). The rule owns the
    cell (both arms in runs of their own, no design cell); the grid says the reading the card says, its estimate too
    (0.8.0's grid said rb compare's change of the means, "0.02967", beside the card's "0.02968"). A relative rule reads
    the cell otherwise (rb compare's read_by), in percent: the cell says its own change in nats, in its own digits,
    linked to the rule, and never the rule's numbers or digits (P12, P5), while the card says the rule's reading."""
    rb("spec", "vary", "e1", "precision")
    rb("spec", "set", "e1", "fp32.precision", "fp32", "--no-verify")
    rb("spec", "set", "e1", "int8.precision", "int8", "--no-verify")
    # the second, scaled by 1e-7, gives bounds rb show writes in exponent form ("+3.885e-09"): MP-1107-0443's second pass
    small = rule == "means in exponent form"
    said = "3.17e-9" if small else "0.0317"
    if rule == "relative":
        rb("criterion", "add", "INT8 adds at most 3.5%", "-e", "e1", "--metric", "change.val_loss", "--at-most", "3.5", "--relative", "--id", "cm")
    else:
        Path("paper.md").write_text(f"INT8 adds {said} to the validation loss.\n", encoding="utf-8")
        rb("criterion", "add", "INT8 adds what the paper says", "-e", "e1", "--metric", "change.val_loss", "--equals", said, "--tolerance", "5.0e-12" if small else "0.00005",
           "--means", "--source", "paper.md", "--quote", f"adds {said} to the validation loss", "--id", "cm")
    scale = (lambda x: f"{x * 1e-7:.10g}") if small else (lambda x: f"{x:g}")  # noqa: E731
    for i, f in enumerate([0.9612, 0.9655, 0.9701], 1):
        rb("run", "add", "e1", f"fp32.val_loss={scale(f)}", "--set", f"seed={i}", "--produced-at", "2026-05-01")
    for i, f in enumerate([0.9931, 0.9978, 1.000309 if small else 1.0003, 0.9899], 1):
        rb("run", "add", "e1", f"int8.val_loss={scale(f)}", "--set", f"seed={i}", "--produced-at", "2026-05-01")
    rb("page", "--out", "page.html")
    raw = Path("page.html").read_text(encoding="utf-8")
    flat = lambda h: re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", h)))  # noqa: E731
    grids = [flat(g) for g in re.findall(r'<table class="grid">.*?</table>', raw, re.S)]
    est, lo, hi = re.search(r": ([^ ]+) \[([^,\]]+), ([^\]]+)\]", finding(rb, "cm")["says"]["comparison"]).groups()
    # each number as rb show writes it, in plain decimals where it writes an exponent
    plain = lambda t: ("+" if t.startswith("+") else "") + format(__import__("decimal").Decimal(t), "f")  # noqa: E731
    card = f"({plain(lo)} to {plain(hi)})"
    amount = plain(est).lstrip("+") + ("%" if rule == "relative" else " nats")
    page_text = flat(raw)
    assert grids and page_text.count(card) >= 2, (card, grids)          # the card's sentence and the list's
    assert f"INT8 is {amount} higher than the FP32 baseline {card}" in page_text, (amount, card)
    if rule == "relative":
        # in its own digits (P5), as a cell no rule reads says them: the rule's edges, in %, are none of the cell's
        assert any("0.030 nats higher than FP32" in g for g in grids) and not any("%" in g for g in grids), grids
        assert re.search(r'<span class="gd"><a [^>]*href="#cr?-cm"[^>]*>0\.030 nats higher than FP32</a>', raw), "linked to its rule"
    else:
        assert any(f"{amount} higher than FP32 {card}" in g for g in grids), (amount, card, grids)


def test_rb_compare_names_every_criterion_that_reads_a_cell(rb):
    """MP-1106-2212 later 4: the batch 32 cell named the rule whose number it shows, and not the relative rule beside it."""
    means_and_relative_at_32(rb)
    env = rb("compare", "e1")
    cell = next(r for r in env.data["rows"] if r["variant"] == "int8" and r["where"].get("batch") == 32)["metrics"]["val_loss"]
    assert cell["delta"] == pytest.approx(0.0045) and [x["criterion"] for x in cell.get("read_beside", [])] == ["c32"], cell
    code, out = rb("compare", "e1", text=True)
    assert "(also read by c32)" in out and "read otherwise by a criterion, beside the change shown: c32 reads" in out, out


def test_across_design_cells_each_cell_reads_the_rule_s_own_estimand(rb):
    """MP-1104-09 should: 'both cells meet the threshold' from the cells' plain differences, in the metric's unit."""
    rb("spec", "set", "e1", "batch", "--per-run", "--design-factor")
    rb("criterion", "add", "INT8 adds at most 0.5%", "-e", "e1", "--metric", "change.val_loss", "--at-most", "0.5", "--relative", "--id", "cr")
    batches(rb, CELLS)
    f = finding(rb, "cr")
    assert f["rule_outcome"] == "undetermined", f["says"]
    cells = {c["key"]: c["estimate"] for c in f["comparison"]["design"]["cells"]}
    assert cells == pytest.approx({"batch=32": 0.5625, "batch=64": 0.1}), cells
    assert f["comparison"]["design"]["meets"] == 1
    assert "1 of 2 cells meet the threshold" in f["says"]["units"] and "+0.1% to +0.5625%" in f["says"]["units"], f["says"]["units"]
    assert f["comparison"]["method"].startswith("relative change") and "%" in f["says"]["comparison"] and "nats" not in f["says"]["comparison"], f["says"]


def test_a_relative_rule_s_figure_across_cells_says_its_amounts_in_percent_as_its_card(rb):
    """MP-1106-2212 should 2: the figure's words said "+0.56 ... estimate: +0.33" beside the card's 0.3312%."""
    rb("spec", "set", "e1", "batch", "--per-run", "--design-factor")
    rb("criterion", "add", "INT8 adds at most 0.5%", "-e", "e1", "--metric", "change.val_loss", "--at-most", "0.5", "--relative", "--id", "cr")
    batches(rb, CELLS)
    rb("page", "--out", "page.html")
    legend = [html.unescape(m) for m in re.findall(r'<p class="figleg figv">(.*?)</p>', Path("page.html").read_text(encoding="utf-8"))]
    assert legend and all(x.endswith("estimate: +0.3312%") for x in legend), legend
    # each amount in its own digits, as rb show says them ("+0.1% to +0.5625%"), never padded to "+0.10%" (MP-1107-1620)
    assert "batch 32: +0.5625% (outside the rule)" in legend[0] and "batch 64: +0.1%" in legend[0] and "+0.10%" not in legend[0], legend
    assert "INT8 is 0.3312% higher" in page(rb)


def pooled(rb, a, b):
    """data a per-run setting the rule pools within each seed, the unit: (int8, fp32) val_loss per seed at data=a and b."""
    rb("spec", "set", "e1", "data", "--per-run")
    rb("spec", "set", "e1", "population.unit", "seed", "--no-verify")
    for d, pairs in (("a", a), ("b", b)):
        for s, (x, y) in enumerate(pairs, 1):
            nums = [f"int8.val_loss={x}"] + ([f"fp32.val_loss={y}"] if y is not None else [])
            rb("run", "add", "e1", *nums, "--set", f"seed={s}", "--set", f"data={d}", "--produced-at", "2026-05-01")


def pooled_misses(f):
    return [c["text"] for c in f["caveats"] if c["code"] == "pooled_misses"]


def test_a_pooled_relative_change_names_the_value_it_misses_at_in_percent(rb):
    """A value's own reading of a relative rule is its relative change, never its plain difference."""
    rb("criterion", "add", "INT8 adds at most 0.5%", "-e", "e1", "--metric", "change.val_loss", "--at-most", "0.5", "--relative", "--id", "cr")
    pooled(rb, CELLS[32], [(0.80, 0.80), (0.81, 0.81), (0.79, 0.79)])
    f = finding(rb, "cr")
    assert f["rule_outcome"] == "meets", f["says"]
    said = pooled_misses(f)
    assert said and "data=a (0.5625%" in said[0], f["caveats"]


def test_a_pooled_best_names_only_the_value_whose_best_unit_misses(rb):
    rb("criterion", "add", "the best seed reaches 0.88", "-e", "e1", "--metric", "int8.val_loss", "--at-most", "0.88", "--best", "--id", "cb")
    # at data=a the best seed reaches 0.84 (its mean, 0.913, would not); at data=b none does; pooled, seed 1 reads 0.87
    pooled(rb, [(0.84, None), (0.95, None), (0.95, None)], [(0.90, None), (0.91, None), (0.92, None)])
    f = finding(rb, "cb")
    assert f["rule_outcome"] == "meets", f["says"]
    said = pooled_misses(f)
    assert said and "data=b (best unit 0.9" in said[0] and "data=a" not in said[0], said


def test_a_pooled_count_names_no_value(rb):
    """A count's K is of all the units it reads: no value's units alone are held to it, nor read as a mean."""
    rb("criterion", "add", "2 seeds at most 0.9", "-e", "e1", "--metric", "int8.val_loss", "--at-most", "0.9", "--count", "2", "--id", "cc")
    # pooled, seeds 1 and 2 read 0.865 and 0.875; data=b's mean (0.92) is past 0.9, which no count asks of it
    pooled(rb, [(0.85, None), (0.86, None), (0.95, None)], [(0.88, None), (0.89, None), (0.99, None)])
    f = finding(rb, "cc")
    assert f["rule_outcome"] == "meets" and not pooled_misses(f), f["caveats"]


def test_a_count_across_design_cells_marks_no_cell_outside_on_the_page(rb):
    """A count judges units, never cells: the page's figure marks no cell outside the rule by its mean."""
    rb("criterion", "add", "4 of 6 seeds at most 0.9", "-e", "e1", "--metric", "int8.val_loss", "--at-most", "0.9", "--count", "4", "--id", "cc")
    rb("spec", "set", "e1", "batch", "--per-run", "--design-factor")
    for b, xs in ((32, [0.85, 0.86, 0.87]), (64, [0.88, 0.89, 0.99])):
        for s, x in enumerate(xs, 1):
            rb("run", "add", "e1", f"int8.val_loss={x}", "--set", f"seed={s}", "--set", f"batch={b}", "--produced-at", "2026-05-01")
    assert finding(rb, "cc")["rule_outcome"] == "meets"
    rb("page", "--out", "page.html")
    assert "(outside the rule)" not in Path("page.html").read_text(encoding="utf-8")


def test_mcp_keeps_format_1_s_schema_and_adds_format_2_s(rb):
    """MP-1104-09: resources/list renamed rb-v1.schema.json; a 0.7 client still finds format 1's at rb://format."""
    from rbtest import Mcp
    server = Mcp("claude-code")
    try:
        listed = {r["uri"]: r["name"] for r in server.handle({"jsonrpc": "2.0", "id": 1, "method": "resources/list", "params": {}})["result"]["resources"]}
        one = json.loads(server.handle({"jsonrpc": "2.0", "id": 2, "method": "resources/read", "params": {"uri": "rb://format"}})["result"]["contents"][0]["text"])
        two = json.loads(server.handle({"jsonrpc": "2.0", "id": 3, "method": "resources/read", "params": {"uri": "rb://format-2"}})["result"]["contents"][0]["text"])
    finally:
        server.close()
    assert listed["rb://format"] == "rb-v1.schema.json" and listed["rb://format-2"] == "rb-v2.schema.json", listed
    v1 = json.loads((Path(__file__).parent.parent / "schema" / "rb-v1.schema.json").read_text(encoding="utf-8"))
    assert one == v1 and two["$id"].endswith("rb-v2.schema.json"), (one.get("$id"), two.get("$id"))


def test_a_count_and_a_best_say_what_they_pass_on_the_page(rb):
    """MP-1104-09 should: the page's 'passes when' said 'The estimate is at most ...' for a count and a best."""
    rb("criterion", "add", "19 of 20 below 0.890", "-e", "e1", "--metric", "int8.val_loss", "--at-most", "0.890", "--count", "19", "--of", "20", "--id", "cc")
    rb("criterion", "add", "best of 20 reaches 0.885", "-e", "e1", "--metric", "int8.val_loss", "--at-most", "0.885", "--best", "--of", "20", "--id", "cb")
    seeds(rb, TWENTY)
    text = page(rb)
    assert "At least 19 of the 20 units' values are at most 0.89 nats" in text, re.findall(r"passes when .{0,80}", text)
    assert "The best of the 20 units' values is at most 0.885 nats" in text, re.findall(r"passes when .{0,80}", text)
    assert "The estimate is at most 0.89" not in text and "The estimate is at most 0.885" not in text


def test_a_met_count_reads_on_its_recorded_values_whatever_the_interval(rb):
    """MP-1104-09 should: AGENTS.md says a met count reads 'recorded values meet criterion'; the build said 'criterion met'."""
    rb("criterion", "add", "4 of 5 seeds at most 0.89", "-e", "e1", "--metric", "int8.val_loss", "--at-most", "0.89", "--count", "4", "--of", "5", "--id", "cc")
    seeds(rb, [0.86, 0.87, 0.88, 0.885, 0.95])
    f = finding(rb, "cc")
    assert f["comparison"]["lo"] is not None and f["rule_outcome"] == "meets"
    assert f["says"]["headline"].startswith("recorded values meet criterion"), f["says"]
    data = rb("status").data
    assert data["tally_on_estimate"].get("meets") == 1, (data["tally"], data["tally_on_estimate"])


def test_a_relative_change_s_call_card_says_percent_never_points(rb):
    """A missed relative rule's call card and figure said '0.56 points higher' where its own card said 0.56%."""
    rb("criterion", "add", "INT8 adds at most 0.5%", "-e", "e1", "--metric", "change.val_loss", "--at-most", "0.5", "--relative", "--id", "cr")
    pooled(rb, CELLS[32], CELLS[32])
    assert finding(rb, "cr")["rule_outcome"] == "misses"
    text = page(rb)
    assert not re.search(r"\d points (higher|lower)", text), re.findall(r".{60}points.{20}", text)


def test_a_relative_change_s_card_says_its_reading_in_rb_s_digits(rb):
    """MP-1104-09 later: '-0.3821%' on the rule's line beside '0.38%' on the card."""
    rb("metric", "add", "top1", "--maximize", "--unit", "percent")
    rb("criterion", "add", "INT8 keeps top1 within 0.5%", "-e", "e1", "--metric", "change.top1", "--at-least", "-0.5", "--relative", "--id", "cr")
    for s, (x, y) in enumerate([(75.71, 76.0), (75.62, 75.95), (75.80, 76.1), (75.70, 75.97)], 1):
        rb("run", "add", "e1", f"int8.top1={x}", f"fp32.top1={y}", "--set", f"seed={s}", "--produced-at", "2026-05-01")
    f = finding(rb, "cr")
    assert "estimate -0.3914%" in f["says"]["rule"], f["says"]
    text = page(rb)
    assert "INT8 is 0.3914% lower than the FP32 baseline (-0.4438 to -0.339)" in text, re.findall(r"INT8 is [^()]*\([^)]*\)", text)
    assert "0.39% lower" not in text and "-0.3390" not in text


def test_a_level_s_card_takes_no_trailing_0_from_the_grid(tmp_path, monkeypatch, capsys):
    """MP-1104-03 should 1: 'wide is 0.740' (the grid's digits) beside 'margin 0.14' and rb status's 0.74."""
    as_the_user(monkeypatch)
    monkeypatch.setenv("RB_ACTOR", "human:ana")
    root = tmp_path / "w"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    monkeypatch.chdir(root)
    for argv in (["init", "INT8 ResNet-50", "--no-agents"], ["metric", "add", "top1", "--maximize"],
                 ["experiment", "add", "x", "--id", "e1", "--purpose", "p", "--baseline", "base", "--candidate", "wide"], ["spec", "set", "e1", "seed", "--per-run"],
                 ["criterion", "add", "wide reaches 0.6", "-e", "e1", "--metric", "wide.top1", "--at-least", "0.6", "--id", "c1"],
                 ["run", "add", "e1", "wide.top1=0.74", "base.top1=0.735", "--set", "seed=1", "--produced-at", "2026-05-01"], ["page", "--out", "page.html"]):
        main(argv, typed=True)
    capsys.readouterr()
    text = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", Path("page.html").read_text(encoding="utf-8"))))
    assert "wide is 0.74," in text and "margin 0.14" in text, re.findall(r"wide is \S+|margin \S+", text)
    assert not re.search(r"0\.7400?\b|0\.1400?\b|0\.7350\b", text), re.findall(r"0\.(?:74|14|735|005)0+\b", text)


def test_a_cited_best_is_never_set_against_the_arms_means(rb):
    """Codex 6b: a cited best read undetermined, not comparable, once a run of one arm alone made the means differ."""
    Path("paper.md").write_text("The best change is at most 0.05 nats.\n", encoding="utf-8")
    rb("criterion", "add", "cited best", "-e", "e1", "--metric", "change.val_loss", "--at-most", "0.05", "--best", "--source", "paper.md",
       "--quote", "The best change is at most 0.05 nats.", "--id", "cb")
    seeds(rb, [0.96, 1.96, 1.96], fp32=[0.96, 0.96, 0.96])
    rb("run", "add", "e1", "int8.val_loss=99", "--set", "seed=4", "--produced-at", "2026-05-01")
    f = finding(rb, "cb")
    assert f["rule_outcome"] == "meets" and not [p for p in f["integrity"] if p["code"] == "not_comparable"], (f["says"], f["integrity"])


def test_a_relative_change_s_runs_keep_their_metric_s_unit_and_its_figure_says_percent(rb):
    """Codex 6b: the runs under a relative rule read '0.8045%' for a loss in nats; the call's figure said '0.5 points'."""
    rb("criterion", "add", "INT8 adds at most 0.5%", "-e", "e1", "--metric", "change.val_loss", "--at-most", "0.5", "--relative", "--id", "cr")
    seeds(rb, [x for x, _ in CELLS[32]], fp32=[y for _, y in CELLS[32]])
    rb("page", "--out", "page.html")
    raw = Path("page.html").read_text(encoding="utf-8")
    text = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", raw)))
    assert not re.search(r"0\.8\d* ?%", text), re.findall(r".{30}0\.8\d* ?%.{10}", text)
    assert "0.5 points" not in text and "0.5% or less" in text, re.findall(r"the rule: [^.]{0,30}", text)


def test_a_relative_change_s_runs_in_points_stay_in_points(rb):
    """Codex 6b pass 2: the relative card swapped every ' points' for '%', so runs of a score in points read '82.00%'."""
    rb("metric", "add", "score", "--maximize", "--unit", "points")
    rb("criterion", "add", "INT8 gains at least 1%", "-e", "e1", "--metric", "change.score", "--at-least", "1", "--relative", "--id", "cr")
    for s, (x, y) in enumerate([(82, 80), (85, 81), (80, 79)], 1):
        rb("run", "add", "e1", f"int8.score={x}", f"fp32.score={y}", "--set", f"seed={s}", "--produced-at", "2026-05-01")
    text = page(rb)
    assert not re.search(r"\b(82|85|80|81|79)\.00 ?%", text), re.findall(r".{20}\b8\d\.00 ?%.{10}", text)
    assert re.search(r"\b82\.00 points\b", text), re.findall(r".{20}\b82\.00.{12}", text)
    assert re.search(r"INT8 is 2\.\d+% higher than the FP32 baseline", text), re.findall(r"INT8 is [^()]*", text)


def test_a_relative_rule_the_other_way_round_leaves_the_grid_cell_its_own(rb):
    """Q2's review: a relative rule of fp32 against int8 reads INT8's cell otherwise (rb compare's read_by), and the
    other way round. The cell keeps its own change of int8 against the baseline: never "2.982% lower than FP32" beside
    means that say INT8 is higher."""
    rb("spec", "vary", "e1", "precision")
    rb("spec", "set", "e1", "fp32.precision", "fp32", "--no-verify")
    rb("spec", "set", "e1", "int8.precision", "int8", "--no-verify")
    rb("criterion", "add", "FP32 is at most 3.5% below INT8", "-e", "e1", "--metric", "val_loss", "--subject", "fp32", "--reference", "int8",
       "--at-most", "3.5", "--relative", "--id", "cm")
    for i, f in enumerate([0.9612, 0.9655, 0.9701], 1):
        rb("run", "add", "e1", f"fp32.val_loss={f:g}", "--set", f"seed={i}", "--produced-at", "2026-05-01")
    for i, f in enumerate([0.9931, 0.9978, 1.0003, 0.9899], 1):
        rb("run", "add", "e1", f"int8.val_loss={f:g}", "--set", f"seed={i}", "--produced-at", "2026-05-01")
    rb("page", "--out", "page.html")
    raw = Path("page.html").read_text(encoding="utf-8")
    flat = lambda h: re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", h)))  # noqa: E731
    grids = [flat(g) for g in re.findall(r'<table class="grid">.*?</table>', raw, re.S)]
    assert any("higher than FP32" in g for g in grids) and not any("lower than FP32" in g or "%" in g for g in grids), grids


# ---------------------------------------------------------------- one reading, one set of digits (round 8)

def raw_page(rb) -> str:
    rb("page", "--out", "page.html")
    return Path("page.html").read_text(encoding="utf-8")


def test_a_means_rule_s_row_says_its_margin_in_the_rule_s_digits(rb):
    """MP-1107-1620: "rule as a difference of means = 0.0317 ± 0.00005 nats · margin 0.00 nats". A reading on the band's
    edge has a margin of 0 at the rule's own resolution: "0.00000", never "0.00", which hides what it was rounded from."""
    rb("spec", "vary", "e1", "precision")
    Path("paper.md").write_text("INT8 adds 0.0317 to the validation loss.\n", encoding="utf-8")
    rb("criterion", "add", "INT8 adds what the paper says", "-e", "e1", "--metric", "change.val_loss", "--equals", "0.0317", "--tolerance", "0.00005",
       "--means", "--source", "paper.md", "--quote", "adds 0.0317 to the validation loss", "--id", "cm")
    for i, (f, x) in enumerate([(0.962, 0.993), (0.963, 0.994), (0.964, 0.995), (0.968, 1.002)], 1):
        rb("run", "add", "e1", f"fp32.val_loss={f}", f"int8.val_loss={x}", "--set", f"seed={i}", "--produced-at", "2026-05-01")
    text = page(rb)
    assert "rule as a difference of means = 0.0317 ± 0.00005 nats · margin 0.00000 nats" in text, re.findall(r"rule as a difference[^·]*· margin [^ ]+ nats", text)


def test_a_count_across_design_cells_says_its_estimate_as_rb_show_does(rb):
    """MP-1107-1228: across design cells a count's card said "What rb measured 0.992 nats" where rb show says 0.9915: the
    cells' mean is no cell of the grid, so the grid's 3 decimals never round it. Its legend says it alike, and so do a
    mean rule's card and legend across the same cells (+0.0245, never +0.025)."""
    rb("spec", "set", "e1", "batch", "--per-run", "--design-factor")
    rb("criterion", "add", "INT8 adds at most 0.05", "-e", "e1", "--metric", "change.val_loss", "--at-most", "0.05", "--id", "c1")
    rb("criterion", "add", "2 of 3 seeds keep INT8 below 1.0", "-e", "e1", "--metric", "int8.val_loss", "--at-most", "1.0", "--count", "2", "--of", "3", "--id", "cc")
    batches(rb, {32: [(0.987, 0.961), (0.989, 0.962), (0.991, 0.963)], 64: [(0.992, 0.971), (0.994, 0.972), (0.996, 0.973)]})
    raw = raw_page(rb)
    text = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", raw)))
    legends = [html.unescape(m) for m in re.findall(r'<p class="figleg figv">(.*?)</p>', raw)]
    for cid, est in (("cc", "0.9915"), ("c1", "+0.0245")):
        assert finding(rb, cid)["says"]["comparison"].split(": ")[1].startswith(est.lstrip("+") if cid == "cc" else est)
        assert re.search(rf"What rb measured {re.escape(est)} nats", text), (cid, re.findall(r"What rb measured [^ ]+ nats", text))
        assert any(x.endswith(f"estimate: {est}") for x in legends), (cid, legends)


def test_a_relative_rule_s_cells_list_says_each_cell_as_rb_show_says_its_reading(rb):
    """MP-1107-0840 later 3: the cells' list said "batch 32 +0.56 [+0.545033, +0.579967]" beside the card's "0.5625%
    (+0.545 to +0.58)" of a rule on that cell: one reading in three sets of digits. Each cell of a relative change is said
    as rb show says a reading with its numbers, and a count's cell as its legend says it."""
    rb("spec", "set", "e1", "batch", "--per-run", "--design-factor")
    rb("criterion", "add", "at most 0.5% at batch 32", "-e", "e1", "--metric", "change.val_loss", "--at-most", "0.5", "--relative", "--where", "batch=32", "--id", "c32")
    rb("criterion", "add", "at most 0.5% across batches", "-e", "e1", "--metric", "change.val_loss", "--at-most", "0.5", "--relative", "--id", "c2")
    rb("criterion", "add", "4 of 6 at most 0.81", "-e", "e1", "--metric", "int8.val_loss", "--at-most", "0.81", "--count", "4", "--of", "6", "--id", "c4")
    batches(rb, CELLS)
    est, lo, hi = re.search(r": ([^ ]+) \[([^,\]]+), ([^\]]+)\]", finding(rb, "c32")["says"]["comparison"]).groups()
    lists = [re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", m))) for m in re.findall(r'<ul class="cells">(.*?)</ul>', raw_page(rb), re.S)]
    assert any(f"batch 32 {est} [{lo}, {hi}]" in x for x in lists), (est, lo, hi, lists)
    assert any("batch 64 +0.1 [" in x for x in lists), lists
    assert any("batch 32 0.8045 3 units" in x for x in lists), lists                       # the count's, as its legend (MP-1108-0609)
    assert not any("0.545033" in x or "+0.56 " in x for x in lists), lists


def test_a_grid_cell_no_rule_owns_says_its_change_as_its_legend_does(rb):
    """MP-1107-1228 (own1228): the batch 32 cell, which a relative rule reads otherwise and a mean rule reads only as one
    of its cells, said "0.03 nats higher than FP32" beside its legend's "batch 32: +0.027": the relative rule's edge, in
    %, set the cell's digits. No rule's reading is that cell's, so it keeps its own digits (P5), linked to the rule (P12)."""
    rb("spec", "set", "e1", "batch", "--per-run", "--design-factor")
    rb("criterion", "add", "INT8 adds at most 0.05", "-e", "e1", "--metric", "change.val_loss", "--at-most", "0.05", "--id", "c1")
    rb("criterion", "add", "INT8 adds at most 3% at batch 32", "-e", "e1", "--metric", "change.val_loss", "--at-most", "3", "--relative", "--where", "batch=32", "--id", "cr")
    batches(rb, {32: [(0.987, 0.961), (0.989, 0.962), (0.991, 0.963)], 64: [(0.992, 0.971), (0.994, 0.972), (0.996, 0.973)]})
    raw = raw_page(rb)
    grid = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", re.search(r'<table class="grid">.*?</table>', raw, re.S).group(0))))
    legend = next(html.unescape(m) for m in re.findall(r'<p class="figleg figv">(.*?)</p>', raw) if "batch 32" in m)
    assert "batch 32: +0.027" in legend, legend
    assert "0.027 nats higher than FP32" in grid and "0.03 nats higher" not in grid, grid
    assert "%" not in grid, grid


def test_the_cells_list_says_a_count_s_cells_as_its_legend_and_units_in_the_plural(rb):
    """MP-1108-0609 laters: across design cells, the page's cells list gave a count rule per-cell bounds
    ("[0.779659, 0.829341]") that no legend and no rb show line gives, since a count reads each cell's units and no
    interval of their mean; and it said "3 unit"."""
    rb("spec", "set", "e1", "batch", "--per-run", "--design-factor")
    rb("criterion", "add", "INT8 adds at most 0.5%", "-e", "e1", "--metric", "change.val_loss", "--at-most", "0.5", "--relative", "--id", "cr")
    rb("criterion", "add", "4 of 6 at most 0.81", "-e", "e1", "--metric", "int8.val_loss", "--at-most", "0.81", "--count", "4", "--of", "6", "--id", "c4")
    batches(rb, CELLS)
    rb("page", "--out", "page.html")
    text = Path("page.html").read_text(encoding="utf-8")
    lists = dict(re.findall(r'<section class="part" id="c-(\w+):runs">.*?<ul class="cells">(.*?)</ul>', text))
    assert '<span class="v"><a href="#run-' in lists["c4"] and "0.8045</a>" in lists["c4"], lists["c4"]
    assert 'class="iv"' not in lists["c4"] and "0.779659" not in text, lists["c4"]
    assert "[+0.545, +0.58]" in lists["cr"], lists["cr"]
    assert lists["c4"].count("3 units</span>") == 2 and lists["cr"].count("3 units</span>") == 2 and "3 unit<" not in text, lists
