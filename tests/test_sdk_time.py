"""The SDK's time: numbers exp.record() is given outside a run block, with no produced_at, were made at a time nobody
gave rb, so every reading says their time is unknown, as rb show does; a run block is seen to end, and its numbers are
dated then. Every name and number is synthetic: an INT8 ResNet-50 on ImageNet, recorded by human:ana."""
import subprocess

import rabbit_brain as rb
from rbtest import as_the_user


def project(tmp_path, monkeypatch):
    as_the_user(monkeypatch)
    monkeypatch.setenv("RB_ACTOR", "human:ana")
    root = tmp_path / "int8"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    monkeypatch.chdir(root)
    state = rb.init("INT8 ResNet-50")
    exp = state.add_experiment("INT8 against FP32", purpose="the runs", baseline="fp32", candidates=["int8"])
    exp.spec.set("seed", per_run=True)
    crit = exp.criterion("INT8 loses at most a point", metric="change.top1", at_least=-1)
    return state, exp, crit


def test_numbers_recorded_outside_a_run_block_with_no_time_read_as_of_unknown_time(tmp_path, monkeypatch):
    state, exp, crit = project(tmp_path, monkeypatch)
    run = exp.record({"fp32.top1": 76.1, "int8.top1": 75.6}, per_run={"seed": 1})
    assert run.produced_at is None and run.produced_basis == "unknown"
    f = crit.finding()
    assert f.timing == "unknown", f.says
    # given a time, they read as of that time
    exp.record({"fp32.top1": 76.0, "int8.top1": 75.5}, per_run={"seed": 2}, produced_at="2030-01-01T00:00:00Z")


def test_a_run_blocks_numbers_are_dated_as_it_ended(tmp_path, monkeypatch):
    state, exp, crit = project(tmp_path, monkeypatch)
    with exp.run(seed=1) as run:
        run.log({"fp32.top1": 76.1, "int8.top1": 75.6})
    assert run.record.produced_basis == "recorded"
    assert crit.finding().timing == "before"


def test_an_attached_file_gives_untimed_numbers_no_time(tmp_path, monkeypatch):
    state, exp, crit = project(tmp_path, monkeypatch)
    (tmp_path / "int8" / "note.txt").write_text("an unrelated attachment\n", encoding="utf-8")
    run = exp.record({"fp32.top1": 76.1, "int8.top1": 75.6}, per_run={"seed": 1}, files=["note.txt"])
    assert run.produced_basis == "unknown" and crit.finding().timing == "unknown"


def test_an_earlier_rbs_run_block_keeps_its_time(tmp_path, monkeypatch):
    """An earlier rb's run block kept no mark of the block, but the script's command line it records: its time stands."""
    import json
    state, exp, crit = project(tmp_path, monkeypatch)
    run = exp.record({"fp32.top1": 76.1, "int8.top1": 75.6}, per_run={"seed": 1}, command="python eval.py --seed 1")
    assert run.produced_basis == "unknown"   # this rb: a bare record, a command given or not, is of unknown time
    with exp.run(seed=2) as r:
        r.log({"fp32.top1": 76.0, "int8.top1": 75.5})
    path = next((tmp_path / "int8" / ".rb" / "experiments").rglob(f"{r.record.id}.yaml"))
    assert "block: true" in path.read_text(encoding="utf-8")
