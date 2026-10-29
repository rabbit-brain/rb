"""Rerun this research: from a published bundle to rb status reading each inherited rule, in a fresh folder.

The parent is the INT8 ResNet-50 on ImageNet example, recorded by human:ana in a repository that commits .rb/, as
rb's manual says to, with the code's repository a local bare git repository standing in for its published address.
"""
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from rabbit_brain.models import Envelope
from rbtest import as_the_user, main, state
from test_export import SERVED, served_at  # noqa: F401 (origin at an https address, served from a bare repository)

# like most evaluation scripts, it keeps when it ran beside its numbers, so a rerun's file holds bytes of its own
EVAL = '''import json, os, sys, time
args = dict(zip(sys.argv[1::2], sys.argv[2::2]))
seed = int(args["--seed"])
out = args["--out"]
os.makedirs(out, exist_ok=True)
fp32 = {1: 0.7613, 2: 0.7609, 3: 0.7611}[seed]
with open(os.path.join(out, "config.yaml"), "w") as f:
    f.write("seed: %d\\n" % seed)
with open(os.path.join(out, "metrics.json"), "w") as f:
    json.dump({"fp32": {"top1": fp32}, "int8": {"top1": round(fp32 - 0.0032, 4)}, "finished": time.time()}, f)
'''


def git(*args, cwd=None):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def rb_in(root, capsys, *argv):
    cwd = Path.cwd()
    os.chdir(root)
    try:
        code = main([*argv, "--json"])
        return code, Envelope.model_validate_json(capsys.readouterr().out)
    finally:
        os.chdir(cwd)


@pytest.fixture
def published(workdir, monkeypatch, tmp_path_factory, capsys):
    """ana's record of e1 (INT8 against FP32, a seed per run) with c1 met at seed 1, committed with its code, pushed to
    a bare repository that stands in for the code's address, and exported with the record."""
    as_the_user(monkeypatch)
    cfg = tmp_path_factory.mktemp("gitconfig") / "config"
    cfg.write_text("[user]\n\tname = ana\n\temail = ana@example.org\n[init]\n\tdefaultBranch = main\n", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(cfg))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("RB_ACTOR", "human:ana")
    code_dir = Path.cwd()
    git("init", "-q")
    Path("eval.py").write_text(EVAL, encoding="utf-8")
    Path(".gitignore").write_text("data/\n", encoding="utf-8")

    def rb(*argv):
        return rb_in(code_dir, capsys, *argv)

    assert rb("init", "INT8 ResNet-50 on ImageNet", "--id", "int8-resnet-50", "--no-agents")[0] == 0
    assert rb("question", "add", "Is INT8 ResNet-50 as accurate as FP32 on ImageNet?", "--id", "q1")[0] == 0
    assert rb("experiment", "add", "INT8 against FP32: top-1 on the validation set", "--id", "e1", "-q", "q1",
              "--hypothesis", "INT8 keeps top-1 within a point of FP32", "--baseline", "fp32", "--candidate", "int8",
              "--varies", "precision")[0] == 0
    assert rb("spec", "set", "e1", "seed", "--per-run")[0] == 0
    assert rb("metric", "add", "top1", "--maximize")[0] == 0
    assert rb("resource", "add", "imagenet-val", "--type", "dataset", "--name", "ImageNet-1k validation",
              "--recipe", "python get_data.py --split val", "--at", "local:data/val")[0] == 0
    assert rb("criterion", "add", "INT8 loses at most a point of top-1", "-e", "e1", "-q", "q1", "--metric",
              "change.top1", "--at-least", "-0.01", "--id", "c1")[0] == 0
    git("add", "-A")
    git("commit", "-qm", "eval.py and the record")
    subprocess.run([sys.executable, "eval.py", "--seed", "1", "--out", "results/s1"], check=True)
    assert rb("run", "add", "e1", "--from", "results/s1/metrics.json", "--set", "seed=1",
              "--command", "python eval.py --seed 1 --out results/s1", "--input", "imagenet-val:eval")[0] == 0
    git("add", "-A")
    git("commit", "-qm", "seed 1")
    bare = tmp_path_factory.mktemp("published") / "int8.git"
    git("clone", "-q", "--bare", str(code_dir), str(bare))
    git("remote", "add", "origin", served_at(monkeypatch, bare))
    commit = git("rev-parse", "HEAD")
    code, env = rb("export", "--with-record")
    assert code == 0
    bundle = Path(env.data["path"])
    return bundle, bare, commit


def test_a_rerun_goes_from_a_bundle_to_each_inherited_rule_s_reading(published, tmp_path_factory, capsys):
    bundle, bare, commit = published
    fresh = tmp_path_factory.mktemp("reader")
    # 1. clone: the code comes too, at the bundle's commit, with the record in place of the parent's
    code, env = rb_in(fresh, capsys, "clone", str(bundle), "int8-rerun")
    assert code == 0, env.errors
    dest = fresh / "int8-rerun"
    assert {k: env.data["code"][k] for k in ("url", "commit", "got", "branch", "why")} == {"url": SERVED, "commit": commit, "got": True, "branch": "rerun", "why": ""}
    assert (dest / "eval.py").is_file() and git("rev-parse", "--abbrev-ref", "HEAD", cwd=dest) == "rerun"
    # one commit on top of the bundle's, and it changes the record alone; the parent's record stays at that commit
    assert git("rev-parse", "HEAD~1", cwd=dest) == commit
    # it changes the record, and takes out what the parent's run wrote: the rerun writes its own
    changed = git("diff", "--name-status", "HEAD~1", "HEAD", cwd=dest).splitlines()
    assert changed and all(p.split("\t")[1].startswith(".rb/") or p in ("D\tresults/s1/config.yaml", "D\tresults/s1/metrics.json") for p in changed), changed
    assert env.data["code"]["taken_out"] == ["results/s1/"] and not (dest / "results").exists()
    assert "int8-resnet-50" in git("show", f"{commit}:.rb/project.yaml", cwd=dest)
    assert git("ls-tree", "-r", "--name-only", commit, "--", ".rb/experiments/e1/runs", cwd=dest)   # the parent's run, in git
    # 2. rb context names the parent, the code, each resource and the first command
    code, env = rb_in(dest, capsys, "context")
    assert code == 0
    rerun = env.data["rerun"]
    assert rerun["parent"]["url"] == bundle.as_posix()
    assert (rerun["code"]["state"], rerun["code"]["commit"]) == ("here", commit)
    assert [r["id"] for r in rerun["resources"]] == ["imagenet-val"]
    assert rerun["resources"][0]["how"] == ["python get_data.py --split val"]
    first = rerun["experiments"][0]["first"]
    assert first == "rb journal run -- python eval.py --seed 1 --out results/s1"
    md = env.data["markdown"]
    assert "## Rerun the parent" in md and f"`{first}`" in md and "how to get it again: python get_data.py --split val" in md
    assert env.next[0] == first
    # the parent's run is not the fork's: nothing reads yet
    code, env = rb_in(dest, capsys, "status")
    c1 = next(f for f in env.data["findings"] if f["criterion"] == "c1")
    assert c1["rule_outcome"] == "untested" and env.next[0] == first
    # 3. the first command, watched; the run rb reconcile proposes for it, recorded as it prints it
    words = first.split()
    assert words[:5] == ["rb", "journal", "run", "--", "python"]
    cwd = Path.cwd()
    os.chdir(dest)
    try:
        assert main(["journal", "run", "--json", "--", sys.executable, *words[5:]]) == 0
        capsys.readouterr()
    finally:
        os.chdir(cwd)
    code, env = rb_in(dest, capsys, "reconcile")
    proposed = [p for p in env.data["found"] if p.get("experiment") == "e1"]
    assert len(proposed) == 1, (env.data["unplaced"], env.data["read"], env.data["scan"])
    argv = proposed[0]["argv"]
    assert argv[:3] == ["run", "add", "e1"]
    code, env = rb_in(dest, capsys, *argv)
    assert code == 0, env.errors
    # 4. rb status reads the inherited rule on the run made here
    code, env = rb_in(dest, capsys, "status")
    c1 = next(f for f in env.data["findings"] if f["criterion"] == "c1")
    assert c1["says"]["headline"] == "reproduced · point only"
    assert c1["integrity"] == []
    # the run's receipt names the fork's commit, whose code is the bundle's
    [run] = state(dest).records("run")
    assert run.receipt.attached["commit"] == git("rev-parse", "HEAD", cwd=dest)
    assert git("diff", "--name-only", commit, "HEAD", "--", ".", ":(exclude).rb", ":(exclude)results", cwd=dest) == ""
    assert env.data["rerun"]["experiments"][0]["runs_here"] == 1


def test_a_bundle_that_names_no_code_says_so_and_how_to_bring_it(published, tmp_path_factory, capsys):
    bundle, bare, commit = published
    code, env = rb_in(bundle.parent, capsys, "export", "--with-record", "--no-repo", "--out", str(tmp_path_factory.mktemp("norepo") / "b"))
    assert code == 0, (bundle, env.errors)
    plain = Path(env.data["path"])
    fresh = tmp_path_factory.mktemp("reader")
    code, env = rb_in(fresh, capsys, "clone", str(plain), "r")
    assert code == 0 and env.data["code"] is None
    assert not (fresh / "r" / "eval.py").exists()
    code, env = rb_in(fresh / "r", capsys, "context")
    assert env.data["rerun"]["code"]["state"] == "unknown"
    assert env.data["rerun"]["code"]["get"] == f"rb clone {plain.as_posix()} <new folder> --code <address of the code>"
    # given the address, the same bundle brings the code, at the commit the record was exported at
    code, env = rb_in(fresh, capsys, "clone", str(plain), "r2", "--code", str(bare))
    assert code == 0 and env.data["code"]["got"] is True and (fresh / "r2" / "eval.py").is_file()


def test_a_code_address_that_cannot_be_reached_leaves_the_record_alone_and_says_why(published, tmp_path_factory, capsys):
    bundle, bare, commit = published
    fresh = tmp_path_factory.mktemp("reader")
    missing = fresh / "no-such-repo.git"
    code, env = rb_in(fresh, capsys, "clone", str(bundle), "r", "--code", str(missing))
    assert code == 0
    assert env.data["code"]["got"] is False and env.data["code"]["why"]
    dest = fresh / "r"
    assert (dest / ".rb" / "project.yaml").is_file() and not (dest / "eval.py").exists()
    assert "first commit" in env.data["git"]        # a repository of its own, as a clone with no code is
    code, env = rb_in(dest, capsys, "context")
    assert env.data["rerun"]["code"]["state"] == "elsewhere"
    # git failed at that address: it is not offered again, and the parent's commands wait for the folder the code
    # comes into
    assert env.data["rerun"]["code"]["get"] == f"rb clone {bundle.as_posix()} <new folder> --code <address of the code>"
    assert env.next[0] == env.data["rerun"]["code"]["get"] and not any("rb journal run" in n for n in env.next), env.next
    # a repository that lacks the bundle's commit is refused the same way
    other = tmp_path_factory.mktemp("other") / "x.git"
    git("init", "-q", "--bare", str(other))
    code, env = rb_in(fresh, capsys, "clone", str(bundle), "r3", "--code", str(other))
    assert code == 0 and env.data["code"]["got"] is False


def test_with_no_git_identity_the_code_is_not_got_and_the_record_lands_alone(published, tmp_path_factory, monkeypatch, capsys):
    """The record's commit takes the place of the parent's record in the code's checkout; git never commits it under an
    identity it guesses, so with none of the user's own the clone gets no code, and its log is never read against the
    parent's."""
    bundle, bare, commit = published
    empty = tmp_path_factory.mktemp("noid") / "config"
    empty.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty))
    monkeypatch.setenv("EMAIL", "guessed@example.com")
    fresh = tmp_path_factory.mktemp("reader")
    code, env = rb_in(fresh, capsys, "clone", str(bundle), "r")
    assert code == 0
    assert env.data["code"]["got"] is False and "user.name and user.email" in env.data["code"]["why"]
    dest = fresh / "r"
    assert not (dest / "eval.py").exists() and "staged" in env.data["git"]
    code, env = rb_in(dest, capsys, "status")
    assert not any(i.get("code") == "edited_outside_rb" for i in env.data["open"])
    assert env.data["rerun"]["code"]["state"] == "elsewhere"
    assert env.next[0] == f"rb clone {bundle.as_posix()} <new folder> --code {SERVED}"


def test_a_path_with_a_space_is_quoted_in_the_command_rb_clone_names(published, tmp_path_factory, capsys):
    bundle, bare, commit = published
    spaced = tmp_path_factory.mktemp("shared") / "int8 bundle"
    shutil.copytree(bundle, spaced)
    fresh = tmp_path_factory.mktemp("reader")
    cwd = Path.cwd()
    os.chdir(fresh)
    try:
        assert main(["clone", str(spaced), "r", "--code", str(fresh / "no such repo")]) == 0
    finally:
        os.chdir(cwd)
    said = capsys.readouterr().out
    assert f"With access, or a copy you can reach: rb clone '{spaced.as_posix()}' <new folder> --code <address of the code>" in said
    code, env = rb_in(fresh / "r", capsys, "context")
    assert env.data["rerun"]["code"]["get"] == f"rb clone '{spaced.as_posix()}' <new folder> --code <address of the code>"


def test_only_what_the_parents_runs_wrote_where_results_land_is_taken_out(published, tmp_path_factory, capsys):
    """Code a run kept as an artifact stays, and so does what a run reads (a resource kept in a results folder): a
    clone takes out the parent's results, never what the rerun needs."""
    bundle, bare, commit = published
    code_dir = bundle.parent
    Path(code_dir, "results/s2").mkdir(parents=True)
    Path(code_dir, "results/s2/labels.json").write_text('{"labels": 1000}', encoding="utf-8")
    assert rb_in(code_dir, capsys, "resource", "add", "labels", "--type", "dataset", "--name", "ImageNet labels", "--at", "local:results/s2/labels.json")[0] == 0
    subprocess.run([sys.executable, "eval.py", "--seed", "2", "--out", "results/s2"], cwd=code_dir, check=True)
    assert rb_in(code_dir, capsys, "run", "add", "e1", "--from", "results/s2/metrics.json", "--set", "seed=2", "--artifact", "eval.py", "--artifact", "results/s2/labels.json",
                 "--input", "labels:eval", "--command", "python eval.py --seed 2 --out results/s2")[0] == 0
    git("add", "-A", cwd=code_dir)
    git("commit", "-qm", "seed 2", cwd=code_dir)
    git("push", "-q", str(bare), "HEAD:main", cwd=code_dir)
    code, env = rb_in(code_dir, capsys, "export", "--with-record", "--out", str(tmp_path_factory.mktemp("b2") / "b"))
    assert code == 0
    fresh = tmp_path_factory.mktemp("reader")
    code, env = rb_in(fresh, capsys, "clone", env.data["path"], "r")
    assert code == 0 and env.data["code"]["got"] is True
    dest = fresh / "r"
    assert env.data["code"]["taken_out"] == ["results/s1/", "results/s2/metrics.json"]
    assert (dest / "eval.py").is_file() and (dest / "results/s2/labels.json").is_file()
    assert not (dest / "results/s2/metrics.json").exists() and not (dest / "results/s1").exists()


def test_the_parents_results_no_run_named_leave_the_branch_and_rb_reconcile_reads_none(published, tmp_path_factory, capsys, monkeypatch):
    """Results folders the code's commit holds that no recorded run named (earlier runs, never recorded) are the
    parent's work too: a clone takes them out of its branch with what the runs wrote, so rb reconcile reports none of
    them as this record's. A folder that holds what a run reads stays, and so does a file that sits in no results
    folder, nor code a run kept (a script recorded as an artifact), however a path is spelled (`./results/...`)."""
    bundle, bare, commit = published
    code_dir = bundle.parent
    for name in ("r001", "r002", "r003"):
        Path(code_dir, "results", name).mkdir(parents=True)
        Path(code_dir, "results", name, "metrics.json").write_text('{"int8": {"top1": 0.75}}', encoding="utf-8")
    Path(code_dir, "results/r001/README.md").write_text("# r001\n\nAn early INT8 run.\n", encoding="utf-8")
    Path(code_dir, "results/plot.py").write_text("print('plot')\n", encoding="utf-8")
    # a folder where results land that holds code is code too: it stays, its numbers beside it
    Path(code_dir, "results/tools").mkdir(parents=True)
    Path(code_dir, "results/tools/summary.json").write_text('{"int8": {"top1": 0.75}}', encoding="utf-8")
    Path(code_dir, "results/tools/summarise.py").write_text("print('summary')\n", encoding="utf-8")
    Path(code_dir, "results/calib").mkdir(parents=True)
    Path(code_dir, "results/calib/images.json").write_text('{"images": 128}', encoding="utf-8")
    assert rb_in(code_dir, capsys, "resource", "add", "calib", "--type", "dataset", "--name", "INT8 calibration images", "--at", "local:./results/calib/images.json")[0] == 0
    # a resource no run read is the rerun's to get again too: where it is kept stays
    Path(code_dir, "results/labels").mkdir(parents=True)
    Path(code_dir, "results/labels/labels.json").write_text('{"labels": 1000}', encoding="utf-8")
    assert rb_in(code_dir, capsys, "resource", "add", "labels", "--type", "dataset", "--name", "ImageNet labels", "--at", "local:./results/labels/labels.json")[0] == 0
    subprocess.run([sys.executable, "eval.py", "--seed", "2", "--out", "results/s2"], cwd=code_dir, check=True)
    Path(code_dir, "results/s2/helper.py").write_text("print('help')\n", encoding="utf-8")
    assert rb_in(code_dir, capsys, "run", "add", "e1", "--from", "results/s2/metrics.json", "--set", "seed=2", "--input", "calib:calibration", "--artifact", "results/plot.py",
                 "--command", "python eval.py --seed 2 --out results/s2")[0] == 0
    git("add", "-A", cwd=code_dir)
    git("commit", "-qm", "seed 2 and earlier results", cwd=code_dir)
    git("push", "-q", str(bare), "HEAD:main", cwd=code_dir)
    code, env = rb_in(code_dir, capsys, "export", "--with-record", "--out", str(tmp_path_factory.mktemp("b3") / "b"))
    assert code == 0
    fresh = tmp_path_factory.mktemp("reader")
    # as on a slow machine (GitHub's Windows runners): git dates the record's commit seconds after the record's first write
    monkeypatch.setenv("GIT_COMMITTER_DATE", f"@{int(time.time()) + 60} +0000")
    code, env = rb_in(fresh, capsys, "clone", env.data["path"], "r")
    monkeypatch.delenv("GIT_COMMITTER_DATE")
    assert code == 0 and env.data["code"]["got"] is True
    dest = fresh / "r"
    assert env.data["code"]["taken_out"] == ["results/r001/", "results/r002/", "results/r003/", "results/s1/", "results/s2/metrics.json"]
    assert not any((dest / "results" / n).exists() for n in ("r001", "r002", "r003", "s1"))
    assert (dest / "results/plot.py").is_file() and (dest / "results/calib/images.json").is_file()
    assert (dest / "results/labels/labels.json").is_file()
    assert (dest / "results/tools/summarise.py").is_file() and (dest / "results/tools/summary.json").is_file()
    # a run's own folder that holds code gives up only what the runs named in it
    assert (dest / "results/s2/helper.py").is_file() and not (dest / "results/s2/metrics.json").exists()
    # git keeps them at the parent's commit, which the branch's record commit follows
    assert git("cat-file", "-e", "HEAD~1:results/r001/metrics.json", cwd=dest) == ""
    cwd = Path.cwd()
    os.chdir(dest)
    try:
        assert main(["reconcile"]) == 0
    finally:
        os.chdir(cwd)
    said = capsys.readouterr().out
    assert "r001" not in said and "r002" not in said and "r003" not in said, said
    code, env = rb_in(dest, capsys, "status")
    assert not any(i.get("code") == "unindexed_folder" and "r00" in str(i) for i in env.data["open"]), env.data["open"]


def test_where_the_code_cannot_be_got_rb_clone_says_it_in_its_own_words_then_gits(published, tmp_path_factory, capsys):
    """rb's own sentence first, naming the address; then git's own message after "git said:", where git failed;
    then the command that gets the code. The exit status is the clone's, as before."""
    bundle, bare, commit = published
    fresh = tmp_path_factory.mktemp("reader")
    missing = fresh / "no-such-repo.git"
    cwd = Path.cwd()
    os.chdir(fresh)
    try:
        code = main(["clone", str(bundle), "r", "--code", str(missing)])
        lines = capsys.readouterr().out.splitlines()
    finally:
        os.chdir(cwd)
    assert code == 0
    at = next(i for i, line in enumerate(lines) if line.startswith("rb clone could not get the code from "))
    assert lines[at].startswith(f"rb clone could not get the code from {missing.as_posix()}. git said: "), lines[at]
    assert len(lines[at]) > len(f"rb clone could not get the code from {missing.as_posix()}. git said: ")
    assert lines[at + 1] == f"With access, or a copy you can reach: rb clone {bundle.as_posix()} <new folder> --code <address of the code>"
    code, env = rb_in(fresh, capsys, "clone", str(bundle), "r2", "--code", str(missing))
    assert env.data["code"]["git_said"] and env.data["code"]["why"] == env.data["code"]["git_said"]
    # a repository git reaches that lacks the bundle's commit: rb's own reason, and no "git said"
    other = tmp_path_factory.mktemp("other") / "x.git"
    git("init", "-q", "--bare", str(other))
    os.chdir(fresh)
    try:
        code = main(["clone", str(bundle), "r3", "--code", str(other)])
        out = capsys.readouterr().out
    finally:
        os.chdir(cwd)
    assert code == 0
    assert f"rb clone could not get the code from {other.as_posix()}: it does not hold commit {commit[:12]}." in out.splitlines(), out
    assert "git said" not in out


def test_a_bundle_whose_export_saw_its_commit_was_not_at_origin_says_so_where_the_code_is_not_got(published, tmp_path_factory, capsys):
    """The export saw that origin does not hold the code's commit (repo.at_origin false): rb clone, from the bundle's
    own address, says the bundle's words for why, never git's, and the --code command after them."""
    bundle, bare, _ = published
    code_dir = bundle.parent
    while not (code_dir / ".rb").is_dir():
        code_dir = code_dir.parent
    git("fetch", "-q", "origin", cwd=code_dir)
    (code_dir / "eval.py").write_text(EVAL + "# a change not pushed\n", encoding="utf-8")
    git("commit", "-qam", "a change not pushed", cwd=code_dir)
    ahead = git("rev-parse", "HEAD", cwd=code_dir)
    code, env = rb_in(code_dir, capsys, "export", "--with-record", "--out", str(tmp_path_factory.mktemp("ahead") / "x"))
    assert code == 0
    later = Path(env.data["path"])
    repo = json.loads((later / "manifest.json").read_text(encoding="utf-8"))["repo"]
    assert repo["commit"] == ahead and repo["at_origin"] is False, repo
    fresh = tmp_path_factory.mktemp("reader")
    cwd = Path.cwd()
    os.chdir(fresh)
    try:
        code = main(["clone", str(later), "r"])
        lines = capsys.readouterr().out.splitlines()
    finally:
        os.chdir(cwd)
    assert code == 0
    words = f"rb clone could not get the code from {SERVED}: the bundle says its code's commit {ahead[:12]} is not at {SERVED}."
    assert words in lines, lines
    assert not any("git said" in line or "has no commit" in line for line in lines), lines
    assert lines[lines.index(words) + 1].startswith("With access, or a copy you can reach: rb clone ")


def test_a_file_the_records_checks_read_stays_in_the_branch_wherever_it_is(published, tmp_path_factory, capsys):
    """A results folder no run named leaves the clone's branch, but never a file a check of the record reads: a
    setting verified from a config there still verifies in the clone, and the rest of what no run named still goes."""
    bundle, bare, commit = published
    code_dir = bundle.parent
    Path(code_dir, "results/sweep").mkdir(parents=True)
    Path(code_dir, "results/sweep/config.yaml").write_text("optim:\n  lr: 0.1\n", encoding="utf-8")
    Path(code_dir, "results/r001").mkdir(parents=True)
    Path(code_dir, "results/r001/metrics.json").write_text('{"int8": {"top1": 0.75}}', encoding="utf-8")
    code, env = rb_in(code_dir, capsys, "spec", "set", "e1", "optim.lr", "--source", "results/sweep/config.yaml#optim.lr")
    assert code == 0, env.errors
    git("add", "-A", cwd=code_dir)
    git("commit", "-qm", "the sweep's config and an earlier result", cwd=code_dir)
    git("push", "-q", str(bare), "HEAD:main", cwd=code_dir)
    code, env = rb_in(code_dir, capsys, "export", "--with-record", "--out", str(tmp_path_factory.mktemp("b4") / "b"))
    assert code == 0
    fresh = tmp_path_factory.mktemp("reader")
    code, env = rb_in(fresh, capsys, "clone", env.data["path"], "r")
    assert code == 0 and env.data["code"]["got"] is True
    dest = fresh / "r"
    assert "results/r001/" in env.data["code"]["taken_out"]
    assert not any(t.startswith("results/sweep") for t in env.data["code"]["taken_out"]), env.data["code"]["taken_out"]
    assert (dest / "results/sweep/config.yaml").is_file()
    code, env = rb_in(dest, capsys, "spec", "verify", "e1", "optim.lr")
    assert code == 0, env.data
