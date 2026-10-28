"""Declared, resolved, observed: the three states rb keeps apart.

A resource declares what it is (identity) and where its bytes have been (locations); a run resolves it at the moment
it is recorded (where the resource stood, and the bytes of the copy here); every read observes it again (availability,
computed). These tests pin the contract: identity is as exact as what was recorded and says so, a run keeps what it
found, a change after the fact is attributable, and a record found twice is caught rather than silently shadowed.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

from rabbit_brain.models import Envelope
from rbtest import as_the_user, main, rbyaml

HAS_GIT = shutil.which("git") is not None
pytestmark = pytest.mark.skipif(not HAS_GIT, reason="the record is played in a git repository")


@pytest.fixture
def rb(workdir, monkeypatch, tmp_path_factory, capsys):
    as_the_user(monkeypatch)
    cfg = tmp_path_factory.mktemp("gitconfig") / "config"
    cfg.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(cfg))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    subprocess.run(["git", "init", "-q"], check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], check=True)
    subprocess.run(["git", "config", "user.name", "t"], check=True)
    monkeypatch.setenv("RB_ACTOR", "human:ana")

    def call(*argv):
        code = main([*argv, "--json"])
        return code, Envelope.model_validate_json(capsys.readouterr().out)

    assert call("init", "t")[0] == 0
    assert call("experiment", "add", "x", "--id", "e1")[0] == 0
    Path("data").mkdir()
    Path("data/val.npz").write_bytes(b"frames")
    return call


def ok(rb, *argv):
    code, env = rb(*argv)
    assert code == 0 and env.ok, (argv, env.errors)
    return env


def test_a_run_keeps_what_it_read_and_a_later_change_is_attributable(rb):
    ok(rb, "resource", "add", "val", "--type", "dataset", "--name", "val split", "--file", "data/val.npz")
    first = ok(rb, "run", "add", "e1", "top1=0.7", "--input", "val:eval")
    use = first.data["object"]["inputs"][0]
    declared = ok(rb, "show", "val").data["object"]["sha256"]
    assert use == {"resource": "val", "role": "eval", "availability": "present", "sha256": declared}
    Path("data/val.npz").write_bytes(b"frames, regenerated")
    shown = ok(rb, "show", "val").data
    assert shown["availability"]["state"] == "changed"                  # a copy is here, not the bytes the resource names
    second = ok(rb, "run", "add", "e1", "top1=0.8", "--input", "val:eval")
    assert any("not the bytes val names" in w for w in second.data.get("warnings", [])), second.data
    use = second.data["object"]["inputs"][0]
    assert use["availability"] == "changed" and use["sha256"] != declared  # the run records what rb found, not what it hoped
    rerun = next(r for r in ok(rb, "show", "e1").data["rerunnability"] if r.get("resource") == "val")
    assert rerun["state"] == "partial"


def test_identity_is_as_exact_as_what_was_recorded(rb):
    ok(rb, "resource", "add", "a", "--type", "dataset", "--name", "hashed", "--file", "data/val.npz")
    ok(rb, "resource", "add", "b", "--type", "checkpoint", "--name", "released", "--version", "v1.2")
    ok(rb, "resource", "add", "c", "--type", "checkpoint", "--name", "named only")
    levels = {r["id"]: r["availability"]["identity"]["level"] for r in ok(rb, "status").data["resources"]}
    assert levels == {"a": "exact", "b": "version", "c": "name"}
    code = main(["status"])
    assert code == 0


def test_a_second_copy_of_a_record_is_caught_set_aside_and_never_adopted(rb, capsys):
    """A criterion copied into another experiment's criteria with a different rule: rb reads the one it wrote, reports
    the copy as written outside rb, restore sets it aside, and no adoption can make it the record."""
    ok(rb, "experiment", "add", "y", "--id", "e2")
    ok(rb, "criterion", "add", "top1 holds", "-e", "e1", "--metric", "top1", "--at-least", "0.7", "--id", "c1")
    ok(rb, "criterion", "add", "other", "-e", "e2", "--metric", "top1", "--at-least", "0.5", "--id", "c2")
    mine = Path(".rb/experiments/e1/criteria/c1.yaml")
    theirs = Path(".rb/experiments/e2/criteria")
    c1 = rbyaml.loads(mine.read_text(encoding="utf-8"))
    (theirs / "c1.yaml").write_text(rbyaml.dumps({**c1, "experiment": "e2", "target": 0.1}), encoding="utf-8")
    assert ok(rb, "show", "c1").data["object"]["target"] == 0.7                  # the record rb wrote is the one read
    code, env = rb("doctor")
    assert code != 0
    code, env = rb("status", "--fail-on", "unestablished")
    assert code == 1 and any(i["code"] == "edited_outside_rb" and "second copy" in i["what"] for i in env.data["open"])
    code, env = rb("doctor", "--adopt", "--why", "keep it")
    assert code != 0 and env.errors[0].code == "E_ADOPT_AUTHORITY"
    ok(rb, "doctor", "--restore")
    assert sorted(p.name for p in theirs.iterdir()) == ["c1.yaml.outside-rb", "c2.yaml"]
    assert ok(rb, "doctor").ok


def test_show_file_gives_the_hashes_log_jsonl_records(rb):
    """Anyone can check a file against the log without rb's store: the hash is the record's content, header left out."""
    ok(rb, "criterion", "add", "top1 holds", "-e", "e1", "--metric", "top1", "--at-least", "0.7", "--id", "c1")
    ok(rb, "question", "add", "does it hold?", "--id", "q1")
    logged = {}
    for line in Path(".rb/log.jsonl").read_text(encoding="utf-8").splitlines():
        row = rbyaml.loads(line)
        if row.get("sha256"):
            logged[(row["kind"], row["id"])] = row["sha256"]
    crit = ok(rb, "show", ".rb/experiments/e1/criteria/c1.yaml").data
    assert crit["content_sha256"] == logged[("criterion", "c1")]
    q = ok(rb, "show", ".rb/questions/q1.yaml").data
    assert q["content_sha256"] == logged[("question", "q1")]


def test_show_file_says_what_a_record_fails_on_line_by_line(rb):
    """A record file that does not validate is still shown, with each problem as rb words them everywhere: where, and
    what is wrong there; the warning names the first three and says how many more."""
    Path("bad.yaml").write_text("rb: 1\nkind: metric\nid: m\nunit: 5\ndirection: sideways\n", encoding="utf-8")
    code, env = rb("show", "bad.yaml")
    assert code == 1 and env.data["valid"] is False
    assert env.data["problems"] == ["created_by: Field required", "created_at: Field required", "unit: Input should be a valid string",
                                    "direction: Input should be 'minimize', 'maximize' or 'none'"]
    assert env.data["warnings"] == ["bad.yaml reads, but is not a valid metric: created_by: Field required; created_at: Field required; "
                                    "unit: Input should be a valid string; and 1 more"]


def test_restore_puts_a_record_back_as_rb_writes_it(rb):
    ok(rb, "question", "add", "does it hold?", "--id", "q1")
    path = Path(".rb/questions/q1.yaml")
    before = path.read_text(encoding="utf-8")
    path.write_text(before.replace("does it hold?", "does it hold, really?"), encoding="utf-8")
    ok(rb, "doctor", "--restore")
    assert path.read_text(encoding="utf-8") == before                           # its own field order, not sorted keys
    assert ok(rb, "fmt", "--check").ok
