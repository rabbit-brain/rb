"""EVIDENCE.md E1: the record survives git's working-tree operations.

rb keeps every log line it appends, with the record forms it wrote, in a copy of its own in the journal's folder
(<git common dir>/rb/journal/<worktree>/kept.jsonl; .rb/journal/ while git's folder cannot be written). A kept write is
taken out by git only when the log on disk lacks its line and no commit HEAD has named since holds it. Then:

- no record reads as edited outside rb, and nothing is set aside: the record reads as the log on disk has it, and a file
  that holds a write git took out is not read;
- rb status, rb context, rb open, rb ui, rb doctor (as a problem), MCP and the SDK say so (the `taken_out` key);
- rb show <id> says git took it out; rb doctor --restore puts it back; rb doctor --forget drops rb's copy;
- a stash that holds the lines is named, with git stash pop, while the log is as the stash left it;
- a switch to another branch or an older commit says nothing; rb remove takes the kept copy.
"""
import json
import os
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

import pytest

from rbtest import AGENT_VARS, Mcp, Open, as_the_user, command, environment

pytestmark = pytest.mark.skipif(shutil.which("git") is None or sys.platform == "win32", reason="needs git and a POSIX shell")
GIT = shutil.which("git")
TOOK = "1 record rb wrote was taken out by git (a checkout, restore or reset). rb kept it: rb doctor --restore puts it back."


@pytest.fixture
def home(tmp_path, monkeypatch):
    as_the_user(monkeypatch)
    cfg = tmp_path / "gitconfig"
    cfg.write_text("[user]\n\temail = ana@example.org\n\tname = ana\n[init]\n\tdefaultBranch = main\n", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(cfg))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("RB_ACTOR", "human:ana")
    return tmp_path


def run(cwd: Path, *argv: str, code: int = 0, bin_: list = None) -> dict:
    r = subprocess.run([*(bin_ or command()), *argv, "--json"], cwd=cwd, env=environment(), capture_output=True, text=True, stdin=subprocess.DEVNULL)
    assert r.returncode == code, (argv, r.returncode, r.stdout, r.stderr)
    return json.loads(r.stdout)


def text(cwd: Path, *argv: str) -> str:
    r = subprocess.run([*command(), *argv], cwd=cwd, env=environment(), capture_output=True, text=True, stdin=subprocess.DEVNULL)
    return r.stdout


def git(cwd: Path, *argv: str) -> str:
    return subprocess.run([GIT, *argv], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def record(home: Path, name: str = "int8-resnet50") -> Path:
    root = home / name
    root.mkdir()
    git(root, "init", "-q")
    (root / "train.py").write_text("lr = 0.1\n", encoding="utf-8")
    run(root, "init", "INT8 ResNet-50", "--no-agents")
    run(root, "experiment", "add", "INT8 against FP32", "--id", "int8", "--baseline", "fp32", "--candidate", "int8")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "the record")
    return root


def add_run(root: Path, top1: str) -> str:
    (root / "out").mkdir(exist_ok=True)
    f = root / "out" / f"m{top1}.json"
    f.write_text(f'{{"int8": {{"top1": {top1}}}, "fp32": {{"top1": 76.0}}}}', encoding="utf-8")
    return run(root, "run", "add", "int8", "--from", f"out/{f.name}")["data"]["object"]["id"]


def taken(root: Path):
    return run(root, "status")["data"].get("taken_out")


def codes(root: Path) -> set:
    return {i["code"] for i in run(root, "status")["data"]["open"]}


def doctor(root: Path) -> dict:
    r = subprocess.run([*command(), "doctor", "--json"], cwd=root, env=environment(), capture_output=True, text=True)
    return json.loads(r.stdout)


def test_a_checkout_that_takes_out_records_is_said_never_read_as_edited_and_restore_puts_them_back(home):
    root = record(home)
    rid = add_run(root, "75.1")
    git(root, "checkout", "--", ".")
    t = taken(root)
    assert t and t["count"] == 1 and t["records"] == [{"kind": "run", "id": rid}] and t["said"] == TOOK
    assert "edited_outside_rb" not in codes(root) and "taken_out_by_git" in codes(root)
    assert TOOK in text(root, "status", "--full") and TOOK in text(root, "context")
    assert not list((root / ".rb").rglob("*.outside-rb"))
    # the record reads as the log on disk has it: the run is not read
    assert run(root, "status")["data"]["runs"] == []
    shown = run(root, "show", "--long", rid)
    assert shown["data"]["taken_out"]["said"] == f"run {rid} was taken out of the record by git. rb kept it: rb doctor --restore puts it back."
    d = doctor(root)
    fails = [c for c in d["data"]["outcome"]["failures"]]
    assert [c["check"] for c in fails] == ["taken_out_by_git"], fails
    assert fails[0]["detail"] == "1 record rb wrote was taken out by git (a checkout, restore or reset)"
    assert fails[0]["fix"] == "rb doctor --restore puts it back; rb doctor --forget drops it"
    out = subprocess.run([*command(), "doctor", "--restore"], cwd=root, env=environment(), capture_output=True, text=True).stdout
    assert f"restored .rb/experiments/int8/runs/{rid}.yaml: put back the write git took out" in out
    assert taken(root) is None and [r["id"] for r in run(root, "status")["data"]["runs"]] == [rid]
    assert doctor(root)["data"]["outcome"]["passed"] is True


def test_forget_drops_rbs_copy_and_sets_the_files_aside(home):
    root = record(home)
    rid = add_run(root, "75.1")
    git(root, "checkout", "--", ".")
    r = run(root, "doctor", "--forget")
    assert r["data"]["forgot"] == [{"kind": "run", "id": rid}]
    assert r["data"]["moved_aside"] == [f".rb/experiments/int8/runs/{rid}.yaml.outside-rb"]
    said = text(root, "doctor", "--forget")   # again: nothing left to drop
    assert "Nothing to drop" in said
    assert taken(root) is None and "edited_outside_rb" not in codes(root)
    assert doctor(root)["data"]["outcome"]["passed"] is True


def test_forget_is_refused_in_an_agent_session_as_rb_remove_is(home, monkeypatch):
    """S1 (MP-1110-1703): dropping rb's copy of what git took out is the user's to do, so an agent session, or RB_ACTOR
    naming an agent, gets E_USAGE in rb remove's sentence form, and the copy stays for rb doctor --restore."""
    root = record(home)
    add_run(root, "75.1")
    git(root, "checkout", "--", ".")
    monkeypatch.setenv("CLAUDECODE", "1")
    r = run(root, "doctor", "--forget", code=2)
    assert r["errors"][0]["code"] == "E_USAGE"
    assert r["errors"][0]["message"] == "Nothing was dropped: this is an agent session, and dropping rb's copy of what git took out is the user's to do."
    assert r["errors"][0]["fix"].startswith("The user runs rb doctor --forget in their own terminal.")
    monkeypatch.delenv("CLAUDECODE")
    monkeypatch.setenv("RB_ACTOR", "agent:helper")
    r = run(root, "doctor", "--forget", code=2)
    assert r["errors"][0]["message"].startswith("Nothing was dropped: RB_ACTOR names an agent here")
    monkeypatch.setenv("RB_ACTOR", "human:ana")
    assert taken(root)["count"] == 1
    assert run(root, "doctor", "--forget")["data"]["forgot"]


def test_runs_committed_on_a_branch_then_a_switch_back_say_nothing_and_left_uncommitted_are_caught(home):
    root = record(home)
    git(root, "checkout", "-q", "-b", "exp")
    add_run(root, "75.1")           # before exp's first commit: its receipt keeps the branch point
    git(root, "add", "-A")
    git(root, "commit", "-qm", "runs on exp")
    git(root, "checkout", "-q", "main")
    assert taken(root) is None and doctor(root)["data"]["outcome"]["passed"] is True
    # the same, left uncommitted, then a forced checkout of main: caught
    git(root, "checkout", "-q", "exp")
    add_run(root, "75.2")
    git(root, "checkout", "-q", "-f", "main")
    t = taken(root)
    assert t and t["count"] >= 1


def test_a_switch_to_an_older_commit_says_nothing(home):
    root = record(home)
    add_run(root, "75.1")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "a run")
    git(root, "checkout", "-q", "HEAD~1")
    assert taken(root) is None
    assert "taken_out_by_git" not in codes(root)


def test_a_pull_that_adds_lines_then_a_write_then_a_checkout_is_caught(home):
    origin = record(home, "origin")
    clone = home / "clone"
    git(home, "clone", "-q", str(origin), str(clone))
    run(origin, "note", "add", "the held-out set is new", "-e", "int8")
    git(origin, "add", "-A")
    git(origin, "commit", "-qm", "a note")
    git(clone, "pull", "-q")
    rid = add_run(clone, "75.1")
    git(clone, "checkout", "--", ".")
    t = taken(clone)
    assert t and t["records"] == [{"kind": "run", "id": rid}]


def test_a_stash_is_named_with_git_stash_pop_until_rb_writes_again(home):
    root = record(home)
    run(root, "spec", "set", "int8", "lr", "0.1", "--no-verify")
    git(root, "stash", "-q")
    t = taken(root)
    assert t["stash"] == "stash@{0}" and t["pop"] is True
    assert t["said"] == "1 record rb wrote is in git's stash: git stash pop stash@{0} puts it back."
    assert [i["do"] for i in run(root, "status")["data"]["open"] if i["code"] == "taken_out_by_git"] == ["git stash pop stash@{0}   (puts it back)"]
    run(root, "note", "add", "a later note")
    t = taken(root)
    assert t["pop"] is False and t["said"] == "1 record rb wrote was taken out by git (a stash, stash@{0}). rb kept it: rb doctor --restore puts it back."


def test_a_preview_keeps_nothing_and_outside_git_nothing_is_kept(home):
    root = record(home)
    kept = Path(git(root, "rev-parse", "--absolute-git-dir")) / "rb" / "journal" / "main" / "kept.jsonl"
    before = kept.read_bytes() if kept.exists() else b""
    run(root, "note", "add", "a previewed note", "--dry-run")
    assert (kept.read_bytes() if kept.exists() else b"") == before
    plain = home / "plain"
    plain.mkdir()
    run(plain, "init", "outside git", "--no-agents")
    run(plain, "note", "add", "a note")
    assert not list(plain.rglob("kept.jsonl"))


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="chmod does not stop root")
def test_with_gits_folder_read_only_the_copy_is_kept_beside_the_record_and_never_counted_as_a_record_file(home):
    root = record(home)
    rbdir = Path(git(root, "rev-parse", "--absolute-git-dir")) / "rb"
    rbdir.mkdir(exist_ok=True)
    for d in [rbdir, *[p for p in rbdir.rglob("*") if p.is_dir()]]:
        d.chmod(0o555)
    try:
        rid = add_run(root, "75.1")
        assert (root / ".rb" / "journal" / "kept.jsonl").is_file()
        repo = run(root, "status")["data"]["repository"]
        assert not any("journal" in f["path"] for f in repo["not_committed_files"]), repo
        git(root, "checkout", "--", ".")
        assert taken(root)["records"] == [{"kind": "run", "id": rid}]
    finally:
        for d in [rbdir, *[p for p in rbdir.rglob("*") if p.is_dir()]]:
            d.chmod(0o755)


def test_rb_remove_takes_the_kept_copy_and_says_so(home):
    root = record(home)
    add_run(root, "75.1")
    plan = run(root, "remove", "--dry-run")["data"]["remove"]
    assert any(i["path"] == ".git/rb/journal/main/" and i["why"] == "rb's copy of what it wrote to this record" for i in plan), plan


def test_rb_open_mcp_and_the_sdk_say_it(home, monkeypatch):
    root = record(home)
    add_run(root, "75.1")
    git(root, "checkout", "--", ".")
    monkeypatch.chdir(root)
    for k in AGENT_VARS:
        monkeypatch.delenv(k, raising=False)
    server = Open(root)
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(server.url + "/state.json", timeout=30) as resp:
            state = json.loads(resp.read().decode("utf-8"))
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(server.url + "/", timeout=30) as resp:
            page = resp.read().decode("utf-8")
    finally:
        server.close()
    assert state["home"]["taken_out"] == TOOK      # rb ui's home draws it too
    assert TOOK in page
    mcp = Mcp(root=root)
    try:
        mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "clientInfo": {"name": "took-client"}}})
        answer = mcp.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "status", "arguments": {}}})["result"]
    finally:
        mcp.close()
    assert answer["structuredContent"]["data"]["taken_out"]["said"] == TOOK
    import rabbit_brain
    assert rabbit_brain.open(root).status()["taken_out"]["said"] == TOOK


def _old_rb_keeps_no_copy():
    """Whether RB_OLD_BIN names an rb that keeps no copy of its writes (before 0.8.5): its note is then in neither the
    log nor rb's copy. 0.8.5 and later keep one, so their note reads as taken out with the rest."""
    old = os.environ.get("RB_OLD_BIN")
    if not old:
        return False
    try:
        return b"kept.jsonl" not in Path(shutil.which(old) or old).read_bytes()
    except OSError:
        return False


@pytest.mark.skipif(not _old_rb_keeps_no_copy(), reason="RB_OLD_BIN names an older rb that keeps no copy of its writes (0.8.4 or before)")
def test_a_note_an_older_rb_wrote_between_two_writes_leaves_both_caught(home):
    root = record(home)
    a = add_run(root, "75.1")
    note = run(root, "note", "add", "by an older rb", "-e", "int8", bin_=[os.environ["RB_OLD_BIN"]])["data"]["object"]["id"]
    b = add_run(root, "75.2")
    git(root, "checkout", "--", ".")
    assert {r["id"] for r in taken(root)["records"]} == {a, b}
    # the older rb's write is in neither the log nor rb's copy: named beside what git took out, before --restore sets it
    # aside (S6)
    said = f"1 more record on disk is in neither the log nor rb's copy (note {note}): an older rb, or a hand, wrote it."
    item = [i for i in run(root, "status")["data"]["open"] if i["code"] == "taken_out_by_git"][0]
    assert said in item["what"], item
    assert said.rstrip(".") in text(root, "doctor")
    # the older rb reads the record as it stands, and never sees the copy
    subprocess.run([os.environ["RB_OLD_BIN"], "status", "--json"], cwd=root, env=environment(), capture_output=True, check=False)


def test_a_finding_never_meets_its_rule_on_what_git_left_and_every_gate_fails_closed(home, tmp_path, monkeypatch):
    """B1 (MP-1110-1703, gates fail closed): three committed runs meet c1; three more that miss are recorded and not
    committed; then git checkout -- . takes them out. The finding rests on what git took out: it names those runs as an
    integrity problem, its rule reads undetermined (never "criterion met" on what is left), and every --fail-on gate
    fails, as 0.8.3's did when it read the three runs as edited outside rb. rb show, rb open's rule card, rb ui's card,
    the page and the JSON say it. Fails on e6d6d7b."""
    from rbtest import object_card
    root = record(home)
    monkeypatch.chdir(root)
    for k in AGENT_VARS:
        monkeypatch.delenv(k, raising=False)
    run(root, "spec", "set", "int8", "seed", "--per-run")
    run(root, "metric", "add", "top1", "--maximize")
    run(root, "criterion", "add", "INT8 loses at most one point", "-e", "int8", "--metric", "change.top1", "--at-least", "-1", "--min-n", "3", "--id", "c1")
    (root / "out").mkdir()

    def seed(n: int, int8: float) -> str:
        f = root / "out" / f"seed{n}.json"
        f.write_text(json.dumps({"int8": {"top1": int8 + n * 0.01}, "fp32": {"top1": 76.0 + n * 0.01}}), encoding="utf-8")
        return run(root, "run", "add", "int8", "--from", f"out/{f.name}", "--set", f"seed={n}")["data"]["object"]["id"]

    for n in (1, 2, 3):
        seed(n, 75.6)
    git(root, "add", "-A")
    git(root, "commit", "-qm", "three seeds")
    missing = [seed(n, 70.0) for n in (4, 5, 6)]
    assert run(root, "show", "--long", "c1")["data"]["finding"]["rule_outcome"] == "misses"
    git(root, "checkout", "--", ".")
    f = run(root, "show", "--long", "c1")["data"]["finding"]
    assert f["rule_outcome"] == "undetermined", f["says"]
    assert not f["says"]["headline"].startswith("criterion met"), f["says"]["headline"]
    took = [p for p in f["integrity"] if p["code"] == "taken_out_by_git"]
    assert took and all(r in took[0]["text"] for r in missing), f["integrity"]
    assert took[0]["text"].startswith("3 runs it rests on were taken out of the record by git: ")
    assert "3 more runs taken out of the record by git" in f["says"]["evidence"], f["says"]["evidence"]
    shown = text(root, "show", "--long", "c1")
    assert "criterion undetermined" in shown and "taken out of the record by git" in shown and "criterion met" not in shown
    for g in ("unestablished", "integrity", "unmet", "late", "stale", "checks", "undecided", "untested", "unresolved", "unknown"):
        out = run(root, "status", "--fail-on", g, code=1)
        assert out["data"]["outcome"]["passed"] is False, g
    # rb ui's finding card and rb open's rule card draw the same finding; the page says it too
    card = json.dumps(object_card("c1", root))
    assert "taken out of the record by git" in card and "criterion met" not in card
    subprocess.run([*command(), "page", "--out", str(tmp_path / "p.html")], cwd=root, env=environment(), check=True, capture_output=True)
    page = (tmp_path / "p.html").read_text(encoding="utf-8")
    assert "were taken out of the record by git" in page
    server = Open(root)
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(server.url + "/", timeout=30) as resp:
            live = resp.read().decode("utf-8")
    finally:
        server.close()
    assert "3 runs it rests on were taken out of the record by git" in live
    # put back, the six runs read again and the rule misses
    run(root, "doctor", "--restore")
    assert run(root, "show", "--long", "c1")["data"]["finding"]["rule_outcome"] == "misses"


def test_an_exposure_or_an_eval_run_git_took_out_is_named_and_the_rule_reads_undetermined(home, tmp_path, monkeypatch):
    """N1 (MP-1110-2007, the 6b's s11 and s12): the finding's timing reads the exposures of the resources its runs
    evaluate on, and other experiments' runs that evaluate on them. Three committed seeds read holdout in an eval role;
    an exposure dated before the declaration is recorded and not committed; git checkout -- . takes it out. The finding
    must not read "declared before the evidence" and "criterion met" on what is left: it names the resource as taken
    out, its rule reads undetermined, and every gate fails. The same for another experiment's eval run on holdout.
    Fails on 0ae4f31."""
    root = record(home)
    monkeypatch.chdir(root)
    for k in AGENT_VARS:
        monkeypatch.delenv(k, raising=False)
    run(root, "spec", "set", "int8", "seed", "--per-run")
    run(root, "metric", "add", "top1", "--maximize")
    run(root, "resource", "add", "holdout", "--type", "dataset", "--name", "held-out images", "--url", "https://data.example.org/holdout")
    run(root, "criterion", "add", "INT8 loses at most one point", "-e", "int8", "--metric", "change.top1", "--at-least", "-1", "--min-n", "3", "--id", "c1")
    (root / "out").mkdir()
    for n in (1, 2, 3):
        f = root / "out" / f"seed{n}.json"
        f.write_text(json.dumps({"int8": {"top1": 75.6 + n * 0.01}, "fp32": {"top1": 76.0 + n * 0.01}}), encoding="utf-8")
        run(root, "run", "add", "int8", "--from", f"out/{f.name}", "--set", f"seed={n}", "--input", "holdout:eval")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "three seeds")
    assert run(root, "show", "--long", "c1")["data"]["finding"]["timing"] == "before"

    def taken_and_closed(what: str):
        f = run(root, "show", "--long", "c1")["data"]["finding"]
        assert f["rule_outcome"] == "undetermined", (what, f["says"])
        took = [p for p in f["integrity"] if p["code"] == "taken_out_by_git"]
        assert took and what in took[0]["text"], f["integrity"]
        shown = text(root, "show", "--long", "c1")
        assert "criterion met" not in shown and "taken out of the record by git" in shown, shown
        for g in ("unestablished", "integrity", "unmet", "late"):
            assert run(root, "status", "--fail-on", g, code=1)["data"]["outcome"]["passed"] is False, g

    run(root, "resource", "expose", "holdout", "inspected", "--at", "2020-01-01", "--why", "looked at cases")
    assert run(root, "show", "--long", "c1")["data"]["finding"]["timing"] == "after_exposure"
    git(root, "checkout", "--", ".")
    taken_and_closed("1 write to a resource it rests on was taken out of the record by git: holdout")
    subprocess.run([*command(), "page", "--out", str(tmp_path / "p.html")], cwd=root, env=environment(), check=True, capture_output=True)
    assert "it rests on was taken out of the record by git" in (tmp_path / "p.html").read_text(encoding="utf-8")
    run(root, "doctor", "--restore")
    assert run(root, "show", "--long", "c1")["data"]["finding"]["timing"] == "after_exposure"
    git(root, "add", "-A")
    git(root, "commit", "-qm", "the exposure")
    # another experiment's run that evaluates on holdout is an exposure too
    run(root, "experiment", "add", "INT4 against FP32", "--id", "int4", "--baseline", "fp32", "--candidate", "int4")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "int4")
    other = run(root, "run", "add", "int4", "int4.top1=70", "fp32.top1=76", "--input", "holdout:eval", "--produced-at", "2020-01-02")["data"]["object"]["id"]
    git(root, "checkout", "--", ".")
    taken_and_closed(f"1 run it rests on was taken out of the record by git: {other}")


def test_when_every_run_is_taken_out_no_surface_says_there_is_no_evidence(home, tmp_path, monkeypatch):
    """S-d (MP-1110-2007): rb keeps the runs git took out, so the finding, its timing line, the inquiry and the rule card
    say the evidence was taken out, never "no evidence recorded yet", "No results yet." or "No recorded run is read
    yet.", on rb show, rb status, rb context, rb ui's card, rb open and the page. Fails on e47ada5."""
    from rbtest import object_card
    root = record(home)
    monkeypatch.chdir(root)
    for k in AGENT_VARS:
        monkeypatch.delenv(k, raising=False)
    run(root, "question", "add", "Does INT8 cost accuracy?", "--id", "q-acc")
    run(root, "spec", "set", "int8", "seed", "--per-run")
    run(root, "metric", "add", "top1", "--maximize")
    run(root, "criterion", "add", "INT8 loses at most one point", "-e", "int8", "-q", "q-acc", "--metric", "change.top1", "--at-least", "-1", "--min-n", "3", "--id", "c1")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "the rule")
    (root / "out").mkdir()
    for n in (1, 2, 3):
        f = root / "out" / f"seed{n}.json"
        f.write_text(json.dumps({"int8": {"top1": 75.6 + n * 0.01}, "fp32": {"top1": 76.0 + n * 0.01}}), encoding="utf-8")
        run(root, "run", "add", "int8", "--from", f"out/{f.name}", "--set", f"seed={n}")
    git(root, "checkout", "--", ".")
    gone = ("no evidence recorded yet", "No results yet.", "No recorded run is read yet.")
    f = run(root, "show", "--long", "c1")["data"]["finding"]
    assert f["says"]["timing"].endswith("its evidence was taken out of the record by git"), f["says"]["timing"]
    inquiry = json.dumps(run(root, "status")["data"])
    assert "no evidence recorded yet" not in inquiry
    for argv in (("show", "--long", "c1"), ("status", "--full"), ("context",), ("show", "--long", "q-acc")):
        said = text(root, *argv)
        assert "taken out of the record by git" in said and not any(g in said for g in gone), (argv, said)
    card = json.dumps(object_card("c1", root))
    assert "timing: declared; its evidence was taken out of the record by git" in card and not any(g in card for g in gone), card
    subprocess.run([*command(), "page", "--out", str(tmp_path / "p.html")], cwd=root, env=environment(), check=True, capture_output=True)
    page = (tmp_path / "p.html").read_text(encoding="utf-8")
    assert "Its results were taken out of the record by git." in page and not any(g in page for g in gone)
    server = Open(root)
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(server.url + "/", timeout=30) as resp:
            live = resp.read().decode("utf-8")
    finally:
        server.close()
    assert "Its runs were taken out of the record by git" in live and not any(g in live for g in gone)


def test_spec_verify_says_the_commit_once(home):
    """MP-1110-0133 later 4: rb spec verify said "@b0cb3ec @b0cb3ec" of a source in a committed file."""
    root = record(home)
    (root / "configs").mkdir()
    (root / "configs" / "train.yaml").write_text("optim:\n  lr: 0.0003\n", encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "config")
    sha = git(root, "rev-parse", "HEAD")[:7]
    run(root, "spec", "set", "int8", "optim.lr", "--source", "configs/train.yaml#optim.lr", "--commit", sha)
    said = text(root, "spec", "verify", "int8")
    line = [l for l in said.splitlines() if "optim.lr: verified in" in l][0]
    assert line.count(f"@{sha}") == 1, line


def test_spec_set_from_names_the_commit_after_its_at_as_its_lines_do(home):
    """f13g item 6 (the 0.8.4 docs round): rb spec set --from's summary said "3 settings from configs/train.yaml
    @234b05955a46", 12 characters after "@", where its lines for each setting, rb spec verify and the design listing
    keep 7, as 0.8.4's CHANGELOG says of a source's short form. The record keeps all 40. Fails on f73697b."""
    root = record(home)
    (root / "configs").mkdir()
    (root / "configs" / "train.yaml").write_text("optim:\n  lr: 0.0003\n  wd: 0.01\n", encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "config")
    full = git(root, "rev-parse", "HEAD")
    said = subprocess.run([*command(), "spec", "set", "int8", "--from", "configs/train.yaml", "--keys", "optim.*"], cwd=root, env=environment(), capture_output=True, text=True).stdout
    head = said.splitlines()[0]
    assert head == f"int8: 2 settings from configs/train.yaml @{full[:7]} · 2 verified", said
    assert f"@{full[:8]}" not in said, said
    st = run(root, "show", "--long", "int8/optim.lr")["data"]
    assert full in json.dumps(st), st


def lens_base(home: Path) -> Path:
    """The record every case of the lens test starts from, committed: q1, int8 with lr and a per-run seed, c1 on
    change.top1 linked to q1 with an interpretation, an analysis of another experiment's run, and three seeds that meet
    c1 reading holdout in an eval role."""
    root = record(home, "lens-base")
    run(root, "question", "add", "Does INT8 cost accuracy?", "--id", "q1")
    run(root, "experiment", "file", "int8", "q1")
    run(root, "spec", "set", "int8", "seed", "--per-run")
    run(root, "spec", "set", "int8", "lr", "0.1", "--source", "train.py:1")
    run(root, "resource", "add", "holdout", "--type", "dataset", "--name", "held-out images", "--url", "https://data.example.org/holdout")
    run(root, "criterion", "add", "INT8 loses at most one point", "-e", "int8", "-q", "q1", "--metric", "change.top1", "--at-least", "-1",
        "--min-n", "3", "--id", "c1", "--when-met", "INT8 stays within the accuracy budget")
    (root / "out").mkdir()
    for n in (1, 2, 3):
        f = root / "out" / f"seed{n}.json"
        f.write_text(json.dumps({"int8": {"top1": 75.6 + n * 0.01}, "fp32": {"top1": 76.0 + n * 0.01}}), encoding="utf-8")
        run(root, "run", "add", "int8", "--from", f"out/{f.name}", "--set", f"seed={n}", "--input", "holdout:eval")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "the lens base")
    return root


LENS_CASES = {
    # what the reading reads: the write that changes it, and the id the finding must name
    "run": (("run", "add", "int8", "int8.top1=70.0", "fp32.top1=76.0", "--set", "seed=4"), None),
    "analysis": (("analysis", "add", "int8", "--compare", "int8/fp32:top1=-0.4[-0.6,-0.2]", "--method", "paired bootstrap over images"), None),
    "criterion": (("criterion", "file", "c1", "q1", "--when-met", "INT8 is within budget", "--why", "narrower words"), "c1"),
    "variant": (("variant", "add", "int8", "int4", "--role", "ablation"), "int8/int4"),
    "experiment": (("spec", "set", "int8", "optim.momentum", "0.9", "--no-verify"), "int8"),
    "metric by its name": (("metric", "add", "top1", "--maximize"), "top1"),
    "metric by an alias": (("metric", "add", "acc", "--maximize", "--alias", "top1"), "acc"),
    "a call on the criterion": (("decide", "c1", "investigate", "--why", "one more seed"), None),
    "a call on a setting": (("decide", "int8/lr", "accept", "--why", "read it off the config"), None),
    "an exposure": (("resource", "expose", "holdout", "inspected", "--at", "2020-01-01", "--why", "looked at cases"), "holdout"),
    "an eval run elsewhere": (None, None),
}


@pytest.mark.parametrize("case", sorted(LENS_CASES))
def test_every_record_the_reading_reads_is_named_when_git_takes_its_write_out(home, case):
    """MP-1110-2303's lens (item 1 of f13g): taken_for takes what a finding rests on from what its reading read, so a
    write of each kind the reading reads, taken out by git checkout -- ., is named on the finding, and its rule reads
    undetermined. B1 (a catalogue entry named by an alias) failed on 33a939c: rule met, integrity ok."""
    base = home / "lens-base"
    if not base.exists():
        lens_base(home)
    root = home / "lens"
    shutil.copytree(base, root)
    argv, named = LENS_CASES[case]
    if case == "an eval run elsewhere":
        run(root, "experiment", "add", "INT4 against FP32", "--id", "int4", "--baseline", "fp32", "--candidate", "int4")
        git(root, "add", "-A")
        git(root, "commit", "-qm", "int4")
        argv = ("run", "add", "int4", "int4.top1=70", "fp32.top1=76", "--input", "holdout:eval", "--produced-at", "2020-01-02")
    out = run(root, *argv)
    named = named or out["data"]["object"]["id"]
    before = run(root, "show", "--long", "c1")["data"]["finding"]
    git(root, "checkout", "--", ".")
    assert taken(root), case
    f = run(root, "show", "--long", "c1")["data"]["finding"]
    took = [p for p in f["integrity"] if p["code"] == "taken_out_by_git"]
    assert took and named in took[0]["text"], (case, f["integrity"], f["says"])
    assert f["rule_outcome"] == "undetermined", (case, f["says"])
    run(root, "doctor", "--restore")
    assert run(root, "show", "--long", "c1")["data"]["finding"]["rule_outcome"] == before["rule_outcome"], case


def test_a_question_reword_git_took_out_is_said_and_the_interpretation_waits_for_it(home):
    """MP-1110-2303's B2: a reword of q1, taken out by git, left the inquiry reading "Interpretation active for c1"
    with no integrity line, and rb show q1 said q1 itself was taken out. The inquiry says the reword was taken out and
    no interpretation is active until rb doctor --restore, after which it reads as before the checkout. Fails on
    33a939c."""
    base = home / "lens-base"
    if not base.exists():
        lens_base(home)
    root = home / "lens"
    shutil.copytree(base, root)

    def row():
        q = next(q for q in run(root, "status")["data"]["questions"] if q["id"] == "q1")
        return next(r for r in q["inquiry"]["direct"] if r["criterion"] == "c1")

    assert row()["interpretation_active"] is True
    run(root, "question", "reword", "q1", "Does INT8 cost top-1 accuracy on the held-out set?", "--why", "narrower")
    reworded = row()
    assert reworded["interpretation_active"] is False and "question_link_outdated" in {p["code"] for p in reworded["integrity"]}
    git(root, "checkout", "--", ".")
    r = row()
    assert r["interpretation_active"] is False, r
    took = [p for p in r["integrity"] if p["code"] == "taken_out_by_git"]
    assert took and "q1" in took[0]["text"], r["integrity"]
    shown = text(root, "show", "--long", "q1")
    assert "A write to question q1 was taken out of the record by git" in shown, shown
    assert "question q1 was taken out of the record by git" not in shown.replace("A write to question q1", ""), shown
    assert "Interpretation active for c1" not in shown, shown
    run(root, "doctor", "--restore")
    r = row()
    assert r["interpretation_active"] is False and "question_link_outdated" in {p["code"] for p in r["integrity"]}, r
    assert "taken_out_by_git" not in {p["code"] for p in r["integrity"]}


def test_a_catalogue_entry_named_by_an_alias_git_took_out_is_named_and_the_rule_reads_undetermined(home):
    """MP-1110-2303's B1, the 6b's case: c1 reads change.ce, three runs committed; rb metric add loss --minimize --alias
    ce, not committed; git checkout -- . takes it out. On 33a939c the finding read "criterion met · higher" with
    integrity ok, where with the entry it read a loss. It names the entry and its rule reads undetermined."""
    root = record(home)
    run(root, "spec", "set", "int8", "seed", "--per-run")
    run(root, "criterion", "add", "INT8 adds at most 2 cross entropy", "-e", "int8", "--id", "c1", "--metric", "change.ce", "--at-most", "2")
    for s in (1, 2, 3):
        run(root, "run", "add", "int8", f"fp32.ce=1.0{s}", f"int8.ce=1.5{s}", "--set", f"seed={s}")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "three seeds")
    run(root, "metric", "add", "loss", "--minimize", "--alias", "ce")
    assert run(root, "show", "--long", "c1")["data"]["finding"]["result"] == "loss"
    git(root, "checkout", "--", ".")
    f = run(root, "show", "--long", "c1")["data"]["finding"]
    assert f["rule_outcome"] == "undetermined", f["says"]
    took = [p for p in f["integrity"] if p["code"] == "taken_out_by_git"]
    assert took and took[0]["text"].startswith("1 metric it rests on was taken out of the record by git: loss."), f["integrity"]
    for g in ("unmet", "integrity", "late", "unestablished", "unresolved"):
        assert run(root, "status", "--fail-on", g, code=1)["data"]["outcome"]["passed"] is False, g
    run(root, "doctor", "--restore")
    f = run(root, "show", "--long", "c1")["data"]["finding"]
    assert f["result"] == "loss" and not f["integrity"], f


def test_with_every_run_taken_out_no_axis_says_untested_or_planned(home, tmp_path, monkeypatch):
    """MP-1110-2303's S4: rb kept the three runs git took out, so the headline, the result, the experiment's state and
    the inquiry's "by result" say the evidence was taken out by git, never "untested" or "planned" as if nothing was
    recorded, on rb show, rb status, rb context, rb ui's card, rb open and the page. The JSON's codes stay (result
    untested, state planned) and its words say it (says, state_said, results_taken_out). Fails on f73697b."""
    from rbtest import object_card
    root = record(home)
    monkeypatch.chdir(root)
    for k in AGENT_VARS:
        monkeypatch.delenv(k, raising=False)
    run(root, "question", "add", "Does INT8 cost accuracy?", "--id", "q-acc")
    run(root, "experiment", "file", "int8", "q-acc")
    run(root, "spec", "set", "int8", "seed", "--per-run")
    run(root, "metric", "add", "top1", "--maximize")
    run(root, "criterion", "add", "INT8 loses at most one point", "-e", "int8", "-q", "q-acc", "--metric", "change.top1", "--at-least", "-1", "--min-n", "3", "--id", "c1")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "the rule")
    for n in (1, 2, 3):
        run(root, "run", "add", "int8", f"int8.top1=75.{n}", f"fp32.top1=76.{n}", "--set", f"seed={n}")
    git(root, "checkout", "--", ".")
    f = run(root, "show", "--long", "c1")["data"]["finding"]
    assert f["result"] == "untested" and f["says"]["headline"] == "criterion undetermined · taken out by git", f["says"]
    assert f["says"]["result"] == "its evidence was taken out of the record by git", f["says"]
    st = run(root, "status")["data"]
    e = next(x for x in st["experiments"] if x["id"] == "int8")
    assert e["state"] == "planned" and e["state_said"] == "3 runs taken out by git", e
    assert "Experiment int8: INT8 against FP32 · 3 runs taken out by git" in text(root, "show", "--long", "int8")
    q = text(root, "show", "--long", "q-acc")
    assert "by result: 1 taken out by git" in q and "is planned" not in q and "1 untested" not in q, q
    assert "experiment int8 has 3 runs taken out by git" in q, q
    assert "(3 runs taken out by git)" in text(root, "context")
    assert "planned" not in text(root, "status", "--full").replace("test planned in advance", ""), text(root, "status", "--full")
    card = json.dumps(object_card("int8", root))
    assert "3 runs taken out by git" in card and "· planned ·" not in card, card
    subprocess.run([*command(), "page", "--out", str(tmp_path / "p.html")], cwd=root, env=environment(), check=True, capture_output=True)
    page = (tmp_path / "p.html").read_text(encoding="utf-8")
    assert "Experiment · 3 runs taken out by git" in page and "Experiment · planned" not in page
    assert "· taken out by git" in page or "taken out by git" in page
    server = Open(root)
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(server.url + "/", timeout=30) as resp:
            live = resp.read().decode("utf-8")
    finally:
        server.close()
    assert "3 runs taken out by git" in live and "Experiment · planned" not in live


def test_the_timing_never_says_before_the_evidence_while_what_it_reads_is_taken_out(home, tmp_path):
    """MP-1110-2303's S1: an exposure the timing reads, taken out by git, left the timing "declared before the evidence
    (by production times)". The timing says rb cannot read it while what it reads is taken out (order unknown), on rb
    show, the page and rb open's data, and reads after_exposure again once rb doctor --restore puts it back. Fails on
    f73697b."""
    root = record(home)
    run(root, "spec", "set", "int8", "seed", "--per-run")
    run(root, "metric", "add", "top1", "--maximize")
    run(root, "resource", "add", "holdout", "--type", "dataset", "--name", "held-out images", "--url", "https://data.example.org/holdout")
    run(root, "criterion", "add", "INT8 loses at most one point", "-e", "int8", "--metric", "change.top1", "--at-least", "-1", "--min-n", "3", "--id", "c1")
    (root / "out").mkdir()
    for n in (1, 2, 3):
        f = root / "out" / f"seed{n}.json"
        f.write_text(json.dumps({"int8": {"top1": 75.6 + n * 0.01}, "fp32": {"top1": 76.0 + n * 0.01}}), encoding="utf-8")
        run(root, "run", "add", "int8", "--from", f"out/{f.name}", "--set", f"seed={n}", "--input", "holdout:eval")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "three seeds")
    assert run(root, "show", "--long", "c1")["data"]["finding"]["timing"] == "before"
    run(root, "resource", "expose", "holdout", "inspected", "--at", "2020-01-01", "--why", "looked at cases")
    git(root, "checkout", "--", ".")
    f = run(root, "show", "--long", "c1")["data"]["finding"]
    assert f["timing"] == "unknown", f["says"]
    assert f["says"]["timing"] == "declared; order unknown: rb cannot read it while what it reads is taken out of the record by git", f["says"]
    shown = text(root, "show", "--long", "c1")
    assert "declared before the evidence" not in shown, shown
    subprocess.run([*command(), "page", "--out", str(tmp_path / "p.html")], cwd=root, env=environment(), check=True, capture_output=True)
    page = (tmp_path / "p.html").read_text(encoding="utf-8")
    assert "rb cannot read it while what it reads is taken out of the record by git" in page
    server = Open(root)
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(server.url + "/state.json", timeout=30) as resp:
            live = resp.read().decode("utf-8")
    finally:
        server.close()
    assert "declared before the evidence" not in live
    run(root, "doctor", "--restore")
    assert run(root, "show", "--long", "c1")["data"]["finding"]["timing"] == "after_exposure"


def test_the_rule_line_says_part_of_its_evidence_and_a_stash_as_the_integrity_line_does(home):
    """MP-1110-2303's S2: where three of six runs are taken out and a reading of three remains, the rule line said "its
    evidence was taken out of the record by git (...)" as if all were; an exposure was named "(holdout)"; and in a
    stash the rule line said "taken out" where the integrity line beside it said "in git's stash". Fails on f73697b."""
    base = home / "s2-base"
    root0 = record(home, "s2-base")
    run(root0, "spec", "set", "int8", "seed", "--per-run")
    run(root0, "metric", "add", "top1", "--maximize")
    run(root0, "resource", "add", "holdout", "--type", "dataset", "--name", "held-out images", "--url", "https://data.example.org/holdout")
    run(root0, "criterion", "add", "INT8 loses at most one point", "-e", "int8", "--metric", "change.top1", "--at-least", "-1", "--min-n", "3", "--id", "c1")
    for n in (1, 2, 3):
        run(root0, "run", "add", "int8", f"int8.top1=75.{n}", f"fp32.top1=76.{n}", "--set", f"seed={n}", "--input", "holdout:eval")
    git(root0, "add", "-A")
    git(root0, "commit", "-qm", "three seeds")

    def rule(case: str, *undo: str) -> str:
        root = home / case
        shutil.copytree(base, root)
        if case == "exposure":
            run(root, "resource", "expose", "holdout", "inspected", "--at", "2020-01-01", "--why", "looked at cases")
        else:
            for n in (4, 5, 6):
                run(root, "run", "add", "int8", f"int8.top1=70.{n}", f"fp32.top1=76.{n}", "--set", f"seed={n}")
        git(root, *undo)
        f = run(root, "show", "--long", "c1")["data"]["finding"]
        assert f["comparison"] and f["rule_outcome"] == "undetermined", f["says"]
        return f["says"]["rule"], f["says"]["integrity"]

    said, _ = rule("partial", "checkout", "--", ".")
    assert said.startswith("criterion undetermined: part of its evidence was taken out of the record by git (run"), said
    said, _ = rule("exposure", "checkout", "--", ".")
    assert "part of its evidence was taken out of the record by git (a write to holdout)" in said, said
    said, integrity = rule("stash", "stash")
    assert "in git's stash" in integrity, integrity
    assert "part of its evidence is in git's stash (" in said and "git stash pop stash@{0} puts it back" in said, said
    assert "taken out" not in said, said


def test_an_analysis_elsewhere_that_supersedes_one_the_reading_reads_is_named(home):
    """The f13g review's blocker: an analysis in another experiment that supersedes c1's analysis changes c1's reading
    (back to rb's reading of the runs); taken out by git, the finding read the superseded analysis again, "criterion
    met", integrity ok. It is named, and the rule reads undetermined."""
    base = home / "lens-base"
    if not base.exists():
        lens_base(home)
    root = home / "lens"
    shutil.copytree(base, root)
    run(root, "experiment", "add", "Other", "--id", "e2", "--baseline", "a", "--candidate", "b")
    a1 = run(root, "analysis", "add", "int8", "--compare", "int8/fp32:top1=-0.3[-0.5,-0.1]", "--method", "paired bootstrap")["data"]["object"]["id"]
    git(root, "add", "-A")
    git(root, "commit", "-qm", "an analysis")
    assert a1 in run(root, "show", "--long", "c1")["data"]["finding"]["says"]["comparison"]
    a2 = run(root, "analysis", "add", "e2", "--compare", "b/a:top1=0.15[0.05,0.25]", "--method", "boot2", "--supersedes", a1, "--why", "redo")["data"]["object"]["id"]
    assert a1 not in run(root, "show", "--long", "c1")["data"]["finding"]["says"]["comparison"]
    git(root, "checkout", "--", ".")
    f = run(root, "show", "--long", "c1")["data"]["finding"]
    took = [p for p in f["integrity"] if p["code"] == "taken_out_by_git"]
    assert took and a2 in took[0]["text"] and f["rule_outcome"] == "undetermined", (f["integrity"], f["says"])


def test_an_unrelated_write_git_took_out_leaves_a_later_link_s_interpretation_alone(home):
    """The f13g review's should: a link declared after its criterion reads its finding at the link's time, inside the
    row's trace; taken_for's own reads leaked into that trace, so an unrelated metric taken out made the row read
    taken out and its interpretation inactive. Nothing unrelated is named."""
    base = home / "lens-base"
    if not base.exists():
        lens_base(home)
    root = home / "lens"
    shutil.copytree(base, root)
    run(root, "question", "add", "Is INT8 shippable?", "--id", "q2")
    run(root, "criterion", "file", "c1", "q2", "--when-met", "INT8 can ship", "--why", "a later link")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "q2")
    run(root, "metric", "add", "latency_ms", "--minimize")
    git(root, "checkout", "--", ".")
    assert taken(root)
    q = next(q for q in run(root, "status")["data"]["questions"] if q["id"] == "q2")
    r = next(r for r in q["inquiry"]["direct"] if r["criterion"] == "c1")
    assert r["interpretation_active"] is True and not r["integrity"], r
    assert not run(root, "show", "--long", "c1")["data"]["finding"]["integrity"]


def test_a_link_to_the_question_git_took_out_is_named_on_the_question(home):
    """The f13g review's later: a criterion of another experiment filed to q1, its link taken out by git, dropped out of
    q1's reading with nothing said. The question's reading names the write."""
    base = home / "lens-base"
    if not base.exists():
        lens_base(home)
    root = home / "lens"
    shutil.copytree(base, root)
    run(root, "experiment", "add", "Other", "--id", "e2", "--baseline", "a", "--candidate", "b")
    run(root, "criterion", "add", "b is at most 1", "-e", "e2", "--metric", "b.top1", "--at-most", "1", "--id", "c9")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "c9")
    run(root, "criterion", "file", "c9", "q1", "--when-met", "b is small", "--why", "it bears on q1")
    git(root, "checkout", "--", ".")
    q = next(q for q in run(root, "status")["data"]["questions"] if q["id"] == "q1")
    assert "taken_out_by_git" in {p["code"] for p in q["inquiry"]["integrity"]}, q["inquiry"]["integrity"]
    assert "1 write to a criterion it rests on was taken out of the record by git: c9" in text(root, "show", "--long", "q1")
