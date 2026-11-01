"""The exported page, which is what a researcher keeps, carries every note rb show gives under a rule, in the same
words: numbers typed in, a cited value not comparable, and the rest. Every name and number is synthetic: an INT8
ResNet-50 on ImageNet, recorded by human:ana."""
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

    def run(*argv):
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



def test_the_page_carries_what_rb_show_says_under_a_rule(rb):
    rb("experiment", "add", "INT8 against FP32", "--id", "int8", "--purpose", "the paper's table", "--baseline", "fp32", "--candidate", "int8")
    crit = rb("criterion", "add", "INT8 keeps top-1 within a point", "-e", "int8", "--metric", "change.top1", "--at-least", "-1").data["object"]["id"]
    rb("run", "add", "int8", "fp32.top1=76.1", "int8.top1=75.6")
    f = rb("show", crit).data["finding"]
    typed = next(c["text"] for c in f["caveats"] if c["code"] == "typed")
    assert typed.startswith("typed in, not read from a file or run")
    page = page_text(rb)
    # in rb show's words, with the run named by what it is, as a page names every run rb minted an id for
    assert typed.split(":")[0] + ":" in page



def test_the_page_carries_a_cited_rule_that_is_not_comparable(rb):
    Path(rb.root, "paper.md").write_text("The paper evaluates on 50000 images and reports 75.6 top-1.\n", encoding="utf-8")
    rb("experiment", "add", "INT8 reproduced", "--id", "int8", "--purpose", "the paper's number", "--baseline", "fp32", "--candidate", "int8")
    rb("spec", "set", "int8", "eval.images", "10000", "--no-verify", "--cited", "50000", "--cited-source", "paper.md:1")
    crit = rb("criterion", "add", "The paper's 75.6 top-1", "-e", "int8", "--metric", "int8.top1", "--equals", "75.6", "--tolerance", "0.05",
              "--source", "paper.md", "--quote", "reports 75.6 top-1", "--locator", "line 1").data["object"]["id"]
    rb("run", "add", "int8", "int8.top1=75.1", "fp32.top1=76.0", "--produced-at", "2030-01-01T00:00:00Z")
    f = rb("show", crit).data["finding"]
    problem = next(p["text"] for p in f["integrity"] if p["code"] == "not_comparable")
    assert problem in page_text(rb)
