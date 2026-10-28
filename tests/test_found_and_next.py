"""What rb finds in the repository, and the next step it suggests for it.

rb looks for results in one list of folders, the one every read names. A results folder is read by an experiment once a
recorded run of that experiment named a file in it; new numbers in it, and numbers a command run under rb journal run
prints, are proposed as rb run add lines on that experiment, never as new experiments. Played on the README's own
layout (INT8 ResNet-50 on ImageNet, each seed's metrics and resolved config in out/seed<N>/) as human:ana."""
import json
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from rabbit_brain.models import Envelope
from rbtest import as_the_user, main

HAS_GIT = shutil.which("git") is not None
pytestmark = pytest.mark.skipif(not HAS_GIT, reason="the repository is played in git")

USUAL = ["out/", "outputs/", "results/", "eval-out/", "metrics/"]
LOSSES = {1: (0.961, 0.987), 2: (0.958, 0.985), 3: (0.964, 0.989), 4: (0.960, 0.986)}


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout.strip()


def seed_files(n: int) -> None:
    """What eval.py leaves for seed n: its metrics and its resolved config, in out/seed<n>/."""
    fp32, int8 = LOSSES[n]
    Path(f"out/seed{n}").mkdir(parents=True, exist_ok=True)
    Path(f"out/seed{n}/metrics.json").write_text(json.dumps({"fp32": {"val_loss": fp32}, "int8": {"val_loss": int8}}), encoding="utf-8")
    Path(f"out/seed{n}/config.yaml").write_text(f"seed: {n}\noptim:\n  lr: 0.1\ndata:\n  dataset: imagenet\n", encoding="utf-8")


@pytest.fixture
def rb(workdir, monkeypatch, tmp_path_factory, capsys):
    """rb as human:ana, in a git repository with Ana's identity: the envelope, or with text=True what rb printed."""
    as_the_user(monkeypatch)
    cfg = tmp_path_factory.mktemp("gitconfig") / "config"
    cfg.write_text("[user]\n\temail = ana@example.org\n\tname = Ana\n\tuseConfigOnly = true\n[commit]\n\tgpgsign = false\n", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(cfg))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("RB_ACTOR", "human:ana")
    _git("init", "-q")

    def run(*argv, text=False, ok=True, agent=None):
        """As human:ana, or in the made-up agent session named `agent`."""
        if agent:
            monkeypatch.delenv("RB_ACTOR", raising=False)
            monkeypatch.setenv("CLAUDECODE", "1")
            monkeypatch.setenv("RB_SESSION", agent)
            monkeypatch.setenv("RB_MODEL", "test-model")
        else:
            for name in ("CLAUDECODE", "RB_SESSION", "RB_MODEL"):
                monkeypatch.delenv(name, raising=False)
            monkeypatch.setenv("RB_ACTOR", "human:ana")
        args = list(argv)
        if not text:
            args.insert(args.index("--") if "--" in args else len(args), "--json")
        code = main(args)
        out = capsys.readouterr().out
        if text:
            return out
        env = Envelope.model_validate_json(out)
        if ok:
            assert env.ok and code in (0, 1), (argv, env.errors)
        return env

    return run


def test_one_list_of_where_rb_looks_for_every_surface(rb):
    """rb status said it looks in five folders while rb reconcile read two more: a runs/ or experiments/ folder was
    listed as results no recorded experiment reads yet, under a line that never named it. Every surface uses one list."""
    rb("init", "INT8 ResNet-50")
    for folder in ("runs/a", "experiments/b", "out/c"):
        Path(folder).mkdir(parents=True)
        Path(f"{folder}/metrics.json").write_text('{"val_loss": 0.97}', encoding="utf-8")
    st = rb("status")
    assert st.data["scope"]["folders"] == USUAL
    assert st.data["repository"]["unindexed"] == ["out/c/"]
    rec = rb("reconcile")
    assert rec.data["looked_in"] == USUAL and [f["folder"] for f in rec.data["repository"]["folders"]] == ["out/c/"]


# ---------------------------------------------------------------- a results folder a recorded run named is read

def readme_layout(rb, seeds=(1, 2, 3)) -> None:
    """README.md's quick start, as written, with each seed's metrics and resolved config in out/seed<N>/."""
    Path("configs").mkdir()
    Path("configs/train.yaml").write_text("optim:\n  lr: 0.1\ndata:\n  dataset: imagenet\n", encoding="utf-8")
    Path("configs/fp32.yaml").write_text("precision: fp32\n", encoding="utf-8")
    Path("configs/int8.yaml").write_text("precision: int8\n", encoding="utf-8")
    _git("add", "-A")
    _git("commit", "-qm", "configs")
    rb("init", "INT8 ResNet-50")
    rb("question", "add", "Is INT8 ResNet-50 as good as FP32 on ImageNet?", "--id", "q-imagenet")
    rb("experiment", "add", "INT8 vs FP32", "--id", "int8", "-q", "q-imagenet", "--hypothesis", "INT8 keeps validation loss within 0.05 of FP32",
       "--baseline", "fp32", "--candidate", "int8", "--varies", "precision")
    rb("spec", "set", "int8", "--from", "configs/train.yaml", "--keys", "optim.*,data.*")
    rb("spec", "set", "int8", "fp32.precision", "fp32", "--source", "configs/fp32.yaml#precision")
    rb("spec", "set", "int8", "int8.precision", "int8", "--source", "configs/int8.yaml#precision")
    rb("spec", "set", "int8", "seed", "--per-run")
    rb("metric", "add", "val_loss", "--unit", "nats", "--minimize")
    rb("criterion", "add", "INT8 adds at most 0.05 validation loss", "-e", "int8", "-q", "q-imagenet", "--metric", "change.val_loss",
       "--at-most", "0.05", "--min-n", "3", "--when-met", "INT8 stays within the declared accuracy budget on ImageNet.")
    rb("freeze", "int8", "-m", "before the held-out runs")
    for n in seeds:
        seed_files(n)
        rb("run", "add", "int8", "--from", f"out/seed{n}/metrics.json", "--set", f"seed={n}", "--config", f"out/seed{n}/config.yaml")


def experiments(rb) -> list:
    return [e["id"] for e in rb("status").data["experiments"]]


def test_the_readmes_own_layout_reads_its_results_folders_and_suggests_no_apply(rb):
    """After the README's quick start, rb status said out/seed1/ held results no experiment reads yet and suggested
    rb reconcile --apply, which added one empty experiment per seed. The runs were recorded from those folders: each is
    read by int8, nothing is unindexed, and nothing rb suggests adds an experiment."""
    readme_layout(rb)
    st = rb("status")
    assert st.data["repository"]["unindexed"] == [] and not any(i["code"] == "unindexed_folder" for i in st.data["open"])
    assert not any("--apply" in n for n in st.next)
    assert not any("--apply" in n for n in rb("context").next)
    rec = rb("reconcile")
    assert [(f["folder"], f["read_by"], f["unplaced"]) for f in rec.data["repository"]["folders"]] == [(f"out/seed{n}/", ["int8"], False) for n in (1, 2, 3)]
    assert rec.next == [] and "experiments to add" not in rb("reconcile", text=True)
    assert rb("reconcile", "--apply").data["applied"]["added"] == [] and experiments(rb) == ["int8"]


def test_a_new_number_in_a_folder_a_run_read_is_another_run_of_its_experiment(rb):
    """A file that lands in out/seed1/, which int8's run was recorded from, is proposed as a run of int8: the folder's
    numbers go to the experiment that reads it, never to a new one. int8 declares seed per-run: the line gives the seed
    the run's own config beside the file states, with that config, so the rb reconcile --apply its Next names records
    it (it recorded nothing and said the line needs --set seed=<value>)."""
    readme_layout(rb)
    rb("status")                                                             # rb looks: what lands after it is new
    Path("out/seed1/rerun.json").write_text(json.dumps({"fp32": {"val_loss": 0.962}, "int8": {"val_loss": 0.988}}), encoding="utf-8")
    rec = rb("reconcile")
    argv = ["run", "add", "int8", "--from", "out/seed1/rerun.json", "--set", "seed=1", "--config", "out/seed1/config.yaml"]
    assert [(f["experiment"], f["argv"]) for f in rec.data["found"]] == [("int8", argv)]
    assert rec.next[0] == "rb reconcile --apply"                            # all it would do is that rb run add
    applied = rb("reconcile", "--apply")
    assert applied.data["not_recorded"] == [] and len(applied.data["recorded"]) == 1 and experiments(rb) == ["int8"]
    assert rb("status").data["found_unrecorded"] == 0 and rb("reconcile").next == []


def test_older_files_beside_a_runs_file_are_the_work_before_the_record(rb):
    """A project under way, recorded as the start guide says: each seed's run from results/seed<N>/metrics.json, with
    an earlier metrics_epoch3.json beside it. rb reconcile proposed each epoch-3 file as another run of int8, and the
    rb reconcile --apply its Next named recorded them, turning the criterion from met to not met. A file there when rb
    first looked, or one a checkout brought, is listed with the line that records it and never proposed; one that lands
    after rb's first look is a new run."""
    import os
    import time
    old = time.mktime((2026, 9, 1, 12, 0, 0, 0, 0, -1))
    for n in (1, 2, 3):
        folder = Path(f"results/seed{n}")
        folder.mkdir(parents=True)
        (folder / "metrics.json").write_text(json.dumps({"fp32": {"val_loss": 0.96 + n / 1000}, "int8": {"val_loss": 0.98 + n / 1000}}), encoding="utf-8")
        (folder / "metrics_epoch3.json").write_text(json.dumps({"fp32": {"val_loss": 1.23}, "int8": {"val_loss": 1.33}}), encoding="utf-8")
        (folder / "config.yaml").write_text(f"seed: {n}\n", encoding="utf-8")
        for f in folder.iterdir():
            os.utime(f, (old, old))
    _git("add", "-A")
    _git("commit", "-qm", "three seeds")
    rb("init", "INT8 ResNet-50")
    rb("experiment", "add", "INT8 vs FP32", "--id", "int8", "--baseline", "fp32", "--candidate", "int8", "--varies", "precision", agent="s-start")
    rb("spec", "set", "int8", "seed", "--per-run", agent="s-start")
    rb("metric", "add", "val_loss", "--unit", "nats", "--minimize", agent="s-start")
    rb("criterion", "add", "INT8 adds at most 0.05 validation loss", "-e", "int8", "--metric", "change.val_loss", "--at-most", "0.05", "--min-n", "3",
       "--id", "c-loss", agent="s-start")
    for n in (1, 2, 3):
        rb("run", "add", "int8", "--from", f"results/seed{n}/metrics.json", "--config", f"results/seed{n}/config.yaml", "--set", f"seed={n}",
           "--produced-at", "2026-09-01", agent="s-start")
    reading = lambda: rb("status").data["findings"][0]["says"]["rule"]  # noqa: E731
    before = reading()
    assert before.startswith("criterion met"), before
    for env in (rb("reconcile"), rb("reconcile", agent="s-start")):
        assert env.data["found"] == [] and not any("--apply" in n for n in env.next), (env.data["found"], env.next)
    unplaced = rb("reconcile").data["unplaced"]
    assert ("results/seed3/metrics_epoch3.json was there when rb first looked here, in results/seed3/, which int8 reads, so rb cannot tell it "
            "from the work before the record and proposes no run of it: rb run add int8 --from results/seed3/metrics_epoch3.json --set seed=3 "
            "--config results/seed3/config.yaml records it, if it is one") in unplaced, unplaced
    assert not any("--apply" in n for n in rb("status").next + rb("context").next)
    assert rb("reconcile", "--apply").data["recorded"] == [] and reading() == before
    base = _git("rev-parse", "--abbrev-ref", "HEAD")
    _git("checkout", "-qb", "rerun")                                         # a seed rerun committed on a branch, and checked out
    _git("rm", "-q", "results/seed2/metrics_epoch3.json")
    Path("results/seed2/rerun.json").write_text(json.dumps({"fp32": {"val_loss": 0.958}, "int8": {"val_loss": 1.2}}), encoding="utf-8")
    _git("add", "results")                                                   # the record stays out of git, in the checkout
    _git("commit", "-qm", "seed 2 again")
    _git("checkout", "-q", base)
    rb("status")
    _git("checkout", "-q", "rerun")
    assert rb("reconcile").data["found"] == [] and any(u.startswith("results/seed2/rerun.json came with a checkout, pull or merge, in results/seed2/") for u in rb("reconcile").data["unplaced"])
    Path("results/seed1/rerun.json").write_text(json.dumps({"fp32": {"val_loss": 0.961}, "int8": {"val_loss": 0.99}}), encoding="utf-8")
    assert [f["argv"] for f in rb("reconcile").data["found"]] == [
        ["run", "add", "int8", "--from", "results/seed1/rerun.json", "--set", "seed=1", "--config", "results/seed1/config.yaml"]]


def test_a_results_file_written_and_committed_here_is_a_new_run(rb):
    """rb said a file it first saw in the look that saw HEAD move came with a checkout, pull or merge, and never proposed
    it, even where the move was a commit made in this checkout of the result just written. A commit made here brings
    nothing: its file was written here, after rb's first look, and is proposed as a new run."""
    readme_layout(rb)
    rb("status")
    Path("out/seed1/rerun.json").write_text(json.dumps({"fp32": {"val_loss": 0.962}, "int8": {"val_loss": 0.988}}), encoding="utf-8")
    _git("add", "out")
    _git("commit", "-qm", "the three seeds and a rerun of seed 1")
    rec = rb("reconcile")
    assert [f["path"] for f in rec.data["found"]] == ["out/seed1/rerun.json"], rec.data["unplaced"]


def _under_way(rb) -> None:
    """A project under way, recorded as the start guide says: three seeds in results/seed<N>/, each with an epoch-3 file
    beside it, two sweep folders with their configs, all committed and written two days before rb init; a
    re-evaluation of seed 2 stashed before it; then the protocol's declarations and the three runs."""
    import os
    import time
    old = time.time() - 2 * 86400
    for n in (1, 2, 3):
        folder = Path(f"results/seed{n}")
        folder.mkdir(parents=True)
        (folder / "metrics.json").write_text(json.dumps({"fp32": {"val_loss": 0.96 + n / 1000}, "int8": {"val_loss": 0.98 + n / 1000}}), encoding="utf-8")
        (folder / "metrics_epoch3.json").write_text(json.dumps({"fp32": {"val_loss": 1.2 + n / 100}, "int8": {"val_loss": 1.3 + n / 100}}), encoding="utf-8")
        (folder / "config.yaml").write_text(f"seed: {n}\n", encoding="utf-8")
    for k in (1, 2):
        folder = Path(f"results/sweep-{k}")
        folder.mkdir(parents=True)
        (folder / "metrics.json").write_text(json.dumps({"fp32": {"val_loss": 0.95 + k / 1e5}, "int8": {"val_loss": 0.96 + k / 1e5}}), encoding="utf-8")
        (folder / "config.yaml").write_text(f"seed: {100 + k}\n", encoding="utf-8")
    final = Path("results/seed3/metrics.json").read_text(encoding="utf-8")   # seed 3's first try, committed, then its rerun
    Path("results/seed3/metrics.json").write_text(json.dumps({"fp32": {"val_loss": 0.97}, "int8": {"val_loss": 1.1}}), encoding="utf-8")
    _git("add", "-A")
    _git("commit", "-qm", "seed 3, first try")
    Path("results/seed3/metrics.json").write_text(final, encoding="utf-8")
    for f in Path("results").rglob("*.*"):
        os.utime(f, (old, old))
    _git("add", "-A")
    _git("commit", "-qm", "three seeds and a sweep")
    Path("results/seed2/metrics.json").write_text(json.dumps({"fp32": {"val_loss": 0.9701}, "int8": {"val_loss": 1.0402}}), encoding="utf-8")
    _git("stash", "push", "-q", "-m", "seed 2 again", "--", "results/seed2/metrics.json")
    rb("init", "INT8 ResNet-50")
    rb("question", "add", "Is INT8 ResNet-50 as good as FP32 on ImageNet?", "--id", "q-imagenet", agent="s-start")
    rb("experiment", "add", "INT8 vs FP32", "--id", "int8", "-q", "q-imagenet", "--baseline", "fp32", "--candidate", "int8", "--varies", "precision",
       agent="s-start")
    rb("spec", "set", "int8", "seed", "--per-run", agent="s-start")
    rb("metric", "add", "val_loss", "--unit", "nats", "--minimize", agent="s-start")
    rb("criterion", "add", "INT8 adds at most 0.05 validation loss", "-e", "int8", "-q", "q-imagenet", "--metric", "change.val_loss", "--at-most", "0.05",
       "--min-n", "3", "--id", "c-loss", agent="s-start")
    for n in (1, 2, 3):
        rb("run", "add", "int8", "--from", f"results/seed{n}/metrics.json", "--config", f"results/seed{n}/config.yaml", "--set", f"seed={n}",
           agent="s-start")


def test_older_results_moved_copied_or_put_back_by_git_are_never_new_runs(rb):
    """After the start protocol, a renamed older results folder, one copied with its times, a copy of an older file made
    without them, an older file renamed in a folder a run read, and a re-evaluation stashed before the record began and
    popped after it were each proposed as a new run, and the rb reconcile --apply its Next named recorded them, changing
    the criterion's reading; so was an earlier commit's version checked out over a file. Bytes rb saw first at another
    path (moved: first seen when they were; still there: a copy), bytes git already kept, and a file last written
    before rb's first look are listed with the line that records them, never proposed; a file written now is still a
    new run, added to git or not."""
    import shutil
    _under_way(rb)
    reading = lambda: rb("status").data["findings"][0]["says"]["rule"]  # noqa: E731
    before = reading()
    shutil.move("results/sweep-1", "results/sweep-1-lr01")
    shutil.copytree("results/sweep-2", "results/sweep-2-copy")               # cp -Rp: the times come too
    shutil.copyfile("results/seed1/metrics_epoch3.json", "results/seed1/metrics_epoch3_copy.json")
    shutil.move("results/seed3/metrics_epoch3.json", "results/seed3/epoch3.json")
    _git("stash", "pop", "-q")
    _git("checkout", "HEAD~1", "--", "results/seed3/metrics.json")         # the first try, back from git
    for env in (rb("reconcile"), rb("reconcile", agent="s-start")):
        assert env.data["found"] == [], env.data["found"]
    unplaced = rb("reconcile").data["unplaced"]
    for line in (
        "results/seed1/metrics_epoch3_copy.json holds the same bytes as results/seed1/metrics_epoch3.json, in results/seed1/, which int8 reads, so rb "
        "reads it as a copy and proposes no run of it: rb run add int8 --from results/seed1/metrics_epoch3_copy.json --set seed=1 --config "
        "results/seed1/config.yaml records it, if it is a run of its own",
        "results/seed3/epoch3.json was there when rb first looked here, as results/seed3/metrics_epoch3.json, in results/seed3/, which int8 reads, so rb "
        "cannot tell it from the work before the record and proposes no run of it: rb run add int8 --from results/seed3/epoch3.json --set seed=3 "
        "--config results/seed3/config.yaml records it, if it is one",
        "results/seed2/metrics.json holds bytes git already kept, in results/seed2/, which int8 reads, so rb cannot tell it from the work before the "
        "record and proposes no run of it: rb run add int8 --from results/seed2/metrics.json --set seed=2 --config results/seed2/config.yaml records it, "
        "if it is one",
        "results/seed3/metrics.json holds bytes git already kept, in results/seed3/, which int8 reads, so rb cannot tell it from the work before the "
        "record and proposes no run of it: rb run add int8 --from results/seed3/metrics.json --set seed=3 --config results/seed3/config.yaml records it, "
        "if it is one",
    ):
        assert line in unplaced, unplaced
    [older] = [i for i in rb("status").data["open"] if i["code"] == "unindexed_folder"]
    assert older["later"] and sorted(older["folders"]) == ["results/sweep-1-lr01/", "results/sweep-2-copy/", "results/sweep-2/"], older
    assert rb("reconcile", "--apply").data["recorded"] == [] and reading() == before
    # a result written now, added to git before rb looks, then committed: new bytes, and a run
    Path("results/seed1/rerun.json").write_text(json.dumps({"fp32": {"val_loss": 0.962}, "int8": {"val_loss": 0.988}}), encoding="utf-8")
    _git("add", "results/seed1/rerun.json")
    assert [f["path"] for f in rb("reconcile").data["found"]] == ["results/seed1/rerun.json"]
    _git("commit", "-qm", "seed 1 again")
    assert [f["path"] for f in rb("reconcile").data["found"]] == ["results/seed1/rerun.json"]


def _epoch10_and_seed4() -> list:
    """Two results that land after the start protocol: seed 1 at epoch 10 beside its run, and seed 4 in a folder of its
    own, with its config."""
    Path("results/seed1/metrics_epoch10.json").write_text(json.dumps({"fp32": {"val_loss": 0.9511}, "int8": {"val_loss": 0.9733}}), encoding="utf-8")
    Path("results/seed4").mkdir()
    Path("results/seed4/metrics.json").write_text(json.dumps({"fp32": {"val_loss": 0.9611}, "int8": {"val_loss": 0.9833}}), encoding="utf-8")
    Path("results/seed4/config.yaml").write_text("seed: 4\n", encoding="utf-8")
    return [["run", "add", "int8", "--from", "results/seed1/metrics_epoch10.json", "--set", "seed=1", "--config", "results/seed1/config.yaml"],
            ["run", "add", "int8", "--from", "results/seed4/metrics.json", "--set", "seed=4", "--config", "results/seed4/config.yaml"]]


def test_a_result_rb_saw_land_is_still_a_new_run_after_a_stash_and_a_look(rb):
    """A results file that landed after the start protocol, which rb saw land and proposed, was never proposed again
    after git stash -u, a look (rb open and rb ui look every 10 seconds) and git stash pop: rb forgot the bytes once no
    file held them, read them back as bytes git already kept, and rb status counted it as work before the record. So
    went a new seed's folder. Bytes rb saw keep the look that first saw them, wherever they come back: these are still
    proposed, a file there at rb's first look that comes back is still the work before the record, and a re-evaluation
    stashed before the record began is still never proposed."""
    _under_way(rb)
    runs = _epoch10_and_seed4()
    found = lambda: sorted(f["argv"] for f in rb("reconcile").data["found"])  # noqa: E731
    assert found() == runs
    _git("stash", "push", "-q", "-u", "-m", "epoch 10 and seed 4", "--", "results/seed1/metrics_epoch10.json", "results/seed4")
    rb("journal", "scan")
    _git("stash", "pop", "-q")
    assert found() == runs, rb("reconcile").data["unplaced"]
    said = rb("status", text=True)
    assert "rb reconcile lists them, with the rb run add for each of the 2 runs; 3 results files were there when rb first looked here" in said, said
    # a file there at rb's first look, gone for a look and written back
    epoch3 = Path("results/seed3/metrics_epoch3.json")
    kept = epoch3.read_bytes()
    epoch3.unlink()
    rb("journal", "scan")
    epoch3.write_bytes(kept)
    assert ("results/seed3/metrics_epoch3.json was there when rb first looked here, in results/seed3/, which int8 reads, so rb cannot tell it from "
            "the work before the record and proposes no run of it: rb run add int8 --from results/seed3/metrics_epoch3.json --set seed=3 --config "
            "results/seed3/config.yaml records it, if it is one") in rb("reconcile").data["unplaced"]
    _git("stash", "pop", "-q")                                               # seed 2's re-evaluation, from before the record
    assert found() == runs
    assert any(u.startswith("results/seed2/metrics.json holds bytes git already kept") for u in rb("reconcile").data["unplaced"])


def test_a_result_rb_saw_land_is_still_a_new_run_after_its_branch_is_left_and_checked_out_again(rb):
    """A result rb saw land, committed on a branch, read as brought by the checkout once the branch was left for a look
    and checked out again, and was never proposed again. Bytes rb saw keep the look that first saw them, a checkout's
    too; a result first seen as a checkout brings it is still never proposed."""
    _under_way(rb)
    _git("stash", "drop", "-q")
    runs = _epoch10_and_seed4()
    found = lambda: sorted(f["argv"] for f in rb("reconcile").data["found"])  # noqa: E731
    assert found() == runs
    base = _git("rev-parse", "--abbrev-ref", "HEAD")
    _git("checkout", "-qb", "epoch10")
    _git("add", "results/seed1/metrics_epoch10.json", "results/seed4")
    _git("commit", "-qm", "seed 1 at epoch 10, and seed 4")
    _git("checkout", "-q", base)
    rb("journal", "scan")
    _git("checkout", "-q", "epoch10")
    assert found() == runs, rb("reconcile").data["unplaced"]
    _git("checkout", "-qb", "epoch20", base)                                 # a result rb never saw, committed on a branch
    Path("results/seed2/metrics_epoch20.json").write_text(json.dumps({"fp32": {"val_loss": 0.9411}, "int8": {"val_loss": 0.9633}}), encoding="utf-8")
    _git("add", "results/seed2/metrics_epoch20.json")
    _git("commit", "-qm", "seed 2 at epoch 20")
    _git("checkout", "-q", "epoch10")
    rb("journal", "scan")
    _git("merge", "-q", "--no-edit", "epoch20")
    assert found() == runs
    assert any(u.startswith("results/seed2/metrics_epoch20.json came with a checkout, pull or merge") for u in rb("reconcile").data["unplaced"])


def test_an_untracked_config_there_before_the_record_is_no_change_after_a_stash_and_a_look(rb):
    """An untracked config there when the record began, put away with git stash -u for a look and popped, was listed
    as new since the record's commit, though nothing had changed it: rb forgot what it first saw of the file once it was
    gone. One back keeps what rb first saw there: no change with the bytes it had, changed with others."""
    Path("configs").mkdir()
    Path("configs/train.yaml").write_text("optim:\n  lr: 0.1\ndata:\n  dataset: imagenet\n", encoding="utf-8")
    _git("add", "-A")
    _git("commit", "-qm", "configs")
    base = _git("rev-parse", "HEAD")
    local = Path("configs/local.yaml")
    local.write_text("data:\n  batch_size: 128\n", encoding="utf-8")
    rb("init", "INT8 ResNet-50")
    said = lambda: [u["said"] for u in rb("status").data["uncited_changes"]]  # noqa: E731
    assert said() == []
    for _ in range(2):
        _git("stash", "push", "-q", "-u", "--", "configs/local.yaml")
        rb("journal", "scan")
        _git("stash", "pop", "-q")
        assert said() == []
    local.unlink()
    rb("journal", "scan")
    local.write_text("data:\n  batch_size: 64\n", encoding="utf-8")
    assert said() == [f"configs/local.yaml: changed since {base[:7]}, not committed; nothing in the record cites it"]


def test_a_run_an_earlier_rb_journaled_owns_only_what_it_wrote_while_it_ran(rb):
    """A record an earlier rb kept, upgraded in place: that rb's entry for `python eval.py --seed 4` listed, beside the
    files it wrote, a sweep folder's results and config from before the record. This rb proposed that sweep result as a
    run of the watched eval, with its seed and that command's commit, and --apply recorded it. Of such a list only a
    file last written while the command ran is its; the rest is read as any file is."""
    _under_way(rb)
    _git("stash", "drop", "-q")
    Path("eval.py").write_text(
        "import json, os, sys\nseed = int(sys.argv[sys.argv.index('--seed') + 1])\nos.makedirs(f'results/seed{seed}')\n"
        "json.dump({'fp32': {'val_loss': 0.961}, 'int8': {'val_loss': 0.99}}, open(f'results/seed{seed}/metrics.json', 'w'))\n"
        "open(f'results/seed{seed}/config.yaml', 'w').write(f'seed: {seed}\\n')\n", encoding="utf-8")
    _git("add", "eval.py")
    _git("commit", "-qm", "the eval")
    rb("journal", "run", "--", sys.executable, "eval.py", "--seed", "4", agent="s-start")
    journal = Path(_git("rev-parse", "--git-common-dir")) / "rb" / "journal" / "main" / "journal.jsonl"
    rows = [json.loads(line) for line in journal.read_text(encoding="utf-8").splitlines()]
    [ran] = [r for r in rows if r.get("op") == "run"]
    assert ran["wrote"] == ["results/seed4/config.yaml", "results/seed4/metrics.json"] and ran.get("wrote_checked") is True
    del ran["wrote_checked"]                                                 # as the earlier rb wrote it
    ran["wrote"] += ["results/sweep-2/config.yaml", "results/sweep-2/metrics.json"]
    journal.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    rec = rb("reconcile")
    commit = _git("rev-parse", "HEAD")
    assert [f["argv"] for f in rec.data["found"]] == [
        ["run", "add", "int8", "--from", "results/seed4/metrics.json", "--set", "seed=4", "--config", "results/seed4/config.yaml", "--commit", commit]]
    [applied] = rb("reconcile", "--apply").data["recorded"]
    assert applied.split(": ", 1)[1].startswith("rb run add int8 --from results/seed4/metrics.json --set seed=4 "), applied
    assert rb("reconcile").data["found"] == []


def test_status_counts_older_files_by_why_rb_cannot_tell_them_new(rb):
    """rb status said a results file a merge brought "was there when rb first looked here", as it says of the files
    that were. It says each kind in its own words, and what they share where they differ."""
    import os
    import time
    readme_layout(rb)
    _git("add", "out", "configs")
    _git("commit", "-qm", "three seeds")
    base = _git("rev-parse", "--abbrev-ref", "HEAD")
    _git("checkout", "-qb", "epoch5")
    Path("out/seed1/metrics_epoch5.json").write_text(json.dumps({"fp32": {"val_loss": 0.97}, "int8": {"val_loss": 0.995}}), encoding="utf-8")
    _git("add", "out")
    _git("commit", "-qm", "seed 1 at epoch 5")
    _git("checkout", "-q", base)
    rb("status")
    _git("merge", "-q", "epoch5")
    said = "Found in the repository, not in the record (1): 1 results file came with a checkout, pull or merge, which rb reconcile lists and proposes no run of"
    assert said in rb("status", text=True)
    Path("out/seed2/eval_old.json").write_text(json.dumps({"fp32": {"val_loss": 0.959}, "int8": {"val_loss": 0.984}}), encoding="utf-8")
    old = time.time() - 2 * 86400
    os.utime("out/seed2/eval_old.json", (old, old))
    said = "Found in the repository, not in the record (2): 2 results files rb cannot tell from the work before the record, which rb reconcile lists and proposes no run of"
    assert said in rb("status", text=True)


def test_under_a_fixed_clock_a_file_written_after_rb_init_is_still_a_new_run(rb, monkeypatch):
    """Under the golden harness's fixed clock (RB_TEST_NOW), whose steps date the files they write an hour before it, rb
    read every results file written after rb init as last written before its first look, so the stored scenarios
    proposed no run at all. A fixed clock does not move: there a file's time is read against rb's first look only past
    the cap."""
    import calendar
    import os
    monkeypatch.setenv("RB_TEST_NOW", "2026-10-01T12:00:00.000+00:00")
    fixed = calendar.timegm((2026, 10, 1, 12, 0, 0))
    readme_layout(rb)
    Path("out/seed1/rerun.json").write_text(json.dumps({"fp32": {"val_loss": 0.962}, "int8": {"val_loss": 0.988}}), encoding="utf-8")
    os.utime("out/seed1/rerun.json", (fixed - 3600 + 30, fixed - 3600 + 30))
    assert [f["path"] for f in rb("reconcile").data["found"]] == ["out/seed1/rerun.json"]


def test_a_folder_two_experiments_runs_read_places_a_new_number_in_neither(rb):
    """Where the runs of two experiments named files in one folder, and both read a new file's numbers, the file is
    listed as unplaced with both, never given to one of them."""
    readme_layout(rb, seeds=(1,))
    rb("experiment", "add", "FP16 vs FP32", "--id", "fp16", "-q", "q-imagenet", "--baseline", "fp32", "--candidate", "fp16", "--varies", "precision")
    rb("criterion", "add", "FP16 adds at most 0.05 validation loss", "-e", "fp16", "-q", "q-imagenet", "--metric", "change.val_loss", "--at-most", "0.05")
    Path("out/seed1/fp16.json").write_text(json.dumps({"fp32": {"val_loss": 0.961}, "fp16": {"val_loss": 0.962}}), encoding="utf-8")
    rb("run", "add", "fp16", "--from", "out/seed1/fp16.json")
    Path("out/seed1/rerun.json").write_text(json.dumps({"fp32": {"val_loss": 0.962}, "int8": {"val_loss": 0.988}}), encoding="utf-8")
    rec = rb("reconcile")
    assert rec.data["found"] == []
    assert "out/seed1/rerun.json reports val_loss and the target is ambiguous: out/seed1/ is read by int8, fp16 (rb run add <int8|fp16> --from out/seed1/rerun.json)" in rec.data["unplaced"]


def test_a_status_of_one_experiment_offers_no_apply_that_would_add_another(rb):
    """rb status <experiment> offered rb reconcile --apply for its changed folder while another folder with a report,
    which --apply would add as an experiment, waited: the whole repository decides, whatever status shows."""
    rb("init", "INT8 ResNet-50")
    Path("results/int8_run").mkdir(parents=True)
    Path("results/int8_run/README.md").write_text("# INT8 run\n\n## Results\n\ntop-1 0.760\n", encoding="utf-8")
    Path("results/int8_run/metrics.json").write_text('{"top1": 0.760}', encoding="utf-8")
    rb("reconcile", "--apply")
    Path("results/int8_run/metrics.json").write_text('{"top1": 0.758}', encoding="utf-8")
    assert [i["do"] for i in rb("status", "int8_run").data["open"] if i["code"] == "reindex"] == ["rb reconcile --apply"]
    Path("results/fp32_run").mkdir()
    Path("results/fp32_run/README.md").write_text("# FP32 run\n", encoding="utf-8")
    st = rb("status", "int8_run")
    assert [i["do"] for i in st.data["open"] if i["code"] == "reindex"] == ["rb reconcile"] and "rb reconcile --apply" not in st.next


def test_a_seed_evaluated_without_the_journal_is_proposed_as_its_run(rb):
    """After the README's quick start, python eval.py --seed 4 left out/seed4/, and rb reconcile gave no rb run add line
    for it, though only int8's criterion reads its numbers. A results file in a folder nothing reads, which rb saw land
    after its first look here, is placed as any file is, with the seed its own config states. One that was there at
    that look is the work the record began after: evaluated before rb init, or touched since, or in a copy of the whole
    project made with fresh file times, it is never proposed."""
    import os
    import time
    Path("out/seed9").mkdir(parents=True)
    Path("out/seed9/metrics.json").write_text(json.dumps({"fp32": {"val_loss": 0.97}, "int8": {"val_loss": 0.99}}), encoding="utf-8")
    old = time.time() - 2 * 86400
    os.utime("out/seed9/metrics.json", (old, old))
    Path("out/seed5").mkdir()                                                # evaluated just before rb init
    Path("out/seed5/metrics.json").write_text(json.dumps({"fp32": {"val_loss": 0.959}, "int8": {"val_loss": 0.986}}), encoding="utf-8")
    Path("out/seed5/config.yaml").write_text("seed: 5\noptim:\n  lr: 0.1\ndata:\n  dataset: imagenet\n", encoding="utf-8")
    readme_layout(rb)
    rb("status")                                                             # the README's rb status: rb looks
    Path("out/seed5/metrics.json").touch()                                   # the same bytes, a new time
    seed_files(4)
    rec = rb("reconcile")
    assert [f["argv"] for f in rec.data["found"]] == [["run", "add", "int8", "--from", "out/seed4/metrics.json", "--set", "seed=4", "--config", "out/seed4/config.yaml"]]
    assert rec.next[0] == "rb reconcile --apply"
    applied = rb("reconcile", "--apply")
    assert len(applied.data["recorded"]) == 1 and applied.data["not_recorded"] == [] and len(rb("status").data["runs"]) == 4
    assert rb("reconcile").data["found"] == []                              # out/seed5/ and out/seed9/ are the start protocol's
    import shutil
    copy = Path.cwd().parent / f"{Path.cwd().name}-copy"
    shutil.copytree(Path.cwd(), copy, symlinks=True, copy_function=shutil.copyfile)   # every file's time is the copy's
    os.chdir(copy)
    rec = rb("reconcile")
    assert rec.data["found"] == [] and not any("--apply" in n for n in rec.next), rec.data["found"]
    assert rb("reconcile", "--apply").data["recorded"] == []


def test_what_rb_init_writes_is_in_its_first_look(rb):
    """rb init looked before it wrote its section into AGENTS.md, so the next look journaled AGENTS.md as changed and
    the tree as dirty: rb's own writes read as work that landed after the record began. Its first look comes once
    rb init has written all it writes."""
    Path("AGENTS.md").write_text("# Agents\n\nRun the tests before a commit.\n", encoding="utf-8")
    _git("add", "-A")
    _git("commit", "-qm", "the agents' instructions")
    rb("init", "INT8 ResNet-50", "--yes")
    assert "rb's section" in Path("AGENTS.md").read_text(encoding="utf-8") or "rabbit-brain" in Path("AGENTS.md").read_text(encoding="utf-8")
    rb("status")
    files = [(e["change"], e["path"]) for e in rb("journal", "-n", "100").data["entries"] if e.get("op") == "file"]
    assert files == [("new", "AGENTS.md")], files


def test_a_record_started_from_python_begins_at_its_first_look_as_rb_inits_does(rb):
    """A record started with the SDK's init looked nowhere until a later read, so a summary written in between read as
    there before the record began and was never proposed. The SDK's init looks once, as rb init does."""
    import rabbit_brain
    rabbit_brain.init("INT8 ResNet-50")
    Path("out").mkdir()
    Path("out/summary.json").write_text(json.dumps({"fp32": {"val_loss": 0.96}, "int8": {"val_loss": 0.99}}), encoding="utf-8")
    rb("experiment", "add", "INT8 vs FP32", "--id", "int8", "--baseline", "fp32", "--candidate", "int8", "--varies", "precision")
    rb("criterion", "add", "INT8 adds at most 0.05 validation loss", "-e", "int8", "--metric", "change.val_loss", "--at-most", "0.05")
    rec = rb("reconcile")
    assert [f["argv"] for f in rec.data["found"]] == [["run", "add", "int8", "--from", "out/summary.json"]], rec.data["unplaced"]


def test_a_loose_results_file_there_at_rb_init_is_the_work_before_the_record(rb):
    """A summary of both arms' numbers, written weeks before the record began and loose in out/, was proposed as a run
    of the one experiment whose criterion reads it, and rb reconcile --apply recorded it as new evidence. rb init's
    look is when the record begins: a file there then is listed with the line that records it, never proposed, and one
    written after it is."""
    import os
    import time
    Path("out").mkdir()
    Path("out/summary.json").write_text(json.dumps({"fp32": {"val_loss": 0.96}, "int8": {"val_loss": 0.99}}), encoding="utf-8")
    old = time.time() - 30 * 86400
    os.utime("out/summary.json", (old, old))
    _git("add", "-A")
    _git("commit", "-qm", "last month's summary")
    rb("init", "INT8 ResNet-50")
    rb("experiment", "add", "INT8 vs FP32", "--id", "int8", "--baseline", "fp32", "--candidate", "int8", "--varies", "precision")
    rb("criterion", "add", "INT8 adds at most 0.05 validation loss", "-e", "int8", "--metric", "change.val_loss", "--at-most", "0.05")
    rec = rb("reconcile")
    assert rec.data["found"] == [] and not any("--apply" in n for n in rec.next)
    assert ("out/summary.json was there when rb first looked here, so rb cannot tell it from the work before the record and proposes no run of "
            "it: rb run add int8 --from out/summary.json records it, if it is one") in rec.data["unplaced"], rec.data["unplaced"]
    assert rb("reconcile", "--apply").data["recorded"] == []
    Path("out/summary-2.json").write_text(json.dumps({"fp32": {"val_loss": 0.961}, "int8": {"val_loss": 0.987}}), encoding="utf-8")
    assert [f["argv"] for f in rb("reconcile").data["found"]] == [["run", "add", "int8", "--from", "out/summary-2.json"]]


def test_a_watched_command_that_writes_a_new_seed_folder_is_a_run_never_an_experiment(rb):
    """rb journal run said reconcile proposes the run that records what the command printed; reconcile then proposed
    no run and one experiment for the folder the command wrote. The file it wrote, with the numbers it printed, is
    proposed as an rb run add on int8, and the folder it is in is listed as unplaced, never added."""
    readme_layout(rb)
    _git("add", "-A")
    _git("commit", "-qm", "seeds 1 to 3")
    fp32, int8 = LOSSES[4]
    Path("eval.py").write_text(
        "import json, os, sys\n"
        "seed = int(sys.argv[2])\n"
        "os.makedirs(f'out/seed{seed}', exist_ok=True)\n"
        f"m = {{'fp32': {{'val_loss': {fp32}}}, 'int8': {{'val_loss': {int8}}}}}\n"
        "json.dump(m, open(f'out/seed{seed}/metrics.json', 'w'))\n"
        f"print(json.dumps({{'metrics': {{'fp32.val_loss': {fp32}, 'int8.val_loss': {int8}}}}}))\n", encoding="utf-8")
    ran = rb("journal", "run", "--", sys.executable, "eval.py", "--seed", "4")
    assert ran.data["metrics"] == {"fp32.val_loss": fp32, "int8.val_loss": int8}
    rec = rb("reconcile")
    [found] = rec.data["found"]
    assert found["experiment"] == "int8" and found["argv"][:5] == ["run", "add", "int8", "--from", "out/seed4/metrics.json"]
    assert found["argv"][5:7] == ["--set", "seed=4"]                       # the seed the command line gave
    seed4 = next(f for f in rec.data["repository"]["folders"] if f["folder"] == "out/seed4/")
    assert seed4["unplaced"] and seed4["experiment"] is None and rec.next[0] == "rb reconcile --apply"
    assert "experiments to add" not in rb("reconcile", text=True)
    applied = rb("reconcile", "--apply")
    assert applied.data["applied"]["added"] == [] and applied.data["not_recorded"] == [] and len(applied.data["recorded"]) == 1
    st = rb("status")
    assert experiments(rb) == ["int8"] and st.data["repository"]["unindexed"] == [] and st.data["found_unrecorded"] == 0


def write_eval(config: bool = False) -> None:
    """eval.py <--seed N>: the seed's metrics in out/seed<N>/metrics.json (and its resolved config beside them when
    `config`), and the line {"metrics": ...} that rb journal run reads."""
    Path("eval.py").write_text(
        "import json, os, sys\n"
        "seed = int(sys.argv[sys.argv.index('--seed') + 1])\n"
        "fp32, int8 = 0.96 + seed / 1000, 0.985 + seed / 1000\n"
        "os.makedirs(f'out/seed{seed}', exist_ok=True)\n"
        "json.dump({'fp32': {'val_loss': fp32}, 'int8': {'val_loss': int8}}, open(f'out/seed{seed}/metrics.json', 'w'))\n"
        + ("open(f'out/seed{seed}/config.yaml', 'w').write(f'seed: {seed}\\noptim:\\n  lr: 0.1\\ndata:\\n  dataset: imagenet\\n')\n" if config else "")
        + "print(json.dumps({'metrics': {'fp32.val_loss': fp32, 'int8.val_loss': int8}}))\n", encoding="utf-8")


def journal_runs(rb) -> list:
    return [e for e in rb("journal", "-n", "1000").data["entries"] if e.get("op") == "run"]


def test_a_watched_command_wrote_only_what_changed_while_it_ran(rb):
    """rb journal run took the journal's last entries as what the command wrote: files the look before it saw (sweeps
    two days old, a file written by hand) were proposed as runs carrying its commit, and rb reconcile --apply, which its
    Next named, recorded them. What a command wrote is what the look after it saw change; a file that landed before it
    is nobody's, and only the command's own output carries its code."""
    import os
    import time
    old = time.time() - 2 * 86400
    for n in (1, 2, 3):
        folder = Path(f"results/sweep-{n}")
        folder.mkdir(parents=True)
        (folder / "metrics.json").write_text(json.dumps({"fp32": {"val_loss": 0.95 + n / 100}, "int8": {"val_loss": 0.97 + n / 100}}), encoding="utf-8")
        for f in folder.iterdir():
            os.utime(f, (old, old))
    write_eval()
    _git("add", "-A")
    _git("commit", "-qm", "sweeps and the evaluation")
    rb("init", "INT8 ResNet-50")
    rb("question", "add", "Is INT8 ResNet-50 as good as FP32 on ImageNet?", "--id", "q-imagenet")
    rb("experiment", "add", "INT8 vs FP32", "--id", "int8", "-q", "q-imagenet", "--baseline", "fp32", "--candidate", "int8", "--varies", "precision")
    rb("metric", "add", "val_loss", "--unit", "nats", "--minimize")
    rb("criterion", "add", "INT8 adds at most 0.05 validation loss", "-e", "int8", "-q", "q-imagenet", "--metric", "change.val_loss", "--at-most", "0.05")
    rb("run", "add", "int8", "--from", "results/sweep-1/metrics.json", "--produced-at", "unknown")
    rb("status")
    Path("results/sweep-1/rerun.json").write_text(json.dumps({"fp32": {"val_loss": 0.962}, "int8": {"val_loss": 0.981}}), encoding="utf-8")
    rb("journal", "run", "--", sys.executable, "eval.py", "--seed", "4")
    [ran] = journal_runs(rb)
    assert ran["wrote"] == ["out/seed4/metrics.json"], ran["wrote"]
    rec = rb("reconcile")
    found = {f["path"]: f for f in rec.data["found"]}
    assert sorted(found) == ["out/seed4/metrics.json", "results/sweep-1/rerun.json"], sorted(found)
    assert "--commit" in found["out/seed4/metrics.json"]["argv"]
    assert "--commit" not in found["results/sweep-1/rerun.json"]["argv"] and not any("written by" in f for f in found["results/sweep-1/rerun.json"]["facts"])
    text = rb("reconcile", "--apply", text=True)
    assert "Kept in the record: 2 runs recorded." in text and "Runs recorded from the repository (2):" in text, text
    assert len(rb("status").data["runs"]) == 3 and rb("reconcile").data["found"] == []


def test_past_the_walks_cap_a_command_wrote_only_its_own_files(rb):
    """In a repository past the walk's 4000-file cap, a watched command that deleted a file let files the look before it
    never reached come into the look after it, as new: weeks-old sweep files were said to be what the command wrote,
    proposed with its commit, and recorded by the rb reconcile --apply its Next named. A file is a command's only when
    rb checked it just before and it changed, or its time is the command's; a file past the cap is unseen, never gone."""
    import os
    import time
    old = time.time() - 2 * 86400
    for n in range(2050):
        folder = Path(f"results/sweep-{n:04d}")
        folder.mkdir(parents=True)
        (folder / "metrics.json").write_text(json.dumps({"fp32": {"val_loss": 0.95 + n / 1e5}, "int8": {"val_loss": 0.97 + n / 1e5}}), encoding="utf-8")
        (folder / "config.yaml").write_text(f"lr: {n}\n", encoding="utf-8")
        for f in folder.iterdir():
            os.utime(f, (old, old))
    Path("out/aaa").mkdir(parents=True)
    for n in range(3):
        Path(f"out/aaa/log{n}.txt").write_text("step 1\n", encoding="utf-8")
    Path("eval.py").write_text(
        "import json, os, shutil, sys\n"
        "if sys.argv[1] == 'clean':\n"
        "    shutil.rmtree(sys.argv[2])\n"
        "    sys.exit(0)\n"
        "seed = int(sys.argv[2])\n"
        "for n in range(3):\n"
        "    if os.path.exists(f'out/aaa/log{n}.txt'):\n"
        "        os.remove(f'out/aaa/log{n}.txt')\n"
        "os.makedirs(f'out/seed{seed}', exist_ok=True)\n"
        "json.dump({'fp32': {'val_loss': 0.9}, 'int8': {'val_loss': 0.91 + seed / 1000}}, open(f'out/seed{seed}/metrics.json', 'w'))\n", encoding="utf-8")
    _git("add", "-A")
    _git("commit", "-qm", "sweeps")
    rb("init", "INT8 ResNet-50")
    rb("experiment", "add", "INT8 vs FP32", "--id", "int8", "--baseline", "fp32", "--candidate", "int8", "--varies", "precision")
    rb("metric", "add", "val_loss", "--unit", "nats", "--minimize")
    rb("criterion", "add", "INT8 adds at most 0.05 validation loss", "-e", "int8", "--metric", "change.val_loss", "--at-most", "0.05")
    rb("run", "add", "int8", "--from", "results/sweep-0001/metrics.json", "--produced-at", "unknown")
    assert rb("status").data["scope"]["cap_hit"]
    gone = lambda: [e["path"] for e in rb("journal", "-n", "100000").data["entries"] if e.get("change") == "gone"]  # noqa: E731
    rb("journal", "run", "--", sys.executable, "-c", "import os; os.makedirs('out/aab', exist_ok=True); open('out/aab/note.txt', 'w').write('x')")
    assert journal_runs(rb)[-1]["wrote"] == ["out/aab/note.txt"] and gone() == []
    rb("journal", "run", "--", sys.executable, "eval.py", "clean", "out/aab")
    assert journal_runs(rb)[-1]["wrote"] == [] and gone() == ["out/aab/note.txt"]
    rec = rb("reconcile")
    assert rec.data["found"] == [] and not any("--apply" in n for n in rec.next), rec.data["found"]
    assert rb("reconcile", "--apply").data["recorded"] == []
    rb("journal", "run", "--", sys.executable, "eval.py", "run", "3")       # deletes three early files, writes one
    assert journal_runs(rb)[-1]["wrote"] == ["out/seed3/metrics.json"], journal_runs(rb)[-1]["wrote"]
    assert [f["path"] for f in rb("reconcile").data["found"]] == ["out/seed3/metrics.json"]
    applied = rb("reconcile", "--apply")
    assert len(applied.data["recorded"]) == 1 and "out/seed3/metrics.json" in applied.data["recorded"][0] and len(rb("status").data["runs"]) == 2


def test_below_the_cap_a_file_a_command_copied_with_its_time_is_what_it_wrote(rb):
    """Below the walk's cap the look before a watched command checks every path where rb looks, there or not, so a file
    new after it is the command's whatever time it keeps. One copied with its time kept (cp -p, a download that keeps
    the source's) was left off what the command wrote when only a file written after the command started counted."""
    import os
    import time
    old = time.time() - 2 * 86400
    Path("archive").mkdir()
    Path("archive/metrics.json").write_text(json.dumps({"fp32": {"val_loss": 0.95}, "int8": {"val_loss": 0.97}}), encoding="utf-8")
    os.utime("archive/metrics.json", (old, old))
    rb("init", "INT8 ResNet-50")
    assert not rb("status").data["scope"]["cap_hit"]
    copy = "import os, shutil; os.makedirs('out/seed5'); shutil.copy2('archive/metrics.json', 'out/seed5/metrics.json')"
    rb("journal", "run", "--", sys.executable, "-c", copy)
    assert journal_runs(rb)[-1]["wrote"] == ["out/seed5/metrics.json"], journal_runs(rb)[-1]["wrote"]


def test_a_run_whose_per_run_value_nothing_gives_is_never_applied(rb):
    """rb reconcile proposed a run without the seed int8 declares per-run, and its Next named rb reconcile --apply,
    which then recorded nothing, and the printed rb run add failed: a loop with no end. Where neither the run's config
    nor the command line gives a per-run value, the line is listed with --set seed=<value> for whoever records it, and
    no next step names a command that fails."""
    readme_layout(rb)
    Path("report.py").write_text("import json\nprint(json.dumps({'metrics': {'fp32.val_loss': 0.961, 'int8.val_loss': 0.99}}))\n", encoding="utf-8")
    rb("journal", "run", "--", sys.executable, "report.py")
    rec = rb("reconcile")
    assert rec.data["found"] == [] and not any("--apply" in n for n in rec.next), rec.next
    [line] = [u for u in rec.data["unplaced"] if "per-run" in u]
    assert line.startswith("What `") and line.endswith("--set seed=<value>") and " is a run of int8, which declares seed per-run, and its command line does not give it: rb run add int8 " in line, line
    applied = rb("reconcile", "--apply")
    assert applied.data["recorded"] == [] and applied.data["not_recorded"] == []
    assert not any("--apply" in n for n in rb("status").next)


def test_a_runs_config_is_read_with_its_run_and_never_listed_as_numbers(rb):
    """rb reconcile called a run's own config, outside the folders it reads, numbers nothing declares and counted it as
    found and not in the record; a config a proposed run passes with --config was listed beside it the same way, and one
    of settings the spec declares read as numbers nothing declares."""
    readme_layout(rb, seeds=(1,))
    Path("logs/seed2").mkdir(parents=True)
    Path("logs/seed2/metrics.json").write_text(json.dumps({"fp32": {"val_loss": 0.958}, "int8": {"val_loss": 0.985}}), encoding="utf-8")
    Path("logs/seed2/config.yaml").write_text("seed: 2\noptim:\n  lr: 0.1\ndata:\n  dataset: imagenet\n  batch_size: 256\n", encoding="utf-8")
    rb("run", "add", "int8", "--from", "logs/seed2/metrics.json", "--set", "seed=2", "--config", "logs/seed2/config.yaml")
    rec = rb("reconcile")
    assert not any("logs/seed2" in u for u in rec.data["unplaced"]) and rec.data["found"] == []
    assert rb("status").data["found_unrecorded"] == 0
    write_eval(config=True)
    rb("journal", "run", "--", sys.executable, "eval.py", "--seed", "3")
    rec = rb("reconcile")
    [found] = rec.data["found"]
    assert found["argv"][3:9] == ["--from", "out/seed3/metrics.json", "--set", "seed=3", "--config", "out/seed3/config.yaml"], found["argv"]
    assert not any("out/seed3/config.yaml" in u for u in rec.data["unplaced"]), rec.data["unplaced"]
    Path("out/resolved.yaml").write_text("optim:\n  lr: 0.1\ndata:\n  dataset: imagenet\n  workers: 8\n", encoding="utf-8")
    [said] = [u for u in rb("reconcile").data["unplaced"] if u.startswith("out/resolved.yaml")]
    assert said.startswith("out/resolved.yaml holds settings int8's spec declares (optim.lr), not results: ") and "nothing declares its other numbers (data.workers)" in said, said
    # a file that echoes declared settings beside a number under none of their keys may hold a result: never said to be
    # settings alone, and never offered as --config
    Path("out/summary.json").write_text(json.dumps({"optim": {"lr": 0.1}, "seed": 4, "top1": 0.7512}), encoding="utf-8")
    [said] = [u for u in rb("reconcile").data["unplaced"] if u.startswith("out/summary.json")]
    assert said == "out/summary.json holds numbers nothing declares (top1) beside settings int8's spec declares (optim.lr, seed)", said


def test_a_proposed_runs_seed_is_its_own_or_none(rb):
    """4289cf1 filled a proposed run's per-run values from any config beside its file and from the watched command's
    line: a shared base config's seed 1 went on seed 3's run, a command that wrote three seeds' files gave all three the
    seed on its line, and a model's own config.json was passed as the run's resolved config, each recorded by the
    rb reconcile --apply its Next named. A config is the run's own when the command that wrote the file wrote it, or a
    recorded run read it with the values it states; the line gives a value only to the one results file its command
    wrote; a config and a line that disagree give neither; a config that states nothing the spec declares is none."""
    readme_layout(rb, seeds=(1,))
    rb("status")
    found = lambda: rb("reconcile").data["found"]  # noqa: E731
    # a shared base config beside each seed's metrics, with the seed none of them ran with
    Path("out/sweep").mkdir()
    Path("out/sweep/config.yaml").write_text("seed: 1\noptim:\n  lr: 0.1\ndata:\n  dataset: imagenet\n", encoding="utf-8")
    Path("evalsweep.py").write_text(
        "import json, sys\n"
        "seed = int(sys.argv[sys.argv.index('--seed') + 1])\n"
        "json.dump({'fp32': {'val_loss': 0.96}, 'int8': {'val_loss': 0.98 + seed / 1000}}, open(f'out/sweep/metrics_seed{seed}.json', 'w'))\n", encoding="utf-8")
    rb("journal", "run", "--", sys.executable, "evalsweep.py", "--seed", "2")
    rb("run", "add", "int8", "--from", "out/sweep/metrics_seed2.json", "--set", "seed=2", "--config", "out/sweep/config.yaml")
    rb("journal", "run", "--", sys.executable, "evalsweep.py", "--seed", "3")
    rec = rb("reconcile")
    assert rec.data["found"] == [] and not any("seed=1" in u for u in rec.data["unplaced"]), (rec.data["found"], rec.data["unplaced"])
    [line] = [u for u in rec.data["unplaced"] if u.startswith("out/sweep/metrics_seed3.json ")]
    assert "the config beside it and the command that wrote it disagree (seed 1 in out/sweep/config.yaml, 3 on its command's line)" in line, line
    assert line.endswith("--set seed=<value>") and not any("--apply" in n for n in rec.next), line
    assert rb("reconcile", "--apply").data["recorded"] == []
    # the same seed's file evaluated by hand: the base config is still no run's own
    Path("out/sweep/metrics_seed4.json").write_text(json.dumps({"fp32": {"val_loss": 0.96}, "int8": {"val_loss": 0.984}}), encoding="utf-8")
    [line] = [u for u in rb("reconcile").data["unplaced"] if u.startswith("out/sweep/metrics_seed4.json ")]
    assert line.endswith("rb run add int8 --from out/sweep/metrics_seed4.json --set seed=<value>") and "seed=1" not in line, line
    # one command, three seeds' files and no config: its line is no one file's
    Path("multi.py").write_text(
        "import json, os, sys\n"
        "first, n = int(sys.argv[2]), int(sys.argv[4])\n"
        "for seed in range(first, first + n):\n"
        "    os.makedirs(f'out/multi{seed}', exist_ok=True)\n"
        "    json.dump({'fp32': {'val_loss': 0.96}, 'int8': {'val_loss': 0.97 + seed / 1000}}, open(f'out/multi{seed}/metrics.json', 'w'))\n"
        "    print(f'wrote out/multi{seed}/metrics.json')\n", encoding="utf-8")
    rb("journal", "run", "--", sys.executable, "multi.py", "--seed", "1", "--n-seeds", "3")
    rec = rb("reconcile")
    assert rec.data["found"] == [], rec.data["found"]
    lines = [u for u in rec.data["unplaced"] if u.startswith("out/multi")]
    assert len(lines) == 3 and all("the command that wrote it wrote results in 3 folders, so its line is no one run's" in u and u.endswith("--set seed=<value>") for u in lines), lines
    assert rb("reconcile", "--apply").data["recorded"] == []
    # a model's own config.json beside its evaluation: no setting the spec declares, so no run's config
    Path("hf_eval.py").write_text(
        "import json, os, sys\n"
        "seed = int(sys.argv[sys.argv.index('--seed') + 1])\n"
        "os.makedirs(f'out/checkpoint-{seed}', exist_ok=True)\n"
        "json.dump({'architectures': ['ResNetForImageClassification'], 'hidden_sizes': [256, 512, 1024, 2048]}, open(f'out/checkpoint-{seed}/config.json', 'w'))\n"
        "json.dump({'fp32': {'val_loss': 0.959}, 'int8': {'val_loss': 0.984}}, open(f'out/checkpoint-{seed}/eval_results.json', 'w'))\n", encoding="utf-8")
    rb("journal", "run", "--", sys.executable, "hf_eval.py", "--seed", "2")
    [hf] = [f for f in found() if f["path"] == "out/checkpoint-2/eval_results.json"]
    assert hf["argv"][3:7] == ["--from", "out/checkpoint-2/eval_results.json", "--set", "seed=2"] and "--config" not in hf["argv"], hf["argv"]


def test_a_file_another_process_wrote_while_a_command_ran_is_never_its_run(rb, monkeypatch, tmp_path_factory):
    """While rb journal run watched python slow.py --seed 2, another job wrote results/other-job/: rb listed it as what
    the command wrote, rb reconcile proposed it with the command's commit and the job's own seed, and the
    rb reconcile --apply its Next named recorded it. A file nothing the command was given or printed names, that is
    not its one results file nor beside one, is said as written while it ran, with the line that records it."""
    import threading
    import time
    readme_layout(rb, seeds=(1,))
    # the other job writes once the command has started (a mark outside the project), never on a timer alone, so a
    # busy machine cannot put its files before rb's first look
    mark = tmp_path_factory.mktemp("mark") / "started"
    monkeypatch.setenv("RB_TEST_STARTED_MARK", str(mark))
    Path("slow.py").write_text(
        "import json, os, sys, time\n"
        "seed = int(sys.argv[sys.argv.index('--seed') + 1])\n"
        "open(os.environ['RB_TEST_STARTED_MARK'], 'w').close()\n"
        "time.sleep(2)\n"
        "os.makedirs(f'out/seed{seed}', exist_ok=True)\n"
        "open(f'out/seed{seed}/config.yaml', 'w').write(f'seed: {seed}\\n')\n"
        "m = {'fp32': {'val_loss': 0.958}, 'int8': {'val_loss': 0.985}}\n"
        "json.dump(m, open(f'out/seed{seed}/metrics.json', 'w'))\n"
        "print(json.dumps({'metrics': {'fp32.val_loss': 0.958, 'int8.val_loss': 0.985}}))\n", encoding="utf-8")

    def other_job():
        until = time.monotonic() + 60
        while not mark.exists() and time.monotonic() < until:
            time.sleep(0.02)
        time.sleep(0.2)
        Path("results/other-job").mkdir(parents=True)
        Path("results/other-job/config.yaml").write_text("seed: 42\n", encoding="utf-8")
        Path("results/other-job/metrics.json").write_text(json.dumps({"fp32": {"val_loss": 0.95}, "int8": {"val_loss": 1.2}}), encoding="utf-8")

    job = threading.Thread(target=other_job)
    job.start()
    rb("journal", "run", "--", sys.executable, "slow.py", "--seed", "2")
    job.join()
    [ran] = journal_runs(rb)[-1:]
    assert ran["wrote"] == ["out/seed2/config.yaml", "out/seed2/metrics.json", "results/other-job/config.yaml", "results/other-job/metrics.json"], ran
    assert ran["unnamed"] == ["results/other-job/config.yaml", "results/other-job/metrics.json"]
    rec = rb("reconcile")
    assert [f["path"] for f in rec.data["found"]] == ["out/seed2/metrics.json"], rec.data["found"]
    [line] = [u for u in rec.data["unplaced"] if u.startswith("results/other-job/metrics.json")]
    assert line.startswith("results/other-job/metrics.json was written while `") and " slow.py --seed 2` ran, and nothing that command was given or printed names it, so rb proposes no run of it: rb run add int8 --from results/other-job/metrics.json " in line, line
    assert "--commit" not in line
    applied = rb("reconcile", "--apply")
    assert len(applied.data["recorded"]) == 1 and "out/seed2/metrics.json" in applied.data["recorded"][0]


def test_a_command_watched_before_anything_is_declared_wrote_its_own_folder(rb):
    """Whether a file was another process's was told by the metrics the project declared when the command ran: an
    agent's first wrapped evaluation, before any experiment or metric, had its own folder kept as written while it ran,
    and once the experiment was declared rb proposed no run of it. A command's one folder of numbers is its own,
    whatever is declared."""
    Path("eval.py").write_text(
        "import json, os, sys\n"
        "seed = int(sys.argv[sys.argv.index('--seed') + 1])\n"
        "os.makedirs(f'out/seed{seed}', exist_ok=True)\n"
        "open(f'out/seed{seed}/config.yaml', 'w').write(f'seed: {seed}\\n')\n"
        "json.dump({'fp32': {'val_loss': 0.96}, 'int8': {'val_loss': 0.985}}, open(f'out/seed{seed}/metrics.json', 'w'))\n", encoding="utf-8")
    _git("add", "-A")
    _git("commit", "-qm", "the evaluation")
    rb("init", "INT8 ResNet-50")
    rb("journal", "run", "--", sys.executable, "eval.py", "--seed", "2")
    assert "unnamed" not in journal_runs(rb)[-1], journal_runs(rb)[-1]
    rb("experiment", "add", "INT8 vs FP32", "--id", "int8", "--baseline", "fp32", "--candidate", "int8", "--varies", "precision")
    rb("spec", "set", "int8", "seed", "--per-run")
    rb("metric", "add", "val_loss", "--unit", "nats", "--minimize")
    rb("criterion", "add", "INT8 adds at most 0.05 validation loss", "-e", "int8", "--metric", "change.val_loss", "--at-most", "0.05")
    rec = rb("reconcile")
    [found] = rec.data["found"] or [rec.data["unplaced"]]
    assert found["argv"][:9] == ["run", "add", "int8", "--from", "out/seed2/metrics.json", "--set", "seed=2", "--config", "out/seed2/config.yaml"], found
    assert "--commit" in found["argv"], found["argv"]


def test_a_watched_lines_seed_is_read_in_each_form_and_a_configs_at_any_depth(rb):
    """AGENTS.md says a watched command's line gives a seed as --seed 4, --seed=4 or seed=4, and a run's own config
    gives it where it states it; only --seed 4 and a top-level seed: were tried. Each form is read, a seed under a
    nested key of the run's own config is read, and a line whose seed is inside a quoted sh -c gives none."""
    readme_layout(rb, seeds=(1,))
    rb("status")
    Path("printonly.py").write_text(
        "import json, sys\n"
        "seed = next((a.split('=')[1] for a in sys.argv if a.startswith('seed=') or a.startswith('--seed=')), '0')\n"
        "print(json.dumps({'metrics': {'fp32.val_loss': 0.96, 'int8.val_loss': 0.98 + int(seed) / 1000}}))\n", encoding="utf-8")
    rb("journal", "run", "--", sys.executable, "printonly.py", "seed=7")
    rb("journal", "run", "--", sys.executable, "printonly.py", "--seed=4")
    rb("journal", "run", "--", "sh", "-c", f"{shlex.quote(sys.executable)} printonly.py seed=8")
    Path("nested.py").write_text(
        "import json, os\n"
        "os.makedirs('out/nested5', exist_ok=True)\n"
        "open('out/nested5/config.yaml', 'w').write('train:\\n  seed: 5\\noptim:\\n  lr: 0.1\\n')\n"
        "json.dump({'fp32': {'val_loss': 0.96}, 'int8': {'val_loss': 0.985}}, open('out/nested5/metrics.json', 'w'))\n", encoding="utf-8")
    rb("journal", "run", "--", sys.executable, "nested.py")
    rec = rb("reconcile")
    sets = {f["path"]: f["argv"][f["argv"].index("--set") + 1] for f in rec.data["found"] if "--set" in f["argv"]}
    printed = {p.split(" printonly.py ")[-1]: v for p, v in sets.items() if "printonly.py" in p}
    assert printed == {"seed=7": "seed=7", "--seed=4": "seed=4"}, sets
    assert sets["out/nested5/metrics.json"] == "seed=5" and "out/nested5/config.yaml" in next(f["argv"] for f in rec.data["found"] if f["path"] == "out/nested5/metrics.json")
    assert any(u.startswith("What `sh -c ") and u.endswith("--set seed=<value>") for u in rec.data["unplaced"]), rec.data["unplaced"]


def test_a_run_that_needs_its_seed_is_counted_as_one(rb):
    """rb reconcile said out/summary.json is a run of int8 that needs its seed, while rb status counted it among numbers
    rb cannot place in one experiment, and the line said the command that wrote it gives no seed where no command wrote
    it. rb status and rb context count a run that needs a per-run value as one (found_needs in --json), and the line
    says who could have given the value."""
    readme_layout(rb)
    Path("out/summary.json").write_text(json.dumps({"fp32": {"val_loss": 0.962}, "int8": {"val_loss": 0.99}}), encoding="utf-8")
    [line] = rb("reconcile").data["unplaced"]
    assert line == ("out/summary.json is a run of int8, which declares seed per-run, and no config of its own beside it gives it, and no watched "
                    "command wrote it: rb run add int8 --from out/summary.json --set seed=<value>"), line
    said = "Found in the repository, not in the record (1): 1 run of int8 needs its seed, which rb reconcile gives the line for"
    assert said in rb("status", text=True) and f"- {said}" in rb("context", text=True)
    st = rb("status")
    assert st.data["found_needs"] == [{"what": "out/summary.json", "experiment": "int8", "names": ["seed"]}]
    Path("report.py").write_text("import json\nprint(json.dumps({'metrics': {'fp32.val_loss': 0.959, 'int8.val_loss': 0.991}}))\n", encoding="utf-8")
    rb("journal", "run", "--", sys.executable, "report.py")
    seed_files(4)
    said = ("Found in the repository, not in the record (3): rb reconcile lists them, with the rb run add for the one run; 2 runs of int8 need "
            "their seed, which rb reconcile gives the lines for")
    assert said in rb("status", text=True), rb("status", text=True)
    assert [n["what"] for n in rb("context").data["found_needs"]] == ["out/summary.json", shlex.join([sys.executable, "report.py"])]


def test_a_watched_command_says_what_reconcile_does_with_what_it_printed(rb):
    """rb journal run said rb reconcile proposes the run that records what the command printed where nothing in the
    record declares those names, where the run needed a seed nothing gave, and where a recorded run already held the
    numbers, and rb reconcile then proposed none: an agent's first wrapped evaluation was told of a run that never came.
    It says what rb reconcile does: the run it proposes, or why none."""
    rb("init", "INT8 ResNet-50")
    Path("report.py").write_text("import json\nprint(json.dumps({'metrics': {'fp32.val_loss': 0.961, 'int8.val_loss': 0.987}}))\n", encoding="utf-8")
    report = lambda: rb("journal", "run", "--", sys.executable, "report.py", text=True)  # noqa: E731
    said = report()
    assert "Nothing in the record declares them yet, so rb reconcile proposes no run of them" in said and "proposes the run" not in said, said
    assert rb("reconcile").data["found"] == []
    rb("metric", "add", "val_loss", "--unit", "nats", "--minimize")
    assert "No experiment is declared yet, so rb reconcile proposes no run of them." in report()
    rb("experiment", "add", "INT8 vs FP32", "--id", "int8", "--baseline", "fp32", "--candidate", "int8", "--varies", "precision")
    rb("criterion", "add", "INT8 loses at most 0.5 top-1", "-e", "int8", "--metric", "change.top1", "--at-least", "-0.5")
    assert "rb reconcile proposes the run of int8 that records them, though no criterion of int8 reads them." in report()
    rb("criterion", "add", "INT8 adds at most 0.05 validation loss", "-e", "int8", "--metric", "change.val_loss", "--at-most", "0.05")
    assert "rb reconcile proposes the run of int8 that records them." in report()
    rb("spec", "set", "int8", "seed", "--per-run")
    assert "rb reconcile lists the run of int8 that records them, which needs seed: rb run add takes --set seed=<value>." in report()
    assert rb("reconcile").data["found"] == []
    rb("run", "add", "int8", "fp32.val_loss=0.961", "int8.val_loss=0.987", "--set", "seed=1")
    assert "rb reconcile proposes no new run: a recorded run of int8 holds these numbers." in report()
    assert rb("reconcile").data["found"] == []
    # a command that failed: what it printed is kept with it, and proposed as no run
    Path("fails.py").write_text("import json, sys\nprint(json.dumps({'metrics': {'fp32.val_loss': 0.9, 'int8.val_loss': 0.99}}))\nsys.exit(1)\n", encoding="utf-8")
    said = rb("journal", "run", "--", sys.executable, "fails.py", "--seed", "2", text=True)
    assert "It failed, so rb reconcile proposes no run of them." in said and "proposes the run" not in said, said
    assert rb("reconcile").data["found"] == []


def test_a_watched_sweeps_own_seed_folders_are_its_runs(rb):
    """rb journal run -- python sweep.py --seeds 5 6 7 wrote out/seed5/, out/seed6/ and out/seed7/, each with its
    metrics and its config, and rb listed all six files as written while the command ran, which nothing ties to it:
    the command's own output said to be possibly another process's, and three runs left to record by hand. A folder
    whose name is a value the line gives under that name (seed5 and --seeds 5) is the command's; one it names nowhere
    is still said as written while it ran."""
    readme_layout(rb)
    Path("sweep.py").write_text(
        "import json, os, sys\ni = sys.argv.index('--seeds') + 1\n"
        "for s in [int(a) for a in sys.argv[i:]] + [9]:\n"
        "    os.makedirs(f'out/seed{s}')\n"
        "    json.dump({'fp32': {'val_loss': 0.96 + s / 1000}, 'int8': {'val_loss': 0.98 + s / 1000}}, open(f'out/seed{s}/metrics.json', 'w'))\n"
        "    if s != 9:\n"
        "        open(f'out/seed{s}/config.yaml', 'w').write(f'seed: {s}\\noptim:\\n  lr: 0.1\\ndata:\\n  dataset: imagenet\\n')\n",
        encoding="utf-8")
    rb("journal", "run", "--", sys.executable, "sweep.py", "--seeds", "5", "6", "7", agent="s-1")
    journal = Path(_git("rev-parse", "--git-common-dir")) / "rb" / "journal" / "main" / "journal.jsonl"
    [ran] = [json.loads(line) for line in journal.read_text(encoding="utf-8").splitlines() if '"op": "run"' in line]
    assert ran["unnamed"] == ["out/seed9/metrics.json"], ran
    found = rb("reconcile").data["found"]
    assert sorted(f["argv"][4:8] for f in found) == [[f"out/seed{s}/metrics.json", "--set", f"seed={s}", "--config"] for s in (5, 6, 7)], found
    assert "1 file was written while a watched command ran, which nothing ties to it" in rb("status", text=True)


def test_the_tour_ends_with_its_results_folders_read_and_no_apply_suggested(rb):
    """rb tour's closing status ended Next: ... rb reconcile --apply, which added +3 experiments (s1, s2, s3)."""
    out = rb("tour", text=True)
    assert "rb reconcile --apply" not in out
    import os
    os.chdir("rb-tour")
    st = rb("status")
    assert st.data["repository"]["unindexed"] == [] and not any("reconcile" in n for n in st.next)
    assert rb("reconcile", "--apply").data["applied"]["added"] == [] and experiments(rb) == ["drop"]


def test_apply_makes_an_experiment_only_of_a_folder_with_a_report(rb):
    """Many folders of results files and one with a report, none read by a run: rb reconcile --apply added one
    experiment per folder. It adds the one with a report; the others are listed as unplaced, never guessed, and no
    next step rb suggests is --apply while it would add an experiment."""
    rb("init", "INT8 ResNet-50")
    for n in range(40):
        Path(f"outputs/run{n:02d}").mkdir(parents=True)
        Path(f"outputs/run{n:02d}/metrics.json").write_text(json.dumps({"val_loss": 0.96 + n / 1000}), encoding="utf-8")
    Path("results/calibration").mkdir(parents=True)
    Path("results/calibration/README.md").write_text("# INT8 calibration\n\n## Results\n\n512 images calibrate INT8 within 0.01 of FP32.\n", encoding="utf-8")
    Path("results/calibration/metrics.json").write_text('{"val_loss": 0.97}', encoding="utf-8")
    st = rb("status")
    [item] = [i for i in st.data["open"] if i["code"] == "unindexed_folder"]
    assert item["do"] == "rb reconcile" and len(item["folders"]) == 41 and len(st.data["repository"]["unindexed"]) == 41
    assert item["what"] == "41 results folders hold results no recorded experiment reads yet: outputs/run00/, outputs/run01/, outputs/run02/ and 38 more"
    assert not any("--apply" in n for n in st.next) and not any("--apply" in n for n in rb("context").next)
    rec = rb("reconcile")
    assert sum(f["unplaced"] for f in rec.data["repository"]["folders"]) == 40 and "--apply" not in " ".join(rec.next)
    assert "rb reconcile --apply keeps this in the record: 1 experiment to add." in rb("reconcile", text=True)
    assert rb("reconcile", "--apply").data["applied"]["added"] == ["calibration"] and experiments(rb) == ["calibration"]


def test_work_from_before_the_record_leads_to_the_start_protocol_until_it_starts(rb):
    """rb init in a project under way, with hundreds of results folders: rb context's Next said rb docs --start and
    rb reconcile --apply. Folders last written before the record began lead to the start protocol, never to --apply.
    Once the protocol has recorded where the work stands (its question, experiment, criterion and runs), Next kept
    saying rb docs --start for as long as any older folder was unread, and a later session restarted the protocol on
    a record already started: the folders are older work, which rb reconcile lists and which can wait."""
    import os
    import time
    old = time.time() - 2 * 86400
    for n in range(60):
        folder = Path(f"outputs/run{n:03d}")
        folder.mkdir(parents=True)
        (folder / "metrics.json").write_text(json.dumps({"val_loss": 0.96 + n / 1000}), encoding="utf-8")
        (folder / "README.md").write_text(f"# Run {n}\n", encoding="utf-8")
        for f in folder.iterdir():
            os.utime(f, (old, old))
    _git("add", "-A")
    _git("commit", "-qm", "the work so far")
    rb("init", "INT8 ResNet-50")
    start = "rb docs --start   (how to record the work already done)"
    for env in (rb("status"), rb("context")):
        assert env.next == [start], env.next
    rb("question", "add", "Is INT8 ResNet-50 as good as FP32 on ImageNet?", "--id", "q-imagenet", agent="s-start")
    rb("experiment", "add", "INT8 vs FP32", "--id", "int8", "-q", "q-imagenet", "--baseline", "fp32", "--candidate", "int8", "--varies", "precision", agent="s-start")
    rb("criterion", "add", "INT8 adds at most 0.05 validation loss", "-e", "int8", "-q", "q-imagenet", "--metric", "change.val_loss", "--at-most", "0.05",
       agent="s-start")
    for n in (1, 2, 3):
        rb("run", "add", "int8", f"fp32.val_loss={0.96 + n / 1000}", f"int8.val_loss={0.98 + n / 1000}", agent="s-start")
    st = rb("status")
    [item] = [i for i in st.data["open"] if i["code"] == "unindexed_folder"]
    assert item["do"] == "rb reconcile" and item["later"] and item["what"].startswith("60 results folders hold results written before the record began, which no recorded experiment reads yet: ")
    for env in (st, rb("context"), rb("status", agent="s-later"), rb("context", agent="s-later")):
        assert not any("rb docs --start" in n or "--apply" in n for n in env.next), env.next
    assert "results folders hold results written before the record began, which no recorded experiment reads yet: " in rb("context", text=True)


def test_a_folder_a_watched_run_writes_after_the_start_protocol_is_an_item_of_its_own(rb):
    """After the start protocol on a project with older results folders, one watched evaluation's new folder folded
    every older folder into its item: the line named them all (tens of KB in rb status and rb context, and in the
    session's briefing), called the older work new results, and --json lost `later` and the older folders. The new
    folder and the older work are two items, each counted on one short line, each listing every folder of its own."""
    import os
    import time
    from rbtest import Mcp, environment
    old = time.time() - 2 * 86400
    for n in range(60):
        folder = Path(f"outputs/run{n:03d}")
        folder.mkdir(parents=True)
        (folder / "metrics.json").write_text(json.dumps({"val_loss": 0.96 + n / 1000}), encoding="utf-8")
        os.utime(folder / "metrics.json", (old, old))
    _git("add", "-A")
    _git("commit", "-qm", "the work so far")
    rb("init", "INT8 ResNet-50")
    rb("question", "add", "Is INT8 ResNet-50 as good as FP32 on ImageNet?", "--id", "q-imagenet", agent="s-start")
    rb("experiment", "add", "INT8 vs FP32", "--id", "int8", "-q", "q-imagenet", "--baseline", "fp32", "--candidate", "int8", "--varies", "precision", agent="s-start")
    rb("criterion", "add", "INT8 adds at most 0.05 validation loss", "-e", "int8", "-q", "q-imagenet", "--metric", "change.val_loss", "--at-most", "0.05",
       agent="s-start")
    for n in (1, 2, 3):
        rb("run", "add", "int8", f"fp32.val_loss={0.96 + n / 1000}", f"int8.val_loss={0.98 + n / 1000}", agent="s-start")
    rb("context", agent="s-start")
    Path("eval.py").write_text("import json, os\nos.makedirs('outputs/run100')\njson.dump({'val_loss': 0.95}, open('outputs/run100/metrics.json', 'w'))\n",
                               encoding="utf-8")
    rb("journal", "run", "--", sys.executable, "eval.py", agent="s-start")
    for st in (rb("status"), rb("status", agent="s-start")):
        items = [i for i in st.data["open"] if i["code"] == "unindexed_folder"]
        assert [(i["folders"], i.get("later", False)) for i in items if not i.get("later")] == [(["outputs/run100/"], False)], items
        [older] = [i for i in items if i.get("later")]
        assert len(items) == 2 and older["folders"] == [f"outputs/run{n:03d}/" for n in range(60)]
        assert [i["what"] for i in items] == ["outputs/run100/ holds results no recorded experiment reads yet",
                                              "60 results folders hold results written before the record began, which no recorded experiment reads yet: "
                                              "outputs/run000/, outputs/run001/, outputs/run002/ and 57 more"], items
    for text in (rb("status", text=True), rb("context", text=True), rb("context", text=True, agent="s-start")):
        assert len(text) < 10_000 and max(map(len, text.splitlines())) < 1_000, (len(text), max(map(len, text.splitlines())))
    server = Mcp("claude-code", env={**{k: v for k, v in environment().items() if k != "RB_ACTOR"}, "RB_SESSION": "s-start"})
    try:
        server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "clientInfo": {"name": "folders"}}})
        said = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "status", "arguments": {}}})["result"]
    finally:
        server.close()
    assert len(said["content"][0]["text"]) < 30_000
    assert [len(i["folders"]) for i in said["structuredContent"]["data"]["open"] if i["code"] == "unindexed_folder"] == [1, 60]


def test_the_mcp_status_counts_the_folders_it_cut_from_each_item(rb):
    """The MCP status tool, cutting each open item's folders to 100, said in data.cut.open_folders the count of the
    last item it cut alone, where the new results and the older work are two items of more than 100 folders each."""
    import os
    import time
    from rbtest import Mcp, environment
    old = time.time() - 2 * 86400
    for n in range(120):
        Path(f"outputs/old{n:03d}").mkdir(parents=True)
        Path(f"outputs/old{n:03d}/metrics.json").write_text(json.dumps({"val_loss": 0.96}), encoding="utf-8")
        os.utime(f"outputs/old{n:03d}/metrics.json", (old, old))
    _git("add", "-A")
    _git("commit", "-qm", "the work so far")
    rb("init", "INT8 ResNet-50")
    rb("question", "add", "Is INT8 ResNet-50 as good as FP32 on ImageNet?", "--id", "q-imagenet", agent="s-start")
    for n in range(110):
        Path(f"outputs/new{n:03d}").mkdir(parents=True)
        Path(f"outputs/new{n:03d}/metrics.json").write_text(json.dumps({"val_loss": 0.95}), encoding="utf-8")
    server = Mcp("claude-code", env={**{k: v for k, v in environment().items() if k != "RB_ACTOR"}, "RB_SESSION": "s-start"})
    try:
        server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "clientInfo": {"name": "cut"}}})
        data = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "status", "arguments": {}}})["result"]["structuredContent"]["data"]
    finally:
        server.close()
    assert [len(i["folders"]) for i in data["open"] if i["code"] == "unindexed_folder"] == [100, 100]
    assert data["cut"]["open_folders"] == 230, data["cut"]


# ---------------------------------------------------------------- the user's own next step, and the first line

def test_a_call_waiting_on_the_user_leads_their_next_line(rb):
    """With a call an agent session proposed, the user's rb status ended Next: with agent work, and a session's rb
    context named rb confirm. In the user's own terminal the Next line names the confirmation first; a session's never
    names it, since only the user confirms."""
    readme_layout(rb, seeds=(1, 2))                                          # two seeds: a run is still to be recorded
    proposed = rb("decide", "int8", "investigate", "--why", "two seeds of three", agent="s-1").data["object"]["id"]
    for user in (rb("status"), rb("context")):
        assert user.next[0] == f"rb confirm {proposed}" and len(user.next) > 1, user.next
    for session in (rb("status", agent="s-1"), rb("context", agent="s-1")):
        assert session.next and not any(n.startswith("rb confirm") for n in session.next), session.next


def test_a_title_that_is_its_id_is_said_once(rb):
    """rb status's first line read `<name> · <name>` where the project's title and id are the same."""
    rb("init", "int8-resnet-50")
    assert rb("status", text=True).splitlines()[0] == "int8-resnet-50"
    rb("project", "title", "INT8 ResNet-50", "--why", "the theme, as the paper titles it")
    assert rb("status", text=True).splitlines()[0] == "INT8 ResNet-50 · int8-resnet-50"


# ---------------------------------------------------------------- long lists: the first ones, and a count of the rest

def test_thousands_of_results_folders_are_counted_on_one_short_line(rb):
    """In a repository of 3,000 results folders rb status printed one line of 54,058 characters and rb context 422 KB.
    The text names the first few and counts the rest; --json keeps every folder."""
    rb("init", "INT8 ResNet-50")
    for n in range(3000):
        Path(f"outputs/r{n:04d}").mkdir(parents=True)
        Path(f"outputs/r{n:04d}/metrics.json").write_text('{"val_loss": 0.97}', encoding="utf-8")
    text = rb("status", text=True)
    assert "3000 results folders hold results no recorded experiment reads yet: outputs/r0000/, outputs/r0001/, outputs/r0002/ and 2997 more" in text
    assert max(map(len, text.splitlines())) < 600 and len(text.splitlines()) < 40, text
    briefing = rb("context", text=True)
    assert len(briefing.splitlines()) < 120 and len(briefing) < 12_000
    st = rb("status")
    [item] = [i for i in st.data["open"] if i["code"] == "unindexed_folder"]
    assert len(item["folders"]) == 3000 == len(st.data["repository"]["unindexed"])


def test_a_long_record_is_listed_in_part_with_a_count_of_the_rest(rb):
    """rb status and rb context listed every experiment, criterion and run, and a caveat named every run it covers:
    a record of a few hundred runs gave a briefing of hundreds of kilobytes. Each list names its first or latest ones
    and counts the rest, with the command that lists them all; --json keeps every one."""
    import re
    rb("init", "INT8 ResNet-50")
    rb("question", "add", "Is INT8 ResNet-50 as good as FP32 on ImageNet?", "--id", "q-imagenet")
    for n in range(22):
        rb("experiment", "add", f"INT8 calibrated on {n + 1} batches", "--id", f"cal{n:02d}", "-q", "q-imagenet")
        rb("criterion", "add", f"INT8 calibrated on {n + 1} batches keeps top-1 at 0.75 or more", "-e", f"cal{n:02d}", "-q", "q-imagenet",
           "--metric", "top1", "--at-least", "0.75")
    for s in range(12):
        rb("run", "add", "cal00", f"top1={0.760 + s / 1000}")
    text = rb("status", text=True)
    assert "  ... and 17 more lines of its reading: rb show q-imagenet reads it whole\n" in text
    assert "  ... and 2 more criteria: rb show <id> reads one, and rb status --json lists every one\n" in text
    assert "  ... and 2 more experiments: rb show <id> reads one, and rb status --json lists every one\n" in text
    assert re.search(r"typed in, not read from a file or run: (run\w+, ){5}run\w+ and 6 more", text), text
    briefing = rb("context", text=True)
    assert "- 2 earlier runs are not listed here: rb show cal00 lists every one\n" in briefing
    assert "2 more experiments, less recently written to, are not shown here: " in briefing
    st = rb("status")
    assert (len(st.data["experiments"]), len(st.data["findings"]), len(st.data["runs"])) == (22, 22, 12)


def test_the_mcp_status_and_reconcile_answers_keep_the_envelopes_order(rb):
    """The status and reconcile tools' answers, cut or not, moved data after errors, next and rb_delta, out of the order
    every answer keeps (ok, rb_version, command, data, errors, next, rb_delta)."""
    from rbtest import Mcp, environment
    rb("init", "INT8 ResNet-50")
    server = Mcp("claude-code", env={**{k: v for k, v in environment().items() if k != "RB_ACTOR"}, "RB_SESSION": "sess-order"})
    try:
        server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "clientInfo": {"name": "order"}}})
        said = {name: server.handle({"jsonrpc": "2.0", "id": i, "method": "tools/call", "params": {"name": name, "arguments": {}}})["result"]
                for i, name in enumerate(("status", "reconcile", "journal"), 2)}
    finally:
        server.close()
    for name, result in said.items():
        assert list(result["structuredContent"])[:4] == ["ok", "rb_version", "command", "data"], (name, list(result["structuredContent"]))
        assert list(json.loads(result["content"][0]["text"]))[:4] == ["ok", "rb_version", "command", "data"], name


def test_a_thousand_results_folders_read_as_one_line_of_reconcile_and_a_short_answer(rb):
    """rb reconcile, the start protocol's first read, printed three lines for each folder of results files no run named
    (2,099 lines and 133 KB for 506 folders), and its MCP tool answered with 760 KB, the status tool with 53 KB, which a
    client cuts before the agent reads any of it. Such folders are one counted line naming three, with the rule said
    once, before and after the record starts; a folder with a report keeps its block; the MCP tools keep the first 100
    of each long list and say what they cut (data.cut); --json and the tools' all keep every one."""
    from rbtest import Mcp, environment
    n = 1000
    for k in range(n):
        Path(f"out/seed{k}").mkdir(parents=True)
        Path(f"out/seed{k}/metrics.json").write_text(json.dumps({"fp32": {"val_loss": 0.96}, "int8": {"val_loss": 0.98}}), encoding="utf-8")
    Path("results/calibration").mkdir(parents=True)
    Path("results/calibration/README.md").write_text("# INT8 calibration\n\n## Results\n\n512 images calibrate INT8.\n", encoding="utf-8")
    Path("results/calibration/metrics.json").write_text('{"val_loss": 0.97}', encoding="utf-8")
    line = f"{n} results folders hold results files {{}}: out/seed0/, out/seed1/, out/seed10/ and {n - 3} more. With no report of its own none is an experiment"
    before = rb("reconcile", text=True)
    assert line.format("nothing records yet") in before and len(before.splitlines()) < 40 and len(before) < 6000, before
    rb("init", "INT8 ResNet-50")
    text = rb("reconcile", text=True)
    assert line.format("no recorded run named") in text and "calibration  results/calibration/  new, not in the record" in text
    assert len(text.splitlines()) < 40 and len(text) < 6000, text
    rec = rb("reconcile")
    assert sum(f["unplaced"] for f in rec.data["repository"]["folders"]) == n and len(rec.data["read"]) == n + 1
    server = Mcp("claude-code", env={**{k: v for k, v in environment().items() if k != "RB_ACTOR"}, "RB_SESSION": "sess-reconcile-cap"})
    try:
        server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "clientInfo": {"name": "cap"}}})
        ask = lambda name, arguments, id: server.handle({"jsonrpc": "2.0", "id": id, "method": "tools/call", "params": {"name": name, "arguments": arguments}})["result"]  # noqa: E731
        capped, whole = ask("reconcile", {}, 2), ask("reconcile", {"all": True}, 3)
        status, status_all = ask("status", {}, 4), ask("status", {"all": True}, 5)
    finally:
        server.close()
    data = capped["structuredContent"]["data"]
    assert data["cut"]["unplaced_folders"] == n and data["cut"]["first"] == ["out/seed0/", "out/seed1/", "out/seed10/"]
    assert [f["folder"] for f in data["repository"]["folders"]] == ["results/calibration/"] and list(data["read"]) == ["calibration"]
    assert len(json.dumps(capped)) < 40_000, len(json.dumps(capped))
    assert len(whole["structuredContent"]["data"]["repository"]["folders"]) == n + 1 and "cut" not in whole["structuredContent"]["data"]
    st = status["structuredContent"]["data"]
    assert st["cut"]["unindexed"] == n + 1 and len(st["repository"]["unindexed"]) == 100 and len(json.dumps(status)) < 40_000, len(json.dumps(status))
    assert len(status_all["structuredContent"]["data"]["repository"]["unindexed"]) == n + 1 and "cut" not in status_all["structuredContent"]["data"]


def test_a_note_or_a_call_on_an_older_experiment_brings_it_into_the_briefing(rb):
    """rb context shows the twenty experiments most recently written to, but counted only an experiment's creation and
    its runs and analyses: a note, a criterion or a call on an older experiment left it hidden and called less recently
    written to, behind experiments nobody had touched since. Any write to an experiment, its parts or its criteria, and
    a call on one of them, counts."""
    rb("init", "INT8 ResNet-50")
    rb("question", "add", "Is INT8 ResNet-50 as good as FP32 on ImageNet?", "--id", "q-imagenet")
    for n in range(22):
        rb("experiment", "add", f"INT8 calibrated on {n + 1} batches", "--id", f"cal{n:02d}", "-q", "q-imagenet")
        rb("criterion", "add", f"INT8 calibrated on {n + 1} batches keeps top-1 at 0.75 or more", "-e", f"cal{n:02d}", "-q", "q-imagenet",
           "--metric", "top1", "--at-least", "0.75", "--id", f"c{n:02d}")
    hidden = lambda text: text.split("more experiments, less recently written to, are not shown here: ")[1].split(" (")[0]  # noqa: E731
    assert hidden(rb("context", text=True)) == "cal00, cal01"
    rb("note", "add", "-e", "cal00", "calibration set 00 reused the train split")
    text = rb("context", text=True)
    assert "## Experiment cal00:" in text and hidden(text) == "cal01, cal02"
    rb("decide", "c01", "investigate", "-m", "look at the calibration images")
    text = rb("context", text=True)
    assert "## Experiment cal01:" in text and hidden(text) == "cal02, cal03"
