"""Smaller things a researcher meets: a name mistyped in --varies is taken back with rb retract. Every name and number is
synthetic: an INT8 ResNet-50 on ImageNet, recorded by human:ana."""
import subprocess

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


def test_a_name_varies_declared_with_no_setting_is_taken_back(rb):
    rb("experiment", "add", "INT8 against FP32", "--id", "int8", "--purpose", "the runs", "--baseline", "fp32", "--candidate", "int8",
       "--varies", "precison")
    code, out = rb("retract", "int8/precison", "--why", "a typo for precision", text=True)
    assert code == 0 and "precison is taken back from what int8 varies: a typo for precision. The log keeps it." in out
    assert rb("show", "int8").data["object"]["varies"] == []


def test_numbers_typed_with_no_time_are_given_again_with_their_per_run_values(rb):
    rb("experiment", "add", "INT8 against FP32", "--id", "int8", "--purpose", "the runs", "--baseline", "fp32", "--candidate", "int8",
       "--varies", "precision")
    rb("spec", "set", "int8", "seed", "--per-run")
    rb("criterion", "add", "INT8 loses at most 0.5 top1", "-e", "int8", "--metric", "change.top1", "--at-least", "-0.5", "--id", "c1")
    code, out = rb("run", "add", "int8", "fp32.top1=76.1", "int8.top1=75.6", "--set", "seed=1", text=True)
    hint = "rb run add int8 <the same numbers and per-run values> --produced-at <when> --replaces "
    assert code == 0 and hint in " ".join(out.split()), out
    late = [i for i in rb("status").data["open"] if i["code"] == "timing_unknown"]
    assert late and late[0]["do"].startswith(hint), late


def test_your_call_puts_the_newest_first_and_keeps_rb_status_s_order_where_they_tie(rb, monkeypatch):
    """The queue's order (OVERVIEW-V2): its fixed ranks, then the newest first, by the minute; calls whose evidence is
    of the same minute keep rb status's order, which is the record's order, then the ids."""
    import re
    from pathlib import Path

    def called(name):
        rb("page", "--out", name)
        page = Path(name).read_text(encoding="utf-8")
        section = page[page.index('id="your-call"'):]
        return list(dict.fromkeys(re.findall(r'data-for="call-([^"]+)"', section)))

    def at(clock):
        monkeypatch.setenv("RB_TEST_NOW", f"2026-10-01T{clock}.000+00:00")

    at("12:00:01")
    rb("experiment", "add", "INT8 against FP32", "--id", "int8", "--purpose", "the runs", "--baseline", "fp32", "--candidate", "int8")
    rb("experiment", "add", "Calibration with 512 images", "--id", "calib", "--purpose", "the runs", "--baseline", "c1024", "--candidate", "c512")
    for clock, cid, exp, ref, sub in (("12:00:05", "c-top1", "int8", "fp32", "int8"), ("12:00:06", "c-calib", "calib", "c1024", "c512")):
        at(clock)
        rb("criterion", "add", f"{sub} loses at most 0.2 top1", "-e", exp, "--metric", "top1", "--subject", sub, "--reference", ref,
           "--at-least", "-0.2", "--id", cid)
    at("12:00:07")
    rb("run", "add", "int8", "fp32.top1=76.1", "int8.top1=75.2")
    at("12:00:08")
    rb("run", "add", "calib", "c1024.top1=75.9", "c512.top1=75.4")
    waiting = lambda: [i["subject"] for i in rb("status").data["open"] if i["who"] == "human"]
    assert waiting() == ["c-top1", "c-calib"]
    assert called("tie.html") == ["c-top1", "c-calib"]
    at("12:02:10")
    rb("run", "add", "calib", "c1024.top1=75.7", "c512.top1=75.3")
    assert waiting() == ["c-top1", "c-calib"]
    assert called("calib.html") == ["c-calib", "c-top1"]
