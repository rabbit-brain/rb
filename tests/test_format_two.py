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
needs_old = pytest.mark.skipif(not OLD, reason="RB_OLD_BIN names an rb of format 1 (0.7.0 or 0.7.1)")


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


def test_a_format_newer_than_2_asks_for_an_upgrade(repo, capsys):
    seed(capsys, *FOUR["a count"])
    p = repo / CRITERIA / "c1.yaml"
    p.write_text(p.read_text(encoding="utf-8").replace("rb: 2\n", "rb: 3\n", 1), encoding="utf-8")
    code, env = rbj(capsys, "show", "c1")
    assert code == 2 and env.errors[0].code == "E_STATE_NEWER", env.errors
    newer = [i for i in ok(capsys, "status").data["open"] if i["code"] == "state_newer"]
    assert newer and "is format 3; this rb reads formats 1 to 2" in json.dumps(newer), newer


def test_a_retracted_rule_keeps_its_format_and_a_file_that_holds_none_goes_back_to_1(repo, capsys):
    """A retraction keeps the rule as it was declared, so its file stays format 2. A file in format 2 that holds none of
    the four (its header raised by hand: the content, and so its hash, unchanged) reads, and rb's next write of it puts
    it back in format 1, which an older rb reads."""
    seed(capsys, *FOUR["a count"])
    ok(capsys, "retract", "c1", "--why", "declared on the wrong arm")
    assert formats(repo)[(CRITERIA / "c1.yaml").as_posix()] == 2
    assert ok(capsys, "show", "c1").data["object"]["over"] == "count"
    p = repo / CRITERIA / "c0.yaml"
    p.write_text(p.read_text(encoding="utf-8").replace("rb: 1\n", "rb: 2\n", 1), encoding="utf-8")
    env = ok(capsys, "status")
    assert not [i for i in env.data["open"] if i["code"] in ("state_newer", "edited_outside_rb")]
    ok(capsys, "question", "add", "Does INT8 cost accuracy?", "--id", "q1")
    ok(capsys, "criterion", "file", "c0", "q1", "--why", "it answers it")
    assert formats(repo)[(CRITERIA / "c0.yaml").as_posix()] == 1


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
    assert code == 2 and out["errors"][0]["code"] == "E_STATE_NEWER" and "upgrade" in out["errors"][0]["fix"].lower(), out["errors"]
    assert not (tmp_path / "copy").exists()


def test_rb_show_of_a_file_reads_its_format_as_it_always_has(repo, capsys):
    """rb show <file>.yaml compares the format by equality, as 0.7 did: `rb: true` is format 1 there."""
    Path("truthy.yaml").write_text("rb: true\nkind: note\nid: n1\ntext: hi\nby: human:ana\nat: '2026-10-01T12:00:00.000+00:00'\n", encoding="utf-8")
    code, env = rbj(capsys, "show", "truthy.yaml")
    assert env.data["valid"] is True, env.data["problems"]
