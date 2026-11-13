"""The git state as chips in rb's own header (owner, Nov 13 2026: "where is the git aware thingy? i didn't see any???").

rb page's header and rb open's: the branch and the commit as one chip ("main · c07560f") with a button that copies the
whole commit, and, only while record files are not committed, an amber chip with their count that opens their list. On
rb open the Where and Version pickers are that chip and still switch. A bundle carries `repo.branch` and
`repo.record_uncommitted`, which the hub's chip reads.
"""
import json
import re
import shutil
import subprocess
import urllib.request

import pytest

from rbtest import Open, command

HAS_GIT = shutil.which("git") is not None
pytestmark = pytest.mark.skipif(not HAS_GIT, reason="the chips say what git says")


def _git(root, *args):
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def mine(int8_demo, tmp_path):
    """A copy of the INT8 demo record (its git history with it), to write to."""
    root = tmp_path / "int8"
    shutil.copytree(int8_demo.root, root, symlinks=True)
    return root, int8_demo.env


def _listed(root):
    out = subprocess.run(["git", "status", "--porcelain=v1", "-z", "--untracked-files=all", "--", ".rb"], cwd=root, capture_output=True,
                         text=True, check=True).stdout
    return [e[3:] for e in out.split("\0") if e]


def _rb(root, env, *argv):
    got = subprocess.run([*command(), *argv], cwd=root, capture_output=True, encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL, env=env)
    assert got.returncode == 0, got.stderr
    return got


def _header(page: str) -> str:
    i = page.index('<header class="top"')
    return page[i:page.index("</header>", i)]


def test_the_page_header_says_the_branch_and_commit_and_what_is_not_committed(mine, tmp_path):
    root, env = mine
    head, branch = _git(root, "rev-parse", "HEAD"), _git(root, "branch", "--show-current")
    _rb(root, env, "page", "--out", str(tmp_path / "a.html"))
    top = _header((tmp_path / "a.html").read_text(encoding="utf-8"))
    # the chip opens where the page says how the checkout holds the record (closed form)
    assert re.search(r'<a class="gref" href="#in-git" title="[^"]*"><i class="ic ic-branch"></i><span class="gb">' + re.escape(branch)
                     + r'</span><span class="gsha mono">' + head[:7] + "</span></a>", top), top
    assert f'<button type="button" class="gcopy" data-sha="{head}" aria-label="Copy the commit"' in top
    assert "gunc" not in top and "not committed" not in top
    # two record files written and not committed: the amber chip counts them and opens their list
    _rb(root, env, "note", "add", "the held-out set is not in the pretraining data")
    _rb(root, env, "page", "--out", str(tmp_path / "b.html"))
    top = _header((tmp_path / "b.html").read_text(encoding="utf-8"))
    listed = _listed(root)
    assert len(listed) == 2
    m = re.search(r'<details class="gunc" data-fold="gunc:top"><summary class="fch fch-warn" title="2 record files not committed">2 not committed</summary>'
                  r'<div class="gpop" role="group" aria-label="Record files not committed"><ul class="gl">(.*?)</ul></div></details>', top)
    assert m, top
    assert re.findall(r'<li class="mono">([^<]+)</li>', m.group(1)) == listed


def test_rb_open_draws_its_where_and_version_pickers_as_the_chip(mine):
    root, env = mine
    head, branch = _git(root, "rev-parse", "HEAD"), _git(root, "branch", "--show-current")
    _rb(root, env, "note", "add", "the held-out set is not in the pretraining data")
    import os
    saved = dict(os.environ)
    os.environ.update(env)
    try:
        srv = Open(root)
        try:
            home = urllib.request.build_opener(urllib.request.ProxyHandler({})).open(srv.url + "/", timeout=120).read().decode("utf-8")
        finally:
            srv.close()
    finally:
        os.environ.clear()
        os.environ.update(saved)
    top = _header(home)
    where = re.search(r'<details class="pick" data-pick="where"[^>]*><summary[^>]*>(.*?)</summary>', top).group(1)
    assert '<span class="sr">Where </span><i class="ic ic-branch"></i>' in where and f'<span class="lab">{branch}</span>' in where
    # the words stay for a screen reader; what is not committed is the amber chip
    assert '<span class="gs sr" data-state="not_committed">' in where
    version = re.search(r'<details class="pick" data-pick="version"[^>]*><summary>(.*?)</summary>', top)
    if version:
        assert f'<span class="lab mono">{head[:7]}</span>' in version.group(1) and "now" in version.group(1)
    else:
        assert f'<span class="gsha mono">{head[:7]}</span>' in top
    assert f'class="gcopy" data-sha="{head}"' in top and ">2 not committed</summary>" in top
    # the pickers still list the places and versions to switch to
    assert 'data-place=' in top


def test_a_bundle_names_the_branch_and_the_record_files_not_committed(mine, tmp_path):
    root, env = mine
    _git(root, "remote", "add", "origin", "https://github.com/ana/int8-resnet50.git")
    _rb(root, env, "note", "add", "the held-out set is not in the pretraining data")
    _rb(root, env, "export", "--with-record", "--out", str(tmp_path / "x"))
    repo = json.loads((tmp_path / "x" / "manifest.json").read_text(encoding="utf-8"))["repo"]
    assert repo["branch"] == _git(root, "branch", "--show-current") and isinstance(repo["branch"], str)
    listed = _listed(root)
    assert repo["record_uncommitted"] == listed and len(listed) == 2
    # what was there stays as it was
    assert {"url", "commit", "changed"} <= set(repo)


def test_every_place_that_says_what_is_not_committed_agrees(mine, tmp_path):
    """One source (git's status of the record, Checkout's listed paths): the bundle's repo.record_uncommitted, which the
    hub's amber chip reads, the page's header chip and About's strip and their lists, the owner's meta line and rb status
    --json say the same files and the same count (orchestrator, Nov 13 08:15 +08)."""
    root, env = mine
    _git(root, "remote", "add", "origin", "https://github.com/ana/int8-resnet50.git")
    _rb(root, env, "note", "add", "the held-out set is not in the pretraining data")
    _rb(root, env, "note", "add", "the calibration images come from the training split")
    out = tmp_path / "x"
    _rb(root, env, "export", "--with-record", "--out", str(out))
    repo = json.loads((out / "manifest.json").read_text(encoding="utf-8"))["repo"]
    page = (out / "page.html").read_text(encoding="utf-8")
    files = repo["record_uncommitted"]
    assert files == _listed(root) and len(files) == 3, files
    lists = re.findall(r'>(\d+) not committed</summary><div class="gpop"[^>]*><ul class="gl">(.*?)</ul>', page)
    assert len(lists) == 2, lists                                 # the header's chip and About's
    for n, ul in lists:
        assert int(n) == len(files) and re.findall(r'<li class="mono">([^<]+)</li>', ul) == files, (n, ul)
    assert f'<a href="#in-git">{len(files)} record files not committed</a>' in page
    st = json.loads(_rb(root, env, "status", "--json").stdout)["data"]["repository"]
    assert st["record_files_not_committed"] == len(files) and st["state"] == "not_committed"
