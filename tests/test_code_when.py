"""EVIDENCE.md E2: the code is captured when the run starts.

One match gives both "watched" and the code: a run matched to an rb journal run entry (by --command, or as the newest
entry that wrote one of its files, the file as the command left it) names the code the command started with
(`produced.captured: start`), and rb run add says which command it came from. An edit between the run and its
recording leaves the start code the run's, and rb says the code changed after the run. A --tree or --commit that is not
the matched entry's code is given: a tree here is pinned under refs/rb/snapshots/, and one this repository lacks is
recorded as given with exit 0. "The exact code" is said nowhere.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from rbtest import as_the_user, command, environment

pytestmark = pytest.mark.skipif(shutil.which("git") is None or sys.platform == "win32", reason="needs git")
GIT = shutil.which("git")
TRAIN = 'import json, os, sys\nlr = 1.5\nos.makedirs(f"out/seed{sys.argv[1]}", exist_ok=True)\njson.dump({"int8": {"top1": 75 + lr}, "fp32": {"top1": 76.0}}, open(f"out/seed{sys.argv[1]}/metrics.json", "w"))\n'


@pytest.fixture
def root(tmp_path, monkeypatch):
    as_the_user(monkeypatch)
    cfg = tmp_path / "gitconfig"
    cfg.write_text("[user]\n\temail = ana@example.org\n\tname = ana\n[init]\n\tdefaultBranch = main\n", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(cfg))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("RB_ACTOR", "human:ana")
    r = tmp_path / "int8-resnet50"
    r.mkdir()
    git(r, "init", "-q")
    (r / "train.py").write_text(TRAIN, encoding="utf-8")
    rb(r, "init", "INT8 ResNet-50", "--no-agents")
    rb(r, "experiment", "add", "INT8 against FP32", "--id", "int8", "--baseline", "fp32", "--candidate", "int8")
    git(r, "add", "-A")
    git(r, "commit", "-qm", "the record")
    return r


def rb(cwd: Path, *argv: str) -> dict:
    r = subprocess.run([*command(), *argv, "--json"], cwd=cwd, env=environment(), capture_output=True, text=True, stdin=subprocess.DEVNULL)
    assert r.returncode == 0, (argv, r.returncode, r.stdout, r.stderr)
    return json.loads(r.stdout)


def say(cwd: Path, *argv: str) -> str:
    return subprocess.run([*command(), *argv], cwd=cwd, env=environment(), capture_output=True, text=True, stdin=subprocess.DEVNULL).stdout


def git(cwd: Path, *argv: str) -> str:
    return subprocess.run([GIT, *argv], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def edit(root: Path, lr: str) -> None:
    (root / "train.py").write_text(TRAIN.replace("lr = 1.5", f"lr = {lr}"), encoding="utf-8")


def test_an_edit_between_the_run_and_its_recording_keeps_the_code_the_command_started_with(root):
    edit(root, "2.5")                               # lr * 2.5 runs
    subprocess.run([*command(), "journal", "run", "--", sys.executable, "train.py", "1"], cwd=root, env=environment(), check=True, capture_output=True)
    entry = [e for e in rb(root, "journal")["data"]["entries"] if e.get("op") == "run"][-1]
    start = entry["code"]["tree"]
    edit(root, "3.0")                               # lr * 3.0 is what the checkout holds when it is recorded
    r = rb(root, "run", "add", "int8", "--from", "out/seed1/metrics.json")
    run = r["data"]["object"]
    p = run["receipt"]["produced"]
    assert (p["captured"], p["tree"], p["from"], p["kept"]) == ("start", start, "journal", "whole")
    assert run["receipt"]["attached"]["tree"] != start
    out = say(root, "show", run["id"])
    assert f"The code when the run started: git tree {start[:12]}." in out
    assert f"Its code is from the start of: {sys.executable} train.py 1 (rb journal run, " in out
    changed = [w for w in r["data"]["warnings"] if w.startswith("The code changed after the run")]
    assert changed == [f"The code changed after the run: rb keeps the code the command started with (git tree {start[:12]}). Your checkout now differs in 1 file: train.py."]
    assert "exact code" not in out.lower()


def test_the_command_given_matches_the_same_entry(root):
    edit(root, "2.5")
    cmd = [sys.executable, "train.py", "2"]
    subprocess.run([*command(), "journal", "run", "--", *cmd], cwd=root, env=environment(), check=True, capture_output=True)
    run = rb(root, "run", "add", "int8", "--from", "out/seed2/metrics.json", "--command", " ".join(cmd))["data"]["object"]
    assert run["receipt"]["produced"]["captured"] == "start" and run["receipt"]["command_seen"]["by"] == "journal"


def test_a_typed_tree_this_repository_lacks_is_recorded_as_given_with_exit_0(root):
    tree = "0123456789abcdef0123456789abcdef01234567"
    r = rb(root, "run", "add", "int8", "top1=75.1", "--variant", "int8", "--tree", tree, "--produced-at", "2026-11-01")
    p = r["data"]["object"]["receipt"]["produced"]
    assert (p["captured"], p["tree"], p["here"]) == ("given", tree, False)
    shown = say(root, "show", r["data"]["object"]["id"])
    assert f"The code as given: git tree {tree[:12]}, which this repository does not have." in shown
    # a tree given alone names no commit: rb show offers no git diff against one (S7 shots)
    assert f"  code that made it: git tree {tree[:12]} (as given)\n" in shown and "git diff  " not in shown, shown


def test_a_given_tree_here_is_pinned_as_rb_pins_its_own(root):
    tree = git(root, "rev-parse", "HEAD^{tree}")
    r = rb(root, "run", "add", "int8", "top1=75.1", "--variant", "int8", "--tree", tree, "--produced-at", "2026-11-01")
    p = r["data"]["object"]["receipt"]["produced"]
    assert p["captured"] == "given" and "here" not in p
    assert git(root, "for-each-ref", "--format=%(objectname)", f"refs/rb/snapshots/{tree}") == tree
    assert f"The code as given: git tree {tree[:12]}." in say(root, "show", r["data"]["object"]["id"])


def test_a_run_card_says_when_its_code_was_captured_on_rb_ui_and_a_page(root, tmp_path):
    """rb ui's run card and a page's say the start and given lines rb show says; a page says a tree its repository
    lacks in its own form, and neither says how your checkout differs (Reproduce says that where it compares one)."""
    from rbtest import object_card
    edit(root, "2.5")
    subprocess.run([*command(), "journal", "run", "--", sys.executable, "train.py", "1"], cwd=root, env=environment(), check=True, capture_output=True)
    start = [e for e in rb(root, "journal")["data"]["entries"] if e.get("op") == "run"][-1]["code"]["tree"]
    edit(root, "3.0")
    ran = rb(root, "run", "add", "int8", "--from", "out/seed1/metrics.json")["data"]["object"]["id"]
    tree = "0123456789abcdef0123456789abcdef01234567"
    given = rb(root, "run", "add", "int8", "top1=75.1", "--variant", "int8", "--tree", tree, "--produced-at", "2026-11-01")["data"]["object"]["id"]
    facts = object_card(ran, root)["facts"]
    assert f"The code when the run started: git tree {start[:12]}." in facts
    assert any(f.startswith(f"Its code is from the start of: {sys.executable} train.py 1 (rb journal run, ") for f in facts)
    assert not [f for f in facts if f.startswith("The code changed after the run")]
    assert f"The code as given: git tree {tree[:12]}, which this repository does not have." in object_card(given, root)["facts"]
    rb(root, "page", "--out", str(tmp_path / "p.html"))
    import html as h
    import re
    page = h.unescape(re.sub(r"<[^>]+>", "", (tmp_path / "p.html").read_text(encoding="utf-8")))
    assert f"The code when the run started: git tree {start[:12]}." in page
    assert f"The code as given: git tree {tree[:12]}, which the repository this page was made from did not have." in page
    assert "which this repository does not have" not in page and "The code changed after the run" not in page


def test_an_sdk_run_block_names_the_code_it_started_with(root):
    """`with exp.run()` captures the code when the block starts: an edit made inside the block, before it ends, is not
    the run's code; exp.record stays the code when it was recorded."""
    import rabbit_brain as rbsdk
    edit(root, "2.5")
    exp = rbsdk.open(root).experiment("int8")
    with exp.run(variant="int8") as run:
        edit(root, "3.0")                           # the checkout changes while the block runs
        run.log(top1=77.5)
    p = run.record.receipt.produced if hasattr(run.record.receipt, "produced") else run.record.receipt["produced"]
    p = p if isinstance(p, dict) else p.model_dump()
    assert (p["captured"], p["from"], p["kept"]) == ("start", "sdk", "whole"), p
    attached = run.record.receipt.attached
    attached = attached if isinstance(attached, dict) else attached.model_dump()
    assert p["tree"] != attached["tree"]
    out = say(root, "show", run.record.id)
    assert f"The code when the run started: git tree {p['tree'][:12]}." in out
    assert "Its code is from the start of" not in out
    later = exp.record({"int8.top1": 77.6}, produced_at="2026-11-01")
    lp = later.receipt.produced if isinstance(later.receipt.produced, dict) else later.receipt.produced.model_dump()
    assert lp.get("captured") is None


def test_an_sdk_run_block_that_raises_leaves_no_ref_and_one_that_records_pins_its_start(root):
    """review5 L2: the block's start keeps no ref under refs/rb/snapshots/; its run pins the start code when it is
    recorded. A block that raises records nothing and leaves no ref behind."""
    import rabbit_brain as rbsdk
    refs = lambda: set(git(root, "for-each-ref", "--format=%(refname)", "refs/rb/snapshots/").split())
    edit(root, "2.5")
    exp = rbsdk.open(root).experiment("int8")
    before = refs()
    with pytest.raises(RuntimeError):
        with exp.run(variant="int8") as run:
            raise RuntimeError("the evaluation failed")
    assert refs() == before, refs() - before
    with exp.run(variant="int8") as run:
        edit(root, "3.0")
        run.log(top1=77.5)
    p = run.record.receipt.produced if isinstance(run.record.receipt.produced, dict) else run.record.receipt.produced.model_dump()
    assert p["captured"] == "start" and f"refs/rb/snapshots/{p['tree']}" in refs()


def test_a_tree_git_gc_removed_reads_as_gone_in_the_clone_that_recorded_the_run(root):
    """S-c (MP-1110-2007): in the clone that recorded the run, a start tree no ref keeps says so, as the tree after the
    run does; once git gc removes the trees, rb show <run> and the experiment's code row say the code is gone from
    this clone, never that it was kept "in the clone that recorded the run". Fails on e47ada5."""
    edit(root, "2.5")
    subprocess.run([*command(), "journal", "run", "--", sys.executable, "train.py", "1"], cwd=root, env=environment(), check=True, capture_output=True)
    edit(root, "3.0")
    run = rb(root, "run", "add", "int8", "--from", "out/seed1/metrics.json")["data"]["object"]
    start = run["receipt"]["produced"]["tree"][:12]
    after = run["receipt"]["attached"]["tree"][:12]
    for ref in git(root, "for-each-ref", "--format=%(refname)", "refs/rb/").split():
        git(root, "update-ref", "-d", ref)
    out = say(root, "show", run["id"])
    assert f"git tree {start} is in this repository, but no ref keeps it, so git gc may remove it." in out, out
    git(root, "reflog", "expire", "--expire=now", "--all")
    git(root, "gc", "-q", "--prune=now")
    assert subprocess.run([GIT, "cat-file", "-e", f"{start}^{{tree}}"], cwd=root, capture_output=True).returncode != 0
    out = say(root, "show", run["id"])
    assert "in the clone that recorded the run" not in out, out
    assert f"This run's code is gone from this clone: rb kept the code its command started with here as git tree {start}, and git no longer has it" in out, out
    assert f"which rb kept here as git tree {after}, and git no longer has it" in out, out
    row = say(root, "show", "int8")
    assert "in the clone that recorded it" not in row and "code gone for 1 run: rb kept it in this clone as git tree" in row, row
    # the finding's evidence line and its note say it as the run does (MP-1110-2303's later; fails on f73697b)
    rb(root, "criterion", "add", "INT8 loses at most ten points", "-e", "int8", "--metric", "change.top1", "--at-least", "-10", "--id", "c9")
    f = rb(root, "show", "c9")["data"]["finding"]
    assert "1 run's code gone from this clone" in f["says"]["evidence"] and "not in this clone" not in f["says"]["evidence"], f["says"]
    notes = " ".join(c["text"] for c in f["caveats"])
    assert f"run {run['id']}'s code is gone from this clone" in notes and "is not in this clone" not in notes, notes


def test_inquiry_integrity_ends_with_one_full_stop_and_forget_of_one_record_says_its_file(root):
    """S-a and S-e (MP-1110-2007): the inquiry's integrity ends "then." once, never "then.."; rb doctor --forget of one
    record says "Its file is set aside", never "Their files". Fails on e47ada5."""
    rb(root, "question", "add", "Does INT8 cost accuracy?", "--id", "q-acc")
    rb(root, "metric", "add", "top1", "--maximize")
    rb(root, "criterion", "add", "INT8 loses at most one point", "-e", "int8", "-q", "q-acc", "--metric", "change.top1", "--at-least", "-1", "--id", "c1")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "the rule")
    subprocess.run([*command(), "journal", "run", "--", sys.executable, "train.py", "1"], cwd=root, env=environment(), check=True, capture_output=True)
    rb(root, "run", "add", "int8", "--from", "out/seed1/metrics.json")
    git(root, "checkout", "--", ".")
    for argv in (("status",), ("context",), ("show", "q-acc")):
        said = say(root, *argv)
        assert "then.." not in said and "again then." in said, (argv, said)
    from rbtest import object_card
    card = json.dumps(object_card("c1", root))
    assert "then.." not in card and "again then." in card, card
    page = root.parent / "p.html"
    subprocess.run([*command(), "page", "--out", str(page)], cwd=root, env=environment(), check=True, capture_output=True)
    assert "then.." not in page.read_text(encoding="utf-8")
    # one record taken out: the run (its log line holds one write)
    forgot = say(root, "doctor", "--forget")
    assert "Its file is set aside as <file>.outside-rb." in forgot and "Their files" not in forgot, forgot


def test_the_code_changed_after_the_run_only_where_its_code_did(root):
    """S-b (MP-1110-2007): the tree when a run is recorded differs from the tree its command started with by the run's
    own outputs, another watched run's outputs and Python's __pycache__; none of them is code, so rb says "The code
    changed after the run" only where a file of code changed, and names only files of code. Fails on e47ada5."""
    (root / "helper.py").write_text("SCALE = 1.0\n", encoding="utf-8")
    (root / "train.py").write_text("import helper\n" + TRAIN, encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "helper")
    (root / "train.py").write_text("import helper\n" + TRAIN.replace("lr = 1.5", "lr = 2.5"), encoding="utf-8")  # a dirty start
    for seed in ("1", "2"):
        subprocess.run([*command(), "journal", "run", "--", sys.executable, "train.py", seed], cwd=root, env=environment(), check=True, capture_output=True)
    assert (root / "__pycache__").is_dir()
    r = rb(root, "run", "add", "int8", "--from", "out/seed1/metrics.json")
    run = r["data"]["object"]
    assert not [w for w in r["data"]["warnings"] if w.startswith("The code changed after the run")], r["data"]["warnings"]
    # git checkout -- . puts the code back after the recording: the code did not change after the run
    git(root, "checkout", "--", "train.py")
    out = say(root, "show", run["id"])
    assert "The code changed after the run" not in out, out
    # a real change between the run and its recording is said, naming only the file of code
    subprocess.run([*command(), "journal", "run", "--", sys.executable, "train.py", "3"], cwd=root, env=environment(), check=True, capture_output=True)
    (root / "helper.py").write_text("SCALE = 2.0\n", encoding="utf-8")
    r = rb(root, "run", "add", "int8", "--from", "out/seed3/metrics.json")
    changed = [w for w in r["data"]["warnings"] if w.startswith("The code changed after the run")]
    assert len(changed) == 1 and changed[0].endswith("Your checkout now differs in 1 file: helper.py."), changed
