"""The agent may forget; rb may not. The journal watches the repository, reconcile matches what it
saw against what the project declares, and the user's screen turns the match into one key.

These tests play the forgotten-run scenario end to end: work happens with no rb call, the journal
picks it up, reconcile proposes the exact rb run add only when the target is unambiguous, and
recording it through the screen lands as the user, with the observed facts as provenance."""
import asyncio
import os
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

from rabbit_brain.models import Envelope
from rbtest import as_the_user, command, confirm_designs, environment, home, main, state

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
    monkeypatch.setenv("CLAUDECODE", "1")     # the agent set the structure up
    assert rb("experiment", "add", "the experiment", "--id", "e1")[0] == 0
    assert rb("criterion", "add", "top1 holds", "-e", "e1", "--metric", "top1", "--at-least", "0.7", "--id", "c1")[0] == 0
    monkeypatch.delenv("CLAUDECODE")
    return rb


def test_the_scan_journals_changes_and_only_changes(repo):
    rec = state()
    Path("out").mkdir()
    Path("out/a.json").write_text('{"top1": 0.9}', encoding="utf-8")
    s = rec.scan()
    assert s["new"] >= 1 and s["gone"] == 0
    assert "journal/" in (Path(".rb/.gitignore").read_text(encoding="utf-8"))
    assert rec.scan()["new"] == 0 == rec.scan()["changed"]
    Path("out/a.json").write_text('{"top1": 0.9}', encoding="utf-8")     # touched, same bytes
    assert rec.scan()["changed"] == 0
    Path("out/a.json").write_text('{"top1": 0.8}', encoding="utf-8")
    assert rec.scan()["changed"] == 1
    Path("out/a.json").unlink()
    assert rec.scan()["gone"] == 1
    ops = [e["op"] for e in rec.journal(None)]
    assert "file" in ops and "git" in ops


def test_reconcile_places_by_criteria_and_aliases_and_never_guesses(repo, capsys):
    assert repo("metric", "add", "val_loss", "--unit", "nats", "--minimize", "--alias", "cross_entropy")[0] == 0
    os.environ["CLAUDECODE"] = "1"
    try:
        assert repo("experiment", "add", "second", "--id", "e2")[0] == 0
        assert repo("criterion", "add", "val_loss small", "-e", "e2", "--metric", "change.val_loss", "--at-most", "0.05")[0] == 0
    finally:
        del os.environ["CLAUDECODE"]
    Path("out").mkdir()
    Path("out/one.json").write_text('{"top1": 0.9}', encoding="utf-8")
    Path("out/two.json").write_text('{"adaptive": {"cross_entropy": 5.6}}', encoding="utf-8")
    Path("out/none.json").write_text('{"weird_number": 1.2}', encoding="utf-8")
    Path("out/both.json").write_text('{"top1": 0.8, "val_loss": 2.0}', encoding="utf-8")
    rec = state()
    rec.scan()
    placed, unplaced = rec.found()
    by_path = {it.path: it for it in placed}
    assert by_path["out/one.json"].experiment == "e1" and by_path["out/one.json"].argv == ["run", "add", "e1", "--from", "out/one.json"]
    assert by_path["out/two.json"].experiment == "e2"           # the alias, under a variant prefix, still names val_loss
    assert "out/both.json" not in by_path                        # two experiments' criteria match: ambiguous, never guessed
    assert any("out/none.json" in line and "nothing declares" in line for line in unplaced)
    assert any("out/both.json" in line for line in unplaced)


def test_found_clears_when_recorded_and_on_dismissal(repo, capsys):
    Path("out").mkdir()
    Path("out/one.json").write_text('{"top1": 0.9}', encoding="utf-8")
    Path("out/two.json").write_text('{"top1": 0.6}', encoding="utf-8")
    rec = state()
    rec.scan()
    placed = rec.found()[0]
    assert {it.path for it in placed} == {"out/one.json", "out/two.json"}
    code, env = repo("run", "add", "e1", "--from", "out/one.json")
    assert code == 0
    remaining = state().found()[0]
    assert [it.path for it in remaining] == ["out/two.json"]     # recording the run cleared it without a rescan
    rec.dismiss(remaining[0].key, "a scratch file")
    assert state().found()[0] == []
    assert any(e.get("op") == "dismiss" for e in rec.journal(None))


def test_the_wrapper_records_the_command_its_exit_and_what_it_wrote(repo, capsys):
    # --json goes before the --, or it would belong to the wrapped command.
    code = main(["journal", "run", "--json", "--", "python", "-c",
                 "import pathlib, sys; pathlib.Path('out').mkdir(exist_ok=True); pathlib.Path('out/r.json').write_text('{\"top1\": 0.75}'); sys.exit(3)"])
    env = Envelope.model_validate_json(capsys.readouterr().out)
    assert code == 1 and env.ok and env.data["exit"] == 3        # the exit code is data, and it is on record
    runs = [e for e in state().journal(None) if e.get("op") == "run"]
    assert runs and runs[-1]["exit"] == 3 and "out/r.json" in runs[-1]["wrote"]
    it = next(i for i in state().found()[0] if i.path == "out/r.json")
    assert any("written by:" in f and "python -c" in f for f in it.facts)


def test_a_file_a_watched_command_wrote_is_proposed_with_the_code_it_ran_at(repo, capsys, monkeypatch):
    """rb journal run keeps the commit a command ran at (and its tree, when it had uncommitted changes); the run rb
    reconcile proposes from a file that command wrote names them, so recorded hours later its code is still known."""
    import sys
    from datetime import datetime, timedelta, timezone
    Path("eval.py").write_text("import json, pathlib\npathlib.Path('out').mkdir(exist_ok=True)\n"
                               "pathlib.Path('out/r.json').write_text(json.dumps({'top1': 0.77}))\n", encoding="utf-8")
    subprocess.run(["git", "add", "eval.py"], check=True)
    subprocess.run(["git", "commit", "-qm", "eval"], check=True)
    head = subprocess.run(["git", "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
    monkeypatch.setenv("RB_TEST_NOW", (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat())
    assert main(["journal", "run", "--", sys.executable, "eval.py"]) == 0
    monkeypatch.delenv("RB_TEST_NOW")
    capsys.readouterr()
    code, env = repo("reconcile")
    item = next(f for f in env.data["found"] if f["path"] == "out/r.json")
    assert item["argv"][:7] == ["run", "add", "e1", "--from", "out/r.json", "--commit", head], item["argv"]
    assert repo("reconcile", "--apply")[0] == 0
    code, env = repo("show", "e1")
    rows = {r["what"]: (r["state"], r["detail"]) for r in env.data["rerunnability"]}
    assert rows["code"][0] == "ok" and rows["code"][1].startswith(f"commit {head[:10]}"), rows["code"]


def test_a_watched_command_runs_in_the_worktree_it_was_given_in(repo, capsys, monkeypatch):
    """From a worktree nested in the checkout, with no record of its own, rb journal run runs the command there, on that
    worktree's code, keeps that worktree's commit and path, and puts the line in the record's journal; in a single
    checkout the command still runs in the record's root, from wherever it was given."""
    import sys
    Path("train.py").write_text("lr = 0.1\n", encoding="utf-8")
    subprocess.run(["git", "add", "train.py"], check=True)
    subprocess.run(["git", "commit", "-qm", "train"], check=True)
    root = Path.cwd().resolve()
    wt = root / ".worktrees" / "b"
    subprocess.run(["git", "worktree", "add", "-q", "-b", "exp-b", str(wt)], check=True)
    (wt / "train.py").write_text("lr = 0.25\n", encoding="utf-8")
    show = "import os; print('PWD=' + os.getcwd()); print(open('train.py').read().strip())"

    def ran(cwd: Path) -> tuple:
        monkeypatch.chdir(cwd)
        assert main(["journal", "run", "--", sys.executable, "-c", show]) == 0
        out = capsys.readouterr().out
        pwd = Path(next(line for line in out.splitlines() if line.startswith("PWD="))[4:]).resolve()
        [entry] = [e for e in state(root).journal(1) if e.get("op") == "run"]
        return pwd, out, entry

    pwd, out, entry = ran(wt)
    assert pwd == wt and "lr = 0.25" in out
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=wt, check=True, capture_output=True, text=True).stdout.strip()
    assert entry["code"]["commit"] == head and entry["code"]["tree"] and entry["worktree"] == {"path": wt.as_posix()}
    assert not (wt / ".rb").exists()
    (root / "sub").mkdir()
    pwd, out, entry = ran(root / "sub")
    assert pwd == root and "lr = 0.1" in out and "worktree" not in entry


def test_status_and_reconcile_carry_the_found_count(repo, capsys):
    Path("out").mkdir()
    Path("out/one.json").write_text('{"top1": 0.9}', encoding="utf-8")
    state().scan()
    code, env = repo("status")
    assert code == 0 and env.data["found_unrecorded"] == 1
    code, env = repo("reconcile")
    assert code == 0
    assert env.data["found"][0]["argv"] == ["run", "add", "e1", "--from", "out/one.json"]
    assert env.next == ["rb reconcile --apply", "rb run add e1 --from out/one.json"]     # keep it all, or record just this


def test_status_says_nothing_is_open_only_while_rb_found_no_run_either(repo, capsys):
    """A run rb found and nobody recorded is open too, as the screens put it under Your call: rb status says nothing
    is open only while it lists no item and found no run. Numbers nothing declares are found and not open, as the
    screens count them: nothing waits on you there either."""
    for top1 in ("0.8", "0.81", "0.82"):
        assert repo("run", "add", "e1", f"top1={top1}")[0] == 0
    assert state().status()["open"] == []
    assert main(["status"]) == 0
    assert "Nothing open." in capsys.readouterr().out
    Path("out").mkdir()
    Path("out/one.json").write_text('{"top1": 0.83}', encoding="utf-8")
    state().scan()
    assert main(["status"]) == 0
    out = capsys.readouterr().out
    assert "Found in the repository, not in the record (1)" in out and "Nothing open." not in out
    assert repo("run", "add", "e1", "--from", "out/one.json")[0] == 0
    Path("out/two.json").write_text('{"loss": 0.3}', encoding="utf-8")
    state().scan()
    assert main(["status"]) == 0
    out = capsys.readouterr().out
    assert "Found in the repository, not in the record (1)" in out and "Nothing open." in out
    waiting = home().quiet      # the agent set it up
    assert waiting.startswith("1 design waits for your confirmation, listed apart. "), waiting
    assert confirm_designs() == ["e1"]
    h = home()
    assert h.found == [] and h.unplaced and h.quiet.startswith("Nothing waits on you.")
    # while designs alone wait, the line counts the record as it does once nothing waits (trial of group E)
    assert waiting.split("listed apart. ", 1)[1] == h.quiet.split("Nothing waits on you. ", 1)[1], (waiting, h.quiet)


def test_status_says_the_rb_run_add_only_of_the_runs_rb_found(repo, capsys):
    """rb status's line about what rb found says rb reconcile gives an rb run add for the runs it can record, and that
    it lists the numbers rb cannot place in one experiment, whichever of the two it found, never that it gives those
    none: numbers several experiments read are listed with an rb run add to choose from. rb compile, given no paths,
    says the same of what the journal found."""
    def found_line() -> str:
        state().scan()
        assert main(["status"]) == 0
        return next(line for line in capsys.readouterr().out.splitlines() if line.startswith("Found in the repository"))

    def compiled_line() -> str:
        assert main(["compile"]) == 0
        return next(line for line in capsys.readouterr().out.splitlines() if line.startswith("The journal has seen"))

    Path("out").mkdir()
    Path("out/one.json").write_text('{"top1": 0.83}', encoding="utf-8")
    assert found_line() == "Found in the repository, not in the record (1): rb reconcile lists it, with the rb run add that records it"
    Path("out/two.json").write_text('{"loss": 0.3}', encoding="utf-8")
    assert found_line() == ("Found in the repository, not in the record (2): rb reconcile lists them, with the rb run add for the one run; "
                            "rb cannot place the numbers of the other one in one experiment")
    assert compiled_line() == ("The journal has seen work nobody recorded: rb reconcile lists them, with the rb run add for the one run; "
                               "rb cannot place the numbers of the other one in one experiment.")
    assert repo("run", "add", "e1", "--from", "out/one.json")[0] == 0
    assert found_line() == "Found in the repository, not in the record (1): numbers rb cannot place in one experiment, which rb reconcile lists"
    Path("out/three.json").write_text('{"top1": 0.84}', encoding="utf-8")
    Path("out/four.json").write_text('{"top1": 0.85}', encoding="utf-8")
    assert found_line() == ("Found in the repository, not in the record (3): rb reconcile lists them, with the rb run add for each of the 2 runs; "
                            "rb cannot place the numbers of the other one in one experiment")
    Path("out/two.json").unlink()
    assert found_line() == "Found in the repository, not in the record (2): rb reconcile lists them, with the rb run add for each"
    assert repo("experiment", "add", "another", "--id", "e2")[0] == 0
    assert repo("criterion", "add", "top1 holds there too", "-e", "e2", "--metric", "top1", "--at-least", "0.5")[0] == 0
    assert found_line() == "Found in the repository, not in the record (2): numbers rb cannot place in one experiment, which rb reconcile lists"
    assert compiled_line() == "The journal has seen work nobody recorded: numbers rb cannot place in one experiment, which rb reconcile lists."
    assert main(["reconcile"]) == 0
    assert "(rb run add <e1|e2> --from out/three.json)" in capsys.readouterr().out


async def until(pilot, ready, timeout: float = 15.0) -> None:
    """Pause until the screen is in the state the next key acts on. The fixed pauses alone are too short on a loaded
    machine (the key then lands on another row or screen); what the tests assert is unchanged."""
    for _ in range(int(timeout * 10)):
        if ready():
            return
        await pilot.pause(0.1)


def on_home(app, found: int) -> bool:
    """Home is showing with this many found items, and when there is one, the next key acts on it."""
    from rabbit_brain.tui import HomeScreen

    s = app.screen
    return isinstance(s, HomeScreen) and len(s.home.found) == found and (found == 0 or getattr(app.focused, "id", None) == "calls")


def test_the_screen_records_a_forgotten_run_as_the_user_and_hides_scratch(repo):
    from rabbit_brain.tui import CardScreen, RBApp, Reason

    Path("out").mkdir()
    Path("out/metrics.json").write_text('{"top1": 0.81}', encoding="utf-8")
    state().scan()
    h = home()
    assert len(h.found) == 1
    assert h.found[0].title == "Record metrics.json on e1"
    assert h.found[0].asked_by == "rb, watching the repository"

    async def drive():
        app = RBApp(Path.cwd())
        async with app.run_test(size=(110, 32)) as pilot:
            await pilot.pause(0.3)
            await until(pilot, lambda: on_home(app, found=1))
            await pilot.press("enter")
            await pilot.pause(0.3)
            await until(pilot, lambda: isinstance(app.screen, CardScreen))
            assert isinstance(app.screen, CardScreen)
            assert app.screen.card.kind == "record"
            assert any("sha" in f for f in app.screen.card.facts)
            await pilot.press("y")
            await pilot.pause(0.2)
            await until(pilot, lambda: isinstance(app.screen, Reason))
            assert isinstance(app.screen, Reason)
            for ch in "saw the run myself":
                await pilot.press(ch if ch != " " else "space")
            await pilot.press("enter")
            await pilot.pause(1.5)
            await until(pilot, lambda: on_home(app, found=0))      # recorded, and home shows nothing waiting
            if app.focused is None:
                # The screen can leave nothing focused once the last call is settled from its card (a timing race in
                # the TUI, older than format 1); a call arriving then loses the focus to the catalogue. The user
                # presses tab to get a highlight back, which puts the focus on the catalogue, where a call takes it.
                await pilot.press("tab")
            # Now the scratch file: found on refresh, hidden with n and a bare enter.
            Path("out/scratch.json").write_text('{"top1": 0.1}', encoding="utf-8")
            await pilot.press("r")
            await pilot.pause(0.5)
            await until(pilot, lambda: on_home(app, found=1))
            await pilot.press("enter")
            await pilot.pause(0.3)
            await until(pilot, lambda: isinstance(app.screen, CardScreen))
            assert isinstance(app.screen, CardScreen) and app.screen.card.kind == "record"
            await pilot.press("n")
            await pilot.pause(0.2)
            await until(pilot, lambda: isinstance(app.screen, Reason))
            await pilot.press("enter")
            await pilot.pause(0.5)
            await until(pilot, lambda: on_home(app, found=0))

    asyncio.run(drive())
    rec = state()
    runs = rec.records("run", live=True)
    assert len(runs) == 1
    run = runs[0]
    assert run.experiment == "e1" and run.metrics == {"top1": 0.81} and run.basis == "file"
    assert run.receipt.actor.startswith("human:")                # recorded as the user, not the agent
    assert run.note == "saw the run myself"
    placed, unplaced = rec.found()
    assert placed == [] and unplaced == []                       # one recorded, one hidden
    assert len([e for e in rec.journal(None) if e.get("op") == "dismiss"]) == 1


def test_y_at_home_records_without_opening_the_card(repo):
    from rabbit_brain.tui import RBApp, Reason

    Path("out").mkdir()
    Path("out/metrics.json").write_text('{"top1": 0.79}', encoding="utf-8")
    state().scan()

    async def drive():
        app = RBApp(Path.cwd())
        async with app.run_test(size=(118, 34)) as pilot:
            await pilot.pause(0.4)
            await until(pilot, lambda: on_home(app, found=1))
            await pilot.press("y")                               # act on the highlighted row from home
            await pilot.pause(0.2)
            await until(pilot, lambda: isinstance(app.screen, Reason))
            assert isinstance(app.screen, Reason)
            await pilot.press("enter")                           # no note
            await pilot.pause(1.5)
            await until(pilot, lambda: on_home(app, found=0))

    asyncio.run(drive())
    rec = state()
    runs = rec.records("run", live=True)
    assert len(runs) == 1 and runs[0].metrics == {"top1": 0.79}
    assert runs[0].receipt.actor.startswith("human:")


def test_a_file_of_progress_is_a_heartbeat_unless_the_project_measures_it(repo):
    Path("out").mkdir()
    Path("out/log.json").write_text('{"step": 120, "epoch": 3}', encoding="utf-8")
    code, env = repo("reconcile")
    assert code == 0 and env.data["found"] == []
    assert any(line.startswith("out/log.json holds only progress") for line in env.data["unplaced"])
    # steps to a target loss is a result when the project measures it
    assert repo("criterion", "add", "few steps", "-e", "e1", "--metric", "steps", "--at-most", "100", "--id", "c2")[0] == 0
    Path("out/steps.json").write_text('{"steps": 80}', encoding="utf-8")
    code, env = repo("reconcile")
    assert [f["argv"] for f in env.data["found"]] == [["run", "add", "e1", "--from", "out/steps.json"]]
    assert not any(line.startswith("out/steps.json") for line in env.data["unplaced"])


def _watched(capsys, *cmd):
    """rb journal run --json -- <cmd>: the envelope, from a standard output that carries nothing else."""
    code = main(["journal", "run", "--json", "--", *cmd])
    captured = capsys.readouterr()
    return code, Envelope.model_validate_json(captured.out), captured.err


def test_a_watched_command_that_prints_its_metrics_is_proposed_as_a_run(repo, capsys):
    import sys
    Path("eval.py").write_text('print("evaluating")\nprint(\'{"metrics": {"top1": 0.81, "extra": {"top5": 0.95}}}\')\n', encoding="utf-8")
    code, env, err = _watched(capsys, sys.executable, "eval.py")
    assert code == 0 and env.data["metrics"] == {"top1": 0.81, "extra.top5": 0.95}
    assert "evaluating" in err                        # the command's output passes through, beside the envelope
    entry = [e for e in state().journal(None) if e.get("op") == "run"][-1]
    assert entry["metrics"] == {"top1": 0.81, "extra.top5": 0.95} and "tail" not in entry
    code, env = repo("reconcile")
    item = next(f for f in env.data["found"] if f["path"].endswith("eval.py"))
    assert item["experiment"] == "e1" and item["argv"][:5] == ["run", "add", "e1", "extra.top5=0.95", "top1=0.81"]
    ran = shlex.join([sys.executable, "eval.py"])       # as rb writes a command line: quoted the way a POSIX shell reads it
    assert item["argv"][5:7] == ["--command", ran] and item["argv"][7] == "--produced-at"
    code, env = repo("reconcile", "--apply")
    assert any(r.startswith("run") and "eval.py" in r for r in env.data["recorded"]), env.data["recorded"]
    run = state().records("run", live=True)[-1]
    assert run.metrics["top1"] == 0.81 and run.receipt.command == ran
    code, env = repo("reconcile")
    assert not any(f["path"].endswith("eval.py") for f in env.data["found"])      # recorded: proposed no more


def test_what_a_watched_command_printed_is_named_by_the_command_never_by_a_file_in_it(repo, capsys):
    """A command whose words hold a path printed its numbers: the user's screen names what it found by the command as
    given (shortened), never by the part after the command's last slash, a file it never wrote."""
    import sys
    show = "import json; print(json.dumps({'metrics': {'top1': 0.81}}))  # results/eval.json is for later"
    code, _env, _err = _watched(capsys, sys.executable, "-c", show)
    assert code == 0
    ran = shlex.join([sys.executable, "-c", show])
    said = ran if len(ran) <= 48 else ran[:18] + "…" + ran[-29:]
    assert [f.title for f in home().found] == [f"Record what `{said}` printed on e1"]


def test_runs_of_one_command_that_differ_at_its_end_read_apart(repo, capsys):
    """Three runs of a long command that differ only in their last word (the seed) are named by the command's start and
    end, so they read apart; two that would still read the same (the same command run twice) say what each printed."""
    import sys
    show = "import json, sys; print(json.dumps({'metrics': {'top1': 0.8 + int(sys.argv[-1]) / 100}}))"
    for seed in ("1", "2", "3"):
        assert _watched(capsys, sys.executable, "-c", show, "--seed", seed)[0] == 0
    titles = [f.title for f in home().found]
    assert len(titles) == 3 and len(set(titles)) == 3 and all(t.endswith(f"--seed {s}` printed on e1") for t, s in zip(sorted(titles), "123"))
    again = "import json, pathlib; p = pathlib.Path('n.txt'); n = int(p.read_text()) + 1 if p.exists() else 1; p.write_text(str(n)); print(json.dumps({'metrics': {'top1': 0.7 + n / 100}}))"
    for _ in range(2):
        assert _watched(capsys, sys.executable, "-c", again)[0] == 0
    twice = sorted(f.title for f in home().found if "0.7 + n" in f.title)
    assert len(twice) == 2 and twice[0].endswith(" printed on e1 · top1=0.71") and twice[1].endswith(" printed on e1 · top1=0.72"), twice


def test_a_command_that_fails_keeps_its_last_lines(repo, capsys):
    import sys
    Path("bad.py").write_text("for i in range(14):\n    print(f'line {i}')\nraise SystemExit(3)\n", encoding="utf-8")
    code, env, _err = _watched(capsys, sys.executable, "bad.py")
    assert code != 0 and env.data["exit"] == 3 and env.data["metrics"] == {}
    assert env.data["tail"] == [f"line {i}" for i in range(4, 14)]
    entry = [e for e in state().journal(None) if e.get("op") == "run"][-1]
    assert entry["tail"][-1] == "line 13" and "metrics" not in entry


def test_printed_numbers_nothing_reads_are_listed_never_placed(repo, capsys):
    import sys
    Path("other.py").write_text('import json\nprint(json.dumps({"metrics": {"wall_s": 3.2}}))\n', encoding="utf-8")
    _watched(capsys, sys.executable, "other.py")
    code, env = repo("reconcile")
    assert not any(f["path"].endswith("other.py") for f in env.data["found"])
    assert any("other.py" in line and "wall_s" in line for line in env.data["unplaced"])


@pytest.mark.skipif(os.name != "posix", reason="a pty is how a terminal's command keeps its line-by-line output")
def test_in_a_terminal_the_command_gets_a_terminal_and_its_metrics_are_still_read(repo):
    """rb journal run from a terminal: the watched command's output is a terminal too, so its progress lines and its
    colours stay as they are, and rb still reads the metrics it printed."""
    import pty
    import select
    import sys
    import time
    Path("shows.py").write_text("import sys, json\nprint('a tty' if sys.stdout.isatty() else 'a pipe')\nprint(json.dumps({'metrics': {'top1': 0.5}}))\n",
                              encoding="utf-8")
    master, slave = pty.openpty()
    proc = subprocess.Popen([*command(), "journal", "run", "--", sys.executable, "shows.py"], stdin=subprocess.DEVNULL, stdout=slave,
                            stderr=subprocess.DEVNULL, env=environment())
    os.close(slave)
    seen, deadline = b"", time.monotonic() + 60
    while time.monotonic() < deadline:
        if not select.select([master], [], [], 0.2)[0]:
            if proc.poll() is not None:
                break
            continue
        try:
            chunk = os.read(master, 4096)
        except OSError:                        # the terminal's other end closed: everything was read
            break
        if not chunk:
            break
        seen += chunk
    os.close(master)
    try:
        code = proc.wait(timeout=30)
    except subprocess.TimeoutExpired:          # rb kept waiting on a terminal nothing writes to any more
        proc.kill()
        proc.wait()
        raise
    assert code == 0 and b"a tty" in seen and b"a pipe" not in seen
    entry = [e for e in state().journal(None) if e.get("op") == "run"][-1]
    assert entry["metrics"] == {"top1": 0.5}


def test_a_record_below_the_top_runs_a_nested_worktree_s_command_in_its_folder(tmp_path_factory, monkeypatch, capsys):
    """A record in a folder of its repository (repo/sub/.rb): from a worktree nested in it, rb journal run runs the
    command in the worktree's copy of that folder, as a single checkout runs it in the record's folder, and the line
    rb status says there, and E_NO_PROJECT in a worktree outside, name the record's own folder."""
    import json
    import sys
    as_the_user(monkeypatch)
    cfg = tmp_path_factory.mktemp("gitconfig") / "config"
    cfg.write_text("[user]\n\temail = ana@example.org\n\tname = ana\n[init]\n\tdefaultBranch = main\n", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(cfg))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    repo = tmp_path_factory.mktemp("mono").resolve() / "repo"
    (repo / "sub").mkdir(parents=True)
    git = lambda *a, cwd=repo: subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True, text=True).stdout  # noqa: E731
    git("init", "-q")
    (repo / "sub" / "train.py").write_text("lr = 0.1\n", encoding="utf-8")
    (repo / "sub" / ".gitignore").write_text(".wt/\n", encoding="utf-8")
    (repo / "README.md").write_text("# INT8 ResNet-50 on ImageNet\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-qm", "start")
    monkeypatch.chdir(repo / "sub")
    assert main(["init", "INT8 ResNet-50 on ImageNet", "--no-agents"]) == 0
    capsys.readouterr()
    wt = repo / "sub" / ".wt" / "x"
    git("worktree", "add", "-q", "-b", "x", str(wt))
    (wt / "sub" / "train.py").write_text("lr = 0.25\n", encoding="utf-8")
    show = "import os; print('PWD=' + os.getcwd()); print(open('train.py').read().strip())"
    for start in (wt, wt / "sub"):
        monkeypatch.chdir(start)
        assert main(["journal", "run", "--", sys.executable, "-c", show]) == 0, start
        out = capsys.readouterr().out
        assert Path(next(line for line in out.splitlines() if line.startswith("PWD="))[4:]).resolve() == wt / "sub"
        assert "lr = 0.25" in out
    listed = {Path(line[len("worktree "):]).resolve(): line[len("worktree "):] for line in git("worktree", "list", "--porcelain").splitlines()
              if line.startswith("worktree ")}
    assert main(["status"]) in (0, 1)
    assert (f"This worktree (x · {listed[wt]}) has no record of its own: rb writes to the record in main · {listed[repo]}/sub, "
            "and each run keeps this worktree's code.") in capsys.readouterr().out
    assert main(["status", "--json"]) in (0, 1)
    assert Path(json.loads(capsys.readouterr().out)["data"]["checkout"]["path"]).resolve() == wt
    outside = repo.parent / "out"
    git("worktree", "add", "-q", "-b", "out", str(outside))
    monkeypatch.chdir(outside / "sub")
    assert main(["status", "--json"]) == 2
    err = json.loads(capsys.readouterr().out)["errors"][0]
    assert err["message"].endswith(f"the project's record is in main · {listed[repo]}/sub.")
    assert err["fix"] == f"To bring it here, commit .rb/ in {listed[repo]}/sub, then run git merge main here."


def test_what_a_watched_command_wrote_in_a_nested_worktree_is_on_its_line(repo, capsys, monkeypatch):
    """rb journal run in a worktree nested in the checkout lists what the command wrote in that worktree's result
    folders, by its path from the record's root, as it does in the checkout; and what the command printed is still
    found, since the record's scan does not watch the file that holds the same numbers."""
    import json
    import sys
    Path("train.py").write_text("lr = 0.1\n", encoding="utf-8")
    subprocess.run(["git", "add", "train.py"], check=True)
    subprocess.run(["git", "commit", "-qm", "train"], check=True)
    root = Path.cwd().resolve()
    wt = root / ".worktrees" / "b"
    subprocess.run(["git", "worktree", "add", "-q", "-b", "exp-b", str(wt)], check=True)
    write = ("import os, json; os.makedirs('out', exist_ok=True); open('out/metrics.json', 'w').write(json.dumps({'top1': 0.75})); "
             "print(json.dumps({'metrics': {'top1': 0.75}}))")
    monkeypatch.chdir(wt)
    assert main(["journal", "run", "--", sys.executable, "-c", write]) == 0
    capsys.readouterr()
    [entry] = [e for e in state(root).journal(1) if e.get("op") == "run"]
    assert entry["wrote"] == [".worktrees/b/out/metrics.json"] and (wt / "out" / "metrics.json").is_file()
    monkeypatch.chdir(root)
    code, env = repo("reconcile")
    assert [f["metrics"] for f in env.data["found"] if f["experiment"] == "e1"] == [{"top1": 0.75}]
    # in the checkout itself, what it wrote is listed as before
    assert main(["journal", "run", "--", sys.executable, "-c", write]) == 0
    capsys.readouterr()
    [entry] = [e for e in state(root).journal(1) if e.get("op") == "run"]
    assert "out/metrics.json" in entry["wrote"] and "worktree" not in entry
