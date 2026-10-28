"""rb reconcile compiles the repository an agent left into the record, without anyone writing it again.

Each results folder is an experiment that points at its files where they are, by hash; what the files say is quoted word
for word with file and line; a manifest's hashes are checked against the code as it is now; the repository's own checks
are registered with the command it gives, and their outcome is read from a run rb watched or the file an audit wrote.
Nothing is written without --apply. Every name and number is synthetic (tests/fixtures/repo_study.py)."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from rabbit_brain.models import Envelope
from rbtest import as_the_user, home, main, state

sys.path.insert(0, str(Path(__file__).parent / "fixtures"))
import repo_study  # noqa: E402

GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false"]


@pytest.fixture
def bare(tmp_path, monkeypatch, tmp_path_factory, capsys):
    """The study as the agent left it, with no record started."""
    as_the_user(monkeypatch)
    cfg = tmp_path_factory.mktemp("gitconfig") / "config"
    cfg.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(cfg))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("RB_ACTOR", "human:t")
    root = repo_study.build(tmp_path / "study")
    monkeypatch.chdir(root)

    def rb(*argv, ok=True) -> Envelope:
        code = main([*argv, "--json"])
        env = Envelope.model_validate_json(capsys.readouterr().out)
        if ok:
            assert code in (0, 1) and env.ok, (argv, code, env.errors)
        return env

    rb.root = root
    return rb


@pytest.fixture
def study(bare):
    bare("init", "does the warmup schedule transfer?")
    return bare


def commit(root: Path, message: str = "more") -> None:
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    subprocess.run([*GIT, "-C", str(root), "commit", "-q", "-m", message], check=True)


def folder(env: Envelope, name: str) -> dict:
    return next(f for f in env.data["repository"]["folders"] if f["folder"] == f"results/{name}/")


def test_each_results_folder_is_an_experiment_pointing_at_its_files_and_nothing_is_written(study):
    env = study("reconcile")
    got = {f["folder"]: f for f in env.data["repository"]["folders"]}
    assert set(got) == {"results/warmup_confirmation/", "results/warmup_transfer/", "results/warmup_cost/"}
    assert all(f["experiment"] is None and f["changes"] == ["new: not in the record"] for f in got.values())
    roles = {x["path"]: x for x in got["results/warmup_transfer/"]["files"]}
    assert roles["results/warmup_transfer/README.md"]["role"] == "report"
    assert roles["results/warmup_transfer/manifest.json"]["role"] == "manifest"
    assert roles["results/warmup_transfer/independent_audit.json"]["role"] == "audit"
    assert roles["research/warmup/reproduction.md"]["role"] == "reproduction"      # the guide its README links
    assert roles["research/warmup/transfer_protocol.md"]["role"] == "protocol"
    data = roles["results/warmup_transfer/"]                                        # the arrays: one entry, counted
    assert (data["role"], data["files"], data["git"]) == ("data", 2, "ignored") and "sha256" not in data
    assert all(len(x.get("sha256") or "x" * 64) == 64 for x in roles.values())
    assert state().records("experiment", live=True) == []                                  # a dry run writes nothing


def test_what_the_reports_say_is_quoted_word_for_word_with_file_and_line(study):
    env = study("reconcile")
    quotes = {q["kind"]: q for q in folder(env, "warmup_transfer")["quotes"] if q["kind"] != "command"}
    assert quotes["title"]["text"] == "Transfer to the wider model" and quotes["title"]["lines"] == "1"
    assert quotes["result"]["text"] == "The warmup arm does not beat cosine_only: 8.8% more steps on the wider model."
    assert quotes["result"]["heading"] == "Result"
    assert quotes["next"]["text"].startswith("Remove warmup at recorded checkpoints")
    for q in [q for f in env.data["repository"]["folders"] for q in f["quotes"]]:
        lines = (study.root / q["path"]).read_text(encoding="utf-8").splitlines()
        lo, _, hi = q["lines"].partition("-")
        span = "\n".join(lines[int(lo) - 1:int(hi or lo)]).strip()
        assert (span.lstrip("#").strip() if q["kind"] == "title" else span) == q["text"], q      # the file's own words, at those lines


def test_apply_keeps_it_as_yours_and_a_second_apply_changes_nothing(study):
    env = study("reconcile", "--apply")
    assert env.data["applied"]["added"] == ["warmup_confirmation", "warmup_cost", "warmup_transfer"]
    rec = state()
    exp = rec.get("warmup_transfer", "experiment")
    assert exp.folder == "results/warmup_transfer/" and exp.title == "Transfer to the wider model"
    assert exp.indexed.by == "human:t" and exp.indexed.commit
    assert rec.experiment(exp.id).state_word == "open"                                     # its results are in the repository
    log = [json.loads(x) for x in (study.root / ".rb" / "log.jsonl").read_text().splitlines()]
    assert [x["op"] for x in log].count("reconcile") == 3 and log[-1]["op"] == "reconciled"
    before = (study.root / ".rb" / "log.jsonl").read_text()
    env = study("reconcile", "--apply")
    assert env.data["applied"]["added"] == [] and env.data["applied"]["updated"] == {}
    assert (study.root / ".rb" / "log.jsonl").read_text() == before                 # nothing changed, nothing written


def watched(capsys, *command: str) -> int:
    """Run a command the way an agent is told to, under rb's watch, and leave the output to the caller."""
    code = main(["journal", "run", "--", *command])
    capsys.readouterr()
    return code


def test_a_check_belongs_to_the_folder_its_command_names_and_its_outcome_is_read_from_a_watched_run(study, capsys):
    study("reconcile", "--apply")
    rec = state()
    assert [c.path for c in rec.get("warmup_transfer", "experiment").checks] == ["research/warmup/audit_transfer.py"]
    assert rec.get("warmup_confirmation", "experiment").checks == []              # the audit command names the other folder
    assert [c.path for c in rec.project.checks] == ["research/warmup/test_transfer.py"]   # run on no folder in particular
    check = rec.get("warmup_transfer", "experiment").checks[0]
    assert check.command == """python -c 'from research.warmup.audit_transfer import check; check("results/warmup_transfer")'"""
    assert check.said_at == "research/warmup/reproduction.md:33"
    read = study("show", "warmup_transfer").data["repository"]
    assert read["checks"][0]["ok"] is None and read["checks"][0]["state"] == "not run under rb's watch"
    assert watched(capsys, "python", "-c", "from research.warmup.audit_transfer import check; check('results/warmup_transfer')") == 0
    read = study("show", "warmup_transfer").data["repository"]
    assert read["checks"][0]["ok"] is True and read["checks"][0]["state"].startswith("passed at")
    assert watched(capsys, "python", "-c", "import research.warmup.audit_transfer; raise SystemExit(3)") != 0
    st = study("status").data
    item = next(i for i in st["open"] if i["code"] == "check_failed")
    assert item["subject"] == "warmup_transfer" and "failed (exit 3)" in item["what"] and st["gate"]["checks"]
    assert watched(capsys, "python", str(Path("research/warmup/audit_transfer.py")), "results/warmup_transfer") == 0
    assert study("show", "warmup_transfer").data["repository"]["checks"][0]["ok"] is True   # run by its path, as this system writes one


def test_a_manifest_is_checked_against_the_code_as_it_is_now(study):
    study("reconcile", "--apply")
    read = study("show", "warmup_confirmation").data["repository"]
    assert read["manifests"][0]["matched"] == read["manifests"][0]["sources"] == 4
    code = study.root / "research" / "warmup" / "run_confirmation.py"
    code.write_text(code.read_text() + "# a change after the freeze\n", encoding="utf-8")
    read = study("show", "warmup_confirmation").data["repository"]
    assert read["manifests"][0]["changed"] == ["research/warmup/run_confirmation.py"]
    st = study("status").data
    # both manifests froze that file, so one item, one command, names both experiments
    assert any(i["code"] == "manifest_changed" and i["subject"] == "warmup_confirmation, warmup_transfer" for i in st["open"])
    assert st["gate"]["stale"]


def test_an_audit_that_says_it_failed_is_shown_as_it_says_it(study):
    study("reconcile", "--apply")
    (study.root / "results" / "warmup_transfer" / "independent_audit.json").write_text('{"passed": false, "checks": 12}')
    st = study("status").data
    item = next(i for i in st["open"] if i["code"] == "audit_failed")
    assert "says passed: false" in item["what"] and st["gate"]["checks"]


def test_a_changed_report_is_read_again_and_the_diff_says_what_changed(study):
    study("reconcile", "--apply")
    readme = study.root / "results" / "warmup_transfer" / "README.md"
    readme.write_text(readme.read_text().replace("8.8% more steps", "9.1% more steps"), encoding="utf-8")
    commit(study.root)
    st = study("status").data
    assert any(i["code"] == "reindex" and i["subject"] == "warmup_transfer" for i in st["open"])
    assert "result changed" in folder(study("reconcile"), "warmup_transfer")["changes"]
    env = study("reconcile", "--apply")
    assert env.data["applied"]["updated"] == {"warmup_transfer": ["files: 1 changed (README.md)", "result changed"]}
    assert "1 re-indexed (warmup_transfer)" in env.data["applied"]["line"] and "1 result quoted" in env.data["applied"]["line"]
    q = next(q for q in state().get("warmup_transfer", "experiment").quotes if q.kind == "result")
    assert "9.1% more steps" in q.text
    assert not any(i["code"] == "reindex" for i in study("status").data["open"])


def test_a_new_folder_is_listed_until_it_is_compiled(study):
    study("reconcile", "--apply")
    new = study.root / "results" / "warmup_ablation"
    new.mkdir()
    (new / "README.md").write_text("# Warmup ablation\n\nWarmup removed at recorded checkpoints.\n")
    (new / "summary.json").write_text('{"cost": 3}')
    assert any(i["code"] == "unindexed_folder" and i["subject"] == "results/warmup_ablation/" for i in study("status").data["open"])
    env = study("reconcile", "--apply")
    assert env.data["applied"]["added"] == ["warmup_ablation"]
    assert not any(i["code"] == "unindexed_folder" for i in study("status").data["open"])


def test_a_folders_numbers_never_go_to_another_experiment(study):
    """The misfiling reconcile used to make: a results folder's numbers went to the only experiment there was."""
    study("metric", "add", "steps", "--minimize")
    study("experiment", "add", "an older line", "--id", "e1")
    study("criterion", "add", "steps stay low", "-e", "e1", "--metric", "steps", "--at-most", "20", "--id", "c1")
    study("journal", "scan")
    env = study("reconcile")
    assert env.data["found"] == []                          # results/*/case_*.json report steps, and none of it is e1's
    study("reconcile", "--apply")
    assert state().records("run", live=True) == []
    study("criterion", "add", "cost stays low here too", "-e", "warmup_cost", "--metric", "cost", "--at-most", "1", "--id", "c2")
    study("metric", "add", "cost", "--minimize")
    env = study("reconcile")
    # its summary was there when rb first looked: the work the record began after, listed with its own experiment's line
    [line] = [u for u in env.data["unplaced"] if "results/warmup_cost/summary.json" in u]
    assert env.data["found"] == [] and "rb run add warmup_cost --from results/warmup_cost/summary.json" in line and "rb run add e1" not in line
    (study.root / "results" / "warmup_cost" / "rerun.json").write_text(json.dumps({"widths": 4, "cost": 0.003}), encoding="utf-8")
    env = study("reconcile")
    assert [f["experiment"] for f in env.data["found"]] == ["warmup_cost"]        # its own folder, its own criterion


def test_a_folder_not_ready_to_checkpoint_says_why(study):
    study("reconcile", "--apply")
    (study.root / "results" / "warmup_cost" / "notes.txt").write_text("scratch\n")
    read = study("show", "warmup_cost").data["repository"]
    assert read["git"]["ready"] is False and read["git"]["untracked"] == ["results/warmup_cost/notes.txt"]
    commit(study.root)
    assert study("show", "warmup_cost").data["repository"]["git"]["ready"] is True


def test_an_experiment_named_after_the_folder_is_the_one_that_reads_it(study):
    study("experiment", "add", "the transfer, as the agent declared it", "--id", "warmup_transfer", "--hypothesis", "warmup transfers")
    env = study("reconcile", "--apply")
    assert "warmup_transfer" not in env.data["applied"]["added"] and "warmup_transfer" in env.data["applied"]["updated"]
    exp = state().get("warmup_transfer", "experiment")
    assert exp.folder == "results/warmup_transfer/" and exp.hypothesis == "warmup transfers"      # its own words stay


def test_an_agent_that_applies_is_recorded_as_the_agent(study, monkeypatch):
    monkeypatch.setenv("CLAUDECODE", "1")
    study("reconcile", "--apply")
    exp = state().get("warmup_confirmation", "experiment")
    assert exp.created_by.startswith("agent:") and exp.indexed.by.startswith("agent:")


def test_a_call_made_on_what_the_repository_said_moves_when_it_says_something_else(study):
    study("reconcile", "--apply")
    d = study("decide", "warmup_transfer", "park", "--why", "the transfer does not hold; look again after the ablation").data["object"]
    assert d["basis"]["warmup_transfer#repository"]["result"] == [
        "The warmup arm does not beat cosine_only: 8.8% more steps on the wider model."]
    assert not any(i["code"] == "decision_outdated" for i in study("status").data["open"])
    readme = study.root / "results" / "warmup_transfer" / "README.md"
    readme.write_text(readme.read_text().replace("does not beat cosine_only: 8.8% more", "beats cosine_only after the fix: 2.1% fewer"), encoding="utf-8")
    study("reconcile", "--apply")
    item = next(i for i in study("status").data["open"] if i["code"] == "decision_outdated")
    assert item["subject"] == "warmup_transfer" and "its repository says something else now" in item["what"]


def test_a_question_call_made_on_what_the_repository_said_moves_when_it_says_something_else(study):
    """With no question links in the record, a call on a question keeps what the repositories of the work filed under
    it said, as a call on the experiment does."""
    study("reconcile", "--apply")
    study("question", "add", "does the warmup schedule transfer?", "--id", "q1")
    study("experiment", "file", "warmup_transfer", "q1")
    d = study("decide", "q1", "park", "--why", "the transfer does not hold; look again after the ablation").data["object"]
    assert d["inquiry_basis"] is None and "warmup_transfer#repository" in d["basis"]
    assert not any(i["code"] == "decision_outdated" for i in study("status").data["open"])
    readme = study.root / "results" / "warmup_transfer" / "README.md"
    readme.write_text(readme.read_text().replace("does not beat cosine_only: 8.8% more", "beats cosine_only after the fix: 2.1% fewer"), encoding="utf-8")
    study("reconcile", "--apply")
    item = next(i for i in study("status").data["open"] if i["code"] == "decision_outdated" and i["subject"] == "q1")
    assert "warmup_transfer (its repository says something else now)" in item["what"]


def test_a_call_that_moves_a_line_on_while_its_own_audit_says_failed_is_shown_to_the_user(study, monkeypatch):
    study("reconcile", "--apply")
    (study.root / "results" / "warmup_transfer" / "independent_audit.json").write_text('{"passed": false}')
    monkeypatch.setenv("CLAUDECODE", "1")
    proposed = study("decide", "warmup_transfer", "accept", "--why", "the numbers look fine").data["object"]["id"]
    monkeypatch.delenv("CLAUDECODE")
    # proposed, it moves nothing on: it waits for the user, who confirms it, and then the call is shown with the failure
    codes = [i["code"] for i in study("status").data["open"]]
    assert "call_proposed" in codes and "call_on_failed_check" not in codes
    study("confirm", proposed)
    item = next(i for i in study("status").data["open"] if i["code"] == "call_on_failed_check")
    assert item["who"] == "human" and item["what"].startswith("warmup_transfer: accepted on 20") and "agent:" not in item["what"]
    assert "independent_audit.json says passed: false" in item["what"]


def test_a_planned_experiment_carries_the_decision_it_gates_in_its_authors_words(study):
    study("question", "add", "why does warmup help the small model but not the wider one?", "--id", "q-why")
    study("experiment", "add", "warmup ablation", "--id", "e-cf", "-q", "q-why",
          "--hypothesis", "warmup matters only while gradient noise is high, early in training",
          "--if-holds", "keep warmup short and tune it per width", "--if-not", "drop warmup from the default recipe")
    exp = state().get("e-cf", "experiment")
    assert (exp.if_holds, exp.if_not) == ("keep warmup short and tune it per width", "drop warmup from the default recipe")
    assert state().experiment(exp.id).state_word == "planned"
    text = study("context").data["markdown"]
    assert "- if it holds: keep warmup short and tune it per width" in text and "- if not: drop warmup from the default recipe" in text


def test_the_preview_says_everything_apply_would_keep_before_anything_is_written(study, capsys):
    env = study("reconcile")
    read = env.data["read"]
    assert read["warmup_confirmation"]["manifests"][0]["matched"] == 4 and read["warmup_transfer"]["git"]["ready"] is True
    assert read["warmup_transfer"]["audits"] == [{"path": "results/warmup_transfer/independent_audit.json", "passed": True, "says": "passed: true"}]
    assert main(["reconcile"]) == 0
    text = capsys.readouterr().out
    assert "all 4 source hashes match" in text and "folder clean" in text and "2 data files kept local (ignored by git)" in text
    assert state().records("experiment", live=True) == []


def test_a_source_two_manifests_froze_is_one_changed_source_in_the_research_diff(study):
    study("reconcile", "--apply")
    code = study.root / "research" / "warmup" / "run_transfer.py"
    code.write_text(code.read_text() + "# changed after both freezes\n", encoding="utf-8")
    new = study.root / "results" / "warmup_ablation"
    new.mkdir()
    (new / "README.md").write_text("# Warmup ablation\n\nWarmup removed at recorded checkpoints.\n")
    applied = study("reconcile", "--apply").data["applied"]
    assert applied["manifests"] == 2 and applied["changed_sources"] == 1
    assert "2 manifests, 1 source changed since frozen" in applied["line"]


def test_status_reads_as_the_state_of_the_research(study, monkeypatch, capsys):
    """Where the work stands, in one read: the open question, what each line tests and what its files say, the next
    experiment with the call it gates, the notes, and the calls made on the work, each with whose words they are."""
    study("reconcile", "--apply")
    monkeypatch.setenv("CLAUDECODE", "1")
    study("question", "add", "where does the saving come from?", "--id", "where", "--words", "so where is the saving coming from?")
    study("experiment", "file", "warmup_transfer", "where")
    nid = study("note", "add", "the transfer result weakens the warmup hypothesis; see results/warmup_transfer/README.md:7", "-e", "warmup_transfer").data["object"]["id"]
    study("experiment", "add", "the peak rate alone", "--id", "sched", "-q", "where", "--hypothesis", "the saving comes from the peak learning rate",
          "--if-holds", "drop the warmup arm", "--if-not", "reopen the warmup question")
    proposed = study("decide", "warmup_transfer", "continue", "--why", "the peak rate is next", "--words", "so where is the saving coming from?").data["object"]["id"]
    monkeypatch.delenv("CLAUDECODE")
    # the agent's call is proposed, not made: listed apart, with the words given with it, which are not yet the user's
    assert main(["status"]) == 0
    text = capsys.readouterr().out
    assert ("Proposed, not made (1)\n  - continue warmup_transfer · note: the peak rate is next · "
            f'words (as given): "so where is the saving coming from?" · confirm: rb confirm {proposed}') in text
    assert "call: continued" not in text and "your words" not in text
    study("confirm", proposed)
    assert main(["status"]) == 0
    text = capsys.readouterr().out
    block = text.split("where does the saving come from? (open)", 1)[1].split("\n\n", 1)[0]
    assert '  result: "The warmup arm does not beat cosine_only: 8.8% more steps on the wider model." (README.md:7)' in block
    # a session's note reads as one the user has not confirmed, naming no writer
    assert f"note {nid}: the transfer result weakens the warmup hypothesis; see results/warmup_transfer/README.md:7 · not confirmed\n" in block + "\n"
    assert "the peak rate alone · planned" in block and "hypothesis: the saving comes from the peak learning rate" in block
    assert "if it holds: drop the warmup arm · if not: reopen the warmup question" in block
    assert ('call: continued: "Transfer to the wider model" · note: the peak rate is next · '
            'your words: "so where is the saving coming from?"') in block
    eb = next(e for b in home().briefs for e in b.experiments if e.id == "sched")
    assert (eb.if_holds, eb.if_not) == ("drop the warmup arm", "reopen the warmup question")      # rb open and rb page read this


def test_what_a_results_folder_holds_is_its_evidence_never_a_loose_heartbeat(study, capsys):
    study("journal", "scan")
    env = study("reconcile")
    assert env.data["unplaced"] == [] and env.data["found"] == []       # case files count steps; they are the folder's
    assert main(["status"]) == 0
    assert "Found in the repository, not in the record" not in capsys.readouterr().out


def test_a_repository_with_no_record_is_read_and_apply_starts_the_record(bare, monkeypatch):
    (bare.root / "README.md").write_text("# Warmup schedules at scale\n\nNotes for the lab.\n", encoding="utf-8")
    commit(bare.root, "a title")
    env = bare("reconcile")
    assert env.data["record"] is None and env.data["would_start"]["title"] == "Warmup schedules at scale"
    assert [f["id"] for f in env.data["repository"]["folders"]] == ["warmup_confirmation", "warmup_cost", "warmup_transfer"]
    # the next step starts the record as the README does: rb's suggestion never adds experiments nobody declared
    assert env.data["read"]["warmup_transfer"]["manifests"][0]["matched"] == 4 and env.next == ['rb init "Warmup schedules at scale"', "rb docs --start"]
    assert not (bare.root / ".rb").exists()                                  # reading it wrote nothing
    monkeypatch.chdir(bare.root / "research")                               # from anywhere in the repository
    env = bare("reconcile", "--apply")
    assert env.data["started"] == {"root": bare.root.as_posix(), "title": "Warmup schedules at scale", "from": "README.md:1"}
    rec = state(bare.root)
    assert rec.project.title == "Warmup schedules at scale"
    assert sorted(e.id for e in rec.records("experiment", live=True)) == ["warmup_confirmation", "warmup_cost", "warmup_transfer"]


def test_a_reconcile_that_finds_nothing_names_every_folder_it_looked_in(tmp_path, monkeypatch, tmp_path_factory, capsys):
    """With no results anywhere, rb reconcile says where it looked for them, and --json lists the same folders, with a
    record or without one, and with any other folder it watches because a run named it."""
    as_the_user(monkeypatch)
    cfg = tmp_path_factory.mktemp("gitconfig") / "config"
    cfg.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(cfg))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("RB_ACTOR", "human:t")
    monkeypatch.chdir(tmp_path)
    usual = ["out/", "outputs/", "results/", "eval-out/", "metrics/"]                # where every read says rb looks

    def reconcile() -> tuple:
        assert main(["reconcile"]) == 0
        said = capsys.readouterr().out
        assert main(["reconcile", "--json"]) == 0
        return said, Envelope.model_validate_json(capsys.readouterr().out).data["looked_in"]

    said, looked = reconcile()
    assert f"no results folder ({', '.join(usual)}) holds a report or a results file yet" in said and looked == usual
    assert main(["init", "t", "--no-agents"]) == 0
    capsys.readouterr()
    said, looked = reconcile()
    assert f"Nothing to compile in the usual results folders ({', '.join(usual)})." in said and looked == usual
    Path("out").mkdir()                                   # one of them, which rb watches
    Path("logs/a").mkdir(parents=True)
    Path("logs/a/run.txt").write_text("done\n", encoding="utf-8")
    assert main(["experiment", "add", "e", "--id", "e1"]) == 0
    assert main(["run", "add", "e1", "m=1", "--artifact", "logs/a/run.txt"]) == 0
    capsys.readouterr()
    said, looked = reconcile()
    assert f"Nothing to compile in the usual results folders ({', '.join(usual)}) or logs/a/." in said and looked == [*usual, "logs/a/"]


def test_the_decision_an_experiment_gates_can_be_set_later_and_revised_with_its_history(study, monkeypatch):
    study("experiment", "add", "the peak rate alone", "--id", "peak", "--hypothesis", "the saving comes from the peak learning rate")
    study("freeze", "peak", "-m", "before any run")
    monkeypatch.setenv("CLAUDECODE", "1")
    env = study("experiment", "gate", "peak", "--if-holds", "drop the warmup arm", "--if-not", "reopen the warmup question",
                "--words", "if the peak rate explains it we drop warmup")
    assert (env.data["object"]["if_holds"], env.data["object"]["if_not"]) == ("drop the warmup arm", "reopen the warmup question")
    study("experiment", "gate", "peak", "--if-not", "run the longer ablation first", "--why", "the user changed the fallback")
    monkeypatch.delenv("CLAUDECODE")
    exp = state().get("peak", "experiment")
    assert (exp.if_holds, exp.if_not) == ("drop the warmup arm", "run the longer ablation first")
    assert not exp.amendments                                       # a gate is not what the experiment reads: no amendment
    gates = [e for e in state().log(None) if e.get("op") == "gate"]
    assert gates[0]["words"] == "if the peak rate explains it we drop warmup" and gates[0]["actor"].startswith("agent:")
    assert gates[1]["detail"]["before"] == {"if_holds": "drop the warmup arm", "if_not": "reopen the warmup question"}
    assert study("experiment", "gate", "peak", ok=False).errors[0].code == "E_USAGE"


def test_an_invalid_run_is_replaced_retracted_and_linked(study, capsys):
    study("metric", "add", "steps", "--minimize")
    study("experiment", "add", "the peak rate", "--id", "peak", "--baseline", "low", "--candidate", "high", "--varies", "lr")
    study("spec", "set", "peak", "seed", "--per-run")
    old = study("run", "add", "peak", "low.steps=44", "high.steps=42", "--set", "seed=2").data["object"]["id"]
    env = study("run", "add", "peak", "low.steps=41", "high.steps=40", "--set", "seed=2", "--replaces", old,
                "--why", "the run read the wrong split", "--words", "it read the wrong split, rerun it")
    new = env.data["object"]["id"]
    rec = state()
    assert rec.get(new, "run").replaces == old
    gone = rec.get(old, "run")
    assert gone.retracted is not None and gone.retracted.why == f"replaced by {new}: the run read the wrong split"
    assert gone.retracted.words == "it read the wrong split, rerun it"
    retraction = [e for e in rec.log(None) if e.get("op") == "retract" and e.get("id") == old][-1]
    assert retraction["detail"]["replaced_by"] == new
    assert [r.id for r in rec.records("run", live=True) if r.experiment == "peak"] == [new]       # only the replacement counts
    assert main(["show", old]) == 0 and f"replaced by {new}" in capsys.readouterr().out
    # a replacement says why, and replaces a run of its own experiment
    assert study("run", "add", "peak", "low.steps=40", "high.steps=39", "--set", "seed=3", "--replaces", new, ok=False).errors[0].code == "E_USAGE"
    assert study("run", "add", "peak", "low.steps=40", "high.steps=39", "--set", "seed=3", "--replaces", "nope", "--why", "x",
                 ok=False).errors[0].code == "E_OBJECT_NOT_FOUND"


def test_a_failing_check_shows_its_last_lines(study, capsys):
    import sys
    study("reconcile", "--apply")
    code = main(["journal", "run", "--json", "--", sys.executable, "-c",
                 "import research.warmup.audit_transfer; print('width 8 diverged'); raise SystemExit(2)"])
    capsys.readouterr()
    assert code != 0
    item = next(i for i in study("status").data["open"] if i["code"] == "check_failed" and i["subject"] == "warmup_transfer")
    assert item["last_lines"] == ["width 8 diverged"]
    assert main(["status"]) == 0
    text = capsys.readouterr().out
    assert "its last lines:" in text and "        width 8 diverged" in text
