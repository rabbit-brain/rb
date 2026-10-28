"""The places the user's own screens can show (rb open, rb ui), computed from git and files.

Promises pinned here:

- Each worktree git lists is one place, named by its branch and path as git gives them (a detached one by its commit),
  with an id that is its path's, and what its record is to the record shown first: this, its own, none of its own (it
  writes to this record), a separate project's, or none; and whether it is gone. Each says its own counts: record
  files not committed, the last write (the log's time, never who made it), and for a place that writes to this
  record, the runs recorded from it. Nothing is summed across places.
- The versions of a record are the commits that changed .rb/, each by its commit and the time it was committed: never
  its subject or its author.
- Nothing in the places names a writer: no actor, session, agent or model, as a key or in a text.
- Listing them is quick: under 0.3 s for three worktrees.
"""
import hashlib
import json
import shutil
import sys
import time
from pathlib import Path

import pytest

from rabbit_brain import _rb
from rbtest import PLACES_AGENT, as_the_user, places_record

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="places are git's worktrees")


@pytest.fixture
def rec(tmp_path, monkeypatch):
    as_the_user(monkeypatch)
    return places_record(tmp_path)


def places(root: Path, place: str = "") -> dict:
    return _rb.call("places", {"place": place}, root=root, via="cli")


def by_branch(answer: dict) -> dict:
    return {p["branch"]: p for p in answer["places"]}


def keys(node) -> set:
    if isinstance(node, dict):
        return set(node) | {k for v in node.values() for k in keys(v)}
    if isinstance(node, list):
        return {k for v in node for k in keys(v)}
    return set()


def test_each_place_says_what_its_record_is(rec):
    answer = places(rec.root)
    p = by_branch(answer)
    assert set(p) == {"main", "exp-b", "exp-c", "exp-g", "exp-n", "exp-s"} and len(answer["places"]) == 6
    assert answer["places"][0]["branch"] == "main" and answer["place"] == p["main"]["id"]
    assert {b: (x["record"], x["gone"], x["readable"]) for b, x in p.items()} == {
        "main": ("this", False, True),
        "exp-b": ("shared", False, False),
        "exp-c": ("own", False, True),
        "exp-s": ("separate", False, True),
        "exp-n": ("none", False, False),
        "exp-g": ("none", True, False),
    }
    listed = {}
    for block in rec.git("worktree", "list", "--porcelain").split("\n\n"):
        lines = dict(line.split(" ", 1) for line in block.splitlines() if " " in line)
        listed[lines.get("branch", "").removeprefix("refs/heads/")] = lines["worktree"]
    for branch, x in p.items():
        # named by its branch and its path, as git lists them; its id is its resolved path's
        assert x["path"] == listed[branch] and x["label"] == f"{branch} · {listed[branch]}", x
        resolved = rec.tree[branch].as_posix()
        assert x["id"] == "p_" + hashlib.sha256(resolved.encode("utf-8")).hexdigest()[:10], (branch, resolved)
        assert x["head"] == rec.git("rev-parse", branch)
    # the nested worktree's code is its own and changed; its run is in this record, said as recorded from it
    assert p["exp-b"]["dirty"] is True and p["exp-b"]["runs"] == 1 and p["main"]["dirty"] is False
    assert p["exp-b"]["said"].startswith("no record of its own: it writes to this record · 1 run recorded from it · last write ")
    assert p["exp-b"]["root"] is None and p["main"]["root"] == rec.root.resolve().as_posix()
    # counts are each place's own: what is not committed there, and the time of its last write there
    assert p["main"]["uncommitted"] == 0 and p["exp-c"]["uncommitted"] == 2 and p["exp-s"]["uncommitted"] > 0
    log = lambda root: [json.loads(line) for line in (root / ".rb" / "log.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    assert p["exp-c"]["last_write"] == log(rec.tree["exp-c"])[-1]["at"] and p["main"]["last_write"] == log(rec.root)[-1]["at"]
    nested_at = [e["at"] for e in log(rec.root) if (e.get("worktree") or {}).get("path") == rec.tree["exp-b"].as_posix()]
    assert p["exp-b"]["last_write"] == max(nested_at)
    assert p["exp-s"]["said"].startswith("a separate project, with its own record") and p["exp-n"]["said"] == "no record"
    assert p["exp-g"]["said"] == "removed since" and p["exp-g"]["last_write"] is None     # what it held is not there to read
    assert p["main"]["said"].startswith("this record · last write 20") and p["main"]["said"].endswith(" UTC")
    assert p["exp-c"]["said"].startswith("its own record · 2 record files not committed · last write ")
    # from the sibling, the sibling's record is this one and the main checkout's is its own record of the same project
    there = by_branch(places(rec.tree["exp-c"]))
    assert (there["exp-c"]["record"], there["main"]["record"], there["exp-s"]["record"]) == ("this", "own", "separate")


def test_a_record_s_versions_are_its_commits_by_sha_and_time(rec):
    answer = places(rec.root)
    [v] = answer["versions"]                                     # one commit changed .rb/ on main
    sha = rec.git("rev-parse", "HEAD")
    assert set(v) == {"sha", "committed", "label", "shown", "shown_short", "note"} and v["sha"] == sha and v["note"] == ""
    # git's own time, with UTC written +00:00 whichever way this git writes it
    made = rec.git("show", "-s", "--format=%cI", sha)
    assert v["committed"] == (made[:-1] + "+00:00" if made.endswith("Z") else made)
    assert v["label"].startswith(sha[:12] + " · 20") and v["shown"].startswith(f"main · {rec.root.resolve().as_posix()} at {sha[:12]}, 20")
    assert v["label"].endswith(" UTC") and v["shown"].endswith(" UTC: a past version, read only")
    text = json.dumps(answer["versions"])
    assert "the record, as of" not in text and "ana" not in text           # never a commit's subject or its author
    # a place's versions are its own record's: the sibling's branch has the same commit, the separate project none
    p = by_branch(answer)
    assert [x["sha"] for x in places(rec.root, p["exp-c"]["id"])["versions"]] == [sha]
    assert places(rec.root, p["exp-s"]["id"])["versions"] == [] and places(rec.root, p["exp-b"]["id"])["versions"] == []


def test_a_record_s_versions_are_its_own_line_s_commits_not_a_merged_branch_s(rec):
    """A branch merged in brings its commits into the history, never into the versions of the record shown: those are
    the commits of its own line, the merge among them, each a state its record was in there."""
    c = rec.tree["exp-c"]
    rec.git("add", ".rb", cwd=c)
    rec.git("commit", "-qm", "the sibling's run", cwd=c)
    theirs = rec.git("rev-parse", "HEAD", cwd=c)
    rec.rb("note", "add", "a note in the main checkout")
    rec.git("add", ".rb")
    rec.git("commit", "-qm", "a note")
    ours = rec.git("rev-parse", "HEAD")
    rec.git("merge", "-q", "--no-ff", "--no-edit", "exp-c")
    merge = rec.git("rev-parse", "HEAD")
    shas = [v["sha"] for v in places(rec.root)["versions"]]
    assert shas == [merge, ours, rec.git("rev-parse", "HEAD~1^")] and theirs not in shas


def test_a_past_version_asked_of_rb_alone_says_where_it_shows(rec, monkeypatch):
    """A past version asked for where no screen keeps a checkout of it (the SDK, or the screen started without rb)
    says, in plain words, where past versions show."""
    from rabbit_brain.errors import RBError
    monkeypatch.delenv("RB_VIEWS", raising=False)
    with pytest.raises(RBError) as refused:
        _rb.call("home", {}, root=rec.root, via="cli", at=rec.git("rev-parse", "HEAD"))
    assert refused.value.message == "A past version shows only on rb open and rb ui, which keep a checkout of it while they are open."


def test_places_name_no_writer(rec):
    answer = places(rec.root)
    assert not keys(answer) & {"actor", "actor_via", "via", "session", "agent", "model", "by", "created_by", "provider"}
    text = json.dumps(answer)
    assert PLACES_AGENT["CLAUDE_CODE_SESSION_ID"] not in text and "claude" not in text.lower() and "human:" not in text


def test_three_worktrees_are_listed_quickly(tmp_path, monkeypatch):
    as_the_user(monkeypatch)
    rec = places_record(tmp_path)
    for branch in ("exp-s", "exp-n"):
        rec.git("worktree", "remove", "--force", str(rec.tree[branch]))
    rec.git("worktree", "prune")                                  # exp-g, removed without it
    assert len(places(rec.root)["places"]) == 3
    took = []
    for _ in range(3):
        t = time.perf_counter()
        places(rec.root)
        took.append(time.perf_counter() - t)
    assert min(took) < (1.5 if sys.platform == "win32" else 0.3), took


def test_a_single_checkout_says_its_counts_as_it_always_has(tmp_path, monkeypatch):
    """With more than one place, each says what its counts count (record files not committed); a single checkout says
    its own as it always has, since its screens read exactly as before places were listed."""
    as_the_user(monkeypatch)
    rec = places_record(tmp_path)
    rec.rb("note", "add", "the fp32 baseline is from the paper")
    [main] = [p for p in places(rec.root)["places"] if p["record"] == "this"]
    assert main["said"].startswith("this record · 2 record files not committed · last write ")
    for branch in ("exp-b", "exp-c", "exp-s", "exp-n"):
        rec.git("worktree", "remove", "--force", str(rec.tree[branch]))
    rec.git("worktree", "prune")
    [alone] = places(rec.root)["places"]
    assert alone["said"].startswith("this record · 2 not committed · last write "), alone
