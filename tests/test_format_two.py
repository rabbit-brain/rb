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
from pathlib import Path

import pytest

from rabbit_brain.models import Envelope
from rbtest import as_the_user, main, rbyaml

HAS_GIT = shutil.which("git") is not None
pytestmark = pytest.mark.skipif(not HAS_GIT, reason="the research state is played in a git repository")
CRITERIA = Path(".rb") / "experiments" / "e1" / "criteria"
OLD = os.environ.get("RB_OLD_BIN")
needs_old = pytest.mark.skipif(not OLD, reason="RB_OLD_BIN names an older rb (0.7.0, 0.7.1 or 0.8.0)")


def _old_reads_format_2() -> bool:
    """Whether RB_OLD_BIN reads format 2: 0.8.0 does, 0.7.x reads format 1 alone."""
    said = subprocess.run([OLD, "version"], capture_output=True, text=True).stdout.split()
    version = tuple(int(x) for x in (said[-1] if said else "0").split(".")[:2] if x.isdigit())
    return version >= (0, 8)


OLD_READS_2 = bool(OLD) and _old_reads_format_2()


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
    names it, with its fix. A file rb writes reads alike in both."""
    seed(capsys)
    ok(capsys, "question", "add", "Does INT8 cost accuracy?", "--id", "q1")
    assert older("doctor")[0] == 0
    p = repo / ".rb" / "questions" / "q1.yaml"
    p.write_text(p.read_text(encoding="utf-8").replace("rb: 1\n", "rb: 2\n", 1), encoding="utf-8")
    assert older("doctor")[0] == 0
    code, env = rbj(capsys, "doctor")
    assert code == 1 and any(x["check"] == "corrupt" and "q1.yaml" in x["detail"] for x in env.data["outcome"]["failures"]), env.data
