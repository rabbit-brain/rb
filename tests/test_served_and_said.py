"""What rb open serves, what the views that offer no command draw, and one check's digits (round 2c, MP-1112-1030).

- B1: rb open serves a field card (`/o/k-<subject>.html`) only for a subject its page lists: a check, run, experiment
  or decision, a setting, a file the Files tab lists, a commit a run names, or what their cards cite. Never git's own
  folder (its config and logs name whoever committed), rb's, or a file git ignores that nothing cites; a file git
  ignores that the record cites is said by its path and that git ignores it, with none of its lines, on rb open and
  on a page. What held before still holds (../, an absolute path, .rb/log.jsonl, a link out, cross-site, a foreign
  Host, POST), and every card the page's links reach is still served.
- B2: a field's command (`rb confirm <id>`) is its own part of the card, drawn as a command: a past version (`?at=`)
  draws none, and a reader's page holds it only where the reader's view holds every command back. The owner's live
  view shows it, and the terminal's `rb show` keeps it.
- S1: a check's result and range read in the same digits on the chart (rb status --json), rb status, rb show, the
  web's field card and rb ui's chart.

The records: the INT8 demo and the scale record (tests/rbtest.py), and the hosted samples where RB_SAMPLE_RECORDS
names them (tests/test_card_cli.py)."""
import contextlib
import html
import http.client
import json
import os
import re
import shutil
import subprocess
import urllib.parse
from pathlib import Path

import pytest

from rbtest import Open, command
from test_card_cli import Sample, samples

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="every record here is a git repository")

SAME = {"Sec-Fetch-Site": "same-origin"}
ANCHOR = re.compile(r'href="#(k-[^"]+)"')
# what a reader's view holds back (script.js: RBCMD, and READER_CSS hides each .cmdbox that holds one)
RBCMD = re.compile(r"^(cd \S.* && )?rb (?!clone\b)[a-z]")


@pytest.fixture(params=["int8_demo", "scale_record", *[f"sample:{p}" for p in samples()]])
def rec(request, tmp_path):
    if request.param.startswith("sample:"):
        return Sample(Path(request.param[7:]), tmp_path, request.getfixturevalue("int8_demo"))
    return request.getfixturevalue(request.param)


@contextlib.contextmanager
def served(rec, root=None):
    old, saved = os.getcwd(), dict(os.environ)
    os.environ.update(rec.env)
    srv = Open(root or rec.root)
    try:
        yield srv
    finally:
        srv.close()
        os.environ.clear()
        os.environ.update(saved)
        os.chdir(old)


def get(srv, path, headers=None, method="GET"):
    c = http.client.HTTPConnection(srv.host, srv.port, timeout=300)
    c.request(method, path, headers=headers or {})
    r = c.getresponse()
    return r.status, r.read().decode("utf-8", "replace")


def card_path(anchor: str, query: str = "") -> str:
    return "/o/" + urllib.parse.quote(anchor, safe="") + ".html" + query


def walk(srv, query: str = "") -> dict:
    """Every field card the page's links reach, from `/` through each card served: {anchor: (status, body)}."""
    s, home = get(srv, "/" + query, SAME)
    assert s == 200, home[:300]
    todo, got = sorted(set(ANCHOR.findall(home))), {}
    while todo:
        a = todo.pop()
        if a in got:
            continue
        got[a] = get(srv, card_path(html.unescape(a), query), SAME)
        if got[a][0] == 200:
            todo += [x for x in set(ANCHOR.findall(got[a][1])) if x not in got]
    return got


def git(root, *argv):
    return subprocess.run(["git", "-C", str(root), *argv], capture_output=True, text=True, check=True).stdout


def rbj(rec, root, *argv):
    env = {**os.environ, **rec.env}
    r = subprocess.run([*command(), *argv, "--json"], cwd=root, env=env, capture_output=True, text=True)
    return json.loads(r.stdout)["data"]


def rbt(rec, root, *argv, columns=100, lines=None):
    env = {**os.environ, **rec.env, "COLUMNS": str(columns)}
    if lines:
        env["LINES"] = str(lines)
    return subprocess.run([*command(), *argv], cwd=root, env=env, capture_output=True, text=True).stdout


# ---------------------------------------------------------------- B1: rb open serves what its page lists

@pytest.fixture
def demo(int8_demo, tmp_path):
    """A copy of the INT8 demo with a file git ignores beside it (a token), and a cited config git ignores now: taken
    out of git's index and named in the clone's own ignore list, still on disk and still the source of its settings."""
    root = tmp_path / "work"
    shutil.copytree(int8_demo.root, root, symlinks=True)
    (root / "secret.env").write_text("TOKEN=abc\n")
    # a file the record cites (a check's source, a setting's, a run's), which git tracks now
    files = rbj(int8_demo, root, "files")["files"]
    cited = [f["path"] for f in files if f["git"] == "committed" and f["cited_by"] and f["lines"] and not f["path"].startswith(".")]
    assert cited, files
    ignored = cited[0]
    git(root, "rm", "-q", "--cached", "--", ignored)
    with open(root / ".git" / "info" / "exclude", "a") as f:
        f.write(f"secret.env\n{ignored}\n")
    return root, ignored


def test_rb_open_serves_no_card_its_page_does_not_list(int8_demo, demo):
    root, _ = demo
    with served(int8_demo, root) as srv:
        _, home = get(srv, "/")
        for path in [".git/config", ".git/logs/HEAD", ".git", ".git/HEAD", ".git/", "secret.env", ".rb/log.jsonl", ".rb/project.yaml", ".rb",
                     "../x", "/etc/passwd", "sub/.git/config", ".git/config:1", ".git/config#core"]:
            anchor = "k-" + path
            assert anchor not in home
            s, body = get(srv, card_path(anchor))
            assert s == 404, (path, s, body[:200])
            assert "TOKEN" not in body and "@example" not in body and "[core]" not in body, path
        # a link in the checkout leads where it leads: to git's folder, rb's log or an ignored file, it is served as none
        for link, to in [("gitcfg.txt", ".git/config"), ("headlog.txt", ".git/logs/HEAD"), ("rblog.txt", ".rb/log.jsonl"), ("env.txt", "secret.env"), ("g", ".git")]:
            os.symlink(to, root / link)
        for anchor in ["k-gitcfg.txt", "k-headlog.txt", "k-rblog.txt", "k-g", "k-g/config", "k-g/logs/HEAD", "k-gitcfg.txt:1"]:
            s, body = get(srv, card_path(anchor))
            assert s == 404, (anchor, s, body[:200])
        s, body = get(srv, card_path("k-env.txt"))
        assert s == 404 or ("TOKEN" not in body and "ignored by git" in body), (s, body[:300])
        # the anchor's own escapes (fields.rs anchor) reach none of them either
        for anchor in ["k-.git~2fconfig", "k-~2e~67it/config", "k-secret~2eenv", "k-~2Fetc~2Fpasswd", "k-..~2Fx"]:
            assert get(srv, card_path(anchor))[0] == 404, anchor
        # what held before: a link out, cross-site, a foreign Host, POST
        assert get(srv, "/o/k-https:~2f~2fexample.org.html")[0] == 404
        assert get(srv, card_path("k-.git/config"), {"Host": "evil.example"})[0] == 403
        assert get(srv, card_path("k-.git/config") + "?at=HEAD", {"Sec-Fetch-Site": "cross-site"})[0] == 403
        assert get(srv, card_path("k-.git/config"), method="POST")[0] == 405


def test_every_card_the_pages_links_reach_is_still_served(int8_demo, demo):
    root, _ = demo
    with served(int8_demo, root) as srv:
        got = walk(srv)
    kinds = {re.search(r'data-kind="(\w+)"', b).group(1) for s, b in got.values() if s == 200}
    assert {"check", "run", "experiment", "decision"} <= kinds and kinds & {"setting", "file", "commit"}, kinds
    assert [a for a, (s, _) in got.items() if s != 200] == []
    assert not [a for a in got if ".git" in a.split("/") or a.startswith(("k-.rb", "k-secret"))]


def test_a_cited_file_git_ignores_says_so_and_draws_none_of_its_lines(int8_demo, demo, tmp_path):
    root, ignored = demo
    line = next(f["lines"][0] for f in rbj(int8_demo, root, "files")["files"] if f["path"] == ignored)
    with served(int8_demo, root) as srv:
        for anchor in ("k-" + ignored, f"k-{ignored}:{line}"):
            s, body = get(srv, card_path(anchor))
            assert s == 200, (anchor, body[:300])
            assert "ignored by git" in body and html.escape(ignored) in body
            assert "<dt>LINES</dt>" not in body and 'class="flines"' not in body and "\u25b6" not in body, anchor
        # nor whether a key or a line is in it, nor its lines at a commit that holds it
        keys = [get(srv, card_path(f"k-{ignored}~23{k}"))[1] for k in ("no_such_key_here", "seed")]
        commit = git(root, "rev-parse", "--short=12", "HEAD").strip()
        at = get(srv, card_path(f"k-{ignored}@{commit}"))
        for body in [*keys, at[1]]:
            assert "<dt>KEY</dt>" not in body and 'class="flines"' not in body and "\u25b6" not in body, body[:400]
    # the terminal's own screen still draws them (rb show is the user's)
    assert "LINES" in rbt(int8_demo, root, "show", ignored)
    # a page draws a file's card at a key or a commit, with the same drawing: one git ignores draws none of its lines
    env = {**os.environ, **int8_demo.env}
    subprocess.run([*command(), "page", "--out", str(tmp_path / "p.html")], cwd=root, env=env, check=True, capture_output=True)
    page = (tmp_path / "p.html").read_text(encoding="utf-8")
    cards = re.findall(r'<div class="card fc" id="k-[^"]*" data-kind="file".*?</dl></div>', page, re.S)
    assert not [c[:120] for c in cards if "ignored by git" in c and 'class="flines"' in c]


# ---------------------------------------------------------------- B2: a command is drawn as a command

def bare_commands(card: str) -> list:
    """The rb commands a reader would see in a card: every `rb confirm` that is not in a command box the reader's view
    holds back."""
    held = re.sub(r'<div class="cmdbox"[^>]*><pre class="cmd">([^<]*)</pre></div>', lambda m: "" if RBCMD.match(html.unescape(m.group(1))) else m.group(0), card)
    return re.findall(r"rb confirm[^<]{0,40}", held)


def test_a_past_version_and_a_readers_page_offer_no_command_in_any_card(rec, tmp_path):
    root = rec.root
    pending = rbj(rec, root, "status")
    waiting = [p for p in pending.get("pending", [])] + [x for x in pending.get("experiments", []) if (x.get("design") or {}).get("pending")]
    # a page: every rb confirm a field card gives sits in a command box, which a reader's view holds back
    env = {**os.environ, **rec.env}
    subprocess.run([*command(), "page", "--out", str(tmp_path / "p.html")], cwd=root, env=env, check=True, capture_output=True)
    page = (tmp_path / "p.html").read_text(encoding="utf-8")
    cards = re.findall(r'<div class="card fc".*?</dl></div>', page, re.S)
    assert cards
    assert [c[:80] for c in cards if bare_commands(c)] == []
    # closed form (owner, Nov 13 2026): a confirmation is a prompt button for their agent (rbcmd, which a
    # reader's view holds back), never an rb confirm to type
    if waiting:
        assert any('class="ibtn ico cpprompt rbcmd"' in c for c in cards)
        assert not any("rb confirm" in c for c in cards)
    # a past version: no card holds an rb command at all; the live view holds each in its box
    with served(rec) as srv:
        st = json.loads(get(srv, "/state.json")[1])
        versions = [v["sha"] for v in (st.get("place") or {}).get("versions", [])]
        live = walk(srv)
        assert [a for a, (_, b) in live.items() if bare_commands(b)] == []
        if waiting:
            # closed form (owner, Nov 13 2026): the live view offers the prompt button, not the command
            assert any('class="ibtn ico cpprompt rbcmd"' in b for _, b in live.values())
        if versions:
            past = walk(srv, "?at=" + versions[-1])
            assert past and [a for a, (_, b) in past.items() if "rb confirm" in b or 'class="cmdbox"' in b or "cpprompt" in b] == []


def test_the_terminal_keeps_the_command_and_the_json_gives_it_apart(int8_demo):
    st = int8_demo.rb("status")
    designs = [x["id"] for x in st["experiments"] if (x.get("design") or {}).get("pending")]
    calls = [p["decision"] for p in st.get("pending", []) if p.get("decision")]
    assert designs or calls
    for subject in designs[:1] + calls[:1]:
        card = int8_demo.rb("show", subject)["card"]
        f = next(f for f in card["fields"] if f["key"] in ("plan", "confirm"))
        assert f["command"] == f"rb confirm {subject}" and "rb confirm" not in f["value"]
        text = rbt(int8_demo, int8_demo.root, "show", subject)
        assert f"rb confirm {subject}" in text


# ---------------------------------------------------------------- S1: one check, one set of digits

def test_a_checks_result_and_range_read_in_the_same_digits_everywhere(rec, tmp_path):
    from rabbit_brain.tui import ov_chart
    root = rec.root
    st = rbj(rec, root, "status")
    rows = [r for r in st["overview"]["chart"] if r.get("est_text")]
    assert rows
    dash = rbt(rec, root, "status", columns=160, lines=400)
    env = {**os.environ, **rec.env}
    subprocess.run([*command(), "page", "--out", str(tmp_path / "p.html")], cwd=root, env=env, check=True, capture_output=True)
    page = html.unescape((tmp_path / "p.html").read_text(encoding="utf-8"))
    ui = "\n".join(t.plain for t, _ in ov_chart(st["overview"]["chart"], 200))
    for r in rows[:25]:
        said = f'{r["result_text"]}  {r["range_text"]}' if r["range_text"] else r["result_text"]
        assert said in dash, (r["id"], said)
        result = next(f for f in rbj(rec, root, "show", r["id"])["card"]["fields"] if f["key"] == "result")
        assert result["value"].startswith(r["result_text"] + "  ") and (not r["range_text"] or result["value"].endswith(r["range_text"])), (r["id"], result["value"], said)
        assert result["value"] in rbt(rec, root, "show", r["id"], columns=160)
        card = re.search(r'<div class="card fc" id="k-' + re.escape(r["id"]) + r'".*?</dl></div>', page, re.S)
        if card:
            assert re.sub(r"<[^>]+>", "", card.group(0)).count(result["value"]) >= 1, (r["id"], result["value"])
        assert said in ui, (r["id"], said)


# ---------------------------------------------------------------- a question with no checks

def test_a_question_with_no_checks_says_its_state_alone(int8_demo, tmp_path):
    """rb status's dashboard says a question's state, then its strip after `·`; a question with no checks has no strip,
    and says its state alone (`  Open`), never `  Open · ` with nothing after the separator (57869d7)."""
    root = tmp_path / "work"
    shutil.copytree(int8_demo.root, root, symlinks=True)
    env = {**os.environ, **int8_demo.env}
    r = subprocess.run([*command(), "question", "add", "Does a larger calibration set help on the test split?", "--json"], cwd=root, env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    text = rbt(int8_demo, root, "status", columns=100, lines=200)
    assert "Does a larger calibration set help" in text
    assert not [l for l in text.splitlines() if l.rstrip().endswith("·")], text
    lines = text.splitlines()
    at = next(i for i, l in enumerate(lines) if "Does a larger calibration set help" in l)
    assert lines[at + 1] == "  Open", lines[at:at + 3]
