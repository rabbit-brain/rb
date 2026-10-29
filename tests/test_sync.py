"""rb sync is a private upload of the record's page to the account signed in on this machine, and of the record itself
only with --with-record: then the uploaded zip is the bundle whose page already carries the address the record will
have. Either is marked private. rb login is the device flow, whose credential is kept 0600 and overridden by
RB_HUB_TOKEN; rb logout revokes and removes it. Neither refuses an agent. rb publish is gone, and says what replaced
it. The hub here is a stub that checks exactly what the real one checks first: the bytes arrive whole and the manifest
inside names what came, a bundle's pointing at the address the hub gave."""
import io
import json
import os
import re
import shutil
import tempfile
import signal
import subprocess
import threading
import time
import zipfile
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from rabbit_brain.models import Envelope
from rbtest import as_the_user, command, environment, main

HAS_GIT = shutil.which("git") is not None
pytestmark = pytest.mark.skipif(not HAS_GIT, reason="the record is played in a git repository")


class _Hub(BaseHTTPRequestHandler):
    polls = 0
    revoked = None
    received: dict = {}
    held = None              # (arrived, release): an upload that has come is held, unanswered, until it is released
    asked = None             # (arrived, release): the same for a whoami, which an upload asks before it builds the bundle
    unavailable = 0          # how many polls for the token to come are answered 503
    refuse = 0               # how many uploads to come are answered 503, as a hub out of service answers
    puts: list = []          # when each upload came, and whether it was taken

    def do_GET(self):
        if self.path == "/api/hub/whoami":
            if _Hub.asked:
                arrived, release = _Hub.asked
                _Hub.asked = None
                arrived.set()
                release.wait(60)
            if not (self.headers.get("authorization") or "").startswith("Bearer rb_"):
                return self._json(401, {"error": "This token is missing, expired or no longer valid.", "fix": "Run rb login again."})
            origin = f"http://127.0.0.1:{self.server.server_address[1]}"
            return self._json(200, {"handle": "t", "base": f"{origin}/u/t", "publish": f"{origin}/api/hub/investigations"})
        self._json(404, {"error": "no"})

    def do_POST(self):
        origin = f"http://127.0.0.1:{self.server.server_address[1]}"
        if self.path == "/api/hub/device/code":
            return self._json(200, {"device_code": "dev_stub", "user_code": "ABCD-EFGH",
                                    "verification_uri": f"{origin}/link", "expires_in": 600, "interval": 0})
        if self.path == "/api/hub/device/token":
            if _Hub.unavailable > 0:                   # a hub down for a moment, as a gateway answers for it
                _Hub.unavailable -= 1
                return self._json(503, {"error": "Service Unavailable"})
            _Hub.polls += 1
            if _Hub.polls < 2:
                return self._json(400, {"error": "authorization_pending"})
            return self._json(200, {"token": "rb_deviceflowtoken", "handle": "t"})
        self._json(404, {"error": "no"})

    def do_DELETE(self):
        _Hub.revoked = self.headers.get("authorization")
        self._json(200, {"ok": True})

    def do_PUT(self):
        body = self.rfile.read(int(self.headers.get("content-length", "0")))
        if _Hub.refuse > 0:
            _Hub.refuse -= 1
            _Hub.puts.append((time.monotonic(), False))
            return self._json(503, {"error": "the hub is out of service for a moment"})
        _Hub.puts.append((time.monotonic(), True))
        if _Hub.held:
            arrived, release = _Hub.held
            arrived.set()
            release.wait(60)
            return
        _Hub.received = {"bytes": body, "auth": self.headers.get("authorization"), "path": self.path}
        with zipfile.ZipFile(io.BytesIO(body)) as z:
            man_name = next(n for n in z.namelist() if n.endswith("manifest.json"))
            man = json.loads(z.read(man_name))
        _Hub.received["manifest"] = man
        origin = f"http://127.0.0.1:{self.server.server_address[1]}"
        answer = {"url": f"{origin}/u/t/{man['project']['id']}"}
        if any(rel.startswith("record/") for rel in man["files"]):          # a page alone has nothing to clone
            answer["clone"] = f"rb clone {origin}/u/t/{man['project']['id']}/bundle.zip"
        self._json(201, answer)

    def _json(self, code, obj):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


@pytest.fixture
def hub(workdir, monkeypatch, tmp_path_factory, capsys):
    """A record with an experiment and a criterion the agent wrote, the user's terminal with no hub credential, and the
    stub hub at RB_HUB_URL. Returns rb, run in-process with --json."""
    as_the_user(monkeypatch, "RB_HUB_TOKEN")
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    monkeypatch.setenv("RB_CONFIG_DIR", str(tmp_path_factory.mktemp("rbconfig")))
    cfg = tmp_path_factory.mktemp("gitconfig") / "config"
    cfg.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(cfg))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    subprocess.run(["git", "init", "-q"], check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], check=True)
    subprocess.run(["git", "config", "user.name", "t"], check=True)

    def rb(*argv):
        code = main([*argv, "--json"])
        captured = capsys.readouterr()
        rb.last_err = captured.err
        return code, Envelope.model_validate_json(captured.out)

    assert rb("init", "the synced project", "--id", "sync-proj")[0] == 0
    monkeypatch.setenv("CLAUDECODE", "1")
    assert rb("experiment", "add", "e", "--id", "e1")[0] == 0
    assert rb("criterion", "add", "c", "-e", "e1", "--metric", "m", "--at-least", "0.5", "--id", "c1")[0] == 0
    monkeypatch.delenv("CLAUDECODE")
    _Hub.received = {}
    _Hub.polls = 0
    _Hub.revoked = None
    _Hub.held = None
    _Hub.asked = None
    _Hub.refuse = 0
    _Hub.unavailable = 0
    _Hub.puts = []
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Hub)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    monkeypatch.setenv("RB_HUB_URL", f"http://127.0.0.1:{httpd.server_address[1]}")
    yield rb
    httpd.shutdown()
    httpd.server_close()


def test_sync_uploads_the_page_alone_marked_private(hub, monkeypatch):
    """Plain rb sync sends the page with the manifest that hashes it, and nothing of the record: no record file, no
    address to clone from."""
    monkeypatch.setenv("RB_HUB_TOKEN", "rb_testtoken")
    code, env = hub("sync")
    assert code == 0 and env.data["with_record"] is False
    assert env.data["url"].endswith("/u/t/sync-proj") and env.data["handle"] == "t" and "clone" not in env.data
    assert "visibility=private" in _Hub.received["path"]
    man = _Hub.received["manifest"]
    assert man["project"]["id"] == "sync-proj" and sorted(man["files"]) == ["README.md", "page.html"] and "url" not in man
    with zipfile.ZipFile(io.BytesIO(_Hub.received["bytes"])) as z:
        assert sorted(n.split("/", 1)[1] for n in z.namelist()) == ["README.md", "manifest.json", "page.html"]
    assert _Hub.received["auth"] == "Bearer rb_testtoken"


def test_sync_with_the_record_uploads_the_bundle_with_its_real_address_and_says_what_it_keeps(hub, monkeypatch, capsys):
    monkeypatch.setenv("RB_HUB_TOKEN", "rb_testtoken")
    code, env = hub("sync", "--with-record")
    assert code == 0 and env.data["with_record"] is True
    assert env.data["url"].endswith("/u/t/sync-proj") and env.data["handle"] == "t"
    assert env.data["clone"].startswith("rb clone http://127.0.0.1:")
    assert "visibility=private" in _Hub.received["path"]
    man = _Hub.received["manifest"]
    assert man["project"]["id"] == "sync-proj" and any(rel.startswith("record/.rb/") for rel in man["files"])
    assert man["url"].endswith("/u/t/sync-proj/bundle.zip")    # the page inside points at its real address
    assert _Hub.received["auth"] == "Bearer rb_testtoken"
    assert main(["sync", "--with-record"]) == 0
    said = capsys.readouterr().out
    assert ("The record keeps who made each write (human:<name> or agent:<name>, and the channel it came through), an agent's chat "
            "session, name, version, provider and model, the user's words passed with --words, and each run's host, platform and "
            "branch. Anyone who can open the copy can read all of it.") in said
    assert main(["sync"]) == 0
    assert "Its page went alone" in capsys.readouterr().out


def test_sync_without_a_credential_says_where_to_get_one(hub):
    code, env = hub("sync")
    assert code != 0 and env.errors[0].code == "E_USAGE" and "rb login" in env.errors[0].fix
    assert _Hub.received == {}                                   # nothing left the machine


class _Proxy:
    """A proxy on this machine that refuses every tunnel and keeps the first line of each: what rb asked to reach, with
    nothing leaving the machine."""

    def __init__(self):
        import socket
        self.asked = []
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.url = f"http://127.0.0.1:{self.sock.getsockname()[1]}"
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            with conn:
                head = b""
                while b"\r\n\r\n" not in head:
                    got = conn.recv(4096)
                    if not got:
                        break
                    head += got
                self.asked.append(head.split(b"\r\n")[0].decode("latin-1"))
                conn.sendall(b"HTTP/1.1 403 Forbidden\r\ncontent-length: 0\r\n\r\n")

    def close(self):
        self.sock.close()


@pytest.fixture
def refusing_proxy(monkeypatch):
    """_Proxy as the proxy every https call goes through, so a call to rabbitbrain.ai never leaves the machine."""
    proxy = _Proxy()
    monkeypatch.setenv("HTTPS_PROXY", proxy.url)
    monkeypatch.setenv("https_proxy", proxy.url)
    yield proxy
    proxy.close()


def test_rabbitbrain_ai_while_it_is_not_open_is_said_and_nothing_is_sent(hub, monkeypatch, refusing_proxy):
    """rb login and rb sync addressed to rabbitbrain.ai, whose hosted service is not open, say so and what works
    without it, and call no address: not with a credential in RB_HUB_TOKEN either. A hub the user names is called.
    RB_TEST_HOSTED_CLOSED plays the service closed, as it was until 0.6.1 and can be again."""
    monkeypatch.setenv("RB_TEST_HOSTED_CLOSED", "1")
    stub = os.environ["RB_HUB_URL"]
    monkeypatch.delenv("RB_HUB_URL")
    for argv, token in ((["login"], None), (["sync"], None), (["sync", "--with-record"], "rb_testtoken"), (["login", "--hub", "https://rabbitbrain.ai/"], None)):
        if token:
            monkeypatch.setenv("RB_HUB_TOKEN", token)
        code, env = hub(*argv)
        assert code == 2 and env.errors[0].code == "E_NOT_AVAILABLE", (argv, env.errors)
        assert "hosted service is not open yet" in env.errors[0].message and "sent nothing" in env.errors[0].message
        assert all(c in env.errors[0].fix for c in ("rb page", "rb open", "rb export --with-record")), env.errors[0].fix
    assert _Hub.received == {} and _Hub.polls == 0 and refusing_proxy.asked == []
    code, env = hub("login", "--hub", stub)
    assert code == 0 and _Hub.polls == 2


def test_rabbitbrain_ai_is_open_and_called(hub, monkeypatch, refusing_proxy):
    """0.6.1 opens rabbitbrain.ai: rb login and rb sync addressed to it call it, as any hub. A proxy on this machine
    that refuses every tunnel shows what rb asked to reach, and nothing leaves the machine."""
    monkeypatch.delenv("RB_HUB_URL")
    monkeypatch.delenv("RB_TEST_HOSTED_CLOSED", raising=False)
    monkeypatch.setenv("RB_HUB_TOKEN", "rb_testtoken")
    for argv in (["login"], ["sync"], ["sync", "--with-record"]):
        code, env = hub(*argv)
        assert code == 2 and env.errors[0].code != "E_NOT_AVAILABLE", (argv, env.errors)
        assert "rabbitbrain.ai" in env.errors[0].message, env.errors
    asked = refusing_proxy.asked
    assert len(asked) == 3 and all(line.startswith("CONNECT rabbitbrain.ai:443 ") for line in asked), asked


def test_publish_is_gone_and_says_what_replaced_it(hub, monkeypatch):
    monkeypatch.setenv("RB_HUB_TOKEN", "rb_testtoken")
    code, env = hub("publish", "--yes")
    assert code == 2 and env.errors[0].code == "E_NOT_AVAILABLE" and "rb export" in env.errors[0].fix
    assert _Hub.received == {}                                   # nothing left the machine


def test_an_agent_signs_in_and_syncs_like_anyone(hub, monkeypatch):
    """Inside an agent session rb login and rb sync go through: the device flow still waits until the code is approved
    on the site, and the upload is still private."""
    monkeypatch.setenv("CLAUDECODE", "1")
    code, env = hub("login")
    assert code == 0 and env.data["handle"] == "t" and _Hub.polls == 2      # it waited for the approval
    code, env = hub("sync")
    assert code == 0 and "visibility=private" in _Hub.received["path"]
    assert _Hub.received["auth"] == "Bearer rb_deviceflowtoken"


def test_login_stores_a_0600_credential_and_sync_uses_it(hub, monkeypatch, tmp_path):
    monkeypatch.setenv("RB_CONFIG_DIR", str(tmp_path / "cfg"))
    code, env = hub("login")
    assert code == 0 and env.data["handle"] == "t"
    p = tmp_path / "cfg" / "credentials.json"
    assert p.exists()
    if os.name == "posix":      # Windows keeps no such bits: the file is the user's alone by the profile it sits in
        assert (p.stat().st_mode & 0o777) == 0o600
    assert "rb_deviceflowtoken" in p.read_text(encoding="utf-8")
    code, env = hub("sync")
    assert code == 0
    assert _Hub.received["auth"] == "Bearer rb_deviceflowtoken"


def test_env_token_overrides_the_credentials_file(hub, monkeypatch, tmp_path):
    monkeypatch.setenv("RB_CONFIG_DIR", str(tmp_path / "cfg"))
    code, _ = hub("login")
    assert code == 0
    monkeypatch.setenv("RB_HUB_TOKEN", "rb_ci_token")
    code, _ = hub("sync")
    assert code == 0
    assert _Hub.received["auth"] == "Bearer rb_ci_token"


def test_logout_revokes_and_removes(hub, monkeypatch, tmp_path):
    monkeypatch.setenv("RB_CONFIG_DIR", str(tmp_path / "cfg"))
    code, _ = hub("login")
    assert code == 0
    code, env = hub("logout")
    assert code == 0 and env.data["revoked"] is True and env.data["removed"] is True
    assert _Hub.revoked == "Bearer rb_deviceflowtoken"
    code, env = hub("sync")
    assert code != 0 and env.errors[0].code == "E_USAGE"   # no credential anywhere now


@pytest.mark.skipif(not hasattr(signal, "SIGHUP"), reason="the signals that end a program at someone's request are POSIX's")
def test_a_sync_ended_by_a_signal_leaves_nothing_behind(hub, monkeypatch, tmp_path_factory):
    """Ctrl-C, a request to end (a cancelled CI job, `timeout`) or a terminal that closed while rb sync builds the
    bundle, or while the hub takes its time over it: rb ends by that signal, as it would have, and the folder it built
    the bundle in is gone either way."""
    tmp = tmp_path_factory.mktemp("tmpdir")
    for name in ("TMPDIR", "TMP", "TEMP"):
        monkeypatch.setenv(name, str(tmp))
    left = lambda: sorted(q.name for q in tmp.iterdir() if q.name.startswith("rb-sync-"))  # noqa: E731
    assert hub("login")[0] == 0
    signals = [sig for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP) if signal.getsignal(sig) != signal.SIG_IGN]
    arrived, release = threading.Event(), threading.Event()
    _Hub.held = (arrived, release)
    fifo = tmp_path_factory.mktemp("pipe") / "config"
    os.mkfifo(fifo)

    def sync(sig, held) -> None:
        p = subprocess.Popen([*command(), "sync", "--json"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, env=environment())
        try:
            assert held(p, sig), sig.name
            assert p.wait(timeout=30) == -sig, sig.name                     # ended by the signal, as without rb's catch
        finally:
            if p.poll() is None:
                p.kill()
                p.wait()
        assert left() == [], sig.name

    def in_the_upload(p, sig) -> bool:
        assert arrived.wait(30) and p.poll() is None, "rb sync never sent the bundle"
        arrived.clear()
        p.send_signal(sig)
        return True

    def in_the_build(p, sig) -> bool:
        # every read git makes of its config is answered, from a pipe that opens only while git waits on it; while
        # one waits with the bundle's folder made, rb is sent the signal
        sent, waited = False, time.monotonic() + 60
        while p.poll() is None:
            assert time.monotonic() < waited, "rb sync never ended"
            try:
                fd = os.open(fifo, os.O_WRONLY | os.O_NONBLOCK)
            except OSError:
                time.sleep(0.01)
                continue
            if not sent and left():
                p.send_signal(sig)
                sent = True
            os.close(fd)
        return sent

    try:
        for sig in signals:
            sync(sig, in_the_upload)
        subprocess.run(["git", "config", "include.path", str(fifo)], check=True)
        try:
            for sig in signals:
                sync(sig, in_the_build)
        finally:
            os.remove(fifo)                                                # so git reads its own config again
            subprocess.run(["git", "config", "--unset", "include.path"], check=True)
    finally:
        release.set()
    assert signals


# ---------------------------------------------------------------- what the hosted page shows, and how it is said

def test_a_page_synced_alone_sends_no_repository_identity(hub, monkeypatch):
    """Plain rb sync tells the hub nothing of the repository: in one with an https origin and a commit, the uploaded
    manifest names no repository, and neither the page nor the manifest holds its address."""
    monkeypatch.setenv("RB_HUB_TOKEN", "rb_testtoken")
    subprocess.run(["git", "remote", "add", "origin", "https://github.com/ana/int8-resnet50.git"], check=True)
    subprocess.run(["git", "add", "-A"], check=True)
    subprocess.run(["git", "commit", "-qm", "the INT8 record"], check=True)
    code, env = hub("sync")
    assert code == 0, env.errors
    assert "repo" not in _Hub.received["manifest"] and env.data["repo"] is None
    assert b"github.com/ana/int8-resnet50" not in _Hub.received["bytes"]


def _sent_files() -> dict:
    with zipfile.ZipFile(io.BytesIO(_Hub.received["bytes"])) as z:
        return {n: z.read(n) for n in z.namelist()}


def test_a_record_synced_names_its_code_only_at_an_https_origin(hub, monkeypatch, capsys):
    """rb sync --with-record names the code by rb export's one rule: where origin, as the checkout's config writes it,
    is an https address, with any user:password@, query and fragment taken out, so a share link's Rerun reaches it; a
    local path, file://, ssh and scp-like name none, and rb sync then says rb export's line. An insteadOf that sends
    the https address to this machine's copy changes nothing: the https address is named. No credential and no path
    of this machine is in the bundle, either way. The named words are the Rerun panel's."""
    monkeypatch.setenv("RB_HUB_TOKEN", "rb_testtoken")
    subprocess.run(["git", "add", "-A"], check=True)
    subprocess.run(["git", "commit", "-qm", "the INT8 record"], check=True)
    head = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    here = os.path.realpath(os.getcwd())
    machine = {here.encode(), os.getcwd().encode(), str(Path.home()).encode(), tempfile.gettempdir().encode()}
    named = {"https://github.com/ana/int8-resnet50.git": "https://github.com/ana/int8-resnet50.git",
             "https://ana:hunter2@github.com/ana/int8-resnet50.git": "https://github.com/ana/int8-resnet50.git",
             "https://github.com/ana/int8-resnet50.git?token=hunter2": "https://github.com/ana/int8-resnet50.git",
             "https://github.com/ana/int8-resnet50.git#hunter2": "https://github.com/ana/int8-resnet50.git",
             "HTTPS://github.com/ana/int8-resnet50.git": "https://github.com/ana/int8-resnet50.git",
             "\x1chttps://github.com/ana/int8-resnet50.git": "https://github.com/ana/int8-resnet50.git"}
    unnamed = [here, "file://" + here, "ssh://git@github.com/ana/int8-resnet50.git", "git@github.com:ana/int8-resnet50.git",
               "http://github.com/ana/int8-resnet50.git"]
    # git's own rewrite for this machine's fetches: the https address goes to this machine's copy, which is never named
    subprocess.run(["git", "config", f"url.{here}.insteadOf", "https://github.com/ana/"], check=True)
    subprocess.run(["git", "remote", "add", "origin", here], check=True)
    for origin in [*named, *unnamed]:
        subprocess.run(["git", "remote", "set-url", "origin", origin], check=True)
        assert main(["sync", "--with-record"]) == 0
        said = capsys.readouterr().out
        man, files = _Hub.received["manifest"], _sent_files()
        everything = b"\n".join(files.values())
        for p in machine:
            assert p not in everything, (origin, p)
        assert b"hunter2" not in everything, origin
        if origin in named:
            assert man["repo"]["url"] == named[origin] and man["repo"]["commit"] == head, (origin, man.get("repo"))
            page = next(b for n, b in files.items() if n.endswith("page.html")).decode("utf-8")
            assert named[origin] in page and "The runs need the code; this bundle doesn&#x27;t say where it is." not in page
            assert f"The code the runs used: {named[origin]} at commit {head[:10]}. Readers who rerun it get the code with the record." in said
            assert "without the code" not in said
        else:
            assert "repo" not in man, (origin, man["repo"])
            assert b"github.com/ana" not in everything, origin
            assert ("The bundle names no address for the code: origin is not an https address a reader can fetch. A reader gets "
                    "the code with rb clone --code <address>.") in said, said
            assert "The code the runs used" not in said
    code, env = hub("sync", "--with-record")
    assert code == 0 and env.data["repo"] is None
    subprocess.run(["git", "remote", "set-url", "origin", "https://github.com/ana/int8-resnet50.git"], check=True)
    code, env = hub("sync", "--with-record")
    assert code == 0 and env.data["repo"]["url"] == "https://github.com/ana/int8-resnet50.git"


def test_sync_says_how_the_copy_is_shared_and_login_and_logout_say_where(hub, monkeypatch, capsys, tmp_path):
    """rb sync says the copy is shared from its page with a link that can be revoked, and with the record that only
    its owner downloads it, unless they let a share link's readers rerun it on the site; without the record, that a
    share link's readers rerun only a sync made with --with-record. Nothing says to make it public. rb login says the page signs you in first; rb logout
    sends you to /account to revoke a credential it could not."""
    monkeypatch.setenv("RB_CONFIG_DIR", str(tmp_path / "cfg"))
    assert main(["login"]) == 0
    assert "Open " in (err := capsys.readouterr().err) and "on any device; it signs you in first if needed, and approve the code." in err
    assert main(["sync"]) == 0
    said = capsys.readouterr().out
    assert "Share it from its page on the site, with a link you can revoke; rb sync again updates what the link shows." in said
    assert "make it public" not in said and "run rb sync again after new runs" in said
    assert "A share link's readers can rerun only a sync made with --with-record." in said
    assert main(["sync", "--with-record"]) == 0
    said = capsys.readouterr().out
    assert "Only you can download the record, unless you let a share link's readers rerun it on the site." in said
    assert "shows the page alone" not in said and "can rerun only" not in said and "make it public" not in said
    monkeypatch.setenv("RB_HUB_URL", "http://127.0.0.1:9")           # nothing answers there: the revoke cannot reach it
    (tmp_path / "cfg" / "credentials.json").write_text(json.dumps({"http://127.0.0.1:9": {"token": "rb_x", "handle": "t"}}), encoding="utf-8")
    assert main(["logout"]) == 0
    said = capsys.readouterr().out
    assert "revoke at /account if it may have leaked" in said and "/welcome" not in said


def test_sync_sends_the_inert_export(hub, monkeypatch):
    """The hosted page is the export (BP 2b): rb's own text inert, no script for the hub's gate to refuse."""
    from test_shell_contract import needs
    needs("R1", "C1")
    monkeypatch.setenv("RB_HUB_TOKEN", "rb_testtoken")
    monkeypatch.setenv("RB_TEST_SHELL", "1")
    code, env = hub("sync")
    assert code == 0, env.errors
    with zipfile.ZipFile(io.BytesIO(_Hub.received["bytes"])) as z:
        page = z.read(next(n for n in z.namelist() if n.endswith("page.html"))).decode("utf-8")
    assert "<script" not in page.lower() and "data-shell=" in page


# ---------------------------------------------------------------- rb sync --watch

PACE = 0.01         # RB_TEST_SYNC_PACE: a look every 0.3 s, a minute 0.6 s, the back-off 0.3, 0.6, 1.2, 3 and 6 s
LOOK, MINUTE = 30 * PACE, 60 * PACE


def _watching() -> None:
    """--watch is a flag of rb sync once cli_parser.py and share.rs carry it (L2's requests to I and R)."""
    spec = json.loads((Path(__file__).resolve().parent.parent / "rust" / "src" / "cli_spec.json").read_text(encoding="utf-8"))
    if '"--watch"' not in json.dumps(spec) or "cmd_sync_watch" not in (Path(__file__).resolve().parent.parent / "rust" / "src" / "cmds" / "share.rs").read_text(encoding="utf-8"):
        pytest.skip("pending the --watch flag on rb sync (requests: cli_parser.py to I, share.rs to R)")


class _Watch:
    """rb sync --watch as its own process, every line it says on stderr kept with when it came."""

    def __init__(self, *argv: str, env: dict = None):
        e = environment()
        e["RB_TEST_SYNC_PACE"] = str(PACE)
        e.update(env or {})
        self.p = subprocess.Popen([*command(), "sync", "--watch", *argv], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, env=e)
        self.lines: list = []
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def _read(self) -> None:
        for raw in self.p.stderr:
            self.lines.append((time.monotonic(), raw.decode("utf-8", "replace").rstrip("\n")))

    def said(self, start: str) -> list:
        return [line for _, line in list(self.lines) if line.startswith(start)]

    def until(self, what, why: str, within: float = 30) -> None:
        deadline = time.monotonic() + within
        while not what():
            assert self.p.poll() is None, f"rb sync --watch ended ({self.p.returncode}): {self.lines}"
            assert time.monotonic() < deadline, f"{why}: {self.lines}"
            time.sleep(0.02)

    def end(self, sig=signal.SIGINT) -> int:
        if self.p.poll() is None:
            if os.name == "nt":     # Windows delivers a child no SIGINT (Popen.send_signal refuses it): end it there
                self.p.terminate()
            else:
                self.p.send_signal(sig)
        try:
            return self.p.wait(timeout=30)
        finally:
            if self.p.poll() is None:
                self.p.kill()
                self.p.wait()
            self.reader.join(5)


def _put(path: str, text: str) -> None:
    """Writes a file while a watch runs the way a careful program does, whole, by a rename: a look that reads a file
    cut mid-write lists it as a changed file nothing in the record cites, and the page that says so is a real change."""
    tmp = Path(path).with_name(f".{Path(path).name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _taken() -> list:
    return [t for t, ok in list(_Hub.puts) if ok]


TIME = re.compile(r"(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2})(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2}| UTC)?")


def _minutes(since: float, until: float) -> set:
    """Every minute from `since` to `until` (seconds since the epoch, UTC), as a page writes one: 2026-10-21 16:59."""
    t, out = since - since % 60, set()
    while t <= until:
        out.add(time.strftime("%Y-%m-%d %H:%M", time.gmtime(t)))
        t += 60
    return out


def _clock_free(page: str, minutes: set) -> str:
    """A page with every time on it that is one of the clock's `minutes` as one mark."""
    return TIME.sub(lambda m: "\0" if f"{m.group(1)} {m.group(2)}" in minutes else m.group(0), page)


def _same(was: str, was_clock: set, now: str, now_clock: set) -> bool:
    """Whether two pages say the same but for the times each one's own clock wrote, as the watch reads them (hub.rs's
    Shown::same): time by time, two times differ only by the clock when each is in its own page's clock minutes, in the
    same form; every other time, a write's in the clock's minute included, is compared as it reads."""
    a, b = list(TIME.finditer(was)), list(TIME.finditer(now))
    between = lambda page, ms: [page[i:j] for i, j in zip([0] + [m.end() for m in ms], [m.start() for m in ms] + [len(page)])]  # noqa: E731
    if len(a) != len(b) or between(was, a) != between(now, b):
        return False
    form = lambda t: re.sub(r"\d", "0", t)  # noqa: E731
    for x, y in zip(a, b):
        clocks = f"{x.group(1)} {x.group(2)}" in was_clock and f"{y.group(1)} {y.group(2)}" in now_clock and form(x.group(0)) == form(y.group(0))
        if x.group(0) != y.group(0) and not clocks:
            return False
    return True


_BEGAN = time.time()


def _since_began(page: str) -> str:
    """A page with every time the clock could have written since this module began as one mark."""
    return _clock_free(page, _minutes(_BEGAN, time.time() + 60))


def _sent() -> str:
    with zipfile.ZipFile(io.BytesIO(_Hub.received["bytes"])) as z:
        return z.read(next(n for n in z.namelist() if n.endswith("page.html"))).decode("utf-8")


def _reads_now() -> str:
    """The page rb sync --watch sends as the record reads now: after the journal's look, as each look of the watch takes
    it, written by rb export outside the repository."""
    out = Path(tempfile.mkdtemp(prefix="rb-page-")) / "page"
    try:
        subprocess.run([*command(), "journal", "scan"], capture_output=True, env=environment(), check=True)
        r = subprocess.run([*command(), "export", "--no-repo", "--out", str(out)], capture_output=True, env=environment())
        assert r.returncode == 0, r.stderr
        return _since_began((out / "page.html").read_text(encoding="utf-8"))
    finally:
        shutil.rmtree(out.parent, ignore_errors=True)


def _sends_what_it_reads_now(w: "_Watch", why: str) -> int:
    """Waits for the watch to send the page as the record reads now, and says how many uploads that took. A change made
    in steps (a file, then its commit) can be seen half made by a look, and the page that look sends is a real one, so
    how many uploads come before is not fixed; once the page as it reads now is sent, a look sends nothing more."""
    now = _reads_now()
    w.until(lambda: _Hub.received.get("bytes") and _since_began(_sent()) == now, why)
    return len(_taken())


def test_watch_uploads_only_on_change(hub, monkeypatch):
    """rb sync --watch uploads at once, then only when the record or what the journal found changed, and never twice
    within a minute: looks that find the same key send nothing, however many there are."""
    _watching()
    monkeypatch.setenv("RB_HUB_TOKEN", "rb_testtoken")
    Path("configs").mkdir()
    Path("configs/eval.yaml").write_text("dataset: imagenet-val\n", encoding="utf-8")
    subprocess.run(["git", "add", "configs"], check=True)
    subprocess.run(["git", "commit", "-qm", "the eval config"], check=True)
    assert main(["status"]) == 0                                         # rb looks: the commit it sees is the start
    w = _Watch()
    try:
        w.until(lambda: len(_taken()) == 1 and w.said("Synced at"), "the first upload never came")
        assert "visibility=private" in _Hub.received["path"] and _Hub.received["manifest"]["project"]["id"] == "sync-proj"
        assert w.said("  Share it from its page on the site, with a link you can revoke; each sync updates what the link shows.")
        time.sleep(6 * LOOK)
        assert len(_taken()) == 1, "an upload with nothing changed"
        assert main(["note", "add", "the held-out set is ImageNet val", "-e", "e1"]) == 0
        w.until(lambda: len(_taken()) == 2, "a record write was never sent")
        # two writes in a row: one upload or two, never within a minute of the one before
        assert main(["note", "add", "INT8 calibrates on 1024 training images", "-e", "e1"]) == 0
        assert main(["note", "add", "FP32 is the torchvision checkpoint", "-e", "e1"]) == 0
        w.until(lambda: len(_taken()) >= 3, "the writes were never sent")
        time.sleep(MINUTE + 3 * LOOK)
        # a changed config nothing in the record cites changes what the page says, though no record file moved
        before = len(_taken())
        _put("configs/eval.yaml", "dataset: imagenet-val\ncrop: resize\n")
        subprocess.run(["git", "commit", "-qam", "resize"], check=True)
        n = _sends_what_it_reads_now(w, "what the journal found was never sent")
        assert n > before
        time.sleep(MINUTE + 3 * LOOK)
        assert len(_taken()) == n
        # and a results file nobody recorded, which only the journal's look finds
        Path("out").mkdir()
        _put("out/metrics.json", '{"m": 0.71}\n')
        found = json.loads(subprocess.run([*command(), "status", "--json"], capture_output=True, env=environment(), check=True).stdout)
        assert found["data"]["found_unrecorded"] == 1, found["data"].get("found_unrecorded")
        n2 = _sends_what_it_reads_now(w, "a results file nobody recorded was never sent")
        assert n2 > n
        time.sleep(MINUTE + 3 * LOOK)
        assert len(_taken()) == n2
        taken = _taken()
        assert all(b - a >= MINUTE * 0.95 for a, b in zip(taken, taken[1:])), taken
        assert len(w.said("Synced at")) == len(taken) and len(w.said("  Share it from")) == 1
    finally:
        w.end()


def test_watch_backs_off_and_says_so(hub, monkeypatch):
    """A hub that refuses is tried again after 30 s, then 1 and 2 min (as paced here), each failure said in one line
    with the hub's reason and the wait; the first upload that goes through is said as any other."""
    _watching()
    monkeypatch.setenv("RB_HUB_TOKEN", "rb_testtoken")
    _Hub.refuse = 3
    w = _Watch()
    try:
        w.until(lambda: len(_taken()) == 1 and w.said("Synced at"), "the upload never went through", within=60)
        failed = w.said("Not synced:")
        assert failed == [f"Not synced: The hub answered 503: the hub is out of service for a moment. Trying again in {d}."
                          for d in ("30 s", "1 min", "2 min")], w.lines
        times = [t for t, _ in _Hub.puts]
        gaps = [b - a for a, b in zip(times, times[1:])]
        assert len(gaps) == 3 and all(g >= want * 0.95 for g, want in zip(gaps, (LOOK, 2 * LOOK, 4 * LOOK))), gaps
        # once through, it looks every 30 s again: one more failure waits 30 s, not 5 min
        _Hub.refuse = 1
        assert main(["note", "add", "the held-out set is ImageNet val", "-e", "e1"]) == 0
        w.until(lambda: len(_taken()) == 2, "the write was never sent after a failure")
        assert w.said("Not synced:")[-1].endswith("Trying again in 30 s.")
    finally:
        w.end()


@pytest.mark.skipif(not hasattr(signal, "SIGHUP"), reason="the signals that end a program at someone's request are POSIX's")
def test_watch_ends_clean_on_a_signal(hub, monkeypatch, tmp_path_factory):
    """Ctrl-C, a request to end or a terminal that closed ends rb sync --watch by that signal, between two looks or
    while the hub takes its time over an upload, and the folder a bundle is built in is gone either way."""
    _watching()
    monkeypatch.setenv("RB_HUB_TOKEN", "rb_testtoken")
    tmp = tmp_path_factory.mktemp("tmpdir")
    for name in ("TMPDIR", "TMP", "TEMP"):
        monkeypatch.setenv(name, str(tmp))
    left = lambda: sorted(q.name for q in tmp.iterdir() if q.name.startswith("rb-sync-"))  # noqa: E731
    signals = [sig for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP) if signal.getsignal(sig) != signal.SIG_IGN]
    assert signals
    for sig in signals:
        _Hub.puts, _Hub.held = [], None
        w = _Watch()
        try:
            w.until(lambda: w.said("Synced at"), "the first upload never came")
            time.sleep(2 * LOOK)                                            # between two looks
        finally:
            code = w.end(sig)
        assert code == -sig and left() == [], (sig.name, code, w.lines)
        arrived, release = threading.Event(), threading.Event()
        _Hub.held = (arrived, release)
        w = _Watch()
        try:
            w.until(arrived.is_set, "the upload never came")
        finally:
            code = w.end(sig)
            release.set()
        assert code == -sig and left() == [], (sig.name, code, w.lines)
    _Hub.held = None


def test_watch_without_a_credential_or_an_open_hub_says_so_at_once(hub, monkeypatch, refusing_proxy):
    """What stops rb sync stops rb sync --watch before it watches: no credential, or rabbitbrain.ai while its hosted
    service is not open. Nothing is sent and nothing waits."""
    _watching()

    def watch(*also: str) -> str:
        r = subprocess.run([*command(), "sync", "--watch", *also], capture_output=True, env=environment(), timeout=60,
                           stdin=subprocess.DEVNULL, text=True)
        assert r.returncode == 2, r
        return r.stdout + r.stderr

    assert "No credential for" in (said := watch()) and "rb login" in said
    monkeypatch.setenv("RB_HUB_TOKEN", "rb_testtoken")
    # --json has one answer to print, and a watch never ends with one
    assert "has no one answer for --json" in watch("--json") and _Hub.puts == []
    monkeypatch.delenv("RB_HUB_URL")
    monkeypatch.setenv("RB_TEST_HOSTED_CLOSED", "1")
    assert "hosted service is not open yet" in watch()
    assert _Hub.puts == [] and _Hub.received == {} and refusing_proxy.asked == []


def test_watch_stops_on_a_refusal_waiting_does_not_change(hub, monkeypatch):
    """A hub that refuses the credential is not asked again and again: rb sync --watch ends as rb sync does, exit 2,
    with the hub's reason and what to do."""
    _watching()
    monkeypatch.setenv("RB_HUB_TOKEN", "bad_token")
    w = _Watch()
    try:
        assert w.p.wait(timeout=60) == 2
    finally:
        w.end()
    said = "\n".join(line for _, line in w.lines)
    assert "The hub answered 401: This token is missing, expired or no longer valid." in said and "Run rb login again" in said
    assert not w.said("Not synced:") and _Hub.puts == []


def test_watch_sends_what_rb_computes_without_a_write(hub, monkeypatch):
    """A setting's source that now states another value is a conflict the page shows, though no record file moved and
    nothing was found: rb sync --watch sends it."""
    _watching()
    monkeypatch.setenv("RB_HUB_TOKEN", "rb_testtoken")
    Path("configs").mkdir()
    Path("configs/train.yaml").write_text("optim:\n  lr: 0.0001\n", encoding="utf-8")
    subprocess.run(["git", "add", "configs"], check=True)
    subprocess.run(["git", "commit", "-qm", "the train config"], check=True)
    assert main(["spec", "set", "e1", "optim.lr", "--source", "configs/train.yaml#optim.lr"]) == 0
    w = _Watch()
    try:
        w.until(lambda: len(_taken()) == 1, "the first upload never came")
        time.sleep(MINUTE + 2 * LOOK)
        assert len(_taken()) == 1
        _put("configs/train.yaml", "optim:\n  lr: 0.0003\n")
        subprocess.run(["git", "commit", "-qam", "a higher lr"], check=True)
        n = _sends_what_it_reads_now(w, "the conflict was never sent")
        assert n >= 2 and "settings: 1 conflict" in _sent()
        time.sleep(MINUTE + 3 * LOOK)
        assert len(_taken()) == n
    finally:
        w.end()


def test_watch_reads_a_source_again_at_each_look(hub, monkeypatch):
    """A source rewritten with as many bytes at a time the file system keeps the same (a coarse clock, issue #2) is
    read again at the watch's next look, as a command line reads it: the conflict is sent. Played here by putting the
    file's time back after the rewrite."""
    _watching()
    monkeypatch.setenv("RB_HUB_TOKEN", "rb_testtoken")
    cfg = Path("configs/train.yaml")
    cfg.parent.mkdir()
    cfg.write_text("optim:\n  lr: 0.0001\n", encoding="utf-8")
    subprocess.run(["git", "add", "configs"], check=True)
    subprocess.run(["git", "commit", "-qm", "the train config"], check=True)
    assert main(["spec", "set", "e1", "optim.lr", "--source", "configs/train.yaml#optim.lr"]) == 0
    w = _Watch()
    try:
        w.until(lambda: len(_taken()) == 1, "the first upload never came")
        time.sleep(MINUTE + 2 * LOOK)
        assert len(_taken()) == 1
        was = cfg.stat()
        _put(str(cfg), "optim:\n  lr: 0.0003\n")
        os.utime(cfg, ns=(was.st_atime_ns, was.st_mtime_ns))
        assert cfg.stat().st_size == was.st_size and cfg.stat().st_mtime_ns == was.st_mtime_ns
        _sends_what_it_reads_now(w, "the conflict was never sent")
        assert "settings: 1 conflict" in _sent()
    finally:
        w.end()


def test_the_watch_leaves_out_only_what_the_clock_writes(hub, monkeypatch):
    """The page rb sync sends, made and looked at a minute or a fortnight apart with nothing else changed, differs only
    by the times the clock wrote on it, wherever the page draws them (its stamp, its footer, when rb last looked, and
    any line a later page adds), and the watch leaves out exactly those: a page that moved only by the clock is not
    sent again. A note written in the clock's own minute reads that minute on every page, and is no clock."""
    def made_at(now: str) -> str:
        env = {**environment(), "RB_TEST_NOW": now}
        subprocess.run([*command(), "journal", "scan"], capture_output=True, env=env, check=True)
        out = Path(tempfile.mkdtemp(prefix="rb-page-")) / "page"
        try:
            subprocess.run([*command(), "export", "--no-repo", "--out", str(out)], capture_output=True, env=env, check=True)
            return (out / "page.html").read_text(encoding="utf-8")
        finally:
            shutil.rmtree(out.parent, ignore_errors=True)
    monkeypatch.setenv("RB_TEST_NOW", "2026-10-21T16:59:30Z")
    assert main(["note", "add", "the held-out set is ImageNet val", "-e", "e1"]) == 0
    monkeypatch.delenv("RB_TEST_NOW")
    clocks = ("2026-10-21T16:59:58Z", "2026-10-21T17:00:01Z", "2026-11-03T08:01:30Z")
    pages = [made_at(now) for now in clocks]
    minutes = [{now[:16].replace("T", " ")} for now in clocks]
    for page, mine in zip(pages, minutes):
        # the stamp, the footer and when rb last looked, at least
        assert sum(f"{m.group(1)} {m.group(2)}" in mine for m in TIME.finditer(page)) >= 3, TIME.findall(page)
    assert "2026-10-21 16:59" in pages[1]  # the note's time, in the first page's clock minute
    assert pages[0] != pages[1] != pages[2]
    assert _same(pages[0], minutes[0], pages[1], minutes[1]) and _same(pages[1], minutes[1], pages[2], minutes[2])
    # and a page that says another thing is not the same, though made in the same minute
    assert not _same(pages[0], minutes[0], pages[0].replace("ImageNet val", "ImageNet v2"), minutes[0])


def test_watch_sends_nothing_when_only_the_clock_moves(hub, monkeypatch):
    """A watch whose clock moves on a minute at each look, with nothing else changed, sends its first upload and no
    other, though every page it builds says another time where it was made and where rb last looked; a note written in
    its first minute is no clock. A write is sent all the same."""
    _watching()
    monkeypatch.setenv("RB_HUB_TOKEN", "rb_testtoken")
    monkeypatch.setenv("RB_TEST_NOW", "2026-10-21T16:59:10Z")
    assert main(["note", "add", "the held-out set is ImageNet val", "-e", "e1"]) == 0
    monkeypatch.delenv("RB_TEST_NOW")
    w = _Watch(env={"RB_TEST_SYNC_CLOCK": "2026-10-21T16:59:30Z,60"})
    try:
        w.until(lambda: len(_taken()) == 1, "the first upload never came")
        assert "2026-10-21 16:59" in _sent()
        time.sleep(MINUTE + 8 * LOOK)                                       # eight looks, each a minute on
        assert len(_taken()) == 1, _taken()
        assert main(["note", "add", "INT8 calibrates on 1024 training images", "-e", "e1"]) == 0
        w.until(lambda: len(_taken()) == 2, "the write was never sent")
        assert "INT8 calibrates on 1024 training images" in _sent() and "2026-10-21 17:0" in _sent()   # its clock moved on
        time.sleep(MINUTE + 4 * LOOK)
        assert len(_taken()) == 2, _taken()
    finally:
        w.end()


def test_watch_sends_a_date_a_source_now_says(hub, monkeypatch):
    """A date is not the clock when a source says it: a cited config whose date moves changes what the page says of
    its conflict, and the watch sends it."""
    _watching()
    monkeypatch.setenv("RB_HUB_TOKEN", "rb_testtoken")
    Path("configs").mkdir()
    Path("configs/eval.yaml").write_text('split_date: "2026-10-20"\n', encoding="utf-8")
    subprocess.run(["git", "add", "configs"], check=True)
    subprocess.run(["git", "commit", "-qm", "the eval config"], check=True)
    assert main(["spec", "set", "e1", "split_date", "--source", "configs/eval.yaml#split_date"]) == 0
    Path("configs/eval.yaml").write_text('split_date: "2026-10-21"\n', encoding="utf-8")
    w = _Watch()
    try:
        w.until(lambda: len(_taken()) == 1, "the first upload never came")
        assert "2026-10-21" in _sent()
        _put("configs/eval.yaml", 'split_date: "2026-10-22"\n')
        _sends_what_it_reads_now(w, "the date the source now says was never sent")
        assert "2026-10-22" in _sent()
    finally:
        w.end()


def test_watch_sends_a_change_made_while_it_uploads_once(hub, monkeypatch):
    """A write that lands after a look and before the upload builds the bundle goes with that upload, and the watch
    compares later looks with what it sent: the same page is not sent again a minute later."""
    _watching()
    monkeypatch.setenv("RB_HUB_TOKEN", "rb_testtoken")
    w = _Watch()
    try:
        w.until(lambda: len(_taken()) == 1, "the first upload never came")
        arrived, release = threading.Event(), threading.Event()
        _Hub.asked = (arrived, release)
        assert main(["note", "add", "the held-out set is ImageNet val", "-e", "e1"]) == 0
        assert arrived.wait(30), w.lines
        assert main(["note", "add", "INT8 calibrates on 1024 training images", "-e", "e1"]) == 0
        release.set()
        w.until(lambda: len(_taken()) == 2, "the writes were never sent")
        assert "INT8 calibrates on 1024 training images" in _sent()
        time.sleep(MINUTE + 3 * LOOK)
        assert len(_taken()) == 2, _taken()
    finally:
        w.end()


def test_watch_says_what_to_do_and_starts_its_waits_again_once_a_failure_clears(hub, monkeypatch):
    """A record rb cannot read is said with what to do (rb doctor), and waited on longer each time; once it reads
    again, with nothing new to send, the next failure waits 30 s again, not where the last ones left off."""
    _watching()
    monkeypatch.setenv("RB_HUB_TOKEN", "rb_testtoken")
    w = _Watch()
    try:
        w.until(lambda: len(_taken()) == 1, "the first upload never came")
        exp = Path(".rb/experiments/e1/experiment.yaml")
        kept = exp.read_bytes()
        exp.write_text("rb: 1\nkind: [experiment\n", encoding="utf-8")
        w.until(lambda: len(w.said("Not synced:")) >= 2, "an unreadable record was never said")
        first = w.said("Not synced:")
        assert "rb doctor" in first[0] and first[0].endswith("Trying again in 30 s.") and first[1].endswith("Trying again in 1 min."), first
        exp.write_bytes(kept)
        n = len(w.said("Not synced:"))
        time.sleep(4 * 2 * LOOK + 3 * LOOK)                                 # the 2 min wait, paced, then looks that find it the same
        assert len(_taken()) == 1
        _Hub.refuse = 1
        assert main(["note", "add", "the held-out set is ImageNet val", "-e", "e1"]) == 0
        w.until(lambda: len(_taken()) == 2, "the write was never sent")
        later = w.said("Not synced:")[n:]
        assert later and later[-1].endswith("Trying again in 30 s."), later
    finally:
        w.end()


def test_watch_sends_nothing_for_a_look_that_finds_the_same(hub, monkeypatch):
    """A training loop rewriting its progress file makes the journal look again and again; the page rb sync sends does
    not show a file of progress numbers, and the look's own time is not compared: nothing is sent again."""
    _watching()
    monkeypatch.setenv("RB_HUB_TOKEN", "rb_testtoken")
    Path("out").mkdir()
    w = _Watch()
    try:
        w.until(lambda: len(_taken()) == 1, "the first upload never came")
        for step in range(1, 40):
            _put("out/progress.json", json.dumps({"step": step * 100, "epoch": step}) + "\n")
            time.sleep(LOOK / 2)
        time.sleep(MINUTE + 3 * LOOK)
        assert len(_taken()) == 1, _taken()
    finally:
        w.end()


def test_watch_asks_git_again_on_each_look(hub, monkeypatch):
    """Each look reads git afresh, as a new command line does: a config committed with a date before the run stops
    reading as written after it, and the watch sends the page that says so."""
    _watching()
    monkeypatch.setenv("RB_HUB_TOKEN", "rb_testtoken")
    Path("README.md").write_text("INT8 ResNet-50 on ImageNet\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], check=True)
    subprocess.run(["git", "commit", "-qm", "the readme"], check=True)
    Path("train.yaml").write_text("lr: 0.0001\n", encoding="utf-8")
    assert main(["spec", "set", "e1", "lr", "--source", "train.yaml#lr"]) == 0
    assert main(["run", "add", "e1", "m=0.71", "--produced-at", "2026-01-01T00:00:00Z"]) == 0
    page = lambda: zipfile.ZipFile(io.BytesIO(_Hub.received["bytes"])).read(  # noqa: E731
        next(n for n in zipfile.ZipFile(io.BytesIO(_Hub.received["bytes"])).namelist() if n.endswith("page.html"))).decode("utf-8")
    w = _Watch()
    try:
        w.until(lambda: len(_taken()) == 1, "the first upload never came")
        assert "settings: 1 provisional" in page()
        old = {**environment(), "GIT_AUTHOR_DATE": "2025-12-01T00:00:00Z", "GIT_COMMITTER_DATE": "2025-12-01T00:00:00Z"}
        subprocess.run(["git", "add", "train.yaml"], check=True, env=old)
        subprocess.run(["git", "commit", "-qm", "the train config"], check=True, env=old)
        _sends_what_it_reads_now(w, "the commit was never sent")
        assert "settings: 1 provisional" not in page()
    finally:
        w.end()


def test_login_asks_again_when_the_hub_is_down_for_a_moment(hub, monkeypatch, tmp_path):
    """A 503 while rb login waits for the approval is asked again (three in a row at most), so a code approved on the
    site still signs this machine in; a fourth in a row ends it, saying so."""
    monkeypatch.setenv("RB_CONFIG_DIR", str(tmp_path / "cfg"))
    _Hub.unavailable = 3
    code, env = hub("login")
    assert code == 0 and env.data["handle"] == "t" and _Hub.polls == 2, env.errors
    _Hub.unavailable, _Hub.polls = 4, 0
    code, env = hub("login", "--hub", os.environ["RB_HUB_URL"])
    assert code == 2 and env.errors[0].code == "E_HUB_REFUSED" and "Service Unavailable" in env.errors[0].message
