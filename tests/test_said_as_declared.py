"""A sentence that starts with a metric's name keeps it as declared (top1, acc, m.acc, never "Top1"); an untested --each
rule beside an analysis is told the run that can test it, never an analysis; one reading is written in one set of
digits on a page (its card and its list sentence alike). Every name and number is synthetic: an INT8 ResNet-50 on
ImageNet, recorded by human:ana."""
import html
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
    return run


def page_text(rb):
    rb("page", "--out", "page.html")
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", Path("page.html").read_text(encoding="utf-8"))))


def levels(rb):
    Path("paper.md").write_text("# Paper\n\nThe INT8 model reaches 0.83 top-1.\n", encoding="utf-8")
    rb("metric", "add", "top1", "--maximize")
    rb("metric", "add", "acc", "--maximize", "--unit", "%")
    rb("metric", "add", "m.acc", "--maximize")
    rb("experiment", "add", "INT8 alone", "--id", "e1", "--purpose", "a level", "--baseline", "int8")
    rb("criterion", "add", "INT8 reaches the paper's 0.83", "-e", "e1", "--metric", "top1", "--equals", "0.83", "--tolerance", "0.01",
       "--source", "paper.md", "--quote", "reaches 0.83 top-1", "--id", "c1")
    rb("criterion", "add", "acc holds 49", "-e", "e1", "--metric", "acc", "--at-least", "49", "--id", "c2")
    rb("criterion", "add", "m.acc holds 0.5", "-e", "e1", "--metric", "m.acc", "--at-least", "0.5", "--id", "c3")
    rb("run", "add", "e1", "top1=0.74", "acc=49.69", "m.acc=0.51", "--produced-at", "2026-05-01")


def test_a_sentence_that_starts_with_a_metric_keeps_its_name_as_declared(rb):
    levels(rb)
    text = page_text(rb)
    for name in ("Top1", "Acc", "M.acc"):
        assert f"{name} is" not in text, re.findall(rf".{{30}}{re.escape(name)} is.{{30}}", text)
    assert "top1 is 0.74, 1 run" in text and "acc is 49.69%, 1 run" in text and "m.acc is 0.51, 1 run" in text


def test_one_reading_is_written_in_one_set_of_digits_on_the_page(rb):
    """The list sentence said "0.740" where the card said "0.74" (a level against a cited 0.83 ± 0.01)."""
    levels(rb)
    text = page_text(rb)
    assert "0.740" not in text, re.findall(r".{40}0\.740.{20}", text)
    assert "top1 is 0.74; the cited number is 0.83" in text


def test_an_untested_each_rule_beside_an_analysis_is_told_a_run(rb):
    rb("metric", "add", "top1", "--maximize")
    rb("experiment", "add", "Wide against base", "--id", "e1", "--purpose", "the width", "--baseline", "base", "--candidate", "wide")
    rb("spec", "set", "e1", "seed", "--per-run")
    rb("analysis", "add", "e1", "--compare", "wide:top1=0.83[0.80,0.86]", "--method", "bootstrap over images", "--unit", "image", "--units", "200")
    rb("criterion", "add", "every seed above 0.7", "-e", "e1", "--metric", "wide.top1", "--at-least", "0.7", "--each", "--id", "c1")
    items = [i for i in rb("status", "--full").data["open"] if i["subject"] == "c1" and i["code"] == "untested"]
    assert items, rb("status", "--full").data["open"]
    for i in items:
        assert "rb analysis add" not in i["do"], i
        assert i["do"].startswith("rb run add e1"), i
    code, out = rb("status", "--full", text=True)
    assert "rb analysis add e1" not in out, out


def test_a_level_with_no_interval_is_said_in_its_card_s_digits_in_the_list_too(rb):
    """Codex M1: a bare level kept rb's own digits in the list ("top2 is 0.761") beside its card's ("0.76")."""
    rb("metric", "add", "top2", "--maximize")
    rb("experiment", "add", "level", "--id", "e1", "--purpose", "level", "--baseline", "base")
    rb("criterion", "add", "top2 holds", "-e", "e1", "--metric", "top2", "--at-least", "0.7", "--id", "c0")
    rb("run", "add", "e1", "top2=0.761", "--produced-at", "2026-05-01")
    text = page_text(rb)
    said = set(re.findall(r"(?i)top2 is (0\.\d+)", text))
    assert len(said) == 1, said


def test_a_variant_s_level_is_one_number_on_its_card_and_in_the_grid(rb):
    """Codex M1: the grid wrote INT8's mean 0.851 (beside a gap of 0.041 to FP32) where the rule's card said 0.85."""
    rb("metric", "add", "score", "--maximize")
    rb("experiment", "add", "INT8 against FP32", "--id", "e1", "--purpose", "the sweep", "--baseline", "fp32", "--candidate", "int8")
    rb("spec", "set", "e1", "seed", "--per-run")
    rb("criterion", "add", "INT8 scores at least 0.8", "-e", "e1", "--metric", "int8.score", "--at-least", "0.8", "--id", "c4")
    for seed, x in [(1, "0.841"), (2, "0.851"), (3, "0.861")]:
        rb("run", "add", "e1", "fp32.score=0.81", f"int8.score={x}", "--set", f"seed={seed}", "--produced-at", "2026-05-01")
    text = page_text(rb)
    assert "INT8 0.851 · 3 seeds" in text
    assert "INT8 is 0.851 (0.826 to 0.876)" in text and "INT8 is 0.85 " not in text and "INT8: 0.85 " not in text, re.findall(r"INT8:? (?:is )?0\.85\d*", text)


def test_an_each_rule_no_run_can_give_is_told_to_declare_it_again(rb):
    """Codex M1: an --each rule on an arm only an analysis names, or on an analysis's estimand, was still told to add
    an analysis, which never tests it."""
    rb("metric", "add", "top2", "--maximize")
    rb("experiment", "add", "Wide against base", "--id", "e2", "--purpose", "the width", "--baseline", "base", "--candidate", "wide")
    rb("analysis", "add", "e2", "--compare", "outside:top2=0.83[0.80,0.86]", "--method", "bootstrap", "--unit", "image", "--units", "200")
    rb("criterion", "add", "each external", "-e", "e2", "--metric", "top2", "--subject", "outside", "--at-least", "0.7", "--each", "--id", "ce")
    rb("criterion", "add", "each named estimand", "-e", "e2", "--metric", "wide.top2", "--estimand", "pooled", "--at-least", "0.7", "--each", "--id", "cp")
    items = [i for i in rb("status").data["open"] if i["subject"] in ("ce", "cp") and i["code"] == "untested"]
    assert len(items) == 2, items
    for i in items:
        assert "rb analysis add" not in i["do"] and i["do"].startswith(f"rb retract {i['subject']}"), i
