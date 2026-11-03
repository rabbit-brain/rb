"""The page tells twin runs apart by their results folder, gives a run's numbers in rb's own digits, and a tally never
hides a reading's outcome. Every name and number is synthetic: an INT8 ResNet-50 on ImageNet, recorded by human:ana."""
import json
import re
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

    run.root = root
    run("init", "INT8 ResNet-50", "--no-agents")
    run("metric", "add", "cosine", "--maximize")
    run("experiment", "add", "INT8 alone", "--id", "solo", "--purpose", "a level", "--baseline", "int8")
    run("spec", "set", "solo", "seed", "--per-run")
    return run


def page_text(rb):
    rb("page", "--out", "p.html")
    return (rb.root / "p.html").read_text(encoding="utf-8")


def test_twin_runs_are_named_by_their_results_folder(rb):
    rb("criterion", "add", "INT8 keeps cosine", "-e", "solo", "--metric", "cosine", "--subject", "int8", "--at-least", "0.9")
    for d in ("results/r04_s0", "results/r12_s0"):
        (rb.root / d).mkdir(parents=True)
        (rb.root / d / "metrics.json").write_text(json.dumps({"cosine": 0.95}), encoding="utf-8")
        rb("run", "add", "solo", "--from", f"{d}/metrics.json", "--set", "seed=0", "--again", "--why", "a second execution", "--produced-at", "2026-05-01")
    text = re.sub("<[^>]+>", "", page_text(rb))
    assert "from results/r04_s0/" in text and "from results/r12_s0/" in text


def test_a_run_card_gives_rbs_own_digits(rb):
    rb("criterion", "add", "INT8 keeps cosine 0.999349", "-e", "solo", "--metric", "cosine", "--subject", "int8", "--equals", "0.999349",
       "--tolerance", "0.0000005")
    rb("run", "add", "solo", "cosine=0.999349", "--set", "seed=0", "--produced-at", "2026-05-01")
    html = page_text(rb)
    cards = re.findall(r'<dl class="ax mets">(.*?)</dl>', html)
    assert cards and all("0.999349" in c and "1.00" not in c for c in cards), cards


def test_a_tally_says_the_rules_met_on_the_estimate_alone(rb):
    rb("criterion", "add", "INT8 averages at least 0.9", "-e", "solo", "--metric", "cosine", "--subject", "int8", "--at-least", "0.9")
    for seed, x in ((0, 0.95), (1, 0.85), (2, 0.96)):
        rb("run", "add", "solo", f"cosine={x}", "--set", f"seed={seed}", "--produced-at", "2026-05-01")
    code, out = rb("status", text=True)
    assert "Rules: 1 met, on the estimate alone" in out, out
    assert rb("status").data["tally_on_estimate"] == {"meets": 1}
    assert "1 met, on the estimate alone" in re.sub("<[^>]+>", "", page_text(rb))


def test_a_rule_met_across_its_whole_interval_is_not_said_met_on_the_estimate_alone(rb):
    """An unresolved difference whose whole interval clears the rule's line meets the rule across it."""
    rb("experiment", "add", "INT8 against FP32", "--id", "two", "--purpose", "a change", "--baseline", "fp32", "--candidate", "int8")
    rb("spec", "set", "two", "seed", "--per-run")
    rb("criterion", "add", "INT8 loses at most a point", "-e", "two", "--metric", "change.cosine", "--at-least", "-1")
    for seed, (a, b) in enumerate([(0.95, 0.96), (0.95, 0.94), (0.95, 0.95)]):
        rb("run", "add", "two", f"fp32.cosine={a}", f"int8.cosine={b}", "--set", f"seed={seed}", "--produced-at", "2026-05-01")
    f = rb("status").data
    assert [x["result"] for x in f["findings"]] == ["unresolved"]
    code, out = rb("status", text=True)
    assert "Rules: 1 met" in out and "on the estimate alone" not in out, out
