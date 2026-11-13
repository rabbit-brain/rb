"""No glossary, show instead (the ruling of Nov 12 20:35 +08, UX-DIRECTION.md): rb page, rb export's page and rb open
draw no list of what their words or marks mean, no hover that defines a word, and no line under a heading that explains
its page. A rule written after its results is placed after them in time, and rb's text says a recorded interpretation
as the note the screens call it."""
import re

import pytest

GONE = [
    "What the words on this page mean", "data-hint", 'class="term"', 'class="gloss"', 'aria-describedby="g-', "figkey",
    "dot: estimate", "Which comparisons carry these qualifications?", "Each run keeps when its output was produced",
    "Each call keeps its reason and its date", "Calls are made with rb in a terminal", "How to read this record",
    "Directions are aligned to the same comparison order", "commands call it a criterion", "Measured differences; uncertainty remains",
    "Evidence includes point estimates only", "Evidence available for review",
]
WHEN = re.compile(r"^(results \d{1,2} [A-Z][a-z]{2} ──▶ rule \d{1,2} [A-Z][a-z]{2}|rule \d{1,2} [A-Z][a-z]{2} ──▶ results \d{1,2} [A-Z][a-z]{2}|written after its results|written before its results)$")
DAY = {m: i for i, m in enumerate(["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"])}


def in_order(said: str) -> bool:
    days = re.findall(r"(\d{1,2}) ([A-Z][a-z]{2})", said)
    return len(days) < 2 or (DAY[days[0][1]], int(days[0][0])) <= (DAY[days[1][1]], int(days[1][0]))


@pytest.mark.parametrize("which", ["int8_demo", "scale_record"])
def test_no_page_explains_its_words_and_late_rules_sit_after_their_results(which, request, tmp_path):
    rec = request.getfixturevalue(which)
    rec.rb("page", "--out", str(tmp_path / "page.html"))
    rec.rb("export", "--out", str(tmp_path / "x"))
    for page in (tmp_path / "page.html", tmp_path / "x" / "page.html"):
        html = page.read_text(encoding="utf-8")
        for said in GONE:
            assert said not in html, (page.name, said)
        assert '<p class="lede">' not in html.split('id="h-runs"', 1)[1][:200]
        # each check of "planned first" placed in time against its results, the earlier day first
        card = html.split('id="rest-planned"', 1)[1].split("</div>", 1)[0] if 'id="rest-planned"' in html else ""
        rows = [re.sub(r"<[^>]+>", "", li).split(" ", 2)[-1] for li in re.findall(r"<li>(.*?)</li>", card)]
        for row in rows:
            assert "‾" not in row and WHEN.match(row.split("</a> ")[-1].strip()) or WHEN.search(row), row
            assert in_order(row), row


def test_rb_status_says_a_recorded_interpretation_as_a_note(int8_demo):
    lines = [line for q in int8_demo.rb("status")["questions"] for line in q.get("inquiry", {}).get("summary", [])]
    assert not any("Recorded interpretation" in line or "Conditional interpretation" in line or "Interpretation active" in line for line in lines), lines
