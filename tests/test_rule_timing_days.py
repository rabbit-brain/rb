"""MP-1112-2347 S1: the day a rule's results came in, on the page, is read on its subject's runs as the finding's timing
reads it: a rule declared after the baseline's runs and before the candidate's says its results came after it."""
import datetime
import json
import subprocess
from pathlib import Path

from rabbit_brain.models import Envelope
from rbtest import as_the_user, command, environment, main


def test_a_rule_written_before_its_subjects_runs_says_its_results_came_after_it(tmp_path, monkeypatch, capsys):
    as_the_user(monkeypatch)
    monkeypatch.setenv("RB_ACTOR", "human:ana")
    root = tmp_path / "int8"
    root.mkdir()
    monkeypatch.chdir(root)
    subprocess.run(["git", "init", "-q"], check=True)

    def rb(*argv):
        main([*argv, "--json"], typed=True)
        env = Envelope.model_validate_json(capsys.readouterr().out)
        assert env.ok, (argv, env.errors)
        return env

    rb("init", "INT8 ResNet-50", "--no-agents")
    rb("metric", "add", "top1", "--maximize")
    rb("experiment", "add", "INT8 against FP32", "--id", "e1", "--baseline", "fp32", "--candidate", "int8")
    rb("spec", "set", "e1", "seed", "--per-run")
    for seed in (1, 2, 3):
        rb("run", "add", "e1", "fp32.top1=76.1", "--set", f"seed={seed}", "--produced-at", "2026-05-01")
    rb("criterion", "add", "INT8 keeps top1 within a point", "-e", "e1", "--metric", "change.top1", "--at-least", "-1", "--id", "c1")
    Path("out").mkdir()
    for seed in (1, 2, 3):
        Path(f"out/int8-{seed}.json").write_text(json.dumps({"top1": 75.8 + seed / 10}), encoding="utf-8")
        rb("run", "add", "e1", "--from", f"out/int8-{seed}.json", "--variant", "int8", "--set", f"seed={seed}")
    timing = rb("show", "c1").data["finding"]["timing"]
    assert timing == "before", timing
    # S2: the card's WRITTEN says the two days in the order they came, the results' day read on int8's runs
    main(["show", "c1", "--json"], typed=True)
    card = json.loads(capsys.readouterr().out)["data"]["card"]
    written = next(f["value"] for f in card["fields"] if f["key"] == "written")
    assert written.startswith("\u2713 before its results") and "\u2500\u2500\u25b6 results" in written and "May" not in written, written
    dump = tmp_path / "doc.json"
    env = {**environment(), "RB_TEST_SHELL": "1", "RB_TEST_SHELL_DUMP": str(dump)}
    got = subprocess.run([*command(), "page", "--out", str(tmp_path / "p.html")], cwd=root, env=env, capture_output=True, text=True)
    assert got.returncode == 0, got.stderr
    when = json.loads(dump.read_text(encoding="utf-8"))["findings"]["c1"]["when"]
    today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    assert when["results"] == today and when["results"] >= when["rule"] and when["results"] != "2026-05-01", when
