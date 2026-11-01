"""Folders of one run each, with no report: rb reconcile reads each folder's own record of its run.

A folder per run, holding the run's config and its metrics, is the common layout. rb reconcile says what each config
says (the command, the commit, whether the code had uncommitted changes, when it started), as the file's words, and
the settings that differ between folders side by side, and gives the rb run add that records each with all the
folder gives. rb run add --config says when the config says the code had uncommitted changes. A command YAML folds
over two lines is the log line --command-source cites. Every name and number is synthetic: an INT8 ResNet-50 on
ImageNet."""
import json
import os
import shlex
import subprocess
from pathlib import Path

import pytest

from rabbit_brain.models import Envelope
from rbtest import as_the_user, main

GIT = ["git", "-c", "user.name=ana", "-c", "user.email=ana@example.org", "-c", "commit.gpgsign=false"]
COMMIT = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


def config(folder: str, prec: str, seed: int, crop: str, dirty: bool) -> str:
    # the command is long enough that YAML folds it onto a second line, as a run's own writer leaves it
    return (
        f"command: python eval.py --precision {prec} --seed {seed} --out results/{folder} --images\n"
        f"  50000 --batch-size 256\n"
        f"commit: {COMMIT}\n"
        f"dirty: {'true' if dirty else 'false'}\n"
        f"started: '2026-05-0{seed}T10:00:00Z'\n"
        f"precision: {prec}\n"
        f"seed: {seed}\n"
        f"out: results/{folder}\n"
        f"eval:\n"
        f"  crop: {crop}\n"
        f"  images: 50000\n"
    )


def build(root: Path) -> Path:
    runs = [("r01_fp32_s1", "fp32", 1, "center", False), ("r02_int8_s1", "int8", 1, "center", False), ("r03_int8_s2", "int8", 2, "resize", True)]
    for folder, prec, seed, crop, dirty in runs:
        d = root / "results" / folder
        d.mkdir(parents=True)
        (d / "config.yaml").write_text(config(folder, prec, seed, crop, dirty), encoding="utf-8")
        top1 = 76.1 if prec == "fp32" else 75.5 + seed / 10
        (d / "metrics.json").write_text(json.dumps({"top1": top1, "finished": f"2026-05-0{seed}T11:00:00Z"}, indent=2), encoding="utf-8")
        (d / "eval.log").write_text("done\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    # committed five hours after the last run finished, as a run's folder often is; the copy here is as new as a clone's
    when = {"GIT_AUTHOR_DATE": "2026-05-02T16:00:00Z", "GIT_COMMITTER_DATE": "2026-05-02T16:00:00Z"}
    subprocess.run([*GIT, "-C", str(root), "commit", "-q", "-m", "runs"], check=True, env={**os.environ, **when})
    return root


@pytest.fixture
def rb(tmp_path, monkeypatch, tmp_path_factory, capsys):
    as_the_user(monkeypatch)
    cfg = tmp_path_factory.mktemp("gitconfig") / "config"
    cfg.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(cfg))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("RB_ACTOR", "human:ana")
    root = build(tmp_path / "int8")
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
    return run


def test_each_run_folder_says_what_its_own_record_holds(rb):
    env = rb("reconcile")
    got = {r["folder"]: r for r in env.data["run_folders"]}
    assert set(got) == {"results/r01_fp32_s1/", "results/r02_int8_s1/", "results/r03_int8_s2/"}
    r3 = got["results/r03_int8_s2/"]
    assert r3["config"]["path"] == "results/r03_int8_s2/config.yaml"
    assert r3["config"]["command"]["value"].startswith("python eval.py --precision int8 --seed 2")
    assert r3["config"]["command"]["value"].endswith("--images 50000 --batch-size 256")
    assert r3["config"]["commit"]["value"] == COMMIT
    assert r3["config"]["dirty"] == {"value": True, "key": "dirty", "line": 4}
    assert r3["results"] == "results/r03_int8_s2/metrics.json"
    assert r3["produced_at"]["at"] == "2026-05-02T11:00:00Z"
    line = shlex.split(r3["line"])
    for flag, value in (("--config", "results/r03_int8_s2/config.yaml"), ("--commit", COMMIT), ("--produced-at", "2026-05-02T11:00:00Z"),
                        ("--command-source", "results/r03_int8_s2/config.yaml:1"), ("--from", "results/r03_int8_s2/metrics.json")):
        assert line[line.index(flag) + 1] == value
    assert line[line.index("--command") + 1] == r3["config"]["command"]["value"]


def test_the_settings_that_differ_between_folders_are_named(rb):
    env = rb("reconcile")
    differ = {d["key"]: {v["value"]: v["folders"] for v in d["values"]} for d in env.data["run_folder_differences"]}
    assert differ["eval.crop"] == {"center": ["r01_fp32_s1", "r02_int8_s1"], "resize": ["r03_int8_s2"]}
    assert set(differ["precision"]) == {"fp32", "int8"} and set(differ["seed"]) == {"1", "2"}
    assert "out" not in differ and "eval.images" not in differ          # its own path differs by construction; 50000 never does


def test_a_dirty_run_is_said_as_its_config_says_and_a_clean_folder_is_not_a_clean_run(rb):
    code, out = rb("reconcile", text=True)
    assert "results/r03_int8_s2/config.yaml:4 says dirty: true: the code had uncommitted changes when it ran" in out
    assert "eval.crop: center (r01_fp32_s1, r02_int8_s1), resize (r03_int8_s2)" in out
    assert "folder clean" not in out


def test_the_printed_line_records_a_run_with_its_command_logged_and_recorded_afterwards(rb):
    rb("init", "INT8 ResNet-50", "--no-agents")
    rb("experiment", "add", "INT8 against FP32", "--id", "int8", "--purpose", "the runs as the folders hold them", "--baseline", "fp32", "--candidate", "int8",
       "--varies", "precision")
    rb("spec", "set", "int8", "seed", "--per-run")
    env = rb("reconcile")
    line = next(r for r in env.data["run_folders"] if r["folder"] == "results/r03_int8_s2/")["line"]
    # the line names the experiment, the variant and the per-run value from the folder's config
    assert line.startswith("rb run add int8 --from results/r03_int8_s2/metrics.json --variant int8 --config ") and "--set seed=2" in line, line
    added = rb(*shlex.split(line)[1:])
    run = added.data["object"]
    assert run["receipt"]["produced"]["commit"] == COMMIT
    shown = rb("show", run["id"])
    assert shown.data["command"]["given"] == "logged"                  # the folded command is the log line it cites
    assert shown.data["made_before_recorded"] is True                  # recorded afterwards, as every run reconcile reads is
    assert "the code had uncommitted changes when it ran" in shown.data["config_dirty"]
    code, out = rb("show", run["id"], text=True)
    assert "results/r03_int8_s2/config.yaml:4 says dirty: true: the code had uncommitted changes when it ran" in out


def test_run_add_with_a_dirty_config_says_so_at_the_write(rb):
    rb("init", "INT8 ResNet-50", "--no-agents")
    rb("experiment", "add", "INT8 against FP32", "--id", "int8", "--purpose", "the runs", "--baseline", "fp32", "--candidate", "int8")
    code, out = rb("run", "add", "int8", "--from", "results/r03_int8_s2/metrics.json", "--variant", "int8", "--config", "results/r03_int8_s2/config.yaml",
                   text=True)
    assert code == 0, out
    assert "results/r03_int8_s2/config.yaml:4 says dirty: true: the code had uncommitted changes when it ran." in out
    code, out = rb("run", "add", "int8", "--from", "results/r01_fp32_s1/metrics.json", "--variant", "fp32", "--config", "results/r01_fp32_s1/config.yaml",
                   text=True)
    assert "uncommitted changes when it ran" not in out


def test_command_source_takes_a_command_yaml_folds_over_two_lines(rb):
    rb("init", "INT8 ResNet-50", "--no-agents")
    rb("experiment", "add", "INT8 against FP32", "--id", "int8", "--purpose", "the runs", "--baseline", "fp32", "--candidate", "int8")
    cmd = "python eval.py --precision fp32 --seed 1 --out results/r01_fp32_s1 --images 50000 --batch-size 256"
    env = rb("run", "add", "int8", "--from", "results/r01_fp32_s1/metrics.json", "--variant", "fp32", "--command", cmd,
             "--command-source", "results/r01_fp32_s1/config.yaml:1", "--produced-at", "2026-05-01T11:00:00Z")
    assert rb("show", env.data["object"]["id"]).data["command"]["given"] == "logged"
    bad = rb("run", "add", "int8", "--from", "results/r02_int8_s1/metrics.json", "--variant", "int8", "--command", cmd + " --extra",
             "--command-source", "results/r02_int8_s1/config.yaml:1", ok=False)
    assert not bad.ok and bad.errors[0].code == "E_SOURCE_UNRESOLVED"


def test_numbered_folders_side_by_side_in_two_groups_name_what_differs(tmp_path, monkeypatch, capsys):
    as_the_user(monkeypatch)
    root = tmp_path / "multirun"
    for group, opt in (("adamw", "adamw"), ("sgd", "sgd")):
        for i, lr in enumerate((0.01, 0.02)):
            d = root / "multirun" / group / str(i)
            d.mkdir(parents=True)
            (d / "config.yaml").write_text(f"command: python train.py lr={lr}\ncommit: 9ba0f86-dirty\noptim:\n  name: {opt}\n  lr: {lr}\n", encoding="utf-8")
            (d / "metrics.json").write_text(json.dumps({"top1": 75.0 + i}), encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    monkeypatch.chdir(root)
    main(["reconcile", "--json"], typed=True)
    data = json.loads(capsys.readouterr().out)["data"]
    differ = {d["key"]: {v["value"]: v["folders"] for v in d["values"]} for d in data["run_folder_differences"]}
    assert differ["optim.lr"] == {"0.01": ["adamw/0", "sgd/0"], "0.02": ["adamw/1", "sgd/1"]}
    assert differ["optim.name"] == {"adamw": ["adamw/0", "adamw/1"], "sgd": ["sgd/0", "sgd/1"]}
    one = data["run_folders"][0]["config"]
    assert one["commit"]["value"] == "9ba0f86" and one["dirty"]["value"] is True        # git describe --dirty


def test_an_argv_list_is_shown_and_never_offered_as_a_log_line(tmp_path, monkeypatch, capsys):
    as_the_user(monkeypatch)
    root = tmp_path / "argv"
    d = root / "results" / "r1"
    d.mkdir(parents=True)
    (d / "config.json").write_text(json.dumps({"argv": ["python", "eval.py", "--name", "int8 run"], "dirty": "yes"}, indent=2), encoding="utf-8")
    (d / "metrics.json").write_text(json.dumps({"top1": 75.6}), encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    monkeypatch.chdir(root)
    main(["reconcile", "--json"], typed=True)
    r = json.loads(capsys.readouterr().out)["data"]["run_folders"][0]
    assert "--command-source" not in r["line"] and "--command" not in r["line"]
    assert r["config"]["dirty"]["value"] is True and r["results"] == "results/r1/metrics.json"


def test_a_recorded_folder_says_its_run_and_a_dirty_run_reads_on_what_it_takes(rb):
    rb("init", "INT8 ResNet-50", "--no-agents")
    rb("experiment", "add", "INT8 against FP32", "--id", "int8", "--purpose", "the runs", "--baseline", "fp32", "--candidate", "int8")
    run = rb("run", "add", "int8", "--from", "results/r03_int8_s2/metrics.json", "--variant", "int8", "--config", "results/r03_int8_s2/config.yaml",
             "--produced-at", "2026-05-02T11:00:00Z").data["object"]["id"]
    folders = {r["folder"]: r for r in rb("reconcile").data["run_folders"]}
    assert folders["results/r03_int8_s2/"]["recorded_as"] == run and folders["results/r01_fp32_s1/"]["recorded_as"] is None
    code, out = rb("show", "int8", text=True)
    assert "config says the code had uncommitted changes when it ran" in out
    Path(rb.root, "results/r03_int8_s2/config.yaml").write_text("command: edited\n", encoding="utf-8")
    code, out = rb("show", run, text=True)
    assert "results/r03_int8_s2/config.yaml changed since the run was recorded" in out and "uncommitted changes when it ran" not in out


def test_the_printed_line_pasted_as_it_is_records_the_run_on_its_variant(rb):
    """The config's value of what the experiment varies (precision: int8) names the variant; --keys takes only the numbers
    the experiment reads, so a setting in the results file is no metric; --set gives each per-run value. Pasted as it
    is, the line records a run its criterion reads."""
    rb("init", "INT8 ResNet-50", "--no-agents")
    rb("metric", "add", "top1", "--maximize")
    rb("experiment", "add", "INT8 against FP32", "--id", "int8", "--purpose", "the runs", "--baseline", "fp32", "--candidate", "int8",
       "--varies", "precision")
    rb("spec", "set", "int8", "seed", "--per-run")
    crit = rb("criterion", "add", "INT8 keeps top-1 within a point", "-e", "int8", "--metric", "change.top1", "--at-least", "-1").data["object"]["id"]
    Path(rb.root, "results/r02_int8_s1/metrics.json").write_text(json.dumps({"top1": 75.6, "seed": 1, "finished": "2026-05-01T11:00:00Z"}), encoding="utf-8")
    lines = {r["folder"]: r["line"] for r in rb("reconcile").data["run_folders"]}
    line = lines["results/r02_int8_s1/"]
    assert line.startswith("rb run add int8 --from results/r02_int8_s1/metrics.json --variant int8 --keys top1 --config ") and "--set seed=1" in line, line
    run = rb(*shlex.split(line)[1:]).data["object"]
    assert run["metrics"] == {"int8.top1": 75.6} and run["per_run"] == {"seed": 1}
    rb(*shlex.split(lines["results/r01_fp32_s1/"])[1:])
    assert rb("show", crit).data["finding"]["rule_outcome"] != "untested"


def test_a_run_that_names_no_variant_is_refused_where_variants_are_compared(rb):
    rb("init", "INT8 ResNet-50", "--no-agents")
    rb("experiment", "add", "INT8 against FP32", "--id", "int8", "--purpose", "the runs", "--baseline", "fp32", "--candidate", "int8")
    rb("criterion", "add", "INT8 keeps top-1 within a point", "-e", "int8", "--metric", "change.top1", "--at-least", "-1")
    env = rb("run", "add", "int8", "--from", "results/r02_int8_s1/metrics.json", ok=False)
    assert not env.ok and env.errors[0].code == "E_OBJECT_INVALID", env.errors
    assert "int8 compares fp32, int8, and these numbers name no variant (top1)" in env.errors[0].message
    assert "--variant <fp32|int8>" in env.errors[0].fix
    assert not list(Path(rb.root, ".rb/experiments/int8/runs").glob("*.yaml")) if Path(rb.root, ".rb/experiments/int8/runs").exists() else True
    # a criterion that names its arms cannot read a number that names none: still refused
    rb("criterion", "add", "INT8 top-1 against FP32", "-e", "int8", "--metric", "top1", "--subject", "int8", "--reference", "fp32", "--at-least", "-1")
    assert not rb("run", "add", "int8", "--from", "results/r02_int8_s1/metrics.json", ok=False).ok
    # a criterion that reads the number as given, with no arm, reads it
    rb("criterion", "add", "Top-1 stays above 70", "-e", "int8", "--metric", "top1", "--at-least", "70")
    assert rb("run", "add", "int8", "--from", "results/r02_int8_s1/metrics.json").ok


def test_a_dirty_run_reaches_the_rules_that_read_it_on_the_terminal_and_the_page(rb):
    import html, re
    rb("init", "INT8 ResNet-50", "--no-agents")
    rb("experiment", "add", "INT8 against FP32", "--id", "int8", "--purpose", "the runs", "--baseline", "fp32", "--candidate", "int8")
    crit = rb("criterion", "add", "INT8 keeps top-1 within a point", "-e", "int8", "--metric", "change.top1", "--at-least", "-1").data["object"]["id"]
    run = rb("run", "add", "int8", "--from", "results/r03_int8_s2/metrics.json", "--variant", "int8", "--config", "results/r03_int8_s2/config.yaml",
             "--produced-at", "2026-05-02T11:00:00Z").data["object"]["id"]
    rb("run", "add", "int8", "--from", "results/r01_fp32_s1/metrics.json", "--variant", "fp32", "--config", "results/r01_fp32_s1/config.yaml",
       "--produced-at", "2026-05-01T11:00:00Z")
    said = f"{run}: results/r03_int8_s2/config.yaml:4 says dirty: true: the code had uncommitted changes when it ran"
    notes = {c["code"]: c["text"] for c in rb("show", crit).data["finding"]["caveats"]}
    assert notes.get("uncommitted_code") == said, notes
    code, out = rb("show", crit, text=True)
    assert said in out
    rb("page", "--out", "page.html")
    page = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", Path(rb.root, "page.html").read_text(encoding="utf-8"))).split())
    assert said.split(": ", 1)[1] in page                               # the page names the run by its label
    # a card with notes leaves its checks to them: never "checks ok" above its own warnings
    assert "checks ok" not in page, page[page.find("checks"):][:300]


def test_the_line_never_guesses_a_variant_or_a_nested_key_and_quotes_a_described_commit(rb):
    """Two config keys that end in the varied setting's name (teacher.precision, student.precision) name no one variant;
    a nested number (eval.top1) is recorded under its own name, so it is no --keys of the line; a commit git describe
    --dirty wrote is quoted as the file gives it."""
    rb("init", "INT8 ResNet-50", "--no-agents")
    rb("metric", "add", "top1", "--maximize")
    rb("experiment", "add", "INT8 against FP32", "--id", "int8", "--purpose", "the runs", "--baseline", "fp32", "--candidate", "int8",
       "--varies", "precision")
    d = Path(rb.root, "results/r09_distill")
    d.mkdir(parents=True)
    (d / "config.yaml").write_text("command: python distill.py\ncommit: 4b825dc-dirty\nteacher:\n  precision: fp32\nstudent:\n  precision: int8\n",
                                   encoding="utf-8")
    (d / "metrics.json").write_text(json.dumps({"eval": {"top1": 75.2}}), encoding="utf-8")
    line = {r["folder"]: r["line"] for r in rb("reconcile").data["run_folders"]}["results/r09_distill/"]
    assert "--variant" not in line and "--keys" not in line, line
    code, out = rb("reconcile", text=True)
    assert "results/r09_distill/config.yaml:2 says commit: 4b825dc-dirty: the code had uncommitted changes when it ran" in out, out


def test_what_reconcile_proposes_is_what_rb_run_add_records(rb):
    """A flat results file names no variant of a two-arm experiment whose criterion names its arm: rb run add refuses it,
    so reconcile proposes no such line; a criterion on a dotted name that is no variant (eval.top1) reads it as given."""
    rb("init", "INT8 ResNet-50", "--no-agents")
    rb("experiment", "add", "INT8 against FP32", "--id", "int8", "--purpose", "the runs", "--baseline", "fp32", "--candidate", "int8")
    rb("criterion", "add", "INT8 top-1 above 70", "-e", "int8", "--metric", "top1", "--subject", "int8", "--at-least", "70")
    Path(rb.root, "out/r1").mkdir(parents=True)
    Path(rb.root, "out/r1/metrics.json").write_text(json.dumps({"top1": 74.9}), encoding="utf-8")
    data = rb("reconcile").data
    assert not any("out/r1/metrics.json" in json.dumps(p) and "rb run add int8 --from out/r1/metrics.json" in json.dumps(p) for p in data.get("found", [])), data.get("found")
    rb("experiment", "add", "INT8 nested", "--id", "nested", "--purpose", "the runs", "--baseline", "fp32", "--candidate", "int8")
    rb("criterion", "add", "Eval top-1 above 70", "-e", "nested", "--metric", "eval.top1", "--at-least", "70")
    Path(rb.root, "nested.json").write_text(json.dumps({"eval": {"top1": 75.1}}), encoding="utf-8")
    assert rb("run", "add", "nested", "--from", "nested.json").data["object"]["metrics"] == {"eval.top1": 75.1}


def test_a_described_dirty_commit_is_said_once_as_the_file_gives_it(rb):
    d = Path(rb.root, "results/r08_desc")
    d.mkdir(parents=True)
    (d / "config.yaml").write_text("command: python eval.py\ncommit: 4b825dc-dirty\nseed: 1\n", encoding="utf-8")
    (d / "metrics.json").write_text(json.dumps({"top1": 75.2}), encoding="utf-8")
    code, out = rb("reconcile", text=True)
    assert "commit: 4b825dc-dirty (line 2)" in out and "commit: true" not in out, out
