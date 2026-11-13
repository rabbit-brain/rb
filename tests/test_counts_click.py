"""The sweep (owner, Nov 13 2026: "so bland and can't click... what for" on "Experiment · open · 9 runs"): on the record
page a count is a link or a chip that filters, and a state is a coloured chip with an icon and a hover. Pinned on the
places UX7's sweep lists: an experiment's header, the Overview's strip, Rules' tally, Runs' group heads and History's
count of changes."""
import json
import os
import re
import subprocess

from rbtest import command

COUNT = re.compile(r"\b\d+ (?:runs?|rules?|changes|seeds?|passed|failed|can't tell yet|no results yet|not met|reproduced|not reproduced)\b")
STATE = re.compile(r"(?<![\w-])(?:open|accepted|rejected|parked|stopped|shipped|pivoted|planned|investigating)(?![\w-])")


def _page(rec, tmp_path):
    out = tmp_path / "p.html"
    got = subprocess.run([*command(), "page", "--out", str(out)], cwd=rec.root, env={**rec.env, "RB_TEST_SHELL": "1"}, capture_output=True, text=True)
    assert got.returncode == 0, got.stderr
    return out


def _bare(html: str) -> str:
    """The region's words outside every link, button and chip."""
    html = re.sub(r"<(a|button)\b[^>]*>.*?</\1>", " ", html, flags=re.S)
    html = re.sub(r'<span class="stc[^"]*"[^>]*><span class="sti"[^>]*>[^<]*</span>[^<]*</span>', " ", html)
    html = re.sub(r'<span class="(?:kind|fch)[^"]*"[^>]*>[^<]*</span>', " ", html)
    return re.sub(r"<[^>]+>", " ", html)


def test_counts_link_and_states_are_chips_where_the_sweep_lists_them(int8_demo, tmp_path):
    page = _page(int8_demo, tmp_path).read_text(encoding="utf-8")
    regions = {}
    for m in re.finditer(r'<p class="who chips" id="(x-[^"]+):lock"[^>]*>(.*?)</p>', page, re.S):
        regions[m.group(1)] = m.group(2)
        # closed form (owner, Nov 13 2026): the state chip is a link to the experiment's record card, an element the page holds
        chip = re.search(r'<(?:span|a href="#([^"]+)") class="stc st-[a-z]+" title="[^"]+"><span class="sti" aria-hidden="true">', m.group(2))
        assert chip and (not chip.group(1) or f' id="{chip.group(1)}"' in page), m.group(2)
        assert re.search(rf'<a class="fch fch-use" href="#{m.group(1)}:runs">\d+ runs?</a>', m.group(2)), m.group(2)
    assert regions, "no experiment header"
    strip = re.search(r'<div class="ovstrip[^"]*"[^>]*>(.*?)</div>', page, re.S).group(1)
    regions["strip"] = strip
    assert re.findall(r'<a class="ct" href="#criteria" data-outcome="(meets|misses|undetermined|untested)"><b>\d+</b>', strip), strip
    tally = re.search(r'<div class="rflts" id="rule-filters"[^>]*>(.*?)</div>', page, re.S).group(1)
    regions["rules"] = tally
    assert tally.startswith('<button type="button" class="rflt" data-rule="" aria-pressed="true">All ')
    for m in re.finditer(r'<div class="grouphd">(.*?)</div>', page, re.S):
        regions[f"group {len(regions)}"] = m.group(1)
        assert re.search(r'<a class="fch fch-use" href="#x-[^"]+:(runs|criteria)">\d+ (runs?|rules?)', m.group(1)), m.group(1)
    for name, html in regions.items():
        bare = _bare(html)
        assert not COUNT.search(bare) and not STATE.search(bare), (name, bare)
    # every rule row says its state and its strip group, so a chip or a count can filter to it
    rows = re.findall(r'<details class="nd krow" data-row data-rule="([^"]*)" data-outcome="(meets|misses|undetermined|untested)"', page)
    assert rows and all(r[0] for r in rows), rows


def test_a_rules_chip_and_a_strip_count_filter_the_rules(int8_demo, tmp_path):
    from test_shell import _cdp_script, _node, _runner
    page = _page(int8_demo, tmp_path)
    js = _cdp_script(tmp_path / "filters.mjs", """
let got = {};
await cdp.run(async () => {
  const p = await (await cdp.launchChrome()).page();
  await p.viewport(1440, 900);
  await p.goto(%s);
  await p.ev("localStorage.setItem('rb.guide.1', 'ended'); true");
  await p.goto(%s + '#criteria');
  await p.waitFor("document.documentElement.classList.contains('ready')", 10000); await cdp.sleep(300);
  const rows = `[...document.querySelectorAll('#criteria .krow')].filter(r => !r.hidden).map(r => r.dataset.rule)`;
  got.all = await p.ev(rows);
  const rule = await p.ev(`[...document.querySelectorAll('#rule-filters .rflt')].map(b => b.dataset.rule).filter(Boolean)[0]`);
  await p.clickEl(`document.querySelector('#rule-filters .rflt[data-rule="${rule}"]')`); await cdp.sleep(200);
  got.rule = rule; got.one = await p.ev(rows);
  await p.clickEl(`document.querySelector('#rule-filters .rflt[data-rule="${rule}"]')`); await cdp.sleep(200);
  got.again = await p.ev(rows);
  await p.goto(%s + '#overview'); await cdp.sleep(400);
  const outcome = await p.ev(`document.querySelector('.ovstrip a.ct').dataset.outcome`);
  await p.clickEl(`document.querySelector('.ovstrip a.ct')`); await cdp.sleep(400);
  got.outcome = outcome; got.hash = await p.ev('location.hash');
  got.strip = await p.ev(`[...document.querySelectorAll('#criteria .krow')].filter(r => !r.hidden).map(r => r.dataset.outcome)`);
  await cdp.stopAll();
  return {};
}, { timeoutMs: 120000 });
console.log(JSON.stringify({ ok: true, got }));
""" % (json.dumps(page.as_uri()), json.dumps(page.as_uri()), json.dumps(page.as_uri())))
    code, said, err = _runner(_node(), js, env=dict(os.environ), timeout=180)
    assert code == 0 and said.get("ok"), err
    g = said["got"]
    assert len(g["all"]) > 1 and g["one"] and set(g["one"]) == {g["rule"]} and len(g["one"]) < len(g["all"]), g
    assert g["again"] == g["all"], g
    assert g["hash"] == "#criteria" and g["strip"] and set(g["strip"]) == {g["outcome"]}, g
