"""rb open on the shell (BP 4a), served to everyone since the switch (4b): `/` is the shell's page with the live module,
`/digest.json` answers the key of what a page shows, and `/o/<id>.html` one card. Each is guarded as every route of rb
open is: a foreign Host gets 403, a POST 405, another method 501, and a place or a version chosen from another site, or
typed into the address bar, 403.

The live module (rust/src/shell/assets/live.js) polls the key every 5 s and, when it moves, fetches the page again and
swaps it in, keeping the folds open, the inspector's cards, where each part was scrolled and a reason being typed.
Until R's frame draws `/`, the browser tests load a stand-in page (`Front`): one made from rb open's own /state.json
in the prototype's markup, with the live module as rb open adds it and a small script that keeps the contract the
shell's script keeps with it (rb:card when a card opens; on rb:swapped, its inspector drawn again). Every other request
the page makes goes to rb open itself, so the key, the polls and the terminal's writes are rb's."""
from __future__ import annotations

import base64
import contextlib
import html
import http.client
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional

import pytest

from rbtest import Open, as_the_user, places_record

pytestmark = pytest.mark.skipif(not shutil.which("git"), reason="the record is played in a git repository")

ROOT = Path(__file__).resolve().parents[1]
LIVE_JS = (ROOT / "rust" / "src" / "shell" / "assets" / "live.js").read_text(encoding="utf-8")
EXAMPLE = ROOT / "tests" / "fixtures" / "shell" / "int8-demo.live.view.json"
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # never a proxy near loopback
CHOSEN_ELSEWHERE = {"error": "A place or a version is chosen on rb open's own page."}
FOREIGN = {"error": "rb open answers only at 127.0.0.1, localhost or [::1]. Open the link it printed."}
POLL = 5.0          # the live module's interval while the page is in view


def needs(*groups: str) -> None:
    """A test that waits on another track's group skips with "pending <group>" until int lists it as merged."""
    from test_shell_contract import merged
    for g in groups:
        if g not in merged():
            pytest.skip(f"pending {g}")


def answer(url: str, headers: dict = None) -> tuple:
    """(status, the JSON or the text) of a GET."""
    try:
        with _OPENER.open(urllib.request.Request(url, headers=headers or {}), timeout=30) as r:
            status, body, ctype = r.status, r.read().decode("utf-8"), r.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        status, body, ctype = e.code, e.read().decode("utf-8"), e.headers.get("Content-Type", "")
    return status, json.loads(body) if ctype.startswith("application/json") else body


def raw(port: int, request: str) -> tuple:
    """The status line and the body rb open answers these bytes with."""
    with socket.create_connection(("127.0.0.1", port), timeout=30) as s:
        s.sendall(request.encode("latin-1"))
        s.shutdown(socket.SHUT_WR)
        got = b""
        while chunk := s.recv(65536):
            got += chunk
    head, _, body = got.partition(b"\r\n\r\n")
    return head.split(b"\r\n")[0].decode("latin-1"), body.decode("utf-8")


@pytest.fixture
def placed(tmp_path, monkeypatch):
    """places_record's clone (INT8 ResNet-50 on ImageNet, with a worktree of every kind), and rb open on it under the
    shell as everyone gets it, with no test switch, started as the user starts it."""
    as_the_user(monkeypatch)
    rec = places_record(tmp_path / "clone")
    for k in ("GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM", "RB_TELEMETRY", "DO_NOT_TRACK", "RB_ACTOR"):
        monkeypatch.setenv(k, rec.env[k])
    monkeypatch.delenv("RB_TEST_SHELL", raising=False)
    server = Open(rec.root)
    rec.ids = {p["branch"]: p["id"] for p in answer(server.url + "/state.json")[1]["places"]}
    yield rec, server
    server.close()


# ---------------------------------------------------------------- the routes

def test_the_digest_route_is_guarded(placed):
    """/digest.json answers the key of what is shown and the scope line, the same key while nothing changes; another
    place's key is its own. A foreign Host is refused, a POST and every other method too, and a place chosen from
    another site or typed into the address bar; a place rb did not list is said in rb's words. `/`, `/index.html`
    and `/o/<id>.html` refuse a foreign Host, a POST and every other method the same way."""
    rec, server = placed
    status, d = answer(server.url + "/digest.json")
    assert status == 200 and set(d) == {"key", "scope", "scope_short"}, d
    assert re.fullmatch(r"[0-9a-f]{64}", d["key"]) and d["scope"].startswith("rb looks in ") and d["scope_short"].startswith("rb last looked at ")
    assert answer(server.url + "/digest.json")[1]["key"] == d["key"]                 # every request recomputes it, the same
    chosen = f"place={rec.ids['exp-c']}"
    status, c = answer(f"{server.url}/digest.json?{chosen}", {"Sec-Fetch-Site": "same-origin"})
    assert status == 200 and c["key"] != d["key"] and c["scope"] is None and c["scope_short"] is None   # read in a preview: not looked at
    for site in ("cross-site", "same-site", "none"):
        assert answer(f"{server.url}/digest.json?{chosen}", {"Sec-Fetch-Site": site}) == (403, CHOSEN_ELSEWHERE), site
    assert answer(server.url + "/digest.json", {"Sec-Fetch-Site": "cross-site"})[0] == 200   # the record shown first, as /state.json
    assert answer(server.url + "/digest.json?place=p_0000000000") == (404, {"error": "p_0000000000 is not a place rb listed here."})
    # and so is every route of the shell's: the page, its card and the key, each before anything is read
    port = server.port
    for route in ("/", "/index.html", "/o/c-c1.html", "/digest.json"):
        status, body = raw(port, f"GET {route} HTTP/1.1\r\nHost: attacker.example:{port}\r\n\r\n")
        assert status == "HTTP/1.0 403 Forbidden" and json.loads(body) == FOREIGN, route
        status, body = raw(port, f"POST {route} HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Length: 0\r\n\r\n")
        assert status == "HTTP/1.0 405 Method Not Allowed" and json.loads(body) == {"error": "rb open only reads. Decisions run in your terminal."}, route
        for method in ("PUT", "HEAD", "DELETE"):
            status, _ = raw(port, f"{method} {route} HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
            assert status == f"HTTP/1.0 501 Unsupported method ('{method}')", (route, method, status)


def test_a_typed_place_is_refused_on_root_and_o(placed):
    """`/` and `/o/<id>.html` take a place or a version only from rb open's own page: chosen from another site or typed
    into the address bar, 403 before anything is read; a place rb did not list, rb's words for it."""
    rec, server = placed
    for route in ("/", "/index.html", "/o/c-c1.html"):
        for ask in (f"place={rec.ids['exp-c']}", "at=" + "0" * 40):
            for site in ("none", "cross-site", "same-site"):
                assert answer(f"{server.url}{route}?{ask}", {"Sec-Fetch-Site": site}) == (403, CHOSEN_ELSEWHERE), (route, ask, site)
        status, said = answer(f"{server.url}{route}?place=p_0000000000", {"Sec-Fetch-Site": "same-origin"})
        assert (status, said) == (404, {"error": "p_0000000000 is not a place rb listed here."}), route
        status, said = answer(f"{server.url}{route}?place={rec.ids['exp-b']}", {"Sec-Fetch-Site": "same-origin"})
        assert status == 404 and said["error"].endswith("has no record of its own to show: it writes to this record."), route


def test_a_card_is_served_alone_or_said_missing_in_rbs_words(tmp_path, monkeypatch):
    """`/o/<id>.html` answers one card of the document, as the shell draws it; an id that names none is said as the
    address grammar says it."""
    as_the_user(monkeypatch)
    rec = places_record(tmp_path / "clone")
    for k in ("GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM", "RB_TELEMETRY", "DO_NOT_TRACK", "RB_ACTOR"):
        monkeypatch.setenv(k, rec.env[k])
    monkeypatch.setenv("RB_TEST_SHELL", "1")
    monkeypatch.setenv("RB_TEST_SHELL_MODEL", str(EXAMPLE))
    server = Open(rec.root)
    try:
        assert answer(server.url + "/o/c-zz.html") == (404, {"error": "nothing here is called 'c-zz'"})
        assert answer(server.url + "/o/x-e1%3Aam%3A9.html") == (404, {"error": "nothing here is called 'x-e1:am:9'"})
        needs("C2")
        status, card = answer(server.url + "/o/c-c1.html")
        doc = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        assert status == 200 and re.match(r'<[a-z]+ [^>]*id="c-c1"', card) and html.escape(doc["findings"]["c1"]["statement"], quote=False) in card
    finally:
        server.close()


def test_the_shell_is_served_without_a_test_switch(tmp_path, monkeypatch):
    """Without RB_TEST_SHELL rb open serves the live shell, its digest and cards, with place choices guarded."""
    as_the_user(monkeypatch)
    rec = places_record(tmp_path / "clone")
    for k in ("GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM", "RB_TELEMETRY", "DO_NOT_TRACK", "RB_ACTOR"):
        monkeypatch.setenv(k, rec.env[k])
    monkeypatch.delenv("RB_TEST_SHELL", raising=False)
    server = Open(rec.root)
    try:
        status, page = answer(server.url + "/")
        assert status == 200 and 'id="rb-live"' in page and "/digest.json" in page
        assert answer(server.url + "/digest.json")[0] == 200
        status, card = answer(server.url + "/o/c-c1.html")
        assert status == 200 and 'id="c-c1"' in card
        for route in ("/", "/index.html", "/digest.json", "/o/c-c1.html"):
            for choice in ("place=p_0000000000", "at=" + "0" * 40):
                assert answer(f"{server.url}{route}?{choice}", {"Sec-Fetch-Site": "none"}) == (
                    403, {"error": "A place or a version is chosen on rb open's own page."})
    finally:
        server.close()


def test_a_found_item_shows_without_a_record_write(placed):
    """What the journal finds changes the key though the record is not written (the record's own digest, /state.json's,
    stays as it was): a changed config nothing cites, then a results file nobody recorded."""
    rec, server = placed
    (rec.root / "configs").mkdir()
    (rec.root / "configs" / "eval.yaml").write_text("dataset: imagenet-val\n", encoding="utf-8")
    rec.git("add", "configs")
    rec.git("commit", "-qm", "the eval config")
    rec.rb("status")                                                    # rb looks: the commit it sees is the start
    before, record = answer(server.url + "/digest.json")[1]["key"], answer(server.url + "/state.json")[1]["digest"]
    (rec.root / "configs" / "eval.yaml").write_text("dataset: imagenet-val\ncrop: resize\n", encoding="utf-8")
    rec.git("commit", "-qam", "resize")
    rec.rb("status")                                                    # rb looks again, in the terminal
    st = answer(server.url + "/state.json")[1]
    assert st["home"]["uncited"] == 1 and st["digest"] == record        # found, and no record write
    after = answer(server.url + "/digest.json")[1]["key"]
    assert after != before
    # and a results file nobody recorded, which rb proposes as a run
    (rec.root / "out").mkdir()
    (rec.root / "out" / "metrics.json").write_text('{"fp32.top1": 0.763, "int8.top1": 0.759}\n', encoding="utf-8")
    rec.rb("status")
    st = answer(server.url + "/state.json")[1]
    assert [f["title"] for f in st["home"]["found"]] == ["Record metrics.json on e1"] and st["digest"] == record, (st["home"]["found"], st["home"]["unplaced"])
    assert answer(server.url + "/digest.json")[1]["key"] not in (before, after)


def test_a_reading_that_moves_without_a_write_moves_the_key(placed):
    """What rb computes on reading the record moves the key with no record file written and nothing found: a setting's
    source that now states another value is a conflict on the page, so the page's key moves, and stays put after."""
    rec, server = placed
    (rec.root / "configs").mkdir()
    (rec.root / "configs" / "train.yaml").write_text("optim:\n  lr: 0.0001\n", encoding="utf-8")
    rec.git("add", "configs")
    rec.git("commit", "-qm", "the train config")
    rec.rb("spec", "set", "e1", "optim.lr", "--source", "configs/train.yaml#optim.lr")
    rec.rb("status")
    before, record = answer(server.url + "/digest.json")[1]["key"], answer(server.url + "/state.json")[1]["digest"]
    assert answer(server.url + "/digest.json")[1]["key"] == before
    (rec.root / "configs" / "train.yaml").write_text("optim:\n  lr: 0.0003\n", encoding="utf-8")
    rec.git("commit", "-qam", "a higher lr")
    rec.rb("status")
    after = answer(server.url + "/digest.json")[1]["key"]
    assert after != before and answer(server.url + "/state.json")[1]["digest"] == record
    assert answer(server.url + "/digest.json")[1]["key"] == after


def test_the_digest_route_looks_on_its_cadence(placed):
    """The route itself has the journal look, at most once every 10 s as rb open always looked, so a change shows on a
    page whose polls are all that asks rb open anything."""
    rec, server = placed
    (rec.root / "configs").mkdir()
    (rec.root / "configs" / "eval.yaml").write_text("dataset: imagenet-val\n", encoding="utf-8")
    rec.git("add", "configs")
    rec.git("commit", "-qm", "the eval config")
    rec.rb("status")
    before = answer(server.url + "/digest.json")[1]["key"]              # its look, now
    (rec.root / "configs" / "eval.yaml").write_text("dataset: imagenet-val\ncrop: resize\n", encoding="utf-8")
    rec.git("commit", "-qam", "resize")
    deadline = time.monotonic() + 30
    while answer(server.url + "/digest.json")[1]["key"] == before:
        assert time.monotonic() < deadline, "the route never looked again"
        time.sleep(1)
    assert answer(server.url + "/state.json")[1]["home"]["uncited"] == 1


def test_the_key_follows_where_the_page_shows(placed):
    """The key moves with what the page says of where it shows the record, which the record's digest does not cover
    (/state.json's stays as it was): a run recorded in exp-c (what Elsewhere says it holds), a new worktree (the
    places) and a commit (the place's own line), each once rb open lists them again, at most 10 s on. While nothing
    changes the key holds across that listing. On another place's page a write to the record rb open started on moves
    that page's key by the places alone."""
    rec, server = placed
    record = answer(server.url + "/state.json")[1]["digest"]
    key = answer(server.url + "/digest.json")[1]["key"]
    time.sleep(11)                                                      # rb open lists the places again
    assert answer(server.url + "/digest.json")[1]["key"] == key

    def moved(what: str, part: str, act) -> None:
        nonlocal key
        was = answer(server.url + "/state.json")[1][part]
        act()
        deadline = time.monotonic() + 30
        # what rb status says of the repository (its commit, what is elsewhere) may move the key before the listing does
        while (now := answer(server.url + "/digest.json")[1]["key"]) == key or answer(server.url + "/state.json")[1][part] == was:
            assert time.monotonic() < deadline, what
            time.sleep(1)
        st = answer(server.url + "/state.json")[1]
        assert st[part] != was and st["digest"] == record, what
        key = answer(server.url + "/digest.json")[1]["key"]

    moved("a run in exp-c", "elsewhere", lambda: rec.rb("run", "add", "e1", "fp32.top1=0.770", "int8.top1=0.766", cwd=rec.base / "wt-c"))
    moved("a new worktree", "places", lambda: rec.worktree("exp-z", rec.base / "wt-z", rec.start))

    def commit() -> None:
        (rec.root / "notes.txt").write_text("calibration: 512 images\n", encoding="utf-8")
        rec.git("add", "notes.txt")
        rec.git("commit", "-qm", "a note")
    moved("a commit", "place", commit)
    # another place shown: a run recorded here moves its key by the places alone (its own record is not written)
    there, mine = f"?place={rec.ids['exp-c']}", {"Sec-Fetch-Site": "same-origin"}
    key, record = answer(f"{server.url}/digest.json{there}", mine)[1]["key"], answer(f"{server.url}/state.json{there}", mine)[1]["digest"]
    rec.rb("run", "add", "e1", "fp32.top1=0.768", "int8.top1=0.764")
    deadline = time.monotonic() + 30
    while answer(f"{server.url}/digest.json{there}", mine)[1]["key"] == key:
        assert time.monotonic() < deadline, "a run here, on exp-c's page"
        time.sleep(1)
    assert answer(f"{server.url}/state.json{there}", mine)[1]["digest"] == record


def test_a_write_elsewhere_moves_each_key_once_in_one_poll(placed):
    """A run recorded in exp-c moves the key of exp-c's page and of the home (its Elsewhere) within one poll, and
    once: the listing of the places follows that record's log too, so neither moves again when the listing lapses."""
    rec, server = placed
    there, mine = f"?place={rec.ids['exp-c']}", {"Sec-Fetch-Site": "same-origin"}
    keys = lambda: (answer(server.url + "/digest.json")[1]["key"], answer(f"{server.url}/digest.json{there}", mine)[1]["key"])  # noqa: E731
    time.sleep(11)                                                      # the listing as it stands
    before = keys()
    rec.rb("run", "add", "e1", "fp32.top1=0.771", "int8.top1=0.767", cwd=rec.base / "wt-c")
    wrote = time.monotonic()
    while (now := keys())[0] == before[0] or now[1] == before[1]:
        assert time.monotonic() - wrote < POLL, (before, now)
        time.sleep(0.2)
    time.sleep(12)                                                      # the listing lapses
    assert keys() == now


def test_the_page_is_the_shell_with_the_live_module(placed):
    """`/` is the shell's page of the place chosen, with the live module, which holds the key the digest route gives
    for it and the query that asks for it again."""
    needs("M0", "R1")
    rec, server = placed
    for query in ("", f"?place={rec.ids['exp-c']}"):
        status, page = answer(server.url + "/" + query, {"Sec-Fetch-Site": "same-origin"})
        assert status == 200 and page.count('<script id="rb-live"') == 1 and LIVE_JS in page
        tag = re.search(r'<script id="rb-live" data-key="([0-9a-f]{64})" data-query="([^"]*)">', page)
        assert tag and html.unescape(tag.group(2)) == query
        assert tag.group(1) == answer(server.url + "/digest.json" + query, {"Sec-Fetch-Site": "same-origin"})[1]["key"]
        assert page.index('<script id="rb-live"') < page.index("</head>")


# ---------------------------------------------------------------- a browser

def chrome_path() -> str:
    """RB_CHROME, then where Chrome is installed by default, then a Chrome on PATH; none, a skip (a failure in CI)."""
    if os.environ.get("RB_CHROME"):
        return os.environ["RB_CHROME"]
    usual = {"darwin": ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "/Applications/Chromium.app/Contents/MacOS/Chromium"],
             "win32": [os.path.join(os.environ.get(v, ""), "Google", "Chrome", "Application", "chrome.exe") for v in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA")]}
    for p in usual.get(sys.platform, ["/usr/bin/google-chrome", "/usr/bin/google-chrome-stable", "/opt/google/chrome/chrome", "/usr/bin/chromium"]):
        if p and os.path.isfile(p):
            return p
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome"):
        if shutil.which(name):
            return shutil.which(name)
    if os.environ.get("CI"):
        pytest.fail("no Chrome here, and CI runs the live module's browser tests")
    pytest.skip("the live module's tests run in Chrome, and there is none here")


class _Socket:
    """A WebSocket client, as much of RFC 6455 as Chrome's debugging port needs: text frames out, masked; frames in."""

    def __init__(self, url: str):
        u = urllib.parse.urlparse(url)
        self.s = socket.create_connection((u.hostname, u.port), timeout=60)
        key = base64.b64encode(os.urandom(16)).decode()
        self.s.sendall((f"GET {u.path} HTTP/1.1\r\nHost: {u.hostname}:{u.port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                        f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        self.buf = b""
        while b"\r\n\r\n" not in self.buf:
            chunk = self.s.recv(4096)
            assert chunk, "Chrome closed its debugging socket"
            self.buf += chunk
        head, self.buf = self.buf.split(b"\r\n\r\n", 1)
        assert head.startswith(b"HTTP/1.1 101"), head

    def _read(self, n: int) -> bytes:
        while len(self.buf) < n:
            chunk = self.s.recv(1 << 20)
            if not chunk:
                raise ConnectionError("Chrome closed its debugging socket")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def _frame(self, op: int, data: bytes) -> None:
        n, mask = len(data), os.urandom(4)
        head = bytes([0x80 | op]) + (bytes([0x80 | n]) if n < 126 else bytes([0xFE]) + n.to_bytes(2, "big") if n < 65536 else bytes([0xFF]) + n.to_bytes(8, "big"))
        self.s.sendall(head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def send(self, text: str) -> None:
        self._frame(1, text.encode("utf-8"))

    def recv(self) -> str:
        parts = []
        while True:
            b0, b1 = self._read(2)
            n = b1 & 0x7F
            if n == 126:
                n = int.from_bytes(self._read(2), "big")
            elif n == 127:
                n = int.from_bytes(self._read(8), "big")
            mask = self._read(4) if b1 & 0x80 else b""
            data = self._read(n)
            if mask:
                data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
            op = b0 & 0x0F
            if op == 8:
                raise ConnectionError("Chrome closed its debugging socket")
            if op == 9:
                self._frame(10, data)
                continue
            if op in (0, 1, 2):
                parts.append(data)
                if b0 & 0x80:
                    return b"".join(parts).decode("utf-8")

    def close(self) -> None:
        with contextlib.suppress(OSError):
            self.s.close()


def _devtools_port(path: Path) -> Optional[int]:
    """The port Chrome writes on the first line of DevToolsActivePort, once that line is whole: the file is there,
    empty or with part of the port, while Chrome is still writing it."""
    with contextlib.suppress(OSError, ValueError, UnicodeDecodeError):
        first, sep, _ = path.read_text(encoding="utf-8").partition("\n")
        if sep:
            return int(first)
    return None


def test_the_devtools_port_is_read_once_its_line_is_whole(tmp_path):
    f = tmp_path / "DevToolsActivePort"
    assert _devtools_port(f) is None
    for partial in ("", "92", "9222"):
        f.write_text(partial, encoding="utf-8")
        assert _devtools_port(f) is None, partial
    f.write_text("9222\n/devtools/browser/x\n", encoding="utf-8")
    assert _devtools_port(f) == 9222


class Chrome:
    """Headless Chrome with one page, driven over the DevTools protocol: `js` evaluates in the page (awaiting a
    promise), `type` types as a keyboard does into what has focus, `wait` waits for an expression to hold. Every
    exception the page throws is kept in `thrown`."""

    def __init__(self, folder: Path, width: int = 1280, height: int = 900):
        profile, home = folder / "profile", folder / "home"
        profile.mkdir(parents=True)
        home.mkdir()
        args = [chrome_path(), "--headless=new", "--remote-debugging-port=0", f"--user-data-dir={profile}", "--no-first-run", "--no-default-browser-check",
                "--disable-background-networking", "--disable-component-update", "--disable-default-apps", "--disable-extensions", "--disable-sync",
                "--mute-audio", "--hide-scrollbars", "--password-store=basic", "--use-mock-keychain", f"--window-size={width},{height}"]
        if sys.platform.startswith("linux"):
            args.append("--no-sandbox")       # a container's Chrome has no user namespaces; the pages are rb's own, on loopback
        env = dict(os.environ) if sys.platform == "win32" else {**os.environ, "HOME": str(home), "XDG_CONFIG_HOME": str(home / ".config"), "XDG_CACHE_HOME": str(home / ".cache")}
        self.proc = subprocess.Popen([*args, "about:blank"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
        deadline = time.monotonic() + 60
        port = None
        while port is None:
            port = _devtools_port(profile / "DevToolsActivePort")
            if port is None:
                assert self.proc.poll() is None and time.monotonic() < deadline, f"Chrome did not start (exit {self.proc.poll()})"
                time.sleep(0.05)
        while True:
            with contextlib.suppress(OSError, ValueError):
                with _OPENER.open(f"http://127.0.0.1:{port}/json/list", timeout=10) as r:
                    pages = [t for t in json.load(r) if t.get("type") == "page"]
                if pages:
                    break
            assert time.monotonic() < deadline, "Chrome opened no page"
            time.sleep(0.05)
        self.ws, self.n, self.thrown = _Socket(pages[0]["webSocketDebuggerUrl"]), 0, []
        self.send("Page.enable")
        self.send("Runtime.enable")
        self.send("Emulation.setDeviceMetricsOverride", width=width, height=height, deviceScaleFactor=1, mobile=False)

    def send(self, method: str, **params) -> dict:
        self.n += 1
        self.ws.send(json.dumps({"id": self.n, "method": method, "params": params}))
        while True:
            m = json.loads(self.ws.recv())
            if m.get("method") == "Runtime.exceptionThrown":
                d = m["params"]["exceptionDetails"]
                self.thrown.append((d.get("exception") or {}).get("description") or d.get("text"))
            if m.get("id") == self.n:
                assert "error" not in m, (method, m["error"])
                return m.get("result", {})

    def js(self, expression: str):
        r = self.send("Runtime.evaluate", expression=expression, awaitPromise=True, returnByValue=True)
        assert "exceptionDetails" not in r, (expression, r["exceptionDetails"])
        return r["result"].get("value")

    def goto(self, url: str) -> None:
        self.send("Page.navigate", url=url)
        assert self.wait("document.readyState === 'complete' && location.href !== 'about:blank'", 30), url

    def type(self, text: str) -> None:
        self.send("Input.insertText", text=text)

    def wait(self, expression: str, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.js(expression):
                return True
            time.sleep(0.1)
        return bool(self.js(expression))

    def close(self) -> None:
        self.ws.close()
        self.proc.terminate()
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()


@pytest.fixture
def chrome(tmp_path):
    browser = Chrome(tmp_path / "chrome")
    yield browser
    browser.close()


# The contract the shell's script keeps with the live module (requested of A): a card opened in the inspector is an
# rb:card event; on rb:swapped the script draws its inspector again from its own state and says so (preventDefault).
STAND_IN_SCRIPT = r"""
(() => {
  document.documentElement.classList.add('app');
  let stack = [], view = 'overview';
  const byId = id => document.getElementById(id);
  function draw() {
    for (const v of document.querySelectorAll('#main .view')) v.classList.toggle('on', v.id === view);
    const body = document.querySelector('#insp .ibody');
    body.textContent = '';
    const src = stack.length && byId(stack[stack.length - 1]);
    if (!src) return;
    const k = src.cloneNode(true);
    for (const e of [k, ...k.querySelectorAll('[id]')]) { e.dataset.id = e.id; e.removeAttribute('id'); }
    body.appendChild(k);
  }
  function compose(box) {
    const why = box.querySelector('input.why').value.trim();
    box.querySelector('.composer .cmd').textContent = box.querySelector('.acts [data-action="' + box.dataset.chosen + '"] .cmd').textContent
      .replace('--why "..."', "--why '" + why + "'");
  }
  document.addEventListener('DOMContentLoaded', () => {
    const aside = document.createElement('aside');
    aside.id = 'insp';
    aside.innerHTML = '<div class="ibody"></div>';
    byId('main').after(aside);
    draw();
  });
  document.addEventListener('click', e => {
    const go = e.target.closest('[data-go]');
    if (go) { e.preventDefault(); stack = [go.dataset.go]; draw(); document.dispatchEvent(new CustomEvent('rb:card', { detail: { id: go.dataset.go } })); return; }
    const pick = e.target.closest('[data-act="pick"]');
    if (pick) {
      const box = pick.closest('.decide');
      box.dataset.chosen = pick.dataset.action;
      box.querySelector('.composer').hidden = false;
      compose(box);
      box.querySelector('input.why').focus({ preventScroll: true });
    }
  });
  document.addEventListener('input', e => { const box = e.target.closest('.decide'); if (box && box.dataset.chosen) compose(box); });
  document.addEventListener('rb:swapped', e => { e.preventDefault(); draw(); });
})();
"""

STAND_IN_CSS = """
html.app body { margin: 0; display: grid; grid-template-columns: 180px minmax(0, 1fr) 360px; height: 100vh; overflow: hidden; }
html.app header.top { display: none; }
html.app #main, html.app #insp { overflow: auto; height: 100vh; }
.view { display: none; } .view.on { display: block; }
details.nd { min-height: 64px; border-bottom: 1px solid #ccc; }
.fill { height: 72px; margin: 0; }
"""


def stand_in_page(state: dict, key: str, query: str) -> str:
    """rb open's page of this state, in the prototype's markup, with the live module as rb open adds it."""
    e, h = html.escape, state["home"]
    runs = list(reversed(h["runs"]))                                   # newest first, so a new run lands above
    row = lambda r: (f'<details class="nd" data-row data-fold="run:{e(r["run_id"])}"><summary>run {e(r["run_id"])}</summary>'
                     f'<p><a href="#run-{e(r["run_id"])}" data-go="run-{e(r["run_id"])}">open {e(r["run_id"])}</a></p>'
                     f'<article class="card" id="run-{e(r["run_id"])}" data-home="overview" data-crumb="run {e(r["run_id"])}"><h3>{e(r["run_id"])}</h3>'
                     f'<details data-fold="run:{e(r["run_id"])}:receipts"><summary>receipts</summary><p>{e(json.dumps(r.get("results")))}</p></details></article></details>')
    fill = "".join(f'<details class="fill" data-fold="fill:{i}"><summary>part {i}</summary><p>{i}</p></details>' for i in range(40))
    call = ('<article class="card call" id="call-c1" data-home="your-call" data-crumb="call c1"><h3>c1</h3>'
            f'<p class="runs">{len(runs)} runs on e1</p>'
            '<details data-fold="call:c1:why"><summary>Why this needs a call</summary><p>one seed per arm</p></details><section class="part decide">'
            '<div class="seg"><button type="button" data-act="pick" data-action="accept">Accept</button>'
            '<button type="button" data-act="pick" data-action="investigate">Investigate</button></div>'
            '<div class="composer" hidden><label>why<input class="why" type="text"></label><pre class="cmd"></pre></div>'
            '<ul class="acts"><li data-action="accept"><span class="cmd">rb decide c1 accept --why "..."</span></li>'
            '<li data-action="investigate"><span class="cmd">rb decide c1 investigate --why "..."</span></li></ul></section></article>')
    live = f'<script id="rb-live" data-key="{e(key)}" data-query="{e(query)}">{LIVE_JS}</script>'
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><title>{e(h["title"])}</title><style>{STAND_IN_CSS}</style>'
            f'<script>{STAND_IN_SCRIPT}</script>{live}</head><body><header class="top" id="top"><span class="ttl" id="top-title" data-swap>{e(h["title"])}</span></header>'
            f'<nav class="nav" id="nav"><a href="#overview">Overview</a> <span id="nav-runs">Runs {len(runs)}</span> '
            f'<a href="#your-call">Your call <span class="n" id="nav-calls">{len(h["calls"])}</span></a></nav>'
            f'<main id="main" tabindex="-1"><section class="view" id="overview" data-title="{e(h["title"])}"><h1>{e(h["title"])}</h1>'
            f'<p data-live="scope">{e(h.get("scope") or "")}</p><p><a href="#call-c1" data-go="call-c1">the call on c1</a></p>'
            f'{"".join(row(r) for r in runs)}{fill}</section><section class="view" id="your-call" data-title="Your call">{call}</section></main></body></html>')


class Front:
    """The shell's page as rb open will serve it, in front of rb open itself until R's frame draws it: `/` is
    stand_in_page of rb open's /state.json for the query, its key taken first, as rb open takes it; every other
    request goes to rb open (or to `routes`' server for its path) as the page asked it. `asked` notes each request the
    page made, with rb's answer."""

    def __init__(self, back: str, routes: dict = None):
        self.back, self.routes, self.asked = back, routes or {}, []
        self.down = False           # rb open as if stopped: its digest unanswered
        front = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                path, _, q = self.path.partition("?")
                q = "?" + q if q else ""
                headers = {k: v for k, v in self.headers.items() if k.lower() in ("x-rb-usage", "sec-fetch-site")}
                if front.down and path == "/digest.json":
                    status, ctype, body = 503, "application/json", b'{"error": "rb open is not answering"}'
                elif path == "/":
                    status, ctype, body = front.forward("/digest.json" + q, headers)
                    if status == 200:
                        key = json.loads(body)["key"]
                        status, ctype, body = front.forward("/state.json" + q, headers)
                        if status == 200:
                            ctype, body = "text/html; charset=utf-8", stand_in_page(json.loads(body), key, q).encode("utf-8")
                else:
                    status, ctype, body = front.forward(self.path, headers)
                front.asked.append((self.path, status))
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        self.thread.start()

    def forward(self, path: str, headers: dict) -> tuple:
        base = urllib.parse.urlparse(self.routes.get(path.partition("?")[0], self.back))
        c = http.client.HTTPConnection(base.hostname, base.port, timeout=30)
        try:
            c.request("GET", path, headers=headers)
            r = c.getresponse()
            return r.status, r.getheader("Content-Type", ""), r.read()
        finally:
            c.close()

    def count(self, path: str) -> int:
        return sum(1 for p, _ in list(self.asked) if p.partition("?")[0] == path)

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=10)


@pytest.fixture
def front(placed):
    rec, server = placed
    f = Front(server.url)
    yield rec, server, f
    f.close()


def opened(chrome: Chrome, url: str) -> None:
    chrome.goto(url)
    assert chrome.wait("!!window.rbLive && !!document.querySelector('#insp .ibody')", 30), chrome.thrown


def until(holds, timeout: float, why=None) -> None:
    """Wait for `holds()`, `timeout` seconds at most; `why()` says what was there instead."""
    deadline = time.monotonic() + timeout
    while not holds():
        assert time.monotonic() < deadline, why() if why else None
        time.sleep(0.1)


def test_a_terminal_write_keeps_what_you_had_open(front, chrome):
    """A run recorded in the terminal shows within one poll, and the page keeps what was open: a fold, the card in the
    inspector and a fold inside it, the action chosen and the reason being typed (still in focus, the caret where it
    was), and where the record was scrolled, though the new run lands above."""
    rec, server, f = front
    opened(chrome, f.url + "/")
    chrome.js(f"document.querySelector('details[data-fold=\"run:{rec.run_main}\"] > summary').click()")
    chrome.js("document.querySelector('[data-go=\"call-c1\"]').click()")
    chrome.js("document.querySelector('#insp [data-action=\"investigate\"][data-act=\"pick\"]').click()")
    chrome.js("document.querySelector('#insp input.why').focus()")
    chrome.type("the eval set changed")
    chrome.js("const i = document.querySelector('#insp input.why'); i.setSelectionRange(4, 4)")
    chrome.js("document.querySelector('#insp details[data-fold]').open = true")
    chrome.js("document.getElementById('main').scrollTop = 700")
    at = chrome.js("(() => { const m = document.getElementById('main').getBoundingClientRect().top;"
                   " const d = [...document.querySelectorAll('#main details.fill')].find(x => x.getBoundingClientRect().top >= m);"
                   " return [d.dataset.fold, d.getBoundingClientRect().top - m]; })()")
    runs = chrome.js("document.querySelectorAll('#main details.nd').length")
    assert chrome.js("document.querySelector('#insp .ibody > .card .runs').textContent") == f"{runs} runs on e1"
    chrome.js("window.swaps = 0; document.addEventListener('rb:swapped', () => swaps++)")
    asked = f.count("/")
    rec.rb("run", "add", "e1", "fp32.top1=0.764", "int8.top1=0.760")
    written = time.monotonic()
    assert chrome.wait(f"document.querySelectorAll('#main details.nd').length === {runs + 1}", POLL + 4), (f.asked[-6:], chrome.thrown)
    assert time.monotonic() - written <= POLL + 3 and f.count("/") == asked + 1
    kept = chrome.js("(() => { const i = document.querySelector('#insp input.why');"
                     " return {fold: document.querySelector('details[data-fold=\"run:" + rec.run_main + "\"]').open,"
                     " card: document.querySelector('#insp .ibody > .card').dataset.id, inner: document.querySelector('#insp details[data-fold]').open,"
                     " said: document.querySelector('#insp .ibody > .card .runs').textContent, swaps: window.swaps,"
                     " why: i.value, focused: document.activeElement === i, caret: [i.selectionStart, i.selectionEnd],"
                     " chosen: document.querySelector('#insp .decide').dataset.chosen, cmd: document.querySelector('#insp .composer .cmd').textContent,"
                     " composer: !document.querySelector('#insp .composer').hidden}; })()")
    # the inspector is drawn again from the new page (the card says the new run), once, and keeps what was open in it
    assert kept == {"fold": True, "card": "call-c1", "inner": True, "said": f"{runs + 1} runs on e1", "swaps": 1,
                    "why": "the eval set changed", "focused": True, "caret": [4, 4],
                    "chosen": "investigate", "cmd": "rb decide c1 investigate --why 'the eval set changed'", "composer": True}, kept
    now = chrome.js("(() => { const m = document.getElementById('main').getBoundingClientRect().top;"
                    f" return document.querySelector('details[data-fold=\"{at[0]}\"]').getBoundingClientRect().top - m; }})()")
    assert abs(now - at[1]) < 1.5, (at, now)
    assert chrome.js("document.getElementById('main').scrollTop") > 700          # the new run above moved the record down
    assert chrome.thrown == []


def test_a_poll_never_counts(front, chrome):
    """The page asks rb open's digest every 5 s; no poll, and no swap after a write, asks for an inspection. The first
    render asks once, and a card opened once more. A write fetches the page once, and no poll after it again."""
    rec, server, f = front
    opened(chrome, f.url + "/")
    until(lambda: f.count("/telemetry/inspected") == 1, 10, lambda: f.asked)
    polls = f.count("/digest.json")
    until(lambda: f.count("/digest.json") >= polls + 2, 2 * POLL + 4, lambda: f.asked)
    pages = f.count("/")
    rec.rb("run", "add", "e1", "fp32.top1=0.764", "int8.top1=0.760")
    until(lambda: f.count("/") == pages + 1, POLL + 4, lambda: f.asked)
    after = f.count("/digest.json")
    until(lambda: f.count("/digest.json") >= after + 3, 3 * POLL + 4, lambda: f.asked)    # polls past rb open's next listing
    # one write, one fetch: the new page's key is taken, and the places listed follow the write in the same poll
    assert f.count("/") == pages + 1, f.asked
    assert f.count("/telemetry/inspected") == 1, f.asked
    chrome.js("document.querySelector('[data-go=\"call-c1\"]').click()")
    until(lambda: f.count("/telemetry/inspected") == 2, 10, lambda: f.asked)
    assert [s for p, s in f.asked if p.startswith("/telemetry/inspected")] == [200, 200]
    assert chrome.thrown == []


def test_a_place_switch_swaps_nav_title_and_count(front, chrome):
    """A place chosen on the page is fetched and swapped in: the nav with its counts and Your call (N), and the title;
    from then on the page polls that place's key."""
    rec, server, f = front
    opened(chrome, f.url + "/")
    shown = ("[document.title, document.getElementById('top-title').textContent, document.getElementById('nav-runs').textContent,"
             " document.getElementById('nav-calls').textContent]")
    here = chrome.js(shown)
    exp_s = f"?place={rec.ids['exp-s']}"
    st = answer(f"{server.url}/state.json{exp_s}", {"Sec-Fetch-Site": "same-origin"})[1]
    # no script answers this swap: the view shown stays shown all the same
    chrome.js("window.addEventListener('rb:swapped', e => e.stopImmediatePropagation(), {capture: true, once: true})")
    assert chrome.js(f"rbLive.show({json.dumps(exp_s)})") is True
    assert chrome.js("document.getElementById('overview').classList.contains('on')") is True
    there = chrome.js(shown)
    assert there == ["another study", "another study", f"Runs {len(st['home']['runs'])}", str(len(st['home']['calls']))] and there != here
    polls = f.count("/digest.json")
    until(lambda: f.count("/digest.json") > polls, POLL + 4, lambda: f.asked)
    assert [p for p, _ in f.asked if p.startswith("/digest.json")][-1] == "/digest.json" + exp_s
    # a place rb does not list now is not shown, the page stays as it was, and rb's words go to whoever says them
    chrome.js("window.failedSaid = []; document.addEventListener('rb:failed', e => failedSaid.push(e.detail))")
    assert chrome.js("rbLive.show('?place=p_0000000000')") is False and chrome.js("document.title") == "another study"
    assert chrome.js("failedSaid") == [{"error": "p_0000000000 is not a place rb listed here.", "query": "?place=p_0000000000"}]
    assert chrome.thrown == []


def test_the_scope_line_moves_in_place(front, chrome):
    """The scope line is said again from each poll's answer, where the page marks it, with no fetch of the page."""
    rec, server, f = front
    opened(chrome, f.url + "/")
    chrome.js("document.querySelector('[data-live=\"scope\"]').textContent = 'an older line'")
    pages = f.count("/")
    assert chrome.wait("document.querySelector('[data-live=\"scope\"]').textContent.startsWith('rb looks in ')", POLL + 4), f.asked
    assert f.count("/") == pages


def test_the_page_polls_less_while_hidden_or_unanswered(front, chrome):
    """Every 5 s while the page is in view; while it is hidden, or while rb open does not answer, each wait twice the
    last (10 s, 20 s, up to a minute); shown again, it asks at once and every 5 s again."""
    rec, server, f = front
    opened(chrome, f.url + "/")
    polls = lambda: f.count("/digest.json")
    until(lambda: polls() >= 1, POLL + 4, lambda: f.asked)
    hide = ("v => { Object.defineProperty(document, 'hidden', {configurable: true, get: () => v});"
            " Object.defineProperty(document, 'visibilityState', {configurable: true, get: () => v ? 'hidden' : 'visible'});"
            " document.dispatchEvent(new Event('visibilitychange')); }")
    chrome.js(f"({hide})(true)")
    n = polls()
    time.sleep(4 * POLL)
    assert polls() - n <= 2, f.asked                                   # 5 s, then 10 s: not the four of 5 s each
    n = polls()
    chrome.js(f"({hide})(false)")
    until(lambda: polls() > n, 2, lambda: f.asked)                   # shown: at once
    n = polls()
    until(lambda: polls() >= n + 2, 2 * POLL + 3, lambda: f.asked)   # and every 5 s
    chrome.js("window.failedSaid = []; document.addEventListener('rb:failed', e => failedSaid.push(e.detail.error))")
    f.down = True
    n = polls()
    time.sleep(4 * POLL)
    assert polls() - n <= 2, f.asked
    assert set(chrome.js("failedSaid")) == {"rb open is not answering"}
    assert chrome.thrown == []
