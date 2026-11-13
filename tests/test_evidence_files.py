"""EVIDENCE.md E5: a finding says when its evidence changed.

A file a counted run named that changed or went since the run was recorded is a problem of the finding only where this
checkout held the bytes the run recorded and rb holds no copy of them (the run's kept tree, a commit). Where a copy is
held it is a note that says where; in a clone that never held the bytes it is a note, "not in this clone". The finding
keeps its recorded number. rb doctor reports the problem, rb status --fail-on integrity fails on it, and rb reconcile
says such a file changed since its run, never proposing it as a run to average in.
"""
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from rbtest import as_the_user, command, environment

pytestmark = pytest.mark.skipif(shutil.which("git") is None or sys.platform == "win32", reason="needs git")
GIT = shutil.which("git")
METRICS = {"int8": {"top1": 75.5}, "fp32": {"top1": 76.0}}


def run_rb(cwd: Path, *argv: str) -> subprocess.CompletedProcess:
    return subprocess.run([*command(), *argv], cwd=cwd, env=environment(), capture_output=True, text=True, stdin=subprocess.DEVNULL)


def rb(cwd: Path, *argv: str) -> dict:
    r = run_rb(cwd, *argv, "--json")
    assert r.returncode in (0, 1), (argv, r.returncode, r.stdout, r.stderr)
    return json.loads(r.stdout)


def git(cwd: Path, *argv: str) -> str:
    return subprocess.run([GIT, *argv], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def make(tmp_path, monkeypatch, ignored: bool) -> Path:
    as_the_user(monkeypatch)
    cfg = tmp_path / "gitconfig"
    cfg.write_text("[user]\n\temail = ana@example.org\n\tname = ana\n[init]\n\tdefaultBranch = main\n", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(cfg))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("RB_ACTOR", "human:ana")
    r = tmp_path / "int8-resnet50"
    r.mkdir()
    git(r, "init", "-q")
    (r / "train.py").write_text("lr = 0.1\n", encoding="utf-8")
    if ignored:
        (r / ".gitignore").write_text("out/\n", encoding="utf-8")
    rb(r, "init", "INT8 ResNet-50", "--no-agents")
    rb(r, "experiment", "add", "INT8 against FP32", "--id", "int8", "--baseline", "fp32", "--candidate", "int8")
    rb(r, "metric", "add", "top1", "--maximize")
    rb(r, "criterion", "add", "INT8 loses at most one point of top-1", "-e", "int8", "--id", "c1", "--metric", "change.top1", "--at-least", "-1")
    git(r, "add", "-A")
    git(r, "commit", "-qm", "the record")
    (r / "out" / "seed1").mkdir(parents=True)
    (r / "out" / "seed1" / "metrics.json").write_text(json.dumps(METRICS), encoding="utf-8")
    run = rb(r, "run", "add", "int8", "--from", "out/seed1/metrics.json", "--produced-at", "2026-11-09T09:00:00Z")["data"]["object"]["id"]
    git(r, "add", "-A")
    git(r, "commit", "-qm", "the run")
    (r / "run_id").write_text(run, encoding="utf-8")
    return r


def codes(root: Path) -> list:
    return [p["code"] for p in rb(root, "show", "--long", "c1")["data"]["finding"]["integrity"]]


def test_a_finding_whose_evidence_is_as_recorded_says_so(tmp_path, monkeypatch):
    root = make(tmp_path, monkeypatch, ignored=True)
    run = (root / "run_id").read_text()
    shown = run_rb(root, "show", "--long", "c1").stdout
    assert "Evidence: 1 run, each with its files unchanged and its code kept when it was recorded, after the run. rb has not rerun them." in shown
    # only what the run named is said to be as recorded (S2): this run named its file, no config and no input
    shown = run_rb(root, "show", "--long", run).stdout
    assert "Its files are as recorded." in shown and "its config" not in shown and "what it read" not in shown, shown
    assert codes(root) == []


def test_a_commit_given_that_the_repository_lacks_is_never_said_as_code_kept(tmp_path, monkeypatch):
    """B2 (MP-1110-1703): a --commit this repository lacks is said so wherever "as given" is said: rb show <run>, the
    finding's evidence line on rb show <criterion> and the page, and rb show <exp>'s code row, as a page's Reproduce
    reads it. Fails on e6d6d7b."""
    root = make(tmp_path, monkeypatch, ignored=True)
    lacks = "0123456789abcdef0123456789abcdef01234567"
    (root / "out" / "seed2").mkdir(parents=True)
    (root / "out" / "seed2" / "metrics.json").write_text(json.dumps({"int8": {"top1": 75.25}, "fp32": {"top1": 76.0}}), encoding="utf-8")
    added = run_rb(root, "run", "add", "int8", "--from", "out/seed2/metrics.json", "--commit", lacks)
    assert "The code as given: commit 0123456789ab, which this repository does not have." in added.stdout, added.stdout
    run = json.loads(run_rb(root, "show", "--long", "c1", "--json").stdout)["data"]["finding"]["observations"][-1]["run"]
    assert "The code as given: commit 0123456789ab, which this repository does not have." in run_rb(root, "show", "--long", run).stdout
    said = rb(root, "show", "--long", "c1")["data"]["finding"]["says"]["evidence"]
    assert "1 run's code as given not in this repository" in said and "kept as given" not in said, said
    shown = rb(root, "show", "--long", "int8")["data"]
    code = [r for r in (shown.get("rerunnability") or shown["object"]["rerunnability"]) if r["what"] == "code"][0]
    # the one commit named is the one not here: said once (f13f item 5)
    assert code["state"] != "ok" and code["detail"].startswith("commit 0123456789ab · not in this repository (get the code repository to rerun)"), code
    run_rb(root, "page", "--out", str(tmp_path / "p.html"))
    page = (tmp_path / "p.html").read_text(encoding="utf-8")
    assert "code as given not in the repository this page was made from" in page
    assert "The code as given: commit 0123456789ab, which the repository this page was made from did not have." in re.sub(r"<[^>]+>", "", page)


def test_a_finding_says_part_kept_and_no_commit_code_in_the_run_s_own_words(tmp_path, monkeypatch):
    """S4 and S5 (MP-1110-1703): a run whose code rb kept in part reads "kept in part" on its finding, never "not kept";
    one recorded where there was no commit reads "no commit to keep it at"; several runs are "runs'", never "runs's"."""
    root = make(tmp_path, monkeypatch, ignored=True)
    (root / "big.bin").write_bytes(b"\0" * (11 * 1024 * 1024))
    (root / "out" / "seed2").mkdir(parents=True)
    (root / "out" / "seed2" / "metrics.json").write_text(json.dumps({"int8": {"top1": 75.25}, "fp32": {"top1": 76.0}}), encoding="utf-8")
    run_rb(root, "run", "add", "int8", "--from", "out/seed2/metrics.json")
    said = rb(root, "show", "--long", "c1")["data"]["finding"]["says"]["evidence"]
    assert "1 run's code kept in part" in said and "not kept" not in said, said
    # no commit yet
    bare = tmp_path / "bare"
    bare.mkdir()
    git(bare, "init", "-q")
    rb(bare, "init", "INT8", "--no-agents")
    rb(bare, "experiment", "add", "INT8 against FP32", "--id", "int8", "--baseline", "fp32", "--candidate", "int8")
    rb(bare, "criterion", "add", "INT8 loses at most one point", "-e", "int8", "--id", "c1", "--metric", "change.top1", "--at-least", "-1")
    for n in (1, 2):
        run_rb(bare, "run", "add", "int8", f"int8.top1={75.5 + n}", "fp32.top1=76.0")
    said = rb(bare, "show", "--long", "c1")["data"]["finding"]["says"]["evidence"]
    assert "runs's" not in said
    assert "2 runs' code not kept (no commit to keep it at)" in said, said


def test_a_changed_file_with_no_copy_is_a_problem_rb_doctor_and_the_gate_report(tmp_path, monkeypatch):
    root = make(tmp_path, monkeypatch, ignored=True)
    run = (root / "run_id").read_text()
    (root / "out" / "seed1" / "metrics.json").write_text(json.dumps({"int8": {"top1": 79.0}, "fp32": {"top1": 76.0}}), encoding="utf-8")
    f = rb(root, "show", "--long", "c1")["data"]["finding"]
    said = [p for p in f["integrity"] if p["code"] == "evidence_file_changed"]
    assert [p["text"] for p in said] == [f"out/seed1/metrics.json changed since run {run} was recorded, and rb holds no copy of the bytes it recorded: the number can no longer be checked against its file"]
    assert f["comparison"]["estimate"] == pytest.approx(-0.5)          # the recorded number stays the reading's
    assert "Short of whole: out/seed1/metrics.json changed since run " in f["says"]["evidence"]
    assert "1 of 1 changed since it was recorded: out/seed1/metrics.json." in run_rb(root, "show", "--long", run).stdout
    assert run_rb(root, "status", "--fail-on", "integrity").returncode == 1
    doc = run_rb(root, "doctor")
    assert doc.returncode == 1 and "evidence_file_changed" in doc.stdout
    rec = rb(root, "reconcile")["data"]
    assert not [x for x in rec.get("found", []) if x.get("path") == "out/seed1/metrics.json"]
    assert any(u.startswith(f"out/seed1/metrics.json changed since run {run} was recorded") for u in rec.get("unplaced", [])), rec.get("unplaced")


def test_a_deleted_file_with_no_copy_is_a_problem(tmp_path, monkeypatch):
    root = make(tmp_path, monkeypatch, ignored=True)
    run = (root / "run_id").read_text()
    (root / "out" / "seed1" / "metrics.json").unlink()
    texts = [p["text"] for p in rb(root, "show", "--long", "c1")["data"]["finding"]["integrity"]]
    assert f"out/seed1/metrics.json is gone since run {run} was recorded, and rb holds no copy of the bytes it recorded: the number can no longer be checked against its file" in texts
    assert "1 of 1 gone: out/seed1/metrics.json." in run_rb(root, "show", "--long", run).stdout


def test_a_kept_tree_that_holds_the_bytes_makes_it_a_note_that_says_where(tmp_path, monkeypatch):
    """out/ is not ignored, so the run's kept code (recorded with the file untracked) holds the bytes it recorded."""
    root = make(tmp_path, monkeypatch, ignored=False)
    run = (root / "run_id").read_text()
    tree = rb(root, "show", "--long", run)["data"]["object"]["receipt"]["attached"]["tree"]
    (root / "out" / "seed1" / "metrics.json").write_text(json.dumps({"int8": {"top1": 79.0}, "fp32": {"top1": 76.0}}), encoding="utf-8")
    f = rb(root, "show", "--long", "c1")["data"]["finding"]
    assert [p["code"] for p in f["integrity"]] == []
    notes = " ".join(c["text"] for c in f["caveats"])
    assert f"out/seed1/metrics.json changed since run {run} was recorded. The kept code holds the bytes rb recorded: git show {tree}:out/seed1/metrics.json" in notes
    # the finding's line never says its files are unchanged where one is not as recorded (review5 S1)
    assert f["says"]["evidence"].startswith("Evidence: 1 run. Short of whole: out/seed1/metrics.json changed since run "), f["says"]["evidence"]
    assert run_rb(root, "status", "--fail-on", "integrity").returncode == 0


def test_a_clone_that_never_held_the_bytes_reads_them_as_not_in_this_clone(tmp_path, monkeypatch):
    root = make(tmp_path, monkeypatch, ignored=True)
    clone = tmp_path / "clone"
    git(tmp_path, "clone", "-q", "--no-local", str(root), str(clone))
    assert not (clone / "out").exists()
    f = rb(clone, "show", "--long", "c1")["data"]["finding"]
    assert [p["code"] for p in f["integrity"]] == []
    assert "out/seed1/metrics.json is not in this clone" in " ".join(c["text"] for c in f["caveats"])
    assert "Short of whole: out/seed1/metrics.json is not in this clone" in f["says"]["evidence"]
    assert run_rb(clone, "status", "--fail-on", "integrity").returncode == 0
    assert run_rb(clone, "doctor").stdout.count("evidence_file_changed") == 0


def test_a_published_page_works_its_runs_files_out_in_the_checkout_it_was_made_from(tmp_path, monkeypatch):
    """rb export --with-record draws its page from the bundle's copy, which holds no file a run named: the runs' files
    are worked out in the exporting checkout (review5 B3), so its problem shows; exported from a clone that never held
    the bytes, no finding gains a problem."""
    root = make(tmp_path, monkeypatch, ignored=True)
    (root / "out" / "seed1" / "metrics.json").unlink()
    for out, cwd in (("pubx", root), ("plain.html", root)):
        if out.endswith(".html"):
            rb(cwd, "page", "--out", str(tmp_path / out))
            page = (tmp_path / out).read_text(encoding="utf-8")
        else:
            rb(cwd, "export", "--with-record", "--url", "https://pages.example.org/int8/", "--out", str(tmp_path / out))
            page = (tmp_path / out / "page.html").read_text(encoding="utf-8")
        assert "rb holds no copy of the bytes it recorded" in page and "When this page was made: 1 of 1 gone" in page, out
    clone = tmp_path / "clone"
    git(tmp_path, "clone", "-q", "--no-local", str(root), str(clone))
    rb(clone, "export", "--with-record", "--url", "https://pages.example.org/int8/", "--out", str(tmp_path / "pubc"))
    page = (tmp_path / "pubc" / "page.html").read_text(encoding="utf-8")
    assert "rb holds no copy of the bytes it recorded" not in page and "not in the repository this page was made from" in page


def test_a_table_that_gains_a_row_is_no_change_to_an_earlier_run_s_evidence(tmp_path, monkeypatch):
    """A row's run is read as its row: a results table that grows breaks no earlier run (review5 B1)."""
    root = make(tmp_path, monkeypatch, ignored=True)
    (root / "out" / "results.csv").write_text("seed,int8.top1,fp32.top1\n1,75.5,76.0\n2,75.6,76.0\n", encoding="utf-8")
    rb(root, "spec", "set", "int8", "seed", "--per-run")
    for seed in ("1", "2"):
        rb(root, "run", "add", "int8", "--from", "out/results.csv", "--where", f"seed={seed}", "--set", "seed=@seed", "--produced-at", "2026-11-09T09:00:00Z")
    with open(root / "out" / "results.csv", "a", encoding="utf-8") as f:
        f.write("3,75.7,76.0\n")
    assert codes(root) == []
    assert run_rb(root, "doctor").stdout.count("evidence_file_changed") == 0


def test_a_look_that_sees_the_change_never_undoes_what_the_journal_saw(tmp_path, monkeypatch):
    """With no kept copy of the run's write (a run an earlier rb recorded), the journal's sighting of the recorded bytes
    says this checkout held them, before and after a look that sees the file change (review5 B2)."""
    root = make(tmp_path, monkeypatch, ignored=True)
    rb(root, "journal", "scan")
    for kept in list(tmp_path.rglob("kept.jsonl")):
        kept.unlink()
    (root / "out" / "seed1" / "metrics.json").write_text(json.dumps({"int8": {"top1": 79.0}, "fp32": {"top1": 76.0}}), encoding="utf-8")
    before = codes(root)
    rb(root, "journal", "scan")
    assert before == codes(root) == ["evidence_file_changed"]


def test_a_changed_config_is_a_note_on_the_finding(tmp_path, monkeypatch):
    root = make(tmp_path, monkeypatch, ignored=False)
    (root / "out" / "seed2").mkdir()
    (root / "out" / "seed2" / "metrics.json").write_text(json.dumps(METRICS), encoding="utf-8")
    (root / "out" / "seed2" / "config.yaml").write_text("seed: 2\n", encoding="utf-8")
    run = rb(root, "run", "add", "int8", "--from", "out/seed2/metrics.json", "--config", "out/seed2/config.yaml", "--again", "--why", "a second execution", "--produced-at", "2026-11-09T09:00:00Z")["data"]["object"]["id"]
    (root / "out" / "seed2" / "config.yaml").write_text("seed: 3\n", encoding="utf-8")
    notes = " ".join(c["text"] for c in rb(root, "show", "--long", "c1")["data"]["finding"]["caveats"])
    assert f"run {run}'s config changed since it was recorded" in notes
    assert "Config: changed since it was recorded." in run_rb(root, "show", "--long", run).stdout


def test_rb_open_rb_ui_and_a_page_say_the_finding_s_evidence(tmp_path, monkeypatch):
    """E5: the reading's evidence line is on rb open's rule card, rb ui's finding card and a page's rule card, the page's
    as of when it was made, as its runs' lines are."""
    import time
    import urllib.request
    from rbtest import object_card
    root = make(tmp_path, monkeypatch, ignored=True)
    (root / "out" / "seed1" / "metrics.json").write_text(json.dumps({"int8": {"top1": 79.0}, "fp32": {"top1": 76.0}}), encoding="utf-8")
    said = rb(root, "show", "--long", "c1")["data"]["finding"]["says"]["evidence"]
    assert said.startswith("Evidence: 1 run. Short of whole: out/seed1/metrics.json changed since run "), said
    assert said in object_card("c1", root)["facts"]
    err = tmp_path / "open.txt"
    with open(err, "wb") as e:
        proc = subprocess.Popen([*command(), "open", "--port", "0"], cwd=root, env=environment(), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=e)
    try:
        deadline = time.monotonic() + 60
        while not (m := re.search(r"^rb open · (http://[0-9.]+:\d+)/$", err.read_text(encoding="utf-8", errors="replace"), flags=re.M)):
            assert proc.poll() is None and time.monotonic() < deadline, err.read_text(encoding="utf-8", errors="replace")
            time.sleep(0.05)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(m.group(1) + "/", timeout=60) as r:
            live = r.read().decode("utf-8")
    finally:
        proc.terminate()
        proc.wait(timeout=30)
    # closed form (owner, Nov 13 2026): the working's row says in plain words what it is, "the runs' files and code"
    EV = "<dt>the runs' files and code</dt>"
    assert f"{EV}<dd>1 run. Short of whole: out/seed1/metrics.json changed since <a href=\"#run-{(root / 'run_id').read_text()}\">run " in live
    # the run inside E5's sentence is named as the page names it, and links to its card (review5 S3)
    run = (root / "run_id").read_text()
    assert re.search(rf'changed since <a href="#run-{run}">run [^<]+</a> was recorded, and rb holds no copy', live), live
    run_rb(root, "page", "--out", str(tmp_path / "p.html"))
    page = (tmp_path / "p.html").read_text(encoding="utf-8")
    assert re.search(rf'changed since <a href="#run-{run}">run [^<]+</a> was recorded, and rb holds no copy', page)
    assert "When this page was made: 1 of 1 changed since it was recorded" in page
    row = re.search(re.escape(EV) + r"<dd>(When this page was made: 1 run\. Short of whole: .*?)</dd>", page)
    assert row and "rb has not rerun them." in row.group(1) and "Evidence:" not in row.group(1), page[:0]
    # the hub's tiers are the export's page: the owner's and a reader's see the same line
    run_rb(root, "export", "--out", str(tmp_path / "ex"))
    assert row.group(1) in (tmp_path / "ex" / "page.html").read_text(encoding="utf-8")


def test_reproduce_marks_a_missing_part_as_its_summary_does_and_says_a_missing_commit_once(tmp_path, monkeypatch):
    """Item 5 of f13f (look-MP-1110-2007, run-reproduce-commit-missing): Reproduce's summary marks a missing part ✗,
    and so does the part's own line, never ○; a code row whose commit is not here names it once ("commit
    0123456789ab · not in this repository (get the code repository to rerun)"), on rb show, rb open, the page and the
    copied recipe's comments. Fails on e47ada5."""
    from rbtest import Open
    import urllib.request
    root = make(tmp_path, monkeypatch, ignored=True)
    lacks = "0123456789abcdef0123456789abcdef01234567"
    rb(root, "experiment", "add", "INT4 against FP32", "--id", "int4", "--baseline", "fp32", "--candidate", "int4")
    run_rb(root, "run", "add", "int4", "int4.top1=70", "fp32.top1=76", "--commit", lacks, "--produced-at", "2026-11-01")
    once = "commit 0123456789ab · not in this repository (get the code repository to rerun)"
    shown = rb(root, "show", "--long", "int4")["data"]
    code = [r for r in (shown.get("rerunnability") or shown["object"]["rerunnability"]) if r["what"] == "code"][0]
    assert code["state"] == "missing" and code["detail"].startswith(once), code
    run_rb(root, "page", "--out", str(tmp_path / "p.html"))
    page = re.sub(r"<[^>]+>", "", (tmp_path / "p.html").read_text(encoding="utf-8"))
    server = Open(root)
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(server.url + "/", timeout=30) as resp:
            live = re.sub(r"<[^>]+>", "", resp.read().decode("utf-8"))
    finally:
        server.close()
    for name, text in (("page", page), ("rb open", live)):
        assert once in text and "0123456789ab (get the code repository" not in text, name
        assert "○ missing" not in text and "✗ missing" in text, name
        assert f"# code: missing · {once}" in text, name


def test_a_run_whose_given_code_is_not_here_gets_no_diff_or_restore_against_the_tree_after_it(tmp_path, monkeypatch):
    """MP-1110-2007's later (f13f item 6): a run whose code was given as a commit this repository lacks gets no "See the
    changes: git diff ..." or "Restore a file: git restore ..." against the tree when it was recorded, which is not its
    code; a run with no code given still does. Fails on e47ada5."""
    root = make(tmp_path, monkeypatch, ignored=True)          # run_id, untracked, makes the tree dirty
    lacks = "0123456789abcdef0123456789abcdef01234567"
    (root / "out" / "seed2").mkdir(parents=True)
    (root / "out" / "seed2" / "metrics.json").write_text(json.dumps({"int8": {"top1": 75.25}, "fp32": {"top1": 76.0}}), encoding="utf-8")
    added = run_rb(root, "run", "add", "int8", "--from", "out/seed2/metrics.json", "--commit", lacks)
    assert "which this repository does not have" in added.stdout and "See the changes" not in added.stdout, added.stdout
    given = rb(root, "show", "--long", "c1")["data"]["finding"]["observations"][-1]["run"]
    out = run_rb(root, "show", "--long", given).stdout
    assert "which this repository does not have" in out and "See the changes" not in out and "Restore a file" not in out, out
    (root / "out" / "seed3").mkdir(parents=True)
    (root / "out" / "seed3" / "metrics.json").write_text(json.dumps({"int8": {"top1": 75.75}, "fp32": {"top1": 76.0}}), encoding="utf-8")
    plain = rb(root, "run", "add", "int8", "--from", "out/seed3/metrics.json")["data"]["object"]["id"]
    assert "See the changes: git diff " in run_rb(root, "show", "--long", plain).stdout


def test_a_code_row_that_names_a_commit_here_and_one_not_here_names_each_once(tmp_path, monkeypatch):
    """MP-1110-2303's S3: where the runs name one commit here and one this repository lacks, the code row said the
    missing commit twice ("commit 47c165734ae7, abc1234 · not in this repository: abc1234 (...)"). Each is named once,
    those here first, on rb show, the page and the copied recipe's comment. Fails on f73697b."""
    root = make(tmp_path, monkeypatch, ignored=True)
    lacks = "0123456789abcdef0123456789abcdef01234567"
    (root / "out" / "seed2").mkdir(parents=True)
    (root / "out" / "seed2" / "metrics.json").write_text(json.dumps({"int8": {"top1": 75.25}, "fp32": {"top1": 76.0}}), encoding="utf-8")
    run_rb(root, "run", "add", "int8", "--from", "out/seed2/metrics.json", "--commit", lacks)
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True).stdout.strip()
    (root / "out" / "seed3").mkdir(parents=True)
    (root / "out" / "seed3" / "metrics.json").write_text(json.dumps({"int8": {"top1": 75.75}, "fp32": {"top1": 76.0}}), encoding="utf-8")
    run_rb(root, "run", "add", "int8", "--from", "out/seed3/metrics.json", "--commit", head)
    shown = rb(root, "show", "--long", "int8")["data"]
    code = [r for r in (shown.get("rerunnability") or shown["object"]["rerunnability"]) if r["what"] == "code"][0]
    assert code["detail"].count("0123456789ab") == 1 and code["detail"].count(head[:12]) == 1, code
    assert re.search(r"^commit [0-9a-f]{12} · commit 0123456789ab · not in this repository \(get the code repository to rerun\)", code["detail"]), code
    run_rb(root, "page", "--out", str(tmp_path / "p.html"))
    page = re.sub(r"<[^>]+>", "", (tmp_path / "p.html").read_text(encoding="utf-8"))
    assert "· commit 0123456789ab · not in this repository (get the code repository to rerun)" in page
    assert "not in this repository: 0123456789ab" not in page
