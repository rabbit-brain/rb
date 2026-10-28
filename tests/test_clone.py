"""rb clone starts a new line of work from a bundle: the structure travels, authority does not. If a
clone ever counts the parent's runs, inherits a ruling, or accepts a bundle that fails its own
manifest, this fails."""
import contextlib
import json
import os
import re
import shutil
import signal
import struct
import subprocess
import sys
import time
import zipfile
import zlib
from pathlib import Path

import pytest

from rabbit_brain.models import Envelope
from rbtest import as_the_user, command, environment, home, main, state

HAS_GIT = shutil.which("git") is not None
pytestmark = pytest.mark.skipif(not HAS_GIT, reason="the record is played in a git repository")


def _zip(path: Path, entries: list) -> Path:
    """A zip whose entries say what they are told, each (name, method, the size it declares, the bytes it stores, its
    CRC), laid out as rb export lays one out."""
    out, central = bytearray(), bytearray()
    for name, method, declared, stored, crc in entries:
        nb = name.encode("utf-8")
        central += struct.pack("<IHHHHHHIIIHHHHHII", 0x02014B50, 20, 20, 0, method, 0, 0, crc, len(stored), declared, len(nb),
                               0, 0, 0, 0, 0, len(out)) + nb
        out += struct.pack("<IHHHHHIIIHH", 0x04034B50, 20, 0, method, 0, 0, crc, len(stored), declared, len(nb), 0) + nb + stored
    at = len(out)
    out += central + struct.pack("<IHHHHIIH", 0x06054B50, 0, 0, len(entries), len(entries), len(central), at, 0)
    path.write_bytes(bytes(out))
    return path


def _deflated(data: bytes) -> bytes:
    c = zlib.compressobj(6, zlib.DEFLATED, -15)
    return c.compress(data) + c.flush()


def _own_tmpdir(monkeypatch, tmp_path_factory) -> Path:
    """A temporary directory for rb of the test's own, under each name a system reads it by."""
    tmp = tmp_path_factory.mktemp("tmpdir")
    for name in ("TMPDIR", "TMP", "TEMP"):
        monkeypatch.setenv(name, str(tmp))
    return tmp


def _left_in(tmp: Path) -> list:
    return sorted(p.name for p in tmp.iterdir() if p.name.startswith("rb-clone-"))


@contextlib.contextmanager
def _served(directory: Path, monkeypatch):
    """The files of `directory` at http://127.0.0.1:<port>, while the block runs."""
    import functools
    import http.server
    import threading
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(directory))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()

@pytest.fixture
def rbcli(workdir, monkeypatch, tmp_path_factory, capsys):
    """`rb ... --json` as a human (git names them parent), in a fresh git repository, with no global git identity."""
    as_the_user(monkeypatch)
    cfg = tmp_path_factory.mktemp("gitconfig") / "config"
    cfg.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(cfg))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for name in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL", "EMAIL"):
        monkeypatch.delenv(name, raising=False)       # an identity only the parent's own repository sets
    subprocess.run(["git", "init", "-q"], check=True)
    subprocess.run(["git", "config", "user.email", "parent@example.com"], check=True)
    subprocess.run(["git", "config", "user.name", "parent"], check=True)

    def rb(*argv):
        code = main([*argv, "--json"])
        return code, Envelope.model_validate_json(capsys.readouterr().out)
    return rb


@pytest.fixture
def bundle(rbcli, tmp_path_factory):
    """A parent project with an accepted experiment, an answered question, a criterion its run meets, a resource,
    and an exported bundle."""
    rb = rbcli
    assert rb("init", "the parent project", "--id", "parent-proj")[0] == 0
    assert rb("question", "add", "does it travel", "--id", "q1")[0] == 0
    assert rb("experiment", "add", "the experiment", "--id", "e1", "--hypothesis", "structure travels, authority does not",
              "--question", "q1", "--baseline", "fp32", "--candidate", "int8", "--varies", "precision")[0] == 0
    Path("train.json").write_text('{"optim": {"lr": 0.0001}}', encoding="utf-8")
    Path("data").mkdir()
    Path("data/imagenet.bin").write_bytes(b"the benchmark's bytes")
    subprocess.run(["git", "add", "-A"], check=True)
    subprocess.run(["git", "commit", "-qm", "src"], check=True)
    assert rb("spec", "set", "e1", "lr", "--source", "train.json#optim.lr")[0] == 0     # verified in the parent
    assert rb("metric", "add", "top1", "--maximize", "--alias", "accuracy")[0] == 0
    assert rb("resource", "add", "imagenet", "--type", "dataset", "--name", "ImageNet-1k", "--version", "validation",
              "--file", "data/imagenet.bin", "--at", "s3:s3://lab-bucket/imagenet")[0] == 0
    elsewhere = tmp_path_factory.mktemp("their-machine") / "imagenet.bin"
    elsewhere.write_bytes(b"the benchmark's bytes")
    assert rb("resource", "locate", "imagenet", "local", str(elsewhere))[0] == 0       # another machine's absolute path
    assert rb("criterion", "add", "top1 holds", "-e", "e1", "--metric", "top1", "--at-least", "0.7", "--id", "c1")[0] == 0
    assert rb("run", "add", "e1", "top1=0.76", "--input", "imagenet:eval")[0] == 0
    assert rb("decide", "e1", "accept", "-m", "held in the parent")[0] == 0             # a human's ruling, in the parent
    assert rb("decide", "q1", "accept", "-m", "it travels")[0] == 0                     # answers the question, in the parent
    code, env = rb("export", "--with-record")
    assert code == 0
    return rb, Path(env.data["path"])


def _rb_in(root: Path, *argv, capsys):
    cwd = Path.cwd()
    os.chdir(root)
    try:
        code = main([*argv, "--json"])
        return code, Envelope.model_validate_json(capsys.readouterr().out)
    finally:
        os.chdir(cwd)


def test_structure_travels_and_authority_does_not(bundle, capsys, tmp_path_factory):
    rb, src = bundle
    dest = tmp_path_factory.mktemp("clones") / "mine"
    code, env = rb("clone", str(src), str(dest))
    assert code == 0 and env.data["criteria"] == 1 and env.data["experiments"] == 1
    assert env.data["questions"] == 1 and env.data["metrics"] == 1 and env.data["resources"] == 1
    rec = state(dest)
    assert rec.project.parent is not None and src.as_posix() in rec.project.parent.url
    # the criterion arrived inherited and reads untested: the parent's run does not count here
    st = rec.status()
    c1 = next(f for f in st["findings"] if f["criterion"] == "c1")
    assert c1["origin"] == "inherited" and c1["rule_outcome"] == "untested" and c1["comparison"] is None
    assert any(i["code"] == "inherited" and i["subject"] == "c1" for i in st["open"])
    assert rec.get("c1", "criterion").origin == "inherited"
    assert c1["says"]["headline"]                        # it has words on every surface
    # the parent's decisions did not transfer: the experiment it accepted is planned here, and its question reopens
    assert rec.records("decision") == []
    exp = rec.get("e1", "experiment")
    assert rec.experiment("e1").state_word == "planned" and exp.frozen is None
    assert [(b.id, b.status) for b in home(dest).briefs] == [("q1", "open")]
    assert exp.hypothesis == "structure travels, authority does not" and exp.questions == ["q1"]
    # the experiment is new here, and records which experiment of the parent it reproduces, by uid
    parent_exp = state(src / "record").get("e1", "experiment")
    assert exp.uid != parent_exp.uid
    assert [(r.kind, r.target) for r in exp.relations] == [("reproduces", parent_exp.uid)]
    # the setting's value and where it was stated travelled; what rb read there did not
    lr = next(s for s in exp.settings if s.name == "lr")
    assert lr.status == "inherited" and lr.value == pytest.approx(1e-4)
    assert lr.source is not None and (lr.source.path, lr.source.key) == ("train.json", "optim.lr") and lr.source.resolved is None
    # variants and varies survived with the same ids
    assert {v.name for v in exp.variants} == {"fp32", "int8"} and exp.varies == ["precision"]
    # no run came along
    assert rec.records("run") == []


def test_a_resource_keeps_its_identity_and_leaves_another_machines_paths_behind(bundle, capsys, tmp_path_factory):
    rb, src = bundle
    dest = tmp_path_factory.mktemp("clones") / "mine"
    assert rb("clone", str(src), str(dest))[0] == 0
    theirs = state(src / "record").get("imagenet", "resource")
    rec = state(dest)
    res = rec.get("imagenet", "resource")
    assert (res.uid, res.name, res.version, res.sha256) == (theirs.uid, "ImageNet-1k", "validation", theirs.sha256)
    assert [(x.provider, x.ref) for x in theirs.locations if x.provider == "local" and Path(x.ref).is_absolute()]
    assert [(x.provider, x.ref) for x in res.locations] == [("local", "data/imagenet.bin"), ("s3", "s3://lab-bucket/imagenet")]
    assert rec.availability("imagenet")["state"] == "remote"   # the bytes are not in this checkout; the s3 copy is recorded
    (dest / "data").mkdir()
    (dest / "data" / "imagenet.bin").write_bytes(b"the benchmark's bytes")
    assert rec.availability("imagenet")["state"] == "present"  # a checkout with the bytes at the same place finds them


def test_reproducing_here_flips_the_criterion(bundle, capsys, tmp_path_factory, monkeypatch):
    rb, src = bundle
    dest = tmp_path_factory.mktemp("clones") / "mine"
    assert rb("clone", str(src), str(dest))[0] == 0
    subprocess.run(["git", "init", "-q"], cwd=dest, check=True)
    subprocess.run(["git", "config", "user.email", "me@example.com"], cwd=dest, check=True)
    subprocess.run(["git", "config", "user.name", "me"], cwd=dest, check=True)
    code, env = _rb_in(dest, "run", "add", "e1", "top1=0.81", capsys=capsys)
    assert code == 0
    findings = {f["criterion"]: f for f in env.data["findings"]}
    assert findings["c1"]["says"]["headline"] == "reproduced · point only"      # inherited + met here = reproduced
    code, env = _rb_in(dest, "run", "add", "e1", "top1=0.41", "--again", "--why", "a second run", capsys=capsys)
    rec = state(dest)
    st = {f["criterion"]: f for f in rec.status()["findings"]}
    assert st["c1"]["says"]["headline"] == "not reproduced · point only"    # a failing run here is read, never inherited away


def test_a_tampered_bundle_is_refused_and_nothing_is_written(bundle, capsys, tmp_path_factory):
    rb, src = bundle
    log = src / "record" / ".rb" / "log.jsonl"
    log.write_text(log.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    dest = tmp_path_factory.mktemp("clones") / "mine"
    code, env = rb("clone", str(src), str(dest))
    assert code != 0 and env.errors[0].code == "E_BUNDLE_INVALID"
    assert not dest.exists()


def test_a_clone_never_lands_inside_a_project(bundle, capsys):
    rb, src = bundle
    code, env = rb("clone", str(src), "inside-here")
    assert code != 0 and env.errors[0].code == "E_PROJECT_EXISTS"
    assert not Path("inside-here").exists()


def test_a_published_bundle_clones_from_its_address(bundle, capsys, tmp_path, tmp_path_factory, monkeypatch):
    import functools
    import http.server
    import threading
    rb, src = bundle
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    assert rb("export", "--with-record", "--zip", "--out", str(tmp_path / "pub.zip"))[0] == 0
    (tmp_path / "junk.zip").write_text("not a bundle", encoding="utf-8")
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(tmp_path))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    dest = tmp_path_factory.mktemp("clones") / "from-url"
    try:
        code, env = rb("clone", f"{base}/pub.zip", str(dest))
        assert code == 0
        rec = state(dest)
        assert rec.project.parent.url == f"{base}/pub.zip"     # provenance keeps the address, as given
        assert {f["criterion"] for f in rec.status()["findings"]} == {"c1"}
        code, env = rb("clone", f"{base}/junk.zip", str(dest) + "-2")
        assert code != 0 and env.errors[0].code == "E_BUNDLE_INVALID"
        # what is wrong is said of the address that was given, not of the copy rb fetched it into
        assert env.errors[0].message.startswith(f"{base}/junk.zip is not a zip") and "rb-clone-" not in env.errors[0].message
        code, env = rb("clone", f"{base}/absent.zip", str(dest) + "-3")
        assert code != 0 and env.errors[0].code == "E_BUNDLE_INVALID"
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_a_clone_lands_as_a_repository_of_its_own(bundle, capsys, tmp_path, tmp_path_factory, monkeypatch):
    rb, src = bundle
    # no git identity in this environment: the record is staged, never committed as nobody
    dest = tmp_path_factory.mktemp("clones") / "staged"
    code, env = rb("clone", str(src), str(dest))
    assert code == 0 and "staged" in env.data["git"]
    assert (dest / ".git").exists()
    staged = subprocess.run(["git", "-C", str(dest), "diff", "--cached", "--name-only"],
                            capture_output=True, text=True).stdout
    assert ".rb/project.yaml" in staged
    # with an identity, the record is the first commit and the message names the source
    cfg = tmp_path / "gitcfg-me"
    cfg.write_text("[user]\n\tname = Me\n\temail = me@example.com\n", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(cfg))
    dest2 = tmp_path_factory.mktemp("clones") / "committed"
    code, env = rb("clone", str(src), str(dest2))
    assert code == 0 and "first commit" in env.data["git"]
    log = subprocess.run(["git", "-C", str(dest2), "log", "--format=%s"], capture_output=True, text=True).stdout
    assert log.strip() == f"clone of parent-proj from {src.as_posix()}"


def test_a_clone_is_never_committed_under_an_identity_git_guesses(bundle, tmp_path_factory, monkeypatch):
    """With no identity set, git guesses one: the account's name with $EMAIL, or with user@host where the host name has
    a domain, as on a Mac. rb never commits under it; the record is staged, as with no identity at all."""
    rb, src = bundle
    monkeypatch.setenv("EMAIL", "guessed@example.com")
    dest = tmp_path_factory.mktemp("clones") / "guessed"
    code, env = rb("clone", str(src), str(dest))
    assert code == 0 and "staged" in env.data["git"]
    head = subprocess.run(["git", "-C", str(dest), "rev-parse", "-q", "--verify", "HEAD"], capture_output=True, text=True)
    assert head.returncode != 0 and head.stdout == ""


def test_a_clone_inside_someone_elses_repository_touches_nothing(bundle, capsys, tmp_path_factory):
    rb, src = bundle
    outer = tmp_path_factory.mktemp("clones") / "their-monorepo"
    outer.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=outer, check=True)
    code, env = rb("clone", str(src), str(outer / "vendored"))
    assert code == 0 and "existing repository" in env.data["git"]
    assert not (outer / "vendored" / ".git").exists()               # never a nested repository
    porcelain = subprocess.run(["git", "-C", str(outer), "status", "--porcelain"],
                               capture_output=True, text=True).stdout
    assert porcelain and all(line.startswith("??") for line in porcelain.strip().splitlines())   # their index untouched


def test_a_published_fork_carries_its_lineage(bundle, capsys, tmp_path_factory):
    """A fork cloned from a path on this machine keeps that path in its record and its manifest, and its page does not
    publish it; neither does a path that reads as a javascript: address put one in a link."""
    rb, src = bundle
    paths = [src.as_posix()]
    if sys.platform != "win32":                                            # a file name on Windows holds no colon
        Path("javascript:alert(document.domain)").mkdir()
        assert rb("export", "--with-record", "--out", "javascript:alert(document.domain)/b")[0] == 0
        paths.append("javascript:alert(document.domain)/b")
    for i, given in enumerate(paths):
        dest = tmp_path_factory.mktemp("clones") / f"fork{i}"
        assert rb("clone", given, str(dest))[0] == 0
        assert state(dest).project.parent.url == given
        code, env = _rb_in(dest, "export", "--with-record", "--url", "https://fork.example/mine/", capsys=capsys)
        assert code == 0
        out = Path(env.data["path"])
        man = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
        assert man["parent"]["url"] == given                               # lineage survives republication
        page = (out / "page.html").read_text(encoding="utf-8")
        assert "a fork of" not in page and given not in page and "javascript:" not in page.lower()


def test_a_published_fork_links_its_parent_only_at_a_web_address(bundle, capsys, tmp_path, tmp_path_factory, monkeypatch):
    """A fork's page links the project it was cloned from when that is a web address, traversable backward host by
    host; whatever else the record says it came from stays in the record and its manifest, and is no link. The address
    is kept without the credentials and the signed query it was fetched with, which the download alone needed."""
    rb, src = bundle
    assert rb("export", "--with-record", "--zip", "--out", str(tmp_path / "pub" / "pub.zip"))[0] == 0
    fork = tmp_path_factory.mktemp("clones") / "fork"
    with _served(tmp_path / "pub", monkeypatch) as base:
        signed = base.replace("http://", "http://alice:s3cret@") + "/pub.zip?X-Amz-Signature=s3cret#top"
        code, env = rb("clone", signed, str(fork))
        assert code == 0 and env.data["parent"]["url"] == f"{base}/pub.zip"
    assert state(fork).project.parent.url == f"{base}/pub.zip"
    kept = [f for f in (fork / ".rb").rglob("*") if f.is_file() and b"s3cret" in f.read_bytes()]
    assert kept == []                                                      # nor in the log, nor the note
    first = subprocess.run(["git", "-C", str(fork), "log", "--format=%s"], capture_output=True, text=True).stdout
    assert "s3cret" not in first

    def published() -> tuple:
        code, env = _rb_in(fork, "export", "--with-record", "--url", "https://fork.example/mine/", capsys=capsys)
        assert code == 0
        out = Path(env.data["path"])
        page, man = (out / "page.html").read_text(encoding="utf-8"), (out / "manifest.json").read_text(encoding="utf-8")
        assert "s3cret" not in page and "s3cret" not in man
        return page, json.loads(man), out

    page, man, out = published()
    with zipfile.ZipFile(out / "bundle.zip") as z:
        assert [n for n in z.namelist() if b"s3cret" in z.read(n)] == []   # the whole bundle, the record in it too
    assert man["parent"]["url"] == f"{base}/pub.zip"
    # About's line on the record says it (plain words pass 4: the top bar names the record by its title alone)
    about = page.split('<p class="recid', 1)[1].split("</p>", 1)[0]
    assert f'a fork of <a href="{base}/pub.zip">{base}/pub.zip</a>' in about
    assert f"(cloned from {base}/pub.zip)" in page                         # and the experiment it reproduces says so
    # what the record says it came from is whatever was adopted there, as a clone by an earlier rb kept it: an address
    # is linked without its credentials and query, a javascript: one is no link
    project, experiment = fork / ".rb" / "project.yaml", fork / ".rb" / "experiments" / "e1" / "experiment.yaml"
    for parent, linked in (("https://alice:s3cret@lab.example/inv/bundle.zip?token=s3cret", "https://lab.example/inv/bundle.zip"),
                           ("javascript:alert(document.domain)", None)):
        for f, line, value in ((project, r"  url: .*", parent), (experiment, r'    note: "?cloned from .*', "cloned from " + parent)):
            text = f.read_text(encoding="utf-8")
            said = line.split(":")[0] + ": " + json.dumps(value)
            assert re.search(rf"(?m)^{line}$", text), f
            f.write_text(re.sub(rf"(?m)^{line}$", lambda m: said, text, count=1), encoding="utf-8")
        assert _rb_in(fork, "doctor", "--adopt", "-m", "where it came from", capsys=capsys)[0] == 0
        page, man, _ = published()
        if linked:
            assert man["parent"]["url"] == linked
            assert f'a fork of <a href="{linked}">{linked}</a>' in page.split('<p class="recid', 1)[1].split("</p>", 1)[0] and f"(cloned from {linked})" in page
        else:
            assert man["parent"]["url"] == parent
            assert "a fork of" not in page and "javascript:" not in page.lower()


def test_a_clone_that_fails_says_its_address_without_what_it_carried(rbcli, tmp_path, tmp_path_factory, monkeypatch):
    """Whatever stops a clone of an address is said of the address without the credentials, the signed query and the
    fragment it was given with, which the download alone needed."""
    rb = rbcli
    (tmp_path / "junk.zip").write_text("not a bundle", encoding="utf-8")
    dest = tmp_path_factory.mktemp("clones") / "never"
    with _served(tmp_path, monkeypatch) as base:
        signed = base.replace("http://", "http://alice:s3cret@") + "/{}?X-Amz-Signature=SIGs3cret#top"
        for name, said in (("missing.zip", f"{base}/missing.zip could not be fetched: Not Found"),
                           ("junk.zip", f"{base}/junk.zip is not a zip; a published bundle is the zip rb export --with-record writes.")):
            code, env = rb("clone", signed.format(name), str(dest))
            assert code == 2 and env.errors[0].message == said, name
            assert "s3cret" not in json.dumps([e.model_dump() for e in env.errors]), name
    assert not dest.exists()


def test_the_zip_form_clones_too(bundle, capsys, tmp_path, tmp_path_factory):
    rb, src = bundle
    code, env = rb("export", "--with-record", "--zip", "--out", str(tmp_path / "travel.zip"))
    assert code == 0
    dest = tmp_path_factory.mktemp("clones") / "from-zip"
    code, env = rb("clone", str(tmp_path / "travel.zip"), str(dest))
    assert code == 0
    rec = state(dest)
    assert {f["criterion"] for f in rec.status()["findings"]} == {"c1"}


def test_a_zip_past_what_a_bundle_takes_is_refused_by_its_size(rbcli, capsys, tmp_path, tmp_path_factory):
    """No bundle rb export writes passes 512 MB, zipped or unpacked: rb clone refuses a zip past that before reading it,
    one whose files say together that they unpack past it before unpacking any, and a file the moment it unpacks past
    the size it declares. Each is said of the zip that was given, with the size; a zip that is no zip says so."""
    rb = rbcli
    dest = tmp_path_factory.mktemp("clones") / "never"
    big = tmp_path / "big.zip"
    with open(big, "wb") as f:
        f.truncate(512 * 1024 * 1024 + 1)          # a hole where the system allows one, so the test writes nothing
    try:
        if hasattr(os, "wait4"):
            p = subprocess.Popen([*command(), "clone", str(big), str(dest), "--json"], stdin=subprocess.DEVNULL,
                                 stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=environment())
            out = p.stdout.read()
            p.stdout.close()
            _, status, usage = os.wait4(p.pid, 0)
            p.returncode = code = os.waitstatus_to_exitcode(status)
            env = Envelope.model_validate_json(out)
            # refused before it is read: rb's own peak (bytes on macOS, KiB elsewhere) is far below the file's size. A
            # child started this way reports at least its parent's high-water mark on Linux, so the peak is measured
            # against an rb started the same way that reads nothing (rb version)
            unit = 1 if sys.platform == "darwin" else 1024
            base = subprocess.Popen([*command(), "version", "--json"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL, env=environment())
            _, _, idle = os.wait4(base.pid, 0)
            peak = (usage.ru_maxrss - idle.ru_maxrss) * unit
            assert peak < 128 << 20, (usage.ru_maxrss * unit, idle.ru_maxrss * unit)
        else:
            code, env = rb("clone", str(big), str(dest))
    finally:
        big.unlink()
    assert code == 2 and env.errors[0].code == "E_BUNDLE_INVALID"
    assert env.errors[0].message == f"{big.as_posix()} is over 512 MB; an rb bundle is not."
    garbage = b"\xff" * 64                           # no deflate stream: inflating either file would say that instead
    bomb = _zip(tmp_path / "bomb.zip", [("b/record/.rb/a.bin", 8, 300 << 20, garbage, 0), ("b/record/.rb/b.bin", 8, 300 << 20, garbage, 0)])
    code, env = rb("clone", str(bomb), str(dest))
    assert code == 2 and env.errors[0].code == "E_BUNDLE_INVALID"
    assert env.errors[0].message == f"{bomb.as_posix()} unpacks to over 512 MB; an rb bundle does not."
    zeros = bytes(8 << 20)
    lying = _zip(tmp_path / "lying.zip", [("b/manifest.json", 8, 1000, _deflated(zeros), zlib.crc32(zeros))])
    code, env = rb("clone", str(lying), str(dest))
    assert code == 2 and env.errors[0].code == "E_BUNDLE_INVALID"
    assert env.errors[0].message == f"{lying.as_posix()} holds a file that unpacks past the 1000 bytes it declares; an rb bundle does not."
    one = _zip(tmp_path / "one.zip", [("b/manifest.json", 8, 1, _deflated(zeros), zlib.crc32(zeros))])
    code, env = rb("clone", str(one), str(dest))
    assert env.errors[0].message == f"{one.as_posix()} holds a file that unpacks past the 1 byte it declares; an rb bundle does not."
    junk = tmp_path / "junk.zip"
    junk.write_text("not a zip\n", encoding="utf-8")
    code, env = rb("clone", str(junk), str(dest))
    assert code == 2 and env.errors[0].message == f"{junk.as_posix()} is not a zip; a published bundle is the zip rb export --with-record writes."
    assert not dest.exists()


def test_a_clone_leaves_nothing_in_the_temporary_directory(bundle, capsys, tmp_path, tmp_path_factory, monkeypatch):
    """rb clone unpacks a zip, and fetches an address, into folders of its own under the temporary directory: each goes
    when the clone is done, whether it landed or was refused, and when a download fails."""
    rb, src = bundle
    pub = tmp_path / "pub"
    assert rb("export", "--with-record", "--zip", "--out", str(pub / "pub.zip"))[0] == 0
    with zipfile.ZipFile(pub / "pub.zip") as z:
        files = {n: z.read(n) for n in z.namelist()}
    with zipfile.ZipFile(pub / "tampered.zip", "w") as z:
        for n, data in files.items():
            z.writestr(n, data + b"changed\n" if n.endswith("/README.md") else data)
    (pub / "junk.zip").write_text("not a bundle", encoding="utf-8")
    tmp = _own_tmpdir(monkeypatch, tmp_path_factory)
    clones = tmp_path_factory.mktemp("clones")
    code, env = rb("clone", str(pub / "pub.zip"), str(clones / "from-zip"))
    assert code == 0 and _left_in(tmp) == []
    code, env = rb("clone", str(pub / "tampered.zip"), str(clones / "tampered"))
    assert env.errors[0].message.endswith("does not match its manifest.") and _left_in(tmp) == []   # refused once unpacked
    with _served(pub, monkeypatch) as base:
        code, env = rb("clone", f"{base}/pub.zip", str(clones / "from-url"))
        assert code == 0 and _left_in(tmp) == []
        for name, said in (("absent.zip", "could not be fetched"), ("junk.zip", "is not a zip"), ("tampered.zip", "does not match its manifest")):
            code, env = rb("clone", f"{base}/{name}", str(clones / name))
            assert env.errors[0].code == "E_BUNDLE_INVALID" and said in env.errors[0].message
            assert _left_in(tmp) == [], name


@pytest.mark.skipif(not hasattr(signal, "SIGHUP"), reason="the signals that end a program at someone's request are POSIX's")
def test_a_clone_ended_by_a_signal_takes_its_folder_with_it(rbcli, tmp_path, tmp_path_factory, monkeypatch):
    """Ctrl-C, a request to end (a cancelled CI job, `timeout`) or a terminal that closed, while rb clone downloads:
    rb ends by that signal, as it would have, and the folder it was downloading into goes with it."""
    import http.server
    import threading
    release = threading.Event()

    class Stalls(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", str(1 << 20))
            self.end_headers()
            self.wfile.write(bytes(64 << 10))
            self.wfile.flush()
            release.wait(60)                                               # the rest never comes while the test looks

        def log_message(self, *args):
            pass

    for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    tmp = _own_tmpdir(monkeypatch, tmp_path_factory)
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Stalls)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{httpd.server_address[1]}/bundle.zip"
    ended = []
    try:
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            if signal.getsignal(sig) == signal.SIG_IGN:
                continue                                                   # ignored here, so by rb too: nothing ends it
            dest = tmp_path / f"never-{sig.name}"
            p = subprocess.Popen([*command(), "clone", url, str(dest), "--json"], stdin=subprocess.DEVNULL,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=environment())
            try:
                waited = time.monotonic() + 30
                while not any(f.stat().st_size for f in tmp.glob("rb-clone-*/bundle.zip")):
                    assert p.poll() is None and time.monotonic() < waited, "rb clone never began its download"
                    time.sleep(0.05)
                p.send_signal(sig)
                assert p.wait(timeout=30) == -sig, sig.name                 # ended by the signal, as without rb's catch
            finally:
                if p.poll() is None:
                    p.kill()
                    p.wait()
            assert _left_in(tmp) == [] and not dest.exists(), sig.name
            ended.append(sig.name)
    finally:
        release.set()
        httpd.shutdown()
        httpd.server_close()
    assert ended


def test_a_clone_leaves_the_folders_of_other_clones_alone(bundle, capsys, tmp_path_factory, monkeypatch):
    """rb clone takes away only the folders it made: another clone's stays as it is, with what it holds, however old it
    reads (writing a download into its folder does not touch the folder's own time)."""
    rb, src = bundle
    tmp = _own_tmpdir(monkeypatch, tmp_path_factory)
    other = tmp / "rb-clone-0badc0de"
    other.mkdir()
    (other / "bundle.zip").write_bytes(b"what a download has reached")
    two_days_ago = time.time() - 2 * 24 * 60 * 60
    os.utime(other, (two_days_ago, two_days_ago))
    assert rb("clone", str(src), str(tmp_path_factory.mktemp("clones") / "mine"))[0] == 0
    assert _left_in(tmp) == ["rb-clone-0badc0de"]
    assert (other / "bundle.zip").read_bytes() == b"what a download has reached"


def test_the_clones_line_says_it_for_the_record_alone_and_them_for_its_files_too(workdir, monkeypatch, tmp_path_factory, capsys):
    """MP-1026-2341's check: rb clone's code line says git keeps "it" (the parent's record) when the runs wrote no file
    it takes out, and "them" when it takes out the record and the files the runs wrote."""
    as_the_user(monkeypatch)
    cfg = tmp_path_factory.mktemp("gitconfig") / "config"
    cfg.write_text("[user]\n\tname = ana\n\temail = ana@example.org\n[init]\n\tdefaultBranch = main\n", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(cfg))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("RB_ACTOR", "human:ana")
    code_dir = Path.cwd()

    def git(*args):
        subprocess.run(["git", *args], cwd=code_dir, check=True, capture_output=True)

    def rb(*argv):
        code = main([*argv, "--json"])
        return code, Envelope.model_validate_json(capsys.readouterr().out)

    git("init", "-q")
    assert rb("init", "INT8 ResNet-50 on ImageNet", "--id", "int8-resnet-50", "--no-agents")[0] == 0
    assert rb("experiment", "add", "INT8 against FP32", "--id", "e1", "--baseline", "fp32", "--candidate", "int8", "--varies", "precision")[0] == 0
    assert rb("criterion", "add", "INT8 loses at most a point of top-1", "-e", "e1", "--metric", "change.top1", "--at-least", "-0.01", "--id", "c1")[0] == 0
    assert rb("run", "add", "e1", "fp32.top1=0.7613", "int8.top1=0.7581")[0] == 0
    git("add", "-A")
    git("commit", "-qm", "the record")
    bare = tmp_path_factory.mktemp("published") / "int8.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(code_dir), str(bare)], check=True, capture_output=True)
    git("remote", "add", "origin", str(bare))

    def cloned(name):
        code, env = rb("export", "--with-record", "--out", str(tmp_path_factory.mktemp("b") / "b"))
        assert code == 0
        fresh = tmp_path_factory.mktemp("reader")
        cwd = Path.cwd()
        os.chdir(fresh)
        try:
            assert main(["clone", env.data["path"], name]) == 0
        finally:
            os.chdir(cwd)
        return capsys.readouterr().out

    said = cloned("r1")
    assert "The parent's record is taken out of this branch; git keeps it at that commit," in said, said
    Path("results/s1").mkdir(parents=True)
    Path("results/s1/metrics.json").write_text('{"fp32": {"top1": 0.7609}, "int8": {"top1": 0.7577}}', encoding="utf-8")
    assert rb("run", "add", "e1", "--from", "results/s1/metrics.json")[0] == 0
    git("add", "-A")
    git("commit", "-qm", "a run's file")
    git("push", "-q", str(bare), "HEAD:main")
    said = cloned("r2")
    # the parent's results, never "its runs wrote": the clone also takes out results folders no run named (P's clone-fix5)
    assert "The parent's record, and the one file or folder of its results, are taken out of this branch; git keeps them at that commit," in said, said


def test_rb_export_says_what_a_rerun_does_not_get_of_the_code(workdir, monkeypatch, tmp_path_factory, capsys):
    """C's FIX6 request: rb export says the files this checkout has not committed (a rerun gets the commit without
    them), and that origin, as this clone last saw it, lacks the commit, in rb's own lines; neither when it is not so."""
    as_the_user(monkeypatch)
    cfg = tmp_path_factory.mktemp("gitconfig") / "config"
    cfg.write_text("[user]\n\tname = ana\n\temail = ana@example.org\n[init]\n\tdefaultBranch = main\n", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(cfg))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("RB_ACTOR", "human:ana")
    code_dir = Path.cwd()

    def git(*args):
        return subprocess.run(["git", *args], cwd=code_dir, check=True, capture_output=True, text=True).stdout.strip()

    def said(*argv):
        code = main(list(argv))
        out = capsys.readouterr()
        assert code == 0, out.err
        return out.out

    git("init", "-q")
    Path("configs").mkdir()
    Path("configs/sweep.yaml").write_text("lr: 0.1\n", encoding="utf-8")
    Path("eval.py").write_text("print('top1')\n", encoding="utf-8")
    assert main(["init", "INT8 ResNet-50 on ImageNet", "--id", "int8-resnet-50", "--no-agents", "--json"]) == 0
    capsys.readouterr()
    git("remote", "add", "origin", str(tmp_path_factory.mktemp("published") / "first.git"))
    out = tmp_path_factory.mktemp("exports")
    # no commit yet: no "at" with nothing after it
    first = said("export", "--with-record", "--out", str(out / "first"))
    assert "This checkout has no commit yet: 2 files not committed: configs/sweep.yaml, eval.py.\nA rerun gets no code until it is committed.\n" in first, first
    assert "changes the code at" not in first, first
    git("remote", "remove", "origin")
    git("add", "-A")
    git("commit", "-qm", "code and record")
    bare = tmp_path_factory.mktemp("published") / "int8.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(code_dir), str(bare)], check=True, capture_output=True)
    git("remote", "add", "origin", str(bare))
    git("fetch", "-q", "origin")
    capsys.readouterr()
    clean = said("export", "--with-record", "--out", str(out / "clean"))
    assert "This checkout changes the code" not in clean and "does not hold commit" not in clean, clean
    # one file changed: "1 file"
    Path("configs/sweep.yaml").write_text("lr: 0.2\n", encoding="utf-8")
    head = git("rev-parse", "HEAD")[:10]
    one = said("export", "--with-record", "--out", str(out / "one"))
    assert f"This checkout changes the code at {head}: 1 file not committed: configs/sweep.yaml.\n" in one, one
    assert "A rerun gets the commit without them; commit them first to share them.\n" in one, one
    assert "does not hold commit" not in one, one
    # eleven: the first ten, then how many more
    for i in range(10):
        Path(f"tool{i:02}.py").write_text("x = 1\n", encoding="utf-8")
    many = said("export", "--with-record", "--out", str(out / "many"))
    named = ", ".join(["configs/sweep.yaml", *[f"tool{i:02}.py" for i in range(9)]])
    assert f"This checkout changes the code at {head}: 11 files not committed: {named} and 1 more.\n" in many, many
    # code committed here and never pushed: origin, as this clone last saw it, lacks the commit
    git("add", "-A")
    git("commit", "-qm", "top5 too")
    head = git("rev-parse", "HEAD")[:10]
    gone = said("export", "--with-record", "--out", str(out / "ahead"))
    assert f"origin, as this clone last saw it, does not hold commit {head}: a rerun cannot get the code from {bare} until it is pushed there.\n" in gone, gone
    assert "This checkout changes the code" not in gone, gone
