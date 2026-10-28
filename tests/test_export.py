"""rb export is the open-format promise as a command. It publishes the page alone unless the record is asked for
(--with-record); then the bundle re-opens with rb and computes the same standings from the same bytes, it verifies
against its own manifest, and it needs no rb, no git and no network to read. If any of that stops being true, this
fails."""
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import pytest

from rabbit_brain.models import Envelope
from rbtest import as_the_user, command, environment, main, rbyaml, state
from test_page import repo as page_repo  # noqa: F401 (a fixture: the small fixture's record)
from test_shell_contract import NOT_BUILT, drawn, needs, rb as shell_rb, rb_env

HAS_GIT = shutil.which("git") is not None
pytestmark = pytest.mark.skipif(not HAS_GIT, reason="the record is played in a git repository")


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

    subprocess.run(["git", "remote", "add", "origin", "https://user:sekrit-token@github.com/lab/proj.git"], check=True)
    assert rb("init", "the travelling project")[0] == 0
    Path("papers").mkdir()
    Path("papers/src.txt").write_text("The paper reports a top1 accuracy of 0.83 on the benchmark.\n", encoding="utf-8")
    Path("train.json").write_text('{"optim": {"lr": 0.0001}}', encoding="utf-8")
    monkeypatch.setenv("CLAUDECODE", "1")
    assert rb("experiment", "add", "the experiment", "--id", "e1")[0] == 0
    assert rb("spec", "set", "e1", "lr", "--source", "train.json#optim.lr")[0] == 0          # verified against a file
    assert rb("criterion", "add", "top1 holds", "-e", "e1", "--metric", "top1", "--at-least", "0.7", "--id", "c1")[0] == 0
    assert rb("criterion", "add", "the paper's top1 is 0.83", "-e", "e1", "--metric", "top1", "--equals", "0.83",
              "--tolerance", "0.01", "--source", "papers/src.txt", "--quote", "top1 accuracy of 0.83", "--id", "c2")[0] == 0
    assert rb("run", "add", "e1", "top1=0.76")[0] == 0
    code, env = rb("decide", "e1", "accept", "-m", "x")
    assert code == 0 and env.data["object"]["by"].startswith("agent:")      # the agent's call, recorded as the agent's
    monkeypatch.delenv("CLAUDECODE")
    return rb


def _standings(root: Path) -> dict:
    return {f["criterion"]: f["says"]["headline"] for f in state(root).status()["findings"]}


def differs_from_its_manifest(bundle: Path) -> list:
    """The files that are not what the bundle's manifest says, checked the way anyone can without rb: each file's
    sha256 against the one written beside its name."""
    man = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    return [rel for rel, want in sorted(man["files"].items())
            if not (bundle / rel).is_file() or hashlib.sha256((bundle / rel).read_bytes()).hexdigest() != want]


def test_the_bundle_reopens_and_computes_the_same_standings(repo, capsys, monkeypatch):
    src_root = Path.cwd()
    code, env = repo("export", "--with-record")
    assert code == 0
    bundle = Path(env.data["path"])
    assert bundle.name == "rb-export"
    # the same bytes: rb status inside record/ is the same computation, without git and without the source repo
    assert not (bundle / "record" / ".git").exists()
    assert _standings(bundle / "record") == _standings(src_root)
    # the calls travel with who made them: the agent's accept is the agent's in the bundle too
    assert [d.by.split(":")[0] for d in state(bundle / "record").records("decision")] == ["agent"]
    # the runs are records inside .rb/, and nothing else rides along beside the record, the page and the manifest
    assert sorted(p.name for p in bundle.iterdir()) == ["README.md", "manifest.json", "page.html", "record"]
    # the local caches are not the record
    assert (src_root / ".rb" / "cache").is_dir() and (src_root / ".rb" / ".lock").exists()
    assert not (bundle / "record" / ".rb" / "cache").exists()
    assert not (bundle / "record" / ".rb" / "journal").exists()
    assert not (bundle / "record" / ".rb" / ".lock").exists()
    # the files the record's checks read travel beside it, so nothing reads stale inside the bundle
    assert (bundle / "record" / "papers" / "src.txt").is_file()
    assert (bundle / "record" / "train.json").is_file()
    inside = state(bundle / "record").status()
    caveats = {x["code"] for f in inside["findings"] for x in f.get("caveats", [])}
    assert "stale" not in caveats and "cited_source_changed" not in caveats
    # and the bundle says where the code lives, with credentials scrubbed
    man = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    assert man["repo"]["url"] == "https://github.com/lab/proj.git"
    assert "sekrit" not in json.dumps(man)
    assert sorted(man["sources"]["included"]) == ["papers/src.txt", "train.json"]
    assert "runs" not in man


RECORD_KEEPS = ("The record keeps who made each write (human:<name> or agent:<name>, and the channel it came through), an agent's chat "
                "session, name, version, provider and model, the user's words passed with --words, and each run's host, platform and "
                "branch. Anyone who can read the bundle can read all of it.")


def test_an_export_is_the_page_alone_unless_the_record_is_asked_for(repo, capsys, tmp_path_factory):
    """rb export publishes the page alone, as rb page writes it: no record, no file a check reads, no bundle.zip and nothing
    to rb clone, its manifest naming the page and README alone. An address to clone from takes the record, and rb clone
    of a page says how it was exported."""
    out = tmp_path_factory.mktemp("exports")
    code, env = repo("export", "--out", str(out / "page"))
    assert code == 0 and env.data["with_record"] is False and env.data["url"] is None
    page = out / "page"
    assert sorted(p.name for p in page.iterdir()) == ["README.md", "manifest.json", "page.html"]
    man = json.loads((page / "manifest.json").read_text(encoding="utf-8"))
    assert sorted(man["files"]) == ["README.md", "page.html"] and "sources" not in man and differs_from_its_manifest(page) == []
    assert (env.data["file_count"], env.data["sources_included"], env.data["resources_not_included"]) == (2, [], [])
    for name in ("README.md", "manifest.json", "page.html"):
        assert "rb clone" not in (page / name).read_text(encoding="utf-8"), name
    shown = (page / "page.html").read_text(encoding="utf-8")
    assert "Reproduce this project" not in shown and "top1 holds" in shown
    assert "--with-record" in (page / "README.md").read_text(encoding="utf-8")
    code, env = repo("export", "--zip", "--out", str(out / "page.zip"))
    with zipfile.ZipFile(out / "page.zip") as z:
        assert code == 0 and sorted(z.namelist()) == ["page/README.md", "page/manifest.json", "page/page.html"]
    code, env = repo("clone", str(page), str(out / "a-clone"))
    assert code == 2 and env.errors[0].code == "E_BUNDLE_INVALID" and "exported without --with-record" in env.errors[0].message
    assert not (out / "a-clone").exists()
    # an address is where a reader clones the record from: without the record there is none, and nothing is written
    code, env = repo("export", "--url", "https://lab.example/inv/", "--out", str(out / "nothing"))
    assert code == 2 and env.errors[0].code == "E_USAGE" and "--with-record" in env.errors[0].fix
    assert not (out / "nothing").exists()


def test_rb_export_says_what_the_record_keeps_whenever_it_goes_out(repo, capsys, monkeypatch):
    """A record that goes out keeps who made each write, an agent's session, name, version, provider and model, the
    user's words and each run's host, platform and branch, the words of a call an agent made included: rb export
    --with-record says so every time, and so does the bundle's README. Without the flag, rb says the record stays here."""
    assert main(["export"]) == 0
    said = capsys.readouterr().out
    assert "The record stays here: rb export --with-record carries it too" in said and RECORD_KEEPS not in said
    monkeypatch.setenv("CLAUDECODE", "1")
    assert repo("decide", "e1", "park", "--why", "later", "--words", "park it until the paper is out")[0] == 0
    monkeypatch.delenv("CLAUDECODE")
    assert main(["export", "--with-record"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[2] == RECORD_KEEPS                           # right after what the bundle is and how it reopens
    readme = " ".join(Path("rb-export/README.md").read_text(encoding="utf-8").split())
    assert "keep who made each write" in readme and "chat session" in readme and "provider and model" in readme
    assert "park it until the paper is out" in Path("rb-export/record/.rb/log.jsonl").read_text(encoding="utf-8")


def test_a_file_read_at_a_commit_or_a_runs_directory_travels_as_the_check_reads_it(repo, capsys, tmp_path):
    """The bundle carries no history and no run directories: a file a setting was verified in at a commit travels as it
    is at that commit, and a run's directory as the record.json in it, so the bundle's page and its rb status read
    those settings as they read here, wherever the bundle is written."""
    Path("pinned.json").write_text('{"warmup": 400}', encoding="utf-8")
    Path("out/seed1").mkdir(parents=True)
    Path("out/seed1/record.json").write_text('{"dataset": {"count": 400}}', encoding="utf-8")
    subprocess.run(["git", "add", "-A"], check=True)
    subprocess.run(["git", "commit", "-qm", "configs"], check=True)
    head = subprocess.run(["git", "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
    assert repo("spec", "set", "e1", "warmup", "--source", "pinned.json#warmup", "--commit", head)[0] == 0
    assert repo("spec", "set", "e1", "data.count", "--source", "run:out/seed1#/dataset/count")[0] == 0
    Path("pinned.json").write_text('{"warmup": 0}', encoding="utf-8")      # the working tree moves on; the commit does not
    [here] = state().status()["experiments"]
    assert here["settings_count"]["verified"] == 3
    for out in (None, tmp_path / "outside"):
        code, env = repo("export", "--with-record", *(["--out", str(out), "--no-repo"] if out else []))
        bundle = Path(env.data["path"])
        assert code == 0 and sorted(env.data["sources_included"]) == ["out/seed1/record.json", "papers/src.txt", "pinned.json", "train.json"]
        assert (bundle / "record" / ".rb" / "cache" / "commits" / head / "pinned.json").read_text(encoding="utf-8") == '{"warmup": 400}'
        [inside] = state(bundle / "record").status()["experiments"]
        assert inside["settings_count"] == here["settings_count"]
        page = (bundle / "page.html").read_text(encoding="utf-8")
        assert "settings: 3 verified" in page and "no longer states it" not in page


def test_the_manifest_hashes_every_file_and_catches_a_changed_byte(repo, capsys):
    code, env = repo("export", "--with-record")
    assert code == 0
    bundle = Path(env.data["path"])
    man = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    assert man["format"] == "rb-bundle" and man["version"] == 2 and man["record_format"] == 1
    proj = state().project
    assert man["project"] == {"id": proj.id, "uid": proj.uid, "title": "the travelling project"}
    on_disk = {p.relative_to(bundle).as_posix() for p in bundle.rglob("*") if p.is_file()}
    assert set(man["files"]) == on_disk - {"manifest.json"}
    assert "page.html" in man["files"] and "README.md" in man["files"]
    assert any(k.startswith("record/.rb/") for k in man["files"])
    assert differs_from_its_manifest(bundle) == []
    log = bundle / "record" / ".rb" / "log.jsonl"
    log.write_text(log.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    assert differs_from_its_manifest(bundle) == ["record/.rb/log.jsonl"]
    # and rb reads the manifest the same way: a clone of the changed bundle is refused, naming the file
    code, env = repo("clone", str(bundle), str(bundle.parent / "a-clone"))
    assert code != 0 and env.errors[0].code == "E_BUNDLE_INVALID" and "log.jsonl" in json.dumps(env.errors[0].model_dump())


def test_the_bundle_is_readable_without_rb(repo, capsys):
    code, env = repo("export", "--with-record")
    bundle = Path(env.data["path"])
    readme = (bundle / "README.md").read_text(encoding="utf-8")
    assert "without rb" in readme and "rb status is right" in readme
    assert "\u2014" not in readme                       # no em dashes, anywhere
    page = (bundle / "page.html").read_text(encoding="utf-8")
    assert page.startswith("<!doctype html>") and "top1 holds" in page
    # every record file is Rabbit Brain YAML anyone can open, saying its format and kind first; the rest is JSON
    record_files = list((bundle / "record" / ".rb").rglob("*.yaml"))
    assert len(record_files) >= 5
    for p in record_files:
        text = p.read_text(encoding="utf-8")
        assert text.startswith("rb: 1\nkind: ")
        rbyaml.loads(text)
    for p in (bundle / "record" / ".rb").rglob("*.json"):
        json.loads(p.read_text(encoding="utf-8"))
    for line in (bundle / "record" / ".rb" / "log.jsonl").read_text(encoding="utf-8").splitlines():
        json.loads(line)


def test_the_readme_names_every_place_the_record_keeps_a_file(repo, capsys):
    """The README describes the record as rb keeps it: a criterion, a variant and a note each in a file of its own in
    their experiment's folder, and the project's notes in notes/; and where an earlier rb kept them, which rb still
    reads."""
    for argv in (["question", "add", "does top1 hold?"], ["metric", "add", "top1", "--maximize"], ["note", "add", "on the project"],
                 ["note", "add", "on e1", "-e", "e1"], ["variant", "add", "e1", "base", "--role", "baseline"],
                 ["resource", "add", "evalset", "--type", "dataset", "--name", "the eval set"],
                 ["analysis", "add", "e1", "--compare", "base:top1=0.76"]):
        assert repo(*argv)[0] == 0, argv
    code, env = repo("export", "--with-record")
    bundle = Path(env.data["path"])
    readme = (bundle / "README.md").read_text(encoding="utf-8")
    kept = bundle / "record" / ".rb"
    places = {p.parent.name + "/" if p.parent != kept and p.name != "experiment.yaml" else p.name
              for p in kept.rglob("*.yaml") if "cache" not in p.relative_to(kept).parts}
    assert {"criteria/", "variants/", "notes/", "runs/", "analyses/", "experiment.yaml"} <= places
    assert [p for p in sorted(places) if p not in readme] == []
    earlier = " ".join(readme.split())
    assert "variants and notes in its experiment.yaml" in earlier and "the project's notes in project.yaml" in earlier


SOURCE_CODES = {"stale", "conflict", "source_changed", "cited_source_changed", "analysis_source_changed"}
PAGE_SAYS = ("a source changed", "a source contradicts a setting", "the cited source changed", "the document stating it changed")


def _commit(message: str) -> str:
    subprocess.run(["git", "add", "-A"], check=True)
    subprocess.run(["git", "commit", "-q", "-m", message], check=True)
    return subprocess.run(["git", "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()


def _kept(bundle: Path) -> dict:
    """The files a bundle keeps as they stood at a commit, each with its sha256, as its manifest names them."""
    files = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))["files"]
    return {rel: sha for rel, sha in files.items() if rel.startswith("record/.rb/cache/")}


def _source_readings(root: Path) -> list:
    """What rb status says of the files the record's checks read, as (code, subject): every item and every integrity
    problem about one, a setting no criterion reads included."""
    st = state(root).status()
    said = [*st["open"], *(i for f in st["findings"] for i in f["integrity"])]
    return sorted({(x["code"], x["subject"]) for x in said if x["code"] in SOURCE_CODES})


def _read_at_a_commit(repo) -> str:
    """A setting, a cited number and an analysis's numbers, each verified in a file as it stood at a commit, the files
    moved on or gone since. The commit."""
    Path("configs").mkdir()
    Path("configs/train.json").write_text('{"optim": {"lr": 0.0001}}', encoding="utf-8")
    Path("configs/eval.json").write_text('{"eval": {"iters": 12}}', encoding="utf-8")
    Path("report.md").write_text("# Report\n\nnew vs old top1: 0.02 [0.01, 0.03]\n", encoding="utf-8")
    first = _commit("as it stood")
    Path("configs/train.json").write_text('{"optim": {"lr": 0.0005}}', encoding="utf-8")
    Path("report.md").write_text("# Report\n\nrewritten since\n", encoding="utf-8")
    os.remove("configs/eval.json")
    _commit("moved on")
    for argv in (["spec", "set", "e1", "optim.lr", "--source", "configs/train.json#optim.lr", "--commit", first],
                 ["spec", "set", "e1", "eval.iters", "--source", "configs/eval.json#eval.iters", "--commit", first],
                 ["criterion", "add", "the paper's top1, as it stood", "-e", "e1", "--metric", "top1", "--equals", "0.83", "--tolerance",
                  "0.01", "--source", "papers/src.txt", "--quote", "top1 accuracy of 0.83", "--commit", first, "--id", "c3"],
                 ["analysis", "add", "e1", "--compare", "new/old:top1=0.02[0.01,0.03]", "--source", "report.md", "--commit", first],
                 ["criterion", "add", "new holds against old", "-e", "e1", "--metric", "top1", "--subject", "new", "--reference", "old",
                  "--at-least", "0", "--id", "c4"]):
        code, env = repo(*argv)
        assert code == 0, (argv, env.errors)
    return first


def test_what_a_check_reads_at_a_commit_travels_as_it_stood_there(repo, capsys, tmp_path_factory):
    """A setting, a cited number and an analysis's numbers verified in a file at a commit are checked again at that
    commit on every read, and a bundle has no git history to read it from. The bundle keeps each such file as it stood
    there, in the record's cache and in the manifest, the very bytes whose sha256 the record holds: the bundle, its page
    and a bundle exported from it read them as home does, with git around it or none, rb spec verify there keeps them
    verified, and rb clone takes the bundle as ever."""
    first = _read_at_a_commit(repo)
    assert _source_readings(Path.cwd()) == []
    out = tmp_path_factory.mktemp("exports")
    code, env = repo("export", "--with-record", "--out", str(out / "away"))
    assert code == 0, env.errors
    # configs/eval.json is gone from disk, and only read at the commit, where the bundle keeps it: nothing is missing,
    # and what the bundle carries of the repository's history is named, in the envelope and in the manifest
    assert sorted(env.data["sources_included"]) == ["configs/train.json", "papers/src.txt", "report.md", "train.json"]
    assert env.data["sources_missing"] == []
    at_commit = [{"path": p, "commit": first} for p in ("configs/eval.json", "configs/train.json", "papers/src.txt", "report.md")]
    assert env.data["sources_at_commit"] == at_commit
    bundle = Path(env.data["path"])
    assert json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))["sources"]["at_commit"] == at_commit
    kept = bundle / "record" / ".rb" / "cache" / "commits" / first
    assert sorted(_kept(bundle)) == [f"record/.rb/cache/commits/{first}/{p['path']}" for p in at_commit]
    assert differs_from_its_manifest(bundle) == []
    # anyone can check a copy against the record without rb: its sha256 is the one rb recorded when it verified
    code, shown = repo("show", "e1/optim.lr")
    assert hashlib.sha256((kept / "configs" / "train.json").read_bytes()).hexdigest() == shown.data["object"]["source"]["resolved"]["sha256"]
    # the file as it is now travels too, and is not what the check reads
    assert json.loads((bundle / "record" / "configs" / "train.json").read_text(encoding="utf-8")) == {"optim": {"lr": 0.0005}}
    assert _source_readings(bundle / "record") == []
    page = (bundle / "page.html").read_text(encoding="utf-8")
    assert [s for s in PAGE_SAYS if s in page] == []
    assert "record/.rb/cache/commits/" in (bundle / "README.md").read_text(encoding="utf-8")
    # beside the record, inside the repository that has the commit but not the bundle's paths, it reads the same
    code, env = repo("export", "--with-record")
    assert code == 0 and _source_readings(Path(env.data["path"]) / "record") == []
    # a bundle exported from the bundle, which has no git, keeps the same copies, and says so to git as the bundle did
    again = Path(state(bundle / "record").export(out=str(out / "again"), with_record=True)["path"])
    assert _kept(again) == _kept(bundle)
    assert (again / "record" / ".rb" / ".gitignore").read_bytes() == (bundle / "record" / ".rb" / ".gitignore").read_bytes()
    assert _source_readings(again / "record") == []
    # rb clone takes it as ever: each setting arrives with its source and commit, inherited, nothing read
    code, env = repo("clone", str(bundle), str(out / "cloned"))
    assert code == 0, env.errors
    exp = rbyaml.loads((out / "cloned" / ".rb" / "experiments" / "e1" / "experiment.yaml").read_text(encoding="utf-8"))
    pinned = {s["name"]: s for s in exp["settings"]}["optim.lr"]
    assert pinned["status"] == "inherited" and pinned["source"]["commit"] == first and pinned["source"].get("resolved") is None
    # and re-reading the source inside the bundle keeps it verified, as it does at home
    assert [r["after"] for r in state(bundle / "record").experiment("e1").spec.verify("optim.lr")] == ["verified"]


def test_a_kept_copy_that_is_not_the_file_rb_verified_is_not_read(repo, capsys, tmp_path_factory):
    """rb reads a file kept as it stood at a commit only when it is the file rb verified there, byte for byte, by the
    sha256 the record holds. An edited copy is not read, though it states the same value, nor one with the same text and
    the other line ending: what rests on it reads as it did before bundles kept such files, and the manifest names the
    copy too."""
    first = _read_at_a_commit(repo)
    code, env = repo("export", "--with-record", "--out", str(tmp_path_factory.mktemp("exports") / "away"))
    assert code == 0, env.errors
    bundle = Path(env.data["path"])
    kept = bundle / "record" / ".rb" / "cache" / "commits" / first
    (kept / "configs" / "train.json").write_text('{"optim": {"lr": 1e-4}}', encoding="utf-8")   # the same value, other bytes
    assert _source_readings(bundle / "record") == [("stale", "e1/optim.lr")]
    said = [i["text"] for f in state(bundle / "record").status()["findings"] for i in f["integrity"] if i["subject"] == "e1/optim.lr"]
    assert said and all(f"cannot be read at commit {first}" in t for t in said), said
    report = kept / "report.md"
    report.write_bytes(report.read_bytes().replace(b"\n", b"\r\n"))
    assert {c for c, _ in _source_readings(bundle / "record")} == {"stale", "analysis_source_changed"}
    assert differs_from_its_manifest(bundle) == [f"record/.rb/cache/commits/{first}/{p}" for p in ("configs/train.json", "report.md")]


def test_a_bundle_committed_to_git_and_checked_out_is_the_bundle_still(repo, capsys, tmp_path_factory):
    """The record's own .gitignore leaves rb's cache out of git. A bundle that keeps files there, as they stood at a
    commit, says to git that those go with it, a name the record's other lines leave out (journal/) included, while the
    rest of the cache, where rb writes as it reads the bundle, stays out. Committed beside the record and checked out
    elsewhere, the bundle matches its manifest, rb clone takes it, and it reads as home does."""
    Path("journal").mkdir()
    Path("journal/opt.yaml").write_text("optim:\n  wd: 0.01\n", encoding="utf-8")
    first = _read_at_a_commit(repo)
    assert repo("spec", "set", "e1", "optim.wd", "--source", "journal/opt.yaml#optim.wd", "--commit", first)[0] == 0
    code, env = repo("export", "--with-record")
    assert code == 0, env.errors
    bundle = Path(env.data["path"])
    ignore = (bundle / "record" / ".rb" / ".gitignore").read_text(encoding="utf-8")
    assert ignore == Path(".rb/.gitignore").read_text(encoding="utf-8") + "!cache/\ncache/*\n!cache/commits/\n!cache/commits/**\n"
    _commit("the record and its bundle")
    tracked = subprocess.run(["git", "ls-files", "rb-export/record/.rb/cache"], check=True, capture_output=True, text=True).stdout.split()
    assert sorted(tracked) == sorted(f"rb-export/{rel}" for rel in _kept(bundle))
    assert f"rb-export/record/.rb/cache/commits/{first}/journal/opt.yaml" in tracked
    assert subprocess.run(["git", "check-ignore", "-q", "rb-export/record/.rb/cache/sessions/s1.json"]).returncode == 0
    mate = tmp_path_factory.mktemp("mate") / "proj"
    subprocess.run(["git", "clone", "-q", str(Path.cwd()), str(mate)], check=True)
    assert differs_from_its_manifest(mate / "rb-export") == []
    assert _source_readings(mate / "rb-export" / "record") == []
    code, env = repo("clone", str(mate / "rb-export"), str(tmp_path_factory.mktemp("forks") / "fork"))
    assert code == 0, env.errors


def test_a_bundle_committed_in_its_project_reads_a_later_commit_as_home_does(repo, capsys, tmp_path_factory):
    """A bundle committed beside its record sits in the repository, where git gives the bundle's path at a later commit
    as the earlier bundle held it, not as rb verified the file there: the bundle reads its kept copy, and a bundle
    exported from it keeps every one. One file read at a commit and as it is now, changed in between, reads both ways."""
    first = _read_at_a_commit(repo)
    assert repo("export", "--with-record")[0] == 0
    _commit("the record and its bundle")
    Path("configs/train.json").write_text('{"optim": {"lr": 0.0005, "wd": 0.05}}', encoding="utf-8")
    Path("report.md").write_text("# Report\n\nan intro\n\nnew vs old top5: 0.03 [0.01, 0.05]\n", encoding="utf-8")
    second = _commit("later")
    for argv in (["spec", "set", "e1", "optim.wd", "--source", "configs/train.json#optim.wd", "--commit", second],
                 ["analysis", "add", "e1", "--compare", "new/old:top5=0.03[0.01,0.05]", "--source", "report.md", "--commit", second],
                 ["criterion", "add", "new holds on top5", "-e", "e1", "--metric", "top5", "--subject", "new", "--reference", "old",
                  "--at-least", "0", "--id", "c5"]):
        code, env = repo(*argv)
        assert code == 0, (argv, env.errors)
    Path("configs/train.json").write_text('{"optim": {"lr": 0.0005, "wd": 0.07}}', encoding="utf-8")
    assert repo("spec", "set", "e1", "wd", "--source", "configs/train.json#optim.wd")[0] == 0     # the same file, as it is now
    assert _source_readings(Path.cwd()) == []
    code, env = repo("export", "--with-record")
    assert code == 0, env.errors
    bundle = Path(env.data["path"])
    _commit("the bundle again")
    # git's file at the bundle's path and the later commit is the earlier bundle's, not the one rb verified there
    held = subprocess.run(["git", "show", f"{second}:rb-export/record/configs/train.json"], check=True, capture_output=True, text=True).stdout
    assert json.loads(held) == {"optim": {"lr": 0.0005}}
    assert sorted({k["commit"] for k in env.data["sources_at_commit"]}) == sorted({first, second})
    assert json.loads((bundle / "record" / "configs" / "train.json").read_text(encoding="utf-8")) == {"optim": {"lr": 0.0005, "wd": 0.07}}
    assert _source_readings(bundle / "record") == []
    again = Path(state(bundle / "record").export(out=str(tmp_path_factory.mktemp("exports") / "again"), with_record=True)["path"])
    assert _kept(again) == _kept(bundle) and _source_readings(again / "record") == []
    assert [r["after"] for r in state(bundle / "record").experiment("e1").spec.verify("optim.wd", "wd")] == ["verified", "verified"]


def test_a_clone_too_shallow_for_the_commit_reads_the_file_rb_verified_there(repo, capsys, tmp_path_factory):
    """A clone made with --depth 1, as CI checks a repository out, lacks an older commit a setting and a document were
    verified at. Where the file at its path is still the one rb verified, byte for byte or with the other line ending,
    they read as at home and an export from the clone keeps it; edited since, each reads as changed."""
    Path("pinned.json").write_bytes(b'{"warmup": 400}\n')
    Path("REPORT.md").write_bytes(b"| arm | delta |\n| new vs old | 0.01 [-0.02, 0.04] |\n")
    first = _commit("as it stood")
    assert repo("spec", "set", "e1", "warmup", "--source", "pinned.json#warmup", "--commit", first)[0] == 0
    assert repo("analysis", "add", "e1", "--compare", "new/old:top1=0.01[-0.02,0.04]", "--source", "REPORT.md", "--commit", first, "--id", "a1")[0] == 0
    assert repo("criterion", "add", "new holds against old", "-e", "e1", "--metric", "top1", "--subject", "new", "--reference", "old",
                "--at-least", "-0.05", "--id", "c4")[0] == 0
    _commit("the record")
    clone = tmp_path_factory.mktemp("ci") / "proj"
    subprocess.run(["git", "clone", "-q", "--depth", "1", Path.cwd().as_uri(), str(clone)], check=True)
    assert subprocess.run(["git", "-C", str(clone), "cat-file", "-e", f"{first}^{{commit}}"], capture_output=True).returncode != 0
    assert _source_readings(clone) == []
    assert [r["after"] for r in state(clone).experiment("e1").spec.verify("warmup")] == ["verified"]
    bundle = Path(state(clone).export(out=str(tmp_path_factory.mktemp("exports") / "from-ci"), with_record=True)["path"])
    assert sorted(_kept(bundle)) == [f"record/.rb/cache/commits/{first}/{p}" for p in ("REPORT.md", "pinned.json")]
    assert _source_readings(bundle / "record") == []
    for name in ("pinned.json", "REPORT.md"):
        (clone / name).write_bytes((clone / name).read_bytes().replace(b"\n", b"\r\n"))
    assert _source_readings(clone) == []
    (clone / "pinned.json").write_bytes(b'{"warmup":400}\n')      # the same value, other bytes
    (clone / "REPORT.md").write_bytes(b"| arm | delta |\n| new vs old | 0.02 [-0.01, 0.05] |\n")
    assert _source_readings(clone) == [("analysis_source_changed", "a1"), ("stale", "e1/warmup")]


def test_a_run_directory_travels_by_the_record_json_in_it(repo, capsys, tmp_path_factory):
    """A run source names a directory by the record.json in it. The bundle carries that file like every other source,
    names it among them and in the manifest, and reads the setting there as home does; rb clone takes it as ever."""
    Path("runs/r1").mkdir(parents=True)
    Path("runs/r1/record.json").write_text('{"seeds": {"torch": 1234}}', encoding="utf-8")
    assert repo("spec", "set", "e1", "seeds.torch", "--source", "run:runs/r1#/seeds/torch")[0] == 0
    assert _source_readings(Path.cwd()) == []
    out = tmp_path_factory.mktemp("exports")
    code, env = repo("export", "--with-record", "--out", str(out / "away"))
    assert code == 0 and env.data["sources_missing"] == [] and "runs/r1/record.json" in env.data["sources_included"]
    bundle = Path(env.data["path"])
    man = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    assert "runs/r1/record.json" in man["sources"]["included"] and "record/runs/r1/record.json" in man["files"]
    assert differs_from_its_manifest(bundle) == []
    assert _source_readings(bundle / "record") == []
    code, env = repo("clone", str(bundle), str(out / "cloned"))
    assert code == 0, env.errors
    exp = rbyaml.loads((out / "cloned" / ".rb" / "experiments" / "e1" / "experiment.yaml").read_text(encoding="utf-8"))
    seeds = {s["name"]: s for s in exp["settings"]}["seeds.torch"]
    assert seeds["status"] == "inherited" and seeds["source"]["path"] == "runs/r1" and seeds["source"]["pointer"] == "/seeds/torch"


def test_a_record_that_reads_nothing_at_a_commit_exports_as_before(repo, capsys, tmp_path_factory):
    """Only a file a check reads at a commit is kept as it stood there. A record with none, a setting that names a commit
    rb never verified at included, exports as it always has: no cache in the bundle, no word of one in its README, and
    its sources as they were."""
    first = _commit("as it stood")
    assert repo("spec", "set", "e1", "pinned", "0.0001", "--source", "train.json#optim.lr", "--commit", first, "--no-verify")[0] == 0
    code, env = repo("export", "--with-record", "--out", str(tmp_path_factory.mktemp("exports") / "away"))
    assert code == 0, env.errors
    bundle = Path(env.data["path"])
    assert not (bundle / "record" / ".rb" / "cache").exists() and _kept(bundle) == {}
    assert "cache/commits" not in (bundle / "README.md").read_text(encoding="utf-8")
    assert (bundle / "record" / ".rb" / ".gitignore").read_bytes() == Path(".rb/.gitignore").read_bytes()
    assert sorted(env.data["sources_included"]) == ["papers/src.txt", "train.json"] and env.data["sources_missing"] == []
    assert "sources_at_commit" not in env.data
    assert json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))["sources"] == {"included": ["papers/src.txt", "train.json"], "missing": []}


def test_a_published_export_carries_its_own_reproduction(repo, capsys):
    code, env = repo("export", "--with-record", "--url", "https://lab.example/inv/")
    assert code == 0 and env.data["url"] == "https://lab.example/inv/bundle.zip"
    bundle = Path(env.data["path"])
    page = (bundle / "page.html").read_text(encoding="utf-8")
    assert "Reproduce this project" in page
    assert "rb clone https://lab.example/inv/bundle.zip" in page      # the stranger's whole path, verbatim
    assert "pip install rabbit-brain" in page
    assert "https://github.com/lab/proj.git" in page and "sekrit" not in page   # the code line, scrubbed
    # and --no-repo keeps it out entirely
    code2, env2 = repo("export", "--with-record", "--no-repo", "--out", str(Path(env.data["path"]).parent / "norepo"))
    assert code2 == 0 and env2.data["repo"] is None
    man2 = json.loads((Path(env2.data["path"]) / "manifest.json").read_text(encoding="utf-8"))
    assert "repo" not in man2
    assert "inherited" in page and "\u2014" not in page
    man = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    assert man["url"] == "https://lab.example/inv/bundle.zip"
    assert "rb clone https://lab.example/inv/bundle.zip" in (bundle / "README.md").read_text(encoding="utf-8")
    # serving this one directory publishes both surfaces: the page, and the artifact a stranger clones
    with zipfile.ZipFile(bundle / "bundle.zip") as z:
        names = z.namelist()
    assert any(n.endswith("manifest.json") for n in names) and any("/record/.rb/" in n for n in names)
    assert differs_from_its_manifest(bundle) == [] and "bundle.zip" not in man["files"]   # bundle.zip sits beside the manifest, not in it
    # an unpublished export stays clean of it all
    code, env = repo("export", "--with-record")
    plain_page = (Path(env.data["path"]) / "page.html").read_text(encoding="utf-8")
    assert "Reproduce this project" not in plain_page and "https://" not in plain_page.lower()


def test_the_code_repository_is_recorded_as_git_names_it_without_credentials(repo, capsys, tmp_path_factory):
    """The address of the code a record was played against is published as git names the origin remote, with the
    credentials a remote may carry before its host taken out, whatever its scheme (a remote helper's prefix kept), and
    nothing else about it changed."""
    out = tmp_path_factory.mktemp("exports")
    for i, (remote, published) in enumerate([
        ("hg::https://alice:pw@host/repo", "hg::https://host/repo"),
        ("gcrypt::https://alice:pw@github.com/org/repo.git", "gcrypt::https://github.com/org/repo.git"),
        ("git_ssh://alice:pw@host/r.git", "git_ssh://host/r.git"),
        ("file:///home/me/c#proj/repo.git", "file:///home/me/c#proj/repo.git"),
        ("https://host/repo.git?ref=main", "https://host/repo.git?ref=main"),
        ("git@host:org/repo", "git@host:org/repo"),
    ]):
        subprocess.run(["git", "remote", "set-url", "origin", remote], check=True)
        code, env = repo("export", "--with-record", "--url", "https://lab.example/inv/", "--out", str(out / f"e{i}"))
        assert code == 0 and env.data["repo"]["url"] == published, remote
        assert json.loads((out / f"e{i}" / "manifest.json").read_text(encoding="utf-8"))["repo"]["url"] == published, remote
        assert "alice:pw" not in (out / f"e{i}" / "page.html").read_text(encoding="utf-8"), remote


def test_a_published_address_is_kept_without_what_it_carried(repo, capsys, tmp_path_factory):
    """The address a bundle will be served from is published, and the clone address built from it, without the
    credentials, the signed query or the fragment it was given with: in the answer, the manifest, the README, the page
    and the zip beside them."""
    out = tmp_path_factory.mktemp("exports") / "pub"
    code, env = repo("export", "--with-record", "--out", str(out), "--url", "https://alice:s3cret@lab.example/pub/?X-Amz-Signature=SIGs3cret#top")
    assert code == 0 and env.data["url"] == "https://lab.example/pub/bundle.zip"
    assert json.loads((out / "manifest.json").read_text(encoding="utf-8"))["url"] == "https://lab.example/pub/bundle.zip"
    assert "rb clone https://lab.example/pub/bundle.zip" in (out / "README.md").read_text(encoding="utf-8")
    for name in ("manifest.json", "README.md", "page.html"):
        assert "s3cret" not in (out / name).read_text(encoding="utf-8"), name
    with zipfile.ZipFile(out / "bundle.zip") as z:
        assert [n for n in z.namelist() if b"s3cret" in z.read(n)] == []


def test_a_bundle_past_what_rb_clone_takes_is_refused_naming_what_makes_it_so(repo, capsys, tmp_path):
    """A bundle carries the files the record's checks read, and rb clone takes none past 512 MB. One that would pass it
    is refused, in every form, before the file that makes it so is copied, and the file is named; nothing is left where
    the bundle was to go, and with the file gone from the record's reach the export is written as ever."""
    with open("preds.jsonl", "wb") as f:
        f.write(b'{"cases": 5000}\n')
        f.truncate(513 * 1024 * 1024)               # a hole where the system allows one, so the test writes nothing
    try:
        assert repo("spec", "set", "e1", "eval.cases", "5000", "--source", "preds.jsonl:1", "--no-verify")[0] == 0
        for form in (["--zip", "--out", str(tmp_path / "big.zip")], ["--out", str(tmp_path / "big")],
                     ["--url", "https://lab.example/big/", "--out", str(tmp_path / "big")]):
            code, env = repo("export", "--with-record", *form)
            assert code == 2 and env.errors[0].code == "E_WRITE_FAILED", form
            assert env.errors[0].message == "The bundle would be over 512 MB; an rb bundle is not. It would carry preds.jsonl (513 MB).", form
            assert [p.name for p in tmp_path.iterdir() if p.name.startswith("big")] == [], form
    finally:
        os.remove("preds.jsonl")
    code, env = repo("export", "--with-record", "--zip", "--out", str(tmp_path / "big.zip"))
    assert code == 0 and env.data["sources_missing"] == ["preds.jsonl"]


def _measured(argv: list) -> tuple:
    """`rb <argv> --json` as a process of its own: its exit code, its envelope, and its peak memory in bytes (None where
    the system does not say)."""
    if not hasattr(os, "wait4"):
        r = subprocess.run([*command(), *argv, "--json"], stdin=subprocess.DEVNULL, capture_output=True, env=environment())
        return r.returncode, Envelope.model_validate_json(r.stdout), None
    p = subprocess.Popen([*command(), *argv, "--json"], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                         stderr=subprocess.DEVNULL, env=environment())
    out = p.stdout.read()
    p.stdout.close()
    _, status, usage = os.wait4(p.pid, 0)
    p.returncode = os.waitstatus_to_exitcode(status)
    return p.returncode, Envelope.model_validate_json(out), usage.ru_maxrss * (1 if sys.platform == "darwin" else 1024)


def test_a_file_that_leaves_no_room_for_the_page_is_refused_before_it_is_read(repo, capsys, tmp_path_factory):
    """A bundle keeps room beside its files for its page, README and manifest. A file that leaves less than that is
    refused before it is copied or read, and named, though it is under 512 MB itself."""
    out = tmp_path_factory.mktemp("exports")
    with open("preds.jsonl", "wb") as f:
        f.write(b'{"cases": 5000}\n')
        f.truncate(512 * 1024 * 1024 - 64 * 1024)  # a hole where the system allows one, so the test writes nothing
    try:
        assert repo("spec", "set", "e1", "eval.cases", "5000", "--source", "preds.jsonl:1", "--no-verify")[0] == 0
        code, env, peak = _measured(["export", "--with-record", "--out", str(out / "near")])
    finally:
        os.remove("preds.jsonl")
    assert code == 2 and env.errors[0].code == "E_WRITE_FAILED"
    assert env.errors[0].message == ("The bundle would leave less than 1 MB for its page, README and manifest; an rb bundle "
                                     "holds 512 MB at most. It would carry preds.jsonl (512 MB).")
    # refused before it was read: its peak stays within 128 MB of rb's own, spawned the same way (wait4's peak is the
    # largest of rb and the git it runs, which varies by machine)
    _, _, base = _measured(["status"])
    assert peak is None or base is None or peak - base < 128 << 20, (peak, base)
    assert list(out.iterdir()) == []


def test_a_file_two_names_reach_is_weighed_once(repo, capsys, tmp_path_factory):
    """On a disk that does not tell BIG.bin from big.bin, a setting citing the one and a setting citing the other reach
    one file, which the bundle carries once: it is weighed once, and the export is written."""
    Path("big.bin").write_bytes(b"5000\n")
    if not Path("BIG.bin").exists():
        os.remove("big.bin")
        pytest.skip("this disk tells the two names apart")
    with open("big.bin", "r+b") as f:
        f.truncate(300 * 1024 * 1024)
    try:
        assert repo("spec", "set", "e1", "a", "5000", "--source", "big.bin:1", "--no-verify")[0] == 0
        assert repo("spec", "set", "e1", "d", "5000", "--source", "BIG.bin:1", "--no-verify")[0] == 0
        code, env = repo("export", "--with-record", "--out", str(tmp_path_factory.mktemp("exports") / "once"))
    finally:
        os.remove("big.bin")
    assert code == 0, env.errors
    assert {"BIG.bin", "big.bin"} <= set(env.data["sources_included"])


def _held_on_git(fifo: Path, p: subprocess.Popen, ready, sig) -> bool:
    """Answer every read git makes of its config (a pipe, included from the repository's), until rb ends; when `ready()`
    says rb is where the test wants it while git waits, send rb `sig` first. Whether it was sent."""
    sent, waited = False, time.monotonic() + 60
    while p.poll() is None:
        assert time.monotonic() < waited, "rb never ended"
        try:
            fd = os.open(fifo, os.O_WRONLY | os.O_NONBLOCK)  # opens only while git waits to read it
        except OSError:
            time.sleep(0.01)
            continue
        if not sent and ready():
            p.send_signal(sig)
            sent = True
        os.close(fd)
    return sent


@pytest.mark.skipif(not hasattr(signal, "SIGHUP"), reason="the signals that end a program at someone's request are POSIX's")
def test_an_export_ended_by_a_signal_takes_its_build_with_it(repo, capsys, tmp_path_factory):
    """Ctrl-C, a request to end (a cancelled CI job, `timeout`) or a terminal that closed while rb export builds the
    bundle: rb ends by that signal, as it would have, the folder it built in goes with it, and the export it was to
    replace stays as it was."""
    out = tmp_path_factory.mktemp("exports") / "pub.zip"
    assert repo("export", "--with-record", "--zip", "--out", str(out))[0] == 0
    earlier = out.read_bytes()
    building = lambda: [q.name for q in out.parent.iterdir() if q.name != "pub.zip"]  # noqa: E731
    fifo = tmp_path_factory.mktemp("pipe") / "config"
    os.mkfifo(fifo)
    subprocess.run(["git", "config", "include.path", str(fifo)], check=True)   # rb's git is held mid-build on it
    ended = []
    try:
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            if signal.getsignal(sig) == signal.SIG_IGN:
                continue                                                   # ignored here, so by rb too: nothing ends it
            p = subprocess.Popen([*command(), "export", "--with-record", "--zip", "--out", str(out), "--json"], stdin=subprocess.DEVNULL,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=environment())
            try:
                assert _held_on_git(fifo, p, lambda: building() != [], sig), sig.name
                assert p.wait(timeout=30) == -sig, sig.name                # ended by the signal, as without rb's catch
            finally:
                if p.poll() is None:
                    p.kill()
                    p.wait()
            assert building() == [] and out.read_bytes() == earlier, sig.name
            ended.append(sig.name)
    finally:
        os.remove(fifo)                                                    # so git reads its own config again
        subprocess.run(["git", "config", "--unset", "include.path"], check=True)
    assert ended


def test_regenerating_replaces_and_zip_is_one_file(repo, capsys, tmp_path):
    assert repo("export", "--with-record")[0] == 0
    code, env = repo("export", "--with-record")                      # over its own previous export: replaced, not refused
    assert code == 0
    # a directory that is not an export is never removed
    other = tmp_path / "precious"
    other.mkdir()
    (other / "keep.txt").write_text("mine", encoding="utf-8")
    code, env = repo("export", "--with-record", "--out", str(other))
    assert code != 0 and env.errors[0].code == "E_WRITE_FAILED"
    assert (other / "keep.txt").read_text(encoding="utf-8") == "mine"
    # the zip form holds the same bundle under one top-level name
    code, env = repo("export", "--with-record", "--zip", "--out", str(tmp_path / "travel.zip"))
    assert code == 0
    with zipfile.ZipFile(tmp_path / "travel.zip") as z:
        names = z.namelist()
    assert all(n.startswith("travel/") for n in names)
    assert "travel/manifest.json" in names and "travel/page.html" in names
    assert any(n.startswith("travel/record/.rb/") for n in names)


def test_out_never_replaces_a_file_rb_did_not_write(repo, capsys):
    """Item 19: rb page --out and rb export --zip --out over a research file (a README, a licence) are refused and the
    file stays byte for byte, with its time; rb's own page or zip there is written again, by the ownership ledger or,
    for one the ledger does not keep (an older rb's), by its own marks; one the user wrote over is theirs; --replace
    writes over it, as asked. The SDK and MCP refuse it the same way."""
    import rabbit_brain
    from rabbit_brain import RBError
    from rbtest import Mcp
    Path("README.md").write_text("# the travelling project\n", encoding="utf-8")
    Path("LICENSE.txt").write_text("MIT License\n", encoding="utf-8")
    kept = {p: (Path(p).read_bytes(), os.stat(p).st_mtime_ns) for p in ("README.md", "LICENSE.txt")}
    for argv, where in ((("page", "--out", "README.md"), "README.md"), (("export", "--zip", "--out", "LICENSE.txt"), "LICENSE.txt")):
        code, env = repo(*argv)
        assert code == 2 and env.errors[0].code == "E_WRITE_FAILED", argv
        assert env.errors[0].message == f"{where} is there and rb did not write it, so rb leaves it as it is."
        assert "--replace" in env.errors[0].fix
    with pytest.raises(RBError) as refused:
        rabbit_brain.open().export(out="LICENSE.txt", zip=True)
    assert refused.value.code == "E_WRITE_FAILED"
    client = Mcp()
    try:
        client.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "clientInfo": {"name": "t", "version": "1"}}})
        answer = client.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "page", "arguments": {"out": "README.md"}}})
        assert answer["result"]["isError"]
    finally:
        client.close()
    assert {p: (Path(p).read_bytes(), os.stat(p).st_mtime_ns) for p in kept} == kept
    # rb's own output is written again
    for argv in (("page", "--out", "shared/page.html"), ("export", "--zip", "--out", "shared/page.zip")):
        assert repo(*argv)[0] == 0 and repo(*argv)[0] == 0, argv
    common = Path(subprocess.run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"], capture_output=True, text=True).stdout.strip())
    (common / "rb" / "owned.jsonl").unlink()                     # as an older rb left them: its own marks say whose they are
    for argv in (("page", "--out", "shared/page.html"), ("export", "--zip", "--out", "shared/page.zip")):
        assert repo(*argv)[0] == 0, argv
    # a page the user wrote over is theirs now
    assert repo("page")[0] == 0
    Path("rb-page.html").write_text("my notes on the page\n", encoding="utf-8")
    assert repo("page")[0] == 2 and Path("rb-page.html").read_text(encoding="utf-8") == "my notes on the page\n"
    code, env = repo("page", "--out", "README.md", "--replace")
    assert code == 0 and Path("README.md").read_bytes().startswith(b"<!doctype html>")


def test_an_rb_output_the_user_added_to_is_not_written_over(repo, capsys):
    """rb export's folder with a file the user put in it (a CNAME for hosting it) or one they changed, and rb-page.html
    with their notes added (rb's own marks still in it): rb export and rb page leave them as they are, unless --replace."""
    assert repo("export")[0] == 0
    cname = Path("rb-export") / "CNAME"
    cname.write_text("study.example.org\n", encoding="utf-8")
    code, env = repo("export")
    assert code != 0 and env.errors[0].code == "E_WRITE_FAILED" and "(CNAME)" in env.errors[0].message and cname.is_file()
    cname.unlink()
    page = Path("rb-export") / "page.html"
    page.write_text(page.read_text(encoding="utf-8") + "<p>ana's notes</p>\n", encoding="utf-8")
    code, env = repo("export")
    assert code != 0 and "(page.html)" in env.errors[0].message and page.read_text(encoding="utf-8").endswith("<p>ana's notes</p>\n")
    assert repo("export", "--replace")[0] == 0 and not page.read_text(encoding="utf-8").endswith("<p>ana's notes</p>\n")
    assert repo("export")[0] == 0                                   # rb's own export, as rb wrote it: written again
    assert repo("page")[0] == 0
    mine = Path("rb-page.html").read_text(encoding="utf-8") + "<p>ana's notes</p>\n"
    Path("rb-page.html").write_text(mine, encoding="utf-8")
    code, env = repo("page")
    assert code != 0 and env.errors[0].code == "E_WRITE_FAILED" and "changed since rb wrote it" in env.errors[0].message
    assert Path("rb-page.html").read_text(encoding="utf-8") == mine


# ---------------------------------------------------------------- the shell's export (RB_TEST_SHELL until the switch)
# The page the shell draws for rb export is what the hub serves (BP 2b, guarantee 35). Its renderer is track R's (R1) and
# its model track M's (M0): until tests/fixtures/shell/merged-phases.txt lists them, or RB_SHELL_CONTRACT_MERGED does,
# these skip as "pending <group>", as the contract's tests do; a group listed whose stub still answers fails.

# Raised from 200,000 to 210,000 on the orchestrator's call of Oct 25, 21:55 +08 (design pass 1): the your view and
# reader's view pass and the citations pass (each cited line drawn in its Source view) put the small page at 203-205 KB.
# Raised from 210,000 to 212,000 on the orchestrator's call of Oct 27, 20:00 +08 (FIX8).
SMALL_PAGE = 212_000                       # bytes: the small record's page, as test_page.py holds rb page's
# The raw budget is 2.2 MB for now (decided Oct 21, 00:50 +08, on MP-1020-21's proposal 1); it goes back toward 1.8 MB
# when R and C take SHELL-SPEC 932's saving (the question link's why on each row of an interpretation group) at R7.
# Raised to 2.3 MB on the orchestrator's call of Oct 24, 13:50 +08: plain words lengthen rb's sentences by design.
# Measuring both raw caps with a stated margin, with SHELL-SPEC 932's saving, stays R's task.
# Raised to 2.4 MB raw and 185,000 gzip on the orchestrator's call of Oct 25, 16:00 +08: the your view and reader's view
# pass (each viewer's wording marked) and the citations pass (every cited line drawn in its Source view) add both.
SCALE_RAW, SCALE_GZIP = 2_400_000, 185_000  # bytes: the scale record's page (SHELL-SPEC section 14), gzip at level 9
# rb open's / is 2.4 MB for now (decided Oct 22, 17:55 +08, on MP-1022-1334's proposal 2): the pointer guide and 4b take
# it past 2.3 MB. R's script squeeze after Oct 24 (SHELL-SPEC 932's saving, script.js squeezed) brings it back down.
# Raised to 2.45 MB on the orchestrator's call of Oct 25, 21:55 +08 (design pass 1: your view and reader's view, and
# citations): rb open's / measured 2,402,838 to 2,407,121 bytes on MP-1025-1905's trial.
OPEN_RAW = 2_450_000                       # bytes: rb open's / on the scale record
OPEN_TIMES_STATUS = 3                      # rb open's / on the scale record, against rb status --json there


@pytest.fixture(scope="module")
def shell_record(tmp_path_factory):
    """A record to draw the shell's examples in (RB_TEST_SHELL_MODEL), so what rb export writes is the example's."""
    root = tmp_path_factory.mktemp("shell-export")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    r = shell_rb(root, "init", "INT8 ResNet-50 on ImageNet")
    assert r.returncode == 0, r.stderr
    return root


def hub_refusals(page: str) -> list:
    """The hub's gate as phase 0 ported it (tests/test_shell.py), reading each pattern as JavaScript does: each reason
    the hub has to refuse `page`, empty when it serves it."""
    from test_shell import inert_refusals
    return inert_refusals(page) + foreign_refusals(page)


# The hub's later rule (lib/bundle-verify.ts pageGate, Oct 21; /srv/rb/x-int/handoff/hub-bundle-verify.ts): raw text to
# the parser in HTML, and markup to a browser in SVG or MathML, or the reverse, so none of these inside svg or math.
_RAW_TEXT = ("style", "script", "title", "textarea", "xmp", "iframe", "noembed", "noframes", "noscript", "plaintext")
_SPECIAL = ("script", "style", "title", "textarea", "xmp", "iframe", "noembed", "noframes", "plaintext")
_VOID = {"area", "base", "basefont", "br", "col", "command", "embed", "frame", "hr", "img", "input", "isindex", "keygen", "link",
         "meta", "param", "source", "track", "wbr"}
_INTEGRATION = {"mi", "mo", "mn", "ms", "mtext", "annotation-xml", "foreignObject", "desc", "title"}
_P = {"p"}
_FORM = {"input", "option", "optgroup", "select", "button", "datalist", "textarea"}
_IMPLIES_CLOSE = {"tr": {"tr", "th", "td"}, "th": {"th"}, "td": {"thead", "th", "td"}, "body": {"head", "link", "script"},
                  "a": {"a"}, "li": {"li"}, "p": _P, "option": {"option"}, "optgroup": {"optgroup", "option"},
                  "dd": {"dd", "dt"}, "dt": {"dd", "dt"}, "rt": {"rt", "rp"}, "rp": {"rt", "rp"}, "tbody": {"thead", "tbody"},
                  "tfoot": {"thead", "tbody"},
                  **{h: {"h1", "h2", "h3", "h4", "h5", "h6", "p"} for h in ("h1", "h2", "h3", "h4", "h5", "h6")},
                  **{f: _FORM for f in ("select", "input", "output", "button", "datalist", "textarea")},
                  **{t: _P for t in ("address", "article", "aside", "blockquote", "details", "div", "dl", "fieldset", "figcaption",
                                     "figure", "footer", "form", "header", "hr", "main", "nav", "ol", "pre", "section", "table", "ul")}}
# JavaScript's \s and trim(), which the hub reads CSS with: U+FEFF is a space there, U+0085 is not
_JS_S = "[\t\n\x0b\x0c\r \xa0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff]"
_JS_SPACES = "\t\n\x0b\x0c\r \xa0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff"
_CSS_ESCAPE = re.compile(r"\\(?:([0-9a-fA-F]{1,6})" + _JS_S + r"?|([^\r\n\f0-9a-fA-F])|\r\n|[\r\n\f])")


def _urls_are_local(css: str) -> bool:
    """urlsAreLocal: one forward pass over each url(; one that does not close cleanly fails closed."""
    local = lambda v: v.strip(_JS_SPACES).startswith("#") or re.match(r"data:image/(?:png|gif|jpeg|webp|avif|svg\+xml)[;,]", v.strip(_JS_SPACES), re.I)
    opener = re.compile(r"url" + _JS_S + r"*\(", re.I)
    pos = 0
    while (m := opener.search(css, pos)):
        i = m.end()
        while i < len(css) and css[i] in " \t\n\r\f":
            i += 1
        if i < len(css) and css[i] in "\"'":
            close = css.find(css[i], i + 1)
            if close < 0:
                return False
            value, i = css[i + 1:close], close + 1
            while i < len(css) and css[i] in " \t\n\r\f":
                i += 1
            if i >= len(css) or css[i] != ")":
                return False
        else:
            close = css.find(")", i)
            if close < 0:
                return False
            value, i = css[i:close], close
        if not local(value):
            return False
        pos = i + 1
    return True


def _css_is_inert(css: str) -> bool:
    """cssIsInert: escapes decoded, comments dropped, no @import or fetching function, every url() local."""
    plain = _CSS_ESCAPE.sub(lambda m: chr(min(int(m.group(1), 16) or 0xFFFD, 0x10FFFF)) if m.group(1) else (m.group(2) or ""), css)
    plain = re.sub(r"/\*[\s\S]*?(?:\*/|$)", "", plain)
    if re.search(r"@import\b|(?:image-set|image|expression)" + _JS_S + r"*\(", plain, re.I):
        return False
    return _urls_are_local(plain)


def foreign_refusals(page: str) -> list:
    """The hub's foreign-content rule, ported as its pageGate reads a page through htmlparser2 12: a raw-text element
    inside svg or math; CSS that could load a resource in a style attribute, or in any attribute holding "(" inside svg
    or math or on one. The parser's stack is followed: an opening tag closes what it implies, "/>" closes only in SVG
    or MathML (not inside a foreignObject or another integration point), and an end tag that closes an element
    implicitly while inside svg or math leaves the gate unsure the svg ended, so it reads on as inside one."""
    from html.parser import HTMLParser
    found = []

    class Gate(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.open, self.ctx, self.foreign, self.unsure = [], [None], 0, False

        def pop(self, implied, in_end_tag):
            top = self.open.pop()
            if (top in ("svg", "math") or top in _INTEGRATION) and len(self.ctx) > 1:
                self.ctx.pop()
            if implied and in_end_tag and self.foreign > 0:
                self.unsure = True
            if top in ("svg", "math") and self.foreign > 0:
                self.foreign -= 1

        def name(self, tag):
            if tag == "foreignobject" and (self.ctx[-1] == "svg" or "foreignObject" in self.open):
                return "foreignObject"
            return "img" if tag == "image" and self.ctx[-1] is None else tag

        def handle_starttag(self, tag, attrs, closed=False):
            here, tag = self.ctx[-1], self.name(tag)
            while self.open and self.open[-1] in _IMPLIES_CLOSE.get(tag, ()):
                self.pop(True, False)
            inside = self.foreign > 0 or self.unsure
            if inside and tag.lower() in _RAW_TEXT:
                found.append(f"raw text inside SVG or MathML: <{tag}>")
            svg_or_math = tag in ("svg", "math")
            for name, value in attrs:
                value = value or ""
                if (name == "style" or ((inside or svg_or_math) and "(" in value)) and not _css_is_inert(value):
                    found.append(f"CSS in an attribute loads a resource: {name}={value!r}")
            if tag in _VOID:
                return
            self.open.append(tag)
            if tag in ("svg", "math"):
                self.ctx.append(tag)
            elif tag in _INTEGRATION:
                self.ctx.append(None)
            if svg_or_math:
                self.foreign += 1
            if closed and self.ctx[-1] is not None:
                self.pop(True, False)
            elif here is None and tag in _SPECIAL and tag not in ("script", "style"):
                self.set_cdata_mode(tag)            # raw text to the hub's parser too: no tag inside it

        def handle_startendtag(self, tag, attrs):
            self.handle_starttag(tag, attrs, closed=True)

        def handle_endtag(self, tag):
            tag = self.name(tag)
            if tag in _VOID or tag not in self.open:
                return
            k = len(self.open) - 1 - self.open[::-1].index(tag)
            while len(self.open) > k + 1:
                self.pop(True, True)
            self.pop(False, True)

    Gate().feed(page)
    return found


def test_the_foreign_rule_refuses_what_the_hub_refuses():
    """The port refuses a title in an SVG mark (the figure before Oct 21), a style or script in svg or math, and an
    svg attribute's url() that loads, and passes the same SVG with its words beside it, as the hub does."""
    clean = '<!doctype html><p>x</p><div><svg role="img" aria-label="at least -0.01"><circle class="est" r="5"/>' \
            '<rect fill="url(#g)"/><text>at least -0.01</text></svg><p>calib_images=128: -0.0184</p></div><title>t</title>'
    assert foreign_refusals(clean) == []
    for bad in ("<circle><title>-0.0029</title></circle>", "<style>.a{}</style>", "<foreignObject><script>1</script></foreignObject>",
                '<rect fill="url(https://example.org/a)"/>', '<g filter="u\\72l(//example.org/f)"/>'):
        assert foreign_refusals(clean.replace("<circle", bad + "<circle", 1)), bad
    assert foreign_refusals("<!doctype html><math><title>t</title></math>")
    # </div> closes the svg implicitly: the hub cannot tell where it ended, so a title after it is still refused; so
    # does </foreignObject> close a <g/>, whose "/>" HTML ignores there; an opening <p> closes the one before it
    assert foreign_refusals("<!doctype html><div><svg><g></div><title>t</title>")
    assert foreign_refusals("<!doctype html><svg><foreignObject><g/></foreignObject></svg><title>t</title>")
    assert foreign_refusals("<!doctype html><svg><p><p></p></svg><title>t</title>") == []
    # a value is read with its character references, and a style attribute is CSS anywhere
    assert foreign_refusals('<!doctype html><svg fill="url&#40;//example.org/x)"></svg>')
    assert foreign_refusals('<!doctype html><svg fill="url(&#35;g)"></svg>') == []
    assert foreign_refusals('<!doctype html><p style="background:image(foo.png)">x</p>')
    # CSS is read with JavaScript's spaces, as the hub reads it: U+FEFF is one, U+0085 is not
    for css, refused in (("url(\x85#x)", True), ("u\\72\xa0l(x)", True), ("url\ufeff(x)", True), ("url(\ufeff#x)", False)):
        assert bool(foreign_refusals(f'<!doctype html><p style="{css}">x</p>')) == refused, css


def shown(page: str) -> str:
    """What a page shows and hands on: its text and its values with their character references read, no script or CSS."""
    import html
    return html.unescape(re.sub(r"<(script|style)\b[^>]*>.*?</\1\s*>", " ", page, flags=re.S | re.I))


def as_viewer(page: str, viewer: str) -> str:
    """The export as `viewer` reads it (OWNER-READER, R's views.rs): a wording marked for other viewers (data-vw) cut
    out, as shell.css hides it, and the owner's wording of an attribute (`<name>-owner`) too, but for the owner."""
    out, rest = [], page
    while (i := rest.find(' data-vw="')) >= 0:
        open_ = rest.rfind("<", 0, i)
        names_end = rest.index('"', i + 10)
        tag = re.match(r"<(\w+)", rest[open_:]).group(1)
        if viewer in rest[i + 10:names_end].split():
            out.append(rest[:i])
            rest = rest[names_end + 1:]
            continue
        out.append(rest[:open_])
        depth, j = 0, open_
        while True:
            o, c = rest.find(f"<{tag}", j + 1), rest.index(f"</{tag}>", j + 1)
            if 0 <= o < c:
                depth, j = depth + 1, o
            elif depth:
                depth, j = depth - 1, c
            else:
                rest = rest[c + len(tag) + 3:]
                break
    page = "".join(out) + rest
    return page if viewer == "owner" else re.sub(r' [\w-]+-owner="[^"]*"', "", page)


def _exported(root: Path, env: dict, tmp: Path, *more: str) -> str:
    """page.html as rb export writes it in `root` with RB_TEST_SHELL; rb failing is an error, never the gate's answer."""
    out = tmp / f"export-{len(list(tmp.iterdir()))}"
    r = subprocess.run([*command(), "export", "--out", str(out), *more, "--json"], cwd=root, env={**env, "RB_TEST_SHELL": "1"},
                       capture_output=True, encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL, timeout=600)
    assert NOT_BUILT not in r.stdout + r.stderr, r.stdout
    assert r.returncode == 0 and json.loads(r.stdout)["ok"], (r.returncode, r.stdout[-2000:], r.stderr[-2000:])
    return (out / "page.html").read_text(encoding="utf-8")


def test_the_shell_export_is_what_the_hub_serves(shell_record):
    """The Owner example drawn by rb export is a page the hub serves: it runs and loads nothing, while the text that
    would trip the hub's gate reads as it does (a run's one_cycle=1, a note's " online=1" and "javascript:"), and its
    <html> names by sha256 the one script rb page inlines."""
    needs("R1")
    page, error = drawn(shell_record, "export", "owner")
    assert page, error
    assert hub_refusals(page) == []
    assert "<script" not in page.lower()
    for text in (" one_cycle=1", " online=1", "javascript: link"):
        assert text in shown(page), text
    on_page, error = drawn(shell_record, "page", "owner")
    assert on_page, error
    scripts = re.findall(r"<script>(.*?)</script>", on_page, flags=re.S)
    # until the shell's script is written (A1), rb page inlines none, and the export names those empty bytes
    shell_script = (Path(__file__).parent.parent / "rust" / "src" / "shell" / "assets" / "script.js").read_text(encoding="utf-8")
    assert len(scripts) == (1 if shell_script.strip() else 0), on_page[:300]
    named = re.findall(r'<html\b[^>]*\sdata-shell="([^"]*)"', page)
    assert named == ["sha256:" + hashlib.sha256((scripts[0] if scripts else shell_script).encode("utf-8")).hexdigest()], page[:300]


def test_a_tests_document_is_exported_as_given(shell_record, tmp_path):
    """rb export draws a test's document (RB_TEST_SHELL_MODEL) as it is given: one that holds reproduce_url carries
    Reproduce this project with that address, though no --url is given; only the model's own document is published."""
    needs("R3")
    doc = json.loads((Path(__file__).parent / "fixtures" / "shell" / "int8-demo.owner.view.json").read_text(encoding="utf-8"))
    doc["reproduce_url"] = "https://lab.example/int8/bundle.zip"
    model = tmp_path / "doc.json"
    model.write_text(json.dumps(doc), encoding="utf-8")
    page = _exported(shell_record, rb_env(RB_TEST_SHELL_MODEL=str(model)), tmp_path)
    assert 'id="reproduce"' in page and "rb clone https://lab.example/int8/bundle.zip" in shown(page)


def test_the_export_of_the_int8_demo_is_inert(request, tmp_path):
    """rb export of the INT8 demo record, through the model and the renderer, the page alone and with its record:
    the hub serves each page it writes, and the demo's own text that trips the gate (onnx_top1), and a per-run setting in words (one cycle 1), is
    on it. rb sync sends the first."""
    needs("M0", "R1")
    demo = request.getfixturevalue("int8_demo")
    for more in ((), ("--with-record",)):
        page = _exported(demo.root, demo.env, tmp_path, *more)
        assert hub_refusals(page) == [], more
        assert "onnx_top1" in shown(page) and "one cycle 1" in shown(page), more


def _you_on_calls(text: str) -> list:
    """Each line of rb's own words on a page that says "you" or "your" about a call. What someone wrote (a title, a
    note, a quote, words as given: .au, q.quote, .asgiven, and text in quotation marks) is theirs, never rb's voice."""
    text = re.sub(r'<span class="au">[^<]*</span>|<q class="quote">.*?</q>|<div class="asgiven">.*?</div>', " ", text, flags=re.S)
    text = re.sub(r'"[^"<>\n]*"|\u201c[^\u201d]*\u201d', " ", text)
    blocks = r"</?(?:p|li|ul|ol|h[1-6]|div|dt|dd|dl|section|article|nav|summary|details|header|footer|main|aside|td|th|tr|table|pre|br)\b[^>]*>"
    text = re.sub(r"<[^>]+>", "", re.sub(blocks, "\n", text, flags=re.I))
    lines = [" ".join(ln.split()) for ln in text.splitlines()]
    about = r"\b(?:calls?|confirm\w*|decid\w*|terminal|reason|wait\w*)\b"
    return [ln for ln in lines if re.search(r"\byour?\b", ln, re.I) and re.search(about, ln, re.I)]


def test_an_export_says_open_calls_never_your_call(request, tmp_path):
    """An export (rb export, what rb sync uploads) cannot know who reads it, and its readers make no calls: its calls
    are Open calls and an open call, and nothing on it says Your call, waits on you or your terminal. The cards are
    C's; the view, its nav and the Overview's link are R's (C's request, Polish 1), checked once the export draws
    Open calls as the view's name."""
    needs("M0", "R1")
    demo = request.getfixturevalue("int8_demo")
    drawn = _exported(demo.root, demo.env, tmp_path)
    # as it is opened from disk, and as a share link's reader reads it: the hub's owner reads Your call (OWNER-READER)
    page = as_viewer(drawn, "none")
    assert _you_on_calls(shown(as_viewer(drawn, "reader"))) == []
    cards = re.findall(r'<article class="card call".*?</article>', page, flags=re.S)
    assert cards, "the demo has open calls"
    for card in cards:
        said = shown(card)
        assert ">Open call<" in said and "It is run with rb in a terminal; rb records the call and the reason." in said, said[:400]
        assert "owner" not in said and "their own terminal" not in said, said[:400]
        assert _you_on_calls(said) == [], _you_on_calls(said)
    if 'id="open-calls"' not in page and "Open calls (" not in shown(page):
        pytest.skip("waits on R's views.rs: the export's calls view says Open calls (C's request, Polish 1)")
    assert _you_on_calls(shown(page)) == []


def test_the_export_of_the_scale_record_is_inert(request, tmp_path):
    """rb export of the scale record (141 runs, 60 criteria, fixed design cells among them) is a page the hub serves:
    its figures carry no raw-text element inside their SVG, and each design cell's value is beside its figure."""
    needs("M0", "R1")
    scale = request.getfixturevalue("scale_record")
    page = _exported(scale.root, scale.env, tmp_path)
    assert hub_refusals(page) == []
    assert '<svg class="fig"' in page


def test_old_pages_are_still_rbs(tmp_path):
    """A page an earlier rb wrote (page.rs's) and one the shell writes are each rb's by their own marks, where the
    ownership ledger has no line for them, as an older rb left it: rb page writes over either, and rb remove names
    either as rb's page."""
    needs("R1")
    root = tmp_path / "rec"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    assert shell_rb(root, "init", "INT8 ResNet-50 on ImageNet").returncode == 0
    common = Path(subprocess.run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"], cwd=root, capture_output=True,
                                 text=True, check=True).stdout.strip())
    page = root / "rb-page.html"
    shell = {"RB_TEST_SHELL": "1", "RB_TEST_SHELL_MODEL": str(Path(__file__).parent / "fixtures" / "shell" / "int8-demo.owner.view.json")}
    for env in ({}, shell, {}):
        (common / "rb" / "owned.jsonl").unlink(missing_ok=True)            # as an older rb left the page: its marks alone say whose
        before = page.read_bytes() if page.exists() else None
        r = shell_rb(root, "page", **env)
        assert r.returncode == 0, (env, r.stdout[-1500:], r.stderr[-1500:])
        assert page.read_bytes() != before
        (common / "rb" / "owned.jsonl").unlink(missing_ok=True)
        plan = json.loads(shell_rb(root, "remove", "--dry-run", "--json").stdout)["data"]
        assert {"path": "rb-page.html", "why": "rb's page, as rb page wrote it"} in [{k: x[k] for k in ("path", "why")} for x in plan["remove"]], plan


def _gzipped(text: str) -> int:
    import gzip
    return len(gzip.compress(text.encode("utf-8"), compresslevel=9))


def _median_ratio(read, other, n: int = 3) -> float:
    """How many times `other` `read` takes: the median of `n` rounds, each timing one read of each back to back after
    one untimed read of each, so load on the box weighs on both sides of a round alike (MP-1026-2341: a busy CI box
    once made rb open's / read 3.85 s against rb status's 1.27 s, read minutes apart)."""
    import statistics

    def seconds(f):
        start = time.perf_counter()
        f()
        return time.perf_counter() - start
    read()
    other()
    return statistics.median(seconds(read) / seconds(other) for _ in range(n))


def test_the_small_fixtures_pages_are_small(page_repo, monkeypatch, tmp_path):
    """The small fixture (test_page.py's record: one question, one experiment, two criteria, one run) drawn by the shell
    stays under 212,000 bytes (SMALL_PAGE), its rb page and its rb export alike (guarantee 29). The budget is that record's: the
    Owner example document, with every kind of card, is no small fixture."""
    needs("M0", "R1")
    monkeypatch.setenv("RB_TEST_SHELL", "1")
    code, env = page_repo("page")
    assert code == 0, env
    page = Path(env.data["path"]).read_text(encoding="utf-8")
    assert 'class="nodes"' in page and len(page.encode("utf-8")) < SMALL_PAGE
    code, env = page_repo("export", "--out", str(tmp_path / "export"))
    assert code == 0, env
    assert len((tmp_path / "export" / "page.html").read_bytes()) < SMALL_PAGE


def test_size_budget(request, tmp_path):
    """The shell's pages stay within their budgets: the scale record's (141 runs, 60 criteria, about 700 writes) under
    2.2 MB raw for now and 170 KB gzip; rb open's / there under 2.2 MB for now, sent as it is though gzip is asked for, within 3 times
    what rb status --json takes on the same record."""
    needs("M0", "R1")
    scale = request.getfixturevalue("scale_record")
    page = _exported(scale.root, scale.env, tmp_path)
    raw, gz = len(page.encode("utf-8")), _gzipped(page)
    assert raw < SCALE_RAW and gz < SCALE_GZIP, (raw, gz)
    env = {**scale.env, "RB_TEST_SHELL": "1"}

    def status():
        r = subprocess.run([*command(), "status", "--json"], cwd=scale.root, env=env, capture_output=True, stdin=subprocess.DEVNULL, timeout=600)
        assert r.returncode in (0, 1), r.stderr[-500:]
    said = tmp_path / "open.txt"
    with open(said, "wb") as err:
        proc = subprocess.Popen([*command(), "open", "--port", "0"], cwd=scale.root, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=err)
    try:
        deadline = time.monotonic() + 60
        while not (m := re.search(r"^rb open · (http://[0-9.]+:\d+)/$", said.read_text(encoding="utf-8", errors="replace"), flags=re.M)):
            assert proc.poll() is None and time.monotonic() < deadline, said.read_text(encoding="utf-8", errors="replace")
            time.sleep(0.05)
        import urllib.request
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        got = {}

        def home():
            ask = urllib.request.Request(m.group(1) + "/", headers={"Accept-Encoding": "gzip, deflate"})
            with opener.open(ask, timeout=120) as resp:
                got["encoding"], got["body"] = resp.headers.get("Content-Encoding"), resp.read()
        times = _median_ratio(home, status)
        assert got["encoding"] is None and len(got["body"]) < OPEN_RAW, (got["encoding"], len(got["body"]))
        assert times <= OPEN_TIMES_STATUS, times
    finally:
        proc.send_signal(signal.SIGINT) if sys.platform != "win32" else proc.terminate()
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


def test_a_bundles_page_quotes_its_citations_as_the_export_does(tmp_path):
    """CITE: rb export --with-record draws the bundle's page from the bundle's copy of the record, which has no git; its
    citations are read from the checkout it was exported from, so a tracked README's cited line is quoted there as on
    the plain export, and a log line is still not quoted on either."""
    root = tmp_path / "int8"
    root.mkdir()
    git = lambda *a: subprocess.run(["git", *a], cwd=root, check=True, capture_output=True)
    git("init", "-q")
    git("config", "user.email", "ana@example.org")
    git("config", "user.name", "ana")
    (root / "README.md").write_text("# INT8 ResNet-50 on ImageNet\nINT8 keeps top-1 within 1 point of FP32.\n", encoding="utf-8")
    (root / "logs").mkdir()
    (root / "logs" / "eval.log").write_text("top1 0.7597\n", encoding="utf-8")
    git("add", "README.md", "logs/eval.log")
    git("commit", "-q", "-m", "README")
    for argv in (["init", "INT8 ResNet-50 on ImageNet"], ["note", "add", "the target is README.md:2, as logs/eval.log:1 shows"]):
        r = shell_rb(root, *argv, RB_ACTOR="human:ana")
        assert r.returncode == 0, r.stderr
    pages = {}
    for name, extra in [("plain", []), ("bundle", ["--with-record"])]:
        r = shell_rb(root, "export", *extra, "--out", str(tmp_path / name), RB_ACTOR="human:ana")
        assert r.returncode == 0, r.stderr
        pages[name] = (tmp_path / name / "page.html").read_text(encoding="utf-8")
    for name, page in pages.items():
        src = page.split('id="cite-README.md:2"', 1)[1].split('<div class="card ', 1)[0]
        assert "INT8 keeps top-1 within 1 point of FP32." in src and "Not quoted on a shared page" not in src, (name, src[:600])
        log = page.split('id="cite-logs/eval.log:1"', 1)[1].split('<div class="card ', 1)[0]
        assert "Not quoted on a shared page" in log and "top1 0.7597" not in log, (name, log[:600])


def _git(*argv, cwd=None):
    return subprocess.run(["git", *argv], check=True, capture_output=True, text=True, cwd=cwd).stdout.strip()


def test_the_manifest_names_what_the_working_tree_changes_of_its_commit(repo, capsys, tmp_path_factory):
    """A rerun gets the code at the bundle's commit, so the manifest names what the checkout changed of it: the paths
    git status names (modified, added, deleted, untracked; not ignored), sorted, from the repository's root. The record
    and the results folders rb reads travel in the bundle and are left out; a clean checkout says []."""
    Path("configs").mkdir()
    Path("configs/sweep.yaml").write_text("lr: 0.1\n", encoding="utf-8")
    Path("eval.py").write_text("print(1)\n", encoding="utf-8")
    Path(".gitignore").write_text("scratch/\n", encoding="utf-8")
    _git("add", "-A")
    _git("commit", "-qm", "code")
    out = tmp_path_factory.mktemp("exports")
    code, env = repo("export", "--with-record", "--out", str(out / "clean"))
    assert code == 0 and env.data["repo"]["changed"] == []
    # what rb writes in the checkout is its own, never the code's: its skill and server, a page, an export, one being built
    assert repo("skill", "add", "--agents-md", "--yes")[0] == 0 and Path("AGENTS.md").is_file()
    assert repo("page")[0] == 0 and repo("export", "--with-record")[0] == 0 and repo("export", "--zip", "--out", "rb-page.zip")[0] == 0
    for _ in range(2):
        code, env = repo("export", "--with-record", "--out", "bundles/b1")
        assert code == 0 and env.data["repo"]["changed"] == [], env.data["repo"]
    assert json.loads(Path("bundles/b1/manifest.json").read_text(encoding="utf-8"))["repo"]["changed"] == []
    assert json.loads((out / "clean" / "manifest.json").read_text(encoding="utf-8"))["repo"]["changed"] == []
    # Look 7's copy: a config modified, a results folder untracked, and what is ignored or the record's own
    Path("configs/sweep.yaml").write_text("lr: 0.2\n", encoding="utf-8")
    Path("results/r013_worked_s1").mkdir(parents=True)
    Path("results/r013_worked_s1/metrics.json").write_text('{"top1": 0.76}', encoding="utf-8")
    Path("scratch").mkdir()
    Path("scratch/notes.txt").write_text("ignored\n", encoding="utf-8")
    assert repo("note", "add", "the held-out set is not in the pretraining data", "-e", "e1")[0] == 0
    code, env = repo("export", "--with-record", "--out", str(out / "changed"))
    man = json.loads((out / "changed" / "manifest.json").read_text(encoding="utf-8"))
    assert code == 0 and man["repo"]["changed"] == ["configs/sweep.yaml"] == env.data["repo"]["changed"], man["repo"]
    # an untracked file, a deletion and a rename are named too, each path once, sorted
    Path("tools.py").write_text("x = 1\n", encoding="utf-8")
    _git("rm", "-q", "eval.py")
    _git("mv", "train.json", "train2.json")
    code, env = repo("export", "--with-record", "--out", str(out / "more"))
    assert env.data["repo"]["changed"] == sorted({"configs/sweep.yaml", "eval.py", "tools.py", "train.json", "train2.json"}), env.data["repo"]
    assert "at_origin" not in env.data["repo"]      # origin's branches never seen here: rb cannot tell, and claims nothing


def test_a_forks_export_names_the_code_commit_origin_holds(workdir, monkeypatch, tmp_path_factory, capsys):
    """A clone made by rb clone sits on the parent's code with its own record committed on top, which origin never
    holds. Its export names the parent's commit, so a clone of the fork's bundle gets the code; once the fork commits
    code of its own that origin lacks, the manifest names HEAD and says the code is not at origin."""
    as_the_user(monkeypatch)
    cfg = tmp_path_factory.mktemp("gitconfig") / "config"
    cfg.write_text("[user]\n\temail = ana@example.com\n\tname = ana\n", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(cfg))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")

    def rb(*argv):
        code = main([*argv, "--json"])
        return code, Envelope.model_validate_json(capsys.readouterr().out)

    w = Path.cwd()
    parent = w / "parent"
    parent.mkdir()
    monkeypatch.chdir(parent)
    _git("init", "-q")
    Path("eval.py").write_text("print('top1')\n", encoding="utf-8")
    _git("add", "-A")
    _git("commit", "-qm", "code")
    assert rb("init", "INT8 ResNet-50", "--no-agents")[0] == 0
    assert rb("experiment", "add", "INT8 vs FP32 on ImageNet", "--id", "e1")[0] == 0
    _git("add", "-A")
    _git("commit", "-qm", "record")
    _git("clone", "-q", "--bare", str(parent), str(w / "origin.git"))
    _git("remote", "add", "origin", str(w / "origin.git"))
    _git("fetch", "-q", "origin")
    head = _git("rev-parse", "HEAD")
    code, env = rb("export", "--with-record", "--out", str(w / "bundle"))
    assert code == 0 and env.data["repo"]["commit"] == head and "at_origin" not in env.data["repo"]
    monkeypatch.chdir(w)
    code, env = rb("clone", str(w / "bundle"), "fork")
    assert code == 0 and env.data["code"]["got"], env.data["code"]
    monkeypatch.chdir(w / "fork")
    assert _git("rev-parse", "HEAD") != head and _git("rev-parse", "HEAD~1") == head
    code, env = rb("export", "--with-record", "--out", str(w / "fbundle"))
    man = json.loads((w / "fbundle" / "manifest.json").read_text(encoding="utf-8"))
    assert code == 0 and man["repo"]["commit"] == head and "at_origin" not in man["repo"], man["repo"]
    monkeypatch.chdir(w)
    code, env = rb("clone", str(w / "fbundle"), "fork2")
    assert code == 0 and env.data["code"]["got"] and env.data["code"]["commit"] == head, env.data["code"]
    # code the fork committed and origin never got: the commit is HEAD, and the manifest says origin lacks it
    monkeypatch.chdir(w / "fork")
    Path("eval.py").write_text("print('top1', 'top5')\n", encoding="utf-8")
    _git("commit", "-qam", "top5 too")
    code, env = rb("export", "--with-record", "--out", str(w / "fbundle2"))
    assert code == 0 and env.data["repo"]["commit"] == _git("rev-parse", "HEAD") and env.data["repo"]["at_origin"] is False, env.data["repo"]


def test_what_the_tree_changes_is_named_from_the_repositorys_root(workdir, monkeypatch, tmp_path_factory, capsys):
    """A record in a folder of its repository names what the tree changes from the repository's root, a name with a
    space as git gives it, and leaves a checkout nested in it out; a repository with no commit names no commit. Past
    any number of record-only commits origin lacks, the commit named is still the one origin holds."""
    as_the_user(monkeypatch)
    cfg = tmp_path_factory.mktemp("gitconfig") / "config"
    cfg.write_text("[user]\n\temail = ana@example.com\n\tname = ana\n", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(cfg))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")

    def rb(*argv):
        code = main([*argv, "--json"])
        return code, Envelope.model_validate_json(capsys.readouterr().out)

    w = Path.cwd()
    top = w / "lab"
    (top / "int8").mkdir(parents=True)
    monkeypatch.chdir(top)
    _git("init", "-q")
    _git("remote", "add", "origin", str(w / "origin.git"))
    Path("train eval.py").write_text("print(1)\n", encoding="utf-8")
    monkeypatch.chdir(top / "int8")
    assert rb("init", "INT8 ResNet-50", "--no-agents")[0] == 0
    code, env = rb("export", "--with-record", "--out", str(w / "b0"))
    assert code == 0 and "commit" not in env.data["repo"] and env.data["repo"]["changed"] == ["train eval.py"], env.data["repo"]
    monkeypatch.chdir(top)
    _git("add", "-A")
    _git("commit", "-qm", "code")
    _git("clone", "-q", "--bare", str(top), str(w / "origin.git"))
    _git("fetch", "-q", "origin")
    base = _git("rev-parse", "HEAD")
    (top / "int8" / "nested").mkdir()
    _git("init", "-q", cwd=top / "int8" / "nested")
    (top / "int8" / "nested" / "a.py").write_text("x = 1\n", encoding="utf-8")
    Path("train eval.py").write_text("print(2)\n", encoding="utf-8")
    for i in range(205):
        _git("commit", "-q", "--allow-empty", "-m", f"record {i}")
    monkeypatch.chdir(top / "int8")
    # a commit of the record alone, read as git names paths whatever the user's diff.relative says
    assert rb("note", "add", "the held-out set is not in the pretraining data")[0] == 0
    _git("add", "-A", "--", ".rb")
    _git("commit", "-qm", "record")
    _git("config", "diff.relative", "true")
    code, env = rb("export", "--with-record", "--out", str(w / "b1"))
    assert code == 0 and env.data["repo"]["changed"] == ["train eval.py"], env.data["repo"]
    assert env.data["repo"]["commit"] == base and "at_origin" not in env.data["repo"], env.data["repo"]
