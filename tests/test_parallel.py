"""Many branches and worktrees serve one question. Agents fork worktrees, record in each, and merge; git topology is
where the work happened, and the record is what it was for. So the record is built to merge: whatever is added to an
experiment (a variant, a note, a criterion, a run) is a file of its own, two branches never write the same file, git
merges their work without a conflict, and rb reads the merge as its own record.

Promises pinned here:

- a fresh record writes one file per variant, note and criterion, and the project's notes in .rb/notes/; an
  experiment's own file holds neither its variants nor its notes;
- three agents in three worktrees each add a variant (with its own settings), a note, a criterion and runs to one
  experiment; every branch merges into main with no conflict, and the merged record holds all of it, with nothing
  read as edited outside rb and every finding computed;
- a write claims only the files it changed: a note's log line names the note's file and not the experiment's;
- a record from before (variants and notes inside experiment.yaml, every criterion in one criteria.yaml) reads as it
  is, and the next write to an experiment moves its variants, notes and criteria to a file each, logged, with every
  finding as it was; the project's notes move with the project's next write;
- a variant edited or deleted outside rb is caught, and is an integrity problem for every finding of its experiment;
- variants are listed in the order they were added; those added together, baseline first;
- a run's code outlives its branch: squash-merged, deleted and garbage-collected, the commit is gone and the tree the
  receipt names is still here, so the experiment still reads as able to run again;
- a record both branches changed is read as rb's own when git kept both sides (merged cleanly, or resolved by hand
  keeping both), with a comment committed on top or not, carried as it stands through a later merge of a branch that
  never changed it, and with .rb/ in a folder of the repository; a hand edit after the merge, committed or not, reads
  as edited outside rb; a merge that dropped a side's change is named as one, with adoption offered first, and
  adopting it takes an author from either side but never a forged one; one that kept a side whole says it keeps what
  rb wrote there, and offers both sides' changes in the merge commit itself or the later write, which restore puts back;
- so is it in a bundle exported from the merge, which has no git to ask: rb export keeps in the bundle's .rb/merges.json
  rb's copies of the versions each merge was made from and which made which, and the bundle checks each merge again,
  for the file as exported and until rb writes it again, while its manifest names merges.json as it is, so an edit
  there reads as one, as do a revert to a merge it rested on and a kept merge whose sides rb never wrote, whose copies
  are not what their hashes name, or that does not make the file again; rb doctor names what reads as rb's own by kept
  merges, rb fmt leaves merges.json as it is, and the bundle exported again keeps them afresh; a repository that hosts
  the bundle (a site's, merging its updates or committing each onto its branch) reads it the same, every commit that
  changed its log having changed its manifest too, though a shallow clone of it, however deep, shows too little
  history to tell, and says so until the whole history is fetched; a checkout's files are git's to account for, at the
  top of its repository or below it, committed or not, cloned whole or shallow, so kept merges there vouch for no hand
  edit, and its export carries none on; a record below the top of its repository reads a clean merge as rb's own, as
  one at the top does; a record with no merge exports as it is, keeping none;
- what a command printed in a worktree is not lost with the worktree: the journal lives in the clone's shared git
  directory, by worktree, and the main checkout finds the numbers a removed worktree's command printed, ready to record;
- rb status says what is elsewhere: for each other worktree and each local branch not merged here, what it adds to
  the record (by experiment and kind) and how much of it is not committed; once merged, it is no longer elsewhere;
- two branches that mint one id for two records are told apart, never called an edit outside rb: rb doctor names the
  collision and rb doctor --restore gives the second record an id of its own;
- a shallow clone (CI's checkout of one commit) that lacks the history of the merge that made a record, or of where a
  merge that kept one side's record meets the other side, says so, and that fetching the whole history shows it, never
  restore or adopt, which leave it as it is (and a review pinned with --seen leaves it out), while the gates still
  fail, and rb export writes no bundle of it, which would have no git and read the record as edited outside rb; once
  fetched, the merge is rb's own, and its export keeps it; an edit git can see in a shallow clone reads as edited
  outside rb, as in any clone, also when a merge carries it from one side as it stands or is made during the merge,
  and restore says plainly when rb's last write is past the clone; a finding whose evidence is unread for both
  reasons names each with its own way back;
- a comparison counts a run while the arms it compares are as they were: a variant added on another branch, or an
  ablation added afterwards, leaves the other arms' runs counting; a change to a compared arm's own setting moves its
  runs to an earlier spec, named by that setting alone; a run recorded before runs kept their arms' specs is held to
  the whole spec, as it was.
"""
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import rabbit_brain
from rabbit_brain.models import Envelope
from rbtest import as_the_user, home, main, rbyaml

HAS_GIT = shutil.which("git") is not None
pytestmark = pytest.mark.skipif(not HAS_GIT, reason="branches and worktrees are git's")
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "shared-files-record"


@pytest.fixture
def repo(workdir, monkeypatch, tmp_path_factory):
    as_the_user(monkeypatch)
    cfg = tmp_path_factory.mktemp("gitconfig") / "config"
    cfg.write_text("[user]\n\temail = t@example.com\n\tname = t\n[init]\n\tdefaultBranch = main\n", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(cfg))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    git("init", "-q")
    return workdir


def git(*args, cwd=None) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


def rb(capsys, *argv) -> Envelope:
    code = main([*argv, "--json"])
    env = Envelope.model_validate_json(capsys.readouterr().out)
    assert code == 0 and env.ok, (argv, env.errors)
    return env


def as_agent(monkeypatch, session: str) -> None:
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", session)


def files(root: Path, sub: str) -> list[str]:
    d = root / ".rb" / sub
    return sorted(p.relative_to(d).as_posix() for p in d.rglob("*.yaml")) if d.exists() else []


def body(path: Path) -> dict:
    return {k: v for k, v in rbyaml.loads(path.read_text(encoding="utf-8")).items() if k not in ("rb", "kind")}


def tampered(capsys) -> list[dict]:
    return [i for i in rb(capsys, "status").data["open"] if i["code"] == "edited_outside_rb"]


def unread(capsys) -> list[tuple[str, str]]:
    """What rb status reads as not rb's own, each (subject, code): edited outside rb, or past what a shallow clone holds."""
    return [(i["subject"], i["code"]) for i in rb(capsys, "status").data["open"] if i["code"] in ("edited_outside_rb", "shallow_clone")]


def outcomes(capsys) -> dict:
    return {f["criterion"]: (f["rule_outcome"], f["result"], len(f["integrity"])) for f in rb(capsys, "status").data["findings"]}


# ---------------------------------------------------------------- the layout


def test_a_fresh_record_writes_one_file_per_variant_note_and_criterion(repo, capsys):
    rb(capsys, "init", "Does a wider model help?", "--no-agents")
    rb(capsys, "experiment", "add", "wide vs base", "--id", "e1", "--baseline", "base", "--candidate", "wide", "--note", "first")
    rb(capsys, "spec", "set", "e1", "wide.model.width", "128")
    rb(capsys, "criterion", "add", "wide beats base", "-e", "e1", "--metric", "change.top1", "--at-least", "0.01", "--id", "c1")
    rb(capsys, "note", "add", "on the project")
    assert files(repo, "experiments/e1") == ["criteria/c1.yaml", "experiment.yaml", *[f"notes/{n}" for n in files(repo, "experiments/e1/notes")],
                                             "variants/base.yaml", "variants/wide.yaml"]
    assert len(files(repo, "experiments/e1/notes")) == 1 and len(files(repo, "notes")) == 1
    exp = body(repo / ".rb" / "experiments" / "e1" / "experiment.yaml")
    assert "variants" not in exp and "notes" not in exp
    assert "notes" not in body(repo / ".rb" / "project.yaml")
    wide = body(repo / ".rb" / "experiments" / "e1" / "variants" / "wide.yaml")
    assert (wide["name"], wide["role"], wide["settings"][0]["name"], wide["settings"][0]["value"]) == ("wide", "candidate", "model.width", 128)
    shown = rb(capsys, "show", "e1").data["object"]
    assert [v["name"] for v in shown["variants"]] == ["base", "wide"] and [n["text"] for n in shown["notes"]] == ["first"]
    assert shown["notes"][0]["id"] == files(repo, "experiments/e1/notes")[0][:-len(".yaml")]
    assert tampered(capsys) == []


def test_a_write_claims_only_the_files_it_changed(repo, capsys):
    """Adding a note writes the note's file and leaves the experiment's untouched, so the log line names the note and
    carries no hash for the experiment: a branch that only added a note never claims the experiment's file."""
    rb(capsys, "init", "t", "--no-agents")
    rb(capsys, "experiment", "add", "x", "--id", "e1", "--baseline", "base")
    before = (repo / ".rb" / "experiments" / "e1" / "experiment.yaml").read_bytes()
    rb(capsys, "note", "add", "seen it", "-e", "e1")
    assert (repo / ".rb" / "experiments" / "e1" / "experiment.yaml").read_bytes() == before
    last = json.loads((repo / ".rb" / "log.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert (last["op"], last["kind"], last["id"]) == ("note", "experiment", "e1") and "sha256" not in last
    [part] = last["parts"]
    assert part["kind"] == "note" and part["path"] == f".rb/experiments/e1/notes/{part['id']}.yaml"
    assert part["sha256"] == rbyaml.content_sha(body(repo / part["path"]))


def test_variants_list_in_the_order_they_were_added(repo, capsys, monkeypatch):
    rb(capsys, "init", "t", "--no-agents")
    monkeypatch.setenv("RB_TEST_NOW", "2026-10-01T12:00:00.000+00:00")
    rb(capsys, "experiment", "add", "x", "--id", "e1", "--baseline", "zeta", "--candidate", "beta", "--candidate", "alpha")
    monkeypatch.setenv("RB_TEST_NOW", "2026-10-01T12:00:01.000+00:00")
    rb(capsys, "variant", "add", "e1", "aaa", "--role", "ablation")
    names = [v["name"] for v in rb(capsys, "show", "e1").data["object"]["variants"]]
    assert names == ["zeta", "alpha", "beta", "aaa"]


# ---------------------------------------------------------------- many worktrees, one experiment


def test_three_agents_in_three_worktrees_merge_into_one_experiment_without_a_conflict(repo, capsys, monkeypatch):
    rb(capsys, "init", "Does a wider model help?", "--no-agents")
    rb(capsys, "question", "add", "Does width help?", "--id", "q1")
    rb(capsys, "experiment", "add", "width", "--id", "e1", "-q", "q1", "--baseline", "base", "--varies", "model.width")
    rb(capsys, "spec", "set", "e1", "base.model.width", "64")
    git("add", "-A")
    git("commit", "-qm", "the record, before the agents fork")
    trees = {"wide": ("128", 0.76), "wider": ("256", 0.78), "narrow": ("32", 0.66)}
    for name, (width, top1) in trees.items():
        wt = repo.parent / f"{repo.name}-wt-{name}"
        git("worktree", "add", "-q", str(wt), "-b", f"agent/{name}", "main", cwd=repo)      # every agent forks from main
        monkeypatch.chdir(wt)
        as_agent(monkeypatch, f"session-{name}")
        rb(capsys, "variant", "add", "e1", name, "--role", "candidate" if name != "narrow" else "ablation")
        rb(capsys, "spec", "set", "e1", f"{name}.model.width", width)
        rb(capsys, "note", "add", f"{name} is worth a look", "-e", "e1")
        rb(capsys, "criterion", "add", f"{name} holds top1", "-e", "e1", "--metric", f"{name}.top1", "--at-least", "0.6")
        for jitter in (0.0, 0.01, -0.01):
            rb(capsys, "run", "add", "e1", f"base.top1={0.70 + jitter:.2f}", f"{name}.top1={top1 + jitter:.2f}")
        git("add", "-A", cwd=wt)
        git("commit", "-qm", f"agent {name}", cwd=wt)
    monkeypatch.chdir(repo)
    for name in ("CLAUDECODE", "CLAUDE_CODE_SESSION_ID"):
        monkeypatch.delenv(name)
    for name in trees:
        git("merge", "-q", "--no-edit", f"agent/{name}")      # check=True: a conflict fails the test here
    assert git("status", "--porcelain") == ""
    exp = rb(capsys, "show", "e1").data["object"]
    assert sorted(v["name"] for v in exp["variants"]) == ["base", "narrow", "wide", "wider"]
    assert sorted(n["text"] for n in exp["notes"]) == [f"{n} is worth a look" for n in sorted(trees)]
    st = rb(capsys, "status").data
    assert len(st["runs"]) == 9 and len(st["findings"]) == 3
    assert tampered(capsys) == []
    assert {f["rule_outcome"] for f in st["findings"]} == {"meets"} and all(f["integrity"] == [] for f in st["findings"])
    assert rb(capsys, "doctor").data["outcome"]["passed"] is True
    rb(capsys, "note", "add", "merged: three directions, one question", "-e", "e1")     # and the merged record takes writes
    assert tampered(capsys) == []


# ---------------------------------------------------------------- a record from before


@pytest.fixture
def before(repo):
    """A record written by the rb before parts had files, committed: e1's variants (base, wide, narrow) and its two
    notes inside experiment.yaml, its criteria c1 and c2 in criteria.yaml, a project note inside project.yaml, three
    runs."""
    shutil.copytree(FIXTURE / ".rb", repo / ".rb")
    git("add", "-A")
    git("commit", "-qm", "a record from before")
    return repo


def test_a_record_from_before_reads_as_it_is(before, capsys):
    assert tampered(capsys) == []
    assert outcomes(capsys) == {"c1": ("meets", "higher", 0), "c2": ("meets", "measured", 0)}
    exp = rb(capsys, "show", "e1").data["object"]
    assert [v["name"] for v in exp["variants"]] == ["base", "wide", "narrow"]
    assert [n["text"] for n in exp["notes"]] == ["first note", "second note"]
    assert rb(capsys, "doctor").data["outcome"]["passed"] is True


def test_the_next_write_moves_an_experiment_to_a_file_per_part_and_changes_no_finding(before, capsys):
    was = outcomes(capsys)
    rb(capsys, "note", "add", "third note", "-e", "e1")
    e1 = before / ".rb" / "experiments" / "e1"
    assert not (e1 / "criteria.yaml").exists()
    assert files(before, "experiments/e1/criteria") == ["c1.yaml", "c2.yaml"]
    assert files(before, "experiments/e1/variants") == ["base.yaml", "narrow.yaml", "wide.yaml"]
    assert len(files(before, "experiments/e1/notes")) == 3
    assert "variants" not in body(e1 / "experiment.yaml") and "notes" not in body(e1 / "experiment.yaml")
    last = json.loads((before / ".rb" / "log.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert {(p["kind"], p["id"]) for p in last["parts"]} >= {("criterion", "c1"), ("criterion", "c2"), ("variant", "e1/base")}
    assert "sha256" in last                                  # the experiment's own file changed: its parts left it
    exp = rb(capsys, "show", "e1").data["object"]
    assert [v["name"] for v in exp["variants"]] == ["base", "wide", "narrow"]
    assert [n["text"] for n in exp["notes"]] == ["first note", "second note", "third note"]
    assert outcomes(capsys) == was and tampered(capsys) == []
    assert files(before, "notes") == [] and [n["text"] for n in rb(capsys, "status").data["project"]["notes"]] == ["a project note"]
    rb(capsys, "note", "add", "another on the project")
    assert len(files(before, "notes")) == 2 and "notes" not in body(before / ".rb" / "project.yaml")
    assert tampered(capsys) == [] and rb(capsys, "doctor").data["outcome"]["passed"] is True


def test_a_project_note_from_before_ids_is_listed_without_one(before, capsys):
    """A project note an older rb wrote has no id: rb status and rb context list it as a note, never as `note None`."""
    for argv in (["status"], ["context"]):
        main(argv)
        out = capsys.readouterr().out
        assert "a project note" in out and "None" not in out, argv
    assert "  note (2026-10-01): a project note" in (main(["status"]), capsys.readouterr().out)[1]


def test_a_criterion_edited_outside_rb_is_not_moved(before, capsys):
    """The move never carries an edit made outside rb into a file of rb's own: the experiment takes no write until rb
    doctor restores or adopts the edit."""
    p = before / ".rb" / "experiments" / "e1" / "criteria.yaml"
    doc = rbyaml.loads(p.read_text(encoding="utf-8"))
    doc["criteria"][0]["target"] = 0.5
    p.write_text(rbyaml.dumps(doc), encoding="utf-8")
    code = main(["note", "add", "x", "-e", "e1", "--json"])
    env = Envelope.model_validate_json(capsys.readouterr().out)
    assert code != 0 and env.errors[0].code == "E_STATE_EDITED" and "c1" in env.errors[0].message
    assert p.exists() and not (p.parent / "criteria").exists()
    rb(capsys, "doctor", "--restore")
    rb(capsys, "note", "add", "x", "-e", "e1")
    assert files(before, "experiments/e1/criteria") == ["c1.yaml", "c2.yaml"] and tampered(capsys) == []


# ---------------------------------------------------------------- a part changed outside rb


@pytest.mark.parametrize("change", ["edit", "delete"])
def test_a_variant_changed_outside_rb_is_a_problem_for_its_experiments_findings(repo, capsys, change):
    rb(capsys, "init", "t", "--no-agents")
    rb(capsys, "experiment", "add", "x", "--id", "e1", "--baseline", "base", "--candidate", "wide")
    rb(capsys, "spec", "set", "e1", "wide.lr", "0.1")
    rb(capsys, "criterion", "add", "wide holds", "-e", "e1", "--metric", "wide.top1", "--at-least", "0.6", "--id", "c1")
    rb(capsys, "run", "add", "e1", "base.top1=0.6", "wide.top1=0.7")
    p = repo / ".rb" / "experiments" / "e1" / "variants" / "wide.yaml"
    before = p.read_text(encoding="utf-8")
    if change == "edit":
        p.write_text(before.replace("value: 0.1", "value: 0.2"), encoding="utf-8")
    else:
        p.unlink()
    [item] = tampered(capsys)
    assert "e1/wide" in item["subject"]
    [f] = rb(capsys, "status").data["findings"]
    assert any(c["code"] == "edited_outside_rb" and c["subject"] == "e1/wide" for c in f["integrity"])
    rb(capsys, "doctor", "--restore")
    assert p.read_text(encoding="utf-8") == before and tampered(capsys) == []


# ---------------------------------------------------------------- what a run is counted under


@pytest.fixture
def compared(repo, capsys):
    """wide against base, three runs, c1 reading the change and c2 reading wide alone; then an ablation added."""
    rb(capsys, "init", "t", "--no-agents")
    rb(capsys, "experiment", "add", "x", "--id", "e1", "--baseline", "base", "--candidate", "wide", "--varies", "model.width")
    rb(capsys, "spec", "set", "e1", "wide.model.width", "128")
    rb(capsys, "criterion", "add", "wide beats base", "-e", "e1", "--metric", "change.top1", "--at-least", "0.01", "--id", "c1")
    rb(capsys, "criterion", "add", "wide holds", "-e", "e1", "--metric", "wide.top1", "--at-least", "0.6", "--id", "c2")
    for j in (0, 1, 2):
        rb(capsys, "run", "add", "e1", f"base.top1=0.7{j}", f"wide.top1=0.7{j + 4}")
    return repo


def test_an_arm_added_afterwards_leaves_the_other_arms_runs_counting(compared, capsys):
    was = outcomes(capsys)
    assert was == {"c1": ("meets", "higher", 0), "c2": ("meets", "measured", 0)}
    rb(capsys, "variant", "add", "e1", "narrow", "--role", "ablation")
    rb(capsys, "spec", "set", "e1", "narrow.model.width", "32")
    assert outcomes(capsys) == was
    assert len(rb(capsys, "compare", "e1").data["rows"]) == 3 and rb(capsys, "compare", "e1").data["run_count"] == 3


def test_a_compared_arms_own_setting_changed_moves_its_runs_to_an_earlier_spec(compared, capsys):
    rb(capsys, "variant", "add", "e1", "narrow", "--role", "ablation")
    rb(capsys, "spec", "set", "e1", "narrow.model.width", "32")
    rb(capsys, "spec", "set", "e1", "wide.model.width", "256")
    f = {x["criterion"]: x for x in rb(capsys, "status").data["findings"]}
    assert f["c1"]["rule_outcome"] == "untested" and f["c2"]["rule_outcome"] == "untested"
    reasons = {o["reason"] for o in f["c2"]["observations"]}
    assert reasons == {"under an earlier spec: wide.model.width 128 -> 256"}          # narrow's setting is not named


def test_a_run_from_before_is_held_to_the_whole_spec(before, capsys):
    """Its runs kept no spec of each arm, so an arm added afterwards moves them to an earlier spec, as before."""
    rb(capsys, "variant", "add", "e1", "half", "--role", "ablation")
    f = {x["criterion"]: x for x in rb(capsys, "status").data["findings"]}
    assert {c: x["rule_outcome"] for c, x in f.items()} == {"c1": "untested", "c2": "untested"}
    assert {o["reason"] for o in f["c2"]["observations"]} == {"under an earlier spec: the variants or declared variables changed"}


# ---------------------------------------------------------------- one id, two branches


def test_two_branches_that_mint_one_id_are_told_apart(repo, capsys, monkeypatch):
    """Ids are random, and two branches can still draw the same one (forced here with a fixed seed). After the merge both
    records are rb's: the collision is named, nothing reads as edited outside rb, and restore renames the second."""
    rb(capsys, "init", "t", "--no-agents")
    rb(capsys, "experiment", "add", "x", "--id", "e1")
    rb(capsys, "experiment", "add", "y", "--id", "e2")
    git("add", "-A")
    git("commit", "-qm", "start")
    ids = []
    for name, exp, value in (("a", "e1", "0.7"), ("b", "e2", "0.6")):
        wt = repo.parent / f"{repo.name}-wt-{name}"
        git("worktree", "add", "-q", str(wt), "-b", f"agent/{name}", "main", cwd=repo)
        monkeypatch.chdir(wt)
        monkeypatch.setenv("RB_TEST_SEED", "one seed on two machines")
        ids.append(rb(capsys, "run", "add", exp, f"top1={value}").data["object"]["id"])
        git("add", "-A", cwd=wt)
        git("commit", "-qm", name, cwd=wt)
    assert ids[0] == ids[1]
    monkeypatch.chdir(repo)
    monkeypatch.delenv("RB_TEST_SEED")
    git("merge", "-q", "--no-edit", "agent/a")
    git("merge", "-q", "--no-edit", "agent/b")
    st = rb(capsys, "status").data
    assert [i["code"] for i in st["open"] if i["code"] in ("id_collision", "edited_outside_rb")] == ["id_collision"]
    assert "two branches gave the same id" in next(i["what"] for i in st["open"] if i["code"] == "id_collision")
    checks = {c["check"] for c in rb_any(capsys, "doctor").data["checks"] if not c["ok"]}
    assert checks == {"id_collision"}
    rb(capsys, "doctor", "--restore")
    runs = rb(capsys, "status").data["runs"]
    assert len(runs) == 2 and len({r["id"] for r in runs}) == 2 and ids[0] in {r["id"] for r in runs}
    assert [i for i in rb(capsys, "status").data["open"] if i["code"] in ("id_collision", "edited_outside_rb")] == []
    last = json.loads((repo / ".rb" / "log.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert last["op"] == "reid" and last["detail"]["was"] == ids[0]


def rb_any(capsys, *argv) -> Envelope:
    main([*argv, "--json"])
    return Envelope.model_validate_json(capsys.readouterr().out)


# ---------------------------------------------------------------- the code a run was recorded at


def test_a_runs_code_outlives_its_branch(repo, capsys):
    rb(capsys, "init", "t", "--no-agents")
    rb(capsys, "experiment", "add", "x", "--id", "e1")
    git("add", "-A")
    git("commit", "-qm", "start")
    git("checkout", "-qb", "agent/try")
    (repo / "train.py").write_text("lr = 0.3\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-qm", "the code the run ran")
    commit = git("rev-parse", "HEAD").strip()
    run = rb(capsys, "run", "add", "e1", "top1=0.7").data["object"]["id"]
    git("add", "-A")
    git("commit", "-qm", "the run")
    git("checkout", "-q", "main")
    git("merge", "-q", "--squash", "agent/try")
    git("commit", "-qm", "squashed")
    git("branch", "-qD", "agent/try")
    git("reflog", "expire", "--expire=now", "--all")
    git("gc", "-q", "--prune=now")
    assert subprocess.run(["git", "cat-file", "-e", f"{commit}^{{commit}}"], capture_output=True).returncode != 0   # the commit is gone
    attached = rb(capsys, "show", run).data["object"]["receipt"]["attached"]
    assert attached["commit"] == commit and git("cat-file", "-t", attached["tree"]).strip() == "tree"
    [code] = [r for r in rb(capsys, "show", "e1").data["rerunnability"] if r["what"] == "code"]
    assert code["state"] == "ok" and "not in this repository" not in code["detail"]


# ---------------------------------------------------------------- one record changed on both sides


@pytest.fixture
def forked(repo, capsys):
    """e1 with a baseline and lr, committed on main, and two branches off it, each checked out in a worktree."""
    rb(capsys, "init", "t", "--no-agents")
    rb(capsys, "experiment", "add", "x", "--id", "e1", "--baseline", "base")
    rb(capsys, "spec", "set", "e1", "lr", "0.1")
    git("add", "-A")
    git("commit", "-qm", "start")
    trees = {}
    for name in ("a", "b"):
        trees[name] = repo.parent / f"{repo.name}-wt-{name}"
        git("worktree", "add", "-q", str(trees[name]), "-b", f"agent/{name}", "main", cwd=repo)
    return trees


def on(monkeypatch, capsys, tree: Path, *argv) -> Envelope:
    monkeypatch.chdir(tree)
    env = rb(capsys, *argv)
    git("add", "-A", cwd=tree)
    git("commit", "-qm", " ".join(argv), cwd=tree)
    return env


def resolve(path: Path, pick) -> None:
    """Resolve a conflicted record file the way someone would, from the two sides git keeps in the index."""
    rel = path.relative_to(Path.cwd()).as_posix()
    ours = rbyaml.loads(git("show", f":2:{rel}"))
    theirs = rbyaml.loads(git("show", f":3:{rel}"))
    path.write_text(rbyaml.dumps(pick(ours, theirs)), encoding="utf-8")
    git("add", rel)
    git("commit", "-qm", "merged by hand")


def test_a_record_git_merges_from_both_sides_is_rbs_own(forked, capsys, monkeypatch, repo):
    on(monkeypatch, capsys, forked["a"], "experiment", "gate", "e1", "--if-holds", "ship it")
    on(monkeypatch, capsys, forked["b"], "experiment", "relate", "e1", "follows_from", "e0-elsewhere")
    monkeypatch.chdir(repo)
    git("merge", "-q", "--no-edit", "agent/a")
    git("merge", "-q", "--no-edit", "agent/b")                    # different parts of experiment.yaml: git merges it
    exp = rb(capsys, "show", "e1").data["object"]
    assert exp["if_holds"] == "ship it" and exp["relations"][0]["target"] == "e0-elsewhere"
    assert tampered(capsys) == [] and rb(capsys, "doctor").data["outcome"]["passed"] is True
    rb(capsys, "spec", "set", "e1", "seed", "3")                  # and rb writes over the merge as its own
    assert tampered(capsys) == []


def keep_both(*rels: str) -> None:
    """Resolve conflicted record files as rb doctor says to: keep both sides' changes over their base (a list keeps the
    base's items and then each side's new ones), then commit the merge."""
    def merged(base, ours, theirs):
        if isinstance(ours, dict) and isinstance(theirs, dict):
            base = base if isinstance(base, dict) else {}
            return {k: merged(base.get(k), ours.get(k), theirs.get(k)) if k in ours and k in theirs else (ours if k in ours else theirs)[k]
                    for k in [*ours, *(k for k in theirs if k not in ours)]}
        if isinstance(ours, list) and isinstance(theirs, list):
            out = list(base or [])
            out += [x for x in [*ours, *theirs] if x not in out]
            return out
        return theirs if ours == base else ours

    for rel in rels:
        sides = [rbyaml.loads(git("show", f":{stage}:{rel}")) for stage in (1, 2, 3)]
        Path(rel).write_text(rbyaml.dumps(merged(*sides)), encoding="utf-8")
        git("add", rel)
    git("commit", "-qm", "merged keeping both sides")


@pytest.mark.parametrize("repair", ["declare again", "withdraw"])
@pytest.mark.parametrize("declared", ["question link", "question link, same words", "question link, no words", "relation"])
def test_two_branches_declaring_one_link_merge_into_a_record_one_command_repairs(repo, capsys, monkeypatch, declared, repair):
    """Two branches file c1 to q1 with different interpretations (or relate c1 to c2 with different orientations) and
    are merged keeping both sides. The record reads, with neither declaration active and an integrity problem naming
    the command, and that one command, declared again or withdrawn, leaves one declaration or none."""
    rb(capsys, "init", "t", "--no-agents")
    rb(capsys, "question", "add", "Is INT8 safe to ship?", "--id", "q1")
    rb(capsys, "metric", "add", "val_loss", "--minimize")
    for n in (1, 2):
        rb(capsys, "experiment", "add", f"INT8 vs FP32, {n}", "--id", f"e{n}", "-q", "q1", "--baseline", "fp32", "--candidate", "int8")
        rb(capsys, "criterion", "add", f"INT8 adds at most 0.05, {n}", "-e", f"e{n}", "--metric", "change.val_loss", "--at-most", "0.05",
           "--id", f"c{n}", *(["-q", "q1"] if n == 2 else []))
    rb(capsys, "freeze", "e1", "-m", "before the runs")
    rb(capsys, "run", "add", "e1", "int8.val_loss=1.01", "fp32.val_loss=1.0")
    git("add", "-A")
    git("commit", "-qm", "start")
    if declared.startswith("question link"):
        command = ["criterion", "file", "c1", "q1"]
        sides = {"a": ["--when-met", "INT8 is safe to ship"], "b": ["--when-met", "INT8 is no worse than FP32"]}
        if declared.endswith("same words"):
            sides["b"] = sides["a"]
        elif declared.endswith("no words"):
            sides = {"a": [], "b": []}
        code = "question_link_ambiguous"
    else:
        command = ["criterion", "relate", "c1", "reverses", "c2"]
        sides = {"a": ["--orientation", "same"], "b": ["--orientation", "opposite"]}
        code = "relation_ambiguous"
    for name, extra in sides.items():
        tree = repo.parent / f"{repo.name}-wt-{name}"
        git("worktree", "add", "-q", str(tree), "-b", f"agent/{name}", "main")
        on(monkeypatch, capsys, tree, *command, *extra, "--amend", "--why", f"declared on {name}")
    monkeypatch.chdir(repo)
    git("merge", "-q", "--no-edit", "agent/a")
    conflicted = subprocess.run(["git", "merge", "-q", "--no-edit", "agent/b"], capture_output=True, text=True)
    assert conflicted.returncode != 0
    keep_both(*git("diff", "--name-only", "--diff-filter=U").split())
    assert rb(capsys, "doctor").data["outcome"]["passed"] is True
    status = rb(capsys, "status").data
    [problem] = [i for i in status["open"] if i["code"] == code]
    assert problem["subject"] == "c1" and problem["do"].startswith("rb " + " ".join(command)) and problem["do"].endswith("--amend")
    # words are asked for only where a declaration has them
    assert ("--when-met" in problem["do"]) == (declared.startswith("question link") and not declared.endswith("no words")), problem["do"]
    assert status["gate"]["integrity"] and not tampered(capsys)
    inquiry = next(q["inquiry"] for q in status["questions"] if q["id"] == "q1")
    if declared.startswith("question link"):
        [row] = [r for r in inquiry["direct"] if r["criterion"] == "c1"]
        assert not row["interpretation_active"] and code in [p["code"] for p in row["integrity"]]
        assert len([l for l in rabbit_brain.open().criterion("c1").data.question_links if not l.retracted]) == 2
        assert [link["why"] for link in row["links"]] == ["declared on a", "declared on b"]
        if not declared.endswith("no words"):
            # each declaration's words and reason are in view wherever the repair is chosen, with no writer
            assert main(["show", "q1"]) == 0
            shown = capsys.readouterr().out
            assert main(["context"]) == 0
            for said in (" ".join(inquiry["summary"]), row["interpretation"], shown, capsys.readouterr().out):
                for name, extra in sides.items():
                    assert f"when met: '{extra[1]}'; why: declared on {name}" in said, said
            [brief] = [b for b in home().briefs if b.id == "q1"]
            assert sorted(g["text"] for g in brief.overview["interpretations"] if "c1" in g["criteria"]) == sorted({extra[1] for extra in sides.values()})
        if not declared.endswith("no words"):
            assert main([*command, "--amend", "--why", "keep whichever", "--json"]) == 2    # whose words it keeps is said
            assert Envelope.model_validate_json(capsys.readouterr().out).errors[0].code == "E_OBJECT_INVALID"
    else:
        [reading] = inquiry["relations"]
        assert reading["outcome"] == "integrity" and code in [p["code"] for p in reading["integrity"]]
        assert len([r for r in rabbit_brain.open().criterion("c1").data.relations if not r.retracted]) == 2
        for name, extra in sides.items():
            assert f"orientation {extra[1]}, why: declared on {name}" in problem["what"], problem["what"]
        assert "human:" not in problem["what"], problem["what"]
    rb(capsys, *command, *(sides["a"] if repair == "declare again" else ["--remove"]), "--amend", "--why", "keep one declaration")
    status = rb(capsys, "status").data
    assert not [i for i in status["open"] if i["code"] in (code, "drift")]
    assert main(["status", "--fail-on", "integrity", "--json"]) == 0
    capsys.readouterr()
    c1 = rb(capsys, "show", "c1").data["object"]
    active = [x for x in c1["question_links" if declared.startswith("question link") else "relations"] if not x.get("retracted")]
    assert len(active) == (1 if repair == "declare again" else 0)


def test_two_branches_relating_a_pair_both_ways_with_other_alignments_read_neither(repo, capsys, monkeypatch):
    """Each branch declares the reversal from its own side; git merges the two criteria's files cleanly, and the
    alignments disagree. Neither reading is shown as a reversal, one item names both declarations, and withdrawing one
    repairs it."""
    rb(capsys, "init", "t", "--no-agents")
    rb(capsys, "question", "add", "Does the direction reverse?", "--id", "q1")
    rb(capsys, "metric", "add", "loss", "--minimize")
    for n in (1, 2):
        rb(capsys, "experiment", "add", f"Training {n}", "--id", f"e{n}", "--baseline", "old", "--candidate", "new")
        rb(capsys, "criterion", "add", f"Comparison {n}", "-e", f"e{n}", "--metric", "change.loss", "--at-most", "0", "--id", f"c{n}", "-q", "q1")
        rb(capsys, "analysis", "add", f"e{n}", "--compare", f"new/old:loss={'-0.2[-0.3,-0.1]' if n == 1 else '0.3[0.2,0.4]'}",
           "--method", "synthetic interval", "--unit", "seed", "--units", "3")
    git("add", "-A")
    git("commit", "-qm", "start")
    for name, (source, target, orientation) in {"a": ("c1", "c2", "same"), "b": ("c2", "c1", "opposite")}.items():
        tree = repo.parent / f"{repo.name}-wt-{name}"
        git("worktree", "add", "-q", str(tree), "-b", f"agent/{name}", "main")
        on(monkeypatch, capsys, tree, "criterion", "relate", source, "reverses", target, "--orientation", orientation, "--why", f"declared on {name}")
    monkeypatch.chdir(repo)
    for name in ("a", "b"):
        git("merge", "-q", "--no-edit", f"agent/{name}")
    status = rb(capsys, "status").data
    [problem] = [i for i in status["open"] if i["code"] == "relation_conflict"]
    assert problem["subject"] == "c1" and problem["do"].startswith("rb criterion relate c1 reverses c2 --remove")
    assert "rb criterion relate c2 reverses c1 --remove" in problem["do"] and status["gate"]["integrity"]
    readings = next(q["inquiry"] for q in status["questions"] if q["id"] == "q1")["relations"]
    assert len(readings) == 2 and all(r["outcome"] == "integrity" and "relation_conflict" in [p["code"] for p in r["integrity"]] for r in readings)
    rb(capsys, "criterion", "relate", "c2", "reverses", "c1", "--remove", "--why", "c1 declared the alignment that holds")
    status = rb(capsys, "status").data
    assert not [i for i in status["open"] if i["code"] in ("relation_conflict", "finding_relation_invalid")]
    [reading] = next(q["inquiry"] for q in status["questions"] if q["id"] == "q1")["relations"]
    assert (reading["source"], reading["outcome"], reading["integrity"]) == ("c1", "resolved_reversal", [])


def merge_three(monkeypatch, capsys, forked, repo) -> None:
    """e1 changed on three branches git merges into main: b's change over a's, then c's over that merge."""
    forked["c"] = repo.parent / f"{repo.name}-wt-c"
    git("worktree", "add", "-q", str(forked["c"]), "-b", "agent/c", "main", cwd=repo)
    on(monkeypatch, capsys, forked["a"], "experiment", "gate", "e1", "--if-holds", "ship it")
    on(monkeypatch, capsys, forked["b"], "spec", "set", "e1", "seed", "3")
    on(monkeypatch, capsys, forked["c"], "spec", "set", "e1", "lr", "0.2")
    monkeypatch.chdir(repo)
    for name in ("a", "b", "c"):
        git("merge", "-q", "--no-edit", f"agent/{name}")


def record(d: Path) -> dict:
    return {p.relative_to(d).as_posix(): p.read_bytes() for p in d.rglob("*")
            if p.is_file() and p.relative_to(d).parts[0] not in ("cache", "journal") and p.name != ".lock"}


def forge(root: Path, kind: str, id_: str, path: Path) -> str:
    """.rb/merges.json as an export writes it, vouching for a record's file as it stands by a merge of a version rb
    wrote with itself; the sha256 a bundle's manifest names it by."""
    form = body(path)
    sha = rbyaml.content_sha(form)
    rows = [json.loads(line) for line in (root / ".rb" / "log.jsonl").read_text(encoding="utf-8").splitlines()]
    writes = sum((r.get("kind"), r.get("id")) == (kind, id_) and bool(r.get("sha256")) for r in rows)
    data = json.dumps({"records": [{"kind": kind, "id": id_, "sha256": sha, "writes": writes,
                                    "merges": [{"sha256": sha, "base": None, "ours": sha, "theirs": sha}]}], "forms": {sha: form}}).encode("utf-8")
    (root / ".rb" / "merges.json").write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def manifest_naming(d: Path, kept: str) -> None:
    """A manifest in d, as a bundle's beside its record/, naming the kept merges by their sha256."""
    (d / "manifest.json").write_text(json.dumps({"format": "rb-bundle", "files": {"record/.rb/merges.json": kept}}), encoding="utf-8")



def said_edited(page: str) -> bool:
    """Whether a page says a record was edited outside rb: in its text or a finding's problem, never rb's glossary
    (the integrity term's hint names the problem in general)."""
    import re
    page = re.sub(r'data-hint="[^"]*"', "", re.sub(r'<details class="fold words".*?</details>', "", page, flags=re.S))
    return "edited outside rb" in page or "<b>edited outside rb</b>" in page

def test_a_merged_record_is_rbs_own_in_its_bundle(forked, capsys, monkeypatch, repo, tmp_path_factory):
    out = tmp_path_factory.mktemp("exports")
    rb(capsys, "criterion", "add", "base holds", "-e", "e1", "--metric", "base.top1", "--at-least", "0.6", "--id", "c1")
    git("add", "-A")
    git("commit", "-qm", "criterion")
    rb(capsys, "export", "--with-record", "--out", str(out / "unmerged"))
    assert record(out / "unmerged" / "record" / ".rb") == record(repo / ".rb")   # nothing merged: the record as it is
    merge_three(monkeypatch, capsys, forked, repo)
    assert tampered(capsys) == []
    inner = git("show", "HEAD^:.rb/experiments/e1/experiment.yaml")         # what merging b made, which c merged over
    bundle = out / "merged"
    rb(capsys, "export", "--with-record", "--out", str(bundle))
    exported = record(bundle / "record" / ".rb")
    assert exported.pop("merges.json") and exported == record(repo / ".rb")   # the record as it is, and what it rests on
    man_bytes = (bundle / "manifest.json").read_bytes()
    assert "record/.rb/merges.json" in json.loads(man_bytes)["files"]
    assert not said_edited((bundle / "page.html").read_text(encoding="utf-8"))
    rb(capsys, "clone", str(bundle), str(out / "cloned"))         # and the bundle still verifies and clones
    monkeypatch.chdir(bundle / "record")                          # no git here: what the export kept is checked again
    assert tampered(capsys) == [] and outcomes(capsys) == {"c1": ("untested", "untested", 0)}
    assert main(["status", "--fail-on", "integrity", "--json"]) == 0
    capsys.readouterr()
    checks = rb(capsys, "doctor").data["checks"]                  # and rb doctor says what it rests on
    assert [c["detail"] for c in checks if c["check"] == "kept_merges"] == [
        "experiment e1 reads as rb's own by the merge rb export kept in .rb/merges.json, checked again here, where git cannot show it"]
    exp = bundle / "record" / ".rb" / "experiments" / "e1" / "experiment.yaml"
    kept = bundle / "record" / ".rb" / "merges.json"
    merged, as_kept = exp.read_bytes(), kept.read_bytes()
    assert main(["fmt", ".rb/merges.json", "--json"]) == 1 and kept.read_bytes() == as_kept   # rb's JSON is not rewritten
    capsys.readouterr()
    kept.write_text(json.dumps(json.loads(as_kept)), encoding="utf-8")
    assert [i["subject"] for i in tampered(capsys)] == ["e1"]     # kept merges not as the manifest names them speak for nothing
    kept.write_bytes(as_kept)
    again = out / "again"
    rb(capsys, "export", "--with-record", "--out", str(again))                    # a bundle exported again keeps what it rests on afresh
    assert (again / "record" / ".rb" / "merges.json").read_bytes() == as_kept
    assert not said_edited((again / "page.html").read_text(encoding="utf-8"))
    [at_export] = json.loads(as_kept)["records"]
    assert at_export["sha256"] == rbyaml.content_sha(body(exp)) and len(at_export["merges"]) == 2
    exp.write_text(inner, encoding="utf-8")
    assert [i["subject"] for i in tampered(capsys)] == ["e1"]     # a merge it rests on is not the file as exported
    exp.write_text(merged.decode("utf-8").replace("if_holds: ship it", "if_holds: ship it now"), encoding="utf-8")
    assert [i["subject"] for i in tampered(capsys)] == ["e1"]     # an edit after the export is one
    edited = body(exp)
    sha = rbyaml.content_sha(edited)
    rows = [json.loads(line) for line in (bundle / "record" / ".rb" / "log.jsonl").read_text(encoding="utf-8").splitlines()]
    logged = next(r["sha256"] for r in rows if (r.get("kind"), r.get("id")) == ("experiment", "e1") and r.get("sha256"))
    forms = {**json.loads(as_kept)["forms"], sha: edited}
    [last, *before] = at_export["merges"]
    for merges, copies in (([{**last, "sha256": sha}, *before], forms),           # rb's sides; merging them does not make it
                           ([{"sha256": sha, "base": None, "ours": sha, "theirs": sha}], forms),   # rb never wrote its sides
                           ([{"sha256": sha, "base": None, "ours": logged, "theirs": logged}],   # rb's copy is not what
                            {**forms, logged: edited})):                                            # its hash names
        data = json.dumps({"records": [{**at_export, "sha256": sha, "merges": merges}], "forms": copies}).encode("utf-8")
        kept.write_bytes(data)
        manifest = json.loads(man_bytes)                          # named by the manifest, as a forger would have it
        manifest["files"]["record/.rb/merges.json"] = hashlib.sha256(data).hexdigest()
        (bundle / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        assert [i["subject"] for i in tampered(capsys)] == ["e1"]
    kept.write_bytes(as_kept)
    (bundle / "manifest.json").write_bytes(man_bytes)
    exp.write_bytes(merged)
    assert tampered(capsys) == []                                 # as exported, it is rb's own again
    rb(capsys, "experiment", "gate", "e1", "--if-holds", "ship it later")
    assert tampered(capsys) == []                                 # rb writes over it as its own
    exp.write_bytes(merged)
    assert [i["subject"] for i in tampered(capsys)] == ["e1"]     # and what was merged speaks no more once rb wrote it since


def test_kept_merges_vouch_for_no_edit_in_a_checkout(repo, capsys, monkeypatch, tmp_path_factory):
    """A checkout's files are git's to account for, the ones rb wrote since the last commit too: kept merges there (in
    .rb/ or rb's cache, with a manifest beside the record or not, committed or not) never make a hand edit rb's own, and
    rb export carries none of them into its bundle. Out of git, kept merges speak only in a bundle, and a bundle has
    only its manifest to say what it holds."""
    top = tmp_path_factory.mktemp("checkout") / "record"          # named as a bundle's record is
    top.mkdir()
    git("init", "-q", cwd=top)
    monkeypatch.chdir(top)
    rb(capsys, "init", "t", "--no-agents")
    rb(capsys, "experiment", "add", "x", "--id", "e1", "--baseline", "base")
    rb(capsys, "criterion", "add", "base holds", "-e", "e1", "--metric", "base.top1", "--at-least", "0.6", "--id", "c1")
    run_id = rb(capsys, "run", "add", "e1", "base.top1=0.9").data["object"]["id"]
    git("add", "-A")
    git("commit", "-qm", "run")
    path = top / ".rb" / "experiments" / "e1" / "runs" / f"{run_id}.yaml"
    before = path.read_bytes()
    rb(capsys, "retract", run_id, "--why", "wrong split")        # not committed: git has no history of this write yet
    path.write_bytes(before)                                       # the retraction taken back by hand
    kept = forge(top, "run", run_id, path)
    (top / ".rb" / "cache").mkdir(exist_ok=True)
    (top / ".rb" / "cache" / "merges.json").write_bytes((top / ".rb" / "merges.json").read_bytes())
    manifest_naming(top.parent, kept)
    assert [i["subject"] for i in tampered(capsys)] == [run_id]
    out = tmp_path_factory.mktemp("exports") / "b"
    rb(capsys, "export", "--with-record", "--out", str(out))
    assert not (out / "record" / ".rb" / "merges.json").exists() and "edited outside rb" in (out / "page.html").read_text(encoding="utf-8")
    monkeypatch.chdir(out / "record")
    assert [i["subject"] for i in tampered(capsys)] == [run_id]   # nor in the bundle
    monkeypatch.chdir(top)
    git("add", "-A")
    git("commit", "-qm", "taken back")
    assert [i["subject"] for i in tampered(capsys)] == [run_id]
    elsewhere = tmp_path_factory.mktemp("copied") / "record"
    shutil.copytree(top, elsewhere, ignore=shutil.ignore_patterns(".git"))
    monkeypatch.chdir(elsewhere)
    assert [i["subject"] for i in tampered(capsys)] == [run_id]   # out of git, a record whose kept merges no manifest names
    manifest_naming(elsewhere.parent, kept)
    assert tampered(capsys) == []                                  # and a bundle's, by what its manifest says


def test_kept_merges_vouch_for_no_edit_below_the_top_of_a_checkout(repo, capsys, monkeypatch, tmp_path_factory):
    """A record kept below the top of its repository (a monorepo's), named as a bundle's record is, with a manifest of
    the repository's beside it that names forged kept merges: its history holds commits that wrote its log alone, so it
    is a checkout, and kept merges vouch for no hand edit there, committed or not, nor in a clone of it, shallow or not:
    a shallow clone says it cannot see how e1 got there, and the whole history, fetched, shows the edit."""
    rec = repo / "record"
    rec.mkdir()
    monkeypatch.chdir(rec)
    rb(capsys, "init", "t", "--no-agents")
    rb(capsys, "experiment", "add", "x", "--id", "e1", "--baseline", "base")
    rb(capsys, "spec", "set", "e1", "lr", "0.1")
    exp = rec / ".rb" / "experiments" / "e1" / "experiment.yaml"
    before = exp.read_bytes()
    git("add", "-A")
    git("commit", "-qm", "start")
    rb(capsys, "spec", "set", "e1", "lr", "0.2")
    git("add", "-A")
    git("commit", "-qm", "lr")
    exp.write_bytes(before)                                        # rb's change taken back by hand
    kept = forge(rec, "experiment", "e1", exp)
    (repo / "manifest.json").write_text("{}", encoding="utf-8")
    git("add", "manifest.json", cwd=repo)
    git("commit", "-qm", "a manifest", cwd=repo)
    manifest_naming(repo, kept)
    assert [i["subject"] for i in tampered(capsys)] == ["e1"]
    git("add", "-A")
    git("commit", "-qm", "taken back")
    assert [i["subject"] for i in tampered(capsys)] == ["e1"]
    for depth, code in (([], "edited_outside_rb"), (["--depth", "1"], "shallow_clone")):
        clone = tmp_path_factory.mktemp("ci") / "mono"
        git("clone", "-q", *depth, repo.as_uri(), str(clone))
        monkeypatch.chdir(clone / "record")
        assert unread(capsys) == [("e1", code)]
        assert main(["status", "--fail-on", "integrity", "--json"]) == 1
        capsys.readouterr()
    git("fetch", "-q", "--unshallow")
    assert unread(capsys) == [("e1", "edited_outside_rb")]
    copied = tmp_path_factory.mktemp("copied")                     # the same files out of git: a bundle's, by its manifest
    shutil.copytree(rec, copied / "record")
    shutil.copy(repo / "manifest.json", copied / "manifest.json")
    monkeypatch.chdir(copied / "record")
    assert tampered(capsys) == []


def test_a_record_below_the_top_of_its_repository_merges_as_rbs_own(repo, capsys, monkeypatch, tmp_path_factory):
    """git reads a commit's files from the top of the repository: a record kept below it reads a clean merge of what rb
    wrote on two branches as rb's own, and its bundle keeps that merge."""
    lab = repo / "lab"
    lab.mkdir()
    monkeypatch.chdir(lab)
    rb(capsys, "init", "t", "--no-agents")
    rb(capsys, "experiment", "add", "x", "--id", "e1", "--baseline", "base")
    rb(capsys, "spec", "set", "e1", "lr", "0.1")
    git("add", "-A")
    git("commit", "-qm", "start")
    for name, argv in (("a", ("experiment", "gate", "e1", "--if-holds", "ship it")), ("b", ("spec", "set", "e1", "seed", "3"))):
        tree = repo.parent / f"{repo.name}-wt-{name}"
        git("worktree", "add", "-q", str(tree), "-b", f"agent/{name}", "main", cwd=repo)
        on(monkeypatch, capsys, tree / "lab", *argv)
    monkeypatch.chdir(lab)
    for name in ("a", "b"):
        git("merge", "-q", "--no-edit", f"agent/{name}")
    assert tampered(capsys) == []
    bundle = tmp_path_factory.mktemp("exports") / "lab"
    rb(capsys, "export", "--with-record", "--out", str(bundle))
    assert (bundle / "record" / ".rb" / "merges.json").is_file()
    monkeypatch.chdir(bundle / "record")
    assert tampered(capsys) == []


def test_a_bundle_a_repository_hosts_reads_its_merges_as_rbs_own(forked, capsys, monkeypatch, repo, tmp_path_factory):
    """A site's repository holds the bundle, not the record's history: an update it merges in (a pull request) reads as
    the bundle did, by the kept merges its manifest names, and a clone of the site still verifies against it. A shallow
    clone shows too little of the history to tell a bundle so hosted from a checkout, and says the clone is shallow; the
    whole history, fetched, shows the bundle, and its kept merges speak again."""
    site = tmp_path_factory.mktemp("site")
    git("init", "-q", cwd=site)
    rb(capsys, "export", "--with-record", "--out", str(site / "docs"))
    git("add", "-A", cwd=site)
    git("commit", "-qm", "publish", cwd=site)
    git("checkout", "-qb", "update", cwd=site)
    merge_three(monkeypatch, capsys, forked, repo)
    rb(capsys, "export", "--with-record", "--out", str(site / "docs"), "--url", "https://example.org/site")
    git("add", "-A", cwd=site)
    git("commit", "-qm", "publish the merge", cwd=site)
    git("checkout", "-q", "main", cwd=site)
    git("merge", "-q", "--no-ff", "--no-edit", "update", cwd=site)   # what last changed e1 there is the site's own merge
    monkeypatch.chdir(site / "docs" / "record")
    assert tampered(capsys) == []
    hosted = tmp_path_factory.mktemp("hosted") / "site"
    git("clone", "-q", str(site), str(hosted))
    rb(capsys, "clone", str(hosted / "docs"), str(tmp_path_factory.mktemp("cloned") / "t"))
    monkeypatch.chdir(hosted / "docs" / "record")
    assert tampered(capsys) == []
    kept = hosted / "docs" / "record" / ".rb" / "merges.json"
    kept.write_text(json.dumps(json.loads(kept.read_text(encoding="utf-8"))), encoding="utf-8")
    assert [i["subject"] for i in tampered(capsys)] == ["e1"]     # kept merges not as its manifest names them speak for nothing
    shallow = tmp_path_factory.mktemp("shallow") / "site"
    git("clone", "-q", "--depth", "1", site.as_uri(), str(shallow))
    monkeypatch.chdir(shallow / "docs" / "record")
    assert unread(capsys) == [("e1", "shallow_clone")]
    git("fetch", "-q", "--unshallow")
    assert unread(capsys) == []


def test_a_bundle_a_site_commits_in_a_line_reads_as_shallow_in_any_shallow_clone(forked, capsys, monkeypatch, repo, tmp_path_factory):
    """A site that commits each export straight onto its branch, after commits of its own: a shallow clone of it, even
    one deep enough to show the commit before the one that published the merge, shows too little history to tell the
    bundle from a checkout, so e1, which its kept merges make rb's own, reads as shallow_clone, never as edited outside
    rb, and rb doctor --restore leaves it as it is; the whole history, fetched, shows the bundle."""
    site = tmp_path_factory.mktemp("site")
    git("init", "-q", cwd=site)
    (site / "index.md").write_text("the site\n", encoding="utf-8")
    git("add", "-A", cwd=site)
    git("commit", "-qm", "the site", cwd=site)
    rb(capsys, "export", "--with-record", "--out", str(site / "docs"))
    git("add", "-A", cwd=site)
    git("commit", "-qm", "publish", cwd=site)
    merge_three(monkeypatch, capsys, forked, repo)
    rb(capsys, "export", "--with-record", "--out", str(site / "docs"))
    git("add", "-A", cwd=site)
    git("commit", "-qm", "publish the merge", cwd=site)
    monkeypatch.chdir(site / "docs" / "record")
    assert tampered(capsys) == []
    for depth in ("1", "2"):                                       # 2 holds the publish before the merge's, with its parent cut
        clone = tmp_path_factory.mktemp("shallow") / "site"
        git("clone", "-q", "--depth", depth, site.as_uri(), str(clone))
        monkeypatch.chdir(clone / "docs" / "record")
        assert git("rev-parse", "--is-shallow-repository").strip() == "true"
        assert unread(capsys) == [("e1", "shallow_clone")]
        [check] = [c for c in rb_any(capsys, "doctor").data["checks"] if not c["ok"]]
        assert check["check"] == "shallow_clone" and check["fix"].startswith("git fetch --unshallow")
        exp = clone / "docs" / "record" / ".rb" / "experiments" / "e1" / "experiment.yaml"
        before = exp.read_bytes()
        [left] = rb_any(capsys, "doctor", "--restore").data["restored"]
        assert left["done"].startswith("left as it is: this clone is shallow") and exp.read_bytes() == before
    git("fetch", "-q", "--unshallow")
    assert unread(capsys) == []


def test_both_sides_kept_by_hand_is_rbs_own(forked, capsys, monkeypatch, repo):
    on(monkeypatch, capsys, forked["a"], "spec", "set", "e1", "momentum", "0.9")
    on(monkeypatch, capsys, forked["b"], "spec", "set", "e1", "warmup", "500")
    monkeypatch.chdir(repo)
    git("merge", "-q", "--no-edit", "agent/a")
    assert subprocess.run(["git", "merge", "-q", "--no-edit", "agent/b"], capture_output=True).returncode != 0   # both appended a setting
    path = repo / ".rb" / "experiments" / "e1" / "experiment.yaml"

    def both(ours, theirs):
        return {**ours, "settings": [*theirs["settings"], *(s for s in ours["settings"] if s["name"] == "momentum")]}
    resolve(path, both)
    names = sorted(s["name"] for s in rb(capsys, "show", "e1").data["object"]["settings"])
    assert names == ["lr", "momentum", "warmup"] and tampered(capsys) == []


def test_a_merge_that_drops_a_side_is_named_and_adopted_on_either_sides_authority(forked, capsys, monkeypatch, repo):
    on(monkeypatch, capsys, forked["a"], "spec", "set", "e1", "lr", "0.2")
    on(monkeypatch, capsys, forked["b"], "spec", "set", "e1", "lr", "0.3")
    monkeypatch.chdir(repo)
    git("merge", "-q", "--no-edit", "agent/a")
    assert subprocess.run(["git", "merge", "-q", "--no-edit", "agent/b"], capture_output=True).returncode != 0
    path = repo / ".rb" / "experiments" / "e1" / "experiment.yaml"
    resolve(path, lambda ours, theirs: ours)                       # keeps 0.2: b's change is gone
    st = rb(capsys, "status").data
    [item] = [i for i in st["open"] if i["code"] in ("merged_outside_rb", "edited_outside_rb")]
    merged = git("rev-parse", "agent/b")[:12]                      # the side merged in, by its commit: never its branch's name
    assert item["code"] == "merged_outside_rb" and f"merge of commit {merged}" in item["what"] and "agent/b" not in item["what"]
    assert item["do"].startswith("rb doctor --adopt")
    assert {c["check"] for c in rb_any(capsys, "doctor").data["checks"] if not c["ok"]} == {"merged_outside_rb"}
    rb(capsys, "doctor", "--adopt", "--why", "0.2 is the one we ran")
    assert tampered(capsys) == [] and rb(capsys, "show", "e1/lr").data["object"]["value"] == 0.2


def test_a_conflict_resolved_by_keeping_one_side_whole_says_it_keeps_that_side(forked, capsys, monkeypatch, repo):
    """Both sides set lr, and the conflict in experiment.yaml is resolved by keeping the earlier side whole: the file is
    what rb wrote on that side, so rb says it keeps one side, never that it is what rb wrote on neither, and rb doctor
    offers what rb reads as its own: both sides' changes in the merge commit itself (a commit on top of it is an edit),
    or the later write, which rb doctor --restore puts back."""
    on(monkeypatch, capsys, forked["a"], "spec", "set", "e1", "lr", "0.2")
    on(monkeypatch, capsys, forked["b"], "spec", "set", "e1", "lr", "0.3")      # the later write
    monkeypatch.chdir(repo)
    git("merge", "-q", "--no-edit", "agent/a")
    assert subprocess.run(["git", "merge", "-q", "--no-edit", "agent/b"], capture_output=True).returncode != 0
    exp = ".rb/experiments/e1/experiment.yaml"
    git("checkout", "--ours", exp)                                 # a's experiment.yaml, whole
    git("add", "-A")
    git("commit", "-qm", "kept a's side")
    merged = git("rev-parse", "agent/b")[:12]
    said = f"experiment e1 keeps one side of the merge of commit {merged}: it is what rb wrote there, without the other side's change"
    [check] = [c for c in rb_any(capsys, "doctor").data["checks"] if not c["ok"]]
    assert check["check"] == "merged_outside_rb" and check["detail"] == f"{exp}: {said}"
    assert "keep both sides' changes, which rb reads as its own in the merge commit itself (git commit --amend once it is made), or the later write, which rb doctor --restore puts back" in check["fix"]
    [item] = [i for i in rb(capsys, "status").data["open"] if i["code"] in ("merged_outside_rb", "edited_outside_rb")]
    assert item["code"] == "merged_outside_rb" and item["what"].endswith(said) and item["do"].startswith("rb doctor --adopt")
    assert "the later write, which rb doctor --restore puts back" in item["do"]
    [restored] = rb(capsys, "doctor", "--restore").data["restored"]          # b's write, from b's side of the history
    assert restored["done"] == "restored to what rb last wrote"
    assert tampered(capsys) == [] and rb(capsys, "show", "e1/lr").data["object"]["value"] == 0.3


def test_a_merge_cannot_forge_an_author(forked, capsys, monkeypatch, repo):
    on(monkeypatch, capsys, forked["a"], "spec", "set", "e1", "lr", "0.2")
    on(monkeypatch, capsys, forked["b"], "spec", "set", "e1", "lr", "0.3")
    monkeypatch.chdir(repo)
    git("merge", "-q", "--no-edit", "agent/a")
    assert subprocess.run(["git", "merge", "-q", "--no-edit", "agent/b"], capture_output=True).returncode != 0
    path = repo / ".rb" / "experiments" / "e1" / "experiment.yaml"

    def forged(ours, theirs):
        settings = [{**s, "set_by": "human:someone-else"} if s["name"] == "lr" else s for s in ours["settings"]]
        return {**ours, "settings": settings}
    resolve(path, forged)
    code = main(["doctor", "--adopt", "--why", "keep it", "--json"])
    env = Envelope.model_validate_json(capsys.readouterr().out)
    assert code != 0 and env.errors[0].code == "E_ADOPT_AUTHORITY" and any("set_by" in p for p in env.errors[0].problems)
    # rb's words name the field, never who wrote it on either side or who it says now
    assert "e1.settings[lr].set_by: not as rb wrote it on either side" in env.errors[0].problems, env.errors[0].problems
    assert not [p for p in env.errors[0].problems if "human:" in p or "agent:" in p], env.errors[0].problems


# ---------------------------------------------------------------- a shallow clone


def commit_all(message: str) -> None:
    git("add", "-A")
    git("commit", "-qm", message)


def merge_e1(capsys, at: Path) -> None:
    """In the record at `at` (the current directory), e1 changed on two branches and merged cleanly: side gives it its
    gate, main a setting read from a config; then a commit that touches no record. The whole clone reads it as rb's own."""
    (at / "configs").mkdir()
    (at / "configs" / "train.json").write_text('{"optim": {"lr": 0.001}}', encoding="utf-8")
    rb(capsys, "init", "t", "--no-agents")
    rb(capsys, "experiment", "add", "x", "--id", "e1", "--baseline", "base", "--candidate", "wide", "--varies", "model.width")
    rb(capsys, "criterion", "add", "wide holds", "-e", "e1", "--metric", "wide.top1", "--at-least", "0.6", "--id", "c1")
    commit_all("start")
    git("checkout", "-qb", "side")
    rb(capsys, "experiment", "gate", "e1", "--if-holds", "x", "--if-not", "y")
    commit_all("gate")
    git("checkout", "-q", "main")
    rb(capsys, "spec", "set", "e1", "optim.lr", "--source", "configs/train.json#optim.lr")
    commit_all("lr")
    git("merge", "-q", "--no-edit", "side")                          # check=True: a conflict fails the test here
    (at / "README.md").write_text("t\n", encoding="utf-8")
    commit_all("readme")
    assert tampered(capsys) == [] and rb(capsys, "doctor").data["outcome"]["passed"] is True


@pytest.fixture
def merged(repo, capsys):
    merge_e1(capsys, repo)
    return repo


def shallow(repo: Path, depth: int = 1) -> Path:
    """A clone holding only the last `depth` commits of `repo`, as actions/checkout makes one (depth 1 by default)."""
    dest = repo.parent / f"{repo.name}-depth-{depth}"
    git("clone", "-q", "--depth", str(depth), repo.as_uri(), str(dest))
    return dest


def failing(capsys) -> list[tuple[str, str]]:
    """rb doctor's failing checks, each (check, the file it names)."""
    return [(c["check"], c["detail"].split(":")[0]) for c in rb_any(capsys, "doctor").data["checks"] if not c["ok"]]


E1 = ".rb/experiments/e1/experiment.yaml"
C1 = ".rb/experiments/e1/criteria/c1.yaml"


def by_hand(path: Path, old: str = "title: x\n", new: str = "title: by hand\n") -> None:
    text = path.read_text(encoding="utf-8")
    assert old in text
    path.write_text(text.replace(old, new), encoding="utf-8")


def test_a_shallow_clone_says_it_cannot_see_the_merge_that_made_a_record(merged, capsys, monkeypatch):
    """CI checks out one commit: the merge that made e1 is past what the clone holds, so rb cannot tell it from an edit.
    It says so, and that fetching the whole history shows it; never restore (which would drop one side's write) or
    adopt. The finding still lists it and the gates still fail: rb cannot vouch for e1."""
    clone = shallow(merged)
    monkeypatch.chdir(clone)
    assert git("rev-parse", "--is-shallow-repository").strip() == "true"
    doctor = rb_any(capsys, "doctor")
    [check] = [c for c in doctor.data["checks"] if not c["ok"]]
    assert check["check"] == "shallow_clone" and check["detail"].startswith(f"{E1}: experiment e1 is not what rb last wrote, and this clone is shallow")
    assert "a merge may have made it" in check["detail"]
    assert check["fix"].startswith("git fetch --unshallow fetches the whole history") and "fetch-depth: 0" in check["fix"]
    st = rb_any(capsys, "status", "--fail-on", "unestablished")
    [item] = [i for i in st.data["open"] if i["subject"] == "e1"]
    assert item["code"] == "shallow_clone" and item["do"].startswith("git fetch --unshallow")
    [f] = st.data["findings"]
    [problem] = f["integrity"]
    assert problem["code"] == "shallow_clone" and problem["subject"] == "e1" and "git fetch --unshallow" in problem["text"]
    assert st.data["outcome"]["passed"] is False
    code = main(["status", "--fail-on", "unmet", "--json"])
    assert code == 1 and Envelope.model_validate_json(capsys.readouterr().out).data["outcome"]["failures"][0]["code"] == "shallow_clone"
    main(["status"])
    text = capsys.readouterr().out
    for said in (json.dumps(doctor.data), json.dumps(st.data), text):
        assert "this clone is shallow" in said and "git fetch --unshallow" in said
        assert not any(w in said for w in ("--restore", "--adopt", "restored or adopted", "edited outside rb")), said
    code = main(["spec", "set", "e1", "seed", "3", "--json"])          # rb will not write over what it cannot vouch for
    err = Envelope.model_validate_json(capsys.readouterr().out).errors[0]
    assert code == 2 and err.code == "E_STATE_EDITED" and "this clone is shallow" in err.message
    assert err.fix.startswith("git fetch --unshallow") and "restore" not in err.fix
    e1, log = clone / E1, clone / ".rb" / "log.jsonl"
    before = (e1.read_bytes(), log.read_bytes())
    [restored] = rb_any(capsys, "doctor", "--restore").data["restored"]
    [adopted] = rb_any(capsys, "doctor", "--adopt", "--why", "keep it").data["adopted"]
    assert restored["done"].startswith("left as it is: this clone is shallow") and adopted["done"].startswith("left as it is: this clone is shallow")
    for argv in (["--restore"], ["--adopt", "--why", "keep it"]):
        main(["doctor", *argv])
        said = [line for line in capsys.readouterr().out.splitlines() if E1 in line]
        assert said[0].startswith(f"left {E1} as it is: this clone is shallow") and not any(" not restored" in s or " not adopted" in s for s in said), said
    assert (e1.read_bytes(), log.read_bytes()) == before


def test_the_whole_history_fetched_shows_the_merge_as_rbs_own(merged, capsys, monkeypatch):
    monkeypatch.chdir(shallow(merged))
    assert failing(capsys) == [("shallow_clone", E1)]
    git("fetch", "-q", "--unshallow")
    assert failing(capsys) == []
    st = rb(capsys, "status", "--fail-on", "integrity").data
    assert [i for i in st["open"] if i["subject"] == "e1"] == [] and st["findings"][0]["integrity"] == []
    rb(capsys, "spec", "set", "e1", "seed", "3")                       # and rb writes over the merge as its own
    assert failing(capsys) == []


def test_an_export_waits_for_the_history_a_shallow_clone_lacks(merged, capsys, monkeypatch, tmp_path_factory):
    """A bundle has no git to show how a record got there: from a clone too shallow to show the merge that made e1,
    rb export --with-record writes nothing and says to fetch the whole history, rather than a bundle that would read e1
    as edited outside rb and offer to put back one side's write, while the page alone is written; fetched, the export
    keeps the merge, and the bundle reads e1 as rb's own."""
    monkeypatch.chdir(shallow(merged))
    out = tmp_path_factory.mktemp("exports") / "from-ci"
    code = main(["export", "--with-record", "--out", str(out), "--json"])
    err = Envelope.model_validate_json(capsys.readouterr().out).errors[0]
    assert code == 2 and err.code == "E_STATE_EDITED" and not out.exists()
    assert err.message.startswith("experiment e1 is not what rb last wrote, and this clone is shallow")
    assert err.message.endswith("a bundle, which has no git, would read it as edited outside rb.")
    assert err.fix.startswith("git fetch --unshallow fetches the whole history") and "fetch-depth: 0" in err.fix
    page = tmp_path_factory.mktemp("exports") / "page"                 # the page alone carries no record to read there
    assert main(["export", "--out", str(page), "--json"]) == 0 and (page / "page.html").is_file()
    capsys.readouterr()
    git("fetch", "-q", "--unshallow")
    rb(capsys, "export", "--with-record", "--out", str(out))
    assert (out / "record" / ".rb" / "merges.json").is_file()
    monkeypatch.chdir(out / "record")
    assert unread(capsys) == []


@pytest.mark.parametrize("forked", ["before any record", "before e1", "after e1"])
def test_a_merge_that_changes_nothing_of_a_merged_record_leaves_it_rbs_own(repo, capsys, monkeypatch, forked):
    """e1 merged cleanly from two sides; then main merges a branch that never changed it, forked before .rb/ existed,
    before e1 (adding e2 through rb), or after e1. That merge holds e1 as main had it: the merge before it made e1, and e1
    is rb's own, in a whole clone and in a shallow one once it fetches the whole history."""
    (repo / "configs").mkdir()
    (repo / "configs" / "train.json").write_text('{"optim": {"lr": 0.001}}', encoding="utf-8")
    if forked == "before any record":
        commit_all("configs")
        git("branch", "old")
    rb(capsys, "init", "t", "--no-agents")
    if forked == "before e1":
        commit_all("init")
        git("branch", "old")
    rb(capsys, "experiment", "add", "x", "--id", "e1", "--baseline", "base", "--candidate", "wide", "--varies", "model.width")
    commit_all("e1")
    if forked == "after e1":
        git("branch", "old")
    git("checkout", "-qb", "side")
    rb(capsys, "experiment", "gate", "e1", "--if-holds", "x", "--if-not", "y")
    commit_all("gate")
    git("checkout", "-q", "main")
    rb(capsys, "spec", "set", "e1", "optim.lr", "--source", "configs/train.json#optim.lr")
    commit_all("lr")
    git("merge", "-q", "--no-edit", "side")
    git("checkout", "-q", "old")
    if forked == "before e1":
        rb(capsys, "experiment", "add", "y", "--id", "e2", "--baseline", "base")
    else:
        (repo / "old.txt").write_text("o\n", encoding="utf-8")
    commit_all("old")
    git("checkout", "-q", "main")
    git("merge", "-q", "--no-edit", "old")
    assert failing(capsys) == [] and tampered(capsys) == []
    monkeypatch.chdir(shallow(repo))
    assert failing(capsys) == [("shallow_clone", E1)]
    git("fetch", "-q", "--unshallow")
    assert failing(capsys) == []


def test_history_cut_between_a_merge_and_where_its_sides_met_is_a_shallow_clone_too(repo, capsys, monkeypatch):
    """side changes a value main kept, so the merge is rb's own only against the commit both sides started from. With the
    merge in the clone and that commit not (depth 3), rb cannot tell which side changed the value, and says so as it
    does when the merge itself is past the clone (depth 1 and 2); with that commit in the clone, the merge is rb's own."""
    rb(capsys, "init", "t", "--no-agents")
    rb(capsys, "experiment", "add", "x", "--id", "e1", "--baseline", "base", "--varies", "model.width")
    rb(capsys, "spec", "set", "e1", "lr", "0.1")
    commit_all("start")
    git("checkout", "-qb", "side")
    rb(capsys, "spec", "set", "e1", "lr", "0.2")
    commit_all("lr")
    git("checkout", "-q", "main")
    rb(capsys, "experiment", "gate", "e1", "--if-holds", "x")
    commit_all("gate")
    git("merge", "-q", "--no-edit", "side")
    (repo / "README.md").write_text("t\n", encoding="utf-8")
    commit_all("readme")
    assert failing(capsys) == []
    for depth in (1, 2, 3):
        monkeypatch.chdir(shallow(repo, depth))
        assert failing(capsys) == [("shallow_clone", E1)], depth
    monkeypatch.chdir(shallow(repo, 4))
    assert failing(capsys) == []


def test_a_merge_whose_side_is_a_merge_past_the_clone_is_a_shallow_clone_too(repo, capsys, monkeypatch):
    """main merges q, which had merged p: with q's merge on the clone's boundary (depth 3), rb cannot see that it kept
    both sides; one commit deeper it can."""
    rb(capsys, "init", "t", "--no-agents")
    rb(capsys, "experiment", "add", "x", "--id", "e1", "--baseline", "base", "--varies", "model.width")
    rb(capsys, "spec", "set", "e1", "lr", "0.1")                      # keeps the three sides' changes apart in its file
    commit_all("start")
    git("checkout", "-qb", "p")
    rb(capsys, "experiment", "gate", "e1", "--if-holds", "x")
    commit_all("gate")
    git("checkout", "-qb", "q", "main")
    rb(capsys, "experiment", "relate", "e1", "follows_from", "e0-elsewhere")
    commit_all("relate")
    git("merge", "-q", "--no-edit", "p")
    git("checkout", "-q", "main")
    rb(capsys, "spec", "vary", "e1", "depth")
    commit_all("vary")
    git("merge", "-q", "--no-edit", "q")
    (repo / "README.md").write_text("t\n", encoding="utf-8")
    commit_all("readme")
    assert failing(capsys) == []
    monkeypatch.chdir(shallow(repo, 3))
    assert failing(capsys) == [("shallow_clone", E1)]
    monkeypatch.chdir(shallow(repo, 4))
    assert failing(capsys) == []


def test_a_merge_holding_one_sides_record_where_the_sides_meet_past_the_clone_is_a_shallow_clone(repo, capsys, monkeypatch):
    """main merges old, which holds e1 as it was before main's merge made it what it is. A whole clone shows old never
    changed e1, so the last merge took main's e1 as it stands. With the commit where old and main meet past the clone
    (depth 4), rb cannot see whether old changed e1 and the merge kept that change out: it says the clone is shallow,
    never that the merge changed e1; with that commit in the clone (depth 7), e1 is rb's own."""
    rb(capsys, "init", "t", "--no-agents")
    rb(capsys, "experiment", "add", "x", "--id", "e1", "--baseline", "base", "--varies", "model.width")
    rb(capsys, "spec", "set", "e1", "lr", "0.1")
    commit_all("start")
    git("branch", "old")
    for branch, name in (("main", "t"), ("old", "o")):
        git("checkout", "-q", branch)
        for n in range(3):
            (repo / f"{name}{n}.txt").write_text(f"{n}\n", encoding="utf-8")
            commit_all(f"{name}{n}")
    git("checkout", "-qb", "side", "main")
    rb(capsys, "experiment", "gate", "e1", "--if-holds", "x")
    commit_all("gate")
    git("checkout", "-q", "main")
    rb(capsys, "spec", "set", "e1", "lr", "0.2")
    commit_all("lr")
    git("merge", "-q", "--no-edit", "side")
    git("merge", "-q", "--no-edit", "old")
    assert failing(capsys) == []
    for depth, met in ((4, False), (7, True)):
        monkeypatch.chdir(shallow(repo, depth))
        assert (subprocess.run(["git", "merge-base", "HEAD^1", "HEAD^2"], capture_output=True).returncode == 0) is met
        assert failing(capsys) == ([] if met else [("shallow_clone", E1)]), depth


@pytest.mark.parametrize("committed", [False, True])
@pytest.mark.parametrize("record", ["criteria/c1.yaml", "experiment.yaml"])
def test_an_edit_git_can_see_reads_as_one_in_a_shallow_clone(merged, capsys, monkeypatch, record, committed):
    """An edit made where the clone's history shows it (not yet committed, or committed in the clone) reads as edited
    outside rb, with restore offered, as in any clone: on c1, while e1 still reads as a shallow clone's; on e1 itself."""
    clone = shallow(merged)
    monkeypatch.chdir(clone)
    p = clone / ".rb" / "experiments" / "e1" / record
    text = p.read_text(encoding="utf-8")
    p.write_text(text.replace("title: x", "title: by hand").replace("statement: wide holds", "statement: wide holds well"), encoding="utf-8")
    assert p.read_text(encoding="utf-8") != text
    if committed:
        git("commit", "-qam", "by hand")
    checks = {c["detail"].split(":")[0]: c for c in rb_any(capsys, "doctor").data["checks"] if not c["ok"]}
    edited = checks.pop(f".rb/experiments/e1/{record}")
    assert edited["check"] == "edited_outside_rb" and "it was edited outside rb" in edited["detail"] and edited["fix"].startswith("rb doctor --restore")
    assert [c["check"] for c in checks.values()] == ([] if record == "experiment.yaml" else ["shallow_clone"])
    main(["doctor", "--restore"])
    [said] = [line for line in capsys.readouterr().out.splitlines() if line.startswith(("left", "restored")) and f"e1/{record}" in line]
    if record == "experiment.yaml":                                    # rb's last write of e1 is past the boundary
        assert said == f"left {E1} as it is: there is no copy of rb's last write here or in the history this shallow clone holds (git fetch --unshallow fetches the rest)"
    else:
        assert said == f"restored {C1}: restored to what rb last wrote"


@pytest.mark.parametrize("edited_on", ["main", "a pull request", "the merge"])
def test_a_merge_that_carries_a_hand_edit_reads_as_the_edit_in_a_shallow_clone(merged, capsys, monkeypatch, edited_on):
    """A merge holding one side's e1 as it stands did not make it: that side did, where git shows the edit; and a merge
    whose sides both hold e1 as the clone began made any change to it itself. e1 edited on main, then merged with a
    branch that never touched .rb/; edited on a branch main merges as a pull request; or edited during the merge: it
    reads as edited outside rb, with restore offered, as in a whole clone."""
    clone = shallow(merged)
    monkeypatch.chdir(clone)
    git("checkout", "-qb", "feature")
    if edited_on == "a pull request":
        by_hand(clone / E1)
    else:
        (clone / "feature.txt").write_text("f\n", encoding="utf-8")
    commit_all("feature")
    git("checkout", "-q", "main")
    if edited_on == "main":
        by_hand(clone / E1)
        commit_all("by hand")
        assert failing(capsys) == [("edited_outside_rb", E1)]
    else:
        (clone / "other.txt").write_text("o\n", encoding="utf-8")
        commit_all("other")
    if edited_on == "the merge":
        git("merge", "-q", "--no-commit", "--no-ff", "feature")
        assert failing(capsys) == [("shallow_clone", E1)]                # as the clone began, on both sides
        by_hand(clone / E1)
        assert failing(capsys) == [("edited_outside_rb", E1)]
        commit_all("merged by hand")
    else:
        git("merge", "-q", "--no-edit", "feature")
    [check] = [c for c in rb_any(capsys, "doctor").data["checks"] if not c["ok"]]
    assert check["check"] == "edited_outside_rb" and check["detail"] == f"{E1}: experiment e1 differs from what rb wrote: it was edited outside rb"
    assert check["fix"].startswith("rb doctor --restore")


def test_a_record_in_a_folder_of_the_repository_reads_its_history_from_git(repo, capsys, monkeypatch):
    """.rb/ a folder down, as a record vendored into a larger repository is, while git names a file at a commit from the
    repository's top: the merge is rb's own in a whole clone and a shallow clone's at depth 1, and a fresh clone (no copy
    of rb's own) restores a criterion edited by hand from git history."""
    (repo / "proj").mkdir()
    monkeypatch.chdir(repo / "proj")
    merge_e1(capsys, repo / "proj")
    whole = repo.parent / f"{repo.name}-whole"
    git("clone", "-q", repo.as_uri(), str(whole))
    monkeypatch.chdir(whole / "proj")
    assert failing(capsys) == []
    c1 = whole / "proj" / C1
    text = c1.read_text(encoding="utf-8")
    by_hand(c1, "statement: wide holds", "statement: wide holds well")
    assert failing(capsys) == [("edited_outside_rb", C1)]
    [restored] = rb(capsys, "doctor", "--restore").data["restored"]
    assert restored["done"] == "restored to what rb last wrote" and c1.read_text(encoding="utf-8") == text
    monkeypatch.chdir(shallow(repo) / "proj")
    assert failing(capsys) == [("shallow_clone", E1)]


def test_a_comment_committed_on_a_merge_is_no_edit(merged, capsys, monkeypatch):
    """rb compares content, never text: a comment committed on top of the merge leaves e1 rb's own in a whole clone, and
    in a clone cut at the merge or after it (depth 2 and 3), a shallow clone's; one that holds the merge's sides (depth
    4) sees the merge."""
    by_hand(merged / E1, "title: x\n", "title: x  # named at the kickoff\n")
    commit_all("a comment")
    assert failing(capsys) == [] and tampered(capsys) == []
    for depth in (2, 3):
        monkeypatch.chdir(shallow(merged, depth))
        assert failing(capsys) == [("shallow_clone", E1)], depth
    monkeypatch.chdir(shallow(merged, 4))
    assert failing(capsys) == []


def test_evidence_unread_for_two_reasons_names_each_with_its_way_back(repo, capsys, monkeypatch):
    """c1 reads two runs, neither as it stands: one changed before the clone's boundary, where rb cannot see how, and one
    edited by hand in the clone. The rule and the timing name each run with its own way back."""
    rb(capsys, "init", "t", "--no-agents")
    rb(capsys, "experiment", "add", "x", "--id", "e1", "--baseline", "base", "--candidate", "wide")
    rb(capsys, "criterion", "add", "wide holds", "-e", "e1", "--metric", "wide.top1", "--at-least", "0.6", "--id", "c1")
    cut, seen = (rb(capsys, "run", "add", "e1", f"wide.top1={v}").data["object"]["id"] for v in ("0.7", "0.8"))
    commit_all("runs")
    by_hand(repo / ".rb" / "experiments" / "e1" / "runs" / f"{cut}.yaml", "wide.top1: 0.7\n", "wide.top1: 0.75\n")
    commit_all("cut")
    (repo / "README.md").write_text("t\n", encoding="utf-8")
    commit_all("readme")
    clone = shallow(repo)
    monkeypatch.chdir(clone)
    by_hand(clone / ".rb" / "experiments" / "e1" / "runs" / f"{seen}.yaml", "wide.top1: 0.8\n", "wide.top1: 0.85\n")
    [f] = rb_any(capsys, "status").data["findings"]
    assert {c["subject"]: c["code"] for c in f["integrity"]} == {cut: "shallow_clone", seen: "edited_outside_rb"}
    assert f["rule_outcome"] == "undetermined"
    edited, shallow_too = f["says"]["rule"].split("; its evidence ")
    assert f"its evidence ({seen}) was edited outside rb" in edited and "restored or adopted (rb doctor)" in edited and cut not in edited
    assert shallow_too.startswith(f"({cut}) is not what rb last wrote, and the clone is shallow") and "git fetch --unshallow" in shallow_too
    assert f"its evidence is not read ({seen} edited outside rb: rb doctor; {cut} whose history this shallow clone lacks: git fetch --unshallow)" in f["says"]["timing"]


def test_a_review_pinned_in_a_shallow_clone_leaves_out_what_it_leaves_as_it_is(merged, capsys, monkeypatch):
    """--seen pins what someone reviewed: the edit to c1 is adopted on that review alone, and e1, whose making this clone
    cannot see, is left as it is, no part of the review either way."""
    clone = shallow(merged)
    monkeypatch.chdir(clone)
    c1 = clone / C1
    by_hand(c1, "statement: wide holds", "statement: wide holds well")

    def pin(rel: str) -> str:
        return f"{rel}@{hashlib.sha256((clone / rel).read_bytes()).hexdigest()}"
    for pinned, adopted in ((C1, {C1: "adopted as it stands", E1: "left as it is"}), (E1, {E1: "left as it is"})):
        main(["doctor", "--adopt", "--why", "reviewed", "--seen", pin(pinned), "--json"])
        env = Envelope.model_validate_json(capsys.readouterr().out)
        assert env.ok and env.errors == [], env.errors
        assert {a["path"]: a["done"].split(":")[0] for a in env.data["adopted"]} == adopted
    assert failing(capsys) == [("shallow_clone", E1)]


@pytest.mark.parametrize("committed", [False, True])
def test_a_hand_edit_after_a_merge_in_a_whole_clone_reads_edited_outside_rb(merged, capsys, committed):
    """Committed or not, the edit is one git shows after the merge, never the merge's own."""
    by_hand(merged / E1)
    if committed:
        git("commit", "-qam", "by hand")
    [item] = tampered(capsys)
    assert item["subject"] == "e1" and item["do"].startswith("rb doctor --restore")
    assert failing(capsys) == [("edited_outside_rb", E1)]


def test_rbs_write_undone_by_hand_after_a_merge_reads_edited_outside_rb(merged, capsys):
    """rb writes e1 after the merge; putting the merge's e1 back by hand is an edit, before it is committed and after."""
    rb(capsys, "spec", "set", "e1", "seed", "3")
    commit_all("seed")
    git("checkout", "HEAD~1", "--", E1)
    assert failing(capsys) == [("edited_outside_rb", E1)]
    commit_all("undo")
    assert failing(capsys) == [("edited_outside_rb", E1)]


# ---------------------------------------------------------------- what a worktree saw


def test_what_a_removed_worktree_printed_is_still_found(repo, capsys, monkeypatch):
    rb(capsys, "init", "t", "--no-agents")
    rb(capsys, "experiment", "add", "x", "--id", "e1", "--baseline", "base", "--candidate", "wide")
    rb(capsys, "criterion", "add", "wide holds", "-e", "e1", "--metric", "wide.top1", "--at-least", "0.6", "--id", "c1")
    git("add", "-A")
    git("commit", "-qm", "start")
    wt = repo.parent / f"{repo.name}-wt-try"
    git("worktree", "add", "-q", str(wt), "-b", "agent/try", "main")
    monkeypatch.chdir(wt)
    as_agent(monkeypatch, "session-try")
    printed = 'print(\'{"metrics": {"wide.top1": 0.71}}\')'
    assert main(["journal", "run", "--json", "--", sys.executable, "-c", printed]) == 0
    capsys.readouterr()
    common = Path(git("rev-parse", "--git-common-dir").strip()).resolve()
    assert (common / "rb" / "journal" / wt.name / "journal.jsonl").is_file() and not (wt / ".rb" / "journal").exists()
    monkeypatch.chdir(repo)
    git("worktree", "remove", "--force", str(wt))                  # thrown away before anyone recorded the run
    [item] = [i for i in rb(capsys, "reconcile").data["found"] if i["experiment"] == "e1"]
    assert "in a worktree removed since" in " ".join(item["facts"]) and item["argv"][:4] == ["run", "add", "e1", "wide.top1=0.71"]
    assert wt.name not in json.dumps(item)                         # a found item is not a place's label: it names no worktree
    rb(capsys, *item["argv"])
    f = rb(capsys, "show", "c1").data["finding"]
    assert f["rule_outcome"] == "meets"
    assert [i for i in rb(capsys, "reconcile").data["found"] if i["experiment"] == "e1"] == []


# ---------------------------------------------------------------- what is elsewhere


def listed_paths() -> dict:
    """Each worktree's path as git worktree list gives it, by the path resolved."""
    return {Path(line[len("worktree "):]).resolve(): line[len("worktree "):] for line in git("worktree", "list", "--porcelain").splitlines()
            if line.startswith("worktree ")}


# what says who wrote, which no place's label carries: the session, the runtime, the actor and the model
WRITER_SAYS = ("sess-wt-wide-41", "claude-code", "agent:", "human:", "test-model-wt-7")


def test_status_says_what_is_in_other_worktrees_and_branches(repo, capsys, monkeypatch):
    rb(capsys, "init", "t", "--no-agents")
    rb(capsys, "experiment", "add", "width", "--id", "e1", "--baseline", "base")
    git("add", "-A")
    git("commit", "-qm", "start")
    wt = repo.parent / f"{repo.name}-wt-wide"
    git("worktree", "add", "-q", str(wt), "-b", "agent/wide", "main")
    monkeypatch.chdir(wt)
    as_agent(monkeypatch, "sess-wt-wide-41")
    monkeypatch.setenv("RB_MODEL", "test-model-wt-7")
    rb(capsys, "variant", "add", "e1", "wide", "--role", "candidate")
    rb(capsys, "run", "add", "e1", "base.top1=0.7", "wide.top1=0.76")
    rb(capsys, "run", "add", "e1", "base.top1=0.71", "wide.top1=0.77")
    git("add", "-A", cwd=wt)
    git("commit", "-qm", "wide", cwd=wt)
    rb(capsys, "note", "add", "wide looks good", "-e", "e1")            # not committed
    as_the_user(monkeypatch)
    monkeypatch.chdir(repo)
    git("checkout", "-qb", "agent/depth")
    rb(capsys, "question", "add", "Is depth next?")
    git("add", "-A")
    git("commit", "-qm", "depth")
    git("checkout", "-q", "main")
    st = rb(capsys, "status").data
    places = {p["branch"]: p for p in st["elsewhere"]}
    assert set(places) == {"agent/wide", "agent/depth"}
    wide = places["agent/wide"]
    assert wide["worktree"] == wt.name and wide["uncommitted"] == 1
    assert (wide["path"], wide["same_project"], wide["gone"]) == (listed_paths()[wt.resolve()], True, False)
    assert {k: len(v) for k, v in wide["added"]["e1"].items()} == {"variant": 1, "run": 2, "note": 1}
    assert list(places["agent/depth"]["added"][""]) == ["question"] and places["agent/depth"]["path"] is None
    main(["status"])
    text = capsys.readouterr().out
    assert "Elsewhere (2)" in text
    # a place by its branch and its path, as git gives them, and its commit; nothing that says who wrote there
    wide_at, depth_at = git("rev-parse", "agent/wide").strip()[:12], git("rev-parse", "agent/depth").strip()[:12]
    assert f"  agent/wide · {wide['path']} at {wide_at}: 1 variant, 2 runs and 1 note on e1 (width) · 1 record file not committed" in text
    assert f"  agent/depth at {depth_at}: 1 question on the project" in text
    assert not [w for w in WRITER_SAYS if w in text]
    git("merge", "-q", "--no-edit", "agent/depth")
    assert [p["branch"] for p in rb(capsys, "status").data["elsewhere"]] == ["agent/wide"]


def test_a_call_elsewhere_is_counted_and_never_said_to_be_on_the_project(repo, capsys):
    """A decision is kept beside the project's own records whatever it is on: rb status counts one made on a branch as
    a decision, and never says it is on the project."""
    rb(capsys, "init", "t", "--no-agents")
    rb(capsys, "experiment", "add", "width", "--id", "e1", "--baseline", "base")
    git("add", "-A")
    git("commit", "-qm", "start")
    for branch, question in (("try/depth", "Is depth next?"), ("try/call", None)):
        git("checkout", "-qb", branch, "main")
        if question:
            rb(capsys, "question", "add", question)
        rb(capsys, "decide", "e1", "continue", "--why", "one more seed")
        git("add", "-A")
        git("commit", "-qm", branch)
    git("checkout", "-q", "main")
    main(["status"])
    text = capsys.readouterr().out
    depth_at, call_at = git("rev-parse", "try/depth").strip()[:12], git("rev-parse", "try/call").strip()[:12]
    assert f"  try/depth at {depth_at}: 1 question on the project; 1 decision\n" in text
    assert f"  try/call at {call_at}: 1 decision\n" in text


def test_status_tells_worktrees_at_one_commit_apart_by_branch_and_path(repo, capsys, monkeypatch):
    """Two worktrees made from one commit, each with work not committed, read apart in rb status by their branch and
    path, verbatim as git gives them whatever they are called, and rb adds no word that ties a place to a writer."""
    rb(capsys, "init", "t", "--no-agents")
    rb(capsys, "experiment", "add", "width", "--id", "e1", "--baseline", "base")
    git("add", "-A")
    git("commit", "-qm", "start")
    trees = {name: repo.parent / f"{repo.name}-{name}" for name in ("claude-narrow", "codex-wide")}
    for name, tree in trees.items():
        git("worktree", "add", "-q", str(tree), "-b", name.replace("-", "/"), "main")
    for n, tree in enumerate(trees.values(), 1):
        monkeypatch.chdir(tree)
        as_agent(monkeypatch, "sess-wt-wide-41")
        for i in range(n):
            rb(capsys, "note", "add", f"note {i} here", "-e", "e1")
    as_the_user(monkeypatch)
    monkeypatch.chdir(repo)
    listed = listed_paths()
    at = git("rev-parse", "main").strip()[:12]
    main(["status"])
    text = capsys.readouterr().out
    lines = sorted(line.strip() for line in text.splitlines() if " at " + at + ": " in line)
    assert lines == sorted(
        f"{name.replace('-', '/')} · {listed[tree.resolve()]} at {at}: {n} note{'s' if n > 1 else ''} on e1 (width) · {n} record file{'s' if n > 1 else ''} not committed"
        for n, (name, tree) in enumerate(trees.items(), 1))
    assert not [w for w in WRITER_SAYS if w in text]


def test_a_worktree_removed_without_prune_reads_as_removed_since(repo, capsys, monkeypatch):
    """A worktree deleted from disk and not pruned is still listed by git: what it committed is still read, and its
    line says it was removed since."""
    rb(capsys, "init", "t", "--no-agents")
    rb(capsys, "experiment", "add", "width", "--id", "e1", "--baseline", "base")
    git("add", "-A")
    git("commit", "-qm", "start")
    wt = repo.parent / f"{repo.name}-wt-gone"
    git("worktree", "add", "-q", str(wt), "-b", "try/gone", "main")
    monkeypatch.chdir(wt)
    rb(capsys, "run", "add", "e1", "base.top1=0.7")
    git("add", "-A", cwd=wt)
    git("commit", "-qm", "a run", cwd=wt)
    rb(capsys, "note", "add", "lost with the worktree", "-e", "e1")      # never committed
    monkeypatch.chdir(repo)
    listed = listed_paths()[wt.resolve()]
    shutil.rmtree(wt)
    [place] = rb(capsys, "status").data["elsewhere"]
    assert (place["branch"], place["path"], place["gone"], place["uncommitted"]) == ("try/gone", listed, True, 0)
    main(["status"])
    at = git("rev-parse", "try/gone").strip()[:12]
    assert f"  try/gone · {listed} at {at} (removed since): 1 run on e1 (width)" in capsys.readouterr().out


def test_a_worktree_holding_a_separate_project_is_told_apart(repo, capsys, monkeypatch):
    """A worktree whose record is another project's (another uid, started with rb init --new-project before this record
    reached its branch) is a separate project, never work on this record."""
    git("commit", "-q", "--allow-empty", "-m", "before the record")
    rb(capsys, "init", "t", "--no-agents")
    rb(capsys, "experiment", "add", "width", "--id", "e1", "--baseline", "base")
    git("add", "-A")
    git("commit", "-qm", "start")
    wt = repo.parent / f"{repo.name}-wt-other"
    git("worktree", "add", "-q", str(wt), "-b", "other", "HEAD~1")
    monkeypatch.chdir(wt)
    theirs = rb(capsys, "init", "t", "--new-project", "--no-agents").data["object"]["uid"]
    rb(capsys, "experiment", "add", "width", "--id", "e1", "--baseline", "base")
    monkeypatch.chdir(repo)
    assert theirs != rb(capsys, "status").data["project"]["uid"]
    [place] = rb(capsys, "status").data["elsewhere"]
    assert (place["branch"], place["same_project"]) == ("other", False)
    main(["status"])
    text = capsys.readouterr().out
    assert f"  other · {listed_paths()[wt.resolve()]} at {git('rev-parse', 'other').strip()[:12]}: a separate project, with its own record" in text
    assert "new experiment e1" not in text
    # the heading says what is there: no work on this record, a separate project's record
    assert "Elsewhere (1): a separate project's record in a branch or worktree of this repository" in text
    assert "work on this record" not in text
