"""About as one strip of facts (owner, Nov 13 2026: "i click About i see all these... human????? can read?????"), at
`#in-git`, where the phrase that says how a page's checkout held the record links: rb's version as a badge, when the page
was made, the header's git chips (the branch and the commit with a button that copies the commit, and an amber chip
counting the record files not committed that opens their list), what git says where it is not a commit (ignored by git,
not in git, rb could not read git) as a chip, and the record's id. No paragraph on git and no definition.

Pinned here, on pages rb page and rb export write in git repositories in scratch folders:

- Each state's strip says what holds, as chips.
- The phrase links to the strip in the owner's header span (the owner's alone).
- An export published with its record at an address (rb export --with-record --url) gives the address, a host link
  for github.com, gitlab.com and codeberg.org where origin's branches, as git last fetched them, hold the commit, and the
  lines to check it yourself, all in the Rerun panel's own classes (rrun rrx); where they lack it the page says so.
  rb page and rb export without --url name no address, in the strip or in the page's data.
- Nothing private: no place path, no credentials, no git message, on any page; the branch only in the git chip.
"""
import html
import json
import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

from rbtest import as_the_user, command, environment

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="the checkout is git's")
GIT = shutil.which("git")
BLOCK = re.compile(r'<div class="astrip" id="in-git" tabindex="-1">(.*?)<p class="dim sm honest1">', re.S)
STRIP = re.compile(r'<div class="astrip" id="in-git" tabindex="-1">(.*?)</div>(?=<p class="dim sm wit"><a |<div class="rrun|<p class="dim sm honest1">)', re.S)
MADE = re.compile(r"a snapshot of the record at (\d{4}-\d\d-\d\d \d\d:\d\d UTC)")
DEFINE = "Record files are the files rb keeps for this project next to its code, such as its questions, rules, runs, notes and log."


@pytest.fixture
def home(tmp_path, monkeypatch):
    as_the_user(monkeypatch)
    cfg = tmp_path / "gitconfig"
    cfg.write_text("[user]\n\temail = ana@example.org\n\tname = ana\n[init]\n\tdefaultBranch = main\n", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(cfg))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("RB_ACTOR", "human:ana")
    monkeypatch.setenv("RB_TEST_SHELL", "1")
    return tmp_path


def rb(cwd: Path, *argv: str, env: dict = None, code: int = 0) -> str:
    r = subprocess.run([*command(), *argv], cwd=cwd, env=env or environment(), capture_output=True, text=True, stdin=subprocess.DEVNULL)
    assert code is None or r.returncode == code, (argv, r.returncode, r.stdout, r.stderr)
    return r.stdout


def git(cwd: Path, *argv: str) -> str:
    return subprocess.run([GIT, *argv], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def record(root: Path, *, repo: bool = True) -> Path:
    root.mkdir(parents=True)
    if repo:
        git(root, "init", "-q")
    rb(root, "init", "INT8 ResNet-50", "--no-agents")
    rb(root, "question", "add", "Does INT8 cost accuracy on the held-out set?", "--id", "q-int8")
    return root


def page(root: Path, out: Path, env: dict = None) -> str:
    rb(root, "page", "--out", str(out), env=env)
    return out.read_text(encoding="utf-8")


def block(doc: str) -> str:
    m = BLOCK.search(doc)
    assert m, "no block"
    return m.group(1)


def chips(doc: str) -> list:
    """The strip's chips, each as its words, the not-committed chip's list left to `listed`."""
    m = STRIP.search(doc)
    assert m, "no strip"
    t = re.sub(r'<div class="gpop".*?</div>', "", m.group(1), flags=re.S)
    return [html.unescape(re.sub(r"<[^>]+>", " ", c)).split() for c in re.findall(r'<(?:span|a|summary)[^>]*class="(?:abadge|fch|gref)[^"]*"[^>]*>(.*?)</(?:span|a|summary)>(?=<|$)', t)]


def listed(doc: str) -> list:
    m = re.search(r'<div class="gpop"[^>]*><ul class="gl">(.*?)</ul>', STRIP.search(doc).group(1), re.S)
    return re.findall(r'<li class="mono">([^<]+)</li>', m.group(1)) if m else []


def said(doc: str) -> str:
    """The block as a reader reads it: its text, boxes on lines of their own."""
    t = re.sub(r'<pre class="cmd[^"]*">(.*?)</pre>', r"\n[\1]\n", block(doc))
    return html.unescape(re.sub(r"<[^>]+>", "", t))


def commit(root: Path) -> str:
    return git(root, "rev-parse", "HEAD")


def lead(doc: str) -> str:
    """Part 1's lead on a page: when it was made, line 1's minute."""
    return f"when this page was made, {MADE.search(doc).group(1)}"


def test_each_state_s_strip_says_what_holds(home):
    at = home / "int8-resnet50"
    root = record(at)
    doc = page(root, home / "nocommit.html")
    version = re.search(r'<span class="abadge">rb ([^<]+)</span>', doc).group(1)
    c0 = chips(doc)
    assert c0[0] == ["rb", version] and c0[1][0] == "snapshot" and c0[2] == ["main"] and '<span class="gb">main</span><span class="gsha">no commit yet</span>' in block(doc), c0
    # with no commit, every record file is new: the amber chip counts them; "no commit yet" is said once
    assert c0[3][1:] == ["not", "committed"] and int(c0[3][0]) == len(listed(doc)) > 0 and block(doc).count("no commit yet") == 1, c0
    assert "gcopy" not in block(doc) and DEFINE not in doc and "rb checked the record with git" not in block(doc)

    git(root, "add", "-A")
    git(root, "commit", "-qm", "the record")
    c = commit(root)
    doc = page(root, home / "committed.html")
    assert chips(doc)[2] == ["main"] and f'<span class="gsha mono">{c[:7]}</span>' in block(doc) and f'class="gcopy" data-sha="{c}"' in block(doc) and "gunc" not in block(doc), chips(doc)
    assert ["int8-resnet-50"] in chips(doc) or any(len(x) == 1 for x in chips(doc))
    # rb's mark of its own page rides on the snapshot chip
    assert re.search(r'<span class="fch fch-at" title="made with rb page · a snapshot of the record at [^"]+">snapshot ', block(doc))

    rb(root, "note", "add", "the held-out set is not in the pretraining data")
    doc = page(root, home / "notcommitted.html")
    assert ["2", "not", "committed"] in chips(doc), chips(doc)
    assert listed(doc)[0] == ".rb/log.jsonl" and re.fullmatch(r"\.rb/notes/n\w+\.yaml", listed(doc)[1]), listed(doc)

    other = record(home / "notheld")
    (other / "eval.py").write_text("print(1)\n", encoding="utf-8")
    git(other, "add", "eval.py")
    git(other, "commit", "-qm", "code")
    doc = page(other, home / "notheld.html")
    n = len([p for p in (other / ".rb").rglob("*") if p.is_file() and not {"cache", "journal"} & set(p.relative_to(other / ".rb").parts)])
    assert [str(len(listed(doc))), "not", "committed"] in chips(doc) and len(listed(doc)) >= n - 2, (chips(doc), n)

    ign = record(home / "ignored")
    (ign / ".gitignore").write_text(".rb/\n", encoding="utf-8")
    git(ign, "add", ".gitignore")
    git(ign, "commit", "-qm", "ignore")
    doc = page(ign, home / "ignored.html")
    assert any(x[1:] == ["record", "files", "ignored", "by", "git"] for x in chips(doc)), chips(doc)

    nogit = record(home / "nogit", repo=False)
    doc = page(nogit, home / "nogit.html")
    assert ["not", "in", "git"] in chips(doc) and "gref" not in block(doc), chips(doc)


def test_a_rule_outside_the_repository_says_no_path(home):
    """On Windows git names the rule's file with its drive letter (C:\\...:1:.rb/): the page still names no path (S6d)."""
    root = record(home / "int8-resnet50")
    outside = home / "global-ignore"
    outside.write_text(".rb/\n", encoding="utf-8")
    git(root, "config", "core.excludesFile", str(outside))
    (root / "eval.py").write_text("print(1)\n", encoding="utf-8")
    git(root, "add", "eval.py")
    git(root, "commit", "-qm", "code")
    doc = page(root, home / "global.html")
    assert any(x[1:] == ["record", "files", "ignored", "by", "git"] for x in chips(doc)), chips(doc)
    assert str(home) not in doc and home.as_posix() not in doc and "global-ignore" not in doc


@pytest.mark.skipif(os.name == "nt", reason="the git put first on PATH here is a shell script")
def test_a_git_that_cannot_be_read_says_no_git_words(home, tmp_path_factory):
    root = record(home / "int8-resnet50")
    fake = tmp_path_factory.mktemp("dubious")
    (fake / "git").write_text("#!/bin/sh\necho \"fatal: detected dubious ownership in repository at '$PWD'\" >&2\nexit 128\n", encoding="utf-8")
    (fake / "git").chmod((fake / "git").stat().st_mode | stat.S_IEXEC)
    env = {**environment(), "PATH": f"{fake}{os.pathsep}{os.environ['PATH']}"}
    doc = page(root, home / "dubious.html", env=env)
    assert ["rb", "could", "not", "read", "git"] in chips(doc), chips(doc)
    assert "dubious" not in doc and "fatal" not in doc and str(home) not in doc


def test_the_phrase_links_to_the_strip_in_the_owner_s_span(home):
    root = record(home / "int8-resnet50")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "the record")
    doc = page(root, home / "p.html")
    # the owner's span: the owner's alone, the phrase its link
    assert re.findall(r'<span class="held" data-vw="([^"]*)"> \u00b7 <a href="#in-git">all record files committed</a></span>', doc) == ["owner"], doc
    # every tier sees the strip: it is drawn for none of them apart
    assert doc.count('id="in-git"') == 1 and not re.search(r'data-vw="[^"]*"[^>]*><div class="astrip" id="in-git"', doc)


def published(root: Path, address: str, home: Path, name: str, *more: str) -> str:
    out = home / name
    rb(root, "export", "--with-record", "--url", "https://pages.example.org/int8/", "--out", str(out), *more)
    return (out / "page.html").read_text(encoding="utf-8")


def with_origin(home: Path, address: str) -> Path:
    bare = home / "origin.git"
    if not bare.exists():
        git(home, "init", "-q", "--bare", str(bare))
    root = record(home / f"int8-{abs(hash(address)) % 10000}")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "the record")
    git(root, "remote", "add", "origin", str(bare))
    git(root, "push", "-q", "origin", "HEAD:refs/heads/main")
    git(root, "fetch", "-q", "origin")
    git(root, "remote", "set-url", "origin", address)
    return root


@pytest.mark.parametrize("address,link", [
    ("https://github.com/ana/int8.git", "https://github.com/ana/int8/tree/{c}/.rb"),
    ("https://gitlab.com/ana/vision/int8/", "https://gitlab.com/ana/vision/int8/-/tree/{c}/.rb"),
    ("https://codeberg.org/ana/int8", "https://codeberg.org/ana/int8/src/commit/{c}/.rb"),
    ("https://git.example.org/ana/int8.git", None),
])
def test_an_export_with_an_address_gives_the_host_link_and_the_lines_where_origin_holds_the_commit(home, address, link):
    root = with_origin(home, address)
    c = commit(root)
    doc = published(root, address, home, "pub")
    b = block(doc)
    pid = re.search(r"^id: (\S+)", (root / ".rb/project.yaml").read_text(encoding="utf-8"), re.M).group(1).strip('"')
    code = b[b.index('<div class="rrun rrx">'):]
    assert address in code and address not in b[:b.index('<div class="rrun rrx">')]
    text = html.unescape(re.sub(r"<[^>]+>", "", code))
    if link:
        assert f'<a href="{link.format(c=c)}">' in code and f"The record's files at commit {c[:12]} open on " in text, code
        assert " for anyone who can see the repository." in text, text
    else:
        assert "<a href" not in code, code
    boxes = [html.unescape(re.sub(r"<[^>]+>", "", x)) for x in re.findall(r'<pre class="cmd">(.*?)</pre>', code)]
    assert boxes == ["pip install rabbit-brain", f"git clone {address} {pid}", f"cd {pid} && git checkout {c}"], boxes
    assert f'<span class="au">{address}</span>' in code and f'<span class="au">{pid}</span>' in code
    assert "Then rb status says “no branch · all record files committed” in its first lines." in text, text


def test_where_origin_lacks_the_commit_the_page_says_so_and_has_no_link(home):
    address = "https://github.com/ana/int8.git"
    root = with_origin(home, address)
    first = commit(root)
    rb(root, "note", "add", "the held-out set is not in the pretraining data")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "a note")
    c = commit(root)
    text = html.unescape(re.sub(r"<[^>]+>", "", block(published(root, address, home, "pub2"))))
    assert f"When this page was made, commit {c[:12]} was on no branch git had fetched from {address}, so a clone of it may not have that commit." in text, text
    assert (f"Commit {first[:12]} there holds the same code; the commits after it change only the record, the results files rb reads and files"
            " rb wrote.") in text, text
    assert "github.com/ana/int8/tree" not in block(published(root, address, home, "pub3")) and "git checkout" not in text
    # with no branch of origin seen, rb says it cannot tell
    git(root, "update-ref", "-d", "refs/remotes/origin/main")
    text = html.unescape(re.sub(r"<[^>]+>", "", block(published(root, address, home, "pub4"))))
    assert f"When this page was made, git had fetched no branch from {address}, so rb could not tell whether a clone of it has commit {c[:12]}." in text, text


def test_no_address_where_none_belongs(home):
    address = "https://github.com/ana/int8.git"
    root = with_origin(home, address)
    for doc in (page(root, home / "p.html"), (lambda: (rb(root, "export", "--out", str(home / "ex")), (home / "ex/page.html").read_text(encoding="utf-8"))[1])()):
        assert 'id="in-git"' in doc and "github.com/ana" not in doc and "rrun" not in block(doc)
    rb(root, "export", "--with-record", "--url", "https://pages.example.org/int8/", "--no-repo", "--out", str(home / "norepo"))
    doc = (home / "norepo/page.html").read_text(encoding="utf-8")
    assert "github.com/ana" not in doc and "rrun" not in block(doc)


def test_nothing_private_on_pages(home):
    """No place path, no credentials and no git message, on a branch named like a session's, at a detached HEAD, and
    with an origin that carries a user name and a token; the branch only in the git chip."""
    address = "https://ana:tok-9f2c@github.com/ana/int8.git"
    root = with_origin(home, address)
    git(root, "checkout", "-q", "-b", "claude/try-1")
    for name, doc in (("branch", published(root, address, home, "b1")), ("branch page", page(root, home / "b.html"))):
        # the branch is the git chip's alone (owner, Nov 13 2026: "main · c07560f" in the header and About)
        rest = re.sub(r'<a class="gref" href="#in-git" title="[^"]*">.*?</span></a>', "", doc)
        assert "claude/try-1" in doc and "claude/try-1" not in rest and "try-1" not in rest, name
        assert "tok-9f2c" not in doc and "ana:" not in doc and str(home) not in doc, name
    git(root, "checkout", "-q", "--detach")
    doc = published(root, address, home, "d1")
    assert '<span class="gb">no branch</span>' in block(doc)
    rest = re.sub(r'<a class="gref" href="#in-git" title="[^"]*">.*?</span></a>', "", block(doc))
    assert "no branch" not in rest.split("Then rb status says")[0] and "tok-9f2c" not in doc and str(home) not in doc
    assert "github.com/ana/int8/tree/" in doc


def test_in_a_browser_the_phrase_reaches_the_block_and_a_hub_reader_without_rb_clone_sees_no_address(home, tmp_path):
    """About's strip shows at #about on rb page's file and on a page served as the hub serves one (its
    rb-viewer meta, then rb's script inlined before </head>); from the owner's header span, on the owner's page. A
    reader there sees the block, and its address only where the rb-clone meta lets readers rerun."""
    import test_shell as ts
    address = "https://github.com/ana/int8.git"
    root = with_origin(home, address)
    plain = home / "plain.html"
    page(root, plain)
    exported = published(root, address, home, "pub")
    script = (Path(__file__).resolve().parents[1] / "rust/src/shell/assets/script.min.js").read_text(encoding="utf-8")
    pages = {"page": plain}
    for viewer, clone in (("owner", True), ("reader", True), ("reader", False)):
        metas = f'<meta name="rb-viewer" content="{viewer}">' + ('<meta name="rb-clone" content="https://pages.example.org/int8/bundle.zip">' if clone else "")
        at = home / f"hub-{viewer}{'-clone' if clone else ''}.html"
        at.write_text(exported.replace("</head>", f"{metas}<script>{script}</script></head>", 1), encoding="utf-8")
        pages[at.stem] = at
    runs = [[name, p.as_uri(), w] for name, p in pages.items() for w in (1280, 390)]
    js = ts._cdp_script(tmp_path / "git.mjs", """
const out = {};
await cdp.run(async () => {
  for (const [name, url, width] of RUNS) {
    const page = await (await cdp.launchChrome()).page();
    await page.viewport(width, 900);
    await page.goto(url); await cdp.sleep(500);
    const probe = `(() => { const b = document.getElementById('in-git'), v = document.querySelector('main .view.on'); const r = b && b.getBoundingClientRect();
      return { view: v ? v.id : null, shown: !!(r && r.height > 0 && r.top < innerHeight && r.bottom > 0), text: v ? v.innerText : '' }; })()`;
    const got = { span: await page.ev(`(() => { const a = [...document.querySelectorAll('.ovh .held a[href="#in-git"]')].find(e => e.getBoundingClientRect().width > 0); return !!a; })()`) };
    if (got.span) { await page.clickEl(`[...document.querySelectorAll('.ovh .held a[href="#in-git"]')].find(e => e.getBoundingClientRect().width > 0)`); await cdp.sleep(400); got.fromSpan = await page.ev(probe); }
    await page.goto(url + '#about'); await cdp.sleep(500);
    got.fromLine = await page.ev(probe);
    out[name + '@' + width] = got;
    await cdp.stopAll();
  }
  return {};
}, { timeoutMs: 300000 });
console.log(JSON.stringify({ ok: true, out }));
""".replace("RUNS", json.dumps(runs)))
    code, got, err = ts._runner(ts._node(), js, env=dict(os.environ), timeout=320)
    assert code == 0 and got.get("ok"), err[-3000:]
    for name, g in got["out"].items():
        line = g["fromLine"]
        assert line["view"] == "about" and line["shown"] and "snapshot" in line["text"], (name, g)
        # the owner's span is the owner's alone, and its link lands on the block too
        assert g["span"] == name.startswith("hub-owner"), (name, g)
        if g["span"]:
            assert g["fromSpan"]["view"] == "about" and g["fromSpan"]["shown"], (name, g)
        # the address and the lines that use it: on the hub only where rb-clone lets readers rerun; never on rb page
        shows_address = "github.com/ana/int8" in line["text"]
        assert shows_address == name.startswith(("hub-owner-clone", "hub-reader-clone")), (name, line["text"])


def test_no_lines_to_check_where_the_commit_holds_no_record_and_no_m_after_a_deletion(home):
    """Review S6: a clone of a commit that does not hold the record (or where git ignores it) has no record, so the page
    gives no address part; after record files are deleted, "N of its M" still adds up, M counting what HEAD holds."""
    address = "https://github.com/ana/int8.git"
    bare = home / "origin.git"
    git(home, "init", "-q", "--bare", str(bare))
    root = record(home / "int8-resnet50")
    (root / "eval.py").write_text("print(1)\n", encoding="utf-8")
    git(root, "add", "eval.py")
    git(root, "commit", "-qm", "code")
    git(root, "remote", "add", "origin", str(bare))
    git(root, "push", "-q", "origin", "HEAD:refs/heads/main")
    git(root, "fetch", "-q", "origin")
    git(root, "remote", "set-url", "origin", address)
    b = block(published(root, address, home, "notheld"))
    assert address not in b and "git checkout" not in b and "Then rb status says" not in b and "/tree/" not in b, b
    for i in range(3):
        rb(root, "note", "add", f"the held-out set, note {i}")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "the record")
    for n in (root / ".rb/notes").iterdir():
        n.unlink()
    doc = page(root, home / "deleted.html")
    assert ["3", "not", "committed"] in chips(doc), chips(doc)
    assert sum(p.startswith(".rb/notes/") for p in listed(doc)) == 3, listed(doc)
