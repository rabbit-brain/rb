"""Writes that count toward nothing: a number that names no variant on an experiment of one variant is that variant's;
on one of more, the refusal names the metrics only, and the variant its run config names; rb show lists a run no rule
counts, with why. Every name and number is synthetic: an INT8 ResNet-50 on ImageNet, recorded by human:ana."""
import json
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
    run("metric", "add", "top1", "--maximize")
    return run


def test_a_flat_number_on_an_experiment_of_one_variant_is_that_variants(rb):
    rb("experiment", "add", "INT8 alone", "--id", "solo", "--purpose", "a level", "--baseline", "int8")
    rb("criterion", "add", "INT8 holds 75", "-e", "solo", "--metric", "top1", "--subject", "int8", "--at-least", "75", "--id", "c1")
    env = rb("run", "add", "solo", "top1=75.6", "--produced-at", "2026-05-01")
    assert not [w for w in env.data.get("warnings", []) if "toward nothing" in w]
    assert env.data["object"]["metrics"] == {"int8.top1": 75.6}
    st = rb("status").data
    assert not [o for o in st["open"] if o["code"] == "unread_metric"]
    f = rb("show", "c1").data["finding"]
    assert f["rule_outcome"] == "meets" and [o["counted"] for o in f["observations"]] == [True]


def test_a_rule_that_names_no_arm_reads_the_one_variants_number_and_says_so(rb):
    rb("experiment", "add", "INT8 alone", "--id", "solo", "--purpose", "a level", "--baseline", "int8")
    rb("criterion", "add", "INT8 holds 75", "-e", "solo", "--metric", "top1", "--at-least", "75", "--id", "c1")
    env = rb("run", "add", "solo", "top1=75.6", "--produced-at", "2026-05-01")
    assert env.data["object"]["metrics"] == {"int8.top1": 75.6}
    assert not [w for w in env.data.get("warnings", []) if "toward nothing" in w], env.data.get("warnings")
    st = rb("status").data
    assert not [o for o in st["open"] if o["code"] == "unread_metric"], [o["what"] for o in st["open"] if o["code"] == "unread_metric"]
    f = rb("show", "c1").data["finding"]
    assert f["rule_outcome"] == "meets" and [o["counted"] for o in f["observations"]] == [True]
    # a number of another name still counts toward nothing, and the line names the rule's name once
    env = rb("run", "add", "solo", "latency_ms=4.1", "--produced-at", "2026-05-02")
    said = [w for w in env.data.get("warnings", []) if "toward nothing" in w]
    assert said and "Its criteria read top1:" in said[0], said


def test_rb_show_names_an_arms_number_as_the_rule_does(rb):
    rb("metric", "add", "eval.top1", "--maximize")
    rb("experiment", "add", "INT8 vs FP32", "--id", "e1", "--purpose", "the cost", "--baseline", "fp32", "--candidate", "int8", "--varies", "precision")
    rb("criterion", "add", "INT8 loses at most a point", "-e", "e1", "--metric", "change.eval.top1", "--at-least", "-1", "--id", "c1")
    rb("run", "add", "e1", "fp32.eval.top1=76.1", "int8.eval.top1=75.6", "--produced-at", "2026-05-01")
    rb("run", "add", "e1", "eval.top1=76.0", "--variant", "fp32", "--produced-at", "2026-05-02")
    code, out = rb("show", "c1", text=True)
    assert "fp32.eval.top1 76" in out, out
    assert "fp32.top1" not in out, out


def test_rb_show_lists_a_run_no_rule_counts_with_why(rb):
    rb("experiment", "add", "INT8 alone", "--id", "solo", "--purpose", "a level", "--baseline", "int8")
    rb("criterion", "add", "INT8 holds 75", "-e", "solo", "--metric", "top1", "--subject", "int8", "--at-least", "75", "--id", "c1")
    rid = rb("run", "add", "solo", "latency_ms=4.1", "--produced-at", "2026-05-01").data["object"]["id"]
    code, out = rb("show", "c1", text=True)
    assert f"{rid} is on solo and counts toward no rule: it reports int8.latency_ms, and this rule reads int8.top1" in out, out


def test_a_refusal_names_the_metrics_only_and_the_variant_its_config_names(rb):
    rb("experiment", "add", "Calibration", "--id", "cal", "--purpose", "the sweep", "--baseline", "fp32", "--candidate", "int8", "--varies", "precision")
    rb("spec", "set", "cal", "seed", "--per-run")
    for v in ("fp32", "int8"):
        rb("spec", "set", "cal", f"{v}.precision", v, "--no-verify")
    rb("criterion", "add", "INT8 loses at most a point", "-e", "cal", "--metric", "change.top1", "--at-least", "-1")
    (rb.root / "out").mkdir()
    (rb.root / "out" / "metrics.json").write_text(json.dumps({"seed": 1, "top1": 75.6}), encoding="utf-8")
    (rb.root / "out" / "config.yaml").write_text("seed: 1\nprecision: int8\n", encoding="utf-8")
    env = rb("run", "add", "cal", "--from", "out/metrics.json", "--set", "seed=1", ok=False)
    err = env.errors[0]
    assert "name no variant (top1)" in err.message and "seed" not in err.message and "<variant>.seed" not in err.fix
    env = rb("run", "add", "cal", "--from", "out/metrics.json", "--set", "seed=1", "--config", "out/config.yaml", ok=False)
    assert "out/config.yaml, names int8 by what cal varies: --variant int8 takes them as int8's" in env.errors[0].fix


def test_a_row_that_names_two_variants_is_refused_as_ambiguous(rb):
    rb("experiment", "add", "Calibration", "--id", "cal", "--purpose", "the sweep", "--baseline", "fp32", "--candidate", "int8", "--varies", "bits")
    for v in ("fp32", "int8"):
        rb("spec", "set", "cal", f"{v}.bits", "8", "--no-verify")
    rb("criterion", "add", "INT8 loses at most a point", "-e", "cal", "--metric", "change.top1", "--at-least", "-1")
    (rb.root / "sweep.csv").write_text("bits,top1\n8,75.6\n", encoding="utf-8")
    env = rb("run", "add", "cal", "--from", "sweep.csv", "--where", "bits=8", ok=False)
    assert "the row names fp32 and int8 by what cal varies, so rb cannot tell which variant they are for" in env.errors[0].message


def test_a_run_folder_line_with_no_metric_to_name_leaves_its_settings_out(rb):
    rb("experiment", "add", "Calibration", "--id", "cal", "--purpose", "the sweep", "--baseline", "int8")
    rb("spec", "set", "cal", "seed", "--per-run")
    for seed in (1, 2):
        d = rb.root / "results" / f"r0{seed}"
        d.mkdir(parents=True)
        (d / "metrics.json").write_text(json.dumps({"seed": seed, "acc": 75.0 + seed}), encoding="utf-8")
        (d / "config.yaml").write_text(f"command: python eval.py --seed {seed}\nseed: {seed}\nlr: 0.{seed}\n", encoding="utf-8")
    env = rb("reconcile")
    lines = [f["line"] for f in env.data["run_folders"]]
    assert lines and all("--keys acc" in l for l in lines), lines


def test_a_run_no_rule_counts_says_what_a_rule_with_no_arm_reads(rb):
    rb("experiment", "add", "INT8 alone", "--id", "solo", "--purpose", "a level", "--baseline", "int8")
    rb("criterion", "add", "top1 holds 75", "-e", "solo", "--metric", "top1", "--at-least", "75", "--id", "c7")
    rb("run", "add", "solo", "top1=75.6", "--produced-at", "2026-05-01")
    rid = rb("run", "add", "solo", "loss=0.3", "--produced-at", "2026-05-01").data["object"]["id"]
    code, out = rb("show", "c7", text=True)
    assert "meets criterion" in out and f"{rid} is on solo and counts toward no rule: it reports int8.loss, and this rule reads top1" in out, out


def _nothing(env):
    return [w for w in env.data.get("warnings", []) if "toward nothing" in w]


def test_on_one_variant_the_advice_names_the_number_as_the_rule_does(rb):
    """On an experiment of one variant, top1 and int8.top1 are the same number. The line a run no rule counts gets gives
    it as the rule names it: int8.top1 where the rule says int8.top1, top1 where it says top1, as rb show does."""
    rb("experiment", "add", "INT8 alone", "--id", "named", "--purpose", "a level", "--baseline", "int8")
    rb("criterion", "add", "INT8 holds 75", "-e", "named", "--metric", "int8.top1", "--at-least", "75", "--id", "c1")
    said = _nothing(rb("run", "add", "named", "latency_ms=4.1", "--produced-at", "2026-05-01"))
    assert said and "Its criteria read int8.top1: a run gives them as int8.top1=<value>." in said[0], said
    rb.root.joinpath("m.json").write_text(json.dumps({"latency_ms": 4.2}), encoding="utf-8")
    said = _nothing(rb("run", "add", "named", "--from", "m.json", "--produced-at", "2026-05-01"))
    assert said and 'Its criteria read int8.top1: a file gives them as {"int8": {"top1": ...}}.' in said[0], said
    # spelled out with --subject, the rule names int8's top1
    rb("experiment", "add", "INT8 alone, spelled out", "--id", "spelled", "--purpose", "a level", "--baseline", "int8")
    rb("criterion", "add", "INT8 holds 75", "-e", "spelled", "--metric", "top1", "--subject", "int8", "--at-least", "75", "--id", "c2")
    said = _nothing(rb("run", "add", "spelled", "latency_ms=4.1", "--produced-at", "2026-05-01"))
    assert said and "Its criteria read int8.top1:" in said[0], said
    # the bare name where the rule says the bare name
    rb("experiment", "add", "INT8 alone, bare", "--id", "bare", "--purpose", "a level", "--baseline", "int8")
    rb("criterion", "add", "top1 holds 75", "-e", "bare", "--metric", "top1", "--at-least", "75", "--id", "c3")
    said = _nothing(rb("run", "add", "bare", "latency_ms=4.1", "--produced-at", "2026-05-01"))
    assert said and "Its criteria read top1: a run gives them as top1=<value>." in said[0], said
    # both spellings are still read
    for exp, crit in (("named", "c1"), ("bare", "c3")):
        rb("run", "add", exp, "top1=75.6", "--produced-at", "2026-05-02")
        rb("run", "add", exp, "int8.top1=75.8", "--produced-at", "2026-05-03")
        assert [o["counted"] for o in rb("show", crit).data["finding"]["observations"]] == [True, True]


def test_with_the_baseline_retracted_the_advice_names_the_candidate_as_the_rule_does(rb):
    rb("experiment", "add", "INT8 vs FP32", "--id", "e1", "--baseline", "fp32", "--candidate", "int8", "--varies", "precision")
    rb("criterion", "add", "INT8 holds 75", "-e", "e1", "--metric", "int8.top1", "--at-least", "75", "--id", "c1")
    rb("retract", "e1/fp32", "--why", "FP32 is measured elsewhere")
    said = _nothing(rb("run", "add", "e1", "latency_ms=4.1", "--produced-at", "2026-05-01"))
    assert said and "Its criteria read int8.top1: a run gives them as int8.top1=<value>." in said[0], said
    # the reference arm's too: with the candidate retracted, a change rule names fp32's number as fp32.top1
    rb("experiment", "add", "INT8 vs FP32, INT8 dropped", "--id", "e2", "--baseline", "fp32", "--candidate", "int8", "--varies", "precision")
    rb("criterion", "add", "INT8 loses at most 1 point", "-e", "e2", "--metric", "change.top1", "--at-least", "-1", "--id", "c2")
    rb("retract", "e2/int8", "--why", "INT8 is measured elsewhere")
    said = _nothing(rb("run", "add", "e2", "latency_ms=4.1", "--produced-at", "2026-05-01"))
    assert said and "fp32.top1" in said[0] and " top1" not in said[0].split("Its criteria read")[1], said


def test_the_sdk_and_mcp_name_the_number_as_the_rule_does(rb):
    import warnings

    import rabbit_brain
    from rbtest import Mcp, environment
    rb("experiment", "add", "INT8 alone", "--id", "named", "--purpose", "a level", "--baseline", "int8")
    rb("criterion", "add", "INT8 holds 75", "-e", "named", "--metric", "int8.top1", "--at-least", "75", "--id", "c1")
    exp = rabbit_brain.open().experiment("named")
    with warnings.catch_warnings(record=True) as told:
        warnings.simplefilter("always")
        with exp.run() as run:
            run.log({"latency_ms": 4.1})
    said = [str(w.message) for w in told if issubclass(w.category, rabbit_brain.RBWarning) and "toward nothing" in str(w.message)]
    assert said and 'Its criteria read int8.top1: a run logs them as run.log({"int8.top1": ...})' in said[0], said
    server = Mcp(env={**environment(), "RB_SESSION": "s-one"})
    try:
        server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "clientInfo": {"name": "one"}}})
        got = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                             "params": {"name": "run_add", "arguments": {"experiment": "named", "metrics": {"latency_ms": 4.3}, "produced_at": "2026-05-01"}}})
    finally:
        server.close()
    data = got["result"]["structuredContent"]["data"]
    said = [w for w in data.get("warnings", []) if "toward nothing" in w]
    assert said and "Its criteria read int8.top1: a run gives them as int8.top1=<value>." in said[0], (said, got["result"]["structuredContent"].get("errors"))
