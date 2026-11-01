"""Arms that differ in a per-run setting, a rule that pools a per-run setting's values, and the page that carries
what rb show says of each rule.

Two arms read from runs that share no value of a per-run setting (500 images against 250) carry a note that says so,
as a shared setting's undeclared difference is said; a rule whose runs pool several values of a per-run setting says so
in its headline, and names each value whose runs alone would miss it; the exported page carries every note rb show
gives under a rule, in the same words. Every name and number is synthetic: an INT8 ResNet-50 on ImageNet, recorded by
human:ana."""
import html
import re
import subprocess
from pathlib import Path

import pytest

from rabbit_brain.models import Envelope
from rbtest import as_the_user, main


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

    def run(*argv, text=False):
        if text:
            code = main(list(argv), typed=True)
            return code, capsys.readouterr().out
        code = main([*argv, "--json"], typed=True)
        env = Envelope.model_validate_json(capsys.readouterr().out)
        assert code in (0, 1) and env.ok, (argv, code, env.errors)
        return env

    run.root = root
    run("init", "INT8 ResNet-50", "--no-agents")
    run("metric", "add", "top1", "--maximize", "--unit", "percent")
    return run


def page_text(rb) -> str:
    rb("page", "--out", "page.html")
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", Path(rb.root, "page.html").read_text(encoding="utf-8"))).split())


def test_arms_that_share_no_value_of_a_per_run_setting_say_so(rb):
    rb("experiment", "add", "INT8 against FP32", "--id", "int8", "--purpose", "the runs", "--baseline", "fp32", "--candidate", "int8")
    rb("spec", "set", "int8", "seed", "--per-run")
    rb("spec", "set", "int8", "images", "--per-run")
    crit = rb("criterion", "add", "INT8 keeps top-1 within a point", "-e", "int8", "--metric", "top1", "--subject", "int8", "--reference", "fp32",
              "--at-least", "-1").data["object"]["id"]
    rb("run", "add", "int8", "top1=76.1", "--variant", "fp32", "--set", "seed=1", "--set", "images=250", "--produced-at", "2030-01-01T00:00:00Z")
    rb("run", "add", "int8", "top1=75.6", "--variant", "int8", "--set", "seed=1", "--set", "images=500", "--produced-at", "2030-01-01T00:00:00Z")
    f = rb("show", crit).data["finding"]
    notes = {c["code"]: c["text"] for c in f["caveats"]}
    assert notes["arms_differ"] == "the arms differ in images: int8's runs hold 500, fp32's 250, so the comparison carries that difference too"
    code, out = rb("show", crit, text=True)
    assert notes["arms_differ"] in out
    assert notes["arms_differ"] in page_text(rb)


def test_arms_that_share_a_value_say_nothing(rb):
    rb("experiment", "add", "INT8 against FP32", "--id", "int8", "--purpose", "the runs", "--baseline", "fp32", "--candidate", "int8")
    rb("spec", "set", "int8", "seed", "--per-run")
    crit = rb("criterion", "add", "INT8 keeps top-1 within a point", "-e", "int8", "--metric", "top1", "--subject", "int8", "--reference", "fp32",
              "--at-least", "-1").data["object"]["id"]
    for seed, variant, top1 in ((1, "fp32", 76.1), (2, "fp32", 76.0), (2, "int8", 75.6)):
        rb("run", "add", "int8", f"top1={top1}", "--variant", variant, "--set", f"seed={seed}", "--produced-at", "2030-01-01T00:00:00Z")
    assert not any(c["code"] == "arms_differ" for c in rb("show", crit).data["finding"]["caveats"])


def test_a_rule_that_pools_a_per_run_setting_says_so_and_names_the_value_that_misses(rb):
    rb("experiment", "add", "INT8 on every validation set", "--id", "int8", "--purpose", "the runs", "--baseline", "fp32", "--candidate", "int8")
    rb("spec", "set", "int8", "seed", "--per-run")
    rb("spec", "set", "int8", "valset", "--per-run")
    rb("spec", "set", "int8", "population.unit", "seed", "--no-verify")
    crit = rb("criterion", "add", "INT8 scores 75 top-1 or more on every validation set", "-e", "int8", "--metric", "top1", "--subject", "int8",
              "--at-least", "75", "--each").data["object"]["id"]
    for seed in (1, 2, 3):
        for valset, top1 in (("imagenet-val", 76.0), ("imagenet-v2", 74.2), ("imagenet-real", 76.5)):
            rb("run", "add", "int8", f"top1={top1 + seed / 100}", "--variant", "int8", "--set", f"seed={seed}", "--set", f"valset={valset}",
               "--produced-at", "2030-01-01T00:00:00Z")
    f = rb("show", crit).data["finding"]
    assert f["rule_outcome"] == "meets"
    assert f["says"]["headline"].endswith("· pooled over 3 valset values, criterion not met at valset=imagenet-v2"), f["says"]["headline"]
    notes = {c["code"]: c["text"] for c in f["caveats"]}
    # an --each rule's miss there is said by its units, never one seed's value read as the set's
    assert notes["pooled_misses"].startswith("the runs at valset=imagenet-v2 (all 3 units below 75, lowest 74.21) alone would not meet the rule")
    page = page_text(rb)
    assert notes["pooled_misses"] in page and notes["pooled_setting"] in page
    # the card's reading leads with rb show's headline words for the pooling, on the question's row and the rule's
    assert page.count("Pooled over 3 valset values, criterion not met at valset=imagenet-v2: INT8 is ") >= 2, page
    status = rb("status")
    assert "pooled over 3 valset values, criterion not met at valset=imagenet-v2" in status.data["findings"][0]["says"]["headline"]



def test_a_pooled_value_is_judged_on_the_difference_of_arms_recorded_apart(rb):
    rb("experiment", "add", "INT8 against FP32 on two validation sets", "--id", "int8", "--purpose", "the runs", "--baseline", "fp32", "--candidate", "int8")
    rb("spec", "set", "int8", "seed", "--per-run")
    rb("spec", "set", "int8", "valset", "--per-run")
    rb("spec", "set", "int8", "population.unit", "seed", "--no-verify")
    crit = rb("criterion", "add", "INT8 gains 5 points", "-e", "int8", "--metric", "top1", "--subject", "int8", "--reference", "fp32",
              "--at-least", "5").data["object"]["id"]
    wide = rb("criterion", "add", "INT8 within 30 points", "-e", "int8", "--metric", "top1", "--subject", "int8", "--reference", "fp32",
              "--at-most", "30").data["object"]["id"]
    for seed in (1, 2, 3):
        for valset, int8, fp32 in (("imagenet-val", 60 + seed, 40 + seed), ("imagenet-v2", 40 + seed, 41 + seed)):
            rb("run", "add", "int8", f"top1={int8}", "--variant", "int8", "--set", f"seed={seed}", "--set", f"valset={valset}", "--produced-at", "2030-01-01")
            rb("run", "add", "int8", f"top1={fp32}", "--variant", "fp32", "--set", f"seed={seed}", "--set", f"valset={valset}", "--produced-at", "2030-01-01")
    f = rb("show", crit).data["finding"]
    assert f["says"]["headline"].endswith("pooled over 2 valset values, criterion not met at valset=imagenet-v2"), f["says"]["headline"]
    assert "the runs at valset=imagenet-v2 (-1, 3 units) alone would not meet the rule" in {c["code"]: c["text"] for c in f["caveats"]}["pooled_misses"]
    g = rb("show", wide).data["finding"]
    assert not any(c["code"] == "pooled_misses" for c in g["caveats"]), g["caveats"]        # 20 and -1 both within 30


def test_arms_that_differ_within_each_unit_say_so(rb):
    rb("experiment", "add", "INT8 against FP32", "--id", "int8", "--purpose", "the runs", "--baseline", "fp32", "--candidate", "int8")
    rb("spec", "set", "int8", "seed", "--per-run")
    rb("spec", "set", "int8", "images", "--per-run")
    rb("spec", "set", "int8", "population.unit", "seed", "--no-verify")
    crit = rb("criterion", "add", "INT8 keeps top-1 within a point", "-e", "int8", "--metric", "top1", "--subject", "int8", "--reference", "fp32",
              "--at-least", "-1").data["object"]["id"]
    for seed, a, b in ((1, 500, 250), (2, 250, 500)):
        rb("run", "add", "int8", "top1=75.6", "--variant", "int8", "--set", f"seed={seed}", "--set", f"images={a}", "--produced-at", "2030-01-01")
        rb("run", "add", "int8", "top1=76.1", "--variant", "fp32", "--set", f"seed={seed}", "--set", f"images={b}", "--produced-at", "2030-01-01")
    notes = {c["code"]: c["text"] for c in rb("show", crit).data["finding"]["caveats"]}
    assert notes["arms_differ"] == "the arms differ in images: within a unit, the two arms' runs hold different values, so the comparison carries that difference too"


def test_a_variant_compared_with_itself_names_each_arm_by_its_selection(rb):
    """Two arms of one variant, told apart by their selections: the note names each by its selection, never the variant
    twice ("int8's runs hold 1, int8's 2")."""
    rb("experiment", "add", "INT8 at two batch sizes", "--id", "int8", "--purpose", "the runs", "--baseline", "fp32", "--candidate", "int8")
    rb("spec", "set", "int8", "seed", "--per-run")
    rb("spec", "set", "int8", "batch", "--per-run")
    crit = rb("criterion", "add", "INT8 keeps top-1 at batch 64", "-e", "int8", "--metric", "top1", "--subject", "int8", "--reference", "int8",
              "--subject-where", "batch=64", "--reference-where", "batch=32", "--at-least", "-1").data["object"]["id"]
    rb("run", "add", "int8", "top1=75.6", "--variant", "int8", "--set", "seed=1", "--set", "batch=64", "--produced-at", "2030-01-01T00:00:00Z")
    rb("run", "add", "int8", "top1=75.4", "--variant", "int8", "--set", "seed=2", "--set", "batch=32", "--produced-at", "2030-01-01T00:00:00Z")
    notes = {c["code"]: c["text"] for c in rb("show", crit).data["finding"]["caveats"]}
    assert notes["arms_differ"] == ("the arms differ in seed: the runs of int8 at batch=64 hold 1, those of int8 at batch=32 hold 2, "
                                    "so the comparison carries that difference too")


def test_the_page_says_a_reading_and_its_rule_to_the_rule_s_own_digits(rb):
    """A cosine cited as 0.999349 ± 5e-07: the card says the reading 0.999349, the band ± 0.0000005, never 1.00 or
    ± 0.000000."""
    Path(rb.root, "paper.md").write_text("INT8 keeps the hidden cosine at 0.999349.\n", encoding="utf-8")
    rb("metric", "add", "cosine", "--maximize")
    rb("experiment", "add", "INT8 reproduced", "--id", "int8", "--purpose", "the paper's number", "--baseline", "fp32", "--candidate", "int8")
    rb("criterion", "add", "The paper's cosine", "-e", "int8", "--metric", "cosine", "--subject", "int8", "--equals", "0.999349",
       "--tolerance", "0.0000005", "--source", "paper.md", "--quote", "at 0.999349", "--locator", "line 1")
    rb("run", "add", "int8", "cosine=0.999349", "--variant", "int8", "--produced-at", "2030-01-01T00:00:00Z")
    page = page_text(rb)
    assert "INT8 is 0.999349, 1 run; the cited number is 0.999349" in page and "margin 0.0000005" in page, page
    assert "0.000000 " not in page + " " and "\u00b1 0.0000005" in page, page


def test_arms_each_at_one_value_pool_nothing_and_say_only_that_they_differ(rb):
    """INT8's runs hold 500 images and FP32's 250: nothing is pooled, so the reading says the arms differ, never
    "pooled over 2 images values"."""
    rb("experiment", "add", "INT8 against FP32", "--id", "int8", "--purpose", "the runs", "--baseline", "fp32", "--candidate", "int8")
    rb("spec", "set", "int8", "seed", "--per-run")
    rb("spec", "set", "int8", "images", "--per-run")
    rb("spec", "set", "int8", "population.unit", "seed", "--no-verify")
    crit = rb("criterion", "add", "INT8 keeps top-1 within a point", "-e", "int8", "--metric", "top1", "--subject", "int8", "--reference", "fp32",
              "--at-least", "-1").data["object"]["id"]
    for seed in (1, 2):
        rb("run", "add", "int8", "top1=76.1", "--variant", "fp32", "--set", f"seed={seed}", "--set", "images=250", "--produced-at", "2030-01-01T00:00:00Z")
        rb("run", "add", "int8", "top1=75.6", "--variant", "int8", "--set", f"seed={seed}", "--set", "images=500", "--produced-at", "2030-01-01T00:00:00Z")
    f = rb("show", crit).data["finding"]
    codes = [c["code"] for c in f["caveats"]]
    assert "arms_differ" in codes and "pooled_setting" not in codes and "pooled over" not in f["says"]["headline"], (codes, f["says"]["headline"])


def test_a_pooled_miss_of_an_equals_rule_names_the_unit_farthest_from_its_number(rb):
    rb("experiment", "add", "INT8 on two validation sets", "--id", "int8", "--purpose", "the runs", "--baseline", "fp32", "--candidate", "int8")
    rb("spec", "set", "int8", "seed", "--per-run")
    rb("spec", "set", "int8", "valset", "--per-run")
    rb("spec", "set", "int8", "population.unit", "seed", "--no-verify")
    crit = rb("criterion", "add", "INT8 scores 5 on every set", "-e", "int8", "--metric", "top1", "--subject", "int8", "--equals", "5",
              "--tolerance", "0.1", "--each").data["object"]["id"]
    for seed, valset, top1 in ((1, "a", 4.0), (2, "a", 5.2), (1, "b", 6.0), (2, "b", 4.8)):
        rb("run", "add", "int8", f"top1={top1}", "--variant", "int8", "--set", f"seed={seed}", "--set", f"valset={valset}", "--produced-at", "2030-01-01T00:00:00Z")
    text = {c["code"]: c["text"] for c in rb("show", crit).data["finding"]["caveats"]}.get("pooled_misses", "")
    # 4.0 is farther from 5 than 5.2 is: the farthest, whichever side it is on
    assert "valset=a (all 2 units outside 5 \u00b1 0.1, farthest 4)" in text and "valset=b (all 2 units outside 5 \u00b1 0.1, farthest 6)" in text, text


def test_a_reading_on_the_edge_of_its_band_has_no_margin_of_float_residue(rb):
    Path(rb.root, "paper.md").write_text("INT8 reaches a perplexity of 5.40.\n", encoding="utf-8")
    rb("metric", "add", "ppl", "--minimize")
    rb("experiment", "add", "INT8 reproduced", "--id", "int8", "--purpose", "the paper's number", "--baseline", "fp32", "--candidate", "int8")
    rb("criterion", "add", "The paper's 5.40", "-e", "int8", "--metric", "ppl", "--subject", "int8", "--equals", "5.40", "--tolerance", "0.05",
       "--source", "paper.md", "--quote", "of 5.40", "--locator", "line 1")
    rb("run", "add", "int8", "int8.ppl=5.45", "--produced-at", "2030-01-01T00:00:00Z")
    page = page_text(rb)
    assert "margin 0.00" in page and "0.000000000" not in page, page[page.find("margin"):][:200]


def test_a_large_reading_on_the_edge_of_its_band_has_no_margin_of_float_residue(rb):
    Path(rb.root, "paper.md").write_text("INT8 serves 1000000 images a day.\n", encoding="utf-8")
    rb("metric", "add", "images", "--maximize")
    rb("experiment", "add", "INT8 reproduced", "--id", "int8", "--purpose", "the paper's number", "--baseline", "fp32", "--candidate", "int8")
    rb("criterion", "add", "The paper's million", "-e", "int8", "--metric", "images", "--subject", "int8", "--equals", "1000000", "--tolerance", "0.05",
       "--source", "paper.md", "--quote", "serves 1000000 images", "--locator", "line 1")
    rb("run", "add", "int8", "int8.images=1000000.05", "--produced-at", "2030-01-01T00:00:00Z")
    page = page_text(rb)
    assert "margin -0.0" not in page and "0.00000000" not in page, page[page.find("margin"):][:200]
