"""A paired reading says what it pairs by, with the same runs read unpaired beside it; a per-run setting can be declared
a repeat index (population.repeat), which links no runs and is no condition a reading pools; and a value is said to miss
the rule alone only where it holds a reading of its own. Every name and number is synthetic: an INT8 ResNet-50 on
ImageNet, recorded by human:ana."""
import subprocess

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
    run("metric", "add", "top1", "--maximize", "--unit", "percent")
    return run


def two_arms(rb):
    rb("experiment", "add", "INT8 against FP32", "--id", "int8", "--purpose", "the sweep", "--baseline", "fp32", "--candidate", "int8")
    rb("spec", "set", "int8", "seed", "--per-run")
    rb("criterion", "add", "INT8 loses at most 1 point", "-e", "int8", "--metric", "change.top1", "--at-least", "-1", "--id", "c1")
    for seed, (a, b) in enumerate([(76.1, 75.6), (75.2, 75.1), (76.4, 75.2)], start=1):
        rb("run", "add", "int8", f"fp32.top1={a}", "--set", f"seed={seed}", "--produced-at", f"2026-05-0{seed}")
        rb("run", "add", "int8", f"int8.top1={b}", "--set", f"seed={seed}", "--produced-at", f"2026-05-0{seed}")


def test_a_paired_reading_says_what_it_pairs_by_beside_its_unpaired_reading(rb):
    two_arms(rb)
    code, out = rb("show", "c1", text=True)
    assert "paired difference, t interval, 3 seeds" in out
    assert "paired by seed: each seed's int8 run against the same seed's fp32 run" in out
    assert "unpaired, the same runs read" in out and "unpaired difference, Welch interval, 6 runs" in out
    assert "rb spec set int8 population.repeat seed --no-verify reads each run as its own unit" in out
    pairing = rb("show", "c1").data["finding"]["comparison"]["pairing"]
    assert pairing["by"] == ["seed"] and pairing["declared_unit"] is False
    assert pairing["unpaired"]["method"] == "unpaired difference, Welch interval" and pairing["command"] == "rb spec set int8 population.repeat seed --no-verify"
    # the researcher says the seeds link nothing: each run is its own unit, and no run reads as under an earlier spec
    rb("spec", "set", "int8", "population.repeat", "seed", "--no-verify")
    f = rb("show", "c1").data["finding"]
    assert f["comparison"]["method"] == "unpaired difference, Welch interval" and f["comparison"]["n"] == 6
    assert "pairing" not in f["comparison"] or not f["comparison"]["pairing"]
    assert all(o["counted"] for o in f["observations"])


def test_arms_in_one_run_say_nothing_more(rb):
    rb("experiment", "add", "INT8 against FP32", "--id", "int8", "--purpose", "the sweep", "--baseline", "fp32", "--candidate", "int8")
    rb("criterion", "add", "INT8 loses at most 1 point", "-e", "int8", "--metric", "change.top1", "--at-least", "-1", "--id", "c1")
    for a, b in [(76.1, 75.6), (75.2, 75.1), (76.4, 75.2)]:
        rb("run", "add", "int8", f"fp32.top1={a}", f"int8.top1={b}", "--produced-at", "2026-05-01")
    code, out = rb("show", "c1", text=True)
    assert "paired difference" in out and "paired by" not in out


def photos(rb, rule):
    rb("experiment", "add", "INT8 on photos", "--id", "ph", "--purpose", "noise cases", "--baseline", "int8")
    for s in ("photo", "camera", "seed"):
        rb("spec", "set", "ph", s, "--per-run")
    rb("spec", "set", "ph", "population.unit", "photo", "--no-verify")
    rb("criterion", "add", "INT8 holds 75 on average", "-e", "ph", "--metric", "top1", "--subject", "int8", *rule, "--id", "c2")
    n = 0
    for photo, camera in (("p1", "canon"), ("p2", "nikon"), ("p3", "canon")):
        for seed, x in ((1, 74.0), (2, 76.0)):
            n += 1
            rb("run", "add", "ph", f"int8.top1={x + 0.1 * n}", "--set", f"photo={photo}", "--set", f"camera={camera}", "--set", f"seed={seed}",
               "--produced-at", "2026-05-01")


def test_no_pooling_over_what_the_unit_is_made_of_nor_a_declared_repeat(rb):
    photos(rb, ["--at-least", "70"])
    code, out = rb("show", "c2", text=True)
    assert "hold camera" not in out and "pooled over 2 camera" not in out
    assert "hold seed 1 and 2, pooled in one reading" in out and "rb spec set ph population.repeat seed --no-verify says so" in out
    rb("spec", "set", "ph", "population.repeat", "seed", "--no-verify")
    code, out = rb("show", "c2", text=True)
    assert "pooled" not in out


def test_a_value_misses_alone_only_where_it_holds_a_reading_of_its_own(rb):
    rb("experiment", "add", "INT8 tokens", "--id", "tk", "--purpose", "a mean over repeats", "--baseline", "int8")
    for s in ("run", "epoch"):
        rb("spec", "set", "tk", s, "--per-run")
    rb("spec", "set", "tk", "population.unit", "run", "--no-verify")
    rb("criterion", "add", "INT8 averages 75", "-e", "tk", "--metric", "top1", "--subject", "int8", "--equals", "75", "--tolerance", "0.5", "--id", "c3")
    for i, x in enumerate((74.0, 75.0, 76.0)):
        rb("run", "add", "tk", f"int8.top1={x}", "--set", f"run=r{i}", "--set", f"epoch={i}", "--produced-at", "2026-05-01")
    f = rb("show", "c3").data["finding"]
    assert f["rule_outcome"] == "meets"
    assert "not reproduced at" not in f["says"]["headline"] and "not met at" not in f["says"]["headline"]
    assert not [c for c in f["caveats"] if c["code"] == "pooled_misses"]


def test_a_repeat_index_is_a_case_within_what_still_pairs_the_runs(rb):
    """Declared a repeat index, an epoch leaves what pairs two arms' runs: they pair by their other per-run values (the
    camera), the epochs averaged within each."""
    rb("experiment", "add", "INT8 against FP32 per camera", "--id", "cam", "--purpose", "the sweep", "--baseline", "fp32", "--candidate", "int8")
    for s in ("camera", "epoch"):
        rb("spec", "set", "cam", s, "--per-run")
    rb("criterion", "add", "INT8 loses at most 2 points", "-e", "cam", "--metric", "change.top1", "--at-least", "-2", "--id", "c4")
    for camera in ("canon", "nikon", "sony"):
        for epoch in (0, 1):
            for v, x in (("fp32", 76.0), ("int8", 75.0 + epoch / 10)):
                rb("run", "add", "cam", f"{v}.top1={x}", "--set", f"camera={camera}", "--set", f"epoch={epoch}", "--produced-at", "2026-05-01")
    code, out = rb("show", "c4", text=True)
    # of what pairs them, only the epoch could be a counter: the camera, a name, is a condition and never offered
    assert "paired by camera and epoch" in out and "where epoch only numbers repeats, rb spec set cam population.repeat epoch --no-verify" in out, out
    assert "population.repeat camera" not in out and "<that setting>" not in out
    rb("spec", "set", "cam", "population.repeat", "epoch", "--no-verify")
    f = rb("show", "c4").data["finding"]
    assert f["comparison"]["method"] == "paired difference, t interval" and f["comparison"]["n"] == 3
    assert f["comparison"]["pairing"]["by"] == ["camera"]


def test_the_unpaired_reading_counts_each_arms_units_and_a_vowel_takes_an(rb):
    rb("experiment", "add", "INT8 against FP32 per angle", "--id", "ang", "--purpose", "the sweep", "--baseline", "fp32", "--candidate", "int8")
    for s in ("angle", "epoch"):
        rb("spec", "set", "ang", s, "--per-run")
    rb("criterion", "add", "INT8 loses at most 2 points", "-e", "ang", "--metric", "change.top1", "--at-least", "-2", "--id", "c5")
    for angle in ("left", "right", "top"):
        for epoch in (0, 1):
            for v, x in (("fp32", 76.0), ("int8", 75.0 + epoch / 10)):
                rb("run", "add", "ang", f"{v}.top1={x}", "--set", f"angle={angle}", "--set", f"epoch={epoch}", "--produced-at", "2026-05-01")
    code, out = rb("show", "c5", text=True)
    assert "if runs sharing an angle are not independent" in out or "if runs sharing an epoch are not independent" in out, out
    rb("spec", "set", "ang", "population.repeat", "epoch", "--no-verify")
    code, out = rb("show", "c5", text=True)
    # paired by angle now; its unpaired reading says the same, estimate and interval, so it is left out (round 4)
    assert "paired by angle: each angle's int8 run against the same angle's fp32 run" in out and "unpaired, " not in out, out


def test_the_offered_repeat_keeps_the_repeats_already_declared(rb):
    rb("experiment", "add", "INT8 against FP32 per seed", "--id", "cam", "--purpose", "the sweep", "--baseline", "fp32", "--candidate", "int8")
    for s in ("seed", "epoch"):
        rb("spec", "set", "cam", s, "--per-run")
    rb("spec", "set", "cam", "population.repeat", "epoch", "--no-verify")
    rb("criterion", "add", "INT8 loses at most 2 points", "-e", "cam", "--metric", "change.top1", "--at-least", "-2", "--id", "c6")
    for seed in (1, 2, 3):
        for epoch in (0, 1):
            for v, x in (("fp32", 76.0), ("int8", 75.0 + epoch / 10)):
                rb("run", "add", "cam", f"{v}.top1={x}", "--set", f"seed={seed}", "--set", f"epoch={epoch}", "--produced-at", "2026-05-01")
    command = rb("show", "c6").data["finding"]["comparison"]["pairing"]["command"]
    assert command == "rb spec set cam population.repeat epoch,seed --no-verify"
    rb(*command.split()[1:])
    f = rb("show", "c6").data["finding"]
    assert f["comparison"]["method"].startswith("unpaired difference")


def test_the_unpaired_reading_reads_the_runs_the_paired_one_leaves_out_too(rb):
    """A run whose unit the other arm never ran is left out of the paired reading; the unpaired one beside it reads every
    run of the arms (round 4: one unpaired figure, never a second reading of the paired runs alone)."""
    rb("experiment", "add", "INT8 against FP32", "--id", "int8", "--purpose", "the sweep", "--baseline", "fp32", "--candidate", "int8")
    rb("spec", "set", "int8", "seed", "--per-run")
    rb("criterion", "add", "INT8 loses at most 1 point", "-e", "int8", "--metric", "change.top1", "--at-least", "-1", "--id", "c1")
    for seed, x in ((1, 76.0), (2, 76.0), (3, 90.0)):
        rb("run", "add", "int8", f"fp32.top1={x}", "--set", f"seed={seed}", "--produced-at", "2026-05-01")
    for seed in (1, 2):
        rb("run", "add", "int8", "int8.top1=76.0", "--set", f"seed={seed}", "--produced-at", "2026-05-01")
    unpaired = rb("show", "c1").data["finding"]["comparison"]["pairing"]["unpaired"]
    assert unpaired["n"] == 5 and abs(unpaired["estimate"] - (76.0 - (76.0 + 76.0 + 90.0) / 3)) < 1e-9, unpaired


def test_the_unpaired_reading_reads_runs_that_give_both_arms_as_each_arms_number(rb):
    """Some runs give one arm, others both: the unpaired figure reads every run of the arms, a run of both read as each
    arm's number, never only the runs that give the difference (round 5)."""
    two_arms(rb)
    for seed, (a, b) in ((8, (76.8, 75.0)), (9, (75.0, 75.9))):
        rb("run", "add", "int8", f"fp32.top1={a}", f"int8.top1={b}", "--set", f"seed={seed}", "--produced-at", "2026-05-08")
    f = rb("show", "c1").data["finding"]
    assert f["comparison"]["n"] == 5, f["comparison"]
    unpaired = f["comparison"]["pairing"]["unpaired"]
    fp32 = [76.1, 75.2, 76.4, 76.8, 75.0]
    int8 = [75.6, 75.1, 75.2, 75.0, 75.9]
    assert unpaired["n"] == 10 and abs(unpaired["estimate"] - (sum(int8) / 5 - sum(fp32) / 5)) < 1e-9, unpaired
    code, out = rb("show", "c1", text=True)
    assert "unpaired, the same runs read" in out and "unpaired difference, Welch interval, 10 " in out, out


def test_no_unpaired_figure_where_a_run_gives_only_the_difference(rb):
    two_arms(rb)
    rb("run", "add", "int8", "change.top1=-0.4", "--set", "seed=8", "--produced-at", "2026-05-08")
    comparison = rb("show", "c1").data["finding"]["comparison"]
    assert comparison["pairing"]["unpaired"] is None, comparison["pairing"]
    code, out = rb("show", "c1", text=True)
    assert "paired by seed" in out and "unpaired" not in out, out


def test_a_setting_a_rule_selects_on_is_never_offered_as_a_repeat(rb):
    two_arms(rb)
    rb("criterion", "add", "INT8 holds at seed 1", "-e", "int8", "--metric", "change.top1", "--at-least", "-1", "--where", "seed=1", "--id", "c2")
    comparison = rb("show", "c2").data["finding"]["comparison"]
    assert comparison["pairing"]["command"] is None, comparison["pairing"]
    code, out = rb("show", "c2", text=True)
    assert "paired by seed" in out and "population.repeat" not in out, out
    # the rule that selects nothing still gets the offer
    assert rb("show", "c1").data["finding"]["comparison"]["pairing"]["command"] == "rb spec set int8 population.repeat seed --no-verify"


def test_rb_compare_gives_no_row_of_its_own_to_a_declared_repeat(rb):
    rb("experiment", "add", "INT8 against FP32", "--id", "int8", "--purpose", "the sweep", "--baseline", "fp32", "--candidate", "int8")
    rb("spec", "set", "int8", "seed", "--per-run")
    rb("spec", "set", "int8", "epoch", "--per-run")
    rb("spec", "set", "int8", "population.unit", "seed", "--no-verify")
    rb("criterion", "add", "INT8 loses at most 1 point", "-e", "int8", "--metric", "change.top1", "--at-least", "-1", "--id", "c1")
    for seed in (1, 2, 3):
        for epoch, (a, b) in enumerate([(76.0, 75.5), (76.2, 75.1)]):
            rb("run", "add", "int8", f"fp32.top1={a}", f"int8.top1={b}", "--set", f"seed={seed}", "--set", f"epoch={epoch}", "--produced-at", "2026-05-01")
    assert rb("compare", "int8").data.get("conditions") == ["epoch"]
    rb("spec", "set", "int8", "population.repeat", "epoch", "--no-verify")
    data = rb("compare", "int8").data
    assert not data.get("conditions"), data.get("conditions")
    code, out = rb("compare", "int8", text=True)
    assert "epoch" not in out and "(n=3)" in out, out


def test_an_unpaired_figure_over_runs_of_both_arms_counts_units_not_runs(rb):
    two_arms(rb)
    for seed, (a, b) in ((8, (76.8, 75.0)), (9, (75.0, 75.9))):
        rb("run", "add", "int8", f"fp32.top1={a}", f"int8.top1={b}", "--set", f"seed={seed}", "--produced-at", "2026-05-08")
    code, out = rb("show", "c1", text=True)
    assert "Welch interval, 10 units" in out and "10 runs" not in out, out
