"""rb reconcile finds results where a repository keeps them, not only in the usual folders at the top.

A folder named for results (reports/, eval/, metrics/, artifacts/ and the like) down to four levels from the top is
where results land, as results/ is. The search goes into no dot-folder, dependency folder, virtualenv, build folder at
the top, folder of code, tests or configs, or folder git ignores; a folder it passes that holds results-like files is
named with why it is not read. A project's package manifest is no result. Every name and number is synthetic: an INT8
ResNet-50 evaluated on ImageNet."""
import json
import subprocess
import time
from pathlib import Path

import pytest

from rabbit_brain.models import Envelope
from rbtest import as_the_user, main

GIT = ["git", "-c", "user.name=ana", "-c", "user.email=ana@example.org", "-c", "commit.gpgsign=false"]


def write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def build(root: Path) -> Path:
    """A repository that keeps its evaluations under reports/ and a sweep's metrics under artifacts/."""
    root.mkdir()
    write(root, "README.md", "# INT8 ResNet-50\n\nQuantized ResNet-50 on ImageNet.\n")
    write(root, "pyproject.toml", "[project]\nname = \"int8-resnet\"\n\n[tool.ruff]\nline-length = 120\n")
    for prec, top1 in (("fp32", 76.1), ("int8", 75.6)):
        for seed in (1, 2):
            write(root, f"reports/stage1/eval/{prec}_s{seed}.json", json.dumps({"top1": top1 + seed / 100, "top5": 92.9}))
    write(root, "reports/stage1/eval/table.md", "| precision | top1 |\n| --- | --- |\n| fp32 | 76.1 |\n")
    write(root, "artifacts/sweep/metrics/summary.csv", "precision,top1\nfp32,76.1\nint8,75.6\n")
    write(root, "data/imagenet_val/labels.json", json.dumps({"n": 50000}))
    write(root, "src/int8_resnet/metrics/__init__.py", "def top1(): ...\n")
    write(root, "src/int8_resnet/metrics/defaults.json", json.dumps({"k": 5}))
    write(root, "configs/eval/int8.yaml", "precision: int8\n")
    write(root, "node_modules/pkg/results/out.json", json.dumps({"top1": 1.0}))
    write(root, ".venv/lib/results/out.json", json.dumps({"top1": 1.0}))
    write(root, "scratch/notes.json", json.dumps({"top1": 75.0}))
    write(root, ".gitignore", "scratch/\n")
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    subprocess.run([*GIT, "-C", str(root), "commit", "-q", "-m", "evals"], check=True)
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

    def run(*argv, text=False):
        if text:
            code = main(list(argv), typed=True)
            return code, capsys.readouterr().out
        code = main([*argv, "--json"], typed=True)
        env = Envelope.model_validate_json(capsys.readouterr().out)
        assert code in (0, 1) and env.ok, (argv, code, env.errors)
        return env

    run.root = root
    return run


def test_folders_named_for_results_below_the_top_are_read_as_results_folders(rb):
    env = rb("reconcile")
    folders = {f["folder"] for f in env.data["repository"]["folders"]}
    assert folders == {"reports/stage1/eval/", "artifacts/sweep/metrics/"}
    assert {"reports/", "artifacts/"} <= set(env.data["looked_in"])
    # nothing from a dependency folder, a virtualenv, code or configs
    assert not any(f.startswith(("node_modules", ".venv", "src/", "configs/")) for f in env.data["looked_in"])


def test_what_the_search_passed_and_does_not_read_is_named_with_why(rb):
    env = rb("reconcile")
    not_read = {n["folder"]: n for n in env.data["not_read"]}
    assert not_read["data/imagenet_val/"]["why"] == "its name does not say it holds results"
    assert not_read["data/imagenet_val/"]["files"] == 1
    assert "scratch/" not in not_read                     # what git ignores is a cache or a dataset as often as not
    assert not any(k.startswith(("src/", "configs/", "node_modules", ".venv")) for k in not_read)
    code, out = rb("reconcile", text=True)
    assert "Not read (" in out and "data/imagenet_val/ (1 .json, .jsonl, .csv or .tsv file): its name does not say it holds results" in out
    assert "rb compile data/imagenet_val/ reads it" in out


def test_a_project_manifest_is_no_result(rb):
    rb("init", "INT8 ResNet-50", "--no-agents")
    code, out = rb("reconcile", text=True)
    assert "pyproject.toml" not in out
    env = rb("reconcile")
    assert not any("pyproject.toml" in u for u in env.data["unplaced"])


def test_the_journal_looks_in_folders_named_for_results(rb):
    rb("init", "INT8 ResNet-50", "--no-agents")
    env = rb("status", "--full")
    scope = env.data["scope"]
    assert "reports/" in scope["folders"] and "artifacts/" in scope["folders"]
    assert "reports/" in scope["said"] and "artifacts/" in scope["said"]


def test_the_usual_folders_alone_are_said_as_before(tmp_path, monkeypatch, capsys):
    as_the_user(monkeypatch)
    monkeypatch.setenv("RB_ACTOR", "human:ana")
    root = tmp_path / "plain"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    monkeypatch.chdir(root)
    assert main(["init", "INT8 ResNet-50", "--no-agents", "--json"], typed=True) == 0
    capsys.readouterr()
    main(["status", "--json"], typed=True)
    scope = json.loads(capsys.readouterr().out)["data"]["scope"]
    assert scope["folders"] == ["out/", "outputs/", "results/", "eval-out/", "metrics/"]


def test_a_report_holding_a_long_table_of_numbers_is_read_quickly(tmp_path, monkeypatch, capsys):
    as_the_user(monkeypatch)
    root = tmp_path / "table"
    rows = "".join(f"| imagenet:val | int8 | {i} | 0.{i:018d} | 0.{i * 7:018d} | -0.{i * 3:018d} |\n" for i in range(2500))
    write(root, "results/int8_sweep/summary_table.md", "| set | mode | step | mean | std | low |\n| --- | --- | --- | --- | --- | --- |\n" + rows)
    write(root, "results/int8_sweep/summary.csv", "mode,top1\nint8,75.6\n")
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    monkeypatch.chdir(root)
    started = time.monotonic()
    main(["reconcile", "--json"], typed=True)
    took = time.monotonic() - started
    env = json.loads(capsys.readouterr().out)
    assert env["ok"]
    assert took < 15, took


def test_the_search_finds_results_deep_in_a_folder_named_for_them_and_passes_over_code_and_crowds(tmp_path, monkeypatch, capsys):
    as_the_user(monkeypatch)
    root = tmp_path / "layouts"
    write(root, "artifacts/sweep1/run3/metrics/top1.json", json.dumps({"top1": 75.6}))       # three levels below artifacts/
    write(root, "work/stage2/eval/int8.json", json.dumps({"top1": 75.6}))                  # a found folder that holds them itself
    write(root, "cache/results/fp32/m.json", json.dumps({"top1": 76.1}))                   # under a folder git ignores
    write(root, ".gitignore", "cache/\n")
    write(root, "Results/run1/metrics.json", json.dumps({"top1": 75.0}))                   # the usual name in another case
    write(root, "int8_resnet/metrics/__init__.py", "")                                     # a package named metrics
    write(root, "int8_resnet/metrics/defaults.json", json.dumps({"k": 5}))
    write(root, "env/conda-meta/history.json", json.dumps({}))                              # a conda environment
    for i in range(320):                                                                   # a dataset's class folders
        (root / "imagenet" / f"n{i:08d}").mkdir(parents=True)
    write(root, "zz/eval/m.json", json.dumps({"top1": 75.6}))
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    monkeypatch.chdir(root)
    main(["reconcile", "--json"], typed=True)
    data = json.loads(capsys.readouterr().out)["data"]
    folders = {f["folder"] for f in data["repository"]["folders"]}
    assert folders == {"artifacts/sweep1/run3/metrics/", "work/stage2/eval/", "cache/results/fp32/", "Results/run1/", "zz/eval/"}
    not_read = {n["folder"]: n for n in data["not_read"]}
    assert not_read["imagenet/"]["why"] == "it holds too many folders to look in" and not_read["imagenet/"]["folders"] == 320
    assert not any(k.startswith(("int8_resnet", "env")) for k in list(not_read) + data["looked_in"])


def test_a_folder_a_run_reads_is_not_listed_as_not_read(tmp_path, monkeypatch, capsys):
    as_the_user(monkeypatch)
    monkeypatch.setenv("RB_ACTOR", "human:ana")
    root = tmp_path / "audit"
    write(root, "docs/assets/int8-audit/status.json", json.dumps({"max_error": 0.004}))
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    monkeypatch.chdir(root)
    main(["reconcile", "--json"], typed=True)
    assert "docs/assets/int8-audit/" in {n["folder"] for n in json.loads(capsys.readouterr().out)["data"].get("not_read", [])}
    main(["init", "INT8 ResNet-50", "--no-agents"], typed=True)
    main(["experiment", "add", "INT8 audit", "--id", "audit", "--purpose", "the audit's numbers"], typed=True)
    assert main(["run", "add", "audit", "--from", "docs/assets/int8-audit/status.json"], typed=True) == 0
    capsys.readouterr()
    main(["reconcile", "--json"], typed=True)
    data = json.loads(capsys.readouterr().out)["data"]
    assert "docs/assets/int8-audit/" not in {n["folder"] for n in data.get("not_read", [])}


def test_many_results_folders_are_named_by_every_place_they_are(tmp_path, monkeypatch, capsys):
    """34 folders were named as three and "31 more": every place they are is named, folders side by side by the folder
    they sit in."""
    as_the_user(monkeypatch)
    root = tmp_path / "sweeps"
    for k in range(5):
        write(root, f"artifacts/int8_sweep/run{k}/metrics.json", json.dumps({"top1": 75.0 + k / 10}))
    write(root, "artifacts/fp32_baseline/metrics/top1.json", json.dumps({"top1": 76.1}))
    write(root, "results/calibration/metrics.json", json.dumps({"top1": 75.4}))
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    monkeypatch.chdir(root)
    main(["reconcile"], typed=True)
    out = " ".join(capsys.readouterr().out.split())
    assert "7 results folders hold results files nothing records yet, in " in out, out
    for place in ("artifacts/int8_sweep/ (5 folders)", "artifacts/fp32_baseline/metrics/", "results/calibration/"):
        assert place in out, (place, out)


def test_loose_folders_at_the_top_are_each_named(tmp_path, monkeypatch, capsys):
    as_the_user(monkeypatch)
    root = tmp_path / "top"
    for name in ("eval", "evals", "reports", "benchmarks"):
        write(root, f"{name}/top1.json", json.dumps({"top1": 75.6}))
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    monkeypatch.chdir(root)
    main(["reconcile"], typed=True)
    out = " ".join(capsys.readouterr().out.split())
    assert "./ (" not in out and all(f"{n}/" in out for n in ("eval", "evals", "reports", "benchmarks")), out


def test_a_folder_that_holds_only_a_tool_s_config_is_no_results_folder(rb):
    write(rb.root, "results/page/package.json", json.dumps({"name": "int8-page", "version": "1.0.0", "top1": 75.6}))
    write(rb.root, "reports/site/pyproject.toml", "[project]\nname = \"int8-site\"\n")
    rb("init", "INT8 ResNet-50", "--no-agents")
    env = rb("reconcile")
    folders = {f["folder"] for f in env.data["repository"]["folders"]}
    assert folders == {"reports/stage1/eval/", "artifacts/sweep/metrics/"}
    assert not any("package.json" in u or "pyproject.toml" in u for u in env.data["unplaced"])
    code, out = rb("reconcile", text=True)
    assert "results/page/" not in out and "reports/site/" not in out


def test_a_run_s_file_named_for_a_lock_keeps_its_folder_a_results_folder(rb):
    write(rb.root, "results/lock/eval_lock.json", json.dumps({"top1": 75.6, "top5": 92.6}))
    write(rb.root, "results/site/package-lock.json", json.dumps({"lockfileVersion": 3, "top1": 75.6}))
    rb("init", "INT8 ResNet-50", "--no-agents")
    env = rb("reconcile")
    folders = {f["folder"] for f in env.data["repository"]["folders"]}
    assert "results/lock/" in folders and "results/site/" not in folders
