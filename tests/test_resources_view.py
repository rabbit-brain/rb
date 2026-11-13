"""The Resources view and a resource's card (owner, Nov 13 2026: "human can understand???", "what is remote what is
present?"): a type icon and the name, the identity in its details, a "get it" chip, "N runs" and "N rules" as links to
their lists, no sentence under the name; the rules as chips under each question they bear on, the runs as a small
table, never a list joined with semicolons."""
import json
import re
import subprocess

from rbtest import command


def _page(rec, tmp_path):
    dump = tmp_path / "doc.json"
    env = {**rec.env, "RB_TEST_SHELL": "1", "RB_TEST_SHELL_DUMP": str(dump)}
    got = subprocess.run([*command(), "page", "--out", str(tmp_path / "p.html")], cwd=rec.root, env=env, capture_output=True, text=True)
    assert got.returncode == 0, got.stderr
    return (tmp_path / "p.html").read_text(encoding="utf-8"), json.loads(dump.read_text(encoding="utf-8"))


def _between(page, start, end):
    i = page.index(start)
    return page[i:page.index(end, i)]


def test_a_resource_reads_as_icon_name_and_chips_that_link(int8_demo, tmp_path):
    page, doc = _page(int8_demo, tmp_path)
    assert doc["resources"]
    for rid, r in doc["resources"].items():
        row = _between(page, f'<details class="nd resrow" data-row data-for="r-{rid}"', "</summary>")
        assert f'<i class="rt rt-' in row and r["name"].split(",")[0] in row, row
        runs, rules = len(r["read_by"]["runs"]), len(r["rests_on"]["criteria"])
        if runs:
            assert f'<a class="fch fch-use" href="#r-{rid}:readers">{runs} run{"s" if runs != 1 else ""}</a>' in row, row
        if rules:
            assert f'<a class="fch fch-use" href="#r-{rid}:criteria">{rules} rule{"s" if rules != 1 else ""}</a>' in row, row
        # no sentence: the counts are the chips
        assert "read by" not in row and "depend" not in row
        i = page.index(f'<div class="card res" id="r-{rid}"')
        card = page[i:page.index('<div class="card ', i + 10)]
        assert card, rid
        assert "runs read it" not in card and "run reads it" not in card and "; " not in re.sub(r"<[^>]+>", "", card)
        if runs:
            table = _between(card, f'<table class="rruns" id="r-{rid}:readers">', "</table>")
            assert table.count("<tr>") == runs + 1, table
        for q, cs in r["rests_on"]["by_question"].items():
            block = _between(card, f'<div class="rbq"><span class="rbqh"><a href="#q-{q}"', "</div>")
            assert [m for m in re.findall(r'href="#c-([^"]+)"', block)] == cs, block
        # the identity is in its source details
        assert not r["identity"] or r["identity"] in card
