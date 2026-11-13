"""The rulings of Nov 12 05:30 +08 (UX-DIRECTION.md) on the web's Overview chart: each row is drawn at the width it is
shown at (rows of HTML, never one picture scaled down), with its check's number on the axis under its panel (once where
its rows share it, else on each row) and 0 at the dashed line, the range at the row's end, the result with its unit,
the chip `claim` on a check of a claim, its state's square at the strip's size, and the key unfolded under the chart."""
import html
import re

import pytest


def overview_html(page: str) -> str:
    return page.split('id="overview"', 1)[1].split('id="research"', 1)[0]


def plain(fragment: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", fragment))


def edge_number(edge: str) -> str:
    for w in ("from ", "up to ", "above ", "below "):
        if edge.startswith(w):
            return edge[len(w):]
    return edge


@pytest.mark.parametrize("which", ["int8_demo", "scale_record"])
def test_each_chart_row_says_its_number_unit_range_and_kind(which, request, tmp_path):
    rec = request.getfixturevalue(which)
    rows = rec.rb("status")["overview"]["chart"][:10]
    rec.rb("page", "--out", str(tmp_path / "page.html"))
    ov = overview_html((tmp_path / "page.html").read_text(encoding="utf-8"))
    assert "<svg class=\"ovfig\"" not in ov                     # never one picture scaled to the screen
    fig = ov.split('<figure class="ovchart">', 1)[1].split("</figure>", 1)[0]
    drawn = re.findall(r'<a class="ovr[^"]*" href="#([^"]+)">(.*?)</a>(?=<a class="ovr|</div>)', fig, flags=re.S)
    assert len(drawn) == len(rows), (len(drawn), len(rows))
    panels = re.findall(r'<div class="ovp"><p class="gh">(.*?)</p>(.*?)</div>(?=<div class="ovp"|<p class="more"|$)', fig, flags=re.S)
    by_group: dict = {}
    for r in rows:
        by_group.setdefault(r["group"], []).append(r)
    # closed form (owner, Nov 13 2026): a panel's head is a link to the rules it shows, so its words are read through it
    assert [plain(g) for g, _ in panels] == list(by_group)
    for (href, inner), r in zip(drawn, rows):
        assert href.endswith(r["id"]), (href, r["id"])
        end = plain(re.search(r'<span class="oe">(.*?)</span>(?:<span class="og">|$)', inner, flags=re.S).group(1))
        assert end.endswith(r["result_text"]), (end, r)
        if r["range_text"]:
            assert f'<span class="og">{html.escape(r["range_text"])}</span>' in inner
        assert ('<span class="chip k kl">claim</span>' in inner) == r["claim"]
    for g, body in panels:
        members = by_group[plain(g)]
        labels = [" ".join(plain(s) for s in re.findall(r"<span [^>]*>([^<]*)</span>", x)) for x in re.findall(r'<span class="oax">((?:<span [^>]*>[^<]*</span>)*)</span>', body)]
        nums = [edge_number(m["edge_text"]) for m in members if m["edge_text"]]
        shared = len({m["edge_text"] for m in members}) == 1
        joined = " ".join(labels)
        if nums and shared:
            assert joined.count(nums[0]) == 1, (labels, nums)    # once a panel
        for n in set(nums):
            assert n in joined, (n, labels)
        if any(m["difference"] for m in members) and not any(edge_number(m["edge_text"]) == "0" for m in members):
            assert re.search(r"(^| )0( |$)", joined) or not labels, labels
    key = ov.split('<figure class="ovchart">', 1)[1].split("</figure>", 1)[0]
    # no key under the chart (no glossary, show instead, Nov 12 20:35 +08): each row says its result and range in words
    assert "figkey" not in key and "<details" not in key


def test_the_marks_are_drawn_at_the_strips_size_and_the_chart_at_a_phones(tmp_path):
    """The styles say it: a row's square at the strip's 20px, the chart's text at the body's smallest size and never
    in a scaled picture, and at 390 the range on the label's line."""
    from pathlib import Path
    css = (Path(__file__).parent.parent / "rust" / "src" / "shell" / "assets" / "shell.css").read_text(encoding="utf-8")
    assert re.search(r"\.ovstrip\.big a\{font-size:20px\}", css)
    assert re.search(r"\.oe b\{font:20px var\(--mono\)", css)
    for sel in (r"\.ol\{grid-area:l;font-size:14px\}", r"\.og\{grid-area:r;white-space:nowrap;font-size:12px"):
        assert re.search(sel, css), sel
    phone = re.search(r"@media \(max-width:639px\)\{\.ovr,\.ovr\.sh\{[^}]*grid-template-areas:\"l r\" \"t e\"", css)
    assert phone, "at a phone's width the range sits on the label's line"


@pytest.mark.parametrize("which", ["int8_demo", "scale_record"])
def test_your_call_is_the_decisions_list_with_one_badge(which, request, tmp_path):
    """The rulings of Nov 12 07:15 +08: the pin and the Decisions heading carry one badge, len(overview.decisions);
    Your call opens that list, every row with its kind chip and its note, a confirmation with its command."""
    rec = request.getfixturevalue(which)
    rows = rec.rb("status")["overview"]["decisions"]
    rec.rb("page", "--out", str(tmp_path / "page.html"))
    page = (tmp_path / "page.html").read_text(encoding="utf-8")
    view = page.split('id="your-call"', 1)[1].split("</section>", 1)[0]
    # closed form (owner, Nov 13 2026): the heading's count is a link to the list it counts
    assert re.search(r'<h1 id="h-your-call">[^<(]*<a class="n" href="#yc-list">\(' + str(len(rows)) + r'\)</a></h1>', view), view[:300]
    assert 'id="yc-list"' in view, "the count opens a list the page holds"
    # closed form (owner, Nov 13 2026): the list carries the id its counts link to, and a confirmation row
    # offers a prompt for their agent (class cpprompt), owner's view only, in place of a command
    lst = re.search(r'<ul class="dq yl" id="yc-list">(.*?)</ul>', view, flags=re.S)
    assert lst and lst.group(1).count('<li class="dr">') == len(rows)
    for li in lst.group(1).split('<li class="dr">')[1:]:
        if ">confirm</span>" in li:
            assert 'cpprompt' in li and '<div class="dcw" data-vw="none owner">' in li, li[:300]
            assert "rb confirm" not in li, li[:300]
        href = re.search(r'<a href="#([^"]+)"', li).group(1)
        assert f'id="{href}"' in page, href
    for r in rows:
        assert f'<span class="dn">{html.escape(r["said"], quote=True)}</span>' in lst.group(1) or not r["said"], r["said"]
    pin = re.search(r'<li class="yc"><a href="#your-call">[^<]*<span class="n">(\d+)</span>', page)
    assert pin and int(pin.group(1)) == len(rows)
