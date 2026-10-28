"""An older rb meeting state a newer rb wrote says "upgrade", and fails closed saying so.

Writes keep extra="forbid"; only reading is forgiving, and only for exactly the shapes a newer writer produces. In
format 1 those shapes are YAML files: a record file whose header says a format above `rb: 1`, a field this rb does
not know, a declared relation of a kind or orientation this rb does not know, and a directory of a kind this rb does not
have, logged like every write.
"""
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
CRITERION = Path(".rb") / "experiments" / "e1" / "criteria" / "c1.yaml"
VARIANT = Path(".rb") / "experiments" / "e1" / "variants" / "base.yaml"


@pytest.fixture
def repo(workdir, monkeypatch, tmp_path_factory):
    as_the_user(monkeypatch)
    cfg = tmp_path_factory.mktemp("gitconfig") / "config"
    cfg.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(cfg))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    subprocess.run(["git", "init", "-q"], check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], check=True)
    subprocess.run(["git", "config", "user.name", "t"], check=True)
    return workdir


def rbj(capsys, *argv):
    code = main([*argv, "--json"])
    return code, Envelope.model_validate_json(capsys.readouterr().out)


def seed(capsys) -> None:
    assert rbj(capsys, "init", "t")[0] == 0
    assert rbj(capsys, "experiment", "add", "x", "--id", "e1", "--baseline", "base")[0] == 0
    assert rbj(capsys, "criterion", "add", "s", "-e", "e1", "--metric", "top1", "--at-least", "0.7", "--noise", "0.001", "--id", "c1")[0] == 0
    assert rbj(capsys, "run", "add", "e1", "top1=0.76")[0] == 0


def edit_file(repo: Path, rel: Path, change) -> None:
    """Change a record file the way a hand, or another rb, would: read the YAML, change it, write it."""
    p = repo / rel
    doc = rbyaml.loads(p.read_text(encoding="utf-8"))
    change(doc)
    p.write_text(rbyaml.dumps(doc), encoding="utf-8")


def edit_criterion(repo: Path, change) -> None:
    edit_file(repo, CRITERION, change)


def test_an_unknown_kind_is_a_newer_rb_not_a_crash(repo, capsys):
    """A newer rb recorded a kind of record this one does not have: a directory of its own under .rb/, and the log line
    every write gets. status must run, name it, and fail every gate closed with the upgrade as the fix; it must never
    KeyError or call it tampering."""
    seed(capsys)
    form = {"id": "dv1", "text": "hold e1 until the rerun", "created_by": "agent:future", "created_at": "2099-01-01T00:00:00+00:00"}
    (repo / ".rb" / "directives").mkdir()
    (repo / ".rb" / "directives" / "dv1.yaml").write_text(rbyaml.dumps({"rb": 2, "kind": "directive", **form}), encoding="utf-8")
    entry = {"at": "2099-01-01T00:00:00+00:00", "actor": "agent:future", "actor_via": "detected:X", "via": "cli",
             "op": "add", "kind": "directive", "id": "dv1", "path": ".rb/directives/dv1.yaml", "sha256": rbyaml.content_sha(form)}
    with (repo / ".rb" / "log.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
    code, env = rbj(capsys, "status")
    assert env.ok, env.errors
    items = env.data["open"]
    newer = [i for i in items if i["code"] == "state_newer"]
    assert newer and "newer rb" in newer[0]["what"] and "upgrade" in newer[0]["do"]
    assert not any(i["code"] == "edited_outside_rb" for i in items)
    for gate in ("stale", "unestablished"):
        assert rbj(capsys, "status", "--fail-on", gate)[0] == 1
    code, env = rbj(capsys, "show", "c1")
    assert any(c["code"] == "state_newer" and c["problem"] for c in env.data["finding"]["integrity"])
    code, env = rbj(capsys, "doctor")
    assert any(c["check"] == "state_newer" for c in env.data["checks"])


def test_an_unknown_via_on_a_record_reads_as_newer_not_corrupt(repo, capsys):
    seed(capsys)
    edit_criterion(repo, lambda c1: c1.update(via="tui"))
    code, env = rbj(capsys, "show", "c1")
    assert code != 0 and env.errors[0].code == "E_STATE_NEWER"
    assert "newer rb" in env.errors[0].message


@pytest.mark.parametrize("where", [CRITERION, VARIANT], ids=["criterion", "variant"])
def test_an_unknown_field_on_a_record_reads_as_newer_not_corrupt(repo, capsys, where):
    """A newer rb may add a field to a record's file, or to a part of one (a variant of the experiment c1 reads)."""
    seed(capsys)
    edit_file(repo, where, lambda doc: doc.update(directive="dv1"))
    code, env = rbj(capsys, "show", "c1")
    assert code != 0 and env.errors[0].code == "E_STATE_NEWER"


NEWER_OR_EDITED = " uses a shape this rb does not know, and it is not what rb last wrote: it was edited by hand, or written by a newer rb."


@pytest.mark.parametrize("where", [CRITERION, VARIANT], ids=["criterion", "variant"])
def test_an_unknown_field_written_by_hand_names_the_edit_and_the_restore(repo, capsys, where):
    """Format 1 gains no field: a newer rb that adds one writes a new format (AGENTS.md). A field this rb does not know,
    in a file that is not what rb last wrote, was most likely written by hand: status, context and show name the file
    and the edit, still E_STATE_NEWER, and offer rb doctor --restore beside the upgrade; rb doctor says it once, and
    its restore puts the file back. A newer rb's own logged write still says only to upgrade (below)."""
    seed(capsys)
    before = (repo / where).read_bytes()
    edit_file(repo, where, lambda doc: doc.update(directive="dv1"))
    for argv in (["status"], ["context"], ["show", "c1"]):
        code, env = rbj(capsys, *argv)
        assert code == 2 and env.errors[0].code == "E_STATE_NEWER", argv
        assert env.errors[0].message == where.as_posix() + NEWER_OR_EDITED, (argv, env.errors[0].message)
        assert env.errors[0].fix.startswith("If it was edited by hand, `rb doctor --restore` puts back what rb last wrote;")
        assert "pip install --upgrade rabbit-brain" in env.errors[0].fix
    code, env = rbj(capsys, "doctor")
    failed = [c for c in env.data["outcome"]["failures"] if c["check"] == "state_newer"]
    assert [c["detail"] for c in failed] == [where.as_posix() + NEWER_OR_EDITED] and "rb doctor --restore" in failed[0]["fix"]
    assert main(["doctor", "--restore"]) == 0
    capsys.readouterr()
    assert (repo / where).read_bytes() == before
    assert rbj(capsys, "status")[0] == 0


def test_an_unknown_field_a_newer_rb_logged_says_only_to_upgrade(repo, capsys):
    seed(capsys)
    logged_as_a_newer_rb(repo, CRITERION, "criterion", "c1", lambda c1: c1.update(directive="dv1"))
    code, env = rbj(capsys, "show", "c1")
    assert code == 2 and env.errors[0].code == "E_STATE_NEWER"
    assert env.errors[0].message.endswith("it was written by a newer rb.") and "by hand" not in env.errors[0].message
    assert "rb doctor --restore" not in env.errors[0].fix and "upgrade" in env.errors[0].fix


def logged_as_a_newer_rb(repo: Path, rel: Path, kind: str, id_: str, change) -> None:
    """A newer rb rewrites a record file and logs the write, as every write is logged."""
    edit_file(repo, rel, change)
    body = {k: v for k, v in rbyaml.loads((repo / rel).read_text(encoding="utf-8")).items() if k not in ("rb", "kind")}
    entry = {"at": "2099-01-01T00:00:00+00:00", "actor": "human:future", "actor_via": "git-email", "via": "cli", "op": "relate",
             "kind": kind, "id": id_, "path": rel.as_posix(), "sha256": rbyaml.content_sha(body)}
    with (repo / ".rb" / "log.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


@pytest.mark.parametrize("change", ["criterion relation kind", "criterion relation orientation", "experiment relation kind"])
def test_a_relation_of_a_kind_or_orientation_this_rb_does_not_know_is_a_newer_part_not_corruption(repo, capsys, change):
    """Relation kinds may grow within format 1. A relation only a newer rb reads is named as state_newer: status still
    reads the rest, every gate fails closed with the upgrade as the fix, and nothing is written over it."""
    assert rbj(capsys, "init", "t")[0] == 0
    for n in (1, 2):
        assert rbj(capsys, "experiment", "add", f"x{n}", "--id", f"e{n}", "--baseline", "base", "--candidate", "new")[0] == 0
        assert rbj(capsys, "criterion", "add", "s", "-e", f"e{n}", "--metric", "change.top1", "--at-least", "0", "--id", f"c{n}")[0] == 0
    assert rbj(capsys, "criterion", "relate", "c1", "reverses", "c2", "--orientation", "same", "--why", "aligned")[0] == 0
    assert rbj(capsys, "experiment", "relate", "e2", "follows_from", "e1")[0] == 0
    if change.startswith("criterion"):
        key, value = ("kind", "replicates") if change.endswith("kind") else ("orientation", "crossed")
        logged_as_a_newer_rb(repo, CRITERION, "criterion", "c1", lambda c1: c1["relations"][0].update({key: value}))
        rel = CRITERION
    else:
        rel = Path(".rb") / "experiments" / "e2" / "experiment.yaml"
        logged_as_a_newer_rb(repo, rel, "experiment", "e2", lambda e2: e2["relations"][0].update(kind="supersedes"))
    before = (repo / rel).read_bytes()
    code, env = rbj(capsys, "status")
    assert env.ok, env.errors
    items = env.data["open"]
    [newer] = [i for i in items if i["code"] == "state_newer"]
    assert "a newer rb" in newer["what"] and "upgrade" in newer["do"] and all(env.data["gate"].values())
    assert not [i for i in items if i["code"] in ("edited_outside_rb", "finding_relation_invalid")]
    code, env = rbj(capsys, "show", "c2")
    assert code == 0 and any(c["code"] == "state_newer" for c in env.data["finding"]["integrity"])
    code, env = rbj(capsys, "doctor")
    assert any(c["check"] == "state_newer" for c in env.data["checks"]) and not any(c["check"] == "corrupt" for c in env.data["checks"])
    code, env = rbj(capsys, "run", "add", "e2", "new.top1=0.7", "base.top1=0.6")
    assert code == 2 and env.errors[0].code == "E_STATE_NEWER"
    assert (repo / rel).read_bytes() == before


@pytest.mark.parametrize("change", ["criterion relation kind", "criterion relation orientation", "experiment relation kind",
                                    "criterion file format", "unknown kind"])
def test_a_bundle_holding_a_part_only_a_newer_rb_reads_is_not_cloned(repo, capsys, tmp_path_factory, change):
    """A clone carries the parent's structure whole. A bundle whose record holds a part only a newer rb reads (a
    relation, a record file, a kind of record) is refused with the upgrade as the fix, and nothing is written, rather
    than cloned without that part."""
    assert rbj(capsys, "init", "t")[0] == 0
    for n in (1, 2):
        assert rbj(capsys, "experiment", "add", f"x{n}", "--id", f"e{n}", "--baseline", "base", "--candidate", "new")[0] == 0
        assert rbj(capsys, "criterion", "add", "s", "-e", f"e{n}", "--metric", "change.top1", "--at-least", "0", "--id", f"c{n}")[0] == 0
    assert rbj(capsys, "criterion", "relate", "c1", "reverses", "c2", "--orientation", "same", "--why", "aligned")[0] == 0
    assert rbj(capsys, "experiment", "relate", "e2", "follows_from", "e1")[0] == 0
    if change == "criterion relation kind":
        logged_as_a_newer_rb(repo, CRITERION, "criterion", "c1", lambda c1: c1["relations"][0].update(kind="replicates"))
    elif change == "criterion relation orientation":
        logged_as_a_newer_rb(repo, CRITERION, "criterion", "c1", lambda c1: c1["relations"][0].update(orientation="crossed"))
    elif change == "experiment relation kind":
        rel = Path(".rb") / "experiments" / "e2" / "experiment.yaml"
        logged_as_a_newer_rb(repo, rel, "experiment", "e2", lambda e2: e2["relations"][0].update(kind="supersedes"))
    elif change == "criterion file format":
        logged_as_a_newer_rb(repo, CRITERION, "criterion", "c1", lambda c1: c1.update(rb=2))
    else:
        form = {"id": "dv1", "text": "hold e1 until the rerun", "created_by": "agent:future", "created_at": "2099-01-01T00:00:00+00:00"}
        (repo / ".rb" / "directives").mkdir()
        (repo / ".rb" / "directives" / "dv1.yaml").write_text(rbyaml.dumps({"rb": 2, "kind": "directive", **form}), encoding="utf-8")
        entry = {"at": "2099-01-01T00:00:00+00:00", "actor": "agent:future", "actor_via": "detected:X", "via": "cli",
                 "op": "add", "kind": "directive", "id": "dv1", "path": ".rb/directives/dv1.yaml", "sha256": rbyaml.content_sha(form)}
        with (repo / ".rb" / "log.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    bundle = tmp_path_factory.mktemp("bundles") / "parent"
    code, env = rbj(capsys, "export", "--with-record", "--out", str(bundle))
    assert code == 0, env.errors
    dest = tmp_path_factory.mktemp("clones") / "mine"
    code, env = rbj(capsys, "clone", str(bundle), str(dest))
    assert code == 2 and env.errors[0].code == "E_STATE_NEWER", env.errors
    assert "a newer rb" in env.errors[0].message and "upgrade" in env.errors[0].fix
    assert not dest.exists()


def test_garbage_is_still_corrupt_not_newer(repo, capsys):
    """The forgiveness is only for a newer writer's shapes. A missing required field or a wrong type
    was never written by any rb, so it stays corruption."""
    seed(capsys)

    def garble(c1):
        del c1["created_by"]
        c1["extra_field"] = 1
    edit_criterion(repo, garble)
    code, env = rbj(capsys, "show", "c1")
    assert code != 0 and env.errors[0].code == "E_STATE_CORRUPT"


def test_a_bumped_format_number_asks_for_an_upgrade(repo, capsys):
    seed(capsys)
    p = repo / ".rb" / "project.yaml"
    doc = rbyaml.loads(p.read_text(encoding="utf-8"))
    doc["rb"] = 2
    p.write_text(rbyaml.dumps(doc), encoding="utf-8")
    for argv in (["status"], ["show", "c1"], ["freeze", "e1", "-m", "lock"]):
        code, env = rbj(capsys, *argv)
        assert code != 0 and env.errors[0].code == "E_STATE_NEWER", argv
        assert "newer rb" in env.errors[0].message


def test_a_record_file_in_a_newer_format_asks_for_an_upgrade_and_never_reads_as_deleted(repo, capsys):
    """Every file says its own format. A newer rb rewrote c1's file in its format and logged the write, as every write
    is logged: c1 is there, unreadable here, so it is newer, never missing or edited, and nothing may be locked on top
    of it."""
    seed(capsys)
    edit_criterion(repo, lambda c1: c1.update(rb=2))
    c1 = {k: v for k, v in rbyaml.loads((repo / CRITERION).read_text(encoding="utf-8")).items() if k not in ("rb", "kind")}
    entry = {"at": "2099-01-01T00:00:00+00:00", "actor": "human:future", "actor_via": "git-email", "via": "cli",
             "op": "add", "kind": "criterion", "id": "c1", "path": CRITERION.as_posix(), "sha256": rbyaml.content_sha(c1)}
    with (repo / ".rb" / "log.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
    code, env = rbj(capsys, "show", "c1")
    assert code != 0 and env.errors[0].code == "E_STATE_NEWER" and "newer rb" in env.errors[0].message
    code, env = rbj(capsys, "status")
    items = env.data["open"]
    newer = [i for i in items if i["code"] == "state_newer"]
    assert newer and "upgrade" in newer[0]["do"] and all(env.data["gate"].values())
    assert not any(i["code"] == "edited_outside_rb" for i in items), items
    code, env = rbj(capsys, "freeze", "e1", "-m", "lock")
    assert code != 0 and env.errors[0].code == "E_STATE_NEWER"


QUESTION_SHAPES = ["an experiment filed under it", "a criterion linked to it", "a missed gate below it", "a call on it settling that gate"]


@pytest.mark.parametrize("shape", QUESTION_SHAPES)
def test_a_question_only_a_newer_rb_reads_leaves_the_rest_of_the_record_read(repo, capsys, shape):
    """A newer rb rewrote q1 in its format. Whatever here rests on q1, status, context and page still run, name it as
    state_newer and fail every gate closed; a call on q1 still settles the gate it settled, and no command rb offers
    names q1, which this rb cannot read."""
    assert rbj(capsys, "init", "t")[0] == 0
    assert rbj(capsys, "question", "add", "does INT8 pay?", "--id", "q1")[0] == 0
    below = shape.startswith(("a missed gate", "a call"))
    assert rbj(capsys, "question", "add", "is it cheap to serve?", "--id", "q2", *(["--parent", "q1"] if below else []))[0] == 0
    filed = "q1" if shape == "an experiment filed under it" else "q2"
    assert rbj(capsys, "experiment", "add", "INT8 on resnet50", "--id", "e1", "-q", filed, "--baseline", "fp32", "--candidate", "int8")[0] == 0
    if shape == "a criterion linked to it":
        assert rbj(capsys, "criterion", "add", "int8 keeps top1", "-e", "e1", "--metric", "top1", "--at-least", "0.7", "--id", "c1", "-q", "q1")[0] == 0
    if below:
        assert rbj(capsys, "criterion", "add", "int8 saves 20%", "-e", "e1", "--metric", "saving_pct", "--at-least", "20", "--role", "gate", "--id", "g20")[0] == 0
        assert rbj(capsys, "run", "add", "e1", "saving_pct=18")[0] == 0
    if shape == "a call on it settling that gate":
        assert rbj(capsys, "criterion", "add", "serving stays cheap", "-e", "e1", "--metric", "cost", "--at-most", "5", "--id", "c2", "-q", "q2")[0] == 0
        assert rbj(capsys, "decide", "q1", "park", "--why", "the saving is below the bar")[0] == 0
    logged_as_a_newer_rb(repo, Path(".rb") / "questions" / "q1.yaml", "question", "q1", lambda q1: q1.update(rb=2))
    code, env = rbj(capsys, "status")
    assert code == 0 and env.ok, env.errors
    items = env.data["open"]
    [newer] = [i for i in items if i["code"] == "state_newer"]
    assert "a newer rb" in newer["what"] and "upgrade" in newer["do"] and all(env.data["gate"].values())
    assert not [i for i in items if i["code"].startswith("question_link_")]
    assert [i["subject"] for i in items if i["code"] == "gate_missed"] == (["g20"] if shape == "a missed gate below it" else [])
    offered = [*env.next, *(i["do"] for i in items)]
    assert not [c for c in offered if " -q q1" in c]
    if shape == "an experiment filed under it":
        assert any(" -e e1 -q <question>" in c for c in offered), offered
    for argv in (["context"], ["page", "--out", str(repo / "page.html")]):
        code, env = rbj(capsys, *argv)
        assert code == 0 and env.ok, (argv, env.errors)


@pytest.mark.parametrize("call", ["a missed gate a question call settles", "a criterion a broad question call was made on"])
def test_a_call_resting_on_a_criterion_only_a_newer_rb_reads_is_still_read_and_shown(repo, capsys, call):
    """A newer rb rewrote a criterion a call on q1 rests on: a missed gate the call settles once links are in use, or,
    in a record with none, a criterion the call was made on. status, rb show q1 and rb show of the call still run; the
    call is not read as made on an earlier reading for it, and rb show of the call says only a newer rb reads it now."""
    assert rbj(capsys, "init", "t")[0] == 0
    assert rbj(capsys, "question", "add", "does INT8 pay?", "--id", "q1")[0] == 0
    assert rbj(capsys, "experiment", "add", "INT8 on resnet50", "--id", "e1", "-q", "q1", "--baseline", "fp32", "--candidate", "int8")[0] == 0
    if call.startswith("a missed gate"):
        assert rbj(capsys, "question", "add", "is it cheap to serve?", "--id", "q2")[0] == 0
        assert rbj(capsys, "criterion", "add", "serving stays cheap", "-e", "e1", "--metric", "cost", "--at-most", "5", "--id", "c2", "-q", "q2")[0] == 0
        assert rbj(capsys, "criterion", "add", "int8 saves 20%", "-e", "e1", "--metric", "saving_pct", "--at-least", "20", "--role", "gate", "--id", "g20")[0] == 0
        assert rbj(capsys, "run", "add", "e1", "saving_pct=18")[0] == 0
        rested_on = "g20"
    else:
        assert rbj(capsys, "criterion", "add", "int8 keeps top1", "-e", "e1", "--metric", "top1", "--at-least", "0.7", "--id", "c1")[0] == 0
        assert rbj(capsys, "run", "add", "e1", "top1=0.76")[0] == 0
        rested_on = "c1"
    code, env = rbj(capsys, "decide", "q1", "park", "--why", "below the bar")
    assert code == 0, env.errors
    decision = env.data["object"]["id"]
    assert rested_on in env.data["object"]["basis"]
    logged_as_a_newer_rb(repo, Path(".rb") / "experiments" / "e1" / "criteria" / f"{rested_on}.yaml", "criterion", rested_on, lambda c: c.update(rb=2))
    code, env = rbj(capsys, "status")
    assert code == 0 and env.ok, env.errors
    assert [i["code"] for i in env.data["open"] if i["code"] in ("state_newer", "decision_outdated", "gate_missed")] == ["state_newer"]
    assert all(env.data["gate"].values())
    code, env = rbj(capsys, "show", "q1")
    assert code == 0 and env.ok, env.errors
    assert env.data["decisions"][-1]["current"] is True
    code, env = rbj(capsys, "show", decision)
    assert code == 0 and env.ok and env.data["current"] is True, env.errors
    assert main(["show", decision]) == 0
    [line] = [x for x in capsys.readouterr().out.splitlines() if x.startswith(f"  {'settles the missed gate' if rested_on == 'g20' else 'made on'} {rested_on}: ")]
    assert line.endswith("  · only a newer rb reads it now")


@pytest.mark.skipif(not os.environ.get("RB_OLD_BIN"), reason="RB_OLD_BIN names an rb from before receipts kept their worktree")
def test_an_older_rb_reads_a_record_written_from_a_nested_worktree(repo, capsys, monkeypatch):
    """A run and an analysis recorded from a worktree nested in the checkout keep the worktree on the receipt
    (attached.worktree, a free mapping) and on the log line (worktree): an older rb reads the record with no problem,
    and its own write leaves it whole for this rb."""
    Path("train.py").write_text("lr = 0.1\n", encoding="utf-8")
    subprocess.run(["git", "add", "train.py"], check=True)
    subprocess.run(["git", "commit", "-qm", "train"], check=True)
    seed(capsys)                                                    # the record is not committed
    assert rbj(capsys, "variant", "add", "e1", "int8", "--role", "candidate")[0] == 0
    wt = Path.cwd().resolve() / ".worktrees" / "b"
    subprocess.run(["git", "worktree", "add", "-q", "-b", "exp-b", str(wt)], check=True)
    (wt / "train.py").write_text("lr = 0.25\n", encoding="utf-8")
    root = Path.cwd()
    monkeypatch.chdir(wt)
    code, env = rbj(capsys, "run", "add", "e1", "top1=0.77")
    assert code == 0 and env.data["object"]["receipt"]["attached"]["worktree"] == {"path": wt.as_posix()}
    code, env = rbj(capsys, "analysis", "add", "e1", "--compare", "int8/base:top1=-0.003[-0.005,-0.001]", "--method", "paired bootstrap", "--unit", "seed",
                    "--units", "3")
    assert code == 0, env.errors
    monkeypatch.chdir(root)
    assert sum('"worktree"' in line for line in (root / ".rb" / "log.jsonl").read_text(encoding="utf-8").splitlines()) == 2

    def older(*argv) -> dict:
        r = subprocess.run([os.environ["RB_OLD_BIN"], *argv, "--json"], capture_output=True, text=True, env=dict(os.environ))
        out = json.loads(r.stdout)
        assert out["ok"] and r.returncode in (0, 1), (argv, r.returncode, out["errors"])
        return out["data"]

    st = older("status")
    assert not [i for i in st["open"] if i["code"] in ("state_newer", "edited_outside_rb")]
    assert not older("doctor").get("problems")
    older("note", "add", "an older rb's note", "-e", "e1")
    code, env = rbj(capsys, "doctor")
    assert code == 0 and not env.data.get("problems")
    code, env = rbj(capsys, "status")
    assert not [i for i in env.data["open"] if i["code"] in ("state_newer", "edited_outside_rb")]


@pytest.mark.skipif(not os.environ.get("RB_OLD_BIN"), reason="RB_OLD_BIN names an rb from before an experiment's design was confirmed")
def test_an_older_rb_reads_a_record_whose_design_the_user_confirmed(repo, capsys, monkeypatch):
    """A confirmation of an experiment's design is one log line of a gate confirmation's kind (op gate, with
    detail.design and the parts as listed) that changes no file: an older rb reads the record with no problem, reads the
    gate it confirmed as the user's, and its own write leaves the design confirmed for this rb."""
    assert rbj(capsys, "init", "t")[0] == 0
    monkeypatch.setenv("CLAUDECODE", "1")
    assert rbj(capsys, "experiment", "add", "x", "--id", "e1", "--baseline", "base", "--candidate", "new", "--if-holds", "ship new")[0] == 0
    assert rbj(capsys, "criterion", "add", "s", "-e", "e1", "--metric", "change.top1", "--at-least", "0", "--id", "c1")[0] == 0
    assert rbj(capsys, "experiment", "add", "y", "--id", "e2", "--purpose", "a sweep")[0] == 0
    monkeypatch.delenv("CLAUDECODE")
    code, env = rbj(capsys, "confirm")
    assert code == 0 and [d["experiment"] for d in env.data["designs_pending"]] == ["e1", "e2"] and len(env.data["gates_pending"]) == 1
    assert rbj(capsys, "confirm", "--all")[0] == 0
    lines = [json.loads(x) for x in (Path(".rb") / "log.jsonl").read_text(encoding="utf-8").splitlines()]
    confirms = [x for x in lines if x["op"] == "gate" and "design" in x.get("detail", {})]
    assert [(x["id"], x["detail"]["confirms"], "sha256" in x, "path" in x) for x in confirms] == [("e1", "gate", False, False), ("e2", "design", False, False)]

    def older(*argv) -> dict:
        r = subprocess.run([os.environ["RB_OLD_BIN"], *argv, "--json"], capture_output=True, text=True, env=dict(os.environ))
        out = json.loads(r.stdout)
        assert out["ok"] and r.returncode in (0, 1), (argv, r.returncode, out["errors"])
        return out["data"]

    st = older("status")
    assert not [i for i in st["open"] if i["code"] in ("state_newer", "edited_outside_rb")]
    assert st.get("gates_pending", []) == [] and not older("doctor").get("problems")
    older("note", "add", "an older rb's note", "-e", "e1")
    code, env = rbj(capsys, "status")
    assert code == 0 and not [i for i in env.data["open"] if i["code"] in ("state_newer", "edited_outside_rb")]
    assert [e["design"]["standing"] for e in env.data["experiments"]] == ["confirmed", "confirmed"] and env.data["designs_pending"] == []


@pytest.mark.skipif(not os.environ.get("RB_OLD_BIN"), reason="RB_OLD_BIN names an rb from before an experiment's design was confirmed")
def test_a_design_an_earlier_build_confirmed_is_one_the_user_owns(repo, capsys, monkeypatch):
    """The snapshot rule (Oct 14): a design confirmation an earlier build wrote kept the design as that build said it,
    under the hash it named it by (one with the snapshot rule in rb's structured form too). This rb reads it as a design
    the user owns: the design reads confirmed, the user's own write keeps it so, and a session's change after it is said
    against it. An rb from before designs were confirmed has none to write, and is skipped."""
    def older(*argv, agent=False) -> dict:
        env = {**os.environ, **({"CLAUDECODE": "1", "CLAUDE_CODE_SESSION_ID": "sess-older-4b1d"} if agent else {})}
        r = subprocess.run([os.environ["RB_OLD_BIN"], *argv, "--json"], capture_output=True, text=True, env=env)
        out = json.loads(r.stdout)
        assert out["ok"] and r.returncode in (0, 1), (argv, r.returncode, out["errors"])
        return out["data"]

    Path("configs").mkdir()
    Path("configs/train.yaml").write_text("optim:\n  lr: 0.001\n", encoding="utf-8")
    older("init", "INT8 ResNet-50 on ImageNet")
    older("experiment", "add", "INT8 vs FP32", "--id", "e1", "--baseline", "fp32", "--candidate", "int8", "--if-holds", "ship INT8", agent=True)
    older("spec", "set", "e1", "optim.lr", "--source", "configs/train.yaml#optim.lr", agent=True)
    older("criterion", "add", "INT8 keeps top-1", "-e", "e1", "--metric", "change.top1", "--at-least", "-0.005", "--id", "c1", agent=True)
    r = subprocess.run([os.environ["RB_OLD_BIN"], "confirm", "--json"], capture_output=True, text=True, env=dict(os.environ))
    waiting = (json.loads(r.stdout).get("data") or {}).get("designs_pending")
    if not waiting:
        pytest.skip("this older rb confirms no design")
    older("confirm", waiting[0]["confirm"].split()[2])
    lines = [json.loads(x) for x in (Path(".rb") / "log.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [x for x in lines if x["op"] == "gate" and "design" in x.get("detail", {})], "the earlier build's own line"
    design = lambda: rbj(capsys, "status")[1].data["experiments"][0]["design"]
    assert design()["standing"] == "confirmed"
    assert rbj(capsys, "spec", "set", "e1", "optim.wd", "0.0001")[0] == 0
    assert design()["standing"] == "confirmed"
    monkeypatch.setenv("CLAUDECODE", "1")
    assert rbj(capsys, "spec", "set", "e1", "optim.wd", "0.0005")[0] == 0
    monkeypatch.delenv("CLAUDECODE")
    d = design()
    assert d["standing"] == "changed" and [(c["part"], c["was"], c["now"]) for c in d["changes"]] == [("setting optim.wd", "0.0001", "0.0005")], d


@pytest.mark.skipif(not os.environ.get("RB_OLD_BIN"), reason="RB_OLD_BIN names an rb from before reconcile kept the paths it saw")
def test_an_older_rb_reads_a_record_whose_reconcile_line_names_the_changed_files(repo, capsys, monkeypatch):
    """rb reconcile --apply keeps the paths of the changed files nothing in the record cites on its own log line
    (detail.uncited_seen), and the journal's state keeps the paths a git move changed and when rb looked: an older rb
    reads the record and shares the journal with no problem, and its own write leaves the record whole for this rb."""
    Path("configs").mkdir()
    Path("configs/eval.yaml").write_text("dataset: imagenet-val\n", encoding="utf-8")
    assert rbj(capsys, "init", "INT8 ResNet-50 on ImageNet")[0] == 0
    assert rbj(capsys, "experiment", "add", "INT8 keeps top-1", "--id", "e1")[0] == 0
    subprocess.run(["git", "add", "-A"], check=True)
    subprocess.run(["git", "commit", "-qm", "the record"], check=True)
    assert rbj(capsys, "status")[0] == 0                                  # rb looks
    Path("configs/eval.yaml").write_text("dataset: imagenet-val\ncrop: resize\n", encoding="utf-8")
    subprocess.run(["git", "commit", "-qam", "resize"], check=True)
    Path("configs/eval.yaml").write_text("dataset: imagenet-val\ncrop: center\n", encoding="utf-8")
    code, env = rbj(capsys, "reconcile", "--apply")
    assert code == 0 and [u["path"] for u in env.data["applied"]["uncited_seen"]["files"]] == ["configs/eval.yaml"]

    def older(*argv) -> dict:
        r = subprocess.run([os.environ["RB_OLD_BIN"], *argv, "--json"], capture_output=True, text=True, env=dict(os.environ))
        out = json.loads(r.stdout)
        assert out["ok"] and r.returncode in (0, 1), (argv, r.returncode, out["errors"])
        return out["data"]

    st = older("status")
    assert not [i for i in st["open"] if i["code"] in ("state_newer", "edited_outside_rb")]
    assert st["repository"]["last_reconcile"]["line"] == "1 changed file recorded by path (configs/eval.yaml)"
    assert not older("doctor").get("problems")
    older("journal", "scan")
    older("note", "add", "an older rb's note", "-e", "e1")
    code, env = rbj(capsys, "doctor")
    assert code == 0 and not env.data.get("problems")
    code, env = rbj(capsys, "status")
    assert code == 0 and not [i for i in env.data["open"] if i["code"] in ("state_newer", "edited_outside_rb")]
    assert env.data["uncited_changes"] == [] and env.data["scope"]["looked_at"]


def _older(*argv) -> dict:
    r = subprocess.run([os.environ["RB_OLD_BIN"], *argv, "--json"], capture_output=True, text=True, env=dict(os.environ))
    out = json.loads(r.stdout)
    assert out["ok"] and r.returncode in (0, 1), (argv, r.returncode, out["errors"])
    return out["data"]


@pytest.mark.skipif(not os.environ.get("RB_OLD_BIN"), reason="RB_OLD_BIN names an rb from before reconcile kept the paths it saw")
def test_an_older_rb_s_apply_records_no_path_and_clears_no_changed_file(repo, capsys):
    """An older rb's rb reconcile --apply keeps no paths on its line: the changed files this rb counts stay counted."""
    Path("configs").mkdir()
    Path("configs/train.yaml").write_text("lr: 0.1\n", encoding="utf-8")
    assert rbj(capsys, "init", "INT8 ResNet-50 on ImageNet")[0] == 0
    subprocess.run(["git", "add", "-A"], check=True)
    subprocess.run(["git", "commit", "-qm", "the record"], check=True)
    assert rbj(capsys, "status")[0] == 0
    Path("configs/train.yaml").write_text("lr: 0.2\n", encoding="utf-8")
    subprocess.run(["git", "commit", "-qam", "lr 0.2"], check=True)
    assert [u["path"] for u in rbj(capsys, "status")[1].data["uncited_changes"]] == ["configs/train.yaml"]
    Path("results/fp32_run").mkdir(parents=True)
    Path("results/fp32_run/README.md").write_text("# FP32 run\n\n## Results\n\ntop-1 0.761\n", encoding="utf-8")
    Path("results/fp32_run/metrics.json").write_text('{"top1": 0.761}', encoding="utf-8")
    _older("reconcile", "--apply")
    lines = [json.loads(x) for x in (Path(".rb") / "log.jsonl").read_text(encoding="utf-8").splitlines()]
    if any("uncited_seen" in (x.get("detail") or {}) for x in lines if x["op"] == "reconciled"):
        pytest.skip("this rb keeps the paths on its line itself")
    assert [u["path"] for u in rbj(capsys, "status")[1].data["uncited_changes"]] == ["configs/train.yaml"]


@pytest.mark.skipif(not os.environ.get("RB_OLD_BIN"), reason="RB_OLD_BIN names an older rb sharing the journal")
def test_an_older_rb_s_look_on_a_dirty_tree_reads_no_move(repo, capsys):
    """The git state every rb compares keeps its shape: on a dirty tree, an older rb's look after this rb's reads no git
    move, and this rb's after it none either."""
    Path("configs").mkdir()
    Path("configs/eval.yaml").write_text("crop: center\n", encoding="utf-8")
    assert rbj(capsys, "init", "INT8 ResNet-50 on ImageNet")[0] == 0
    subprocess.run(["git", "add", "-A"], check=True)
    subprocess.run(["git", "commit", "-qm", "the record"], check=True)
    Path("configs/eval.yaml").write_text("crop: resize\n", encoding="utf-8")
    for _ in range(2):
        assert rbj(capsys, "status")[0] == 0
        assert _older("journal", "scan")["scan"]["git_moved"] is False
        assert rbj(capsys, "journal", "scan")[1].data["scan"]["git_moved"] is False
