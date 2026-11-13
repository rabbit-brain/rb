"""Closed form on rb's own pages (owner, Nov 13 2026; notes/CLOSED-FORM.md): every value and label on rb open, rb page
and the hosted page opens its source, and the actions are prompts for their agent, never a command to type.

Here, on the INT8 demo record: a question's reasons name their subjects, uncut (G3); a rule that holds carries no "not
decided" chip, only one rb status asks a call on does (MP-1113-0936 S1); and the page's labels and values sit in what
opens them."""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

import test_shell_contract as contract
from rbtest import command


@pytest.fixture(scope="module")
def doc(int8_demo, tmp_path_factory):
    return contract.model_document(Path(int8_demo.root), "live", tmp_path_factory.mktemp("cf-doc"))


@pytest.fixture(scope="module")
def page(int8_demo, tmp_path_factory):
    out = tmp_path_factory.mktemp("cf-page") / "page.html"
    got = subprocess.run([*command(), "page", "--out", str(out)], cwd=int8_demo.root, capture_output=True, encoding="utf-8",
                         errors="replace", stdin=subprocess.DEVNULL, env=int8_demo.env)
    assert got.returncode == 0, got.stderr
    return out.read_text(encoding="utf-8")


def test_a_question_reason_names_its_subjects_and_none_is_cut(doc):
    """G3: every reason of a top-level question's line names what it is about, a wait included (the design that waits,
    by its experiment; a call that waits, by its decision); the line says two, and `more` counts the rest."""
    pending = {p["id"] for p in doc["pending"]["rows"]}
    designs = {p["id"].split("@")[0] for p in doc["pending"]["rows"] if p["kind"] == "design"}
    seen_wait = False
    for card in doc["home"]["standing"]:
        for r in card["reasons"]:
            assert r["subjects"], r
            for s in r["subjects"]:
                if s["kind"] == "criterion":
                    assert s["id"] in doc["findings"], s
                elif s["kind"] == "design":
                    seen_wait = True
                    assert s["id"] in designs, s
                else:
                    assert s["kind"] == "decision" and s["id"] in pending, s
            if r["criteria"]:
                assert [s["id"] for s in r["subjects"]] == r["criteria"], r
        if card["state"] == "open" and card["reasons"]:
            assert card["more"] == max(0, len(card["reasons"]) - 2), card
    assert seen_wait, "the INT8 demo has designs that wait: a reason names them"


def test_a_rule_that_holds_carries_no_not_decided_chip(doc):
    """S1 (MP-1113-0936): only a rule rb status asks a call on (an undecided item, a gate missed) says it is not decided;
    a met rule nothing waits on never does."""
    asked = {i["subject"] for i in doc.get("open_items", []) if i.get("code") == "undecided"}
    for cid, f in doc["findings"].items():
        undecided = [c for c in f.get("chips", []) if c["kind"] == "undecided"]
        if f["state"] in ("meets", "reproduced", "gate met"):
            assert not undecided, (cid, f["chips"])
        if undecided and f["role"] != "gate":
            assert cid in asked or not asked, (cid, asked)


def _view(page: str, vid: str) -> str:
    i = page.index(f'id="{vid}"')
    return page[i:page.index("</section>", i)]


def test_the_question_view_opens_each_value_it_says(page, doc):
    """The INT8 question view: its state opens why it stands so, each reason opens its subject, the crumb names the
    question by its words (no id), and the interpretation's note opens its declaration."""
    q = doc["home"]["standing"][0]["question"]
    view = page[page.index(f'<article class="view" id="q-{q}"') if f'<article class="view" id="q-{q}"' in page else page.index(f'id="q-{q}"'):]
    view = view[:view.index("</article>")]
    assert re.search(rf'<p class="meta"><a href="#q-{re.escape(q)}:why" title="[^"]*">', view), view[:2000]
    assert f'id="q-{q}:why"' in view
    assert 'class="rsn"' in view, "each reason a link"
    crumb = view[view.index('<p class="crumb">'):view.index("</p>", view.index('<p class="crumb">'))]
    assert f">{q}<" not in crumb and 'class="cid"' not in crumb, crumb


def test_the_page_offers_prompts_not_commands_to_confirm(page):
    """msg 47: a design that waits says one plain line and offers a prompt for their agent; no box holds
    `rb confirm` for them to type, and no "list it to confirm" line is left."""
    assert "list it to confirm" not in page
    assert 'class="ibtn ico cpprompt rbcmd"' in page
    assert not re.search(r'<pre class="cmd">rb confirm ', page)


def test_the_bar_and_history_open_what_they_name(page):
    """The bar's title opens the Overview, its time and commit open where the page says how it was made; a history
    line's time opens its write in the raw log."""
    top = page[page.index('<header class="top"'):page.index("</header>")]
    assert '<a class="ttl" href="#overview">' in top and '<a class="stamp" href="#in-git"' in top, top
    assert re.search(r'<a class="ht" href="#log-\d+">', page)
    assert re.search(r'<li class="hev" data-go="log-\d+">', page)


def test_no_jargon_label_is_left_on_the_page(page):
    """OWNER-ASKS msgs 29, 30, 43, 55, 56: none of these labels is drawn."""
    shown = re.sub(r"<script.*?</script>|<style.*?</style>", "", page, flags=re.S)
    shown = re.sub(r"<[^>]+>", " ", shown).replace("&#x27;", "'")
    for word in ["Source details", "is provisional", "linked here:", "rule not met", "can't tell yet", "no interval"]:
        assert word not in shown, word
