"""A project is not a directory: a git worktree nested in the checkout, with no record of its own because the record
was not committed when it forked, writes to the checkout's record, and what it records is its own code.

Promises pinned here:

- A run or an analysis recorded from such a worktree keeps that worktree's commit, branch, uncommitted changes and
  exact tree (what git says in the worktree, and what an independent write-tree of it gives), and the worktree's path,
  on its receipt and its log line. The SDK opened there, and rb mcp started there, do the same.
- A receipt and a log line from a single checkout carry exactly what they carried before.
- Another worktree nested in the checkout is not the checkout's code, and a repository of its own inside it (a
  submodule moved on) is said as one, never as a file over 10 MB.
- A run's exact code is said to be kept in this repository only where git here has its tree.
- A read leaves git's index as it was.
- The nested worktree says once, on rb status and rb context, that it writes to the checkout's record, and rb status
  --json gives the checkout it ran in. A worktree outside the checkout with no record is told where the project's
  record is and how to bring it there, in words that bring it when followed (a detached checkout, an ignored .rb/),
  rb reconcile and rb compile there point at it (the main checkout is never pointed at a linked worktree's record), rb
  init there is refused with the same words unless --new-project, and rb init in a nested worktree is still refused.
- A journal directory a worktree name git gives again would share is set aside, so main's reconcile still finds what
  the first worktree printed while the second one exists, and a worktree git names main keeps a journal apart from
  the main checkout's. A worktree moved (git worktree move, relative links, a project folder moved and repaired)
  keeps its journal: main finds nothing of it, and it reads its own runs as its own.
"""
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

import rabbit_brain
from rabbit_brain.models import Envelope
from rbtest import Mcp, as_the_user, main, rbyaml

HAS_GIT = shutil.which("git") is not None
pytestmark = pytest.mark.skipif(not HAS_GIT, reason="worktrees are git's")


@pytest.fixture
def repo(workdir, monkeypatch, tmp_path_factory, capsys):
    as_the_user(monkeypatch)
    monkeypatch.setenv("RB_ACTOR", "human:ana")
    cfg = tmp_path_factory.mktemp("gitconfig") / "config"
    cfg.write_text("[user]\n\temail = ana@example.org\n\tname = ana\n[init]\n\tdefaultBranch = main\n[protocol \"file\"]\n\tallow = always\n",
                   encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(cfg))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    git("init", "-q")
    Path("train.py").write_text("lr = 0.1\n", encoding="utf-8")
    Path("README.md").write_text("# INT8 ResNet-50 on ImageNet\n", encoding="utf-8")
    git("add", "train.py", "README.md")
    git("commit", "-qm", "start")
    rb(capsys, "init", "INT8 ResNet-50 on ImageNet", "--no-agents")
    rb(capsys, "experiment", "add", "int8 against fp32", "--id", "e1", "--baseline", "fp32", "--candidate", "int8")
    return workdir.resolve()                                       # the record is not committed


def git(*args, cwd=None, env=None) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True, env=env).stdout


def rb(capsys, *argv) -> Envelope:
    code = main([*argv, "--json"])
    env = Envelope.model_validate_json(capsys.readouterr().out)
    assert code == 0 and env.ok, (argv, env.errors)
    return env


def refused(capsys, *argv) -> dict:
    code = main([*argv, "--json"])
    env = json.loads(capsys.readouterr().out)
    assert code == 2 and not env["ok"], (argv, env)
    return env["errors"][0]


def nested(repo: Path, name: str = "b", branch: str = "exp-b") -> Path:
    wt = repo / ".worktrees" / name
    git("worktree", "add", "-q", "-b", branch, str(wt), cwd=repo)
    return wt


def slashed(p: Path) -> str:
    return p.resolve().as_posix()


def receipt(repo: Path, run_id: str) -> dict:
    [path] = (repo / ".rb" / "experiments").glob(f"*/runs/{run_id}.yaml")
    return rbyaml.loads(path.read_text(encoding="utf-8"))["receipt"]


def log_line(repo: Path, op: str, id_: str) -> dict:
    lines = [json.loads(line) for line in (repo / ".rb" / "log.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    return [entry for entry in lines if entry.get("op") == op and entry.get("id") == id_][-1]


def write_tree(top: Path) -> str:
    """The tree of the worktree's files as they are, written through an index of its own: rb's snapshot, done apart."""
    index = top.parent / f"{top.name}-index"
    env = {**os.environ, "GIT_INDEX_FILE": str(index)}
    try:
        git("read-tree", "HEAD", cwd=top, env=env)
        git("add", "-A", cwd=top, env=env)
        return git("write-tree", cwd=top, env=env).strip()
    finally:
        index.unlink(missing_ok=True)


# ---------------------------------------------------------------- the code a run keeps


def test_a_run_from_a_nested_worktree_keeps_that_worktree_s_code(repo, capsys, monkeypatch):
    wt = nested(repo)
    (wt / "train.py").write_text("lr = 0.25\n", encoding="utf-8")
    (wt / "augment.py").write_text("flip = True\n", encoding="utf-8")
    monkeypatch.chdir(wt)
    run_id = rb(capsys, "run", "add", "e1", "fp32.top1=0.761", "int8.top1=0.758").data["object"]["id"]
    assert not (wt / ".rb").exists()                               # it wrote the checkout's record
    att = receipt(repo, run_id)["attached"]
    assert att["commit"] == git("rev-parse", "HEAD", cwd=wt).strip()
    assert att["branch"] == git("symbolic-ref", "--short", "HEAD", cwd=wt).strip() == "exp-b"
    assert att["dirty"] is True and git("status", "--porcelain", cwd=wt).splitlines() == [" M train.py", "?? augment.py"]
    assert att["tree"] == write_tree(wt) != git("rev-parse", "HEAD^{tree}", cwd=repo).strip()
    assert att["worktree"] == {"path": slashed(wt)}
    assert log_line(repo, "record", run_id)["worktree"] == {"path": slashed(wt)}
    # the analysis receipt is asked in the same place
    an = rb(capsys, "analysis", "add", "e1", "--compare", "int8/fp32:top1=-0.003[-0.005,-0.001]", "--method", "paired bootstrap",
            "--unit", "seed", "--units", "3").data["object"]
    att = an["receipt"]["attached"]
    assert (att["commit"], att["branch"], att["dirty"]) == (git("rev-parse", "HEAD", cwd=wt).strip(), "exp-b", True)
    assert att["worktree"] == {"path": slashed(wt)}


def test_the_same_numbers_from_a_nested_worktree_are_a_run_of_its_own(repo, capsys, monkeypatch):
    """Numbers given in a nested worktree, the same as a run the checkout recorded, are that worktree's run, with its
    code; given again there, they are the run already recorded there."""
    argv = ("run", "add", "e1", "fp32.top1=0.5", "int8.top1=0.4")
    here = rb(capsys, *argv).data["object"]["id"]
    wt = nested(repo, "e", "exp")
    (wt / "train.py").write_text("lr = 0.25\n", encoding="utf-8")
    monkeypatch.chdir(wt)
    there = rb(capsys, *argv).data["object"]["id"]
    assert there != here
    att = receipt(repo, there)["attached"]
    assert (att["branch"], att["dirty"], att["worktree"]) == ("exp", True, {"path": slashed(wt)})
    assert main([*argv]) == 0
    assert f"Already recorded as {there} " in capsys.readouterr().out


def test_the_sdk_opened_in_a_nested_worktree_records_that_worktree_s_code(repo, monkeypatch):
    wt = nested(repo)
    (wt / "train.py").write_text("lr = 0.25\n", encoding="utf-8")
    st = rabbit_brain.open(wt)
    assert st.root == repo and st.here == wt
    with pytest.warns(rabbit_brain.RBWarning, match="refs/rb/snapshots/"):
        run = st.experiment("e1").record({"fp32.top1": 0.761, "int8.top1": 0.758})
    att = receipt(repo, run.id)["attached"]
    assert (att["branch"], att["dirty"], att["worktree"]) == ("exp-b", True, {"path": slashed(wt)})
    assert rabbit_brain.open(repo).here is None                    # the checkout's own record, opened in it
    # opened by the record's own path from the worktree, it still keeps the worktree's code: the caller works there
    monkeypatch.chdir(wt)
    by_path = rabbit_brain.open(repo)
    assert by_path.root == repo and by_path.here == wt
    later = by_path.experiment("e1").record({"fp32.top1": 0.763, "int8.top1": 0.757})   # the tree kept already
    att = receipt(repo, later.id)["attached"]
    assert (att["branch"], att["dirty"], att["worktree"]) == ("exp-b", True, {"path": slashed(wt)})
    monkeypatch.chdir(repo)
    # rb mcp started there writes the same way
    server = Mcp("claude-code", root=wt)
    try:
        server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "clientInfo": {"name": "t"}}})
        server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"})
        got = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                             "params": {"name": "run_add", "arguments": {"experiment": "e1", "metrics": {"fp32.top1": 0.762, "int8.top1": 0.759}}}})
    finally:
        server.close()
    run_id = got["result"]["structuredContent"]["data"]["object"]["id"]
    att = receipt(repo, run_id)["attached"]
    assert (att["branch"], att["dirty"], att["worktree"]) == ("exp-b", True, {"path": slashed(wt)})


def test_a_result_found_in_the_checkout_is_recorded_from_there_whatever_directory_asks(repo, capsys, monkeypatch):
    """rb reconcile proposes a file it found in the record's checkout with its path from there: asked in a nested
    worktree that holds a file of the same name, the line it prints starts with the cd to the checkout (run_in in
    --json and the SDK), and --apply records the checkout's file with the checkout's code, never the worktree's."""
    rb(capsys, "metric", "add", "top1", "--maximize")
    rb(capsys, "criterion", "add", "int8 holds top1", "-e", "e1", "--metric", "change.top1", "--at-least", "-0.01", "--id", "c1")
    wt = nested(repo)
    for top, nums in ((repo, {"int8": {"top1": 0.761}, "fp32": {"top1": 0.77}}), (wt, {"int8": {"top1": 0.5}, "fp32": {"top1": 0.9}})):
        (top / "out").mkdir()
        (top / "out" / "metrics.json").write_text(json.dumps(nums), encoding="utf-8")
    rb(capsys, "journal", "scan")
    main(["reconcile"])
    assert "\n    rb run add e1 --from out/metrics.json\n" in capsys.readouterr().out      # from the checkout, as before
    monkeypatch.chdir(wt)
    [found] = rb(capsys, "reconcile").data["found"]
    assert found["argv"] == ["run", "add", "e1", "--from", "out/metrics.json"] and found["run_in"] == slashed(repo)
    main(["reconcile"])
    assert f"    cd {slashed(repo)} && rb run add e1 --from out/metrics.json\n" in capsys.readouterr().out
    assert [f.run_in for f in rabbit_brain.open(wt).found()[0]] == [slashed(repo)]
    rb(capsys, "reconcile", "--apply")
    [run] = rb(capsys, "status").data["runs"]
    shown = rb(capsys, "show", run["id"]).data["object"]
    assert shown["metrics"] == {"int8.top1": 0.761, "fp32.top1": 0.77} and [f["path"] for f in shown["files"]] == ["out/metrics.json"]
    assert "worktree" not in shown["receipt"]["attached"] and shown["receipt"]["attached"]["branch"] == "main"


def test_rb_status_says_once_where_a_finding_s_runs_were_recorded(repo, capsys, monkeypatch):
    """rb status, the user's own screen as rb open is, says once under a finding that counts a run recorded in a nested
    worktree where it was recorded, by the worktree's branch and path as git lists it (`observed` in --json, "" for a
    finding that counts none)."""
    rb(capsys, "metric", "add", "top1", "--maximize")
    rb(capsys, "criterion", "add", "int8 holds top1", "-e", "e1", "--metric", "change.top1", "--at-least", "-0.01", "--id", "c1")
    rb(capsys, "run", "add", "e1", "fp32.top1=0.761", "int8.top1=0.758")
    assert [f["observed"] for f in rb(capsys, "status").data["findings"]] == [""]
    wt = nested(repo)
    (wt / "train.py").write_text("lr = 0.25\n", encoding="utf-8")
    monkeypatch.chdir(wt)
    rb(capsys, "run", "add", "e1", "fp32.top1=0.760", "int8.top1=0.757")
    listed = [b.split("\n")[0].split(" ", 1)[1] for b in git("worktree", "list", "--porcelain").split("\n\n") if "refs/heads/exp-b" in b]
    note = f"1 of 2 runs was recorded in exp-b · {listed[0]}"
    for where in (wt, repo):
        monkeypatch.chdir(where)
        assert [f["observed"] for f in rb(capsys, "status").data["findings"]] == [note]
        main(["status"])
        assert capsys.readouterr().out.count(note) == 1


def test_a_finding_whose_runs_were_all_recorded_in_a_worktree_says_the_only_run_or_both(repo, capsys, monkeypatch):
    """rb status said 1 of 1 run was recorded and 2 of 2 runs were recorded in a worktree: one counted run reads as the
    only run, two as both."""
    rb(capsys, "metric", "add", "top1", "--maximize")
    rb(capsys, "criterion", "add", "int8 holds top1", "-e", "e1", "--metric", "change.top1", "--at-least", "-0.01", "--id", "c1")
    wt = nested(repo)
    monkeypatch.chdir(wt)
    listed = listed_path("exp-b")
    said = []
    for fp32, int8 in ((0.761, 0.758), (0.760, 0.757)):
        rb(capsys, "run", "add", "e1", f"fp32.top1={fp32}", f"int8.top1={int8}")
        said += [f["observed"] for f in rb(capsys, "status").data["findings"]]
    assert said == [f"the only run was recorded in exp-b · {listed}", f"both runs were recorded in exp-b · {listed}"]


def listed_path(branch: str) -> str:
    """A worktree's path as git lists it, by its branch."""
    return [b.split("\n")[0].split(" ", 1)[1] for b in git("worktree", "list", "--porcelain").split("\n\n") if f"refs/heads/{branch}" in b][0]


def code_note_fixture(repo, capsys, monkeypatch) -> dict:
    """c1 over a run in the main checkout, on its commit with no changes, and a run in exp-b (nested), on the same commit
    with train.py changed: the reading's runs did not all run the same code."""
    rb(capsys, "metric", "add", "top1", "--maximize")
    rb(capsys, "criterion", "add", "int8 holds top1", "-e", "e1", "--metric", "change.top1", "--at-least", "-0.01", "--id", "c1")
    here = rb(capsys, "run", "add", "e1", "fp32.top1=0.761", "int8.top1=0.758").data["object"]["id"]
    wt = nested(repo)
    (wt / "train.py").write_text("lr = 0.25\n", encoding="utf-8")
    monkeypatch.chdir(wt)
    there = rb(capsys, "run", "add", "e1", "fp32.top1=0.760", "int8.top1=0.757").data["object"]["id"]
    monkeypatch.chdir(repo)
    return {"wt": wt, "here": here, "there": there, "commit": receipt(repo, there)["attached"]["commit"]}


def test_a_reading_whose_runs_ran_other_code_says_which_from_the_receipts(repo, capsys, monkeypatch):
    """Where the runs a reading counts were recorded in more than one place and did not all run the same code, rb says
    so from their receipts, in its own words: on rb status as one quiet line under the finding (`other_code` in
    --json), on rb open and rb ui with the place in short, beside the note on where they were recorded, on the
    comparison's cell too, and on a page (rb page, rb export) by commit alone, with no branch or path."""
    import urllib.request
    from rbtest import Open
    fx = code_note_fixture(repo, capsys, monkeypatch)
    at = f"{fx['commit'][:12]}, with uncommitted changes"
    note = f"1 of 2 runs ran other code: exp-b · {listed_path('exp-b')} at {at}"
    [f] = rb(capsys, "status").data["findings"]
    assert f["other_code"] == note and f["observed"].startswith("1 of 2 runs was recorded in exp-b · ")
    main(["status"])
    out = capsys.readouterr().out
    assert out.count(f"          · {note}\n") == 1 and out.index(f["observed"]) < out.index(note)
    server = Open(repo)
    try:
        def fetch(route, raw=False):
            body = urllib.request.urlopen(f"{server.url}{route}", timeout=60).read().decode("utf-8")
            return body if raw else json.loads(body)
        state, card, exp_card, page = fetch("/state.json"), fetch("/card.json?ref=o:c1")["card"], fetch("/card.json?ref=o:e1")["card"], fetch("/page.html", True)
    finally:
        server.close()
    short = f"1 of 2 runs ran other code: exp-b · .worktrees/b at {at}"
    [line] = [x for b in state["home"]["briefs"] for e in b["experiments"] for x in e["findings"] if x["criterion_id"] == "c1"]
    assert line["other_code"] == short and short in card["facts"] and f"c1: {short}" in exp_card["facts"]
    from rabbit_brain.tui import object_card
    assert short in object_card(repo, "c1").facts                           # rb ui's card for the criterion
    [eb] = [e for b in state["home"]["briefs"] for e in b["experiments"] if e["id"] == "e1"]
    [cand] = [r for r in eb["table"]["rows"] if r["variant"] == "int8"]
    assert cand["metrics"]["top1"]["other_code"] == short                  # the change against the baseline: both runs
    # a page names no place: commits alone
    paged = f"1 of 2 runs ran other code: commit {at}"
    assert paged in page and "exp-b" not in page and ".worktrees" not in page
    rb(capsys, "page", "--out", str(repo.parent / "sent.html"))
    sent = (repo.parent / "sent.html").read_text(encoding="utf-8")
    assert paged in sent and "exp-b" not in sent and ".worktrees" not in sent
    rb(capsys, "export", "--out", str(repo.parent / "exported"))
    exported = (repo.parent / "exported" / "page.html").read_text(encoding="utf-8")
    assert paged in exported and "exp-b" not in exported and ".worktrees" not in exported
    assert "other_code" not in json.dumps(rb(capsys, "compare", "e1").data)    # rb compare's text and --json as before


def test_the_notes_on_a_reading_say_a_worktree_removed_since_as_its_runs_do(repo, capsys, monkeypatch):
    """Once the worktree a counted run was recorded in is removed, the finding's note on where its runs were recorded
    and the note on the code they ran say so after the place, as the run's own line does, on rb status and on rb
    open; a page, which names no place, says the same as before."""
    import urllib.request
    from rbtest import Open
    fx = code_note_fixture(repo, capsys, monkeypatch)
    listed = listed_path("exp-b")
    git("worktree", "remove", "--force", str(fx["wt"]), cwd=repo)
    at = f"{fx['commit'][:12]}, with uncommitted changes"
    [f] = rb(capsys, "status").data["findings"]
    assert f["observed"] == f"1 of 2 runs was recorded in exp-b · {listed} (removed since)"
    assert f["other_code"] == f"1 of 2 runs ran other code: exp-b · {listed} (removed since) at {at}"
    server = Open(repo)
    try:
        def fetch(route):
            return urllib.request.urlopen(f"{server.url}{route}", timeout=60).read().decode("utf-8")
        state, page = json.loads(fetch("/state.json")), fetch("/page.html")
    finally:
        server.close()
    [line] = [x for b in state["home"]["briefs"] for e in b["experiments"] for x in e["findings"] if x["criterion_id"] == "c1"]
    [run] = [r for r in state["home"]["runs"] if r["run_id"] == fx["there"]]
    assert run["observed_in"]["label_short"] == f"recorded in exp-b · .worktrees/b at {at} (removed since)"
    assert line["observed_short"] == "1 of 2 runs was recorded in exp-b · .worktrees/b (removed since)" and line["observed_places"] == []
    assert line["other_code"] == f"1 of 2 runs ran other code: exp-b · .worktrees/b (removed since) at {at}"
    assert f"1 of 2 runs ran other code: commit {at}" in page and "removed since" not in page


def test_a_run_made_before_it_was_recorded_is_never_said_to_run_other_code(repo, capsys, monkeypatch):
    """A run from earlier work recorded in a worktree with changed code keeps the recording checkout's code, so rb
    counts it as neither other code nor the same: with such a run among a reading's runs, rb says nothing about code,
    even where another of them ran other code. The note on where they were recorded stays as it is."""
    fx = code_note_fixture(repo, capsys, monkeypatch)
    [f] = rb(capsys, "status").data["findings"]
    assert f["other_code"].startswith("1 of 2 runs ran other code: exp-b · ")
    wt = nested(repo, "c", "exp-c")
    (wt / "train.py").write_text("lr = 0.5\n", encoding="utf-8")
    monkeypatch.chdir(wt)
    earlier = rb(capsys, "run", "add", "e1", "fp32.top1=0.762", "int8.top1=0.759", "--produced-at", "2026-01-05T10:00:00Z").data["object"]["id"]
    monkeypatch.chdir(repo)
    assert receipt(repo, earlier)["attached"]["dirty"] is True                  # the recording checkout's changed code
    [f] = rb(capsys, "status").data["findings"]
    assert f["other_code"] == "" and f["observed"].startswith("2 of 3 runs were recorded elsewhere: 1 in exp-b · ")
    main(["status"])
    assert "other code" not in capsys.readouterr().out


def test_a_run_whose_code_rb_cannot_read_leaves_the_code_unsaid(repo, capsys, monkeypatch):
    """A run whose receipt says it was made at another commit than the checkout it was recorded in, or names a commit
    this clone does not have, or uncommitted changes with no tree that keeps them, or a tree with files left out of it
    (as a receipt edited by hand and adopted can), holds no code rb can read: with one among a reading's runs, rb says
    nothing about code, even where another of them ran other code."""
    fx = code_note_fixture(repo, capsys, monkeypatch)
    wt = nested(repo, "c", "exp-c")
    (wt / "train.py").write_text("lr = 0.5\n", encoding="utf-8")
    git("commit", "-qam", "a larger rate", cwd=wt)
    monkeypatch.chdir(wt)
    rb(capsys, "run", "add", "e1", "fp32.top1=0.762", "int8.top1=0.759")
    monkeypatch.chdir(repo)
    [path] = (repo / ".rb" / "experiments").glob(f"*/runs/{fx['there']}.yaml")
    kept = path.read_text(encoding="utf-8")

    def adopted(text: str) -> None:
        path.write_text(text, encoding="utf-8")
        rb(capsys, "doctor", "--adopt", "--why", "the receipt as another clone wrote it")
    for edit in (lambda r: r["produced"].update(commit="0" * 40), lambda r: r["attached"].update(commit="1" * 40),
                 lambda r: r["attached"].pop("tree"), lambda r: r["attached"].update(left_out=1)):
        doc = rbyaml.loads(kept)
        edit(doc["receipt"])
        adopted(rbyaml.dumps(doc))
        [f] = rb(capsys, "status").data["findings"]
        assert f["other_code"] == "" and f["observed"].startswith("2 of 3 runs were recorded elsewhere: 1 in exp-b · ")
    adopted(kept)
    [f] = rb(capsys, "status").data["findings"]
    assert f["other_code"].startswith("2 of 3 runs ran other code: 1 in exp-b · ")


def test_with_no_run_here_the_place_with_the_most_runs_sets_what_other_code_is(repo, capsys, monkeypatch):
    """With no run of a reading recorded at the place shown, other code is code no run of the place with the most of
    its runs ran; where two places hold as many, neither is taken as the one to compare with, and each place's code
    is said."""
    rb(capsys, "metric", "add", "top1", "--maximize")
    rb(capsys, "criterion", "add", "int8 holds top1", "-e", "e1", "--metric", "change.top1", "--at-least", "-0.01", "--id", "c1")
    b, c = nested(repo), nested(repo, "c", "exp-c")
    (c / "train.py").write_text("lr = 0.5\n", encoding="utf-8")
    git("commit", "-qam", "a larger rate", cwd=c)
    commit = git("rev-parse", "HEAD").strip()
    c_head = git("rev-parse", "HEAD", cwd=c).strip()
    monkeypatch.chdir(b)
    rb(capsys, "run", "add", "e1", "fp32.top1=0.760", "int8.top1=0.757")
    monkeypatch.chdir(c)
    rb(capsys, "run", "add", "e1", "fp32.top1=0.762", "int8.top1=0.759")
    monkeypatch.chdir(repo)
    [f] = rb(capsys, "status").data["findings"]
    assert f["other_code"] == (f"2 runs ran different code: 1 in exp-b · {listed_path('exp-b')} at {commit[:12]}; "
                               f"1 in exp-c · {listed_path('exp-c')} at {c_head[:12]}")
    monkeypatch.chdir(b)
    rb(capsys, "run", "add", "e1", "fp32.top1=0.761", "int8.top1=0.758")
    monkeypatch.chdir(repo)
    [f] = rb(capsys, "status").data["findings"]
    assert f["other_code"] == f"1 of 3 runs ran other code: exp-c · {listed_path('exp-c')} at {c_head[:12]}"


def test_the_same_code_in_another_place_is_said_only_where_it_was_recorded(repo, capsys, monkeypatch):
    """A run in a worktree on another branch, at another commit of the same tree, with no changes, ran the same code:
    only where it was recorded is said."""
    rb(capsys, "metric", "add", "top1", "--maximize")
    rb(capsys, "criterion", "add", "int8 holds top1", "-e", "e1", "--metric", "change.top1", "--at-least", "-0.01", "--id", "c1")
    rb(capsys, "run", "add", "e1", "fp32.top1=0.761", "int8.top1=0.758")
    wt = nested(repo, "same", "exp-same")
    git("commit", "-q", "--allow-empty", "-m", "the same tree", cwd=wt)
    assert git("rev-parse", "HEAD", cwd=wt) != git("rev-parse", "HEAD") and git("rev-parse", "HEAD^{tree}", cwd=wt) == git("rev-parse", "HEAD^{tree}")
    monkeypatch.chdir(wt)
    rb(capsys, "run", "add", "e1", "fp32.top1=0.760", "int8.top1=0.757")
    monkeypatch.chdir(repo)
    [f] = rb(capsys, "status").data["findings"]
    assert f["other_code"] == "" and f["observed"] == f"1 of 2 runs was recorded in exp-same · {listed_path('exp-same')}"


def test_commits_that_differ_only_in_the_record_hold_the_same_code(repo, capsys, monkeypatch, tmp_path_factory):
    """The record's own folder is not code, as on a receipt: a run in a worktree whose commit adds only .rb/ to the
    commit a run here was recorded at ran the same code, so only where it was recorded is said; a change outside .rb/
    is other code."""
    rb(capsys, "metric", "add", "top1", "--maximize")
    rb(capsys, "experiment", "add", "int8 against fp32, again", "--id", "e2", "--baseline", "fp32", "--candidate", "int8")
    for exp, crit in (("e1", "c1"), ("e2", "c2")):
        rb(capsys, "criterion", "add", "int8 holds top1", "-e", exp, "--metric", "change.top1", "--at-least", "-0.01", "--id", crit)
        rb(capsys, "run", "add", exp, "fp32.top1=0.761", "int8.top1=0.758")
    start = git("rev-parse", "HEAD").strip()
    git("add", ".rb")
    git("commit", "-qm", "the record")
    wt = tmp_path_factory.mktemp("outside") / "wt-c"
    git("worktree", "add", "-q", "-b", "exp-c", str(wt))
    monkeypatch.chdir(wt)
    rb(capsys, "run", "add", "e1", "fp32.top1=0.760", "int8.top1=0.757")
    (wt / "train.py").write_text("lr = 0.5\n", encoding="utf-8")
    git("commit", "-qam", "a larger rate", cwd=wt)
    rb(capsys, "run", "add", "e2", "fp32.top1=0.762", "int8.top1=0.759")
    found = {f["criterion"]: f for f in rb(capsys, "status").data["findings"]}
    assert found["c1"]["observed"] == "1 of 2 runs was recorded in main" and found["c1"]["other_code"] == ""
    assert found["c2"]["other_code"] == f"1 of 2 runs ran other code: main at {start[:12]}"


def test_runs_from_three_places_say_each_place_s_code(repo, capsys, monkeypatch):
    """Over runs from three places, each other place's code is said apart, by place and commit, and nothing is added
    up: the changed tree of one, and the commit of the other."""
    fx = code_note_fixture(repo, capsys, monkeypatch)
    wt = nested(repo, "c", "exp-c")
    (wt / "train.py").write_text("lr = 0.5\n", encoding="utf-8")
    git("commit", "-qam", "a larger rate", cwd=wt)
    monkeypatch.chdir(wt)
    rb(capsys, "run", "add", "e1", "fp32.top1=0.762", "int8.top1=0.759")
    monkeypatch.chdir(repo)
    c_head = git("rev-parse", "HEAD", cwd=wt).strip()
    [f] = rb(capsys, "status").data["findings"]
    assert f["other_code"] == (f"2 of 3 runs ran other code: 1 in exp-b · {listed_path('exp-b')} at {fx['commit'][:12]}, with uncommitted changes; "
                               f"1 in exp-c · {listed_path('exp-c')} at {c_head[:12]}")


def test_a_live_journal_run_on_changed_code_in_a_worktree_says_it_ran_other_code(repo, capsys, monkeypatch):
    """What a command printed in a nested worktree with changed code, journalled by rb journal run and recorded where it
    ran, ran that worktree's code: the reading says so."""
    rb(capsys, "metric", "add", "top1", "--maximize")
    rb(capsys, "criterion", "add", "int8 holds top1", "-e", "e1", "--metric", "change.top1", "--at-least", "-0.01", "--id", "c1")
    rb(capsys, "run", "add", "e1", "fp32.top1=0.761", "int8.top1=0.758")
    wt = nested(repo)
    (wt / "train.py").write_text("lr = 0.25\n", encoding="utf-8")
    monkeypatch.chdir(wt)
    printed = json.dumps({"metrics": {"fp32.top1": 0.760, "int8.top1": 0.757}})
    assert main(["journal", "run", "--", sys.executable, "-c", f"print({printed!r})"]) == 0
    capsys.readouterr()
    rb(capsys, "reconcile", "--apply")
    monkeypatch.chdir(repo)
    [f] = rb(capsys, "status").data["findings"]
    commit = git("rev-parse", "HEAD").strip()
    assert f["observed"].startswith("1 of 2 runs was recorded in exp-b · ")
    assert f["other_code"] == f"1 of 2 runs ran other code: exp-b · {listed_path('exp-b')} at {commit[:12]}, with uncommitted changes"


EVAL_PY = """import json, sys
open("eval.log", "w").write("done\\n")
print(json.dumps({"metrics": {"fp32.top1": float(sys.argv[1]), "int8.top1": float(sys.argv[2])}}))
"""


def eval_committed(repo, capsys) -> None:
    """c1 over e1, and eval.py committed: it prints its numbers and writes eval.log, which no commit holds."""
    rb(capsys, "metric", "add", "top1", "--maximize")
    rb(capsys, "criterion", "add", "int8 holds top1", "-e", "e1", "--metric", "change.top1", "--at-least", "-0.01", "--id", "c1")
    (repo / "eval.py").write_text(EVAL_PY, encoding="utf-8")
    git("add", "eval.py")
    git("commit", "-qm", "an evaluation")


def watched(capsys, *numbers) -> None:
    """eval.py run under rb journal run where the shell is, and what it printed recorded."""
    assert main(["journal", "run", "--", sys.executable, "eval.py", *numbers]) == 0
    capsys.readouterr()
    rb(capsys, "reconcile", "--apply")


def test_what_the_runs_wrote_is_not_code(repo, capsys, monkeypatch):
    """Runs in three places on one commit, with the code unchanged, are not said to run other code because of the
    files they wrote, which no commit holds: each place's own result file read with --from, and the log a watched
    command wrote beside the numbers it printed. rb status, rb open and a page say only where they were recorded."""
    import urllib.request
    from rbtest import Open
    eval_committed(repo, capsys)

    def result(top: Path, fp: float, i8: float) -> None:
        (top / "out").mkdir(exist_ok=True)
        (top / "out" / "metrics.json").write_text(json.dumps({"fp32": {"top1": fp}, "int8": {"top1": i8}}), encoding="utf-8")
    result(repo, 0.761, 0.758)
    rb(capsys, "run", "add", "e1", "--from", "out/metrics.json")
    b, c = nested(repo), nested(repo, "c", "exp-c")
    result(b, 0.760, 0.757)
    monkeypatch.chdir(b)
    by_file = rb(capsys, "run", "add", "e1", "--from", "out/metrics.json").data["object"]["id"]
    monkeypatch.chdir(c)
    watched(capsys, "0.762", "0.759")
    monkeypatch.chdir(repo)
    [by_journal] = [p.stem for p in (repo / ".rb" / "experiments").glob("*/runs/*.yaml") if receipt(repo, p.stem).get("command")]
    assert receipt(repo, by_file)["attached"]["dirty"] is True                    # its own result file
    made = receipt(repo, by_journal)
    assert made["attached"]["dirty"] is True and made["produced"]["commit"] and "tree" not in made["produced"]    # eval.log
    [f] = rb(capsys, "status").data["findings"]
    assert f["other_code"] == "" and f["observed"].startswith("2 of 3 runs were recorded elsewhere: 1 in exp-b · ")
    main(["status"])
    assert "other code" not in capsys.readouterr().out
    server = Open(repo)
    try:
        def fetch(route):
            return urllib.request.urlopen(f"{server.url}{route}", timeout=60).read().decode("utf-8")
        state, page = json.loads(fetch("/state.json")), fetch("/page.html")
    finally:
        server.close()
    [line] = [x for b_ in state["home"]["briefs"] for e in b_["experiments"] for x in e["findings"] if x["criterion_id"] == "c1"]
    assert line["other_code"] == "" and "other code" not in json.dumps(state) and "other code" not in page


def test_a_watched_command_on_changed_code_that_writes_a_file_says_it_ran_other_code(repo, capsys, monkeypatch):
    """A command rb journal run watched in a nested worktree with changed code ran the code the journal saw when it
    started, whatever it wrote then: the reading says it ran other code, with uncommitted changes."""
    eval_committed(repo, capsys)
    rb(capsys, "run", "add", "e1", "fp32.top1=0.761", "int8.top1=0.758")
    wt = nested(repo)
    (wt / "train.py").write_text("lr = 0.25\n", encoding="utf-8")
    monkeypatch.chdir(wt)
    watched(capsys, "0.760", "0.757")
    monkeypatch.chdir(repo)
    assert (wt / "eval.log").is_file()
    [f] = rb(capsys, "status").data["findings"]
    commit = git("rev-parse", "HEAD").strip()
    assert f["other_code"] == f"1 of 2 runs ran other code: exp-b · {listed_path('exp-b')} at {commit[:12]}, with uncommitted changes"


def test_runs_tied_across_places_are_said_by_the_code_they_ran(repo, capsys, monkeypatch):
    """With no run here and three places holding one run each, two of them on the same code: the note says the runs
    did not all run the same code, and puts the two together, at their commit, with each place; a page by commit."""
    import urllib.request
    from rbtest import Open
    rb(capsys, "metric", "add", "top1", "--maximize")
    rb(capsys, "criterion", "add", "int8 holds top1", "-e", "e1", "--metric", "change.top1", "--at-least", "-0.01", "--id", "c1")
    b, c, d = nested(repo), nested(repo, "c", "exp-c"), nested(repo, "d", "exp-d")
    (d / "train.py").write_text("lr = 0.5\n", encoding="utf-8")
    git("commit", "-qam", "a larger rate", cwd=d)
    commit, d_head = git("rev-parse", "HEAD").strip(), git("rev-parse", "HEAD", cwd=d).strip()
    for at, numbers in ((b, ("fp32.top1=0.760", "int8.top1=0.757")), (c, ("fp32.top1=0.762", "int8.top1=0.759")), (d, ("fp32.top1=0.763", "int8.top1=0.755"))):
        monkeypatch.chdir(at)
        rb(capsys, "run", "add", "e1", *numbers)
    monkeypatch.chdir(repo)
    [f] = rb(capsys, "status").data["findings"]
    assert f["other_code"] == (f"3 runs did not all run the same code: 2 at {commit[:12]} (1 in exp-b · {listed_path('exp-b')}, "
                               f"1 in exp-c · {listed_path('exp-c')}); 1 in exp-d · {listed_path('exp-d')} at {d_head[:12]}")
    server = Open(repo)
    try:
        page = urllib.request.urlopen(f"{server.url}/page.html", timeout=60).read().decode("utf-8")
    finally:
        server.close()
    assert f"3 runs did not all run the same code: 2 at commit {commit[:12]}; 1 at commit {d_head[:12]}" in page


def test_a_single_checkout_keeps_what_it_kept_before(repo, capsys):
    clean = rb(capsys, "run", "add", "e1", "fp32.top1=0.761", "int8.top1=0.758").data["object"]["id"]
    assert set(receipt(repo, clean)["attached"]) == {"commit", "branch", "dirty", "tree"}
    Path("train.py").write_text("lr = 0.2\n", encoding="utf-8")
    Path("notes.txt").write_text("try a larger batch\n", encoding="utf-8")
    dirty = rb(capsys, "run", "add", "e1", "fp32.top1=0.762", "int8.top1=0.759").data["object"]["id"]
    assert set(receipt(repo, dirty)["attached"]) == {"commit", "branch", "dirty", "diff_sha256", "untracked", "tree"}
    assert "worktree" not in log_line(repo, "record", dirty) and "checkout" not in rb(capsys, "status").data
    main(["status"])
    assert "This worktree" not in capsys.readouterr().out


def test_a_worktree_nested_in_the_checkout_is_not_the_checkout_s_code(repo, capsys):
    nested(repo)                                                   # not ignored: git lists it as untracked
    assert git("status", "--porcelain").splitlines() == ["?? .rb/", "?? .worktrees/"]
    run_id = rb(capsys, "run", "add", "e1", "fp32.top1=0.761", "int8.top1=0.758").data["object"]["id"]
    att = receipt(repo, run_id)["attached"]
    assert att["dirty"] is False and "left_out" not in att and "untracked" not in att
    assert att["tree"] == git("rev-parse", "HEAD^{tree}").strip()


def test_the_journal_watches_no_file_of_a_worktree_nested_in_the_checkout(repo, capsys, monkeypatch):
    """A run recorded from a nested worktree names a file there, and another worktree sits in out/, which rb watches:
    neither checkout's files are the record's, so the journal logs none of them and nothing of theirs is found."""
    rb(capsys, "metric", "add", "top1", "--maximize")
    wt = nested(repo)
    (wt / "data").mkdir()
    (wt / "data" / "val_meta.json").write_text(json.dumps({"images": 50000, "shards": 64}), encoding="utf-8")
    (wt / "m.json").write_text(json.dumps({"fp32.top1": 0.761, "int8.top1": 0.758}), encoding="utf-8")
    other = repo / "out" / "c"
    git("worktree", "add", "-q", "-b", "exp-c", str(other), cwd=repo)
    (other / "eval.json").write_text(json.dumps({"int8": {"top1": 0.5}}), encoding="utf-8")
    monkeypatch.chdir(wt)
    rb(capsys, "run", "add", "e1", "--from", "m.json")
    monkeypatch.chdir(repo)
    (repo / "out" / "metrics.json").write_text(json.dumps({"int8": {"top1": 0.75}, "fp32": {"top1": 0.76}}), encoding="utf-8")
    rb(capsys, "journal", "scan")
    journal = (repo / ".git" / "rb" / "journal" / "main" / "journal.jsonl").read_text(encoding="utf-8")
    paths = [json.loads(line).get("path") for line in journal.splitlines() if line.strip()]
    assert "out/metrics.json" in paths and not [p for p in paths if p and p.startswith((".worktrees/", "out/c/"))]
    assert not [u for u in rb(capsys, "reconcile").data["unplaced"] if ".worktrees/" in u or "out/c/" in u]


def test_rb_s_reads_of_the_repository_take_no_file_of_a_worktree_nested_in_it(repo, capsys):
    """A worktree nested in the checkout at wt-b/, with its code, a results folder and a database at its top: rb compile
    . in the checkout, the work rb status says is already done and the results folders rb reconcile reads name none of
    its files, which are that checkout's; rb compile pointed at the worktree reads them."""
    wt = repo / "wt-b"
    git("worktree", "add", "-q", "-b", "exp-b", str(wt), cwd=repo)
    (wt / "results" / "sweep").mkdir(parents=True)
    (wt / "results" / "sweep" / "metrics.json").write_text(json.dumps({"top1": 0.75}), encoding="utf-8")
    import sqlite3
    with sqlite3.connect(wt / "results.db") as db:
        db.execute("create table runs (top1 real)")
    other = repo / "results" / "main-wt"
    git("worktree", "add", "-q", "-b", "exp-c", str(other), cwd=repo)
    (other / "metrics.json").write_text(json.dumps({"top1": 0.5}), encoding="utf-8")
    (repo / "results" / "main").mkdir(parents=True)
    (repo / "results" / "main" / "metrics.json").write_text(json.dumps({"top1": 0.7}), encoding="utf-8")
    main(["compile", "."])
    text = capsys.readouterr().out
    assert "results/main/metrics.json" in text and "wt-b/" not in text and "results/main-wt/" not in text
    main(["status"])
    assert "wt-b/" not in capsys.readouterr().out
    read = json.dumps(rb(capsys, "reconcile").data["repository"])
    assert "results/main/" in read and "wt-b/" not in read and "main-wt" not in read
    main(["compile", "wt-b"])
    assert "wt-b/results/sweep/metrics.json" in capsys.readouterr().out


@pytest.mark.skipif(os.name == "nt", reason="a backslash is a separator on Windows")
def test_a_nested_worktree_with_a_backslash_in_its_name_is_not_the_checkout_s_code(repo, capsys):
    nested(repo, "bs\\x", "exp-bs")
    run_id = rb(capsys, "run", "add", "e1", "fp32.top1=0.761", "int8.top1=0.758").data["object"]["id"]
    att = receipt(repo, run_id)["attached"]
    assert att["dirty"] is False and "untracked" not in att and "nested" not in att


def test_a_submodule_moved_on_is_a_repository_of_its_own_not_a_large_file(repo, capsys, tmp_path_factory):
    lib = tmp_path_factory.mktemp("lib")
    git("init", "-q", cwd=lib)
    (lib / "quant.py").write_text("bits = 8\n", encoding="utf-8")
    git("add", "quant.py", cwd=lib)
    git("commit", "-qm", "lib", cwd=lib)
    git("submodule", "add", "-q", str(lib), "lib")
    git("commit", "-qm", "the quantizer")
    (repo / "lib" / "quant.py").write_text("bits = 4\n", encoding="utf-8")
    git("commit", "-qam", "four bits", cwd=repo / "lib")       # the submodule moves on: its gitlink is changed
    run_id = rb(capsys, "run", "add", "e1", "fp32.top1=0.761", "int8.top1=0.758").data["object"]["id"]
    att = receipt(repo, run_id)["attached"]
    assert att["dirty"] is True and att["nested"] == 1 and "left_out" not in att
    main(["show", run_id])
    text = capsys.readouterr().out
    assert "over 10 MB" not in text and "1 nested repository is not in it." in text


def test_a_tree_is_said_to_be_kept_only_where_git_has_it(repo, capsys, tmp_path_factory):
    git("add", "-A")
    git("commit", "-qm", "the record")
    Path("train.py").write_text("lr = 0.2\n", encoding="utf-8")
    run_id = rb(capsys, "run", "add", "e1", "fp32.top1=0.761", "int8.top1=0.758").data["object"]["id"]
    git("checkout", "--", "train.py")
    git("add", "-A")
    git("commit", "-qm", "the run")
    main(["show", run_id])
    here = capsys.readouterr().out
    assert "kept in this repository under refs/rb/snapshots/." in here and "See the changes: git diff" in here
    clone = tmp_path_factory.mktemp("elsewhere") / "clone"
    git("clone", "-q", "--no-local", str(repo), str(clone))
    os.chdir(clone)
    try:
        main(["show", run_id])
        there = capsys.readouterr().out
    finally:
        os.chdir(repo)
    assert "not in this clone: the clone that recorded the run kept it." in there
    assert "kept in this repository" not in there and "See the changes: git diff" not in there


def test_a_read_leaves_git_s_index_as_it_was(repo, capsys):
    git("add", "-A")
    git("commit", "-qm", "the record")
    index = repo / ".git" / "index"
    before = index.read_bytes()
    later = time.time() + 5
    os.utime("README.md", (later, later))                         # re-saved unchanged: git's stat cache is stale
    for argv in (["status"], ["compile", "README.md"], ["context"]):
        assert main(argv) in (0, 1)
        capsys.readouterr()
        assert index.read_bytes() == before, argv


# ---------------------------------------------------------------- which record a worktree writes to


def test_a_nested_worktree_says_once_that_it_writes_to_the_checkout_s_record(repo, capsys, monkeypatch):
    wt = nested(repo)
    (wt / "train.py").write_text("lr = 0.25\n", encoding="utf-8")
    listed = {Path(line[len("worktree "):]).resolve(): line[len("worktree "):] for line in git("worktree", "list", "--porcelain").splitlines()
              if line.startswith("worktree ")}
    line = (f"This worktree (exp-b · {listed[wt]}) has no record of its own: rb writes to the record in main · {listed[repo]}, "
            "and each run keeps this worktree's code.")
    monkeypatch.chdir(wt)
    for argv in (["status"], ["context"]):
        main(argv)
        assert capsys.readouterr().out.count(line) == 1, argv
    checkout = rb(capsys, "status").data["checkout"]
    assert checkout == {"path": slashed(wt), "branch": "exp-b", "commit": git("rev-parse", "HEAD", cwd=wt).strip(), "dirty": True}
    assert refused(capsys, "init", "another", "--new-project")["code"] == "E_PROJECT_EXISTS"   # nested: still the checkout's


def test_a_worktree_outside_the_checkout_is_told_where_the_record_is(repo, capsys, monkeypatch):
    outside = repo.parent / f"{repo.name}-side"
    git("worktree", "add", "-q", "-b", "side", str(outside))
    listed = {Path(line[len("worktree "):]).resolve(): line[len("worktree "):] for line in git("worktree", "list", "--porcelain").splitlines()
              if line.startswith("worktree ")}
    message = f"No record in this worktree (side · {listed[outside.resolve()]}): the project's record is in main · {listed[repo]}."
    bring = f"To bring it here, commit .rb/ in {listed[repo]}, then run git merge main here."
    monkeypatch.chdir(outside)
    err = refused(capsys, "run", "add", "e1", "fp32.top1=0.761")
    assert (err["code"], err["message"], err["fix"]) == ("E_NO_PROJECT", message, bring)
    err = refused(capsys, "init", "INT8 ResNet-50 on ImageNet")
    assert (err["code"], err["message"]) == ("E_PROJECT_EXISTS", message)
    assert err["fix"] == bring + " To start a separate project here instead, run rb init --new-project."
    for argv in (["reconcile"], ["compile", "README.md"]):           # they read, and point at the record, never at a second one
        main(argv)
        text = capsys.readouterr().out
        assert message in text and bring in text and "rb init" not in text and "--apply starts" not in text, argv
    assert not (outside / ".rb").exists()
    with pytest.raises(rabbit_brain.RBError) as sdk:
        rabbit_brain.init("INT8 ResNet-50 on ImageNet", outside)
    assert sdk.value.code == "E_PROJECT_EXISTS" and not (outside / ".rb").exists()
    # once the record is committed, merging is all it takes
    monkeypatch.chdir(repo)
    uid = rb(capsys, "status").data["project"]["uid"]
    git("add", "-A")
    git("commit", "-qm", "the record")
    monkeypatch.chdir(outside)
    assert refused(capsys, "status")["fix"] == "To bring it here, run git merge main here."
    git("merge", "-q", "main")
    assert rb(capsys, "status").data["project"]["uid"] == uid


# ---------------------------------------------------------------- the journal of a worktree name git reuses


def test_a_worktree_name_git_reuses_keeps_each_worktree_s_journal(repo, capsys, monkeypatch):
    rb(capsys, "criterion", "add", "int8 holds", "-e", "e1", "--metric", "int8.top1", "--at-least", "0.75", "--id", "c1")
    git("add", "-A")
    git("commit", "-qm", "the record")
    first, second = repo.parent / f"{repo.name}-first" / "tmp", repo.parent / f"{repo.name}-second" / "tmp"

    def printed(tree: Path, value: float) -> None:
        monkeypatch.chdir(tree)
        line = f'print(\'{{"metrics": {{"int8.top1": {value}}}}}\')'
        assert main(["journal", "run", "--json", "--", sys.executable, "-c", line]) == 0
        capsys.readouterr()

    def found_here() -> list:
        monkeypatch.chdir(repo)
        return sorted((i["metrics"]["int8.top1"], "in a worktree removed since" in " ".join(i["facts"]))
                      for i in rb(capsys, "reconcile").data["found"] if i["experiment"] == "e1")

    git("worktree", "add", "-q", "-b", "try-1", str(first))
    printed(first, 0.761)
    monkeypatch.chdir(repo)
    git("worktree", "remove", "--force", str(first))
    git("worktree", "add", "-q", "-b", "try-2", str(second))     # git gives it the removed one's name
    common = Path(git("rev-parse", "--git-common-dir").strip()).resolve()
    assert (common / "worktrees" / "tmp").is_dir()
    assert found_here() == [(0.761, True)]                         # before the second one writes its journal
    printed(second, 0.772)
    assert found_here() == [(0.761, True)]                         # the second one still exists: its own is its own
    place = json.loads((common / "rb" / "journal" / "tmp" / "place.json").read_text(encoding="utf-8"))
    assert Path(place["path"]).resolve() == second.resolve()
    [aside] = [d for d in (common / "rb" / "journal").iterdir() if d.name.startswith("tmp@")]
    assert "0.761" in (aside / "journal.jsonl").read_text(encoding="utf-8")
    git("worktree", "remove", "--force", str(second))
    assert found_here() == [(0.761, True), (0.772, True)]


def test_a_worktree_git_names_main_keeps_a_journal_apart_from_the_main_checkout(repo, capsys, monkeypatch):
    """git names a worktree by its directory, so one in a directory called main has the main checkout's journal name:
    its journal is its own, the main checkout's is left as it is, and what it printed is found once it is removed."""
    rb(capsys, "criterion", "add", "int8 holds", "-e", "e1", "--metric", "int8.top1", "--at-least", "0.75", "--id", "c1")
    git("add", "-A")
    git("commit", "-qm", "the record")
    wt = repo.parent / f"{repo.name}-try" / "main"
    git("worktree", "add", "-q", "-b", "try-main", str(wt))
    common = Path(git("rev-parse", "--git-common-dir").strip()).resolve()
    assert (common / "worktrees" / "main").is_dir()
    monkeypatch.chdir(wt)
    line = 'print(\'{"metrics": {"int8.top1": 0.761}}\')'
    assert main(["journal", "run", "--json", "--", sys.executable, "-c", line]) == 0
    capsys.readouterr()
    mine = common / "rb" / "journal" / "main"
    assert "0.761" not in ((mine / "journal.jsonl").read_text(encoding="utf-8") if (mine / "journal.jsonl").exists() else "")
    assert not (mine / "place.json").exists()

    def found_here() -> list:
        monkeypatch.chdir(repo)
        return [(i["metrics"]["int8.top1"], "in a worktree removed since" in " ".join(i["facts"]))
                for i in rb(capsys, "reconcile").data["found"] if i["experiment"] == "e1"]

    assert found_here() == []                                      # the worktree is still there: its run is not main's
    git("worktree", "remove", "--force", str(wt))
    assert found_here() == [(0.761, True)]


def test_a_moved_worktree_keeps_its_journal(repo, capsys, monkeypatch, tmp_path_factory):
    """git worktree move, relative links, and a project folder moved then git worktree repair all keep git's entry for
    the worktree: its journal stays its own, main's reconcile finds nothing of it, and it reads its own run as here."""
    rb(capsys, "criterion", "add", "int8 holds", "-e", "e1", "--metric", "int8.top1", "--at-least", "0.75", "--id", "c1")
    git("add", "-A")
    git("commit", "-qm", "the record")

    def printed(tree: Path, value: float) -> None:
        monkeypatch.chdir(tree)
        line = f'print(\'{{"metrics": {{"int8.top1": {value}}}}}\')'
        assert main(["journal", "run", "--json", "--", sys.executable, "-c", line]) == 0
        capsys.readouterr()

    def found_in(tree: Path) -> list:
        monkeypatch.chdir(tree)
        return sorted((i["metrics"]["int8.top1"], "in a worktree removed since" in " ".join(i["facts"]))
                      for i in rb(capsys, "reconcile").data["found"] if i["experiment"] == "e1")

    def runs_in(tree: Path) -> int:
        monkeypatch.chdir(tree)
        return sum(1 for e in rb(capsys, "journal").data["entries"] if e["op"] == "run")

    # git worktree move: git keeps the worktree's name and its entry
    side = repo.parent / f"{repo.name}-c"
    git("worktree", "add", "-q", "-b", "t3", str(side / "mv"))
    printed(side / "mv", 0.761)
    monkeypatch.chdir(repo)                                        # Windows moves no folder a process works in
    git("worktree", "move", str(side / "mv"), str(side / "mv2"), cwd=repo)
    assert found_in(repo) == []
    assert found_in(side / "mv2") == [(0.761, False)] and runs_in(side / "mv2") == 1
    printed(side / "mv2", 0.762)                                   # a later write keeps the journal where it is
    common = repo / ".git"
    assert sorted(d.name for d in (common / "rb" / "journal").iterdir()) == ["main", "mv"]
    place = json.loads((common / "rb" / "journal" / "mv" / "place.json").read_text(encoding="utf-8"))
    assert Path(place["path"]).resolve() == (side / "mv2").resolve()
    assert found_in(repo) == [] and runs_in(side / "mv2") == 2
    # links git writes relative (worktree.useRelativePaths): the same worktree, read the same way
    git("worktree", "add", "-q", "-b", "t4", str(side / "rel"), cwd=repo)
    # written as git writes them: "/" between the parts and "\n" after, on every system
    rel_gitdir = Path(os.path.relpath(side / "rel" / ".git", common / "worktrees" / "rel")).as_posix()
    rel_entry = Path(os.path.relpath(common / "worktrees" / "rel", side / "rel")).as_posix()
    (common / "worktrees" / "rel" / "gitdir").write_text(rel_gitdir + "\n", encoding="utf-8", newline="\n")
    # rewritten in place: git for Windows hides a worktree's .git file, and Windows opens no hidden file anew to write it
    with open(side / "rel" / ".git", "r+", encoding="utf-8", newline="\n") as dot_git:
        dot_git.write(f"gitdir: {rel_entry}\n")
        dot_git.truncate()
    printed(side / "rel", 0.763)
    assert found_in(repo) == [] and found_in(side / "rel") == [(0.763, False)]
    # the project folder moved, then git worktree repair
    base = tmp_path_factory.mktemp("moved")
    git("clone", "-q", str(repo), str(base / "a" / "proj"))
    git("worktree", "add", "-q", "-b", "exp-a", str(base / "a" / "wt-a"), cwd=base / "a" / "proj")
    printed(base / "a" / "wt-a", 0.757)
    monkeypatch.chdir(base)
    (base / "a").rename(base / "b")
    git("worktree", "repair", str(base / "b" / "wt-a"), cwd=base / "b" / "proj")
    assert found_in(base / "b" / "proj") == []
    assert found_in(base / "b" / "wt-a") == [(0.757, False)] and runs_in(base / "b" / "wt-a") == 1


def test_the_way_to_bring_the_record_here_brings_it(repo, capsys, monkeypatch):
    """The fix E_NO_PROJECT gives in a worktree outside the checkout works when followed: a detached checkout's record
    is merged by the commit that holds it, never the one before it, and an ignored .rb/ that no commit holds is to be
    committed first."""
    outside = repo.parent / f"{repo.name}-side"
    git("worktree", "add", "-q", "-b", "side", str(outside))
    listed = {Path(line[len("worktree "):]).resolve(): line[len("worktree "):] for line in git("worktree", "list", "--porcelain").splitlines()
              if line.startswith("worktree ")}
    # the checkout is detached, its .rb/ not committed
    git("checkout", "-q", "--detach")
    monkeypatch.chdir(outside)
    fix = refused(capsys, "status")["fix"]
    assert fix == f"To bring it here, commit .rb/ in {listed[repo]}, then run git merge <that commit> here."
    git("add", "-A", cwd=repo)
    git("commit", "-qm", "the record", cwd=repo)
    head = git("rev-parse", "HEAD", cwd=repo).strip()
    fix = refused(capsys, "status")["fix"]
    assert fix == f"To bring it here, run git merge {head[:12]} here."
    git("merge", "-q", head[:12])
    assert rb(capsys, "status").data["project"]["title"] == "INT8 ResNet-50 on ImageNet"


def test_an_ignored_record_is_to_be_committed_before_a_merge_brings_it(repo, capsys, monkeypatch):
    Path(".gitignore").write_text(".rb/\n", encoding="utf-8")
    git("add", ".gitignore")
    git("commit", "-qm", "ignore the record")
    outside = repo.parent / f"{repo.name}-side"
    git("worktree", "add", "-q", "-b", "side", str(outside))
    listed = {Path(line[len("worktree "):]).resolve(): line[len("worktree "):] for line in git("worktree", "list", "--porcelain").splitlines()
              if line.startswith("worktree ")}
    assert git("status", "--porcelain") == ""                      # git status says nothing of an ignored .rb/
    monkeypatch.chdir(outside)
    assert refused(capsys, "status")["fix"] == f"To bring it here, commit .rb/ in {listed[repo]}, then run git merge main here."
    git("add", "-f", ".rb", cwd=repo)
    git("commit", "-qm", "the record", cwd=repo)
    assert refused(capsys, "status")["fix"] == "To bring it here, run git merge main here."
    git("merge", "-q", "main")
    assert rb(capsys, "status").data["project"]["title"] == "INT8 ResNet-50 on ImageNet"


def test_the_main_checkout_with_no_record_starts_its_own(workdir, monkeypatch, capsys, tmp_path_factory):
    """Where a linked worktree holds a record (an agent ran rb init there) and the main checkout holds none, the main
    checkout is not told to merge that worktree's branch: it gets the plain E_NO_PROJECT, rb ui its paste line, and
    rb init starts its record, which Elsewhere then tells apart from the worktree's."""
    as_the_user(monkeypatch)
    monkeypatch.setenv("RB_ACTOR", "human:ana")
    cfg = tmp_path_factory.mktemp("gitconfig") / "config"
    cfg.write_text("[user]\n\temail = ana@example.org\n\tname = ana\n[init]\n\tdefaultBranch = main\n", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(cfg))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    repo = workdir.resolve()
    git("init", "-q")
    Path("README.md").write_text("# INT8 ResNet-50 on ImageNet\n", encoding="utf-8")
    git("add", "README.md")
    git("commit", "-qm", "start")
    side = repo.parent / f"{repo.name}-try"
    git("worktree", "add", "-q", "-b", "codex/try", str(side))
    monkeypatch.chdir(side)
    rb(capsys, "init", "INT8 ResNet-50 on ImageNet", "--no-agents")
    monkeypatch.chdir(repo)
    err = refused(capsys, "status")
    assert err["code"] == "E_NO_PROJECT" and "worktree" not in err["message"] and "git merge" not in err["fix"]
    assert main(["ui"]) == 2
    assert "paste this line into your agent's chat" in capsys.readouterr().err
    rb(capsys, "init", "INT8 ResNet-50 on ImageNet", "--no-agents")
    [place] = rb(capsys, "status").data["elsewhere"]
    assert place["branch"] == "codex/try" and place["same_project"] is False


def test_a_record_that_goes_out_says_it_keeps_the_worktree_paths_it_keeps(repo, capsys, monkeypatch):
    """rb export --with-record says what the record keeps: where a write was made in a linked worktree, that
    worktree's path too, in what it prints and in the bundle's README; a record with no such write reads as before."""
    monkeypatch.chdir(repo)
    assert main(["export", "--with-record", "--out", str(repo / "before")]) == 0
    said = capsys.readouterr().out
    assert "and each run's host, platform and branch. Anyone who can read the bundle" in said and "worktree" not in said
    assert "linked git worktree" not in (repo / "before" / "README.md").read_text(encoding="utf-8")
    wt = nested(repo)
    monkeypatch.chdir(wt)
    rb(capsys, "run", "add", "e1", "fp32.top1=0.761", "int8.top1=0.758")
    monkeypatch.chdir(repo)
    assert main(["export", "--with-record", "--out", str(repo / "after")]) == 0
    said = capsys.readouterr().out
    assert ("each run's host, platform and branch, and the path of each linked worktree a write was made in. "
            "Anyone who can read the bundle can read all of it.") in said
    assert slashed(wt) in (repo / "after" / "record" / ".rb" / "log.jsonl").read_text(encoding="utf-8")
    readme = " ".join((repo / "after" / "README.md").read_text(encoding="utf-8").split())
    assert "A write made in a linked git worktree keeps that worktree's path" in readme
    assert slashed(wt) not in (repo / "after" / "page.html").read_text(encoding="utf-8")


def test_a_second_init_in_a_checkout_is_refused_as_before(repo, capsys, monkeypatch):
    """A second rb init in a checkout, or in a worktree nested in it, says what it said before worktrees were told
    apart: no word of bringing a record here, and no --new-project, which does not start one there."""
    before = "Use it: `rb status`. One .rb/ per line of work; start another in a different directory."
    assert refused(capsys, "init", "again")["fix"] == before
    monkeypatch.chdir(nested(repo))
    assert refused(capsys, "init", "again", "--new-project")["fix"] == before


def files_in_a_worktree(repo: Path, capsys, monkeypatch) -> tuple:
    """A nested linked worktree (exp-wide, at .worktrees/wide-sweep) holding the files the record names: a run's file
    and its artifact, a database row's run, a resource's file and the document two past calls are given from (one the
    user's, one a session's, which waits). It gives the worktree, the file's run and the row's run."""
    import sqlite3
    wt = nested(repo, "wide-sweep", "exp-wide")
    (wt / "train.py").write_text("lr = 0.25\n", encoding="utf-8")
    (wt / "out").mkdir()
    (wt / "out" / "metrics.json").write_text(json.dumps({"fp32": {"top1": 0.761}, "int8": {"top1": 0.758}}), encoding="utf-8")
    (wt / "out" / "config.yaml").write_text("lr: 0.25\n", encoding="utf-8")
    con = sqlite3.connect(wt / "results.db")
    con.execute("create table evals (seed integer, top1 real)")
    con.execute("insert into evals values (1, 0.757)")
    con.commit()
    con.close()
    monkeypatch.chdir(wt)
    rb(capsys, "metric", "add", "top1", "--maximize")
    rb(capsys, "criterion", "add", "int8 holds top1", "-e", "e1", "--metric", "change.top1", "--at-least", "-0.01", "--id", "c1")
    by_file = rb(capsys, "run", "add", "e1", "--from", "out/metrics.json", "--artifact", "out/config.yaml").data["object"]["id"]
    by_row = rb(capsys, "run", "add", "e1", "--from", "results.db", "--table", "evals", "--where", "seed=1", "--variant", "int8").data["object"]["id"]
    # a resource's file, and the document a past call is given from, in the worktree too: a session's call waits
    rb(capsys, "resource", "add", "int8-ckpt", "--name", "int8 checkpoint", "--type", "checkpoint", "--file", "out/config.yaml")
    (wt / "CALLS.md").write_text("# Calls\n\n2026-10-01: we keep INT8 at eight bits.\n", encoding="utf-8")
    rb(capsys, "decide", "c1", "accept", "--past", "--quote", "we keep INT8 at eight bits", "--source", "CALLS.md:3", "--why", "kept")
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.delenv("RB_ACTOR", raising=False)
    rb(capsys, "decide", "c1", "accept", "--past", "--quote", "we keep INT8 at eight bits", "--source", "CALLS.md:3", "--why", "kept again")
    monkeypatch.delenv("CLAUDECODE")
    monkeypatch.setenv("RB_ACTOR", "human:ana")
    monkeypatch.chdir(repo)
    return wt, by_file, by_row


def test_no_linked_worktree_s_path_reaches_a_page(repo, capsys, monkeypatch, tmp_path_factory):
    """rb page, rb export's page.html (the page alone, and a bundle's) and rb open's /page.html name a file inside a
    nested linked worktree by its path inside it, with the words saying it was in another worktree, and never the
    worktree's own path: not in a run's files, a database row, nor what it takes to run an experiment again, and not
    in the page of a clone of the bundle. The record, --json and the user's own screens keep the path as it is."""
    from rbtest import Open
    wt, by_file, _ = files_in_a_worktree(repo, capsys, monkeypatch)
    place = ".worktrees/wide-sweep"
    # the record and --json keep the path as it is
    assert [f["path"] for f in receipt_files(repo, by_file)] == [f"{place}/out/config.yaml", f"{place}/out/metrics.json"]
    shown = rb(capsys, "show", by_file).data["object"]
    assert any(f["path"].startswith(place) for f in shown["files"])
    out = tmp_path_factory.mktemp("pages")
    pages = {}
    for name, argv in (("page", ["page", "--out", str(out / "page.html")]), ("export", ["export", "--out", str(out / "exp")]),
                       ("bundle", ["export", "--with-record", "--out", str(out / "bundle")])):
        rb(capsys, *argv)
    pages["page"] = (out / "page.html").read_text(encoding="utf-8")
    pages["export"] = (out / "exp" / "page.html").read_text(encoding="utf-8")
    pages["bundle"] = (out / "bundle" / "page.html").read_text(encoding="utf-8")
    rb(capsys, "clone", str(out / "bundle"), str(out / "clone"))
    monkeypatch.chdir(out / "clone")
    rb(capsys, "page", "--out", str(out / "clone.html"))
    pages["clone"] = (out / "clone.html").read_text(encoding="utf-8")
    monkeypatch.chdir(repo)
    server = Open(repo)
    try:
        import urllib.request
        pages["open"] = urllib.request.urlopen(f"{server.url}/page.html", timeout=60).read().decode("utf-8")
        state = urllib.request.urlopen(f"{server.url}/state.json", timeout=60).read().decode("utf-8")
    finally:
        server.close()
    for name, page in pages.items():
        assert "wide-sweep" not in page and ".worktrees" not in page and slashed(wt) not in page, name
    for name in ("page", "export", "bundle", "open"):
        # each in its run's receipt, as the receipt's rows say them
        # a path in monospace, the rest of the row in the UI font (DESIGN-PASS-1 target 7)
        assert '<dd><span class="mono">out/metrics.json</span> (in another worktree) · sha256' in pages[name], name
        assert '<dt>row</dt><dd>read from <span class="mono">results.db</span> (in another worktree), table evals' in pages[name], name
        assert "local:out/config.yaml (in another worktree) · " in pages[name], name
        assert "CALLS.md:3 (in another worktree): &quot;we keep INT8 at eight bits&quot;" in pages[name], name
    assert "not here: out/config.yaml (in another worktree), out/metrics.json (in another worktree)" in pages["bundle"]
    assert f"{place}/out/metrics.json" in state                    # the user's own screen keeps the path as it is


def test_rb_open_and_rb_ui_name_a_file_in_a_linked_worktree_by_its_path_inside_it(repo, capsys, monkeypatch):
    """On the user's screens a file inside a nested linked worktree is named by its path inside it, with the worktree
    said in short beside it, or once, on a run's card, by where the run was recorded: a run's files, a database row, a
    resource's file, the document a past call is given from and what it takes to run the experiment again. The run's
    card keeps the whole path, and so do the data's `path`, rb's text and --json."""
    import urllib.request
    from rbtest import Open
    wt, by_file, by_row = files_in_a_worktree(repo, capsys, monkeypatch)
    place, short = ".worktrees/wide-sweep", "exp-wide · .worktrees/wide-sweep"
    server = Open(repo)
    try:
        def fetch(route):
            return json.loads(urllib.request.urlopen(f"{server.url}{route}", timeout=60).read().decode("utf-8"))
        state = fetch("/state.json")
        card = fetch(f"/card.json?ref=o:{by_file}")["card"]
        row_card = fetch(f"/card.json?ref=o:{by_row}")["card"]
        resource = fetch("/card.json?ref=o:int8-ckpt")["card"]
        shutil.rmtree(wt)                                   # the worktree goes: its files are not here to run again
        gone = fetch("/state.json")
    finally:
        server.close()
    runs = {r["run_id"]: r for r in state["home"]["runs"]}
    files = runs[by_file]["receipt"]["files"]
    assert [(f["path"], f["inside"], f["in_place"], f["recorded_there"]) for f in files] == [
        (f"{place}/out/config.yaml", "out/config.yaml", short, True), (f"{place}/out/metrics.json", "out/metrics.json", short, True)]
    row = runs[by_row]["receipt"]["row"]
    assert (row["path"], row["inside"], row["in_place"], row["recorded_there"]) == (f"{place}/results.db", "results.db", short, True)
    assert runs[by_file]["observed_in"]["label_short"].startswith(f"recorded in {short} at ")      # the place, said once
    assert any(f.startswith("file: out/metrics.json · sha256 ") for f in card["facts"])
    assert f"paths as recorded: {place}/out/config.yaml, {place}/out/metrics.json" in card["facts"]
    assert any(f.startswith("row: read from results.db, table evals") for f in row_card["facts"]) and f"path as recorded: {place}/results.db" in row_card["facts"]
    [res] = state["home"]["resources"]
    assert res["where"][0].startswith(f"local:{place}/out/config.yaml · ") and res["where_short"][0].startswith(f"local:out/config.yaml (in {short}) · ")
    assert any(f.startswith(f"local: out/config.yaml (in {short}) · ") for f in resource["facts"])
    assert f"path as recorded: {place}/out/config.yaml" in resource["facts"]
    [waits] = state["home"]["pending"]
    assert f'CALLS.md:3 (in {short}): "we keep INT8 at eight bits"' in waits["text"]
    lines = [line for b in state["home"]["briefs"] for line in (*b["changed"], *b["ruled"])]
    assert any(f"from CALLS.md:3 (in {short})" in line for line in lines), lines
    [eb] = [e for b in gone["home"]["briefs"] for e in b["experiments"] if e["id"] == "e1"]
    [files_row] = [r for r in eb["rerun"] if r["what"] == "files"]
    assert f"not here: out/config.yaml (in {short}), out/metrics.json (in {short}), results.db (in {short})" in files_row["detail"], files_row
    said = json.dumps(state)
    assert "(in another worktree)" not in said and f"{place}/out/metrics.json" in said
    # rb's text and --json keep the whole path
    assert any(f["path"] == f"{place}/out/metrics.json" for f in rb(capsys, "show", by_file).data["object"]["files"])
    assert main(["show", by_file]) == 0 and f"{place}/out/metrics.json" in capsys.readouterr().out


def receipt_files(repo: Path, run_id: str) -> list:
    [path] = (repo / ".rb" / "experiments").glob(f"*/runs/{run_id}.yaml")
    return rbyaml.loads(path.read_text(encoding="utf-8"))["files"]




def test_shell_run_receipt_says_its_short_place_and_keeps_recorded_paths(repo, capsys, monkeypatch):
    """A run receipt says its short place once and retains the full paths that were recorded."""
    from test_open import _shell
    from rbtest import Open
    monkeypatch.setenv("RB_TEST_SHELL", "1")
    _, by_file, by_row = files_in_a_worktree(repo, capsys, monkeypatch)
    server = Open(repo)
    try:
        doc = _shell(server.url)
        receipt = doc.at("run-" + by_file)
        assert receipt.text.count("recorded in exp-wide · .worktrees/wide-sweep at ") == 1
        assert ".worktrees/wide-sweep/out/config.yaml" in receipt.text
        assert ".worktrees/wide-sweep/out/metrics.json" in receipt.text
        row = doc.at("run-" + by_row)
        assert row.text.count("recorded in exp-wide · .worktrees/wide-sweep at ") == 1
        assert ".worktrees/wide-sweep/results.db" in row.text
    finally:
        server.close()
