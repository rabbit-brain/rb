"""rb open, held by every route: the app page, the state, every object's card, every found item's card, the page, the
refusals, and the requests a browser never sends but `http.server` answers in its own way. Each answer is kept byte
for byte, the clock line of the state and the `Server` and `Date` lines aside.

    python3 rust/tests_py/diff_open.py rust/target/release/rb [scenario...]           the binary against what is stored
    python3 rust/tests_py/diff_open.py --record rust/target/release/rb [scenario...]  store what this binary answers

What is stored (golden/open-<scenario>.json) is every answer of rb to a record it built itself, the directory it ran in
written @BASE@ (and the id of the place it is, p_@BASE@work, and the live module's key, @KEY@), each distinct body once. The scenarios stored are "reading" and "found"; "found" is this file's own, a
record with things rb found and not yet recorded.
"""
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import golden  # noqa: E402
from golden_scenarios import SCENARIOS  # noqa: E402


# Things rb found and the record does not hold yet, so their cards are asked too: a file of numbers a criterion reads,
# with its seed in its own config; numbers nothing declares; a heartbeat; a results folder an experiment reads (one with
# a report, which rb reconcile --apply adds as an experiment); and what a watched command printed, with its seed on its
# line. All of it lands where rb looks.
FOUND = {"steps": [
    ("rb", "init t --no-agents"),
    ("rb", "experiment add x --id e1 --baseline base --candidate wide --hypothesis 'wider wins'"),
    ("rb", "metric add top1 --maximize --alias acc --unit fraction"),
    ("rb", "metric add loss --minimize --unit nats"),
    ("rb", "criterion add 'wide holds' -e e1 --metric wide.top1 --at-least 0.6 --min-n 2 --id c1"),
    ("rb", "spec set e1 seed --per-run"),
    ("sh", "git add -A && git commit -qm start"),
    ("write", "outputs/sweep/README.md", "# Sweep\n\n## Results\n\nThe loss ends at 0.5.\n"),
    ("write", "outputs/sweep/results.json", '{"loss": 0.5}\n'),
    ("rb", "reconcile --apply"),
    ("rbsh", "$RB criterion add 'sweep holds' -e $(ls .rb/experiments | grep -v '^e1$' | head -1) --metric loss --at-most 0.6 --id c2"),
    ("write", "outputs/sweep/top.json", '{"loss": 0.41}\n'),
    ("write", "outputs/run1/metrics.json", '{"wide.top1": 0.71, "base.top1": 0.69}\n'),
    ("write", "outputs/run1/config.yaml", "seed: 1\n"),
    ("write", "outputs/run2.json", '{"other": 1.0}\n'),
    ("write", "outputs/heartbeat.json", '{"step": 10}\n'),
    ("rb", ["journal", "run", "--", "python3", "-c", "print('{\"metrics\": {\"wide.top1\": 0.72}}')", "--seed", "2"]),
    ("rb", "journal scan"),
    ("rb", "status"),
]}


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def fetch(port: int, path: str, method: str = "GET") -> tuple[int, bytes]:
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method, data=b"" if method == "POST" else None)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


# Requests as bytes on the wire, for what a browser never sends but http.server still answers in its own way: other
# methods, HTTP/0.9, bad versions and syntax, absolute and doubled paths, parameters, odd escapes, the limits.
RAW = [
    b"HEAD / HTTP/1.0\r\n\r\n",
    b"PUT /state.json HTTP/1.0\r\n\r\n",
    b"DELETE / HTTP/1.1\r\nHost: x\r\n\r\n",
    b"get / HTTP/1.0\r\n\r\n",
    b"GET /\r\n\r\n",
    b"POST /\r\n\r\n",
    b"GET / HTTP/x\r\n\r\n",
    b"GET / HTTP/1.x\r\n\r\n",
    b"GET / HTTP/2.0\r\n\r\n",
    b"GET / HTTP/01.1\r\n\r\n",
    b"A B C HTTP/1.0\r\n\r\n",
    b"GET //index.html HTTP/1.0\r\n\r\n",
    b"GET http://127.0.0.1/index.html HTTP/1.0\r\n\r\n",
    b"GET http://[::1/x HTTP/1.0\r\n\r\n",
    b"GET x:y HTTP/1.0\r\n\r\n",
    b"GET /card.json;x?ref=o%3Anothing HTTP/1.0\r\n\r\n",
    b"GET /card.json?ref=a;ref=b HTTP/1.0\r\n\r\n",
    b"GET /card.json?ref=%+1&ref=x HTTP/1.0\r\n\r\n",
    b"GET /card.json?ref=&ref=%E9 HTTP/1.0\r\n\r\n",
    b"GET /card.json?r%65f=o:nothing#x HTTP/1.0\r\n\r\n",
    b"GET /\xe9t\xc3\xa9 HTTP/1.0\r\n\r\n",
    b"GET /\x01x\x85 HTTP/1.0\r\n\r\n",
    b"GET /x HTTP/1.1\r\nHost: a\r\n\r\n",
    b"GET /nope HTTP/1.0\n\r\n",
    b"GET /nope HTTP/1.0\r\nHost: x\r\n",
    b"\r\n",
    b"GET /" + b"a" * (65537 - 5),
    b"GET / HTTP/1.0\r\n" + b"X: " + b"y" * (65537 - 3),
    b"GET / HTTP/1.0\r\n" + b"X: y\r\n" * 101,
    b"GET / HTTP/1.0\r\n" + b"X: y\r\n" * 99 + b"\r\n",
]


def raw(port: int, data: bytes) -> bytes:
    """The whole answer to these bytes, the Server and Date lines aside (whose they are, and when)."""
    with socket.create_connection(("127.0.0.1", port), timeout=60) as s:
        s.sendall(data)
        s.shutdown(socket.SHUT_WR)
        got = b""
        try:
            while chunk := s.recv(65536):
                got += chunk
        except ConnectionResetError:
            got += b"<reset>"
    if got.startswith(b"HTTP/"):
        head, _, body = got.partition(b"\r\n\r\n")
        head = b"\r\n".join(x for x in head.split(b"\r\n") if not x.startswith((b"Server: ", b"Date: ")))
        got = head + b"\r\n\r\n" + body
    return got


def wait(port: int) -> None:
    for _ in range(200):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            return
        except OSError:
            time.sleep(0.05)
    raise SystemExit(f"nothing listens on {port}")


def steps_of(scenario: str):
    return FOUND if scenario == "found" else SCENARIOS[scenario]


def refs_of(port: int, root: str) -> list:
    """What to ask a card for: every row of the catalogue, every object the record holds by its id (each kind, and the
    kinds a card does not open), every found item, and names nothing goes by."""
    _, state = fetch(port, "/state.json")
    home_ = json.loads(state)["home"]
    ids = set()
    for d, dirs, files in os.walk(os.path.join(root, ".rb")):
        if os.path.basename(d) == "experiments":
            ids.update(dirs)
        ids.update(f[:-5] for f in files if f.endswith(".yaml"))
    refs = [f"o:{r['id']}" for r in home_["tree"]] + [f"o:{i}" for i in sorted(ids)] + [a["request_id"] for a in home_["found"]]
    return refs + ["o:nothing", "jnothing", "o:", "o:e1/seed", "o:../x"]


def answers(port: int, refs: list) -> list:
    """Every answer of the server on `port`, in order: (what was asked, the status or None, the bytes)."""
    out = []
    paths = ["/", "/index.html", "/state.json", "/state.json", "/page.html", "/nope", "/card.json", "/card.json?ref="]
    paths += [f"/card.json?ref={urllib.request.quote(r)}" for r in refs]
    for path in paths:
        code, body = fetch(port, path)
        if path == "/state.json":
            j = json.loads(body)
            j.pop("at", None)                      # the clock line: when the state was computed
            body = json.dumps(j, sort_keys=False).encode()
        out.append((f"GET {path}", code, body))
    code, body = fetch(port, "/", "POST")
    out.append(("POST /", code, body))
    for data in RAW:
        label = data[:60].decode("latin-1").encode("unicode_escape").decode("ascii")
        out.append((f"RAW {label}" + (f" ({len(data)} bytes)" if len(data) > 60 else ""), None, raw(port, data)))
    return out


class Served:
    """rb open on a record, for as long as the block runs."""

    def __init__(self, head: list, cwd: str, env: dict) -> None:
        self.port = free_port()
        self.proc = subprocess.Popen([*head, "open", "--port", str(self.port)], cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def __enter__(self) -> int:
        wait(self.port)
        return self.port

    def __exit__(self, *exc) -> None:
        self.proc.terminate()
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()


def built(head: list, scenario: str, base: str) -> tuple:
    """A record the scenario's commands made with this rb, in <base>/work: (the directory, the environment)."""
    work, home = os.path.join(base, "work"), os.path.join(base, "home")
    os.makedirs(work)
    os.makedirs(home)
    env = golden.environment(home, {})
    subprocess.run(["git", "init", "-q"], cwd=work, check=True, env=env, capture_output=True)
    golden.run(head, steps_of(scenario), work, home)
    return work, env


# ---------------------------------------------------------------- what is stored

STORED_SCENARIOS = ("reading", "found")


def stored_path(scenario: str) -> str:
    return os.path.join(golden.STORED, f"open-{scenario}.json")


LIVE_KEY = re.compile(rb'(<script id="rb-live" data-key=")[0-9a-f]{64}(")')


def broken(path: bytes) -> bytes:
    """A path as a picker's list draws it (open.rs's unbroken): each part in its own span, a line break allowed after
    each '/'. A path ending in '/' is drawn as the start of a longer one, so it stands for every path under it."""
    parts = path.split(b"/")
    return b"".join(b'<span class="nw">' + seg + b'/</span><wbr>' for seg in parts[:-1])


def said(head: list, scenario: str) -> dict:
    """Every answer of this rb's `rb open` to a record it built, as it is stored: each distinct body once, by its
    sha256, with the directory it ran in written @BASE@."""
    base = tempfile.mkdtemp(prefix="rb-open-")
    try:
        work, env = built(head, scenario, base)
        here = os.path.realpath(base).encode()
        # the id of the place shown is its path's hash, which the directory a scenario runs in changes on every run
        place = ("p_" + hashlib.sha256(os.path.realpath(work).encode()).hexdigest()[:10]).encode()
        with Served(head, work, env) as port:
            got = answers(port, refs_of(port, work))
    finally:
        shutil.rmtree(base, ignore_errors=True)
    bodies, asked = {}, []
    for ask, code, body in got:
        body = body.replace(broken(here + b"/"), broken(golden.BASE + b"/")).replace(here, golden.BASE).replace(place, b"p_@BASE@work")
        # the live module's key hashes the places listed, paths and all, so the directory moves it too
        body = LIVE_KEY.sub(rb"\1@KEY@\2", body)
        sha = hashlib.sha256(body).hexdigest()
        bodies[sha] = golden.lines_of(body)
        asked.append({"ask": ask, "status": code, "body": sha})
    return {"answers": asked, "bodies": bodies}


def record(implementation: str, scenario: str) -> list:
    got = said(golden.head_of(implementation), scenario)
    with open(stored_path(scenario), "w", encoding="utf-8") as f:
        json.dump(got, f, indent=1, ensure_ascii=True)
        f.write("\n")
    return []


def against_stored(binary: str, scenario: str) -> list:
    try:
        with open(stored_path(scenario), encoding="utf-8") as f:
            want = json.load(f)
    except FileNotFoundError:
        return [f"nothing is stored for rb open on {scenario}: python3 rust/tests_py/diff_open.py --record <binary> {scenario}"]
    got = said([binary], scenario)
    problems = []
    if [a["ask"] for a in want["answers"]] != [a["ask"] for a in got["answers"]]:
        problems.append("what is asked differs from what is stored (the record's ids, or the requests): "
                        + " | ".join(sorted({a["ask"] for a in want["answers"]} ^ {a["ask"] for a in got["answers"]}))[:600])
    for w, g in zip(want["answers"], got["answers"]):
        if w["ask"] != g["ask"]:
            break
        if w["status"] != g["status"]:
            problems.append(f"{g['ask']}: {w['status']} (stored) vs {g['status']} (now)")
        if w["body"] != g["body"]:
            was, now = golden.bytes_of(want["bodies"][w["body"]]), golden.bytes_of(got["bodies"][g["body"]])
            problems.append(f"{g['ask']}: the answer differs\n{golden.show_diff(was, now, g['ask'], ('stored', 'now'))}")
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
    failed = 0
    for scenario in args[1:] or STORED_SCENARIOS:
        problems = record(implementation, scenario) if mode == "record" else against_stored(implementation, scenario)
        if problems:
            failed += 1
            print(f"FAIL rb open on {scenario}")
            for pr in problems[:12]:
                print("  " + pr.replace("\n", "\n  "))
        else:
            print(f"{'kept' if mode == 'record' else 'ok  '} rb open on {scenario}", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
