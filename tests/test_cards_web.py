"""The web's cards (UX5 2.1, round 2): the inspector draws C's card model, a check, run, setting, commit, file,
decision or experiment, as its fields in their order, never a paragraph. A page holds the field card of each thing its
Overview and its cards open, within its size (fields.rs BUDGET); rb open serves each when it is opened. Each card's
fields are the JSON's `card` (`rb show <id> --json`), label for label and in the same order, with the user's words left
off a page; every link a card holds names an element the page holds."""
import html
import json
import os
import re
import urllib.request

import pytest

from rbtest import Open

CARD = re.compile(r'<div class="card fc" id="([^"]+)" data-kind="(\w+)" data-crumb="([^"]*)">(.*?)</dl></div>', re.S)


def drawn(page: str) -> dict:
    out = {}
    for anchor, kind, crumb, body in CARD.findall(page):
        head = re.search(r'<h2 class="tt fh"><span class="fl">([^<]*)</span> <span class="mono">([^<]*)</span>', body)
        # closed form (owner, Nov 13 2026): a label may be a link (OPEN opens the long form), so its tags are read through
        labels = [html.unescape(re.sub(r"<[^>]+>", "", x)) for x in re.findall(r"<dt[^>]*>(.*?)</dt>", body, flags=re.S)]
        out[html.unescape(crumb)] = {"anchor": anchor, "kind": kind, "head": (html.unescape(head.group(1)), html.unescape(head.group(2))), "labels": labels,
                                     "links": [html.unescape(h) for h in re.findall(r'href="#([^"]+)"', body)]}
    return out


def want_labels(card: dict, page: bool) -> list:
    out = []
    for f in card["fields"][1:]:
        # a page leaves the user's words off, and names no viewer's checkout
        if not f["label"] or (page and f["key"] in ("words", "your_checkout")):
            continue
        if f["key"] == "open" and not f.get("opens"):
            continue
        out.append(f["label"])
    return out


@pytest.mark.parametrize("which", ["int8_demo", "scale_record"])
def test_each_card_on_a_page_is_its_jsons_fields_in_order(which, request, tmp_path):
    rec = request.getfixturevalue(which)
    rec.rb("page", "--out", str(tmp_path / "page.html"))
    page = (tmp_path / "page.html").read_text(encoding="utf-8")
    cards = drawn(page)
    kinds = {c["kind"] for c in cards.values()}
    assert {"check", "experiment", "decision"} <= kinds, kinds
    # a page holds its field cards within its size (fields.rs BUDGET): runs come last, so the scale record's page may
    # hold none, and each run's cite then opens the card the page drew for it before
    assert ("run" in kinds) or which == "scale_record", kinds
    assert "setting" in kinds or "commit" in kinds, kinds
    ids = set(re.findall(r' id="([^"]+)"', page))
    seen = 0
    for subject, c in sorted(cards.items(), key=lambda kv: (kv[1]["kind"], kv[0]))[:60]:
        got = rec.rb("show", subject)["card"]
        assert c["kind"] == got["kind"], (subject, c["kind"], got["kind"])
        assert c["head"] == (got["fields"][0]["label"], got["id"]), (subject, c["head"])
        assert c["labels"] == want_labels(got, page=True), (subject, c["labels"], want_labels(got, True))
        # every link names an element the page holds, or Files (a folder)
        for to in c["links"]:
            assert to in ids or to == "files", (subject, to)
        seen += 1
    assert seen
    # the Overview opens field cards: each strip square and chart row
    ov = page.split('id="overview"', 1)[1].split('id="research"', 1)[0]
    squares = re.findall(r'<div class="ovstrip big".*?</div>', ov, flags=re.S)[0]
    # each square opens its check's card; each count, Rules filtered to its state (rec-2: counts click)
    squares_to = re.findall(r'<a class="s-[^"]*" href="#([^"]+)"', squares)
    assert squares_to and all(h.startswith("k-") for h in squares_to)
    assert set(re.findall(r'<a class="ct" href="#([^"]+)"', squares)) == {"criteria"}
    assert all(h.startswith("k-") for h in re.findall(r'<a class="ovr[^"]*" href="#([^"]+)"', ov))
    assert all(h.startswith("rest-") for h in re.findall(r'<a class="mk[^"]*" href="#([^"]+)"', ov))


def test_a_page_leaves_the_users_words_off_its_cards(int8_demo, tmp_path):
    int8_demo.rb("page", "--out", str(tmp_path / "page.html"))
    page = (tmp_path / "page.html").read_text(encoding="utf-8")
    for _, _, _, body in CARD.findall(page):
        assert "WORDS" not in re.findall(r"<dt[^>]*>([^<]*)</dt>", body)


def test_rb_open_serves_each_card_when_it_is_opened(int8_demo):
    old, saved = os.getcwd(), dict(os.environ)
    os.environ.update(int8_demo.env)
    try:
        srv = Open(int8_demo.root)
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            home = opener.open(srv.url + "/", timeout=120).read().decode("utf-8")
            # rb open's page holds no field card, and its Overview links to them
            assert '<div class="card fc"' not in home and 'href="#k-' in home
            st = int8_demo.rb("status")
            subjects = [st["findings"][0]["criterion"], st["experiments"][0]["id"], st["runs"][0]["id"]]
            for s in subjects:
                got = int8_demo.rb("show", s)["card"]
                anchor = "k-" + s
                card = opener.open(srv.url + "/o/" + urllib.request.quote(anchor) + ".html", timeout=120).read().decode("utf-8")
                c = drawn(card)[s]
                assert c["anchor"] == anchor and c["labels"] == want_labels(got, page=False), (s, c["labels"])
            with pytest.raises(urllib.error.HTTPError):
                opener.open(srv.url + "/o/k-nothing-here.html", timeout=60)
        finally:
            srv.close()
    finally:
        os.environ.clear()
        os.environ.update(saved)
        os.chdir(old)
