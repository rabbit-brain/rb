"""A setting is dated only against the runs that read it, and the integrity line never says ok above a run whose code
had uncommitted changes. Every name and number is synthetic: an INT8 ResNet-50 on ImageNet, recorded by human:ana."""
import os
import subprocess

import pytest

from rabbit_brain.models import Envelope
from rbtest import as_the_user, main

GIT = ["git", "-c", "user.name=ana", "-c", "user.email=ana@example.org", "-c", "commit.gpgsign=false"]


def commit(root, msg, when):
    env = dict(os.environ, GIT_AUTHOR_DATE=when, GIT_COMMITTER_DATE=when)
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    subprocess.run([*GIT, "-C", str(root), "commit", "-q", "-m", msg], check=True, env=env)


@pytest.fixture
def rb(tmp_path, monkeypatch, capsys):
    as_the_user(monkeypatch)
    monkeypatch.setenv("RB_ACTOR", "human:ana")
    root = tmp_path / "int8"
    (root / "configs").mkdir(parents=True)
    (root / "eval.py").write_text("print('top1')\n", encoding="utf-8")
    (root / "configs" / "fp32.yaml").write_text("calib: none\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    commit(root, "code", "2026-05-01T00:00:00Z")
    (root / "configs" / "int8.yaml").write_text("calib: minmax\n", encoding="utf-8")
    commit(root, "the int8 config", "2026-05-10T00:00:00Z")
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
    run("metric", "add", "top1", "--maximize")
    run("experiment", "add", "INT8 against FP32", "--id", "int8", "--purpose", "the runs", "--baseline", "fp32", "--candidate", "int8")
    run("spec", "set", "int8", "seed", "--per-run")
    run("spec", "set", "int8", "int8.calib", "--source", "configs/int8.yaml#calib")
    run("spec", "set", "int8", "fp32.calib", "--source", "configs/fp32.yaml#calib")
    run("spec", "vary", "int8", "calib")
    run("criterion", "add", "INT8 loses at most a point", "-e", "int8", "--metric", "change.top1", "--at-least", "-1", "--id", "c1")
    return run


def postdates(rb):
    f = rb("show", "c1").data["finding"]
    return [c["text"] for c in f["caveats"] if c["code"] == "source_postdates_run"]


def test_a_variants_setting_is_dated_against_that_variants_runs_only(rb):
    # fp32 ran before the int8 config was committed; every int8 run after it
    for seed in (1, 2, 3):
        rb("run", "add", "int8", f"fp32.top1=76.{seed}", "--set", f"seed={seed}", "--produced-at", "2026-05-05")
        rb("run", "add", "int8", f"int8.top1=75.{seed}", "--set", f"seed={seed}", "--produced-at", "2026-05-12")
    assert postdates(rb) == []
    # an int8 run before it is dated, and named
    early = rb("run", "add", "int8", "int8.top1=75.0", "--set", "seed=4", "--produced-at", "2026-05-03").data["object"]["id"]
    rb("run", "add", "int8", "fp32.top1=76.0", "--set", "seed=4", "--produced-at", "2026-05-03")
    [said] = postdates(rb)
    assert early in said


def test_a_setting_is_dated_only_against_the_runs_the_reading_counts(rb):
    rb("spec", "set", "int8", "eval.crop", "--per-run")
    cfg = rb.root / "early.yaml"
    cfg.write_text("eval:\n  crop: center\nseed: 9\n", encoding="utf-8")
    for seed in (1, 2, 3):
        rb("run", "add", "int8", f"fp32.top1=76.{seed}", f"int8.top1=75.{seed}", "--set", f"seed={seed}", "--set", "eval.crop=resize",
           "--produced-at", "2026-05-12")
    # a run before the config, whose own config disagrees with what it was given: not counted, so not what dates it
    rb("run", "add", "int8", "fp32.top1=76.0", "int8.top1=75.0", "--set", "seed=9", "--set", "eval.crop=resize", "--config", "early.yaml",
       "--produced-at", "2026-05-03")
    f = rb("show", "c1").data["finding"]
    assert any(not o["counted"] for o in f["observations"])
    assert postdates(rb) == []


def test_the_integrity_line_never_says_ok_above_a_run_on_uncommitted_code(rb):
    for seed in (1, 2, 3):
        cfg = rb.root / f"run{seed}.yaml"
        cfg.write_text(f"seed: {seed}\ndirty: {'true' if seed == 2 else 'false'}\n", encoding="utf-8")
        rb("run", "add", "int8", f"fp32.top1=76.{seed}", f"int8.top1=75.{seed}", "--set", f"seed={seed}", "--config", cfg.name,
           "--produced-at", "2026-05-12")
    code, out = rb("show", "c1", text=True)
    assert "integrity: ok" not in out
    assert "integrity: a run it counts ran on code with uncommitted changes, as said below" in out, out
