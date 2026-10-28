"""What a setting bears on, and what its source states: a variant's own setting bears only on the findings that read that
variant; a count written 999M states 999000000; and a file no commit held before a run made before the record began is
said to postdate that run.

Every name, number and session id here is made up.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from rbtest import AGENT_VARS, command, environment

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="a source file is dated by git")

MAY = "2026-05-07T12:00:00+00:00"
JUNE = "2026-06-01T12:00:00+00:00"


class Rec:
    """A git repository whose user is human:ana, at their own terminal."""

    def __init__(self, root: Path, home: Path) -> None:
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        (home / "gitconfig").write_text("", encoding="utf-8")
        self.env = {k: v for k, v in environment().items() if k not in AGENT_VARS}
        self.env.update(GIT_CONFIG_GLOBAL=str(home / "gitconfig"), GIT_CONFIG_NOSYSTEM="1", RB_TELEMETRY="0", DO_NOT_TRACK="1",
                        HOME=str(home), USERPROFILE=str(home))
        self.git("init", "-q")
        self.git("config", "user.email", "ana@example.org")
        self.git("config", "user.name", "ana")

    def git(self, *args: str, when: str = "") -> str:
        env = {**self.env, **({"GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when} if when else {})}
        return subprocess.run(["git", *args], cwd=self.root, env=env, check=True, capture_output=True, text=True).stdout

    def write(self, path: str, text: str, when: str = "") -> None:
        p = self.root / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        if when:
            self.git("add", path)
            self.git("commit", "-qm", f"write {path}", when=when)

    def rb(self, *argv: str) -> tuple[int, dict]:
        r = subprocess.run([*command(), *argv, "--json"], cwd=self.root, env=self.env, capture_output=True, stdin=subprocess.DEVNULL,
                           encoding="utf-8")
        return r.returncode, json.loads(r.stdout)

    def ok(self, *argv: str) -> dict:
        code, env = self.rb(*argv)
        assert code in (0, 1) and env["ok"], (argv, code, env["errors"])
        return env

    def text(self, *argv: str) -> str:
        r = subprocess.run([*command(), *argv], cwd=self.root, env=self.env, capture_output=True, stdin=subprocess.DEVNULL, encoding="utf-8")
        assert r.returncode in (0, 1), (argv, r.stdout, r.stderr)
        return r.stdout

    def finding(self, cid: str) -> dict:
        return next(f for f in self.ok("status")["data"]["findings"] if f["criterion"] == cid)


@pytest.fixture
def rec(tmp_path, tmp_path_factory) -> Rec:
    r = Rec(tmp_path / "work", tmp_path_factory.mktemp("home"))
    r.write("README.md", "# INT8 ResNet-50\n", when=MAY)
    r.ok("init", "INT8 ResNet-50 on ImageNet", "--no-agents")
    r.ok("metric", "add", "top1", "--maximize")
    r.ok("experiment", "add", "INT8 and FP16 against FP32", "--id", "prec", "--baseline", "fp32", "--candidate", "int8", "--candidate", "fp16")
    r.ok("criterion", "add", "INT8 loses at most 0.01 top-1", "-e", "prec", "--metric", "top1", "--subject", "int8", "--reference", "fp32",
         "--at-least", "-0.01", "--id", "c-int8")
    r.ok("criterion", "add", "FP16 loses at most 0.01 top-1", "-e", "prec", "--metric", "top1", "--subject", "fp16", "--reference", "fp32",
         "--at-least", "-0.01", "--id", "c-fp16")
    return r


def codes(finding: dict, key: str) -> set:
    return {c["code"] for c in finding[key]}


def test_a_variants_unknown_setting_bears_only_on_the_findings_that_read_that_variant(rec):
    """Round 6: one variant's unknown training file put INTEGRITY and a CONFOUND on every criterion of the experiment,
    the ones that never read that variant included."""
    rec.ok("spec", "set", "prec", "fp32.calibration", "imagenet-cal", "--no-verify")
    rec.ok("spec", "set", "prec", "int8.calibration", "imagenet-cal", "--no-verify")
    rec.ok("spec", "set", "prec", "fp16.calibration", "--unknown")
    fp16, int8 = rec.finding("c-fp16"), rec.finding("c-int8")
    assert {"unknown", "confound"} <= codes(fp16, "integrity")
    assert "unknown" not in codes(int8, "integrity") and "confound" not in codes(int8, "integrity")
    assert "fp16.calibration" not in rec.text("show", "c-int8")
    # a shared setting still bears on every finding of the experiment
    rec.ok("spec", "set", "prec", "dataset", "--unknown")
    assert "unknown" in codes(rec.finding("c-int8"), "integrity")


def test_a_count_with_a_suffix_states_the_count_never_its_digits(rec):
    """Round 6: rb read '999M' in a quote as 999, refused 999000000 and stored 999 as verified."""
    rec.write("docs/plan.md", "# Plan\n\nThe corpus is a fixed 999M-token mix, 1.5B images at most, over 30K steps.\n")
    quote = "a fixed 999M-token mix"
    env = rec.ok("spec", "set", "prec", "train_tokens", "999000000", "--source", "docs/plan.md:3", "--quote", quote, "--term", "token")
    assert env["data"]["object"]["status"] == "verified"
    env = rec.ok("spec", "set", "prec", "steps", "30000", "--source", "docs/plan.md:3", "--quote", "over 30K steps", "--term", "steps")
    assert env["data"]["object"]["status"] == "verified"
    rec.ok("spec", "set", "prec", "seq_len", "999", "--source", "docs/plan.md:3", "--quote", quote, "--term", "token")
    shown = rec.text("show", "prec")
    assert "seq_len                  999            provisional" in shown and "CONFLICT" in shown
    assert "the record says 999, but docs/plan.md:3 says 999000000" in rec.text("spec", "set", "prec", "seq_len", "999", "--source",
                                                                             "docs/plan.md:3", "--quote", quote, "--term", "token")


def test_a_file_no_commit_held_before_a_past_run_is_said_to_postdate_it(rec):
    """Round 6: a plan's line for the next runs verified the settings of runs made in May. A setting a file states, which
    no commit held before a run made before the record began, says the file postdates that run; a file committed
    before it, the run's own config and a run made since the record began say nothing of the kind."""
    rec.write("configs/base.yaml", "lr: 0.1\n", when=MAY)
    rec.write("docs/report.md", "# Report\n\nWe trained for 90 epochs.\n", when=JUNE)
    rec.write("sweep/next.sh", "python train.py --batch-size 256\n")                 # a plan, never committed
    rec.ok("spec", "set", "prec", "lr", "--source", "configs/base.yaml#lr")
    rec.ok("spec", "set", "prec", "epochs", "90", "--source", "docs/report.md:3", "--quote", "for 90 epochs", "--term", "epochs")
    rec.ok("spec", "set", "prec", "fp16.batch_size", "256", "--source", "sweep/next.sh:1", "--quote", "--batch-size 256", "--term", "batch-size")
    rec.write("runs/may/config.yaml", "warmup: 5\n")                              # the run's own config, never committed
    rec.ok("spec", "set", "prec", "warmup", "--source", "runs/may/config.yaml#warmup")
    notes = lambda cid: [c["text"] for c in rec.finding(cid)["caveats"] if c["code"] == "source_postdates_run"]
    assert notes("c-int8") == [] and notes("c-fp16") == []                       # no run yet
    may = rec.ok("run", "add", "prec", "fp32.top1=0.761", "int8.top1=0.755", "fp16.top1=0.760", "--produced-at", "2026-05-20T10:00:00Z",
                 "--config", "runs/may/config.yaml")["data"]["object"]["id"]
    said_report = f"epochs is provisional: docs/report.md has no commit from before run {may} was produced (2026-05-20): the file postdates the run"
    said_plan = f"fp16.batch_size is provisional: sweep/next.sh has no commit from before run {may} was produced (2026-05-20): the file postdates the run"
    assert notes("c-int8") == [said_report]                                      # the plan sets fp16 only
    assert notes("c-fp16") == [said_report, said_plan]
    assert f"  · {said_plan}" in rec.text("show", "prec").splitlines()
    # a run made since the record began is not a past run
    rec.ok("run", "add", "prec", "fp32.top1=0.762", "int8.top1=0.756", "fp16.top1=0.761")
    rec.ok("retract", may, "--why", "made with the old eval script")
    assert notes("c-fp16") == []


def test_a_file_postdates_only_the_runs_a_finding_reads_and_a_write_says_so_at_once(rec):
    """Review of round 6's build: the postdates note named a run the finding never read (another arm's), and rb spec
    set said only 'verified' for a file that postdates a past run already recorded. A finding names only the runs it
    reads; the write that verifies the setting says it at once, in its text and in data.warnings."""
    rec.ok("criterion", "add", "FP16 reaches 0.75 top-1", "-e", "prec", "--metric", "top1", "--subject", "fp16", "--at-least", "0.75",
           "--id", "c-fp16-level")
    may = rec.ok("run", "add", "prec", "fp32.top1=0.761", "int8.top1=0.755", "--produced-at", "2026-05-20T10:00:00Z")["data"]["object"]["id"]
    rec.ok("run", "add", "prec", "fp32.top1=0.762", "fp16.top1=0.760", "--produced-at", "2026-07-02T10:00:00Z")
    rec.write("docs/report.md", "# Report\n\nWe trained for 90 epochs.\n", when=JUNE)
    said = f"epochs is provisional: docs/report.md has no commit from before run {may} was produced (2026-05-20): the file postdates the run"
    env = rec.ok("spec", "set", "prec", "epochs", "90", "--source", "docs/report.md:3", "--quote", "for 90 epochs", "--term", "epochs")
    assert said in env["data"]["warnings"]
    notes = lambda cid: [c["text"] for c in rec.finding(cid)["caveats"] if c["code"] == "source_postdates_run"]
    assert notes("c-int8") == [said]
    assert notes("c-fp16-level") == []                    # it reads the July run alone, made after the June commit
    assert f"  {said}" in rec.text("spec", "set", "prec", "epochs", "90", "--source", "docs/report.md:3", "--quote", "for 90 epochs",
                                   "--term", "epochs").splitlines()


def test_a_setting_verified_at_a_commit_made_after_a_past_run_is_said_to_postdate_it(rec):
    """Round 7: a cold session verified max_steps from a script at a commit made three days after a run it covers; the
    file had an earlier commit, so nothing was said. A setting verified at a commit made after a past run it covers says
    so, at the write and on the finding; the same line at a commit from before the run says nothing of the kind."""
    rec.write("sweep/train.py", "EPOCHS = 90\nLR = 0.1\n", when=MAY)
    may_commit = rec.git("rev-parse", "HEAD").strip()
    rec.write("sweep/train.py", "EPOCHS = 90\nLR = 0.2\n", when=JUNE)
    june_commit = rec.git("rev-parse", "HEAD").strip()
    run = rec.ok("run", "add", "prec", "fp32.top1=0.761", "int8.top1=0.755", "--produced-at", "2026-05-20T10:00:00Z")["data"]["object"]["id"]
    said = (f"epochs is provisional: sweep/train.py at {june_commit[:7]} was committed 2026-06-01, after run {run} was produced (2026-05-20): "
            "the file postdates the run")
    env = rec.ok("spec", "set", "prec", "epochs", "90", "--source", "sweep/train.py:1", "--commit", june_commit[:7], "--term", "EPOCHS")
    assert env["data"]["object"]["status"] == "verified" and said in env["data"]["warnings"]
    notes = lambda cid: [c["text"] for c in rec.finding(cid)["caveats"] if c["code"] == "source_postdates_run"]
    assert notes("c-int8") == [said]
    env = rec.ok("spec", "set", "prec", "epochs", "90", "--source", "sweep/train.py:1", "--commit", may_commit[:7], "--term", "EPOCHS")
    assert not [w for w in env["data"].get("warnings", []) if "postdates" in w]
    assert notes("c-int8") == []


def test_a_setting_only_a_postdating_line_states_reads_provisional_and_one_at_an_older_commit_verifies(rec, monkeypatch):
    """Round 8: a session set a setting from a paper line committed after the runs it covers, and from a script at the
    tip; rb took both as verified, with a note that the line postdates the run. A setting only such a line states reads
    provisional, with that note, at the write and on every listing (status, show, context and their counts); the same
    line at a commit from before the run verifies as before."""
    rec.write("sweep/train.py", "EPOCHS = 90\nLR = 0.1\n", when=MAY)
    may_commit = rec.git("rev-parse", "HEAD").strip()[:7]
    rec.write("docs/report.md", "# Report\n\nWe trained for 90 epochs.\n", when=JUNE)
    run = rec.ok("run", "add", "prec", "fp32.top1=0.761", "int8.top1=0.755", "--produced-at", "2026-05-20T10:00:00Z")["data"]["object"]["id"]
    said = f"epochs is provisional: docs/report.md has no commit from before run {run} was produced (2026-05-20): the file postdates the run"
    out = rec.text("spec", "set", "prec", "epochs", "90", "--source", "docs/report.md:3", "--quote", "for 90 epochs", "--term", "epochs")
    assert out.startswith('prec/epochs = 90 · provisional · source docs/report.md:3 "for 90 epochs"\n') and f"  {said}\n" in out, out
    count = lambda: next(e for e in rec.ok("status")["data"]["experiments"] if e["id"] == "prec")["settings_count"]
    assert count()["verified"] == 0 and count()["provisional"] == 1
    assert "settings 0 verified · 1 provisional · 0 unknown" in rec.text("status")
    shown = rec.text("show", "prec")
    assert "    epochs                   90             provisional · docs/report.md:3" in shown and f"  · {said}" in shown, shown
    assert "| epochs | 90 | provisional | (given: docs/report.md:3 \"for 90 epochs\") |" in rec.text("context"), rec.text("context")
    # the line at a commit from before the run verifies, as before
    out = rec.text("spec", "set", "prec", "epochs", "90", "--source", "sweep/train.py:1", "--commit", may_commit, "--term", "EPOCHS")
    assert out.startswith("prec/epochs = 90 · verified in sweep/train.py:1") and "postdates" not in out, out
    assert count()["verified"] == 1 and count()["provisional"] == 0
    assert "    epochs                   90             verified · sweep/train.py:1" in rec.text("show", "prec")
    # the SDK says the postdating line as an RBWarning, as MCP and the command line say it in data.warnings
    import warnings
    import rabbit_brain
    from rbtest import Mcp
    monkeypatch.chdir(rec.root)
    for k, v in rec.env.items():
        monkeypatch.setenv(k, v)
    for k in AGENT_VARS:
        monkeypatch.delenv(k, raising=False)
    with warnings.catch_warnings(record=True) as told:
        warnings.simplefilter("always")
        rabbit_brain.open(rec.root).experiment("prec").spec.set("epochs", 90, source="docs/report.md:3", quote="for 90 epochs", term="epochs")
    assert said in [str(w.message) for w in told if issubclass(w.category, rabbit_brain.RBWarning)]
    assert count()["provisional"] == 1
    server = Mcp(root=rec.root, env={**rec.env, "RB_SESSION": "sess-reads-mcp"})
    try:
        server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "clientInfo": {"name": "reads"}}})
        answer = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "spec_set", "arguments": {
            "experiment": "prec", "name": "epochs", "value": "90", "source": "docs/report.md:3", "quote": "for 90 epochs", "term": "epochs"}}})["result"]
    finally:
        server.close()
    assert said in answer["structuredContent"]["data"]["warnings"], answer


def test_a_setting_only_a_postdating_line_states_reads_provisional_on_its_own_view_and_its_verify(rec):
    """Review of group 10: rb show <exp>/<setting> and rb spec verify said verified for a setting only a postdating line
    states, beside the note that says it is provisional. Its own view, the verify receipt and its count say provisional;
    the record keeps what rb read."""
    rec.write("docs/report.md", "# Report\n\nWe trained for 90 epochs.\n", when=JUNE)
    run = rec.ok("run", "add", "prec", "fp32.top1=0.761", "int8.top1=0.755", "--produced-at", "2026-05-20T10:00:00Z")["data"]["object"]["id"]
    said = f"epochs is provisional: docs/report.md has no commit from before run {run} was produced (2026-05-20): the file postdates the run"
    rec.ok("spec", "set", "prec", "epochs", "90", "--source", "docs/report.md:3", "--quote", "for 90 epochs", "--term", "epochs")
    shown = rec.text("show", "prec/epochs")
    assert shown.startswith("prec/epochs = 90 · provisional\n") and f"  · {said}\n" in shown, shown
    out = rec.text("spec", "verify", "prec", "epochs")
    assert out.startswith("  epochs: provisional, docs/report.md:3") and "but it postdates a run (below)\n" in out, out
    assert "0 verified · 1 provisional · 0 failed · 0 not checkable\n" in out and f"  {said}\n" in out, out
    env = rec.ok("spec", "verify", "prec", "epochs")
    assert env["data"]["verified_count"] == 0 and said in env["data"]["warnings"]
    assert env["data"]["results"][0]["ok"] is True                    # the file states it; the record keeps what rb read
    assert rec.ok("show", "prec/epochs")["data"]["object"]["status"] == "verified"


def test_the_users_vouch_for_a_setting_only_a_postdating_line_states_shows_as_any_vouch_does(rec):
    """Review of group 10: the user's vouch for a setting that reads provisional because only a postdating line states
    it was accepted and then shown on no screen. It reads as a vouch for a stored provisional setting does, in place of
    the postdates note."""
    rec.write("docs/report.md", "# Report\n\nWe trained for 90 epochs.\n", when=JUNE)
    rec.ok("run", "add", "prec", "fp32.top1=0.761", "int8.top1=0.755", "--produced-at", "2026-05-20T10:00:00Z")
    rec.ok("spec", "set", "prec", "epochs", "90", "--source", "docs/report.md:3", "--quote", "for 90 epochs", "--term", "epochs")
    assert "postdates the run" in rec.text("show", "prec")
    rec.ok("decide", "prec/epochs", "accept", "--why", "we trained for 90 epochs")
    shown = rec.text("show", "prec")
    assert '    epochs                   90             provisional · docs/report.md:3 "for 90 epochs" · vouched\n' in shown, shown
    assert "postdates the run" not in shown and "epochs = 90 is provisional · vouched for (" in shown, shown
    codes = [c["code"] for c in rec.finding("c-int8")["caveats"]]
    assert "vouched" in codes and "source_postdates_run" not in codes, codes
    assert "settings it reads are not verified" not in rec.text("show", "c-int8")


def test_a_setting_read_from_the_working_tree_is_as_old_as_its_line_and_a_run_add_says_so(rec):
    """Review of group 9: a setting verified from the working tree, with no --commit, on a line changed after a past
    run it covers, said nothing, as the file had an older commit; a run recorded after such a setting said nothing at
    the write; and a commit made later the same UTC day read as made the same day. The line is as old as the commit
    that last changed it, a run add says it of the run it records, and the same day gives both UTC times."""
    rec.write("sweep/train.py", "EPOCHS = 90\nLR = 0.1\n", when=MAY)
    rec.write("sweep/train.py", "EPOCHS = 90\nLR = 0.2\n", when=JUNE)
    june = rec.git("rev-parse", "HEAD").strip()[:7]
    first = rec.ok("run", "add", "prec", "fp32.top1=0.761", "int8.top1=0.755", "--produced-at", "2026-05-20T10:00:00Z")["data"]["object"]["id"]
    said = lambda run, day: f"lr is provisional: sweep/train.py:2 was last changed at {june}, committed 2026-06-01, after run {run} was produced ({day}): the line postdates the run"
    env = rec.ok("spec", "set", "prec", "lr", "0.2", "--source", "sweep/train.py:2", "--term", "LR")
    assert env["data"]["object"]["status"] == "verified" and said(first, "2026-05-20") in env["data"]["warnings"], env["data"].get("warnings")
    notes = lambda cid: [c["text"] for c in rec.finding(cid)["caveats"] if c["code"] == "source_postdates_run"]
    assert notes("c-int8") == [said(first, "2026-05-20")]
    # a line no commit after the run changed says nothing of the kind
    env = rec.ok("spec", "set", "prec", "epochs", "90", "--source", "sweep/train.py:1", "--term", "EPOCHS")
    assert not [w for w in env["data"].get("warnings", []) if "postdates" in w] and notes("c-int8") == [said(first, "2026-05-20")]
    # a run recorded under the setting says it at the write; one made after the line changed does not
    env = rec.ok("run", "add", "prec", "fp32.top1=0.760", "int8.top1=0.754", "--produced-at", "2026-05-21T10:00:00Z")
    second = env["data"]["object"]["id"]
    assert said(second, "2026-05-21") in env["data"]["warnings"], env["data"].get("warnings")
    later = rec.ok("run", "add", "prec", "fp32.top1=0.762", "int8.top1=0.756", "--produced-at", "2026-06-05T10:00:00Z")
    assert not [w for w in later["data"].get("warnings", []) if "postdates" in w]
    # later the same UTC day: both times, in UTC
    rec.write("sweep/eval.py", "BATCH = 256\n", when="2026-05-20T15:30:00-05:00")
    late = rec.git("rev-parse", "HEAD").strip()[:7]
    env = rec.ok("spec", "set", "prec", "batch", "256", "--source", "sweep/eval.py:1", "--commit", late, "--term", "BATCH")
    assert (f"batch is provisional: sweep/eval.py at {late} was committed 2026-05-20 20:30 UTC, after run {first} was produced (2026-05-20 10:00 UTC): "
            "the file postdates the run") in env["data"]["warnings"], env["data"].get("warnings")


def test_a_value_written_with_a_count_suffix_is_told_the_count_its_source_states(rec):
    """Review of round 6's build: rb spec set ... 999M with a quote that says '999M-token' answered that the source
    'says something else', hiding the number. It says the count the quote states, and how to give it."""
    rec.write("docs/plan.md", "# Plan\n\nThe corpus is a fixed 999M-token mix.\n")
    said = rec.text("spec", "set", "prec", "train_tokens", "999M", "--source", "docs/plan.md:3", "--quote", "a fixed 999M-token mix", "--term", "token")
    assert "the record says 999M, but docs/plan.md:3 says 999000000" in said and "something else" not in said, said
    env = rec.ok("spec", "set", "prec", "train_tokens", "999000000", "--source", "docs/plan.md:3", "--quote", "a fixed 999M-token mix", "--term", "token")
    assert env["data"]["object"]["status"] == "verified"
