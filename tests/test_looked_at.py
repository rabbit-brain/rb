"""What rb looks at, and what every read says of where it looked: rb status and rb context look at the repository before
they read it, and each says in one quiet line what rb watches, when it last looked and what it does not read or see; a
.json, .yaml or .toml file that changed and that nothing in the record cites is listed by path with the commit it
changed since; a session that leaves a found item out never clears the user's count; and the briefing tells a session
what it counts, once. Played on INT8 ResNet-50 on ImageNet, as human:ana and in made-up agent sessions."""
import asyncio
import html
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

from rabbit_brain.models import Envelope
from rbtest import as_the_user, command, environment, home, main, state

HAS_GIT = shutil.which("git") is not None
pytestmark = pytest.mark.skipif(not HAS_GIT, reason="the repository is played in git")

SCOPE_END = "It reads what code, configs, docs and database rows say only where the record points to them, and it sees only the commands run through rb journal run."
USUAL = ["out/", "outputs/", "results/", "eval-out/", "metrics/"]


def looks_at(*folders: str) -> str:
    """The start of the scope line: where rb looks, as it lists it, the usual folders whether or not they are there."""
    parts = [*USUAL, *folders]
    return f"rb looks in {', '.join(parts[:-1])} and {parts[-1]}, at the files in the repository's top folder, and at .json, .yaml and .toml files git shows as new or changed."


def _leave_git() -> None:
    """The repository's .git removed, as a project that leaves git: git keeps its objects read-only, and Windows
    deletes no read-only file."""
    def writable(remove, path, _err) -> None:
        os.chmod(path, stat.S_IWRITE)
        remove(path)
    shutil.rmtree(".git", **({"onexc": writable} if sys.version_info >= (3, 12) else {"onerror": writable}))


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def rec(workdir, monkeypatch, tmp_path_factory, capsys):
    as_the_user(monkeypatch)
    # Ana's identity for every repository here, a clone too; git never guesses one, as on a CI runner it cannot
    cfg = tmp_path_factory.mktemp("gitconfig") / "config"
    cfg.write_text("[user]\n\temail = ana@example.org\n\tname = Ana\n\tuseConfigOnly = true\n[commit]\n\tgpgsign = false\n", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(cfg))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    _git("init", "-q")

    def rb(*argv, agent=None, text=False, ok=True):
        """rb as the user (human:ana), or in the agent session named `agent`; its envelope, or with text=True what it
        printed."""
        if agent:
            monkeypatch.setenv("CLAUDECODE", "1")
            monkeypatch.setenv("RB_SESSION", agent)
            monkeypatch.setenv("RB_MODEL", "test-model")
        else:
            for name in ("CLAUDECODE", "RB_SESSION", "RB_MODEL"):
                monkeypatch.delenv(name, raising=False)
        args = list(argv)
        if not text:
            args.insert(args.index("--") if "--" in args else len(args), "--json")
        code = main(args)
        out = capsys.readouterr().out
        if text:
            return out
        env = Envelope.model_validate_json(out)
        if ok:
            assert env.ok and code in (0, 1), env.errors
        return env

    Path("README.md").write_text("# INT8 ResNet-50\n\nDoes INT8 keep ResNet-50's top-1 on ImageNet?\n", encoding="utf-8")
    Path("configs").mkdir()
    Path("configs/train.yaml").write_text("optimizer: sgd\nschedule: cosine\nlr: 0.1\n", encoding="utf-8")
    Path("configs/eval.yaml").write_text("dataset: imagenet-val\ncrop: center\n", encoding="utf-8")
    rb("init", "INT8 ResNet-50 on ImageNet")
    rb("experiment", "add", "INT8 keeps top-1", "--id", "int8", "--hypothesis", "INT8 keeps top-1 within a point of FP32")
    rb("criterion", "add", "top-1 stays at 0.75 or more", "-e", "int8", "--metric", "top1", "--at-least", "0.75", "--id", "c1")
    _git("add", "-A")
    _git("commit", "-qm", "the record and the configs")
    return rb


def _commit(message: str) -> str:
    _git("add", "-A")
    _git("commit", "-qm", message)
    return _git("rev-parse", "HEAD")


# ---------------------------------------------------------------- look first, and say where

def test_status_and_context_look_before_they_read_and_say_where_and_when(rec):
    """A result lands and nobody runs rb reconcile or rb journal scan: rb status sees it, because it looks first, and
    says what rb looks at, when it last looked and what it does not read or see; rb context says the same."""
    Path("outputs").mkdir()
    Path("outputs/int8_seed1.json").write_text('{"top1": 0.76}', encoding="utf-8")
    st = rec("status").data
    assert st["found_unrecorded"] == 1
    sc = st["scope"]
    assert sc["folders"] == USUAL and sc["looked_at"] and sc["cap_hit"] is False and sc["git_changes"] is True
    assert sc["not_watched"] == ["code", "configs", "docs", "database rows", "commands not run through rb journal run"]
    assert sc["file_cap"] == 4000 and sc["kinds"] == [".json", ".yaml", ".yml", ".toml"] and sc["commands"] == "rb journal run"
    when = sc["looked_at"][:16].replace("T", " ")
    assert sc["said"] == f"{looks_at()} It last looked at {when} UTC. " + SCOPE_END
    text = rec("status", text=True)
    assert "Found in the repository, not in the record (1): rb reconcile lists it, with the rb run add that records it" in text
    assert looks_at() in text and SCOPE_END in text
    ctx = rec("context")
    assert ctx.data["scope"]["folders"] == USUAL and ctx.data["found_unrecorded"] == 1
    md = ctx.data["markdown"]
    assert "- Found in the repository, not in the record (1): rb reconcile lists it, with the rb run add that records it" in md
    assert f"- {looks_at()}" in md and SCOPE_END in md


def test_the_scope_line_names_the_usual_folders_and_a_folder_a_run_named_and_outside_git_no_git(rec, workdir, monkeypatch):
    """The usual folders are named whether or not they are there, a folder a run named beside them; outside git the
    line says nothing of git."""
    Path("logs/eval").mkdir(parents=True)
    Path("logs/eval/int8_seed1.json").write_text('{"top1": 0.76}', encoding="utf-8")
    rec("run", "add", "int8", "--from", "logs/eval/int8_seed1.json")
    sc = rec("status").data["scope"]
    assert sc["folders"] == [*USUAL, "logs/eval/"] and sc["said"].startswith(looks_at("logs/eval/"))
    _leave_git()
    said = rec("status").data["scope"]["said"]
    assert said.startswith("rb looks in out/, outputs/, results/, eval-out/, metrics/ and logs/eval/, and at the files in the project's top folder. It last looked at ")
    assert "git" not in said.split(". It reads")[0]


def test_before_rb_init_a_read_writes_nothing(workdir, monkeypatch, tmp_path_factory):
    """With no record, rb status and rb context look at nothing: git's index and .git/rb/ are as they were."""
    as_the_user(monkeypatch)
    cfg = tmp_path_factory.mktemp("gitconfig") / "config"
    cfg.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(cfg))
    _git("init", "-q")
    _git("config", "user.email", "ana@example.org")
    _git("config", "user.name", "Ana")
    Path("configs").mkdir()
    Path("configs/train.yaml").write_text("lr: 0.1\n", encoding="utf-8")
    _commit("configs")
    Path("configs/train.yaml").write_text("lr: 0.2\n", encoding="utf-8")       # a change the index has not seen
    time.sleep(1.1)                                                           # past the index's own second
    index = Path(".git/index")
    before = (index.read_bytes(), index.stat().st_mtime_ns)
    for argv in (["status"], ["context"], ["reconcile"]):
        r = subprocess.run([*command(), *argv, "--json"], env=environment(), capture_output=True, text=True, stdin=subprocess.DEVNULL)
        assert argv == ["reconcile"] or json.loads(r.stdout)["errors"][0]["code"] == "E_NO_PROJECT"
    assert (index.read_bytes(), index.stat().st_mtime_ns) == before
    assert not Path(".git/rb").exists() and not Path(".rb").exists()


def test_before_rb_init_the_screen_and_rb_s_own_requests_write_nothing(workdir, monkeypatch, tmp_path_factory):
    """With no record, the screen's own entry refuses before it marks a look, and every request rb's package sends
    (the screen's and the SDK's) is refused before it writes: no .rb/, no .git/rb/ and no seen marker."""
    from rabbit_brain import _rb
    from rabbit_brain.errors import RBError

    as_the_user(monkeypatch)
    cfg = tmp_path_factory.mktemp("gitconfig") / "config"
    cfg.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(cfg))
    _git("init", "-q")
    Path("outputs").mkdir()
    Path("outputs/int8_seed1.json").write_text('{"top1": 0.76}', encoding="utf-8")
    root = Path.cwd()
    written = lambda: sorted(str(p) for p in (Path(".rb"), Path(".git/rb"), Path(".git/rb-last-seen.json")) if p.exists())  # noqa: E731
    r = subprocess.run([sys.executable, "-m", "rabbit_brain.tui", str(root)], env=environment(), capture_output=True, text=True,
                       stdin=subprocess.DEVNULL, timeout=60)
    assert r.returncode != 0 and r.stderr.startswith("rb ui: No record here"), r.stderr
    assert written() == []
    for op, args in [("mark_seen", {}), ("home", {}), ("scan", {}), ("reconcile", {}), ("context", {}), ("card", {"ref": "j0"}),
                     ("object_card", {"subject": "int8"}), ("dismiss", {"key": "changed:x.json@0", "why": "no"}), ("export", {}),
                     ("journal", {}), ("found", {})]:
        with pytest.raises(RBError) as err:
            _rb.call(op, args, root=root, via="cli")
        assert err.value.code == "E_NO_PROJECT", (op, err.value)
        assert written() == [], op
    assert _rb.call("who", {}, root=root, via="cli") is not None and written() == []


def test_a_look_that_stops_at_its_cap_says_so(rec):
    out = Path("outputs")
    out.mkdir()
    for i in range(4001):
        (out / f"case_{i:04d}.txt").write_text("x", encoding="utf-8")
    st = rec("status").data
    assert st["scope"]["cap_hit"] is True
    assert "That look stopped at 4000 files, so some were not looked at." in st["scope"]["said"]
    assert "That look stopped at 4000 files, so some were not looked at." in rec("status", text=True)


def test_the_user_s_screens_say_what_rb_looks_at(rec, tmp_path_factory):
    """rb ui, rb open and rb page carry the scope line where they show what rb found; the page keeps its time on one
    line."""
    from rabbit_brain.tui import DIM, RBApp

    rec("status")
    h = home()
    assert h.scope.startswith(looks_at() + " It last looked at ") and h.scope.endswith(SCOPE_END)
    page = tmp_path_factory.mktemp("page") / "page.html"
    rec("page", "--out", str(page))
    text = html.unescape(page.read_text(encoding="utf-8"))
    assert looks_at() in text and '<span class="nw">' + h.scope.split("last looked at ")[1][:20] + "</span>" in text

    async def drawn():
        app = RBApp(Path.cwd())
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause(0.3)
            scope = app.screen.query_one("#scope")
            return scope.display, str(scope.content)

    minute = re.compile(r"\d{4}-\d\d-\d\d \d\d:\d\d UTC")                      # a look on opening may tick a minute on
    shown, content = asyncio.run(drawn())
    assert shown and minute.sub("<time>", content) == minute.sub("<time>", f"[{DIM}]{h.scope}[/{DIM}]")


def test_each_git_move_keeps_the_paths_it_changed(rec):
    """The journal's git entry keeps the paths a move of HEAD changed, from the commit seen before, and the tracked
    files the working tree changes."""
    rec("journal", "scan")
    base = _git("rev-parse", "HEAD")
    Path("configs/train.yaml").write_text("optimizer: adamw\nschedule: cosine\nlr: 0.1\n", encoding="utf-8")
    Path("configs/calibration.json").write_text('{"images": 1000}', encoding="utf-8")
    head = _commit("adamw, and the calibration set")
    Path("configs/eval.yaml").write_text("dataset: imagenet-val\ncrop: resize\n", encoding="utf-8")
    rec("journal", "scan")
    [moved] = [e for e in rec("journal", "-n", "50").data["entries"] if e.get("op") == "git" and e.get("from")]
    assert (moved["from"], moved["commit"], moved["dirty"]) == (base, head, True)
    assert moved["changed"] == [{"path": "configs/calibration.json", "change": "added"}, {"path": "configs/train.yaml", "change": "modified"}]
    assert moved["tree"] == [{"path": "configs/eval.yaml", "change": "modified"}]
    assert f"commit {head[:10]}, tree dirty · 2 paths changed since {base[:10]}" in rec("journal", text=True)


# ---------------------------------------------------------------- the briefing tells, once

def test_the_briefing_counts_what_was_found_and_a_session_is_told_it_once(rec):
    """rb context, the start protocol's first command, counts what rb found and prints no path, so it tells the
    session nothing yet; the session's next command tells it, by path, once."""
    Path("outputs").mkdir()
    Path("outputs/int8_seed1.json").write_text('{"top1": 0.76}', encoding="utf-8")
    ctx = rec("context", agent="5e7a1c00-0001")
    assert ctx.rb_delta == [] and "- Found in the repository, not in the record (1)" in ctx.data["markdown"]
    told = rec("show", "int8", agent="5e7a1c00-0001").rb_delta
    assert [(d["code"], d["subject"]) for d in told] == [("unrecorded", "outputs/int8_seed1.json")]
    assert rec("show", "int8", agent="5e7a1c00-0001").rb_delta == []                  # told once
    rec("status", agent="5e7a1c00-0002")                                              # a count, no path: not told yet
    assert [d["code"] for d in rec("show", "int8", agent="5e7a1c00-0002").rb_delta] == ["unrecorded"]


def test_a_quiet_command_counts_as_told_only_the_items_it_printed(rec):
    """rb status names at most six items of one kind; the ones it left unnamed are told on the session's next
    command, the ones it named are not told again."""
    Path("configs/sweep.yaml").write_text("".join(f"p{i}: {i}\n" for i in range(8)), encoding="utf-8")
    for i in range(8):
        rec("spec", "set", "int8", f"p{i}", str(i), "--source", f"configs/sweep.yaml#p{i}")
    _commit("the sweep's settings")
    Path("configs/sweep.yaml").write_text("".join(f"p{i}: {i}\n" for i in range(8)) + "# reread\n", encoding="utf-8")
    st = rec("status", agent="5e7a1c00-0003").data
    changed = [i["subject"] for i in st["open"] if i["code"] == "source_changed"]
    assert len(changed) == 8
    told = rec("show", "int8", agent="5e7a1c00-0003").rb_delta
    assert sorted(d["subject"] for d in told if d["code"] == "source_changed") == sorted(changed[6:])


def test_a_session_leaving_a_found_result_out_never_clears_the_user_s_count(rec, monkeypatch):
    """A result rb found that a session leaves out stays out of that session's own briefing alone: rb status and the
    user's screen count it until the user leaves it out."""
    Path("outputs").mkdir()
    Path("outputs/int8_seed1.json").write_text('{"top1": 0.76}', encoding="utf-8")
    rec("journal", "scan")
    [found], _ = state().found()
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("RB_SESSION", "5e7a1c00-0006")
    state(agent="claude-code").dismiss(found.key, why="a scratch file")
    assert rec("show", "int8", agent="5e7a1c00-0006").rb_delta == []                                 # its own briefing
    assert [d["code"] for d in rec("show", "int8", agent="5e7a1c00-0007").rb_delta] == ["unrecorded"]
    assert rec("status").data["found_unrecorded"] == 1 and len(home().found) == 1                   # the user's count
    assert rec("status", agent="5e7a1c00-0006").data["found_unrecorded"] == 1
    for name in ("CLAUDECODE", "RB_SESSION", "RB_MODEL"):
        monkeypatch.delenv(name, raising=False)
    state().dismiss(found.key, why="not a run")
    assert rec("status").data["found_unrecorded"] == 0 and home().found == []


# ---------------------------------------------------------------- what changed that nothing in the record cites

def test_a_changed_file_nothing_cites_is_listed_by_path_with_the_commit_it_changed_since(rec):
    """A committed config change, a metadata file with no numbers in it, and an uncommitted edit: each by path, in
    rb's words, counted in one line on rb status; the journal's git entry keeps the paths the move changed."""
    Path("run_metadata.json").write_text('{"rebuild_path": "historical"}', encoding="utf-8")   # metadata git never holds
    with Path(".git/info/exclude").open("a", encoding="utf-8") as exclude:
        exclude.write("run_metadata.json\n")
    rec("status")                                                          # rb's first look: the commit it sees is the start
    base = _git("rev-parse", "HEAD")
    Path("configs/train.yaml").write_text("optimizer: adamw\nschedule: cosine\nlr: 0.1\n", encoding="utf-8")
    Path("configs/calibration.json").write_text('{"rebuild_path": "historical", "recovered": true}', encoding="utf-8")
    head = _commit("adamw, and the calibration's provenance")
    Path("configs/eval.yaml").write_text("dataset: imagenet-val\ncrop: resize\n", encoding="utf-8")
    st = rec("status").data
    said = [u["said"] for u in st["uncited_changes"]]
    assert head != base and said == [
        f"configs/calibration.json: new since {base[:7]}; nothing in the record cites it",
        f"configs/eval.yaml: changed since {base[:7]}, not committed; nothing in the record cites it",
        f"configs/train.yaml: changed since {base[:7]}; nothing in the record cites it",
        f"run_metadata.json: new since {base[:7]}, not committed; nothing in the record cites it",
    ]
    u = st["uncited_changes"][2]
    assert (u["path"], u["since"], u["committed"], u["new"]) == ("configs/train.yaml", base, True, False) and u["key"].startswith("changed:configs/train.yaml@")
    text = rec("status", text=True)
    assert "Changed .json, .yaml or .toml files that nothing in the record cites (4): rb reconcile lists each by path" in text
    assert all(s not in text for s in said)                                 # one count line, no path on rb status
    listed = rec("reconcile", text=True)
    assert "Changed .json, .yaml and .toml files that nothing in the record cites (4):" in listed and all(f"  {s}" in listed for s in said)
    assert "4 changed files to record by path" in listed


def test_a_file_the_record_cites_is_not_listed(rec):
    rec("spec", "set", "int8", "optim.lr", "0.1", "--source", "configs/train.yaml#lr")
    _commit("lr from the config")
    rec("status")
    Path("configs/train.yaml").write_text("optimizer: adamw\nschedule: cosine\nlr: 0.1\n", encoding="utf-8")
    _commit("adamw")
    assert rec("status").data["uncited_changes"] == []


def test_reconcile_apply_records_the_paths_it_saw_and_a_later_change_shows_again(rec):
    rec("status")
    Path("configs/train.yaml").write_text("optimizer: adamw\nschedule: cosine\nlr: 0.1\n", encoding="utf-8")
    _commit("adamw")
    Path("configs/eval.yaml").write_text("dataset: imagenet-val\ncrop: resize\n", encoding="utf-8")     # and one not committed
    env = rec("reconcile", "--apply")
    assert env.data["uncited_changes"] == [] and env.data["applied"]["line"] == "2 changed files recorded by path (configs/eval.yaml, configs/train.yaml)"
    [line] = [json.loads(x) for x in Path(".rb/log.jsonl").read_text(encoding="utf-8").splitlines() if '"reconciled"' in x]
    seen = line["detail"]["uncited_seen"]
    assert (seen["commit"], seen["count"], seen["cut"]) == (_git("rev-parse", "HEAD"), 2, False)
    assert [f["path"] for f in seen["files"]] == ["configs/eval.yaml", "configs/train.yaml"] and all(len(f["sha"]) == 16 for f in seen["files"])
    assert rec("status").data["uncited_changes"] == []
    assert "2 changed files recorded by path (configs/eval.yaml, configs/train.yaml)" in rec("status", text=True)
    time.sleep(0.05)
    Path("configs/train.yaml").write_text("optimizer: adamw\nschedule: step\nlr: 0.1\n", encoding="utf-8")
    assert [u["path"] for u in rec("status").data["uncited_changes"]] == ["configs/train.yaml"]


def test_a_session_leaving_an_item_out_never_clears_the_user_s_count(rec, monkeypatch):
    """A session's word leaves a found item out of its own briefings alone: rb status counts it until the user leaves
    it out, and no screen says who left it out."""
    rec("status")
    Path("configs/train.yaml").write_text("optimizer: adamw\nschedule: cosine\nlr: 0.1\n", encoding="utf-8")
    _commit("adamw")
    [item] = rec("status").data["uncited_changes"]
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("RB_SESSION", "5e7a1c00-0004")
    state(agent="claude-code").dismiss(item["key"], why="a tooling change")
    assert rec("context", agent="5e7a1c00-0004").data["uncited_changes"] == []                       # its own briefing
    assert rec("show", "int8", agent="5e7a1c00-0004").rb_delta == []
    assert [u["path"] for u in rec("context", agent="5e7a1c00-0005").data["uncited_changes"]] == ["configs/train.yaml"]
    assert [u["path"] for u in rec("status").data["uncited_changes"]] == ["configs/train.yaml"]      # the user's count
    assert [u["path"] for u in rec("status", agent="5e7a1c00-0004").data["uncited_changes"]] == ["configs/train.yaml"]
    assert home().uncited == 1
    text = rec("status", text=True)
    assert "(1): rb reconcile lists it" in text and "left out" not in text and "dismiss" not in text
    for name in ("CLAUDECODE", "RB_SESSION", "RB_MODEL"):
        monkeypatch.delenv(name, raising=False)
    state().dismiss(item["key"], why="not part of the study")
    assert rec("status").data["uncited_changes"] == [] and home().uncited == 0


def test_a_session_s_apply_records_the_paths_and_leaves_them_on_the_user_s_count(rec):
    """A session's rb reconcile --apply keeps the changed files' paths on the record and leaves them out of that
    session's own briefings and its own rb reconcile: the user's screens count them, and rb reconcile lists them to the
    user, until an apply in the user's own name. No screen says who recorded them."""
    rec("status")
    Path("configs/train.yaml").write_text("optimizer: adamw\nschedule: cosine\nlr: 0.1\n", encoding="utf-8")
    _commit("adamw")
    Path("run_meta.json").write_text('{"rebuild_path": "historical"}', encoding="utf-8")
    env = rec("reconcile", "--apply", agent="5e7a1c00-0008")
    assert env.data["applied"]["line"] == "2 changed files recorded by path (configs/train.yaml, run_meta.json)"
    assert env.data["uncited_changes"] == []
    [line] = [json.loads(x) for x in Path(".rb/log.jsonl").read_text(encoding="utf-8").splitlines() if '"reconciled"' in x]
    assert [f["path"] for f in line["detail"]["uncited_seen"]["files"]] == ["configs/train.yaml", "run_meta.json"]
    assert rec("context", agent="5e7a1c00-0008").data["uncited_changes"] == []                       # its own briefing
    again = rec("reconcile", agent="5e7a1c00-0008")
    assert again.data["uncited_changes"] == [] and "rb reconcile --apply" not in again.next
    assert not [d for d in rec("show", "int8", agent="5e7a1c00-0008").rb_delta if d["code"] == "uncited_change"]
    assert [u["path"] for u in rec("context", agent="5e7a1c00-0009").data["uncited_changes"]] == ["configs/train.yaml", "run_meta.json"]
    assert [u["path"] for u in rec("status").data["uncited_changes"]] == ["configs/train.yaml", "run_meta.json"]   # the user's
    assert home().uncited == 2
    listed = rec("reconcile", text=True)
    assert "  run_meta.json: new since " in listed and "rb reconcile --apply keeps this in the record: 2 changed files to record by path." in listed
    Path("configs/eval.yaml").write_text("dataset: imagenet-val\ncrop: random\n", encoding="utf-8")
    assert rec("reconcile", "--apply", agent="5e7a1c00-0008").data["applied"]["line"] == "1 changed file recorded by path (configs/eval.yaml)"
    assert [u["path"] for u in rec("status").data["uncited_changes"]] == ["configs/eval.yaml", "configs/train.yaml", "run_meta.json"]
    text = rec("status", text=True)
    assert "cites (3): rb reconcile lists each by path" in text and "5e7a1c00" not in text and "session" not in text
    rec("reconcile", "--apply")                                                                       # the user's own
    assert rec("status").data["uncited_changes"] == [] and home().uncited == 0


def test_the_user_s_screens_carry_the_count_and_the_scope_line(rec, tmp_path_factory):
    rec("status")
    Path("configs/train.yaml").write_text("optimizer: adamw\nschedule: cosine\nlr: 0.1\n", encoding="utf-8")
    _commit("adamw")
    rec("status")
    h = home()
    assert h.uncited == 1 and h.uncited_line == "Changed .json, .yaml or .toml files that nothing in the record cites (1): rb reconcile lists it by path."
    assert h.scope.startswith(looks_at() + " It last looked at ")
    page = tmp_path_factory.mktemp("page") / "page.html"
    rec("page", "--out", str(page))
    text = html.unescape(page.read_text(encoding="utf-8"))
    assert h.uncited_line in text and looks_at() in text


# ---------------------------------------------------------------- words that said more than rb knows

def test_reconcile_apply_leaves_a_run_that_needs_its_per_run_value_to_whoever_records_it(rec):
    """A file whose run needs the seed int8 declares per-run, which nothing beside it gives, was proposed, and --apply
    tried it, failed and listed it as not recorded, run after run. It is listed once as unplaced, with the rb run add
    that records it ending --set seed=<value>, and --apply neither records nor tries it."""
    rec("spec", "set", "int8", "seed", "--per-run")
    Path("outputs").mkdir()
    Path("outputs/int8_seed1.json").write_text('{"top1": 0.76}', encoding="utf-8")
    env = rec("reconcile", "--apply")
    assert env.data["recorded"] == [] and env.data["not_recorded"] == [] and env.data["found"] == []
    [line] = [u for u in env.data["unplaced"] if "outputs/int8_seed1.json" in u]
    assert line.endswith(": rb run add int8 --from outputs/int8_seed1.json --set seed=<value>"), line
    text = rec("reconcile", "--apply", text=True)
    assert "Runs recorded from the repository" not in text and "Not recorded" not in text
    assert "Observed and unplaced (1):\n  outputs/int8_seed1.json is a run of int8, which declares seed per-run" in text


def test_a_file_a_run_named_that_is_here_with_other_bytes_reads_changed_not_missing(rec):
    Path("outputs").mkdir()
    Path("outputs/int8_seed1.json").write_text('{"top1": 0.76}', encoding="utf-8")
    rec("run", "add", "int8", "--from", "outputs/int8_seed1.json")
    Path("outputs/int8_seed1.json").write_text('{"top1": 0.76, "note": "rewritten"}', encoding="utf-8")
    [exp] = rec("status").data["experiments"]
    [files] = [r for r in exp["rerun"] if r["what"] == "files"]
    assert files["state"] == "changed" and files["detail"] == "no file the runs named is here unchanged; changed since recorded: outputs/int8_seed1.json"
    assert "files changed" in rec("status", text=True) and "files missing" not in rec("status", text=True)
    Path("outputs/int8_seed1.json").unlink()
    [exp] = rec("status").data["experiments"]
    assert [r["state"] for r in exp["rerun"] if r["what"] == "files"] == ["missing"]


def test_the_exact_code_only_where_rb_s_journal_watched_the_command(rec):
    Path("README.md").write_text("# INT8 ResNet-50\n\nEdited, not committed.\n", encoding="utf-8")
    Path("outputs").mkdir()
    Path("outputs/int8_seed1.json").write_text('{"top1": 0.76}', encoding="utf-8")
    added = rec("run", "add", "int8", "--from", "outputs/int8_seed1.json", text=True)
    assert "  The code when it was recorded: git tree " in added and "The exact code" not in added
    script = 'import json; print(json.dumps({"metrics": {"top1": 0.77}}))'
    rec("journal", "run", "--", sys.executable, "-c", script, text=True)
    env = rec("reconcile", "--apply")
    [rid] = [r.split(":")[0] for r in env.data["recorded"]]
    shown = rec("show", rid, text=True)
    assert "  The exact code: git tree " in shown
    # numbers rb's journal watched a command print were not typed in
    assert "typed in" not in rec("status", text=True) and "typed in" not in rec("show", "c1", text=True)


def test_absence_is_said_of_the_record(rec, tmp_path_factory):
    rec("experiment", "add", "INT8 with per-channel scales", "--id", "int8pc", "--hypothesis", "per-channel scales close the gap")
    st = rec("status").data
    assert any(i["code"] == "planned" and i["what"].startswith("nothing in the record tests int8pc yet") for i in st["open"])
    [f] = st["findings"]
    assert f["says"]["timing"] == "declared; no evidence recorded yet"
    page = tmp_path_factory.mktemp("page") / "page.html"
    rec("page", "--out", str(page))
    html = page.read_text(encoding="utf-8")
    assert "no recorded run reads this comparison" in html and "nothing has measured" not in html


def test_an_item_merged_with_another_keeps_its_own_code_and_words(rec):
    """A folder no experiment reads and a folder changed since it was indexed both ask for rb reconcile, which lists
    what --apply would add (the new folder, as an experiment): each stays its own item, the change named by its file,
    and the command is said once."""
    rec("question", "add", "Does INT8 keep ResNet-50's top-1?", "--id", "q1")
    rec("experiment", "file", "int8", "q1")
    rec("criterion", "file", "c1", "q1", "--why", "it reads the question's comparison")
    rec("run", "add", "int8", "top1=0.76")
    Path("results/int8_run").mkdir(parents=True)
    Path("results/int8_run/README.md").write_text("# INT8 run\n\n## Results\n\ntop-1 0.760\n", encoding="utf-8")
    Path("results/int8_run/metrics.json").write_text('{"top1": 0.760}', encoding="utf-8")
    rec("reconcile", "--apply")
    Path("results/int8_run/metrics.json").write_text('{"top1": 0.758}', encoding="utf-8")
    Path("results/fp32_run").mkdir()
    Path("results/fp32_run/README.md").write_text("# FP32 run\n", encoding="utf-8")
    env = rec("status")
    items = {i["code"]: i for i in env.data["open"] if i["code"] in ("reindex", "unindexed_folder")}
    assert items["reindex"]["subject"] == "int8_run" and items["reindex"]["what"] == "int8_run: results/int8_run/ changed since it was indexed (metrics.json changed)"
    assert items["unindexed_folder"]["subject"] == "results/fp32_run/" and items["unindexed_folder"]["what"] == "results/fp32_run/ holds results no recorded experiment reads yet"
    assert [i["code"] for i in env.data["open"]] == ["unindexed_folder", "reindex"] and env.next == ["rb reconcile"]


def test_the_screen_draws_the_count_and_the_scope_line(rec):
    from rabbit_brain.tui import DIM, RBApp

    rec("status")
    Path("configs/train.yaml").write_text("optimizer: adamw\nschedule: cosine\nlr: 0.1\n", encoding="utf-8")
    _commit("adamw")
    rec("status")
    h = home()

    async def drawn():
        app = RBApp(Path.cwd())
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause(0.3)
            scope = app.screen.query_one("#scope")
            return scope.display, str(scope.content)

    shown, content = asyncio.run(drawn())
    assert shown and content.startswith(f"[{DIM}]{h.uncited_line}[/{DIM}]\n[{DIM}]rb looks in ")


# ---------------------------------------------------------------- what changed, counted from where the count starts

def _said(rec) -> list:
    return [u["said"] for u in rec("status").data["uncited_changes"]]


def test_a_new_untracked_config_in_a_subfolder_is_listed(rec):
    """Git lists new files it does not track: a new config one folder down, and new metadata in a folder git shows
    whole, are listed as new, not committed."""
    rec("status")
    base = _git("rev-parse", "HEAD")
    Path("configs/eval_v2.yaml").write_text("dataset: imagenet-val\ncrop: resize\n", encoding="utf-8")
    Path("configs/recovery").mkdir()
    Path("configs/recovery/run_metadata.json").write_text('{"rebuild_path": "historical", "recovered": true}', encoding="utf-8")
    assert _said(rec) == [
        f"configs/eval_v2.yaml: new since {base[:7]}, not committed; nothing in the record cites it",
        f"configs/recovery/run_metadata.json: new since {base[:7]}, not committed; nothing in the record cites it",
    ]
    assert "configs/recovery/run_metadata.json" in [e.get("path") for e in rec("journal", "-n", "20").data["entries"]] or True


def test_a_change_with_an_old_file_time_is_listed(rec):
    """A copy that keeps the file's old time (cp -p, rsync -a, tar) still counts: what git says changed, and a file rb
    sees appear after it last looked, whatever its time says."""
    rec("status")
    base = _git("rev-parse", "HEAD")
    old = time.time() - 30 * 86400
    Path("configs/eval.yaml").write_text("dataset: imagenet-val\ncrop: resize\n", encoding="utf-8")
    os.utime("configs/eval.yaml", (old, old))
    Path("run_meta_cluster.json").write_text('{"cluster": "a100", "rebuild_path": "historical"}', encoding="utf-8")
    os.utime("run_meta_cluster.json", (old, old))
    assert _said(rec) == [
        f"configs/eval.yaml: changed since {base[:7]}, not committed; nothing in the record cites it",
        f"run_meta_cluster.json: new since {base[:7]}, not committed; nothing in the record cites it",
    ]


def test_a_file_back_to_what_it_was_where_the_count_starts_is_not_listed(rec):
    """A branch left, or a commit reverted: a file the same as at the commit the count starts at is no change."""
    rec("status")
    _git("checkout", "-qb", "seed")
    Path("configs/train.yaml").write_text("optimizer: sgd\nschedule: cosine\nlr: 0.2\n", encoding="utf-8")
    _commit("lr 0.2")
    rec("status")
    _git("checkout", "-q", "-")
    assert _said(rec) == []
    Path("configs/train.yaml").write_text("optimizer: sgd\nschedule: step\nlr: 0.1\n", encoding="utf-8")
    _commit("step schedule")
    assert len(_said(rec)) == 1
    _git("revert", "--no-edit", "HEAD")
    assert _said(rec) == []


def test_a_tracked_config_at_the_top_edited_after_rb_first_looked_is_changed_not_new(rec):
    Path("params.yaml").write_text("seed: 0\n", encoding="utf-8")
    base = _commit("params")
    rec("status")
    Path("params.yaml").write_text("seed: 1\n", encoding="utf-8")
    Path("configs/quant.yaml").write_text("bits: 8\n", encoding="utf-8")
    _commit("seed 1, and the quantizer's bits")
    assert _said(rec) == [
        f"configs/quant.yaml: new since {base[:7]}; nothing in the record cites it",
        f"params.yaml: changed since {base[:7]}; nothing in the record cites it",
    ]


def test_a_config_gone_is_listed_as_gone(rec):
    rec("status")
    base = _git("rev-parse", "HEAD")
    _git("rm", "-q", "configs/eval.yaml")
    _commit("no eval config")
    assert _said(rec) == [f"configs/eval.yaml: gone since {base[:7]}; nothing in the record cites it"]


def test_files_recorded_by_path_stay_recorded_once_committed_unchanged(rec):
    """rb reconcile --apply records each by its path and bytes: a commit of the same bytes changes nothing, a later
    edit shows again."""
    rec("status")
    Path("configs/eval.yaml").write_text("dataset: imagenet-val\ncrop: resize\n", encoding="utf-8")
    Path("run_meta.json").write_text('{"rebuild_path": "historical"}', encoding="utf-8")
    assert "2 changed files recorded by path (configs/eval.yaml, run_meta.json)" in rec("reconcile", "--apply").data["applied"]["line"]
    applied = _git("rev-parse", "HEAD")                          # the count starts again at the commit the apply saw
    _commit("what was recorded")
    assert _said(rec) == []
    Path("run_meta.json").write_text('{"rebuild_path": "rebuilt"}', encoding="utf-8")
    assert _said(rec) == [f"run_meta.json: new since {applied[:7]}; nothing in the record cites it"]


def _later(hours: int) -> str:
    """A commit date that many hours from now, as git reads one."""
    return f"{int(time.time()) + hours * 3600} +0000"


def _second_the_record_began() -> str:
    """The record's first write, to the second, as git reads a commit date."""
    from datetime import datetime
    first = json.loads(Path(".rb/log.jsonl").read_text(encoding="utf-8").splitlines()[0])
    return f"{int(datetime.fromisoformat(first['at']).timestamp())} +0000"


@pytest.mark.parametrize("committed_before", [True, False], ids=["a-commit-at-init", "no-commit-at-init"])
def test_the_count_starts_at_the_commit_rb_init_saw_though_a_later_commit_shares_its_second(workdir, monkeypatch, tmp_path_factory, capsys,
                                                                                            committed_before):
    """git dates a commit to the second, so a commit made just after rb init can carry the record's own second: the
    count starts at the commit rb init saw, and where it saw none, at HEAD when rb first looks, never at that later
    commit."""
    as_the_user(monkeypatch)
    cfg = tmp_path_factory.mktemp("gitconfig") / "config"
    cfg.write_text("[user]\n\temail = ana@example.org\n\tname = Ana\n[commit]\n\tgpgsign = false\n", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(cfg))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    _git("init", "-q")
    Path("configs").mkdir()
    Path("configs/eval.yaml").write_text("dataset: imagenet-val\ncrop: center\n", encoding="utf-8")
    if committed_before:
        _commit("the eval config")
    began_on = _git("rev-parse", "HEAD") if committed_before else None
    assert main(["init", "INT8 ResNet-50 on ImageNet"]) == 0
    capsys.readouterr()
    monkeypatch.setenv("GIT_COMMITTER_DATE", _second_the_record_began())
    monkeypatch.setenv("GIT_AUTHOR_DATE", _second_the_record_began())
    Path("configs/eval.yaml").write_text("dataset: imagenet-val\ncrop: random\n", encoding="utf-8")
    _git("add", "configs")
    _git("commit", "-qm", "random crop, in the record's own second")
    later = str(int(_second_the_record_began().split()[0]) + 5) + " +0000"
    monkeypatch.setenv("GIT_COMMITTER_DATE", later)
    monkeypatch.setenv("GIT_AUTHOR_DATE", later)
    Path("configs/eval.yaml").write_text("dataset: imagenet-val\ncrop: five\n", encoding="utf-8")
    _commit("five crops, seconds later")
    head = _git("rev-parse", "HEAD")
    main(["status", "--json"])
    said = [u["said"] for u in Envelope.model_validate_json(capsys.readouterr().out).data["uncited_changes"]]
    if committed_before:
        assert said == [f"configs/eval.yaml: changed since {began_on[:7]}; nothing in the record cites it"]
    else:                                                   # the first look's HEAD
        assert said == [] and json.loads(_journal_state())["changes"]["base"] == head


def _journal_state() -> str:
    return Path(".git/rb/journal/main/state.json").read_text(encoding="utf-8")


def test_a_clone_without_the_commit_the_last_apply_saw_counts_from_the_newest_commit_before_it(rec, tmp_path_factory, monkeypatch):
    """A squash merge leaves the commit the last rb reconcile --apply saw out of a fresh clone: there the count starts
    at the newest commit before that apply, so a config change committed after it is listed, and what the apply recorded
    is not."""
    rec("status")
    main_branch = _git("rev-parse", "--abbrev-ref", "HEAD")
    _git("checkout", "-qb", "feature")
    Path("configs/train.yaml").write_text("optimizer: adamw\nschedule: cosine\nlr: 0.1\n", encoding="utf-8")
    _commit("adamw")
    assert rec("reconcile", "--apply").data["applied"]["line"] == "1 changed file recorded by path (configs/train.yaml)"
    _commit("the apply")
    monkeypatch.setenv("GIT_COMMITTER_DATE", _later(1))
    _git("checkout", "-q", main_branch)
    _git("merge", "-q", "--squash", "feature")
    _commit("feature, squashed")
    _git("branch", "-qD", "feature")
    Path("configs/eval.yaml").write_text("dataset: imagenet-val\ncrop: random\n", encoding="utf-8")
    _commit("random crop")
    clone = tmp_path_factory.mktemp("clone") / "repo"
    _git("clone", "-q", "--no-local", str(Path.cwd()), str(clone))
    monkeypatch.chdir(clone)
    stand_in = _git("rev-list", "--max-parents=0", "HEAD")              # the newest commit here before the apply
    env = rec("reconcile")
    assert [u["said"] for u in env.data["uncited_changes"]] == [f"configs/eval.yaml: changed since {stand_in[:7]}; nothing in the record cites it"]
    assert env.data["uncited_from_head"] is None
    Path("configs/train.yaml").write_text("optimizer: adamw\nschedule: step\nlr: 0.1\n", encoding="utf-8")
    _commit("step schedule")
    assert [u["path"] for u in rec("status").data["uncited_changes"]] == ["configs/eval.yaml", "configs/train.yaml"]


def test_a_clone_with_no_commit_before_the_last_apply_says_the_count_starts_at_head(rec, tmp_path_factory, monkeypatch):
    """Where a clone holds neither the commit the last apply saw nor any commit before it, the count starts at HEAD,
    and rb reconcile says so, listed or not."""
    rec("status")
    Path("configs/train.yaml").write_text("optimizer: adamw\nschedule: cosine\nlr: 0.1\n", encoding="utf-8")
    _commit("adamw")
    applied = _git("rev-parse", "HEAD")
    rec("reconcile", "--apply")
    _commit("the apply")
    monkeypatch.setenv("GIT_COMMITTER_DATE", _later(1))
    _git("checkout", "-q", "--orphan", "rewritten")
    _commit("the history, rewritten")
    clone = tmp_path_factory.mktemp("clone") / "repo"
    _git("clone", "-q", "--no-local", "--single-branch", "--branch", "rewritten", str(Path.cwd()), str(clone))
    monkeypatch.chdir(clone)
    head = _git("rev-parse", "HEAD")
    env = rec("reconcile")
    assert env.data["uncited_changes"] == [] and env.data["uncited_from_head"] == {"commit": head, "not_in_clone": applied}
    text = rec("reconcile", text=True)
    assert f"Changed .json, .yaml and .toml files are counted from {head[:7]}. This clone does not hold {applied[:7]}, where the count started, or any commit before it." in text
    Path("configs/eval.yaml").write_text("dataset: imagenet-val\ncrop: random\n", encoding="utf-8")
    _commit("random crop")
    assert [u["said"] for u in rec("reconcile").data["uncited_changes"]] == [f"configs/eval.yaml: changed since {head[:7]}; nothing in the record cites it"]


def test_more_changed_files_than_the_cap_are_said_as_more_and_an_apply_records_the_first_thousand(rec):
    """Past the cap, the count says "or more" on every read, the cap line stays, and an apply that records only some
    leaves the count's start where it was, so the rest still show."""
    rec("status")
    ann = Path("data/annotations")
    ann.mkdir(parents=True)
    for i in range(4010):
        (ann / f"val_{i:05d}.json").write_text('{"label": "cat"}', encoding="utf-8")
    _commit("annotations")
    for _ in range(2):
        st = rec("status").data
        assert st["scope"]["cap_hit"] is True and len(st["uncited_changes"]) == 4000
    text = rec("status", text=True)
    assert "cites (4000 or more): rb reconcile lists each by path" in text and "That look stopped at 4000 files" in text
    listed = rec("reconcile", text=True)
    assert "cites (4000 or more):" in listed and "  and 3900 more: rb reconcile --json lists every one" in listed
    assert "the first 1000 of 4000 changed files to record by path" in listed
    applied = rec("reconcile", "--apply").data["applied"]
    assert applied["line"].startswith("1000 of 4000 changed files recorded by path") and applied["uncited_seen"]["cut"] is True
    st = rec("status").data                                      # the first 4000 git listed, less the 1000 recorded
    assert len(st["uncited_changes"]) == 3000 and st["scope"]["cap_hit"] is True
    assert "4000 or more paths changed since" in rec("journal", text=True)


def test_rb_s_own_files_lockfiles_ci_editor_and_vendored_configs_are_left_out(rec):
    rec("status")
    left_out = {
        ".github/workflows/ci.yml": "on: push\n", ".gitlab-ci.yml": "test: {}\n", ".vscode/settings.json": "{}", ".mcp.json": "{}",
        ".claude/skills/rb/config.json": "{}", "package-lock.json": "{}", "pnpm-lock.yaml": "lockfileVersion: 6\n", "conda-lock.yml": "x: 1\n",
        "azure-pipelines.yml": "trigger: [main]\n", "renovate.json": "{}", "pyrightconfig.json": "{}", "node_modules/left-pad/package.json": "{}",
        "vendor/lib/config.json": "{}", "third_party/tool/settings.toml": "a = 1\n", "rb-export/manifest.json": "{}",
        "tsconfig.json": "{}", "tsconfig.build.json": "{}", "cspell.json": "{}", "jest.config.json": "{}", "tox.toml": "a = 1\n", "mypy.toml": "a = 1\n",
        "mkdocs.yml": "site_name: x\n", "build/lib/meta.json": "{}", "dist/meta.json": "{}", "_build/conf.json": "{}",
        "results/fp32_run/README.md": "# FP32 baseline\n", "results/fp32_run/metrics.json": '{"top1": 0.761}',
        "ci/pipeline.yml": "steps: []\n", "tools/ci/azure.yml": "trigger: [main]\n", "bitrise.yml": "workflows: {}\n",
        "codemagic.yaml": "workflows: {}\n", "_config.yml": "theme: minima\n", "target/release/build.json": "{}",
        "coverage.json": '{"totals": {"percent_covered": 91.5}}',
    }
    for rel, body in left_out.items():
        Path(rel).parent.mkdir(parents=True, exist_ok=True)
        Path(rel).write_text(body, encoding="utf-8")
    Path("configs/eval.yaml").write_text("dataset: imagenet-val\ncrop: resize\n", encoding="utf-8")
    # names that only end in the letters of a lockfile are research configs
    Path("configs/resblock.yaml").write_text("blocks: 4\n", encoding="utf-8")
    Path("configs/clock.json").write_text('{"hz": 1}', encoding="utf-8")
    Path("models/residual_block.yaml").parent.mkdir()
    Path("models/residual_block.yaml").write_text("width: 64\n", encoding="utf-8")
    Path("analysis/ci").mkdir(parents=True)                 # a JSON file in a folder named ci is no pipeline
    Path("analysis/ci/top1_bootstrap.json").write_text('{"low": 0.751, "high": 0.763}', encoding="utf-8")
    # a build tool's folder names, below the top, are research folders
    for rel in ("data/target/labels.json", "analysis/dist/top1_hist.json", "data/build/shards.yaml"):
        Path(rel).parent.mkdir(parents=True, exist_ok=True)
        Path(rel).write_text('{"n": 3}' if rel.endswith(".json") else "n: 3\n", encoding="utf-8")
    _git("add", "-A", "-f")
    _commit("tooling, a dependency, a results folder and the eval config")
    st = rec("status").data
    assert [u["path"] for u in st["uncited_changes"]] == [
        "analysis/ci/top1_bootstrap.json", "analysis/dist/top1_hist.json", "configs/clock.json", "configs/eval.yaml", "configs/resblock.yaml",
        "data/build/shards.yaml", "data/target/labels.json", "models/residual_block.yaml"]
    assert any(i["code"] == "unindexed_folder" and i["subject"] == "results/fp32_run/" for i in st["open"])   # its own item says so


def test_a_lockfile_with_numbers_is_not_found_as_a_result(rec):
    Path("package-lock.json").write_text('{"lockfileVersion": 3}', encoding="utf-8")
    Path("conda-lock.yml").write_text("version: 1\n", encoding="utf-8")
    st = rec("status").data
    assert st["found_unrecorded"] == 0
    assert not any("lock" in u for u in rec("reconcile").data["unplaced"])


def test_a_read_takes_a_file_s_numbers_from_the_look_that_hashed_it(rec):
    """The look that hashes a result keeps its numbers beside its mark, so a read never parses an unchanged file again:
    the result is found from what the look kept, also where an earlier rb kept no numbers and the look reads it once."""
    Path("outputs").mkdir()
    result = Path("outputs/int8_seed1.json")
    result.write_text('{"top1": 0.76}', encoding="utf-8")
    assert rec("status").data["found_unrecorded"] == 1
    st_path = Path(".git/rb/journal/main/state.json")
    st = json.loads(st_path.read_text(encoding="utf-8"))
    assert st["files"]["outputs/int8_seed1.json"]["n"] == {"top1": 0.76}
    del st["files"]["outputs/int8_seed1.json"]["n"]                       # as an earlier rb kept it
    st_path.write_text(json.dumps(st), encoding="utf-8")
    assert rec("status").data["found_unrecorded"] == 1
    assert json.loads(st_path.read_text(encoding="utf-8"))["files"]["outputs/int8_seed1.json"]["n"] == {"top1": 0.76}
    result.chmod(0)
    try:
        if os.access(result, os.R_OK):
            pytest.skip("this user reads a file with no read permission")
        [found] = rec("reconcile").data["found"]
        assert (found["path"], found["metrics"]) == ("outputs/int8_seed1.json", {"top1": 0.76})
    finally:
        result.chmod(0o644)
    # more numbers than a look keeps: marked so, never read again by a look, and read by a read that needs them
    Path("outputs/per_class.json").write_text(json.dumps({f"class_{i:03d}": i / 1000 for i in range(300)}), encoding="utf-8")
    assert rec("status").data["found_unrecorded"] == 2
    meta = json.loads(st_path.read_text(encoding="utf-8"))["files"]["outputs/per_class.json"]
    assert "n" in meta and meta["n"] is None
    assert any(u.startswith("outputs/per_class.json holds numbers nothing declares (class_000") for u in rec("reconcile").data["unplaced"])


@pytest.mark.skipif(os.name == "nt", reason="the git put first on PATH here is a shell script")
def test_rb_context_asks_git_where_the_repository_is_once(rec, tmp_path_factory, monkeypatch):
    """Each git process costs every read: the look rb context makes before it reads asks git where the repository is
    once, the question the journal's own place already asked, and never again for the repository's top."""
    rec("status")
    wrap = tmp_path_factory.mktemp("gitwrap")
    calls = wrap / "calls.txt"
    (wrap / "git").write_text(f'#!/bin/sh\nprintf "%s\\n" "$*" >> "{calls}"\nexec "{shutil.which("git")}" "$@"\n', encoding="utf-8")
    (wrap / "git").chmod(0o755)
    monkeypatch.setenv("PATH", f"{wrap}{os.pathsep}{os.environ['PATH']}")
    assert rec("context").data["scope"]["looked_at"]
    asked = calls.read_text(encoding="utf-8").splitlines()
    assert any(" status --porcelain=v2 " in c for c in asked), asked                       # it did look
    assert len([c for c in asked if "rev-parse --git-common-dir" in c]) == 1, asked
    assert not [c for c in asked if "--show-toplevel" in c], asked


def test_a_result_rb_lists_as_found_where_results_land_is_not_counted_again(rec):
    """A file in a folder where results land that rb lists as found is not a changed file too; one at the top that holds
    numbers nothing declares is both, and rb status says how many it counted twice."""
    rec("status")
    Path("outputs").mkdir()
    Path("outputs/fp32_eval.json").write_text('{"top5": 0.93, "latency_ms": 4.1}', encoding="utf-8")
    Path("outputs/fp32_meta.json").write_text('{"host": "a100"}', encoding="utf-8")
    Path("run_metadata.json").write_text('{"recovery_attempts": 1}', encoding="utf-8")
    st = rec("status").data
    assert st["found_unrecorded"] == 2
    assert [u["path"] for u in st["uncited_changes"]] == ["outputs/fp32_meta.json", "run_metadata.json"]
    assert "Changed .json, .yaml or .toml files that nothing in the record cites (2; 1 also counted above): rb reconcile lists each by path" in rec("status", text=True)


def test_a_folder_a_resource_names_cites_the_files_in_it(rec):
    rec("status")
    Path("data/annotations").mkdir(parents=True)
    Path("data/annotations/val_0001.json").write_text('{"label": "cat"}', encoding="utf-8")
    _commit("annotations")
    assert len(_said(rec)) == 1
    rec("resource", "add", "imagenet-val-labels", "--name", "ImageNet validation labels", "--type", "dataset", "--at", "local:data/annotations")
    assert _said(rec) == []


# ---------------------------------------------------------------- the journal says what changed, and only that

def test_a_config_edited_again_in_the_working_tree_is_journaled(rec):
    """A file git lists as changed, edited again: the next look journals it, and rb journal scan says so."""
    rec("status")
    Path("configs/eval.yaml").write_text("dataset: imagenet-val\ncrop: resize\n", encoding="utf-8")
    rec("status")
    Path("configs/eval.yaml").write_text("dataset: imagenet-val\ncrop: resize\nselection: second pass\n", encoding="utf-8")
    line = rec("journal", "scan", text=True)
    assert line.startswith("0 new, 1 changed, 0 gone") and "git moved" not in line
    *_, e = [e for e in rec("journal", "-n", "5").data["entries"] if e.get("path") == "configs/eval.yaml"]
    assert (e["op"], e["change"], e["git"]) == ("file", "changed", "modified")


def test_a_working_tree_change_is_not_called_a_git_move(rec):
    Path("src").mkdir()
    Path("src/quantize.py").write_text("SCALE = 1\n", encoding="utf-8")
    _commit("the quantizer")
    rec("journal", "scan")
    Path("src/quantize.py").write_text("SCALE = 2\n", encoding="utf-8")
    assert rec("journal", "scan", text=True).startswith("0 new, 0 changed, 0 gone, the working tree changed")
    _commit("scale 2")
    assert rec("journal", "scan", text=True).startswith("0 new, 0 changed, 0 gone, git moved")


def test_an_empty_journal_says_when_rb_last_looked_and_outside_git_says_nothing_of_git(rec):
    _leave_git()
    Path("README.md").unlink()                                   # nothing where rb looks: a look writes nothing down
    rec("status")
    text = rec("journal", text=True)
    assert text.startswith("The journal is empty here: rb last looked at ") and "has not looked" not in text
    assert rec("journal", "scan", text=True).startswith("No file where rb looks changed since its last look.\n")


def test_new_bytes_behind_an_old_file_time_and_size_show_again(rec):
    """A file left out, rewritten to the same size with its time put back: its bytes moved, so it is listed again."""
    rec("status")
    Path("configs/eval.yaml").write_text("dataset: imagenet-val\ncrop: resize\n", encoding="utf-8")
    [item] = rec("status").data["uncited_changes"]
    state().dismiss(item["key"], why="a local tweak")
    assert rec("status").data["uncited_changes"] == []
    st = os.stat("configs/eval.yaml")
    time.sleep(0.05)
    Path("configs/eval.yaml").write_text("dataset: imagenet-val\ncrop: zoomed\n", encoding="utf-8")   # the same size
    os.utime("configs/eval.yaml", ns=(st.st_atime_ns, st.st_mtime_ns))
    assert [u["path"] for u in rec("status").data["uncited_changes"]] == ["configs/eval.yaml"]


def test_the_git_state_keeps_the_shape_an_earlier_rb_compares(rec):
    """The tree's changes are kept apart, so the git state an earlier rb compares reads no move on a dirty tree."""
    Path("configs/eval.yaml").write_text("dataset: imagenet-val\ncrop: resize\n", encoding="utf-8")
    rec("status")
    st = json.loads(Path(".git/rb/journal/main/state.json").read_text(encoding="utf-8"))
    assert sorted(st["git"]) == ["branch", "commit", "dirty"] and st["git"]["dirty"] is True
    assert st["tree"]["paths"] == [{"path": "configs/eval.yaml", "change": "modified"}]


# ---------------------------------------------------------------- who leaves an item out

def test_a_word_rb_cannot_tell_the_writer_of_is_refused_and_clears_nothing(rec, monkeypatch):
    rec("status")
    Path("configs/eval.yaml").write_text("dataset: imagenet-val\ncrop: resize\n", encoding="utf-8")
    [item] = rec("status").data["uncited_changes"]
    opened = state()
    monkeypatch.setenv("CODEX_THREAD_ID", "5e7a1c00-0011")
    monkeypatch.setenv("RB_ACTOR", "codex")                      # a name rb cannot read as a writer
    with pytest.raises(Exception, match="RB_ACTOR"):
        opened.dismiss(item["key"], why="tooling")
    monkeypatch.delenv("CODEX_THREAD_ID")
    monkeypatch.delenv("RB_ACTOR")
    assert [u["path"] for u in rec("status").data["uncited_changes"]] == ["configs/eval.yaml"]
    st = json.loads(Path(".git/rb/journal/main/state.json").read_text(encoding="utf-8"))
    assert item["key"] not in st.get("dismissed", {})


def test_rb_journal_lists_only_the_user_s_word_by_path(rec, monkeypatch):
    rec("status")
    Path("configs/eval.yaml").write_text("dataset: imagenet-val\ncrop: resize\n", encoding="utf-8")
    Path("configs/train.yaml").write_text("optimizer: sgd\nschedule: step\nlr: 0.1\n", encoding="utf-8")
    eval_, train = rec("status").data["uncited_changes"]
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("RB_SESSION", "5e7a1c00-0012")
    state(agent="claude-code").dismiss(eval_["key"], why="not research")
    for name in ("CLAUDECODE", "RB_SESSION"):
        monkeypatch.delenv(name)
    state().dismiss(train["key"], why="a scratch schedule")
    Path("outputs").mkdir()
    Path("outputs/int8_seed1.json").write_text('{"top1": 0.76}', encoding="utf-8")
    rec("journal", "scan")
    [found], _ = state().found()
    state().dismiss(found.key, why="a smoke test")
    text = rec("journal", text=True)
    assert 'left out configs/train.yaml: "a scratch schedule"' in text and "not research" not in text and "hidden" not in text
    assert 'left out outputs/int8_seed1.json: "a smoke test"' in text
    assert sum(e["op"] == "dismiss" for e in rec("journal").data["entries"]) == 3
    assert [u["path"] for u in rec("status").data["uncited_changes"]] == ["configs/eval.yaml"]


def test_the_sdk_lists_the_changed_files_and_the_user_leaves_one_out(rec):
    rec("status")
    Path("configs/eval.yaml").write_text("dataset: imagenet-val\ncrop: resize\n", encoding="utf-8")
    rec("status")
    [u] = state().uncited_changes()
    assert u["path"] == "configs/eval.yaml" and u["said"].startswith("configs/eval.yaml: changed since ")
    state().dismiss(u["key"], why="not part of the study")
    assert rec("status").data["uncited_changes"] == [] and state().uncited_changes() == []


# ---------------------------------------------------------------- what a session has been told

def test_a_quiet_command_does_not_tell_writes_it_did_not_print(rec):
    """A write by the user after the session's rb context, which the session's rb status does not print, is told by the
    session's next command."""
    rec("context", agent="5e7a1c00-0021")
    rec("metric", "add", "top5", "--maximize")
    rec("status", agent="5e7a1c00-0021")
    told = rec("show", "int8", agent="5e7a1c00-0021").rb_delta
    assert [(d["kind"], d["code"], d["subject"]) for d in told] == [("write", "add", "top5")]


def test_writes_past_what_the_briefing_prints_are_told_later(rec):
    rec("context", agent="5e7a1c00-0022")
    for i in range(15):
        rec("metric", "add", f"extra{i:02d}", "--maximize")
    md = rec("context", agent="5e7a1c00-0022").data["markdown"]
    assert "extra14" in md and "extra00" not in md                               # Recent activity shows the last twelve
    told = rec("show", "int8", agent="5e7a1c00-0022").rb_delta
    assert [d["subject"] for d in told] == ["extra00", "extra01", "extra02"]


def test_what_a_command_cannot_fit_is_told_by_the_next(rec):
    """More than twelve facts at once: the rest are told by the session's next command, not marked as told."""
    rec("show", "int8", agent="5e7a1c00-0023")
    for i in range(16):
        rec("metric", "add", f"extra{i:02d}", "--maximize")
    first = rec("show", "int8", agent="5e7a1c00-0023")
    assert len(first.rb_delta) == 13 and first.rb_delta[-1]["text"] == "and 4 more, which the session's next command tells"
    assert first.rb_delta[-1]["do"] == "rb log"
    second = rec("show", "int8", agent="5e7a1c00-0023").rb_delta
    assert [d["subject"] for d in second] == [f"extra{i:02d}" for i in range(12, 16)]
    assert rec("show", "int8", agent="5e7a1c00-0023").rb_delta == []


def test_many_changed_files_are_told_as_a_few_and_a_count_after_the_user_s_writes(rec):
    """Fifty changed files nothing cites never keep the user's call from a session: its next command tells the user's
    writes first, then five of the files by path and the rest on one line, once."""
    rec("status")
    ann = Path("data/ann")
    ann.mkdir(parents=True)
    for i in range(50):
        (ann / f"img_{i:04d}.json").write_text('{"label": "cat"}', encoding="utf-8")
    rec("context", agent="5e7a1c00-0026")
    rec("decide", "int8", "park", "-m", "the calibration set is being rebuilt")
    rec("note", "add", "calibration rebuilt from the train split", "-e", "int8")
    told = rec("show", "int8", agent="5e7a1c00-0026").rb_delta
    assert [d["kind"] for d in told[:2]] == ["write", "write"] and [d["code"] for d in told[:2]] == ["decide", "note"]
    named = [d for d in told if d["code"] == "uncited_change"]
    assert [d["subject"] for d in named] == [f"data/ann/img_{i:04d}.json" for i in range(5)] and all(d.get("counted") for d in named)
    [more] = [d for d in told if d["code"] == "uncited_more"]
    assert (more["text"], more["count"], more["do"], more.get("counted")) == ("and 45 more changed .json, .yaml or .toml files that nothing in the record cites", 45, "rb reconcile", True)
    assert len(told) == 8 and not [d for d in told if d["kind"] == "more"]
    shown = rec("show", "int8", agent="5e7a1c00-0026", text=True)
    assert "uncited" not in shown and "data/ann" not in shown                                        # told once
    text = rec("show", "int8", agent="5e7a1c00-0027", text=True)                                   # another session
    assert "  - and 45 more changed .json, .yaml or .toml files that nothing in the record cites   (rb reconcile)" in text


def test_what_the_briefing_counted_is_named_under_its_own_heading(rec):
    Path("outputs").mkdir()
    Path("outputs/int8_seed1.json").write_text('{"top1": 0.76}', encoding="utf-8")
    rec("context", agent="5e7a1c00-0024")
    env = rec("show", "int8", agent="5e7a1c00-0024")
    assert [d.get("counted") for d in env.rb_delta] == [True]
    text = rec("status", agent="5e7a1c00-0025", text=True)         # a quiet first look for another session
    assert "Since this session last looked" not in text
    shown = rec("show", "int8", agent="5e7a1c00-0025", text=True)
    assert "\nCounted before, named now:\n  - outputs/int8_seed1.json: results landed and are not in the record" in shown
    assert "Since this session last looked" not in shown


# ---------------------------------------------------------------- the user's screen

def test_rb_ui_keeps_the_catalogue_on_a_short_screen(rec):
    """On an 80 by 24 terminal the two quiet lines take one line each, in rb's short words, and the catalogue keeps its
    rows; on a tall one they are whole."""
    from rabbit_brain.tui import RBApp

    rec("status")
    Path("configs/eval.yaml").write_text("dataset: imagenet-val\ncrop: resize\n", encoding="utf-8")
    _commit("resize")
    rec("status")
    h = home()
    assert h.uncited_short == "Changed .json/.yaml/.toml files the record does not cite (1): rb reconcile"
    assert h.scope_short.startswith("rb last looked at ") and h.scope_short.endswith(" UTC; rb status says where it looks.")

    async def drawn(size):
        app = RBApp(Path.cwd())
        async with app.run_test(size=size) as pilot:
            await pilot.pause(0.3)
            scope, tree = app.screen.query_one("#scope"), app.screen.query_one("#tree-list")
            return scope.region.height, tree.region.height, str(scope.render())

    lines, rows, said = asyncio.run(drawn((80, 24)))
    # the screen looks again as it opens, maybe a minute after the read above: its time is its own
    stamp = re.compile(r"rb last looked at \d{4}-\d\d-\d\d \d\d:\d\d UTC")
    assert lines == 2 and rows >= 2 and [stamp.sub("@", x) for x in said.splitlines()] == [h.uncited_short, stamp.sub("@", h.scope_short)], said
    lines, _, said = asyncio.run(drawn((140, 40)))
    assert lines > 2 and said.replace("\n", " ").startswith(h.uncited_line)


def test_rb_ui_s_observed_lines_leave_out_what_rb_leaves_out_of_what_changed(rec):
    """Lockfiles, CI configs and rb's own page change beside the results: rb ui's observed lines name the results
    alone, and rb journal still lists every file it saw."""
    rec("status")
    Path("outputs").mkdir()
    Path("outputs/int8_seed1.json").write_text('{"top1": 0.76}', encoding="utf-8")
    for rel, body in {"package-lock.json": "{}", "uv.lock": "version = 1\n", "azure-pipelines.yml": "trigger: [main]\n"}.items():
        Path(rel).write_text(body, encoding="utf-8")
    rec("page")
    assert Path("rb-page.html").is_file()
    rec("journal", "scan")
    live = home().live
    assert live[-1].endswith("new  outputs/int8_seed1.json")
    assert not [line for line in live if any(n in line for n in ("package-lock.json", "uv.lock", "azure-pipelines.yml", "rb-page.html"))]
    journal = rec("journal", text=True)
    assert all(name in journal for name in ("package-lock.json", "uv.lock", "azure-pipelines.yml", "rb-page.html"))


def test_rb_ui_says_when_it_last_looked_after_a_look_that_found_nothing(rec, monkeypatch):
    from rabbit_brain.tui import RBApp

    monkeypatch.setenv("RB_TEST_NOW", "2026-10-14T03:00:00.000+00:00")
    rec("status")

    async def drawn():
        app = RBApp(Path.cwd())
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause(0.3)
            before = str(app.screen.query_one("#scope").render())
            monkeypatch.setenv("RB_TEST_NOW", "2026-10-14T04:00:00.000+00:00")
            app.screen._tick = 4
            app.screen._poll()                     # every 10 s: a look, which finds nothing new
            await pilot.pause(0.3)
            return before, str(app.screen.query_one("#scope").render())

    before, after = asyncio.run(drawn())
    assert "It last looked at 2026-10-14 03:00 UTC." in before.replace("\n", " ")
    assert "It last looked at 2026-10-14 04:00 UTC." in after.replace("\n", " ")


# ---------------------------------------------------------------- words scoped to what rb recorded or watched

def test_the_words_for_what_is_planned_or_has_no_evidence_say_the_record(rec):
    added = rec("experiment", "add", "INT8 with per-channel scales", "--id", "int8pc", "--hypothesis", "per-channel scales close the gap", text=True)
    assert "  Planned: nothing in the record tests it yet, and it shows as a planned direction until its first run or analysis." in added
    said = rec("decide", "int8pc", "park", "-m", "not this quarter", text=True)
    assert "  int8pc has no evidence recorded yet: this call rests on nothing in the record." in said
    md = rec("context").data["markdown"]
    assert "- planned: an experiment nothing in the record tests yet. open: evidence and no call." in md


def test_a_run_no_watched_command_made_says_git_tree_not_the_exact_code(rec):
    Path("README.md").write_text("# INT8 ResNet-50\n\nEdited, not committed.\n", encoding="utf-8")
    Path("outputs").mkdir()
    Path("outputs/int8_seed1.json").write_text('{"top1": 0.76}', encoding="utf-8")
    rid = rec("run", "add", "int8", "--from", "outputs/int8_seed1.json").data["object"]["id"]
    shown = rec("show", rid, text=True)
    assert "the exact code" not in shown.lower() and "  The code when it was recorded: git tree " in shown, shown
    head, tree = _git("rev-parse", "HEAD"), _git("rev-parse", "HEAD^{tree}")
    rid = rec("run", "add", "int8", "top1=0.77", "--commit", head, "--tree", tree).data["object"]["id"]
    shown = rec("show", rid, text=True)
    assert f"  code that made it: {head[:12]} (as given), git tree {tree[:12]} (git diff " in shown and "exact" not in shown, shown


def test_code_too_big_to_keep_is_not_called_the_exact_code(rec):
    Path("scratch").mkdir()
    for i in range(2001):
        (Path("scratch") / f"n_{i:04d}.txt").write_text("x", encoding="utf-8")
    added = rec("run", "add", "int8", "top1=0.76", text=True)
    assert "  The code was not kept: 2001 changed or untracked files were too many to snapshot." in added


def test_a_look_that_finds_nothing_says_where_it_looked(rec):
    rec("journal", "scan")
    assert rec("journal", "scan", text=True).startswith("No file where rb looks changed since its last look, and git did not move.")
