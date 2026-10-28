"""The same repository on a system whose git converts line endings.

Git on Windows, as it is installed by default, checks text files out with \\r\\n where every other system has \\n. rb
names a file by the sha256 of its bytes, so such a checkout would read as a repository where everything changed: every
verified setting moved, every indexed folder changed, every quote gone, every file a run named different. It is the
same text, and rb reads it as the same files: nothing is said to have changed, and what rb writes there does not flip
the record for the next user on another system. Every name and number is synthetic (tests/fixtures/repo_study.py)."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from rabbit_brain.models import Envelope
from rbtest import as_the_user, main, state

sys.path.insert(0, str(Path(__file__).parent / "fixtures"))
import repo_study  # noqa: E402

GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false"]
CONFIG = "optim:\n  lr: 0.0003\n  warmup: 2000\ndata:\n  batch_size: 32\n"
PAPER = "We train for 90 epochs.\nThe warmup arm uses a learning rate of 0.0003 with cosine decay.\n"


@pytest.fixture
def both(tmp_path, monkeypatch, tmp_path_factory, capsys):
    """The study with its record, committed where lines end \\n, and a clone of it made by a git that converts them.
    `rb(...)` runs in whichever of the two is the working directory."""
    as_the_user(monkeypatch)
    cfg = tmp_path_factory.mktemp("gitconfig") / "config"
    cfg.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(cfg))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("RB_ACTOR", "human:t")
    root = repo_study.build(tmp_path / "study")
    monkeypatch.chdir(root)

    def rb(*argv) -> Envelope:
        code = main([*argv, "--json"])
        env = Envelope.model_validate_json(capsys.readouterr().out)
        assert code in (0, 1) and env.ok, (argv, code, env.errors)
        return env

    for rel, text in (("configs/train.yaml", CONFIG), ("paper.txt", PAPER), ("out/s1/metrics.json", json.dumps({"top1": 0.71}, indent=1) + "\n")):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8", newline="\n")
    commit(root, "a config, a paper, a result")
    rb("init", "does the warmup schedule transfer?")
    rb("experiment", "add", "warmup against none", "--id", "warm", "--baseline", "none", "--candidate", "warm", "--varies", "optim.warmup")
    rb("spec", "set", "warm", "--from", "configs/train.yaml", "--keys", "optim.lr,data.*")
    rb("spec", "set", "warm", "lr", "0.0003", "--source", "paper.txt", "--quote", "a learning rate of 0.0003", "--term", "learning rate")
    rb("spec", "set", "warm", "epochs", "90", "--source", "paper.txt:1")
    rb("spec", "set", "warm", "seed", "--per-run")
    rb("run", "add", "warm", "--variant", "warm", "--from", "out/s1/metrics.json", "--config", "configs/train.yaml", "--set", "seed=1")
    rb("reconcile", "--apply")
    commit(root, "the record")
    there = tmp_path / "converted"
    subprocess.run([*GIT, "clone", "-q", "-c", "core.autocrlf=true", str(root), str(there)], check=True)
    for kept_out_of_git in root.rglob("*.npz"):                   # the arrays git ignores are on this disk too
        shutil.copy2(kept_out_of_git, there / kept_out_of_git.relative_to(root))
    for rel in ("configs/train.yaml", "paper.txt", "out/s1/metrics.json", "results/warmup_transfer/README.md", "research/warmup/run_transfer.py"):
        assert b"\r\n" in (there / rel).read_bytes() and b"\r\n" not in (root / rel).read_bytes()    # that git did convert them
    rb.here, rb.there = root, there
    return rb


def commit(root: Path, message: str) -> None:
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    subprocess.run([*GIT, "-C", str(root), "commit", "-q", "-m", message], check=True)


def said(rb) -> dict:
    """What rb says of the repository it is in, without the directory's own name: the state, every finding, every
    setting's check, what the reconcile would change."""
    status = rb("status").data
    status.pop("root")
    status["scope"].pop("looked_at")          # when rb looked, in each checkout
    status["scope"].pop("said")
    return {"status": status, "reconcile": rb("reconcile").data["repository"], "warm": rb("show", "warm").data,
            "transfer": rb("show", "warmup_transfer").data}


def test_a_checkout_with_converted_line_endings_says_what_the_original_says(both, monkeypatch):
    here = said(both)
    assert all(f["changes"] == [] for f in here["reconcile"]["folders"]), here["reconcile"]["folders"]
    monkeypatch.chdir(both.there)
    there = said(both)
    assert there["reconcile"] == here["reconcile"]            # no folder changed, no quote gone, every manifest's sources as frozen
    assert there["warm"] == here["warm"] and there["transfer"] == here["transfer"]
    assert there["status"] == here["status"]


def test_the_record_itself_is_never_converted(both):
    """.rb/.gitattributes tells git to leave the record's files as rb wrote them, on every system."""
    assert (both.here / ".rb" / ".gitattributes").read_text(encoding="utf-8").splitlines()[0] == "* -text"
    assert not [p for p in (both.there / ".rb").rglob("*") if p.is_file() and b"\r\n" in p.read_bytes()]


def files_row(rb) -> dict:
    """What rb says of the files the experiment's runs named: all here unchanged, or what is not."""
    return next(r for r in rb("show", "warm").data["rerunnability"] if r["what"] == "files")


def test_what_a_run_named_and_what_a_setting_was_read_from_are_still_those_files(both, monkeypatch):
    monkeypatch.chdir(both.there)
    assert files_row(both) == {"what": "files", "state": "ok", "detail": "the only file the runs named is here unchanged"}
    checked = both("spec", "verify", "warm").data["results"]
    assert len(checked) == 4 and all(c["ok"] and c["before"] == c["after"] == "verified" for c in checked), checked
    assert all(c["commit"] for c in checked)       # the converted file is the committed one, and is named by its commit


def test_a_real_change_is_still_a_change(both, monkeypatch):
    monkeypatch.chdir(both.there)
    (both.there / "configs" / "train.yaml").write_bytes(CONFIG.replace("0.0003", "0.001").replace("\n", "\r\n").encode())
    (both.there / "out" / "s1" / "metrics.json").write_bytes(b'{\r\n "top1": 0.9\r\n}\r\n')
    by_name = {c["setting"]: c for c in both("spec", "verify", "warm").data["results"]}
    assert not by_name["optim.lr"]["ok"] and by_name["data.batch_size"]["ok"]
    assert by_name["data.batch_size"]["commit"] is None           # the file is no longer the committed one
    assert files_row(both)["state"] != "ok"


def test_a_record_written_on_the_converting_side_reads_the_same_on_the_other(both, monkeypatch):
    """The other direction: a run recorded where the files have \\r\\n names them by those bytes, and where they have
    \\n it is still those files."""
    monkeypatch.chdir(both.there)
    (both.there / "out" / "s2").mkdir(parents=True)
    (both.there / "out" / "s2" / "metrics.json").write_bytes(b'{\r\n "top1": 0.73\r\n}\r\n')
    both("run", "add", "warm", "--variant", "warm", "--from", "out/s2/metrics.json", "--set", "seed=2")
    both("spec", "verify", "warm")                                # the settings' digests are now of the \r\n bytes
    commit(both.there, "seed 2, recorded on the converting side")
    subprocess.run([*GIT, "-C", str(both.here), "pull", "-q", str(both.there), "HEAD"], check=True)
    assert b"\r\n" not in (both.here / "out" / "s2" / "metrics.json").read_bytes()
    monkeypatch.chdir(both.here)
    assert len(state().records("run", live=True)) == 2
    assert files_row(both) == {"what": "files", "state": "ok", "detail": "both files the runs named are here unchanged"}
    checked = both("spec", "verify", "warm").data["results"]
    assert all(c["ok"] and c["before"] == "verified" for c in checked), checked
    status = both("status").data
    assert not [o for o in status["open"] if "changed since" in json.dumps(o)]
