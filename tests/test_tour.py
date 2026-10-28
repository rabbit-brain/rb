"""rb tour: a one-minute walkthrough builds a real project by running the real commands.
A human's tour ends with one criterion that meets its rule and one rb will not judge on too few units;
an agent's tour has its freeze refused and queued, which is the honest version; and the tour never lands
inside an existing record."""
import shutil
import subprocess
from pathlib import Path

import pytest

from rabbit_brain.models import Envelope
from rbtest import as_the_user, command, environment, main, state


@pytest.fixture
def clean(workdir, monkeypatch, tmp_path_factory):
    as_the_user(monkeypatch)
    cfg = tmp_path_factory.mktemp("gitconfig") / "config"
    cfg.write_text("[user]\n\temail = t@example.com\n\tname = t\n", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(cfg))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    return workdir


def test_a_humans_tour_ends_with_a_standing_and_a_refusal_to_round_up(clean, capsys):
    assert main(["tour"]) == 0
    text = capsys.readouterr().out
    assert "$ rb init" in text and "$ rb status" in text
    assert "Criteria: 1 criterion met · 1 criterion undetermined" in text
    assert "criterion undetermined: 2 units; the criterion needs 3" in text     # two runs report step_ms, the rule asks for three
    assert "rule criterion " not in text
    assert "rb will not round that up" in text  # the closing lines name the point
    # rb init's paste line, as the tour runs it, asks the agent to record where the work stands, not only to read
    assert "\n  Use Rabbit Brain to track this: read rb context, then record where the work stands.\n" in text
    assert (clean / "rb-tour" / ".rb").is_dir()
    # the scaffolding was committed, so receipts have a commit to record
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=clean / "rb-tour", capture_output=True, text=True)
    assert head.returncode == 0


def test_after_uvx_the_tour_says_how_to_run_its_next_commands(clean, tmp_path_factory):
    """uvx runs rb from its cache of tool environments, and rb is not on the user's path once it ends: a tour run from
    there says to start each next command with uvx --from rabbit-brain, or to keep rb with uv tool install; one run
    from an install says nothing of it."""
    said = "  rb is not on your PATH after uvx: start each with uvx --from rabbit-brain (uvx --from rabbit-brain rb ui), or keep rb with uv tool install rabbit-brain."
    rb = Path(command()[0])
    cached = tmp_path_factory.mktemp("cache") / "uv" / "archive-v0" / "Xy12ab" / "bin" / rb.name
    cached.parent.mkdir(parents=True)
    shutil.copy2(rb, cached)
    for exe, dest, says in ((cached, "via-uvx", True), (rb, "installed", False)):
        r = subprocess.run([str(exe), "tour", dest], cwd=clean, env=environment(), capture_output=True, text=True, stdin=subprocess.DEVNULL)
        assert r.returncode == 0, r.stderr
        assert (said in r.stdout.splitlines()) is says, (exe, r.stdout[-600:])


def test_the_tour_never_commits_under_an_identity_git_guesses(workdir, monkeypatch, tmp_path_factory, capsys):
    """With no identity set, git guesses one: the account's name with $EMAIL, or with user@host where the host name has
    a domain, as on a Mac. The tour never commits under it: its scaffolding stays staged, and the tour runs to its end."""
    as_the_user(monkeypatch)
    cfg = tmp_path_factory.mktemp("gitconfig") / "config"
    cfg.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(cfg))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for name in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("EMAIL", "guessed@example.com")
    assert main(["tour"]) == 0
    assert "Criteria: 1 criterion met · 1 criterion undetermined" in capsys.readouterr().out
    tour = workdir / "rb-tour"
    head = subprocess.run(["git", "rev-parse", "-q", "--verify", "HEAD"], cwd=tour, capture_output=True, text=True)
    assert head.returncode != 0 and head.stdout == ""
    staged = subprocess.run(["git", "diff", "--cached", "--name-only"], cwd=tour, capture_output=True, text=True)
    assert "configs/train.json" in staged.stdout.split()


def test_the_tour_declares_its_criteria_before_the_evidence_they_read(clean, capsys):
    """The tour teaches the loop in its order: declare, freeze, then run. Its criteria read as declared before their
    evidence existed, and status lists no late declaration."""
    assert main(["tour"]) == 0
    capsys.readouterr()
    st = state(clean / "rb-tour").status()
    assert {f["criterion"]: f["timing"] for f in st["findings"]} == {"c1": "before", "c2": "before"}
    assert not st["gate"]["late"] and not any(i["code"] == "late" for i in st["open"])
    [question] = st["questions"]
    assert {row["criterion"] for row in question["inquiry"]["direct"]} == {"c1", "c2"}
    assert question["inquiry"]["unmapped"] == []


def test_the_tours_closing_lines_use_only_the_words_rb_reads_in(clean, monkeypatch, capsys):
    """The closing lines name the point in the words every finding is read in. The words the reshape retired
    (established, supported, exploratory, counts for nothing) come back in neither a human's tour nor an agent's."""
    assert main(["tour"]) == 0
    human = capsys.readouterr().out.rsplit("That is the loop", 1)[1]
    monkeypatch.setenv("CLAUDECODE", "1")
    assert main(["tour", "agent-tour"]) == 0
    agent = capsys.readouterr().out.rsplit("That is the loop", 1)[1]
    for closing in (human, agent):
        assert not any(w in closing.lower() for w in ("established", "supported", "exploratory", "counts for nothing"))


def test_an_agents_tour_is_recorded_as_the_agents(clean, monkeypatch, capsys):
    """Nothing in the tour waits for anyone: run by an agent, the freeze goes through and is the agent's."""
    monkeypatch.setenv("CLAUDECODE", "1")
    assert main(["tour"]) == 0
    capsys.readouterr()
    exp = state(clean / "rb-tour").get("drop", "experiment")
    assert exp.frozen is not None and exp.frozen.by.startswith("agent:")
    assert not (clean / "rb-tour" / ".rb" / "requests").exists()


def test_the_tour_refuses_to_land_inside_existing_state(clean, capsys):
    assert main(["init", "already here", "--json"]) == 0
    capsys.readouterr()
    code = main(["tour", "--json"])
    env = Envelope.model_validate_json(capsys.readouterr().out)
    assert code != 0
    assert env.errors and env.errors[0].code == "E_PROJECT_EXISTS"


def test_json_mode_reports_every_step(clean, capsys):
    assert main(["tour", "--json"]) == 0
    env = Envelope.model_validate_json(capsys.readouterr().out)
    # 16 steps
    assert env.ok and len(env.data["steps"]) == 16
    assert all(s["exit"] == 0 for s in env.data["steps"])
    exp = state(clean / "rb-tour").get("drop", "experiment")
    assert exp.hypothesis == "Dropout 0.1 lowers val_loss" and exp.question == "q1"
    assert [n.text for n in exp.notes] == ["assumes the validation split stayed fixed"]


def test_the_tour_needs_nothing_but_the_install(clean, capsys):
    """`pip install rabbit-brain` then `rb tour`, with no extras: the tour's run configs are YAML, which rb reads
    itself. (It did not, in 0.5: the first minute failed on a clean install.)"""
    assert main(["tour"]) == 0, capsys.readouterr().err
    assert "Criteria: 1 criterion met · 1 criterion undetermined" in capsys.readouterr().out
    runs = state(clean / "rb-tour").records("run")                 # each run's YAML config was read and compared with the spec
    assert len(runs) == 3 and all(r.receipt.config.matches == ["optim.lr", "seed"] for r in runs)
