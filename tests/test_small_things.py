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
