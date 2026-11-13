"""The Files tree in the left pane (owner, Nov 13 2026: "btw where is my file explorer/dir/browser on left pane?").

Under Record, "Files (n)" opens a tree of every file the record knows, folders folding, a filter box on top; each row a
type icon, the name and chips (cited, settings, made by a run or read by runs, not committed, changed since cited,
missing). A file opens in the right pane: on rb open whole, on a page that carries it (a bundle's sources) whole, and
elsewhere its cited lines with a chip that says the full file stays on the machine it was made on.
"""
import json
import os
import re
import subprocess
import urllib.error
import urllib.request

from rbtest import Open, command


def _page(rec, out, *extra):
    got = subprocess.run([*command(), "page", "--out", str(out), *extra], cwd=rec.root, capture_output=True, encoding="utf-8",
                         errors="replace", stdin=subprocess.DEVNULL, env=rec.env)
    assert got.returncode == 0, got.stderr
    return out.read_text(encoding="utf-8")


def _card(page: str, anchor: str) -> str:
    i = page.index(f' id="{anchor}"')
    i = page.rindex("<div", 0, i)
    depth, j = 0, i
    for m in re.finditer(r"<(/?)div\b", page[i:]):
        depth += -1 if m.group(1) else 1
        if depth == 0:
            j = i + m.end()
            break
    return page[i:page.index(">", j) + 1]


def _row(page: str, path: str) -> str:
    m = re.search(r'<li class="fr" data-path="' + re.escape(path) + r'">.*?</li>', page, flags=re.S)
    assert m, path
    return m.group(0)


def test_files_sits_under_record_as_a_tree_with_a_filter_and_chips(int8_demo, tmp_path):
    page = _page(int8_demo, tmp_path / "p.html")
    i = page.index('<nav class="nav"')
    nav = page[i:page.index("</nav>", i)]
    record = nav[nav.index('<ul class="rec">'):]
    # under Record: "Files (n)", the tree right under it, no tabs over the navigator
    m = re.search(r'<li class="fls"><a href="#files" class="ftog" aria-expanded="false" aria-controls="files">Files <span class="n">(\d+)</span></a>'
                  r'<div class="ftree" id="files"', record)
    assert m, record[:400]
    rows = re.findall(r'<li class="fr" data-path="([^"]+)"', nav)
    assert int(m.group(1)) == len(rows) and "configs/train.yaml" in rows, rows
    assert "ptabs" not in page and 'data-tab="files"' not in page
    assert '<input type="search" class="ffilter" placeholder="Filter files" aria-label="Filter files"' in nav
    # folders fold, each by its path; a file's row is its type icon, its name and its chips, and opens its card
    assert '<details data-folder="results/" data-fold="files:results/"' in nav and 'data-folder="results/e1-seed1/"' in nav
    cfg = _row(nav, "configs/train.yaml")
    assert 'href="#file-configs/train.yaml"' in cfg and 'class="fti fti-cfg"' in cfg and '<span class="fn">train.yaml</span>' in cfg
    assert re.search(r'class="fch fch-use"[^>]*>\d+ settings?</span>', cfg), cfg
    assert re.search(r'class="fch fch-use"[^>]*>cited</span>', _row(nav, "docs/baseline.md")) and 'fti-doc' in _row(nav, "docs/baseline.md")
    assert re.search(r'class="fch fch-use"[^>]*>read by \d+ runs</span>', _row(nav, "results.db")), _row(nav, "results.db")
    assert re.search(r'class="fch fch-use"[^>]*>(made by [^<]+|read by \d+ runs)</span>', _row(nav, "results/e1-seed1/metrics.json"))
    # the words of every chip are rb's own: no glyph that needs a key
    assert not re.search(r'class="fch[^"]*"[^>]*>[❝⚙▣△±⊘∅]', nav)


def test_a_file_on_a_page_shows_its_cited_lines_and_says_the_whole_stays_home(int8_demo, tmp_path):
    page = _page(int8_demo, tmp_path / "p.html")
    card = _card(page, "file-docs/baseline.md")
    assert '<span class="fch fch-dim">full file stays on your machine</span>' in card
    assert re.search(r'<pre class="srclines"><span class="sl[^"]*" data-n="\d+">', card) and '<span class="sl on"' in card
    # each cited line links to what cites it
    assert 'class="ctag"' in card
    assert 'fwhole' not in card


def test_rb_open_serves_any_file_it_lists_whole_and_nothing_else(int8_demo):
    old, saved = os.getcwd(), dict(os.environ)
    os.environ.update(int8_demo.env)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        srv = Open(int8_demo.root)
        try:
            home = opener.open(srv.url + "/", timeout=120).read().decode("utf-8")
            # the page holds no file card: each is served when it is opened
            assert 'id="file-configs/train.yaml"' not in home and 'data-path="configs/train.yaml"' in home
            got = opener.open(srv.url + "/o/" + urllib.request.quote("file-configs/train.yaml") + ".html", timeout=120).read().decode("utf-8")
            lines = (int8_demo.root / "configs" / "train.yaml").read_text(encoding="utf-8").splitlines()
            assert '<pre class="srclines fwhole">' in got and len(re.findall(r'<span class="sl[^"]*" data-n="', got)) == len(lines)
            assert "full file stays" not in got and re.search(r'class="fch fch-at mono"[^>]*>[0-9a-f]{12}</span>', got)
            # a file git lists untracked opens too, said not committed
            new = opener.open(srv.url + "/o/" + urllib.request.quote("file-results/fp16-probe/metrics.json") + ".html", timeout=120).read().decode("utf-8")
            # closed form (owner, Nov 13 2026): the chip opens where its state is listed; nothing cites this file, so
            # that is its row in Files, at its folder, never a "Cited by" part the card does not hold
            warn = re.search(r'<a class="fch fch-warn" href="#([^"]+)"( data-folder="[^"]*")?>not committed</a>', new)
            assert "fwhole" in new and warn, new
            assert warn.group(1) == "files" and warn.group(2) == ' data-folder="results/fp16-probe/"', warn.group(0)
            assert ":by" not in new, new
            # git's folder, rb's, and a path out of the project are no file it lists
            for bad in ["file-.git/config", "file-.rb/project.yaml", "file-../etc/passwd", "file-nothing.txt"]:
                try:
                    opener.open(srv.url + "/o/" + urllib.request.quote(bad) + ".html", timeout=60)
                    raise AssertionError(bad)
                except urllib.error.HTTPError as e:
                    assert e.code == 404, (bad, e.code)
        finally:
            srv.close()
    finally:
        os.environ.clear()
        os.environ.update(saved)
        os.chdir(old)


def test_a_bundle_page_opens_the_files_it_carries_whole(int8_demo, tmp_path):
    out = tmp_path / "x"
    got = subprocess.run([*command(), "export", "--with-record", "--out", str(out)], cwd=int8_demo.root, capture_output=True,
                         encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL, env=int8_demo.env)
    assert got.returncode == 0, got.stderr
    page = (out / "page.html").read_text(encoding="utf-8")
    carried = _card(page, "file-configs/train.yaml")
    assert (out / "record" / "configs" / "train.yaml").is_file()
    assert '<pre class="srclines fwhole">' in carried and "full file stays" not in carried
    assert not (out / "record" / "notes" / "2026-05-07.md").exists()
    left = _card(page, "file-notes/2026-05-07.md")
    assert "full file stays on your machine" in left and "fwhole" not in left


def test_the_tree_opens_filters_and_opens_a_file_in_the_right_pane(int8_demo, tmp_path):
    from test_shell import _cdp_script, _node, _runner
    page = tmp_path / "p.html"
    _page(int8_demo, page)
    js = _cdp_script(tmp_path / "tree.mjs", """
const out = {};
const PROBE = `(() => { const li = document.querySelector('#nav li.fls'), t = li.querySelector('.ftree');
  const rows = [...li.querySelectorAll('li.fr')].filter(r => r.checkVisibility()).map(r => r.dataset.path);
  const ins = document.querySelector('#insp .card, #insp .ibody'); const r = t.getBoundingClientRect();
  return { open: li.classList.contains('open'), expanded: li.querySelector('.ftog').getAttribute('aria-expanded'), shown: t.checkVisibility(),
    rows, insp: ins ? ins.innerText.slice(0, 200) : '', top: r.top, width: r.width, ul: li.querySelector('ul.ft').getBoundingClientRect().width,
    focus: document.activeElement && document.activeElement.className }; })()`;
await cdp.run(async () => {
  for (const width of [1440, 390]) {
    const page = await (await cdp.launchChrome()).page();
    await page.viewport(width, width < 600 ? 844 : 900);
    await page.goto(%s);
    await page.ev("localStorage.setItem('rb.guide.1', 'ended'); true");
    await page.goto(%s);
    await page.waitFor("document.documentElement.classList.contains('ready')", 10000); await cdp.sleep(300);
    const got = { before: await page.ev(PROBE) };
    if (width > 960) await page.clickEl("document.querySelector('#nav .ftog')");
    else { await page.clickEl("document.querySelector('button[data-more]')"); await cdp.sleep(150); await page.clickEl("document.querySelector('.ctl.ftab')"); }
    await cdp.sleep(250); got.opened = await page.ev(PROBE);
    await page.ev("(() => { const f = document.querySelector('#nav .ffilter'); f.value = 'train'; f.dispatchEvent(new Event('input', { bubbles: true })); return true; })()");
    await cdp.sleep(150); got.filtered = await page.ev(PROBE);
    await page.clickEl("document.querySelector('#nav li.fr[data-path=\\"configs/train.yaml\\"] a')");
    await cdp.sleep(400); got.file = await page.ev(PROBE);
    out[width] = got;
    await cdp.stopAll();
  }
  return {};
}, { timeoutMs: 120000 });
console.log(JSON.stringify({ ok: true, out }));
""" % (json.dumps(page.as_uri()), json.dumps(page.as_uri())))
    code, said, err = _runner(_node(), js, env=dict(os.environ), timeout=180)
    assert code == 0 and said.get("ok"), err
    for width in ("1440", "390"):
        g = said["out"][width]
        assert not g["before"]["open"] and not g["before"]["shown"], (width, g["before"])
        assert g["opened"]["open"] and g["opened"]["shown"] and len(g["opened"]["rows"]) > 1, (width, g["opened"])
        assert g["filtered"]["rows"] == ["configs/train.yaml"], (width, g["filtered"])
        assert "train.yaml" in g["file"]["insp"], (width, g["file"])
    assert said["out"]["1440"]["opened"]["expanded"] == "true"
    # the tree's list takes the pane's width: no rule of a file's type icon narrows it (review of rec-2)
    assert said["out"]["1440"]["opened"]["ul"] > 150, said["out"]["1440"]["opened"]
    # under 960 the tree opens as a sheet, the filter box taking the keys, and closes once a file is chosen
    assert said["out"]["390"]["opened"]["width"] >= 380 and "ffilter" in said["out"]["390"]["opened"]["focus"]
    assert not said["out"]["390"]["file"]["open"]
