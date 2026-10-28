"""The screen reads one record (what rb found, its card, a record's card), the confirm dialog says what a yes records
and never as whom, and the last-seen marker is real: what was written since, whoever wrote it, is counted with no name."""
import asyncio
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from rabbit_brain.models import Envelope
from rbtest import as_the_user, found_card, home, main, mark_seen, object_card, state

HAS_GIT = shutil.which("git") is not None
pytestmark = pytest.mark.skipif(not HAS_GIT, reason="the research state is played in a git repository")


@pytest.fixture
def repo(workdir, monkeypatch, tmp_path_factory, capsys):
    as_the_user(monkeypatch)
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

    assert rb("init", "t")[0] == 0
    monkeypatch.setenv("CLAUDECODE", "1")
    assert rb("experiment", "add", "the experiment", "--id", "e1")[0] == 0
    assert rb("criterion", "add", "top1 holds", "-e", "e1", "--metric", "top1", "--at-least", "0.7", "--id", "c1")[0] == 0
    assert rb("run", "add", "e1", "top1=0.76")[0] == 0
    monkeypatch.delenv("CLAUDECODE")
    return rb


def _found_one() -> None:
    """A run nobody recorded: its numbers land in out/, and rb finds them there."""
    Path("out").mkdir()
    Path("out/metrics.json").write_text('{"top1": 0.81}', encoding="utf-8")
    state().scan()


async def until(pilot, ready, timeout: float = 15.0) -> None:
    for _ in range(int(timeout * 10)):
        if ready():
            return
        await pilot.pause(0.1)


def test_the_screen_reads_what_rb_found_its_card_and_a_records_card(repo):
    _found_one()
    h = home()
    assert h.found and h.found[0].title == "Record metrics.json on e1"
    c = found_card(h.found[0].request_id)
    assert c.kind == "record" and c.argv[:3] == ["run", "add", "e1"]
    assert object_card("e1").title == "the experiment"


def test_the_modal_says_what_a_yes_records_and_no_id(repo):
    """y on what rb found asks for a note and says what it records, a run, through the rb run add anyone would type:
    who it is recorded as is rb's to decide (the user, at their own terminal), and the screen names no one."""
    from rabbit_brain.tui import YES_RECORDS, HomeScreen, RBApp, Reason

    assert home().weak_identity is None       # git identity set: no warning
    _found_one()

    async def drive():
        app = RBApp(Path.cwd())
        async with app.run_test(size=(110, 32)) as pilot:
            await until(pilot, lambda: isinstance(app.screen, HomeScreen) and getattr(app.focused, "id", None) == "calls")
            await pilot.press("y")                              # y at home acts on the highlighted call
            await until(pilot, lambda: isinstance(app.screen, Reason))
            assert isinstance(app.screen, Reason)
            assert app.screen.sub == YES_RECORDS == "recorded as a run, with this note, through the same rb run add anyone would type"
            await pilot.press("escape")

    asyncio.run(drive())
    assert len(state().records("run", live=True)) == 1                  # escape records nothing


def test_since_runs_from_the_last_look(repo, monkeypatch):
    old, lines = mark_seen()
    assert old == -1 and lines > 0                              # first look ever
    assert home().since is None                                 # nothing since they looked
    monkeypatch.setenv("CLAUDECODE", "1")
    assert repo("run", "add", "e1", "top1=0.79")[0] == 0
    monkeypatch.delenv("CLAUDECODE")
    h = home()
    assert h.since and "1 write" in h.since                     # the agent moved since the look
    old2, _ = mark_seen()
    assert old2 == lines                                        # the marker is the last look, kept
    assert home().since is None                                 # looked again: quiet again


def test_with_no_look_kept_nothing_is_counted_since_one(repo):
    """Before the user's first look nothing is counted since one: never the writes after their own last write, which
    are only what others wrote, so the count would mark them. Here an agent wrote after the user's rb init."""
    assert not (Path(".git") / "rb-last-seen.json").exists()
    assert home().since is None


def test_since_counts_every_write_whoever_made_it(repo, monkeypatch):
    """What was written since the user's last look is counted whoever wrote it, the user at another terminal or an
    agent, in one count with no name: a count of an agent's writes alone would mark which writes were an agent's."""
    mark_seen()
    assert repo("note", "add", "a note from another terminal", "-e", "e1")[0] == 0
    monkeypatch.setenv("CLAUDECODE", "1")
    assert repo("run", "add", "e1", "top1=0.79")[0] == 0
    monkeypatch.delenv("CLAUDECODE")
    day = state().log(1)[0]["at"][:10]
    assert home().since == f"Since you last looked: 2 writes, 1 run recorded · last {day}"


def test_since_counts_what_a_merge_brought_in_wherever_its_lines_sort(repo, monkeypatch):
    def git(*args):
        subprocess.run(["git", *args], check=True, capture_output=True)

    git("add", "-A")
    git("commit", "-qm", "the record")
    git("switch", "-qc", "elsewhere")
    monkeypatch.setenv("CLAUDECODE", "1")
    assert repo("run", "add", "e1", "top1=0.79")[0] == 0        # before the note the user sees
    git("add", "-A")
    git("commit", "-qm", "a run")
    git("switch", "-q", "-")
    assert repo("note", "add", "on this branch", "-e", "e1")[0] == 0
    monkeypatch.delenv("CLAUDECODE")
    mark_seen()
    git("add", "-A")
    git("commit", "-qm", "a note")
    git("merge", "-q", "--no-edit", "elsewhere")                # the run's line sorts in before the note's
    old, _ = mark_seen()                                        # the screen opens: since that look
    assert home(since_baseline=old).since.startswith("Since you last looked: 1 write, 1 run recorded")   # the run, not the note
    assert home().since is None


def test_a_marker_an_earlier_rb_kept_reads_as_it_did_for_one_look(repo):
    marker = Path(".git") / "rb-last-seen.json"
    marker.write_text('{"log_lines": 1, "at": "2026-10-01T12:00:00.000+00:00"}', encoding="utf-8")   # a count alone
    old, lines = mark_seen()
    assert old == 1
    assert home(since_baseline=old).since.startswith("Since you last looked: 3 writes, 1 run recorded")   # past that count
    kept = json.loads(marker.read_text(encoding="utf-8"))
    assert kept["log_lines"] == lines == len(kept["seen_lines"])  # the count kept beside the lines, for that rb
