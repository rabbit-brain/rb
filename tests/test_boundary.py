"""The boundary between the open core and the hub, as a test.

Anything needed to know where the research stands belongs to the files and the local tool; anything that only helps
people share, find, discuss or host that state may live on rabbitbrain.ai. So with the network cut, no account and no
hub, every command that says what the record means must still answer: what the experiments depend on and whether
each dependency is here, what ran and whether each receipt is what rb wrote, where every criterion stands, what went
stale, and what is unknown. If one of them ever needs the hub, this fails: the hub can disappear tomorrow and the
record stays intelligible, verifiable and operable.
"""
import shutil
import socket
import subprocess
from pathlib import Path

import pytest

from rabbit_brain.models import Envelope
from rbtest import as_the_user, at_home, main, rbyaml

HAS_GIT = shutil.which("git") is not None
pytestmark = pytest.mark.skipif(not HAS_GIT, reason="the record is played in a git repository")


@pytest.fixture
def offline(workdir, monkeypatch, tmp_path_factory, capsys):
    """A project with a question, an experiment frozen by a human, criteria, runs that read resources, and then no
    network, no account and no hub: every socket this process opens fails, and every attempt is kept."""
    as_the_user(monkeypatch, "RB_HUB_TOKEN", "RB_HUB_URL")
    cfg = tmp_path_factory.mktemp("gitconfig") / "config"
    cfg.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(cfg))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    subprocess.run(["git", "init", "-q"], check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], check=True)
    subprocess.run(["git", "config", "user.name", "t"], check=True)

    def rb(*argv):
        code = main([*argv, "--json"])
        return code, Envelope.model_validate_json(capsys.readouterr().out)

    def ok(*argv):
        code, env = rb(*argv)
        assert code == 0 and env.ok, (argv, env.errors)
        return env.data

    monkeypatch.setenv("RB_ACTOR", "human:ana")
    ok("init", "Is INT8 ResNet-50 as good as FP32 on ImageNet?")
    ok("question", "add", "Can we ship INT8 ResNet-50?", "--id", "q1")
    ok("metric", "add", "val_loss", "--unit", "nats", "--minimize")
    ok("experiment", "add", "INT8 vs FP32", "--id", "e1", "-q", "q1", "--hypothesis", "INT8 keeps validation loss within 0.05",
       "--baseline", "fp32", "--candidate", "int8", "--varies", "precision")
    ok("experiment", "add", "FP16 at half the memory", "--id", "e2", "-q", "q1", "--hypothesis", "FP16 matches FP32")
    ok("spec", "set", "e1", "fp32.precision", "fp32")
    ok("spec", "set", "e1", "int8.precision", "int8")
    ok("spec", "set", "e1", "seed", "--per-run")
    ok("criterion", "add", "INT8 adds at most 0.05 validation loss", "-e", "e1", "--metric", "change.val_loss", "--at-most", "0.05", "--id", "c1")
    Path("data").mkdir()
    Path("data/imagenet-val.npz").write_bytes(b"the evaluation images")
    ok("resource", "add", "imagenet", "--type", "dataset", "--name", "ImageNet-1k", "--version", "validation", "--file", "data/imagenet-val.npz",
       "--url", "https://www.image-net.org/")
    ok("resource", "add", "resnet50", "--type", "checkpoint", "--name", "ResNet-50 baseline")  # a name only: unresolved, said so
    ok("freeze", "e1", "-m", "rule fixed before the runs")
    subprocess.run(["git", "add", "-A"], check=True)
    subprocess.run(["git", "commit", "-qm", "the record"], check=True)
    for seed in (1, 2, 3):
        ok("run", "add", "e1", f"fp32.val_loss=5.6{seed}", f"int8.val_loss=5.6{seed + 2}", "--set", f"seed={seed}",
           "--input", "imagenet:eval", "--input", "resnet50:weights", "--command", f"python eval.py --seed {seed}")

    tried: list[str] = []

    def cut(*args, **kwargs):
        tried.append(repr(args[:2]))
        raise OSError("the network is cut for this test")

    for name in ("connect", "connect_ex"):
        monkeypatch.setattr(socket.socket, name, cut)
    monkeypatch.setattr(socket, "create_connection", cut)
    monkeypatch.setattr(socket, "getaddrinfo", cut)
    home = tmp_path_factory.mktemp("no-account")                                # no credentials file anywhere
    at_home(monkeypatch, home)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    return ok, rb, tried


def test_status_answers_where_the_record_stands_with_no_network_and_no_account(offline):
    ok, _, tried = offline
    st = ok("status")
    deps = {r["id"]: r["availability"] for r in st["resources"]}
    assert deps["imagenet"]["state"] == "present" and deps["imagenet"]["identity"]["level"] == "exact"  # declared, resolved, here
    assert deps["resnet50"]["state"] == "lost" and deps["resnet50"]["identity"]["level"] == "name"      # known by name only: said
    assert [r["id"] for r in st["runs"]] and all(r["inputs"] for r in st["runs"])                       # what ran, and what it read
    c1 = next(f for f in st["findings"] if f["criterion"] == "c1")
    assert c1["says"]["headline"] == "criterion met · loss" and c1["n"] == 3                                # where the criterion stands
    assert c1["timing"] == "before" and c1["integrity"] == []
    e2 = next(e for e in st["experiments"] if e["id"] == "e2")
    assert e2["state"] == "planned"                                                                     # a direction nothing has run
    assert not st["gate"]["stale"]
    assert tried == []


def test_every_command_that_says_what_the_record_means_runs_offline(offline, tmp_path_factory):
    ok, rb, tried = offline
    away = tmp_path_factory.mktemp("elsewhere")                         # outside the project: a fork starts its own
    run_id = ok("status")["runs"][0]["id"]
    for argv in (("show", "c1"), ("show", "e1"), ("show", run_id), ("show", "imagenet"), ("show", "q1"), ("show", "e1/seed"),
                 ("compare", "e1"), ("context",), ("log",), ("doctor",), ("env", "check"), ("fmt", "--check"),
                 ("schema", "all"), ("show", ".rb/experiments/e1/criteria/c1.yaml"), ("page", "--out", str(away / "p.html")),
                 ("export", "--out", str(away / "page")), ("export", "--with-record", "--out", str(away / "bundle")), ("reconcile",),
                 ("journal", "scan")):
        ok(*argv)
    ok("clone", str(away / "bundle"), str(away / "fork"))                                          # a fork from a local bundle
    assert tried == []


def test_a_receipt_is_checked_and_a_changed_dependency_is_said_offline(offline):
    """Verifying is local: a run edited by hand is caught by its hash in log.jsonl, and a dataset whose bytes changed
    after the runs read it is `changed`, with the runs' own record of what they read beside it."""
    ok, rb, tried = offline
    Path("data/imagenet-val.npz").write_bytes(b"the evaluation images, regenerated")
    st = ok("status")
    imagenet = next(r for r in st["resources"] if r["id"] == "imagenet")
    assert imagenet["availability"]["state"] == "changed"
    exp = ok("show", "e1")
    row = next(r for r in exp["rerunnability"] if r.get("resource") == "imagenet")
    assert row["state"] == "partial" and "not the bytes the runs read" in row["detail"]
    run = next(p for p in Path(".rb/experiments/e1/runs").glob("*.yaml"))
    doc = rbyaml.loads(run.read_text(encoding="utf-8"))
    doc["metrics"]["int8.val_loss"] -= 0.03                                         # a better number, typed in by hand
    run.write_text(rbyaml.dumps(doc), encoding="utf-8")
    code, env = rb("status", "--fail-on", "unestablished")
    assert code == 1
    assert any(i["code"] == "edited_outside_rb" for i in env.data["open"])
    assert tried == []
