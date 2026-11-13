"""Readings that must say what happened, not something a researcher would misread.

Numbers typed in with no time are of unknown time, never "declared before the evidence"; an unpaired reading counts
both arms' units produced before a rule; a file's first commit is not when it was written; no hint offers to type a
number in; a call that accepts a missed rule says so; a run that does not count shares a unit with none; a replaced run
is not a retracted one on the page; a cited value on a per-run setting is compared with the runs. Every name and number
is synthetic: an INT8 ResNet-50 on ImageNet, recorded by human:ana."""
import json
import re
import subprocess
from pathlib import Path

import pytest

from rabbit_brain.models import Envelope
from rbtest import as_the_user, main

GIT = ["git", "-c", "user.name=ana", "-c", "user.email=ana@example.org", "-c", "commit.gpgsign=false"]


@pytest.fixture
def rb(tmp_path, monkeypatch, tmp_path_factory, capsys):
    as_the_user(monkeypatch)
    cfg = tmp_path_factory.mktemp("gitconfig") / "config"
    cfg.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(cfg))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("RB_ACTOR", "human:ana")
    root = tmp_path / "int8"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    monkeypatch.chdir(root)

    def run(*argv, text=False, ok=True):
        if text:
            code = main(list(argv), typed=True)
            return code, capsys.readouterr().out
        code = main([*argv, "--json"], typed=True)
        env = Envelope.model_validate_json(capsys.readouterr().out)
        if ok:
            assert code in (0, 1) and env.ok, (argv, code, env.errors)
        return env

    run.root = root
    run("init", "INT8 ResNet-50", "--no-agents")
    run("metric", "add", "top1", "--maximize", "--unit", "percent")
    return run


def commit(root: Path, when: str) -> None:
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    env = {"GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when}
    import os
    subprocess.run([*GIT, "-C", str(root), "commit", "-q", "-m", "evals"], check=True, env={**os.environ, **env})


def test_numbers_typed_with_no_time_have_an_unknown_time(rb):
    rb("experiment", "add", "INT8 against FP32", "--id", "int8", "--purpose", "the paper's table", "--baseline", "fp32", "--candidate", "int8")
    crit = rb("criterion", "add", "INT8 keeps top-1 within a point", "-e", "int8", "--metric", "change.top1", "--at-least", "-1").data["object"]["id"]
    code, out = rb("run", "add", "int8", "fp32.top1=76.1", "int8.top1=75.6", text=True)
    assert "when is unknown: the numbers were typed in" in out
    run = rb("status", "--full").data["runs"][0]["id"]
    shown = rb("show", "--long", run)
    assert shown.data["object"]["produced_basis"] == "unknown" and shown.data["made_before_recorded"] is True
    f = rb("show", "--long", crit).data["finding"]
    assert f["timing"] == "unknown"
    assert "declared before" not in f["says"]["timing"]
    code, out = rb("status", "--full", text=True)
    assert "recorded afterwards" in out


def test_a_typed_run_with_a_time_given_reads_as_before(rb):
    rb("experiment", "add", "INT8 against FP32", "--id", "int8", "--purpose", "the table", "--baseline", "fp32", "--candidate", "int8")
    crit = rb("criterion", "add", "INT8 keeps top-1 within a point", "-e", "int8", "--metric", "change.top1", "--at-least", "-1").data["object"]["id"]
    rb("run", "add", "int8", "fp32.top1=76.1", "int8.top1=75.6", "--produced-at", "2030-01-01T00:00:00Z")
    assert rb("show", "--long", crit).data["finding"]["timing"] == "before"


def test_an_unpaired_reading_counts_both_arms_units_produced_before_it(rb):
    rb("experiment", "add", "INT8 against FP32", "--id", "int8", "--purpose", "the table", "--baseline", "fp32", "--candidate", "int8")
    rb("spec", "set", "int8", "seed", "--per-run")
    rb("run", "add", "int8", "top1=76.1", "--variant", "fp32", "--set", "seed=1", "--produced-at", "2026-01-01T00:00:00Z")
    rb("run", "add", "int8", "top1=75.6", "--variant", "int8", "--set", "seed=2", "--produced-at", "2026-01-02T00:00:00Z")
    crit = rb("criterion", "add", "INT8 keeps top-1 within a point", "-e", "int8", "--metric", "top1", "--subject", "int8", "--reference", "fp32",
              "--at-least", "-1").data["object"]["id"]
    f = rb("show", "--long", crit).data["finding"]
    assert f["comparison"]["method"].startswith("unpaired")
    assert "both units were produced before it was declared" in f["says"]["timing"], f["says"]["timing"]


def test_a_metrics_file_committed_after_its_run_is_still_its_own_output(rb):
    out = Path(rb.root, "results", "r1")
    out.mkdir(parents=True)
    (out / "metrics.json").write_text(json.dumps({"fp32": {"top1": 76.1}, "int8": {"top1": 75.6}}), encoding="utf-8")
    commit(rb.root, "2026-05-01T15:00:00Z")
    rb("experiment", "add", "INT8 against FP32", "--id", "int8", "--purpose", "the runs", "--baseline", "fp32", "--candidate", "int8")
    code, out = rb("run", "add", "int8", "--from", "results/r1/metrics.json", "--produced-at", "2026-05-01T10:00:00Z", text=True)
    assert "not the run's own output" not in out
    run = rb("status", "--full").data["runs"][0]["id"]
    assert rb("show", "--long", run).data["file_written_after"] is None
    status = rb("status", "--full")
    assert not any(i["code"] == "file_written_after" for i in status.data["open"])


def test_no_hint_offers_to_type_a_number_in(rb):
    rb("experiment", "add", "INT8 against FP32", "--id", "int8", "--purpose", "the table", "--baseline", "fp32", "--candidate", "int8")
    env = rb("criterion", "add", "The paper's 75.6 top-1", "-e", "int8", "--metric", "int8.top1", "--equals", "75.6", "--tolerance", "0.05")
    assert not any("=<value>" in n and "top1" in n for n in env.next)
    assert not any("--command" in n for n in env.next)
    status = rb("status", "--full")
    for item in status.data["open"]:
        assert "top1=<value>" not in item.get("do", ""), item
        assert '--command "..."' not in item.get("do", ""), item


def test_a_call_that_accepts_a_missed_rule_says_what_it_accepted(rb):
    rb("experiment", "add", "INT8 against FP32", "--id", "int8", "--purpose", "the table", "--baseline", "fp32", "--candidate", "int8")
    crit = rb("criterion", "add", "INT8 keeps top-1 within a tenth", "-e", "int8", "--metric", "change.top1", "--at-least", "-0.1").data["object"]["id"]
    rb("run", "add", "int8", "fp32.top1=76.1", "int8.top1=75.6", "--produced-at", "2030-01-01T00:00:00Z")
    rb("decide", crit, "accept", "--why", "a half point is within what ships")
    rb("page", "--out", "page.html")
    page = " ".join(re.sub(r"<[^>]+>", " ", Path(rb.root, "page.html").read_text(encoding="utf-8")).split())
    assert f"accepted {crit} against its rule (criterion not met)" in page


def test_a_run_that_does_not_count_shares_a_unit_with_none(rb):
    rb("experiment", "add", "INT8 against FP32", "--id", "int8", "--purpose", "the runs", "--baseline", "fp32", "--candidate", "int8")
    rb("spec", "set", "int8", "seed", "--per-run")
    rb("spec", "set", "int8", "eval.crop", "center", "--no-verify")
    Path(rb.root, "resize.yaml").write_text("eval:\n  crop: resize\n", encoding="utf-8")
    rb("run", "add", "int8", "fp32.top1=76.1", "int8.top1=75.6", "--set", "seed=0", "--config", "resize.yaml")
    code, out = rb("run", "add", "int8", "fp32.top1=76.0", "int8.top1=75.5", "--set", "seed=0", text=True)
    assert "averaged" not in out


def test_a_replaced_run_is_not_a_retracted_one_on_the_page(rb):
    rb("experiment", "add", "INT8 against FP32", "--id", "int8", "--purpose", "the table", "--baseline", "fp32", "--candidate", "int8")
    first = rb("run", "add", "int8", "fp32.top1=76.1", "int8.top1=75.6").data["object"]["id"]
    rb("run", "add", "int8", "fp32.top1=76.1", "int8.top1=75.6", "--produced-at", "2026-03-27T00:00:00Z", "--replaces", first, "--why", "the date the table gives")
    rb("page", "--out", "page.html")
    page = Path(rb.root, "page.html").read_text(encoding="utf-8")
    assert "run was retracted" not in page and "runs were retracted" not in page


def test_a_cited_value_on_a_per_run_setting_is_compared_with_the_runs(rb):
    Path(rb.root, "paper.md").write_text("The paper evaluates on 50000 images and reports 75.6 top-1.\n", encoding="utf-8")
    rb("experiment", "add", "INT8 reproduced", "--id", "int8", "--purpose", "the paper's number", "--baseline", "fp32", "--candidate", "int8")
    rb("spec", "set", "int8", "images", "--per-run", "--cited", "50000", "--cited-source", "paper.md:1")
    crit = rb("criterion", "add", "The paper's 75.6 top-1", "-e", "int8", "--metric", "int8.top1", "--equals", "75.6", "--tolerance", "0.05",
              "--source", "paper.md", "--quote", "reports 75.6 top-1", "--locator", "line 1").data["object"]["id"]
    rb("run", "add", "int8", "int8.top1=75.1", "fp32.top1=76.0", "--set", "images=10000", "--produced-at", "2030-01-01T00:00:00Z")
    f = rb("show", "--long", crit).data["finding"]
    codes = [p["code"] for p in f["integrity"]]
    assert "not_comparable" in codes, f["integrity"]
    assert any("images 10000 in the runs it reads vs 50000 in the cited source" in p["text"] for p in f["integrity"])



def test_typed_numbers_of_unknown_time_point_at_recording_them_again_with_the_time(rb):
    rb("experiment", "add", "INT8 against FP32", "--id", "int8", "--purpose", "the table", "--baseline", "fp32", "--candidate", "int8")
    rb("criterion", "add", "INT8 keeps top-1 within a point", "-e", "int8", "--metric", "change.top1", "--at-least", "-1", "--id", "c1")
    run = rb("run", "add", "int8", "fp32.top1=76.1", "int8.top1=75.6").data["object"]["id"]
    [item] = [i for i in rb("status", "--full").data["open"] if i["code"] == "timing_unknown"]
    assert f"--produced-at <when> --replaces {run}" in item["do"]
    again = rb("run", "add", "int8", "fp32.top1=76.1", "int8.top1=75.6", "--produced-at", "2030-01-01T00:00:00Z", "--replaces", run, "--why", "the time it ran")
    assert again.data["object"]["id"] != run
    assert rb("show", "--long", "c1").data["finding"]["timing"] == "before"


def test_a_hand_retraction_that_reads_like_a_replacement_still_counts_as_retracted(rb):
    rb("experiment", "add", "INT8 against FP32", "--id", "int8", "--purpose", "the table", "--baseline", "fp32", "--candidate", "int8")
    run = rb("run", "add", "int8", "fp32.top1=76.1", "int8.top1=75.6", "--produced-at", "2030-01-01T00:00:00Z").data["object"]["id"]
    rb("retract", run, "--why", "replaced by nothing: the GPU was faulty")
    rb("page", "--out", "page.html")
    assert "1 run was retracted" in " ".join(re.sub(r"<[^>]+>", " ", Path(rb.root, "page.html").read_text(encoding="utf-8")).split())


BEFORE = Path(__file__).resolve().parent / "fixtures" / "shared-files-record"   # typed runs an earlier rb kept as made when recorded


def test_typed_runs_an_earlier_rb_wrote_read_as_of_unknown_time(tmp_path, monkeypatch, tmp_path_factory, capsys):
    """An earlier rb kept numbers typed with no time as made when they were recorded (produced_basis: recorded). This rb
    reads them as new typed runs are read, of a time nobody knows, and rewrites nothing in .rb/ to do it."""
    import shutil
    as_the_user(monkeypatch)
    cfg = tmp_path_factory.mktemp("gitconfig") / "config"
    cfg.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(cfg))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("RB_ACTOR", "human:ana")
    root = tmp_path / "before"
    shutil.copytree(BEFORE, root)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    monkeypatch.chdir(root)
    stored = {p: p.read_bytes() for p in (root / ".rb").rglob("*") if p.is_file() and "cache" not in p.parts}
    assert all(b"produced_basis: recorded" in stored[p] for p in (root / ".rb/experiments/e1/runs").glob("*.yaml"))

    code = main(["status", "--json"], typed=True)
    data = json.loads(capsys.readouterr().out)["data"]
    assert code == 0
    runs = data["runs"]
    assert runs and all(r["produced_basis"] == "unknown" and r["produced_at"] is None for r in runs), runs
    timings = {f["criterion"]: f["timing"] for f in data["findings"]}
    assert timings and set(timings.values()) == {"unknown"}, timings
    assert data["witness"]["runs"]["afterwards"] == len(runs)

    code = main(["show", "--long", "c1"], typed=True)
    out = capsys.readouterr().out
    assert "order unknown" in out and "declared before the evidence" not in out
    code = main(["show", "--long", runs[0]["id"]], typed=True)
    assert "produced: when is unknown" in capsys.readouterr().out

    # the reading changed, not the files
    assert {p: p.read_bytes() for p in stored} == stored


def test_a_cited_difference_of_means_over_unequal_seeds_is_not_said_not_reproduced(rb):
    """The paper's -0.5 is fp32's mean over 3 seeds less int8's over 2: right, and not what rb's paired reading over the
    2 seeds both arms ran gives (-0.4). rb never says such a number is not reproduced: the two are different estimands,
    so the rule is not comparable, and rb says the difference of the arms' means it reads (-0.5)."""
    Path(rb.root, "paper.md").write_text("INT8 changes top-1 by -0.5 points against FP32.\n", encoding="utf-8")
    rb("experiment", "add", "INT8 reproduced", "--id", "int8", "--purpose", "the paper's number", "--baseline", "fp32", "--candidate", "int8")
    rb("spec", "set", "int8", "seed", "--per-run")
    crit = rb("criterion", "add", "The paper's -0.5 top-1", "-e", "int8", "--metric", "change.top1", "--equals", "-0.5", "--tolerance", "0.05",
              "--source", "paper.md", "--quote", "by -0.5 points", "--locator", "line 1").data["object"]["id"]
    for variant, seed, top1 in [("fp32", 1, 76.0), ("fp32", 2, 76.2), ("fp32", 3, 76.4), ("int8", 1, 75.5), ("int8", 2, 75.9)]:
        rb("run", "add", "int8", f"top1={top1}", "--variant", variant, "--set", f"seed={seed}", "--produced-at", "2030-01-01T00:00:00Z")
    f = rb("show", "--long", crit).data["finding"]
    assert f["rule_outcome"] == "undetermined", f["says"]
    assert "not reproduced" not in json.dumps(f["says"])
    [problem] = [p for p in f["integrity"] if p["code"] == "not_comparable"]
    assert "paired reading reads only the 2 seeds both arms ran and leaves out " in problem["text"], problem["text"]
    assert "means over all their seeds (int8 over 2, fp32 over 3), which is -0.5 here" in problem["text"], problem["text"]
    code, out = rb("show", "--long", crit, text=True)
    assert "not reproduced" not in out and "not comparable" in out


def test_a_cited_difference_where_every_seed_pairs_is_read_as_before(rb):
    """With every unit paired, the mean of the differences is the difference of the means: the cited number is read."""
    Path(rb.root, "paper.md").write_text("INT8 changes top-1 by -0.5 points against FP32.\n", encoding="utf-8")
    rb("experiment", "add", "INT8 reproduced", "--id", "int8", "--purpose", "the paper's number", "--baseline", "fp32", "--candidate", "int8")
    rb("spec", "set", "int8", "seed", "--per-run")
    crit = rb("criterion", "add", "The paper's -0.5 top-1", "-e", "int8", "--metric", "change.top1", "--equals", "-0.5", "--tolerance", "0.05",
              "--source", "paper.md", "--quote", "by -0.5 points", "--locator", "line 1").data["object"]["id"]
    for variant, seed, top1 in [("fp32", 1, 76.0), ("fp32", 2, 76.2), ("int8", 1, 75.5), ("int8", 2, 75.7)]:
        rb("run", "add", "int8", f"top1={top1}", "--variant", variant, "--set", f"seed={seed}", "--produced-at", "2030-01-01T00:00:00Z")
    f = rb("show", "--long", crit).data["finding"]
    assert f["rule_outcome"] == "meets" and not [p for p in f["integrity"] if p["code"] == "not_comparable"], f


def test_one_run_outside_a_rules_selectors_is_said_in_the_singular(rb):
    rb("experiment", "add", "INT8 against FP32", "--id", "int8", "--purpose", "the table", "--baseline", "fp32", "--candidate", "int8")
    rb("spec", "set", "int8", "batch", "--per-run")
    crit = rb("criterion", "add", "INT8 keeps top-1 within a point at batch 64", "-e", "int8", "--metric", "change.top1", "--at-least", "-1",
              "--where", "batch=64").data["object"]["id"]
    for batch in (64, 32):
        rb("run", "add", "int8", "fp32.top1=76.1", "int8.top1=75.6", "--set", f"batch={batch}", "--produced-at", "2030-01-01T00:00:00Z")
    notes = {c["code"]: c["text"] for c in rb("show", "--long", crit).data["finding"]["caveats"]}
    assert notes["run_scope"].startswith("1 run outside this criterion's per-run selectors is not read: run"), notes["run_scope"]
    # the run it leaves out is named, with its value of what the rule selects by (round 5)
    assert notes["run_scope"].endswith(" (batch=32)"), notes["run_scope"]


def cited_minus_half(rb, *design: str) -> str:
    """An experiment whose cited rule is the paper's -0.5 for INT8 against FP32 on top-1, with seed per run."""
    Path(rb.root, "paper.md").write_text("INT8 changes top-1 by -0.5 points against FP32.\n", encoding="utf-8")
    rb("experiment", "add", "INT8 reproduced", "--id", "int8", "--purpose", "the paper's number", "--baseline", "fp32", "--candidate", "int8")
    rb("spec", "set", "int8", "seed", "--per-run")
    for name in design:
        rb("spec", "set", "int8", name, "--design-factor")
    return rb("criterion", "add", "The paper's -0.5 top-1", "-e", "int8", "--metric", "change.top1", "--equals", "-0.5", "--tolerance", "0.05",
              "--source", "paper.md", "--quote", "by -0.5 points", "--locator", "line 1").data["object"]["id"]


def typed(rb, *numbers: str, **per_run) -> str:
    sets = [a for k, v in per_run.items() for a in ("--set", f"{k}={v}")]
    return rb("run", "add", "int8", *numbers, *sets, "--produced-at", "2030-01-01T00:00:00Z").data["object"]["id"]


def not_comparable(rb, crit: str) -> str:
    f = rb("show", "--long", crit).data["finding"]
    assert f["rule_outcome"] == "undetermined" and "not reproduced" not in json.dumps(f["says"]), f["says"]
    [problem] = [p for p in f["integrity"] if p["code"] == "not_comparable"]
    return problem["text"]


def test_a_cited_difference_across_design_cells_with_unequal_seeds_is_not_comparable(rb):
    crit = cited_minus_half(rb, "batch")
    for batch in (32, 64):
        for seed, fp32, int8 in ((1, 76.0, 75.5), (2, 76.2, 75.7)):
            typed(rb, f"top1={fp32}", "--variant", "fp32", seed=seed, batch=batch)
            typed(rb, f"top1={int8}", "--variant", "int8", seed=seed, batch=batch)
    typed(rb, "top1=70.0", "--variant", "fp32", seed=3, batch=64)
    text = not_comparable(rb, crit)
    assert text.startswith("rb's paired reading reads only the units both arms ran within each design cell and leaves out "), text


def test_a_cited_difference_where_some_runs_give_the_difference_is_not_comparable(rb):
    crit = cited_minus_half(rb)
    typed(rb, "fp32.top1=76.0", "int8.top1=75.5", seed=1)
    typed(rb, "fp32.top1=76.2", "int8.top1=75.7", seed=2)
    typed(rb, "top1=70.0", "--variant", "fp32", seed=3)
    text = not_comparable(rb, crit)
    assert text.startswith("rb's paired reading reads only the units both arms ran and leaves out run") and "which is" not in text, text


def test_a_cited_difference_of_a_variant_with_itself_names_each_arm_and_its_mean(rb):
    Path(rb.root, "paper.md").write_text("Batch 64 changes INT8 top-1 by -0.5 points against batch 32.\n", encoding="utf-8")
    rb("experiment", "add", "INT8 at two batch sizes", "--id", "int8", "--purpose", "the paper's number", "--baseline", "fp32", "--candidate", "int8")
    rb("spec", "set", "int8", "seed", "--per-run")
    rb("spec", "set", "int8", "batch", "--per-run")
    crit = rb("criterion", "add", "The paper's -0.5 top-1", "-e", "int8", "--metric", "top1", "--subject", "int8", "--reference", "int8",
              "--subject-where", "batch=64", "--reference-where", "batch=32", "--equals", "-0.5", "--tolerance", "0.05",
              "--source", "paper.md", "--quote", "by -0.5 points", "--locator", "line 1").data["object"]["id"]
    for batch, seed, top1 in [(32, 1, 76.0), (32, 2, 76.2), (32, 3, 76.4), (64, 1, 75.5), (64, 2, 75.9)]:
        typed(rb, f"top1={top1}", "--variant", "int8", seed=seed, batch=batch)
    text = not_comparable(rb, crit)
    assert "(int8 at batch=64 over 2, int8 at batch=32 over 3), which is -0.5 here" in text, text


def test_the_arms_means_average_a_units_cases_as_rbs_reading_does(rb):
    """A repeat is one case with the run it repeats; a second execution is a case of its own; a unit's cases are
    averaged, then the units: fp32 is (76.0 + 76.2 + mean(mean(76.4, 76.0), 77.2)) / 3 = 76.3, int8 75.7 (a flat mean of
    seed 3's runs would give -0.5444)."""
    crit = cited_minus_half(rb)
    for variant, seed, top1 in [("fp32", 1, 76.0), ("fp32", 2, 76.2), ("int8", 1, 75.5), ("int8", 2, 75.9)]:
        typed(rb, f"top1={top1}", "--variant", variant, seed=seed)
    first = typed(rb, "top1=76.4", "--variant", "fp32", seed=3)
    typed(rb, "top1=76.0", "--variant", "fp32", "--repeats", first, seed=3)
    typed(rb, "top1=77.2", "--variant", "fp32", "--again", "--why", "a second execution", seed=3)
    # the paired -0.4 and the means' -0.6 both miss -0.5 ± 0.05: the rule is judged, with the means said beside it
    f = rb("show", "--long", crit).data["finding"]
    assert f["rule_outcome"] == "misses" and not [p for p in f["integrity"] if p["code"] == "not_comparable"]
    [said] = [c["text"] for c in f["caveats"] if c["code"] == "cited_differs"]
    assert "(int8 over 2, fp32 over 3), which is -0.6 here" in said


def test_a_cited_difference_a_clone_brought_is_not_comparable_either(rb, tmp_path):
    crit = cited_minus_half(rb)
    rb("export", "--with-record", "--out", str(tmp_path / "bundle"))
    rb("clone", str(tmp_path / "bundle"), str(tmp_path / "rerun"))
    import os
    os.chdir(tmp_path / "rerun")
    for variant, seed, top1 in [("fp32", 1, 76.0), ("fp32", 2, 76.2), ("fp32", 3, 76.4), ("int8", 1, 75.5), ("int8", 2, 75.9)]:
        typed(rb, f"top1={top1}", "--variant", variant, seed=seed)
    assert "(int8 over 2, fp32 over 3), which is -0.5 here" in not_comparable(rb, crit)


def test_a_cited_value_is_said_only_on_a_rule_that_cites_a_number(rb):
    Path(rb.root, "paper.md").write_text("The paper evaluates on 50000 images and reports 75.6 top-1.\n", encoding="utf-8")
    rb("experiment", "add", "INT8 reproduced", "--id", "int8", "--purpose", "the paper's number", "--baseline", "fp32", "--candidate", "int8")
    rb("spec", "set", "int8", "images", "--per-run", "--cited", "50000", "--cited-source", "paper.md:1")
    cited = rb("criterion", "add", "The paper's 75.6 top-1", "-e", "int8", "--metric", "int8.top1", "--equals", "75.6", "--tolerance", "0.05",
               "--source", "paper.md", "--quote", "reports 75.6 top-1", "--locator", "line 1").data["object"]["id"]
    own = rb("criterion", "add", "INT8 keeps top-1 within a point", "-e", "int8", "--metric", "change.top1", "--at-least", "-1").data["object"]["id"]
    rb("run", "add", "int8", "int8.top1=75.1", "fp32.top1=76.0", "--set", "images=10000", "--produced-at", "2030-01-01T00:00:00Z")
    assert any(p["code"] == "not_comparable" for p in rb("show", "--long", cited).data["finding"]["integrity"])
    f = rb("show", "--long", own).data["finding"]
    assert not any(c["code"] == "cited_differs" for c in f["caveats"]) and not f["integrity"], f


def test_a_call_on_an_undetermined_rule_is_said_as_unjudged_and_a_cited_value_is_said_back(rb):
    Path(rb.root, "paper.md").write_text("The paper evaluates on 50000 images and reports 75.6 top-1.\n", encoding="utf-8")
    rb("experiment", "add", "INT8 reproduced", "--id", "int8", "--purpose", "the paper's number", "--baseline", "fp32", "--candidate", "int8")
    code, out = rb("spec", "set", "int8", "images", "--per-run", "--cited", "50000", "--cited-source", "paper.md:1", text=True)
    assert "  cited: 50000 (paper.md:1)" in out, out
    crit = rb("criterion", "add", "The paper's 75.6 top-1", "-e", "int8", "--metric", "int8.top1", "--equals", "75.6", "--tolerance", "0.05",
              "--source", "paper.md", "--quote", "reports 75.6 top-1", "--locator", "line 1").data["object"]["id"]
    rb("run", "add", "int8", "int8.top1=75.1", "fp32.top1=76.0", "--set", "images=10000", "--produced-at", "2030-01-01T00:00:00Z")
    code, out = rb("decide", crit, "accept", "--why", "the subset is what we ship on", text=True)
    assert "goes against the rule" not in out and "rb cannot judge this rule yet (criterion undetermined)" in out, out
    rb("page", "--out", "page.html")
    page = " ".join(re.sub(r"<[^>]+>", " ", Path(rb.root, "page.html").read_text(encoding="utf-8")).split())
    assert f"accepted {crit} while rb cannot judge its rule (criterion undetermined)" in page and "against its rule (criterion undetermined)" not in page


def test_a_direction_cited_by_its_words_compares_nothing_and_reads_as_its_rule_says(rb):
    """A file says INT8 is worse on every set, with no number: over unequal seeds the paired reading and the difference
    of the means are both below 0, and nothing is compared, so the rule is judged, never "not comparable"."""
    Path(rb.root, "notes.md").write_text("INT8 is worse than FP32 on every set.\n", encoding="utf-8")
    rb("experiment", "add", "INT8 against FP32", "--id", "int8", "--purpose", "the note's claim", "--baseline", "fp32", "--candidate", "int8")
    rb("spec", "set", "int8", "seed", "--per-run")
    crit = rb("criterion", "add", "INT8 is worse than FP32", "-e", "int8", "--metric", "change.top1", "--at-most", "0",
              "--source", "notes.md", "--quote", "INT8 is worse than FP32 on every set").data["object"]["id"]
    for variant, seed, top1 in [("fp32", 1, 76.0), ("fp32", 2, 76.2), ("fp32", 3, 76.4), ("int8", 1, 75.5), ("int8", 2, 75.9)]:
        rb("run", "add", "int8", f"top1={top1}", "--variant", variant, "--set", f"seed={seed}", "--produced-at", "2030-01-01T00:00:00Z")
    f = rb("show", "--long", crit).data["finding"]
    assert f["rule_outcome"] == "meets" and not [p for p in f["integrity"] if p["code"] == "not_comparable"], f


def test_a_cited_number_both_readings_meet_is_judged_with_the_difference_said_beside_it(rb):
    Path(rb.root, "paper.md").write_text("INT8 changes top-1 by -0.45 points against FP32.\n", encoding="utf-8")
    rb("experiment", "add", "INT8 reproduced", "--id", "int8", "--purpose", "the paper's number", "--baseline", "fp32", "--candidate", "int8")
    rb("spec", "set", "int8", "seed", "--per-run")
    crit = rb("criterion", "add", "The paper's -0.45 top-1", "-e", "int8", "--metric", "change.top1", "--equals", "-0.45", "--tolerance", "0.1",
              "--source", "paper.md", "--quote", "by -0.45 points", "--locator", "line 1").data["object"]["id"]
    for variant, seed, top1 in [("fp32", 1, 76.0), ("fp32", 2, 76.2), ("fp32", 3, 76.4), ("int8", 1, 75.5), ("int8", 2, 75.9)]:
        rb("run", "add", "int8", f"top1={top1}", "--variant", variant, "--set", f"seed={seed}", "--produced-at", "2030-01-01T00:00:00Z")
    f = rb("show", "--long", crit).data["finding"]
    assert f["rule_outcome"] == "meets" and not [p for p in f["integrity"] if p["code"] == "not_comparable"], f
    assert [c for c in f["caveats"] if c["code"] == "cited_differs"]


def test_a_zero_the_cited_words_state_is_a_number_the_reading_can_miss(rb):
    """The paper says the change is at least 0 points: a stated 0 is compared as a number is, and where the paired reading
    and the arms' means fall on either side of it the rule is not comparable."""
    Path(rb.root, "paper.md").write_text("The change is at least 0 points.\n", encoding="utf-8")
    rb("experiment", "add", "INT8 reproduced", "--id", "int8", "--purpose", "the paper's number", "--baseline", "fp32", "--candidate", "int8")
    rb("spec", "set", "int8", "seed", "--per-run")
    crit = rb("criterion", "add", "The change is at least 0", "-e", "int8", "--metric", "change.top1", "--at-least", "0",
              "--source", "paper.md", "--quote", "at least 0 points").data["object"]["id"]
    for variant, seed, top1 in [("fp32", 1, 76.0), ("fp32", 2, 76.0), ("fp32", 3, 90.0), ("int8", 1, 77.0), ("int8", 2, 77.0)]:
        rb("run", "add", "int8", f"top1={top1}", "--variant", variant, "--set", f"seed={seed}", "--produced-at", "2030-01-01T00:00:00Z")
    f = rb("show", "--long", crit).data["finding"]
    assert f["rule_outcome"] == "undetermined" and [p for p in f["integrity"] if p["code"] == "not_comparable"], f["says"]
