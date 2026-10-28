"""rb login, rb logout, rb sync and rb clone of an address, held against one stub hub: every way the hub can answer (a
sign-in approved, waited on, slowed, declined, expired, refused, broken; a token revoked or not; an upload of the page
alone or of the record too, refused with and without its own words; a bundle missing or not a zip) and every way it can
be out of reach. Each command's exit
code, stdout and stderr are kept byte for byte, and so is the credentials file (its "created" time aside, which is the
wall clock), with its mode, and every file of the project the clone lands.

    python3 rust/tests_py/diff_hub.py rust/target/release/rb             the binary against what is stored
    python3 rust/tests_py/diff_hub.py --record rust/target/release/rb    store what this binary says and leaves

What is stored (golden/hub.json) names the stub hub by a fixed address (127.0.0.1:47821, RB_GOLDEN_HUB_PORT to move
it), since the project a clone lands records where it came from, and the directory it ran in @BASE@. A failure to
connect is said in the machine's own words (its errno, its resolver): what is stored is Linux's, and where a name does
not resolve only the exit code is held.
"""
import io
import json
import os
import re
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import golden  # noqa: E402

STATE: dict = {}
HUB_PORT = int(os.environ.get("RB_GOLDEN_HUB_PORT") or 47821)       # where the stub hub answers when what it hears is stored
STORED = os.path.join(golden.STORED, "hub.json")


class Hub(BaseHTTPRequestHandler):
    """The hub's sign-in, revoke, whoami and upload endpoints, each answering as the mode in the path says:
    /m/<mode>/api/hub/..., so one server plays every hub."""

    def _route(self):
        m = re.match(r"^/m/([^/]+)(/.*)$", self.path)
        return (m.group(1), m.group(2)) if m else ("", self.path)

    def _origin(self, mode):
        return f"http://127.0.0.1:{self.server.server_address[1]}/m/{mode}"

    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("content-type", ctype)
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        mode, path = self._route()
        self.rfile.read(int(self.headers.get("content-length", "0") or 0))
        if path == "/api/hub/device/code":
            if mode == "code500":
                return self._send(500, {"error": "the hub is down"})
            if mode == "code404":
                return self._send(404, b"<html>no</html>", "text/html")
            if mode == "codejunk":
                return self._send(200, b"<html>not json</html>", "text/html")
            if mode == "nocode":
                return self._send(200, {"error": "no device flow here"})
            start = {"device_code": "dev_stub", "user_code": "ABCD-EFGH", "verification_uri": f"{self._origin(mode)}/link",
                     "expires_in": 600, "interval": 0}
            if mode == "complete":
                start["verification_uri_complete"] = f"{self._origin(mode)}/link?code=ABCD-EFGH"
            if mode == "nousercode":
                del start["user_code"]
            if mode == "expiresnow":
                start["expires_in"] = 0
            if mode == "strinterval":
                start["interval"] = "0"
            if mode == "hugeexpiry":
                start["expires_in"] = 9223372036854775807
            return self._send(200, start)
        if path == "/api/hub/device/token":
            n = STATE[mode] = STATE.get(mode, 0) + 1
            if mode == "slow" and n == 1:
                return self._send(400, {"error": "slow_down"})
            if n < 2:
                return self._send(400, {"error": "authorization_pending"})
            answers = {
                "denied": (400, {"error": "access_denied"}),
                "expired": (400, {"error": "expired_token"}),
                "described": (400, {"error": "server_error", "error_description": "the hub tripped"}),
                "undescribed": (400, {"error": "server_error"}),
                "notoken": (200, {"handle": "t"}),
                "nohandle": (200, {"token": "rb_stub_nohandle"}),
                "nullhandle": (200, {"token": "rb_stub_nullhandle", "handle": None}),
                "nulltoken": (200, {"token": None, "handle": "t"}),
                "emptytoken": (200, {"token": "", "handle": "t"}),
                "token500": (500, {"token": "rb_stub_token500", "handle": "t"}),
            }
            code, body = answers.get(mode, (200, {"token": f"rb_stub_{mode}", "handle": "t"}))
            return self._send(code, body)
        self._send(404, {"error": "no"})

    def do_DELETE(self):
        mode, path = self._route()
        self.rfile.read(int(self.headers.get("content-length", "0") or 0))
        if mode == "norevoke":
            return self._send(401, {"error": "bad token"})
        if mode == "revokejunk":
            return self._send(200, b"ok", "text/plain")
        if mode == "revoke204":
            STATE["revoked:" + mode] = self.headers.get("authorization")
            return self._send(204, b"", "text/plain")
        STATE["revoked:" + mode] = self.headers.get("authorization")
        self._send(200, {"ok": True})

    def do_GET(self):
        mode, path = self._route()
        if path == "/api/hub/whoami":
            if mode == "who401":
                return self._send(401, {"error": "bad token"})
            if mode == "who403fix":
                return self._send(403, {"error": "not yours", "fix": "Sign in as the owner."})
            if mode == "whojunk":
                return self._send(500, b"oops", "text/plain")
            if mode == "nobase":
                return self._send(200, {"handle": "t", "publish": f"{self._origin(mode)}/api/hub/upload"})
            if mode == "relpublish":
                return self._send(200, {"handle": "t", "base": f"{self._origin(mode)}/u/t", "publish": "/api/hub/upload"})
            publish = f"{self._origin(mode)}/api/hub/upload" + ("?project=x" if mode == "query" else "")
            return self._send(200, {"handle": "t", "base": f"{self._origin(mode)}/u/t", "publish": publish})
        if path.startswith("/files/"):
            name = path[len("/files/"):]
            p = os.path.join(STATE["files"], name)
            if os.path.isfile(p):
                return self._send(200, open(p, "rb").read(), "application/zip")
            return self._send(404, b"missing", "text/plain")
        self._send(404, {"error": "no"})

    def do_PUT(self):
        mode, path = self._route()
        body = self.rfile.read(int(self.headers.get("content-length", "0") or 0))
        if mode == "put500":
            return self._send(500, b"oops", "text/plain")
        if mode == "putjunk":
            return self._send(201, b"stored", "text/plain")
        try:
            with zipfile.ZipFile(io.BytesIO(body)) as z:
                record = any("/record/.rb/" in n for n in z.namelist())
        except zipfile.BadZipFile:
            record = False
        STATE["upload:" + mode] = {"path": path, "auth": self.headers.get("authorization"), "type": self.headers.get("content-type"),
                                   "bytes": len(body) > 100, "record": record}
        if mode == "putlist":
            return self._send(201, ["not", "a", "mapping"])
        answer = {"url": f"{self._origin(mode)}/u/t/proj"}
        if record:                                       # a page alone has nothing to clone
            answer["clone"] = f"rb clone {self._origin(mode)}/u/t/proj/bundle.zip"
        self._send(201, answer)

    def log_message(self, *a):
        pass


def closed_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def creds(cfg: str):
    p = os.path.join(cfg, "credentials.json")
    if not os.path.exists(p):
        return None
    data = json.load(open(p))
    for entry in data.values():
        assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", entry.pop("created", "")), entry
    return data, stat.S_IMODE(os.stat(p).st_mode)


def steps_for(origin: str, dead: str) -> list:
    """Every command, as (what it shows, its arguments, what it adds to the environment, whether its words are the
    machine's own)."""
    hub = lambda mode: f"{origin}/m/{mode}"  # noqa: E731
    login_modes = ["ok", "complete", "slow", "denied", "expired", "described", "undescribed", "notoken", "nohandle", "nullhandle",
                   "code500", "code404", "codejunk", "nocode", "nousercode", "expiresnow", "strinterval"]
    steps: list = [("login", ["login", "--hub", hub(m)], {}, False) for m in login_modes]
    steps += [
        ("login unreachable", ["login", "--hub", dead], {}, False),
        ("login no scheme", ["login", "--hub", "rabbitbrain"], {}, False),
        ("login odd scheme", ["login", "--hub", "localhost:1"], {}, False),
        ("login no host", ["login", "--hub", "https://"], {}, False),
        ("login nowhere", ["login", "--hub", "http://rb-nowhere.invalid"], {}, True),       # the resolver's words
        ("login json", ["login", "--hub", hub("ok") + "/", "--json"], {}, False),
        ("login env", ["login"], {"RB_HUB_URL": hub("complete")}, False),
        ("logout", ["logout", "--hub", hub("ok")], {}, False),
        ("logout again", ["logout", "--hub", hub("ok")], {}, False),
        ("login for norevoke", ["login", "--hub", hub("norevoke")], {}, False),
        ("logout norevoke", ["logout", "--hub", hub("norevoke"), "--json"], {}, False),
        ("login for revokejunk", ["login", "--hub", hub("revokejunk")], {}, False),
        ("logout revokejunk", ["logout", "--hub", hub("revokejunk")], {}, False),
        ("logout nothing", ["logout", "--hub", hub("never")], {}, False),
        ("sync no credential", ["sync", "--hub", hub("ok")], {}, False),
        ("sync env token", ["sync", "--hub", hub("ok")], {"RB_HUB_TOKEN": "rb_stub_env"}, False),
        ("sync stored", ["sync", "--hub", hub("complete")], {}, False),
        ("sync query", ["sync", "--hub", hub("query") + "/", "--json"], {"RB_HUB_TOKEN": "rb_stub_env"}, False),
        ("sync with record", ["sync", "--hub", hub("ok"), "--with-record", "--json"], {"RB_HUB_TOKEN": "rb_stub_env"}, False),
        ("sync who401", ["sync", "--hub", hub("who401")], {"RB_HUB_TOKEN": "rb_stub_env"}, False),
        ("sync who403fix", ["sync", "--hub", hub("who403fix"), "--json"], {"RB_HUB_TOKEN": "rb_stub_env"}, False),
        ("sync whojunk", ["sync", "--hub", hub("whojunk")], {"RB_HUB_TOKEN": "rb_stub_env"}, False),
        ("sync nobase", ["sync", "--hub", hub("nobase")], {"RB_HUB_TOKEN": "rb_stub_env"}, False),
        ("sync put500", ["sync", "--hub", hub("put500")], {"RB_HUB_TOKEN": "rb_stub_env"}, False),
        ("sync putjunk", ["sync", "--hub", hub("putjunk")], {"RB_HUB_TOKEN": "rb_stub_env"}, False),
        ("sync putlist", ["sync", "--hub", hub("putlist")], {"RB_HUB_TOKEN": "rb_stub_env"}, False),
        ("sync unreachable", ["sync", "--hub", dead], {"RB_HUB_TOKEN": "rb_stub_env"}, False),
        ("logout unreachable", ["logout", "--hub", dead], {}, False),
        ("clone", ["clone", f"{origin}/files/bundle.zip", "fromhub"], {}, False),
        ("clone missing", ["clone", f"{origin}/files/none.zip", "x1"], {}, False),
        ("clone junk", ["clone", f"{origin}/files/junk.zip", "x2"], {}, False),
        ("clone unreachable", ["clone", f"{dead}/bundle.zip", "x3"], {}, False),
        ("clone no host", ["clone", "https:///bundle.zip", "x4"], {}, False),
        # answers that are almost the hub's: a token that is not one, an error status that carries one, an expiry no
        # clock can hold, a revoke answered with no body, a place to publish that is not an address
        ("login nulltoken", ["login", "--hub", hub("nulltoken")], {}, False),
        ("login emptytoken", ["login", "--hub", hub("emptytoken")], {}, False),
        ("login token500", ["login", "--hub", hub("token500")], {}, False),
        ("login hugeexpiry", ["login", "--hub", hub("hugeexpiry")], {}, False),
        ("login for revoke204", ["login", "--hub", hub("revoke204")], {}, False),
        ("logout revoke204", ["logout", "--hub", hub("revoke204"), "--json"], {}, False),
        ("sync relpublish", ["sync", "--hub", hub("relpublish")], {"RB_HUB_TOKEN": "rb_stub_env"}, False),
        ("sync unsendable", ["sync", "--hub", hub("ok")], {"RB_HUB_TOKEN": "rb_stub\rsplit"}, False),
        ("logout no address", ["logout"], {"RB_HUB_URL": ""}, False),
    ]
    return steps


def played(head: list, steps: list, base: str) -> tuple:
    """One rb through every command, in <base>/work with its credentials in <base>/cfg: (each command's label, exit
    code, stdout, stderr and the credentials file after it; the files of the project the clone landed)."""
    for k in [k for k in STATE if k != "files"]:
        del STATE[k]
    work, home, cfg, clones = (os.path.join(base, x) for x in ("work", "home", "cfg", "clones"))
    for d in (work, home, cfg, clones):
        shutil.rmtree(d, ignore_errors=True)
        os.makedirs(d)
    env = golden.environment(home, {})
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
        env.pop(name, None)
    env.update({"no_proxy": "127.0.0.1,localhost", "RB_CONFIG_DIR": cfg})
    # the record the syncs upload, and the bundle the clones fetch
    run = lambda argv: subprocess.run([*head, *argv], cwd=work, env=env, capture_output=True, check=True)  # noqa: E731
    subprocess.run(["git", "init", "-q"], cwd=work, env=env, check=True)
    run(["init", "the synced project", "--id", "sync-proj", "--no-agents"])
    run(["experiment", "add", "e", "--id", "e1"])
    run(["criterion", "add", "c", "-e", "e1", "--metric", "m", "--at-least", "0.5", "--id", "c1"])
    run(["export", "--with-record", "--zip", "--out", os.path.join(STATE["files"], "bundle.zip")])
    results = []
    env["RB_PYTHON"] = os.path.join(base, "no-python-here")         # none of these commands needs a Python
    for label, argv, extra, _local in steps:
        # a clone starts a project of its own, so it runs outside the one the syncs upload
        cwd = clones if argv[0] == "clone" else work
        r = subprocess.run([*head, *argv], cwd=cwd, env=dict(env, **extra), capture_output=True, stdin=subprocess.DEVNULL, timeout=120)
        # a bundle fetched lands under a temporary name of its own: say it as TMP
        err = re.sub(rb"/[^ ]*/rb-clone-[^/ ]+/", b"TMP/", r.stderr)
        outp = re.sub(rb"/[^ \"]*/rb-clone-[^/ \"]+/", b"TMP/", r.stdout)
        results.append((label, r.returncode, outp, err, creds(cfg)))
    landed = os.path.join(clones, "fromhub")
    return results, (golden.snapshot(landed) if os.path.isdir(landed) else {})


class StubHub:
    """The stub hub, answering for as long as the block runs; port 0 takes any free one."""

    def __init__(self, port: int, base: str) -> None:
        try:
            self.httpd = ThreadingHTTPServer(("127.0.0.1", port), Hub)
        except OSError as e:
            raise SystemExit(f"the stub hub could not listen on 127.0.0.1:{port} ({e.strerror}): free it, or set RB_GOLDEN_HUB_PORT "
                             f"to move it (what is stored then has to be recorded again)")
        self.origin = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        STATE.clear()
        STATE["files"] = os.path.join(base, "files")
        os.makedirs(STATE["files"])
        with open(os.path.join(STATE["files"], "junk.zip"), "wb") as f:
            f.write(b"not a zip\n")

    def __enter__(self) -> str:
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        return self.origin

    def __exit__(self, *exc) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


# ---------------------------------------------------------------- what is stored


def said(implementation: str) -> dict:
    """What this rb says and leaves against the stub hub at its fixed address, as it is stored."""
    head = golden.head_of(implementation)
    base = tempfile.mkdtemp(prefix="rb-hub-")
    try:
        with StubHub(HUB_PORT, base) as origin:
            steps = steps_for(origin, "http://127.0.0.1:1")          # port 1: nothing listens there
            results, files = played(head, steps, base)
        here = os.path.realpath(base).encode()
        plain = lambda data: data.replace(here, golden.BASE)   # noqa: E731
        out = []
        for (label, argv, _extra, local), (_, code, o, e, k) in zip(steps, results):
            step = {"run": f"{label}: rb {' '.join(argv)}", "exit": code}
            if not local:
                step.update({"out": golden.lines_of(plain(o)), "err": golden.lines_of(plain(e)),
                             "credentials": None if k is None else {"entries": k[0], "mode": oct(k[1])}})
            out.append(step)
        return {"steps": out, "files": {rel: golden.stored_file(plain(data)) for rel, data in sorted(files.items())
                                        if not rel.endswith(golden.RUST_ONLY)}}
    finally:
        shutil.rmtree(base, ignore_errors=True)


def against_stored(binary: str) -> list:
    try:
        with open(STORED, encoding="utf-8") as f:
            want = json.load(f)
    except FileNotFoundError:
        return ["nothing is stored for the hub commands: python3 rust/tests_py/diff_hub.py --record <binary>"]
    got = said(binary)
    problems = []
    if [w["run"] for w in want["steps"]] != [g["run"] for g in got["steps"]]:
        problems.append("the commands run differ from the ones stored: record them again")
    for w, g in zip(want["steps"], got["steps"]):
        for what in ("exit", "out", "err", "credentials"):
            if w.get(what) != g.get(what):
                if what in ("out", "err"):
                    problems.append(f"{g['run']}: {what} differs\n" + golden.show_diff(golden.bytes_of(w[what]), golden.bytes_of(g[what]), what, ("stored", "now")))
                else:
                    problems.append(f"{g['run']}: {what} {w.get(what)!r} (stored) vs {g.get(what)!r} (now)")
    wf, gf = want["files"], got["files"]
    for rel in sorted(set(wf) | set(gf)):
        if wf.get(rel) != gf.get(rel):
            problems.append(f"clone fromhub/{rel}: " + ("stored, and not written now" if rel not in gf else "written now, and not stored" if rel not in wf else "differs"))
    return problems


def main() -> int:
    args = sys.argv[1:]
    mode = args.pop(0)[2:] if args and args[0] in ("--stored", "--record") else "stored"
    if not args:
        print(__doc__)
        return 2
    implementation = os.path.abspath(args[0])
    if not os.path.isfile(implementation):
        print(f"no rb at {implementation}: the first argument is the binary to run")
        return 2
    if mode == "record":
        got = said(implementation)
        with open(STORED, "w", encoding="utf-8") as f:
            json.dump(got, f, indent=1, ensure_ascii=True)
            f.write("\n")
        print(f"kept {len(got['steps'])} hub commands and {len(got['files'])} files of the project the clone landed")
        return 0
    problems = against_stored(implementation)
    for pr in problems[:20]:
        print("  " + pr.replace("\n", "\n  "))
    print("ok   the hub commands agree with what is stored" if not problems else f"FAIL the hub commands: {len(problems)} differences")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
