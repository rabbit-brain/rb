"""Format 2: a file is written in it only when it holds what format 1 cannot, a count rule (`over: count`), a best of N
(`over: best`), a relative change (`measure: relative`) or a difference of means (`measure: means`), and every other
file stays `rb: 1`, as 0.7 writes it. This rb reads both; an older rb (RB_OLD_BIN, 0.7.0 or 0.7.1) names the format-2
file, says to upgrade, reads every other file, fails its gates closed and writes nothing. Every name and number is
synthetic: an INT8 ResNet-50 on ImageNet, recorded by human:ana."""
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from rabbit_brain.models import Envelope
from rbtest import as_the_user, main, rbyaml

HAS_GIT = shutil.which("git") is not None
pytestmark = pytest.mark.skipif(not HAS_GIT, reason="the research state is played in a git repository")
CRITERIA = Path(".rb") / "experiments" / "e1" / "criteria"
OLD = os.environ.get("RB_OLD_BIN")
needs_old = pytest.mark.skipif(not OLD, reason="RB_OLD_BIN names an older rb (0.7.0, 0.7.1 or 0.8.0)")


def _old_version() -> tuple:
    """RB_OLD_BIN's version, (0, 8, 1) for 0.8.1."""
    said = subprocess.run([OLD, "version"], capture_output=True, text=True).stdout.split()
    return tuple(int(x) for x in (said[-1] if said else "0").split(".")[:3] if x.isdigit())


def _old_reads_format_2() -> bool:
    """Whether RB_OLD_BIN reads format 2: 0.8.0 does, 0.7.x reads format 1 alone."""
    return _old_version()[:2] >= (0, 8)


OLD_READS_2 = bool(OLD) and _old_reads_format_2()
# 0.8.1 names a file raised to rb: 2 by hand, as this rb does; 0.8.0 reads it as fine
OLD_NAMES_RAISED = bool(OLD) and _old_version() >= (0, 8, 1)


@pytest.fixture
def repo(workdir, monkeypatch, tmp_path_factory):
    as_the_user(monkeypatch)
    monkeypatch.setenv("RB_ACTOR", "human:ana")
    cfg = tmp_path_factory.mktemp("gitconfig") / "config"
    cfg.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(cfg))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    subprocess.run(["git", "init", "-q"], check=True)
    subprocess.run(["git", "config", "user.email", "ana@example.com"], check=True)
    subprocess.run(["git", "config", "user.name", "ana"], check=True)
    return workdir


def rbj(capsys, *argv):
    code = main([*argv, "--json"])
    return code, Envelope.model_validate_json(capsys.readouterr().out)


def ok(capsys, *argv):
    code, env = rbj(capsys, *argv)
    assert code in (0, 1) and env.ok, (argv, env.errors)
    return env


def seed(capsys, *rule):
    """INT8 on four seeds, with one mean rule (c0) and, given `rule`, one rule c1 of format 2."""
    ok(capsys, "init", "INT8 ResNet-50", "--no-agents")
    ok(capsys, "metric", "add", "val_loss", "--minimize")
    ok(capsys, "experiment", "add", "INT8 sweep", "--id", "e1", "--purpose", "the sweep", "--baseline", "fp32", "--candidate", "int8")
    ok(capsys, "spec", "set", "e1", "seed", "--per-run")
    ok(capsys, "criterion", "add", "INT8 adds at most 0.05", "-e", "e1", "--metric", "change.val_loss", "--at-most", "0.05", "--id", "c0")
    if rule:
        ok(capsys, "criterion", "add", "the rule", "-e", "e1", *rule, "--id", "c1")
    for s, (f, i) in enumerate([(0.96, 0.98), (0.95, 0.97), (0.97, 0.99), (0.96, 0.99)], 1):
        ok(capsys, "run", "add", "e1", f"fp32.val_loss={f}", f"int8.val_loss={i}", "--set", f"seed={s}", "--produced-at", "2026-05-01")


def formats(root: Path) -> dict:
    """Every record file's format, by its path under .rb/."""
    out = {}
    for p in sorted((root / ".rb").rglob("*.yaml")):
        if "cache" in p.relative_to(root / ".rb").parts:
            continue
        out[p.relative_to(root).as_posix()] = rbyaml.loads(p.read_text(encoding="utf-8")).get("rb")
    return out


FOUR = {
    "a count": ["--metric", "int8.val_loss", "--at-most", "0.99", "--count", "3", "--of", "4"],
    "a best of N": ["--metric", "int8.val_loss", "--at-most", "0.975", "--best", "--of", "4"],
    "a relative change": ["--metric", "change.val_loss", "--at-most", "3", "--relative"],
}


def test_a_record_that_uses_none_of_the_four_is_format_1_throughout(repo, capsys):
    seed(capsys)
    ok(capsys, "export", "--with-record", "--out", "bundle")
    assert set(formats(repo).values()) == {1}, formats(repo)
    assert json.loads(Path("bundle/manifest.json").read_text(encoding="utf-8"))["record_format"] == 1


@pytest.mark.parametrize("which", list(FOUR))
def test_only_the_rule_that_uses_one_is_format_2(repo, capsys, which):
    seed(capsys, *FOUR[which])
    fs = formats(repo)
    assert fs.pop((CRITERIA / "c1.yaml").as_posix()) == 2 and set(fs.values()) == {1}, fs
    # this rb reads it, and every other file, as written
    env = ok(capsys, "status")
    assert not [i for i in env.data["open"] if i["code"] in ("state_newer", "edited_outside_rb")]
    assert ok(capsys, "show", str(CRITERIA / "c1.yaml")).data["valid"] is True
    ok(capsys, "export", "--with-record", "--out", "bundle")
    assert json.loads(Path("bundle/manifest.json").read_text(encoding="utf-8"))["record_format"] == 2
    assert json.loads(Path("bundle/manifest.json").read_text(encoding="utf-8"))["version"] == 2


def test_a_cited_difference_of_means_is_format_2(repo, capsys):
    Path("paper.md").write_text("# Paper\n\nINT8 adds 0.02 to the validation loss.\n", encoding="utf-8")
    seed(capsys, "--metric", "change.val_loss", "--equals", "0.02", "--tolerance", "0.005", "--means", "--source", "paper.md", "--quote", "adds 0.02 to the validation loss")
    assert formats(repo)[(CRITERIA / "c1.yaml").as_posix()] == 2


def test_a_format_1_file_that_holds_a_count_is_no_record_of_either(repo, capsys):
    """Format 1 is fixed: a file that says rb: 1 and holds over: count was not written by rb as it stands."""
    seed(capsys, *FOUR["a count"])
    p = repo / CRITERIA / "c1.yaml"
    p.write_text(p.read_text(encoding="utf-8").replace("rb: 2\n", "rb: 1\n", 1), encoding="utf-8")
    code, env = rbj(capsys, "show", "c1")
    assert code == 2 and env.errors[0].code == "E_STATE_CORRUPT", env.errors
    assert "is format 1 and holds over: count, which only format 2 has" in env.errors[0].message
    code, env = rbj(capsys, "show", str(CRITERIA / "c1.yaml"))
    assert env.data["valid"] is False and any("format 1 holds no over: count" in x for x in env.data["problems"]), env.data["problems"]
    # rb doctor names it with the restore, never an adoption: its format is put back, as rb writes it
    code, env = rbj(capsys, "doctor")
    bad = [x for x in env.data["outcome"]["failures"] if x["check"] == "corrupt" and "c1.yaml" in x["detail"]]
    assert code == 1 and bad and "--restore" in bad[0]["fix"] and "--adopt" not in bad[0]["fix"], env.data


def test_a_format_newer_than_2_asks_for_an_upgrade(repo, capsys):
    seed(capsys, *FOUR["a count"])
    p = repo / CRITERIA / "c1.yaml"
    p.write_text(p.read_text(encoding="utf-8").replace("rb: 2\n", "rb: 3\n", 1), encoding="utf-8")
    code, env = rbj(capsys, "show", "c1")
    assert code == 2 and env.errors[0].code == "E_STATE_NEWER", env.errors
    newer = [i for i in ok(capsys, "status").data["open"] if i["code"] == "state_newer"]
    assert newer and "is format 3; this rb reads formats 1 to 2" in json.dumps(newer), newer
    # rb show of the file itself says so too, never that it is no valid record (MP-1107-0219)
    code, env = rbj(capsys, "show", str(CRITERIA / "c1.yaml"))
    assert code == 2 and env.errors[0].code == "E_STATE_NEWER" and "written by a newer rb" in env.errors[0].message, env.errors
    assert "pip install --upgrade rabbit-brain" in env.errors[0].fix


def test_a_retracted_rule_keeps_its_format(repo, capsys):
    """A retraction keeps the rule as it was declared, so its file stays format 2."""
    seed(capsys, *FOUR["a count"])
    ok(capsys, "retract", "c1", "--why", "declared on the wrong arm")
    assert formats(repo)[(CRITERIA / "c1.yaml").as_posix()] == 2
    assert ok(capsys, "show", "c1").data["object"]["over"] == "count"
    assert ok(capsys, "show", str(CRITERIA / "c1.yaml")).data["valid"] is True


@pytest.mark.parametrize("rel", [CRITERIA / "c0.yaml", Path(".rb/questions/q1.yaml")])
def test_a_format_2_file_that_holds_none_of_the_four_is_no_record_of_either(repo, capsys, rel):
    """rb writes format 2 only where a file holds what format 1 cannot. A file whose first line was raised to rb: 2 by
    hand (its content, and so its hash, unchanged) is named by rb doctor with its fix, stops every gate and every write
    that reads or writes it (any other write is made whole: test_a_write_stops_before_its_files_change_...), and reads
    again once it says rb: 1; a real format-2 file beside it reads in full."""
    seed(capsys, *FOUR["a count"])
    ok(capsys, "question", "add", "Does INT8 cost accuracy?", "--id", "q1")
    p = repo / rel
    p.write_text(p.read_text(encoding="utf-8").replace("rb: 1\n", "rb: 2\n", 1), encoding="utf-8")
    said = "is format 2 and holds nothing only format 2 has"
    code, env = rbj(capsys, "doctor")
    bad = [x for x in env.data["outcome"]["failures"] if x["check"] == "corrupt" and rel.as_posix() in x["detail"]]
    assert code == 1 and bad and said in bad[0]["detail"] and "set its first line back to rb: 1" in bad[0]["detail"], env.data
    assert "--adopt" not in bad[0]["fix"] and "--restore" in bad[0]["fix"], bad
    code, env = rbj(capsys, "status", "--fail-on", "integrity")
    assert code == 2 and env.errors[0].code == "E_STATE_CORRUPT" and said in env.errors[0].message, env.errors
    # a write of the file itself
    before = p.read_bytes()
    write = ("question", "reword", "q1", "Does INT8 cost top-1 accuracy?") if rel.name == "q1.yaml" else ("criterion", "file", "c0", "q1")
    code, env = rbj(capsys, *write, "--why", "the metric")
    assert code == 2 and env.errors[0].code in ("E_STATE_CORRUPT", "E_STATE_EDITED"), env.errors
    assert p.read_bytes() == before
    code, env = rbj(capsys, "show", str(rel))
    assert env.data["valid"] is False and any("this file holds none" in x for x in env.data["problems"]), env.data["problems"]
    p.write_text(p.read_text(encoding="utf-8").replace("rb: 2\n", "rb: 1\n", 1), encoding="utf-8")
    d = ok(capsys, "doctor").data["outcome"]; assert d["passed"], d["failures"]
    assert ok(capsys, "status", "--fail-on", "integrity")
    assert ok(capsys, "show", "c1").data["object"]["over"] == "count"
    assert formats(repo)[(CRITERIA / "c1.yaml").as_posix()] == 2


@pytest.mark.parametrize("broken", ["raised", "yaml"])
def test_a_write_stops_before_its_files_change_while_a_record_file_does_not_read(repo, capsys, broken):
    """A write beside a file that does not read is made whole, or refused leaving every file as it was: never its file
    written and its log line not (the record would then read as edited outside rb)."""
    seed(capsys)
    ok(capsys, "question", "add", "Does INT8 cost accuracy?", "--id", "q1")
    p = repo / CRITERIA / "c0.yaml"
    p.write_text(p.read_text(encoding="utf-8").replace("rb: 1\n", "rb: 2\n", 1) if broken == "raised" else "rb: 1\nkind: [\n", encoding="utf-8")
    q = repo / ".rb/questions/q1.yaml"
    before, log = q.read_bytes(), (repo / ".rb/log.jsonl").read_bytes()
    code, env = rbj(capsys, "question", "reword", "q1", "Does INT8 cost top-1 accuracy?", "--why", "the metric")
    wrote, logged = q.read_bytes() != before, (repo / ".rb/log.jsonl").read_bytes() != log
    assert wrote == logged == (code == 0), (code, env.errors, wrote, logged)


def files_of(root: Path) -> dict:
    """Every file of the record but rb's cache, by path, with its bytes' digest."""
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((root / ".rb").rglob("*"))
            if p.is_file() and "cache" not in p.relative_to(root / ".rb").parts}


@pytest.mark.parametrize("broken", ["raised", "yaml"])
@pytest.mark.parametrize("on", ["e1", "e2"])
def test_a_run_or_an_analysis_beside_a_file_that_does_not_read_is_recorded_and_names_it(repo, capsys, broken, on):
    """rb run add and rb analysis add need no criterion file to write: beside one that does not read, the write is made
    whole and succeeds (exit 0, ok true), and its reply names the file with doctor's fix in place of what reads it. The
    gates still fail closed, and a write that needs the file is refused with every file as it was."""
    import warnings

    import rabbit_brain as rb

    seed(capsys)
    ok(capsys, "experiment", "add", "INT8 at batch 64", "--id", "e2", "--purpose", "the batch", "--baseline", "fp32", "--candidate", "int8")
    ok(capsys, "criterion", "add", "INT8 adds at most 0.05 at batch 64", "-e", "e2", "--metric", "change.val_loss", "--at-most", "0.05", "--id", "c9")
    p = repo / CRITERIA / "c0.yaml"
    p.write_text(p.read_text(encoding="utf-8").replace("rb: 1\n", "rb: 2\n", 1) if broken == "raised" else "rb: 1\nkind: [\n", encoding="utf-8")
    rel = (CRITERIA / "c0.yaml").as_posix()
    seed_ = ["--set", "seed=5"] if on == "e1" else []
    for argv, kind in [(["run", "add", on, "fp32.val_loss=0.96", "int8.val_loss=0.97", *seed_], "run"),
                       (["analysis", "add", on, "--compare", "int8/fp32:val_loss=0.01[0.0,0.02]", "--method", "paired bootstrap over images"], "analysis")]:
        log = (repo / ".rb/log.jsonl").read_text(encoding="utf-8").count("\n")
        code, env = rbj(capsys, *argv)
        assert code == 0 and env.ok, (argv, code, env.errors)
        assert env.data["object"]["kind"] == kind
        assert [u["path"] for u in env.data["unread_files"]] == [rel], env.data.get("unread_files")
        assert "rb doctor --restore" in env.data["unread_files"][0]["fix"]
        assert any(rel in w and "rb doctor" in w for w in env.data["warnings"]), env.data["warnings"]
        assert env.next[0] == "rb doctor", env.next
        assert (repo / ".rb/log.jsonl").read_text(encoding="utf-8").count("\n") == log + 1
        if on == "e2":
            assert "c9" in [f["criterion"] for f in env.data["findings"]], env.data["findings"]
    # the text says it too, after the write's own lines
    assert main(["run", "add", on, "fp32.val_loss=0.95", "int8.val_loss=0.97", *(["--set", "seed=6"] if on == "e1" else [])]) == 0
    said = capsys.readouterr().out
    assert rel in said and "is recorded" in said and "rb doctor --restore" in said, said
    # the SDK's record() returns the run, with the file named as an RBWarning
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        got = rb.open().experiment(on).record({"fp32.val_loss": 0.94, "int8.val_loss": 0.96}, per_run={"seed": 7} if on == "e1" else None)
    assert got is not None and got.id.startswith("run")
    assert any(rel in str(w.message) for w in caught), [str(w.message) for w in caught]
    # what still stops: the gates, and a write that reads the file
    code, env = rbj(capsys, "status", "--fail-on", "integrity")
    assert code == 2 and not env.ok and env.errors[0].code in ("E_STATE_CORRUPT", "E_STATE_EDITED"), (code, env.errors)
    before = files_of(repo)
    code, env = rbj(capsys, "criterion", "add", "another", "-e", "e1", "--metric", "change.val_loss", "--at-most", "0.1")
    assert code == 2 and not env.ok, (code, env.errors)
    assert files_of(repo) == before


def broken_beside(capsys, repo, *before):
    """Two experiments, e1 and e2, each with a rule; the commands `before`; then an analysis on e1 whose file does not
    read."""
    seed(capsys)
    ok(capsys, "experiment", "add", "INT8 at batch 64", "--id", "e2", "--purpose", "the batch", "--baseline", "fp32", "--candidate", "int8")
    ok(capsys, "criterion", "add", "INT8 adds at most 0.05 at batch 64", "-e", "e2", "--metric", "change.val_loss", "--at-most", "0.05", "--id", "c9")
    for argv in before:
        assert main(list(argv)) == 0, argv
        capsys.readouterr()
    aid = ok(capsys, "analysis", "add", "e1", "--compare", "int8/fp32:val_loss=0.02[0.01,0.03]", "--method", "paired bootstrap over images").data["object"]["id"]
    p = next((repo / ".rb" / "experiments" / "e1" / "analyses").glob(f"{aid}.yaml"))
    p.write_text("rb: 1\nkind: [\n", encoding="utf-8")
    return p.relative_to(repo).as_posix()


def test_every_unreadable_file_beside_a_run_is_named_one_that_parses_too(repo, capsys):
    """Codex S1 should 1: with one file that is not YAML and another that parses but is no valid criterion, the reply
    named the first alone. Each file the reply's reads stopped at is named."""
    broken = broken_beside(capsys, repo)
    c0 = repo / CRITERIA / "c0.yaml"
    c0.write_text(c0.read_text(encoding="utf-8").replace("at_most", "at_most_by_hand", 1), encoding="utf-8")
    code, env = rbj(capsys, "run", "add", "e2", "fp32.val_loss=0.96", "int8.val_loss=0.97")
    assert code == 0 and env.ok, env.errors
    named = [u["path"] for u in env.data["unread_files"]]
    assert broken in named and (CRITERIA / "c0.yaml").as_posix() in named, named


def test_every_row_and_reconcile_name_an_unreadable_file_beside_the_runs_they_record(repo, capsys):
    """Codex S1 shoulds 2 and 3: rb run add --every-row and rb reconcile --apply record runs through rb run add, and
    dropped what its reply said of a file that does not read: each says it once, with rb doctor first."""
    observed = ("journal", "run", "--", sys.executable, "-c", "print('{\"metrics\": {\"fp32.val_loss\": 0.95, \"int8.val_loss\": 0.99}}')")
    broken = broken_beside(capsys, repo, ("retract", "c0", "--why", "only e2 reads these numbers"), ("journal", "scan"), observed)
    Path("rows.csv").write_text("group,seed,fp32.val_loss,int8.val_loss\nx,1,0.96,0.97\nx,2,0.95,0.97\n", encoding="utf-8")
    code, env = rbj(capsys, "run", "add", "e2", "--from", "rows.csv", "--where", "group=x", "--every-row")
    assert code == 0 and env.ok and env.data["recorded_count"] == 2, env.errors
    assert [u["path"] for u in env.data["unread_files"]] == [broken] and env.next[0] == "rb doctor", (env.data.get("unread_files"), env.next)
    assert sum(broken in w for w in env.data["warnings"]) == 1, env.data["warnings"]
    code, env = rbj(capsys, "reconcile", "--apply")
    assert code == 0 and env.ok and env.data["recorded"], (env.errors, {k: v for k, v in env.data.items() if k in ("unplaced", "found", "proposed", "runs", "not_recorded")})
    assert [u["path"] for u in env.data["unread_files"]] == [broken] and env.next[0] == "rb doctor", (env.data.get("unread_files"), env.next)


def beside_a_criterion(capsys, repo, broken):
    """e1 with a second rule, c3, on change.top1, which the catalogue does not declare, and e2 with c9 and a per-run
    seed; then c3's file made not YAML ("yaml") or raised to rb: 2 by hand ("raised"). Returns c3's path."""
    seed(capsys)
    ok(capsys, "criterion", "add", "INT8 keeps top-1", "-e", "e1", "--metric", "change.top1", "--at-least", "-0.5", "--id", "c3")
    ok(capsys, "experiment", "add", "INT8 at batch 64", "--id", "e2", "--purpose", "the batch", "--baseline", "fp32", "--candidate", "int8")
    ok(capsys, "spec", "set", "e2", "seed", "--per-run")
    ok(capsys, "criterion", "add", "INT8 adds at most 0.05 at batch 64", "-e", "e2", "--metric", "change.val_loss", "--at-most", "0.05", "--id", "c9")
    p = repo / CRITERIA / "c3.yaml"
    p.write_text(p.read_text(encoding="utf-8").replace("rb: 1\n", "rb: 2\n", 1) if broken == "raised" else "rb: 1\nkind: [\n", encoding="utf-8")
    Path("rows.csv").write_text("group,seed,fp32.val_loss,int8.val_loss,int8.top1\nx,1,0.96,0.97,75.1\nx,2,0.95,0.97,75.0\n", encoding="utf-8")
    return (CRITERIA / "c3.yaml").as_posix()


@pytest.mark.parametrize("broken", ["yaml", "raised"])
def test_a_row_beside_an_unreadable_criterion_file_is_read_where_select_names_its_numbers(repo, capsys, broken):
    """MP-1107-2006 should 1: a row's numbers are its columns that name a declared metric, and a criterion's file that
    does not read may declare one, so without --select the row is refused, naming the file and the columns it cannot
    place, with nothing written. With --select naming metrics the files that read declare, the row is the same run
    whichever the file declares: it is recorded whole, one row or --every-row, and the reply names the file."""
    rel = beside_a_criterion(capsys, repo, broken)
    before = files_of(repo)
    for extra in ([], ["--every-row"]):
        code, env = rbj(capsys, "run", "add", "e2", "--from", "rows.csv", "--where", "group=x" if extra else "group=x,seed=1", "--set", "seed=@seed", *extra)
        assert code == 2 and not env.ok and env.errors[0].code == "E_STATE_CORRUPT", (extra, code, env.errors)
        said = env.errors[0].message
        assert rel in said and ("picks 2 rows" if extra else "int8.top1") in said and said.count("Nothing written.") == 1, said
        if not extra:
            assert "--select fp32.val_loss,int8.val_loss" in env.errors[0].fix, env.errors[0].fix
        assert files_of(repo) == before
    # a column only that file could declare cannot be selected beside it
    code, env = rbj(capsys, "run", "add", "e2", "--from", "rows.csv", "--where", "group=x,seed=1", "--set", "seed=@seed", "--select", "int8.top1")
    assert code == 2 and env.errors[0].code == "E_STATE_CORRUPT" and rel in env.errors[0].message, env.errors
    assert files_of(repo) == before
    log = (repo / ".rb/log.jsonl").read_text(encoding="utf-8").count("\n")
    code, env = rbj(capsys, "run", "add", "e2", "--from", "rows.csv", "--where", "group=x,seed=1", "--set", "seed=@seed", "--select", "fp32.val_loss,int8.val_loss")
    assert code == 0 and env.ok, env.errors
    assert sorted(env.data["object"]["metrics"]) == ["fp32.val_loss", "int8.val_loss"], env.data["object"]["metrics"]
    assert [u["path"] for u in env.data["unread_files"]] == [rel] and env.next[0] == "rb doctor", (env.data.get("unread_files"), env.next)
    assert (repo / ".rb/log.jsonl").read_text(encoding="utf-8").count("\n") == log + 1
    # --every-row picking several rows: that file may declare as a metric every column that tells them apart, which
    # rb does not tell two runs apart by (Codex S2 should 1), so the batch is refused whole, --select or not
    before = files_of(repo)
    code, env = rbj(capsys, "run", "add", "e2", "--from", "rows.csv", "--where", "group=x", "--set", "seed=@seed", "--select", "fp32.val_loss,int8.val_loss", "--every-row")
    assert code == 2 and env.errors[0].code == "E_STATE_CORRUPT" and rel in env.errors[0].message and "picks 2 rows" in env.errors[0].message, env.errors
    assert files_of(repo) == before
    # --every-row picking one row is that row
    code, env = rbj(capsys, "run", "add", "e2", "--from", "rows.csv", "--where", "group=x,seed=2", "--set", "seed=@seed", "--select", "fp32.val_loss,int8.val_loss", "--every-row")
    assert code == 0 and env.ok and env.data["recorded_count"] == 1, env.errors
    assert [u["path"] for u in env.data["unread_files"]] == [rel], env.data.get("unread_files")
    # with no --select, a row whose every column holding a number names a metric the files that read declare
    Path("rows2.csv").write_text("group,fp32.val_loss,int8.val_loss\ny,0.96,0.97\n", encoding="utf-8")
    code, env = rbj(capsys, "run", "add", "e2", "--from", "rows2.csv", "--where", "group=y", "--set", "seed=8")
    assert code == 0 and env.ok and sorted(env.data["object"]["metrics"]) == ["fp32.val_loss", "int8.val_loss"], env.errors


def test_a_database_row_beside_an_unreadable_criterion_file_is_read_where_select_names_its_numbers(repo, capsys):
    """MP-1107-2006 should 1, a SQLite row: as a table's row."""
    import sqlite3

    rel = beside_a_criterion(capsys, repo, "yaml")
    con = sqlite3.connect("results.db")
    con.execute("create table evals (seed integer, fp32_loss real, int8_loss real, top1 real)")
    con.execute("insert into evals values (1, 0.96, 0.97, 75.1)")
    con.commit()
    con.close()
    ok(capsys, "metric", "add", "fp32_loss", "--minimize")
    before = files_of(repo)
    code, env = rbj(capsys, "run", "add", "e2", "--from", "results.db", "--table", "evals", "--where", "seed=1", "--set", "seed=@seed", "--variant", "fp32")
    assert code == 2 and env.errors[0].code == "E_STATE_CORRUPT" and rel in env.errors[0].message and "top1" in env.errors[0].message, env.errors
    assert files_of(repo) == before
    code, env = rbj(capsys, "run", "add", "e2", "--from", "results.db", "--table", "evals", "--where", "seed=1", "--set", "seed=@seed", "--variant", "fp32", "--select", "fp32_loss")
    assert code == 0 and env.ok and [u["path"] for u in env.data["unread_files"]] == [rel], (env.errors, env.data.get("unread_files"))


def test_a_row_beside_an_unreadable_analysis_file_is_recorded_and_names_it(repo, capsys):
    """MP-1107-2006 should 1: an analysis's file declares no metric, so a row beside one that does not read is read
    whole, with no --select, and the reply names the file."""
    broken = broken_beside(capsys, repo)
    ok(capsys, "spec", "set", "e2", "seed", "--per-run")
    Path("rows.csv").write_text("group,seed,fp32.val_loss,int8.val_loss\nx,1,0.96,0.97\n", encoding="utf-8")
    code, env = rbj(capsys, "run", "add", "e2", "--from", "rows.csv", "--where", "group=x", "--set", "seed=@seed")
    assert code == 0 and env.ok, env.errors
    assert [u["path"] for u in env.data["unread_files"]] == [broken], env.data.get("unread_files")


def test_every_row_reads_a_cell_that_starts_with_an_at_as_the_row_s_value(repo, capsys):
    """Codex S2: a table's cell "@b" given as a per-run value with --set seed=@seed passed the batch's first read of
    every row, then failed rb run add's check for an @<column> no row gives, after the rows before it were written.
    A row's cell is that row's value: every row is recorded."""
    seed(capsys)
    Path("rows.csv").write_text("group,seed,fp32.val_loss,int8.val_loss\nx,a1,0.96,0.97\nx,@b,0.95,0.97\n", encoding="utf-8")
    code, env = rbj(capsys, "run", "add", "e1", "--from", "rows.csv", "--where", "group=x", "--set", "seed=@seed", "--every-row")
    assert code == 0 and env.ok and env.data["recorded_count"] == 2, env.errors


@pytest.mark.parametrize("kind", ["question", "resource"])
def test_a_run_or_an_analysis_names_an_unreadable_file_its_reply_never_reads(repo, capsys, kind):
    """MP-1107-2006 later 3: a question's or a resource's file that does not read is in no read a run's reply makes,
    and rb status then stops on it: the reply names it too, from the index the write already read."""
    seed(capsys)
    if kind == "question":
        ok(capsys, "question", "add", "Does INT8 cost accuracy?", "--id", "q1")
        p = repo / ".rb" / "questions" / "q1.yaml"
    else:
        ok(capsys, "resource", "add", "holdout-v2", "--type", "dataset", "--name", "held-out images, v2")
        p = repo / ".rb" / "resources" / "holdout-v2.yaml"
    p.write_text("rb: 1\nkind: [\n", encoding="utf-8")
    rel = p.relative_to(repo).as_posix()
    for argv in (["run", "add", "e1", "fp32.val_loss=0.95", "int8.val_loss=0.97", "--set", "seed=9"],
                 ["analysis", "add", "e1", "--compare", "int8/fp32:val_loss=0.02[0.01,0.03]", "--method", "paired bootstrap over images"]):
        code, env = rbj(capsys, *argv)
        assert code == 0 and env.ok, (argv, env.errors)
        assert rel in [u["path"] for u in env.data.get("unread_files", [])], (argv, env.data.get("unread_files"))
        assert env.next[0] == "rb doctor", env.next
    code, env = rbj(capsys, "status")
    assert code == 2 and env.errors[0].code == "E_STATE_CORRUPT"


@pytest.mark.parametrize("where", ["criterion", "question"])
def test_the_sdk_s_analysis_beside_an_unreadable_file_names_it(repo, capsys, where):
    """MP-1107-2006 should 2: exp.analysis(...) beside a record file that does not read returns the analysis, with the
    file named as an RBWarning, as rb analysis add names it."""
    import warnings

    import rabbit_brain as rb

    seed(capsys)
    ok(capsys, "question", "add", "Does INT8 cost accuracy?", "--id", "q1")
    p = repo / (CRITERIA / "c0.yaml" if where == "criterion" else Path(".rb") / "questions" / "q1.yaml")
    p.write_text("rb: 1\nkind: [\n", encoding="utf-8")
    rel = p.relative_to(repo).as_posix()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        got = rb.open().experiment("e1").analysis([{"subject": "int8", "reference": "fp32", "metric": "val_loss", "estimate": 0.02, "lo": 0.01, "hi": 0.03}],
                                                  method="paired bootstrap over images")
    assert got is not None and got.id.startswith("a")
    assert any(rel in str(w.message) and "rb doctor" in str(w.message) for w in caught), [str(w.message) for w in caught]


@pytest.mark.parametrize("where", ["criterion", "question"])
def test_mcp_analysis_add_beside_an_unreadable_file_names_it(repo, capsys, where):
    """MP-1107-2006 should 2: the analysis_add tool beside a record file that does not read answers ok, with the file in
    data.unread_files and among data.warnings, as rb analysis add does."""
    from rbtest import Mcp

    seed(capsys)
    ok(capsys, "question", "add", "Does INT8 cost accuracy?", "--id", "q1")
    p = repo / (CRITERIA / "c0.yaml" if where == "criterion" else Path(".rb") / "questions" / "q1.yaml")
    p.write_text("rb: 1\nkind: [\n", encoding="utf-8")
    rel = p.relative_to(repo).as_posix()
    server = Mcp(root=repo)
    try:
        server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "clientInfo": {"name": "an-mcp-client", "version": "1.0"}}})
        server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"})
        answer = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "analysis_add", "arguments": {
            "experiment": "e1", "compare": ["int8/fp32:val_loss=0.02[0.01,0.03]"], "method": "paired bootstrap over images"}}})["result"]
    finally:
        server.close()
    env = answer["structuredContent"]
    assert not answer["isError"] and env["ok"], env
    assert [u["path"] for u in env["data"]["unread_files"]] == [rel], env["data"].get("unread_files")
    assert any(rel in w and "rb doctor" in w for w in env["data"]["warnings"]), env["data"]["warnings"]


def test_rb_doctor_restore_puts_a_raised_format_back(repo, capsys):
    seed(capsys)
    p = repo / CRITERIA / "c0.yaml"
    p.write_text(p.read_text(encoding="utf-8").replace("rb: 1\n", "rb: 2\n", 1), encoding="utf-8")
    ok(capsys, "doctor", "--restore")
    assert formats(repo)[(CRITERIA / "c0.yaml").as_posix()] == 1
    assert ok(capsys, "doctor").data["outcome"]["passed"]


def test_a_clone_of_a_bundle_in_a_newer_format_is_refused(repo, capsys, tmp_path):
    seed(capsys)
    ok(capsys, "export", "--with-record", "--out", "bundle")
    man = json.loads(Path("bundle/manifest.json").read_text(encoding="utf-8"))
    man["record_format"] = 3
    Path("bundle/manifest.json").write_text(json.dumps(man), encoding="utf-8")
    code, env = rbj(capsys, "clone", "bundle", str(tmp_path / "copy"))
    assert code == 2 and env.errors[0].code == "E_STATE_NEWER" and "format 3" in env.errors[0].message, env.errors


# ---------------------------------------------------------------- an older rb (0.7.0, 0.7.1)

def older(*argv, ok_codes=(0, 1, 2)):
    r = subprocess.run([OLD, *argv, "--json"], capture_output=True, text=True, env=dict(os.environ))
    out = json.loads(r.stdout)
    assert r.returncode in ok_codes, (argv, r.returncode, out)
    return r.returncode, out


def digest(root: Path) -> dict:
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted((root / ".rb").rglob("*")) if p.is_file() and "cache" not in p.relative_to(root / ".rb").parts and p.name != ".lock"}


@needs_old
@pytest.mark.parametrize("which", list(FOUR))
def test_an_older_rb_names_the_file_says_to_upgrade_and_writes_nothing(repo, capsys, which):
    seed(capsys, *FOUR[which])
    name = (CRITERIA / "c1.yaml").as_posix()
    before = digest(repo)
    if OLD_READS_2:
        # 0.8.0 reads format 2: the record whole, doctor clean, the rule as this rb wrote it, and a write this rb reads
        code, doc = older("doctor")
        assert code == 0, doc["data"]
        code, st = older("status")
        assert not [i for i in st["data"]["open"] if i["code"] in ("state_newer", "edited_outside_rb")], st["data"]["open"]
        code, shown = older("show", "c1")
        assert shown["data"]["object"]["id"] == "c1" and (shown["data"]["object"].get("over") or shown["data"]["object"].get("measure")), shown["data"]
        older("note", "add", "an older rb's note", "-e", "e1")
        code, env = rbj(capsys, "doctor")
        assert code == 0 and env.data["outcome"]["passed"], env.data
        return
    code, doc = older("doctor")
    said = [c for c in doc["data"]["checks"] if c["check"] == "state_newer" and not c["ok"]]
    assert code == 1 and said and name in said[0]["detail"] and "newer rb" in said[0]["detail"] and "upgrade" in said[0]["fix"], doc["data"]
    code, st = older("status")
    newer = [i for i in st["data"]["open"] if i["code"] == "state_newer"]
    assert newer and name in json.dumps(newer) and all(st["data"]["gate"].values()), (newer, st["data"]["gate"])
    # it reads every other file: the mean rule and the runs are there
    assert any(f["criterion"] == "c0" for f in st["data"]["findings"]) and len(st["data"]["runs"]) == 4
    code, _ = older("status", "--fail-on", "unmet")
    assert code == 1
    for argv in (["note", "add", "an older rb's note", "-e", "e1"],
                 ["criterion", "add", "another rule", "-e", "e1", "--metric", "change.val_loss", "--at-most", "0.1"],
                 ["decide", "c1", "accept", "--why", "within budget"],
                 ["decide", "c0", "accept", "--why", "within budget"]):
        code, out = older(*argv)
        assert code == 2 and out["errors"][0]["code"] == "E_STATE_NEWER", (argv, out["errors"])
        assert "upgrade" in out["errors"][0]["fix"].lower()
    older("doctor", "--restore")
    assert digest(repo) == before, "an older rb wrote into a record holding a format-2 file"


@needs_old
def test_an_older_rb_reads_a_record_that_uses_none_of_the_four_as_before(repo, capsys):
    seed(capsys)
    code, doc = older("doctor")
    assert code == 0, doc
    code, st = older("status")
    assert not [i for i in st["data"]["open"] if i["code"] in ("state_newer", "edited_outside_rb")]
    older("note", "add", "an older rb's note", "-e", "e1")
    code, env = rbj(capsys, "doctor")
    assert code == 0 and not env.data.get("problems")


@needs_old
def test_an_older_rb_s_clone_of_a_format_2_bundle_says_to_upgrade(repo, capsys, tmp_path):
    seed(capsys, *FOUR["a count"])
    ok(capsys, "export", "--with-record", "--out", "bundle")
    code, out = older("clone", "bundle", str(tmp_path / "copy"))
    if OLD_READS_2:
        # 0.8.0 reads format 2: the clone lands (in a folder of its own, never inside this project)
        import tempfile
        away = Path(tempfile.mkdtemp(prefix="rb-clone-")) / "copy"
        code, out = older("clone", str(Path("bundle").resolve()), str(away))
        assert code == 0 and out["ok"], out["errors"]
        assert (away / ".rb").is_dir()
        return
    assert code == 2 and out["errors"][0]["code"] == "E_STATE_NEWER" and "upgrade" in out["errors"][0]["fix"].lower(), out["errors"]
    assert not (tmp_path / "copy").exists()


def test_rb_show_of_a_file_reads_its_format_as_it_always_has(repo, capsys):
    """rb show <file>.yaml compares the format by equality, as 0.7 did: `rb: true` is format 1 there."""
    Path("truthy.yaml").write_text("rb: true\nkind: note\nid: n1\ntext: hi\nby: human:ana\nat: '2026-10-01T12:00:00.000+00:00'\n", encoding="utf-8")
    code, env = rbj(capsys, "show", "truthy.yaml")
    assert env.data["valid"] is True, env.data["problems"]


@needs_old
@pytest.mark.skipif(not OLD_READS_2, reason="RB_OLD_BIN reads format 1 alone, where rb: 2 is newer")
def test_a_file_raised_by_hand_is_the_one_file_0_8_0_and_this_rb_read_apart(repo, capsys):
    """A team on two versions (MP-1107-0443): a question raised to rb: 2 by hand reads as fine in 0.8.0, and this rb
    names it, with its fix; 0.8.1 names it too. A file rb writes reads alike in each."""
    seed(capsys)
    ok(capsys, "question", "add", "Does INT8 cost accuracy?", "--id", "q1")
    assert older("doctor")[0] == 0
    p = repo / ".rb" / "questions" / "q1.yaml"
    p.write_text(p.read_text(encoding="utf-8").replace("rb: 1\n", "rb: 2\n", 1), encoding="utf-8")
    assert older("doctor")[0] == (1 if OLD_NAMES_RAISED else 0)
    code, env = rbj(capsys, "doctor")
    assert code == 1 and any(x["check"] == "corrupt" and "q1.yaml" in x["detail"] for x in env.data["outcome"]["failures"]), env.data


@pytest.mark.parametrize("argv", [
    ["criterion", "add", "INT8 adds at most 0.1", "-e", "e1", "--metric", "change.val_loss", "--at-most", "0.1", "--id", "c5"],
    ["criterion", "add", "INT8 adds at most 0.1", "-e", "e1", "-q", "q1", "--metric", "change.val_loss", "--at-most", "0.1", "--id", "c5"],
    ["freeze", "e1", "-m", "before the held-out runs"],
], ids=["criterion", "criterion-q", "freeze"])
def test_a_criterion_or_a_freeze_beside_an_unreadable_analysis_file_is_made_whole(repo, capsys, argv):
    """MP-1108-0010 should 1: rb criterion add (and rb freeze) beside an analysis's file that does not read wrote the
    criterion, then read its finding and exited 2: a half write. The write reads no analysis before it writes, so that
    file cannot change what it writes: it is made whole, exits 0, and its reply names the file."""
    import warnings

    import rabbit_brain as rb

    broken = broken_beside(capsys, repo, ("question", "add", "Does INT8 cost accuracy?", "--id", "q1"))
    log = (repo / ".rb/log.jsonl").read_text(encoding="utf-8").count("\n")
    code, env = rbj(capsys, *argv)
    assert code == 0 and env.ok, (code, env.errors)
    assert [u["path"] for u in env.data["unread_files"]] == [broken] and env.next[0] == "rb doctor", (env.data.get("unread_files"), env.next)
    assert any(broken in w and "rb doctor --restore" in w for w in env.data["warnings"]), env.data["warnings"]
    assert (repo / ".rb/log.jsonl").read_text(encoding="utf-8").count("\n") == log + 1
    if argv[0] == "criterion":
        assert env.data["object"]["id"] == "c5" and "finding" not in env.data
        # the SDK's criterion() returns the criterion, with the file named as an RBWarning
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            got = rb.open().experiment("e1").criterion("INT8 adds at most 0.2", metric="change.val_loss", at_most=0.2)
        assert got is not None
        assert any(broken in str(w.message) for w in caught), [str(w.message) for w in caught]
    code, env = rbj(capsys, "status", "--fail-on", "integrity")
    assert code == 2 and env.errors[0].code == "E_STATE_CORRUPT"


@pytest.mark.parametrize("where", ["experiment", "project"])
def test_a_write_beside_an_unreadable_note_file_is_made_whole_and_names_it(repo, capsys, where):
    """MP-1108-0010 should 2: beside a note's file that does not read, every write that read the experiment (or the
    project) was refused, a run's and an analysis's among them. A note changes no finding and is in no design or
    freeze, and a write rewrites only the files it changes: the write leaves that note out, is made whole and names
    the file. Every read (rb status, rb show) still stops at it, and the note's own file is never written."""
    import warnings

    import rabbit_brain as rb

    seed(capsys)
    ok(capsys, "question", "add", "Does INT8 cost accuracy?", "--id", "q1")
    nid = ok(capsys, "note", "add", "the held-out set is not in the pretraining data", *(["-e", "e1"] if where == "experiment" else [])).data["object"]["id"]
    p = repo / ".rb" / (f"experiments/e1/notes/{nid}.yaml" if where == "experiment" else f"notes/{nid}.yaml")
    p.write_text("rb: 1\nkind: [\n", encoding="utf-8")
    rel, held = p.relative_to(repo).as_posix(), p.read_bytes()
    for argv in (["run", "add", "e1", "fp32.val_loss=0.95", "int8.val_loss=0.97", "--set", "seed=9"],
                 ["analysis", "add", "e1", "--compare", "int8/fp32:val_loss=0.02[0.01,0.03]", "--method", "paired bootstrap over images"],
                 ["criterion", "add", "INT8 adds at most 0.1", "-e", "e1", "--metric", "change.val_loss", "--at-most", "0.1"],
                 ["note", "add", "a second note", "-e", "e1"],
                 ["spec", "set", "e1", "optim.lr", "0.1", "--no-verify"],
                 ["variant", "add", "e1", "int4", "--role", "ablation"],
                 ["decide", "c0", "accept", "--why", "within budget"],
                 ["freeze", "e1", "-m", "before the held-out runs"]):
        log = (repo / ".rb/log.jsonl").read_text(encoding="utf-8").count("\n")
        code, env = rbj(capsys, *argv)
        assert code == 0 and env.ok, (argv, code, env.errors)
        assert rel in [u["path"] for u in env.data.get("unread_files", [])], (argv, env.data.get("unread_files"))
        assert env.next[0] == "rb doctor", (argv, env.next)
        assert (repo / ".rb/log.jsonl").read_text(encoding="utf-8").count("\n") > log, argv
        assert p.read_bytes() == held, argv
    # a write that reads neither the experiment nor the project's notes succeeds, and names no file it never reads
    code, env = rbj(capsys, "question", "add", "Does INT8 cost latency?")
    assert code == 0 and env.ok and p.read_bytes() == held, env.errors
    # the SDK too, with the file named as an RBWarning
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        got = rb.open().experiment("e1").record({"fp32.val_loss": 0.94, "int8.val_loss": 0.96}, per_run={"seed": 10})
    assert got is not None and any(rel in str(w.message) for w in caught), [str(w.message) for w in caught]
    for argv in (["status", "--fail-on", "integrity"], ["show", "e1"]):
        code, env = rbj(capsys, *argv)
        assert code == 2 and env.errors[0].code == "E_STATE_CORRUPT", (argv, env.errors)
    # the note's own file is the one write that stays refused
    code, env = rbj(capsys, "retract", nid, "--why", "wrong")
    assert code == 2 and p.read_bytes() == held, env.errors


def test_a_duplicate_run_or_analysis_names_an_unreadable_file(repo, capsys):
    """MP-1108-0010 later 3: a run (or an analysis) already recorded answered "nothing written" and named no file that
    does not read, while rb status stopped at it: the reply names it too, the SDK's record_from as an RBWarning."""
    import warnings

    import rabbit_brain as rb

    seed(capsys)
    ok(capsys, "question", "add", "Does INT8 cost accuracy?", "--id", "q1")
    Path("m.json").write_text('{"fp32": {"val_loss": 0.93}, "int8": {"val_loss": 0.95}}', encoding="utf-8")
    ok(capsys, "run", "add", "e1", "--from", "m.json", "--set", "seed=11")
    compare = ["analysis", "add", "e1", "--compare", "int8/fp32:val_loss=0.02[0.01,0.03]", "--method", "paired bootstrap over images"]
    ok(capsys, *compare)
    p = repo / ".rb" / "questions" / "q1.yaml"
    p.write_text("rb: 1\nkind: [\n", encoding="utf-8")
    rel = p.relative_to(repo).as_posix()
    for argv in (["run", "add", "e1", "--from", "m.json", "--set", "seed=11"], compare):
        before = files_of(repo)
        code, env = rbj(capsys, *argv)
        assert code == 0 and env.ok and env.data.get("duplicate_of"), (argv, env.errors)
        assert [u["path"] for u in env.data.get("unread_files", [])] == [rel] and env.next[0] == "rb doctor", (argv, env.data.get("unread_files"), env.next)
        assert files_of(repo) == before
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        got = rb.open().experiment("e1").record_from("m.json", per_run={"seed": 11})
    assert got is None and any(rel in str(w.message) for w in caught), [str(w.message) for w in caught]


def test_reconcile_apply_that_records_nothing_names_an_unreadable_file(repo, capsys):
    """MP-1108-0010 later 4: rb reconcile --apply beside a question's file that does not read named it only when it
    recorded a run: an apply that records nothing names it too."""
    seed(capsys)
    ok(capsys, "question", "add", "Does INT8 cost accuracy?", "--id", "q1")
    p = repo / ".rb" / "questions" / "q1.yaml"
    p.write_text("rb: 1\nkind: [\n", encoding="utf-8")
    code, env = rbj(capsys, "reconcile", "--apply")
    assert code == 0 and env.ok and not env.data["recorded"], env.errors
    assert [u["path"] for u in env.data.get("unread_files", [])] == [p.relative_to(repo).as_posix()] and env.next[0] == "rb doctor", (env.data.get("unread_files"), env.next)


def test_a_row_refusal_says_why_it_asks_of_a_column_that_picks_the_row(repo, capsys):
    """MP-1108-0010 later 5: beside an unreadable criterion file, a row's refusal named seed, the column --where picks
    the row by and --set seed=@seed reads, as one that file might declare, with no word why. A metric of that name is
    one of the row's numbers whenever the file reads (Codex S3 should 2), so rb still cannot place it: the refusal names
    the other columns first, and says why it asks of seed."""
    rel = beside_a_criterion(capsys, repo, "yaml")
    before = files_of(repo)
    code, env = rbj(capsys, "run", "add", "e2", "--from", "rows.csv", "--where", "group=x,seed=1", "--set", "seed=@seed")
    assert code == 2 and env.errors[0].code == "E_STATE_CORRUPT" and files_of(repo) == before, env.errors
    said = env.errors[0].message
    assert rel in said and "named int8.top1 or seed (seed picks the row or gives a per-run value, and a metric of that name is one of its numbers too)" in said, said


def test_a_session_s_gate_beside_an_unreadable_criterion_file_is_made_whole(repo, capsys, monkeypatch):
    """Codex S3 blocker (in rb 0.8.1 too): in an agent session, rb experiment gate beside a criterion's file that does
    not read wrote the gate and its log line, then read the experiment's design for its reply and exited 2. The gate
    is made whole: it exits 0 and names the file."""
    seed(capsys)
    p = repo / CRITERIA / "c0.yaml"
    p.write_text("rb: 1\nkind: [\n", encoding="utf-8")
    rel = p.relative_to(repo).as_posix()
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sess-gate-3f10")
    log = (repo / ".rb/log.jsonl").read_text(encoding="utf-8").count("\n")
    code, env = rbj(capsys, "experiment", "gate", "e1", "--if-holds", "INT8 ships")
    assert code == 0 and env.ok, (code, env.errors)
    assert [u["path"] for u in env.data["unread_files"]] == [rel] and env.next[0] == "rb doctor", (env.data.get("unread_files"), env.next)
    lines = (repo / ".rb/log.jsonl").read_text(encoding="utf-8").splitlines()[log:]
    assert [json.loads(x)["op"] for x in lines if json.loads(x)["op"] != "session"] == ["gate"], lines


@pytest.mark.parametrize("where", ["experiment", "project"])
def test_a_note_s_retraction_beside_an_unreadable_criterion_file_is_made_whole(repo, capsys, where):
    """MP-1108-0425 should 1 (in rb 0.8.1 too): rb retract of a note beside a criterion's file that does not read wrote
    the retraction and its log line, then read the criteria to say which findings moved and exited 2. A note is in no
    finding, so the retraction reads no criterion before it writes: it is made whole, exits 0 and names the file."""
    import rabbit_brain as rb

    seed(capsys)
    on = ["-e", "e1"] if where == "experiment" else []
    nid = ok(capsys, "note", "add", "the held-out set is not in the pretraining data", *on).data["object"]["id"]
    nid2 = ok(capsys, "note", "add", "the calibration set matches the eval distribution", *on).data["object"]["id"]
    p = repo / CRITERIA / "c0.yaml"
    p.write_text("rb: 1\nkind: [\n", encoding="utf-8")
    rel = p.relative_to(repo).as_posix()
    log = (repo / ".rb/log.jsonl").read_text(encoding="utf-8").count("\n")
    code, env = rbj(capsys, "retract", nid, "--why", "written on the wrong experiment")
    assert code == 0 and env.ok, (code, env.errors)
    assert env.data["object"]["id"] == nid and env.data["finding_changes"] == []
    assert [u["path"] for u in env.data["unread_files"]] == [rel] and env.next[0] == "rb doctor", (env.data.get("unread_files"), env.next)
    assert any(rel in w and "rb doctor --restore" in w for w in env.data["warnings"]), env.data["warnings"]
    assert (repo / ".rb/log.jsonl").read_text(encoding="utf-8").count("\n") == log + 1
    # the SDK's retract, which reads nothing after it writes, succeeds as before
    rb.open().retract(nid2, why="written on the wrong experiment")
    held = repo / ".rb" / (f"experiments/e1/notes/{nid2}.yaml" if where == "experiment" else f"notes/{nid2}.yaml")
    assert "retracted:" in held.read_text(encoding="utf-8")
    # a retraction that reads the criteria before it writes (a run's) stays refused, with every file as it was
    run = sorted((repo / ".rb/experiments/e1/runs").glob("*.yaml"))[0].stem
    before = files_of(repo)
    code, env = rbj(capsys, "retract", run, "--why", "wrong config")
    assert code == 2 and env.errors[0].code == "E_STATE_CORRUPT" and files_of(repo) == before, env.errors


def test_a_relation_beside_an_unreadable_analysis_file_is_made_whole(repo, capsys):
    """MP-1108-0425 should 1 (in rb 0.8.1 too): rb criterion relate beside an analysis's file that does not read wrote
    the relation, then read the two findings for its reply and exited 2. The relation reads the two criteria, their
    experiments and settings, never an analysis: it is made whole, exits 0 and names the file."""
    broken = broken_beside(capsys, repo)
    log = (repo / ".rb/log.jsonl").read_text(encoding="utf-8").count("\n")
    code, env = rbj(capsys, "criterion", "relate", "c0", "reverses", "c9", "--orientation", "same", "--why", "the same comparison at two batch sizes")
    assert code == 0 and env.ok, (code, env.errors)
    assert env.data["object"]["id"] == "c0" and "relation" not in env.data
    assert [u["path"] for u in env.data["unread_files"]] == [broken] and env.next[0] == "rb doctor", (env.data.get("unread_files"), env.next)
    lines = (repo / ".rb/log.jsonl").read_text(encoding="utf-8").splitlines()[log:]
    assert [json.loads(x)["op"] for x in lines] == ["relate"], lines


@pytest.mark.parametrize("how", ["yaml", "invalid"])
def test_a_relation_s_withdrawal_beside_an_unreadable_analysis_file_names_it(repo, capsys, how):
    """MP-1108-1008 later 1: rb criterion relate --remove beside an analysis's file that does not read wrote the
    withdrawal and exited 0, and its reply named no file, where the declaration's did. It names the file, as a
    declaration does, though the withdrawal reads no finding."""
    broken = broken_beside(capsys, repo)
    if how == "invalid":
        p = repo / broken
        p.write_text("rb: 1\nkind: note\nid: n1\ntext: hi\nby: human:ana\nat: '2026-10-01T12:00:00.000+00:00'\n", encoding="utf-8")
    capsys.readouterr()
    assert main(["criterion", "relate", "c0", "reverses", "c9", "--orientation", "same", "--why", "the same comparison at two batch sizes"]) == 0
    capsys.readouterr()
    log = (repo / ".rb/log.jsonl").read_text(encoding="utf-8").count("\n")
    code, env = rbj(capsys, "criterion", "relate", "c0", "reverses", "c9", "--remove", "--why", "not the same comparison")
    assert code == 0 and env.ok, (code, env.errors)
    assert [u["path"] for u in env.data["unread_files"]] == [broken] and env.next[0] == "rb doctor", (env.data.get("unread_files"), env.next)
    lines = (repo / ".rb/log.jsonl").read_text(encoding="utf-8").splitlines()[log:]
    assert [json.loads(x)["op"] for x in lines] == ["unrelate"], lines


@pytest.mark.parametrize("how", ["yaml", "invalid", "raised"])
@pytest.mark.parametrize("where", ["experiment", "project"])
def test_a_retraction_of_a_note_whose_file_does_not_read_names_the_file(repo, capsys, where, how):
    """MP-1108-0425 should 2: a write leaves out a note whose file does not read, so rb retract of that note said
    "Nothing called '<id>' in this project.", where rb 0.8.1 named the file. It names the file, where it breaks and
    doctor's fix, with nothing written."""
    seed(capsys)
    nid = ok(capsys, "note", "add", "the held-out set is not in the pretraining data", *(["-e", "e1"] if where == "experiment" else [])).data["object"]["id"]
    p = repo / ".rb" / (f"experiments/e1/notes/{nid}.yaml" if where == "experiment" else f"notes/{nid}.yaml")
    text = p.read_text(encoding="utf-8")
    p.write_text({"yaml": "rb: 1\nkind: [\n", "invalid": text.replace("kind: note", "kind: question"), "raised": text.replace("rb: 1\n", "rb: 2\n", 1)}[how], encoding="utf-8")
    rel, before = p.relative_to(repo).as_posix(), files_of(repo)
    code, env = rbj(capsys, "retract", nid, "--why", "wrong")
    assert code == 2 and env.errors[0].code == "E_STATE_CORRUPT", env.errors
    assert env.errors[0].message.startswith(rel) and "Nothing called" not in env.errors[0].message, env.errors[0].message
    assert "rb doctor --restore" in env.errors[0].fix
    assert files_of(repo) == before


def test_a_session_s_write_beside_an_unreadable_note_tells_what_changed(repo, capsys, monkeypatch):
    """MP-1108-0425 later: in an agent session, a write beside a note's file that does not read told nothing since the
    session last looked (rb_delta []), where a clean record tells the open items and how to say the model. It tells
    them, and that the note's file changed outside rb."""
    seed(capsys)
    nid = ok(capsys, "note", "add", "the held-out set is not in the pretraining data", "-e", "e1").data["object"]["id"]
    (repo / f".rb/experiments/e1/notes/{nid}.yaml").write_text("rb: 1\nkind: [\n", encoding="utf-8")
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sess-delta-4e21")
    code, env = rbj(capsys, "spec", "set", "e1", "optim.lr", "0.1", "--no-verify")
    assert code == 0 and env.ok, env.errors
    told = {f["code"]: f for f in env.rb_delta}
    assert "model_unsaid" in told and nid in told["edited_outside_rb"]["text"], env.rb_delta


def test_a_session_s_read_beside_an_unreadable_note_tells_what_changed(repo, capsys, monkeypatch):
    """MP-1108-0809 later 3: in an agent session, a read that never reads notes (rb journal, rb log) beside a
    note's file that does not read told nothing (rb_delta []), where a clean record tells the open items and how to say
    the model. It tells them, and that the note's file changed outside rb, as a write beside it does."""
    seed(capsys)
    nid = ok(capsys, "note", "add", "the held-out set is not in the pretraining data", "-e", "e1").data["object"]["id"]
    (repo / f".rb/experiments/e1/notes/{nid}.yaml").write_text("rb: 1\nkind: [\n", encoding="utf-8")
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sess-delta-read-7b10")
    code, env = rbj(capsys, "journal")
    assert code == 0 and env.ok, env.errors
    told = {f["code"]: f for f in env.rb_delta}
    assert "model_unsaid" in told and nid in told["edited_outside_rb"]["text"], env.rb_delta


def test_a_file_that_holds_another_kind_says_an_analysis(repo, capsys):
    """MP-1108-0425 later (older): a file whose kind is not the one its place holds said "holds a analysis"."""
    broken_beside(capsys, repo)
    p = next((repo / ".rb/experiments/e1/analyses").glob("*.yaml"))
    p.write_text("rb: 1\nkind: note\nid: n1\ntext: hi\nby: human:ana\nat: '2026-10-01T12:00:00.000+00:00'\n", encoding="utf-8")
    code, env = rbj(capsys, "status")
    assert code == 2 and "a file there holds an analysis" in env.errors[0].message, env.errors[0].message


@pytest.mark.parametrize("note", ["reads", "broken"])
def test_reconcile_apply_in_a_session_tells_what_the_runs_it_records_saw(repo, capsys, monkeypatch, note):
    """Codex S4 should: rb reconcile --apply runs rb run add inside itself; the inner command told the session what had
    changed, remembered it as told, and its reply was dropped, so neither the apply nor the next command told it (on
    a clean record too). A command run inside another tells nothing: the enclosing one tells it."""
    seed(capsys)
    ok(capsys, "question", "add", "Does INT8 cost?", "--id", "q1")
    nid = ok(capsys, "note", "add", "the held-out set is not in the pretraining data", "-e", "e1").data["object"]["id"]
    Path("results/r1").mkdir(parents=True)
    Path("results/r1/metrics.json").write_text('{"fp32": {"val_loss": 0.9}, "int8": {"val_loss": 0.95}}', encoding="utf-8")
    Path("results/r1/config.yaml").write_text("seed: 13\n", encoding="utf-8")
    ok(capsys, "run", "add", "e1", "--from", "results/r1/metrics.json", "--set", "seed=13", "--config", "results/r1/config.yaml")
    subprocess.run(["git", "add", "-A"], check=True)
    subprocess.run(["git", "commit", "-qm", "r"], check=True)
    session = {"CLAUDECODE": "1", "CLAUDE_CODE_SESSION_ID": "sess-apply-7c02"}
    with monkeypatch.context() as m:
        for k, v in session.items():
            m.setenv(k, v)
        ok(capsys, "spec", "set", "e1", "warmup", "1", "--no-verify")
    ok(capsys, "question", "reword", "q1", "Does INT8 cost accuracy?", "--why", "the held-out set")
    Path("results/r1/next.json").write_text('{"fp32": {"val_loss": 0.91}, "int8": {"val_loss": 0.96}}', encoding="utf-8")
    if note == "broken":
        (repo / f".rb/experiments/e1/notes/{nid}.yaml").write_text("rb: 1\nkind: [\n", encoding="utf-8")
    for k, v in session.items():
        monkeypatch.setenv(k, v)
    code, env = rbj(capsys, "reconcile", "--apply")
    assert code == 0 and env.ok and env.data["recorded"], (env.errors, {k: v for k, v in env.data.items() if k in ("unplaced", "found", "proposed", "runs", "not_recorded")})
    told = [f["code"] for f in env.rb_delta]
    assert "reword" in told, env.rb_delta
    assert ("edited_outside_rb" in told) == (note == "broken"), env.rb_delta
