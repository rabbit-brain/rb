"""A watched rerun, and what a look reads mid-write.

The INT8 ResNet-50 on ImageNet example, recorded by human:ana: e1 compares variants fp32 (baseline) and int8
(candidate) on top1.

- A rerun under rb journal run that makes, bit for bit, the bytes a commit already kept is the run a reproduction hopes
  for: rb reconcile proposes it. Bytes git itself puts back (rb journal run -- git checkout ...) are still not a run.
- A file of one arm's flat numbers names its arm (--variant) where the run's config beside it, or the watched command's
  line, states that variant's own settings and no other's.
- A file where results land that a look reads cut mid-write is not a changed file nothing in the record cites.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from rabbit_brain.models import Envelope
from rbtest import as_the_user, main

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="played in a git repository")

# a deterministic evaluation: a rerun writes the same bytes
EVAL = '''import json, os, sys
os.makedirs("results/run1", exist_ok=True)
with open("results/run1/metrics.json", "w") as f:
    json.dump({"fp32": {"top1": 76.13}, "int8": {"top1": 75.81}}, f)
'''


def git(*args):
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repo(workdir, monkeypatch, tmp_path_factory, capsys):
    as_the_user(monkeypatch)
    cfg = tmp_path_factory.mktemp("gitconfig") / "config"
    cfg.write_text("[user]\n\tname = ana\n\temail = ana@example.org\n[init]\n\tdefaultBranch = main\n", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(cfg))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("RB_ACTOR", "human:ana")
    git("init", "-q")

    def rb(*argv):
        code = main([*argv, "--json"])
        return code, Envelope.model_validate_json(capsys.readouterr().out)

    return rb


def declare(rb, *more):
    assert rb("init", "INT8 ResNet-50 on ImageNet", "--no-agents")[0] == 0
    assert rb("experiment", "add", "INT8 against FP32: top-1 on the validation set", "--id", "e1",
              "--baseline", "fp32", "--candidate", "int8", "--varies", "precision")[0] == 0
    assert rb("metric", "add", "top1", "--maximize")[0] == 0
    for argv in more:
        assert rb(*argv)[0] == 0, argv
    assert rb("criterion", "add", "INT8 loses at most a point of top-1", "-e", "e1", "--metric", "change.top1",
              "--at-least", "-1", "--id", "c1")[0] == 0


def committed_then_cleared(rb):
    """The parent's run: eval.py and its results committed, the results cleared from the working tree before the
    record began, as a reproduction starts from the code."""
    Path("eval.py").write_text(EVAL, encoding="utf-8")
    subprocess.run([sys.executable, "eval.py"], check=True)
    git("add", "-A")
    git("commit", "-qm", "eval.py and its results")
    Path("results/run1/metrics.json").unlink()
    declare(rb)


def test_a_bit_identical_watched_rerun_is_proposed(repo, capsys):
    committed_then_cleared(repo)
    assert main(["journal", "run", "--", sys.executable, "eval.py"]) == 0
    capsys.readouterr()
    code, env = repo("reconcile")
    assert code == 0
    found = {f["path"]: f for f in env.data["found"]}
    assert "results/run1/metrics.json" in found, env.data
    assert found["results/run1/metrics.json"]["argv"][:5] == ["run", "add", "e1", "--from", "results/run1/metrics.json"]
    assert not any("holds bytes git already kept" in line for line in env.data.get("unplaced", []))


def test_bytes_git_itself_puts_back_are_not_proposed(repo, capsys):
    committed_then_cleared(repo)
    assert main(["journal", "run", "--", "git", "checkout", "HEAD", "--", "results/run1/metrics.json"]) == 0
    capsys.readouterr()
    code, env = repo("reconcile")
    assert code == 0
    assert "results/run1/metrics.json" not in {f["path"] for f in env.data["found"]}, env.data
    assert any("results/run1/metrics.json holds bytes git already kept" in line for line in env.data["unplaced"]), env.data


ONE_ARM = '''import json, os, sys
out = sys.argv[sys.argv.index("--out") + 1]
os.makedirs(out, exist_ok=True)
%s
with open(os.path.join(out, "metrics.json"), "w") as f:
    json.dump({"top1": 75.4}, f)
'''


def one_arm(rb, capsys, script, *argv):
    declare(rb, ("spec", "set", "e1", "fp32.precision", "fp32", "--no-verify"),
            ("spec", "set", "e1", "int8.precision", "int8", "--no-verify"))
    Path("eval.py").write_text(ONE_ARM % script, encoding="utf-8")
    assert main(["journal", "run", "--", sys.executable, "eval.py", "--out", "results/r1", *argv]) == 0
    capsys.readouterr()
    code, env = rb("reconcile")
    assert code == 0
    found = {f["path"]: f for f in env.data["found"]}
    assert "results/r1/metrics.json" in found, env.data
    return found["results/r1/metrics.json"]["argv"]


def recorded_metrics(rb, argv):
    code, env = rb(*argv)
    assert code == 0, env.errors
    return set(env.data["object"]["metrics"])


def test_one_arms_flat_numbers_name_the_variant_the_command_line_gives(repo, capsys):
    argv = one_arm(repo, capsys, "", "--precision", "int8")
    assert argv[:7] == ["run", "add", "e1", "--from", "results/r1/metrics.json", "--variant", "int8"], argv
    assert recorded_metrics(repo, argv) == {"int8.top1"}


def test_one_arms_flat_numbers_name_the_variant_its_config_gives(repo, capsys):
    script = 'open(os.path.join(out, "config.yaml"), "w").write("precision: int8\\n")'
    argv = one_arm(repo, capsys, script)
    assert argv[:7] == ["run", "add", "e1", "--from", "results/r1/metrics.json", "--variant", "int8"], argv
    assert "--config" in argv and argv[argv.index("--config") + 1] == "results/r1/config.yaml"
    assert recorded_metrics(repo, argv) == {"int8.top1"}


def test_flat_numbers_with_no_arm_stated_name_no_variant(repo, capsys):
    argv = one_arm(repo, capsys, "")
    assert "--variant" not in argv, argv


def uncited(rb):
    code, env = rb("status")
    return [u["path"] for u in env.data.get("uncited_changes", [])]


def test_a_file_read_mid_write_is_not_an_uncited_change(repo):
    declare(repo)
    Path("out").mkdir()
    progress = Path("out/progress.json")
    progress.write_text('{"step": 10}', encoding="utf-8")
    assert "out/progress.json" not in uncited(repo)
    progress.write_text("", encoding="utf-8")
    assert "out/progress.json" not in uncited(repo)
    progress.write_text('{"step": 1', encoding="utf-8")
    assert "out/progress.json" not in uncited(repo)
    progress.write_text('{"step": 20}', encoding="utf-8")
    assert "out/progress.json" not in uncited(repo)


def test_bytes_git_puts_back_under_a_shell_are_not_proposed(repo, capsys):
    committed_then_cleared(repo)
    assert main(["journal", "run", "--", "sh", "-c", "git checkout HEAD -- results/run1/metrics.json"]) == 0
    capsys.readouterr()
    code, env = repo("reconcile")
    assert code == 0
    assert "results/run1/metrics.json" not in {f["path"] for f in env.data["found"]}, env.data


TWO_ARMS = '''import json, os, sys
out = sys.argv[sys.argv.index("--out") + 1]
os.makedirs(out, exist_ok=True)
for arm, top1 in (("int8", 75.4), ("fp32", 76.1)):
    with open(os.path.join(out, arm + ".json"), "w") as f:
        json.dump({"top1": top1}, f)
'''


def test_a_line_that_wrote_two_flat_files_names_no_variant(repo, capsys):
    declare(repo, ("spec", "set", "e1", "fp32.precision", "fp32", "--no-verify"),
            ("spec", "set", "e1", "int8.precision", "int8", "--no-verify"))
    Path("eval.py").write_text(TWO_ARMS, encoding="utf-8")
    assert main(["journal", "run", "--", sys.executable, "eval.py", "--out", "results/r1", "--precision", "int8"]) == 0
    capsys.readouterr()
    code, env = repo("reconcile")
    assert code == 0
    for f in env.data["found"]:
        assert "--variant" not in f["argv"], f
    for line in env.data.get("unplaced", []):
        assert "--variant" not in line, line


def test_a_file_that_stays_unread_is_listed_after_a_minute(repo):
    declare(repo)
    Path("out").mkdir()
    lines = Path("out/lines.json")
    lines.write_text('{"step": 1}\n{"step": 2}\n', encoding="utf-8")
    assert "out/lines.json" not in uncited(repo)
    old = lines.stat().st_mtime - 120
    os.utime(lines, (old, old))
    assert "out/lines.json" in uncited(repo)
