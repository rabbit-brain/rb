"""rb page is the record in one file: every criterion's standing, every catalogue row and every call on the page
match what rb status computes, with no writer and none of the user's words, since a page is sent to anyone, or this
fails."""
import html as html_mod
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from rabbit_brain.models import Envelope
from rbtest import as_the_user, main, state

HAS_GIT = shutil.which("git") is not None
pytestmark = pytest.mark.skipif(not HAS_GIT, reason="the research state is played in a git repository")


@pytest.fixture
def repo(workdir, monkeypatch, tmp_path_factory, capsys):
    as_the_user(monkeypatch)
    cfg = tmp_path_factory.mktemp("gitconfig") / "config"
    cfg.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(cfg))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    subprocess.run(["git", "init", "-q"], check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], check=True)
    subprocess.run(["git", "config", "user.name", "t"], check=True)

    def rb(*argv):
        code = main([*argv, "--json"])
        return code, Envelope.model_validate_json(capsys.readouterr().out)

    assert rb("init", "the page project")[0] == 0
    monkeypatch.setenv("CLAUDECODE", "1")
    assert rb("question", "add", "does the page tell the truth", "--id", "q1")[0] == 0
    assert rb("experiment", "add", "the experiment", "--id", "e1", "--hypothesis", "it cannot lie by construction", "-q", "q1")[0] == 0
    assert rb("criterion", "add", "top1 holds", "-e", "e1", "--metric", "top1", "--at-least", "0.7", "--id", "c1", "-q", "q1")[0] == 0
    assert rb("criterion", "add", "loss stays tiny", "-e", "e1", "--metric", "loss", "--at-most", "0.1", "--id", "c2", "-q", "q1")[0] == 0
    assert rb("run", "add", "e1", "top1=0.76", "loss=0.4")[0] == 0
    monkeypatch.delenv("CLAUDECODE")
    return rb


def _plain(text: str) -> str:
    return html_mod.unescape(re.sub(r"<[^>]+>", "", text))


# rb's rule phrases as the screens say them (strings.rs plain_word, plain words passes 1 and 2)
_PLAIN = {"criterion met": "rule met", "criterion not met": "rule not met", "criterion undetermined": "can't tell yet",
          "criterion untested": "not tested", "untested": "not tested", "estimate meets criterion": "estimate meets the rule",
          "recorded values meet criterion": "recorded values meet the rule", "point only": "no interval",
          "gate undetermined": "gate, can't tell yet", "gate untested": "gate not tested"}


def _plain_words(headline: str) -> str:
    return " · ".join(_PLAIN.get(p, p) for p in headline.split(" · "))


def _said(text: str) -> str:
    """What the page says of the record: its text without the script and the style, rb's glossary, and the words rb
    offers for a call not yet made ("Its review will read: accepted ‹day›: ‹why›"), which name no call."""
    text = re.sub(r"<script>.*?</script>|<style>.*?</style>", "", text, flags=re.S)
    text = re.sub(r'<details class="fold words".*?</details>', "", text, flags=re.S)
    text = re.sub(r'<span class="cons">[^<]*</span>|<p class="willread">.*?</p>', "", text, flags=re.S)
    return _plain(text)


def _verdict_agrees(text: str, f: dict) -> int:
    """Guarantee 29 on a finding card: its verdict leads with the rule's state word, said once under its title (FIX2),
    and says the result axis (FIX3): an interval where rb has one, "(no interval yet)" where it has none."""
    lead = f["says"]["headline"].split(" · ")[0]
    # every lead rb gives, by name (FIX3): a lead this test does not know fails, never reads as "Met"
    words = {"criterion met": "Met", "estimate meets criterion": "Met", "recorded values meet criterion": "Met",
             "gate met": "Met", "reproduced": "Reproduced", "not reproduced": "Not reproduced",
             "criterion not met": "Not met", "gate missed": "Not met",
             "criterion undetermined": "Can't tell yet", "gate undetermined": "Can't tell yet",
             "criterion untested": "Not tested", "gate untested": "Not tested"}
    assert lead in words, f"a lead the page's pin does not know: {lead!r}"
    word = words[lead]
    card = _plain(text.split(f'id="c-{f["criterion"]}"', 1)[1].split('<p class="str">', 1)[1].split("</p>", 1)[0])
    assert card.startswith(word), (word, card)
    # and the result axis through the verdict: an interval where rb has one, "(no interval yet)" where it has none
    result = f["result"]
    interval = re.search(r"\([+-]?[\d.,]+\S* to [+-]?[\d.,]+\S*\)", card)
    if result == "point_only":
        assert "(no interval yet)" in card or "(no interval across design cells)" in card, (result, card)
        assert not interval, (result, card)
    elif result == "untested":
        assert not interval and "(no interval" not in card, (result, card)
    else:
        assert result in ("gain", "loss", "unresolved", "equivalent", "measured", "higher", "lower"), f"a result the pin does not know: {result!r}"
        assert interval and "(no interval" not in card, (result, card)
        # the numbers too (FIX4): each bound is rb's, in the digits the card gives it, and so is the estimate it leads with
        c = f["comparison"]
        lo, hi = re.match(r"\(([+-]?[\d.,]+)\S* to ([+-]?[\d.,]+)\S*\)", interval.group(0)).groups()
        for shown, rbs in ((lo, c["lo"]), (hi, c["hi"])):
            assert _within_its_digits(shown, rbs), (shown, rbs, card)
        # the first number the card says, a word's digits aside ("Top-1"): "Top-1: 0.78", "INT8 is 0.50 points higher"
        estimate = re.search(r"(?<![\w.-])\d[\d,]*(?:\.\d+)?", card)
        assert estimate and _within_its_digits(estimate.group(0), abs(c["estimate"])), (c["estimate"], card)
        # and a difference's direction, the word after its size (review should 3): "0.28 higher" on +0.28, never "lower"
        if c.get("reference"):
            way = re.search(r"\b(higher|lower)\b", card[estimate.end():])
            assert way and way.group(1) == ("higher" if c["estimate"] > 0 else "lower"), (c["estimate"], card)
            # and the grid's cell that opens this rule says the card's own digits (FIX5): never rb compare's 0.1567
            # beside a card's 0.16
            cells = [_plain(m) for m in re.findall(r'<span class="gd"><a [^>]*href="#cr?-' + re.escape(f["criterion"]) + r'"[^>]*>(.*?)</a>', text, flags=re.S)]
            for cell in cells:
                size = re.search(r"(?<![\w.-])\d[\d,]*(?:\.\d+)?", cell)
                assert size and size.group(0) == estimate.group(0), (estimate.group(0), cell, card)
                bounds = re.search(r"\(([+-]?[\d.,]+) to ([+-]?[\d.,]+)\)", cell)
                assert not bounds or bounds.groups() == (lo, hi), ((lo, hi), cell, card)
            return len(cells)
    return 0


def _within_its_digits(shown: str, value: float) -> bool:
    """`shown` is `value` rounded to the digits it shows (half a unit of its last digit either way)."""
    shown = shown.replace(",", "")
    places = len(shown.split(".", 1)[1]) if "." in shown else 0
    return abs(float(shown) - value) <= 0.5 * 10 ** -places + 1e-9


def test_the_page_agrees_with_the_computed_state(repo, capsys, monkeypatch):
    monkeypatch.setenv("CLAUDECODE", "1")
    code, env = repo("decide", "e1", "accept", "-m", "the run holds")
    monkeypatch.delenv("CLAUDECODE")
    proposed = env.data["object"]
    assert code == 0 and proposed["by"] == "agent:claude-code"             # an agent's call lands, as the agent's, proposed
    # a call that waits for the user's confirmation is on the page apart, with the one command: never one of the calls
    code, env = repo("page")
    plain = _plain(Path(env.data["path"]).read_text(encoding="utf-8"))
    assert code == 0 and "accepted" not in _said(Path(env.data["path"]).read_text(encoding="utf-8"))
    assert f"Proposed, not made (1)accept e1 · note: the run holdsconfirm in your own terminal:rb confirm {proposed['id']}" in plain
    assert repo("confirm", proposed["id"])[0] == 0
    code, env = repo("page")
    assert code == 0
    text = Path(env.data["path"]).read_text(encoding="utf-8")
    plain = _plain(text)
    st = state().status()
    for f in st["findings"]:
        assert f["criterion"] in plain and f["statement"] in plain
        assert f["rule"] in plain
        # the page shows the computed standing: a finding card's verdict leads with the rule's state word, said once
        # under its title (FIX2): "Not met: ...", "Can't tell yet: ..."
        _verdict_agrees(text, f)
    assert {f["result"] for f in st["findings"]} == {"point_only"}, "this record's one run gives no interval"
    for e in st["experiments"]:
        assert e["id"] in plain and e["title"] in plain
    assert "does the page tell the truth" in plain and "it cannot lie by construction" in plain
    # the call, once on its question's view, with no writer, its reason the session's note
    question = _plain(text.split('id="q-q1"', 1)[1].split('<article', 1)[0])
    assert re.search(r"accepted \d{4}-\d{2}-\d{2} · note: the run holds e1", question), question
    # c2 misses its rule and nobody has decided on it: the call rb status lists for the user waits on you
    assert [i["code"] for i in st["open"] if i["who"] == "human"] == ["undecided"]
    c2 = next(f for f in st["findings"] if f["criterion"] == "c2")
    # the call itself, once, by what it asks, with the command of each call rb offers on it
    assert "Your call (1)" in plain and plain.count(f"Review this result") >= 1
    row = text.split('data-fold="call-c2:row"', 1)[1].split("</details>", 1)[0]
    assert '<p class="lab">Review this result</p>' in row and c2["statement"] in _plain(text.split('id="queue"', 1)[1].split("</section>", 1)[0])
    assert "Record a call" in plain
    for action in ("accept", "reject", "investigate"):
        assert f'<pre class="cmd">{html_mod.escape(f"rb decide c2 {action} --why {chr(39)}‹why›{chr(39)}", quote=True)}</pre>' in row, action
    assert "rb approve" not in plain
    assert "rb status is right" in plain
    assert "\u2014" not in text                     # no em dashes, anywhere
    # Guarantee 29, as test_export.py SMALL_PAGE. Raised from 210,000 to 212,000 on the orchestrator's call of Oct 27,
    # 20:00 +08 (FIX8): the Rerun panel's commands and A's script. The last raise before launch: what adds to the
    # small page from here cuts as much as it adds.
    assert env.data["bytes"] < 212_000
    # The glosses exist once each.
    assert plain.count("each rule is read on axes") == 1
    assert plain.count("Each run keeps when its output was produced and when it was recorded") == 1
    assert 'id="x-e1"' in text and 'href="#x-e1"' in text  # the map links to the card, inertly
    assert "Experiment · accepted · 1 run" in plain
    # the call made, rb status lists no call for the user and the page has nothing waiting on you
    assert repo("decide", "c2", "reject", "-m", "0.1 was never the bar")[0] == 0
    assert [i for i in state().status()["open"] if i["who"] == "human"] == []
    code, env = repo("page")
    text = Path(env.data["path"]).read_text(encoding="utf-8")
    # Your call says it holds none, wherever the page names it, and no call is listed anywhere
    assert code == 0 and set(re.findall(r"Your call \((\d+)\)", _plain(text))) == {"0"} and 'class="callrow"' not in text and 'id="call-' not in text
    # with three runs rb gives an interval, and each verdict says it (FIX3)
    for top1 in ("0.78", "0.8"):
        assert repo("run", "add", "e1", f"top1={top1}", "loss=0.4")[0] == 0
    code, env = repo("page")
    text = Path(env.data["path"]).read_text(encoding="utf-8")
    st = state().status()
    assert {f["result"] for f in st["findings"]} == {"measured"}
    for f in st["findings"]:
        _verdict_agrees(text, f)
    # and a comparison of two arms, through each result an interval gives it: a gain, a loss and one unresolved (FIX4)
    assert repo("experiment", "add", "INT8 against FP32", "--id", "e2", "--hypothesis", "INT8 holds", "-q", "q1",
                "--baseline", "fp32", "--candidate", "int8", "--varies", "precision")[0] == 0
    for metric, way in (("top1", "--maximize"), ("loss", "--minimize"), ("latency_ms", "--minimize")):
        assert repo("metric", "add", metric, way)[0] == 0
    for cid, metric in (("cg", "top1"), ("cl", "loss"), ("cu", "latency_ms")):
        assert repo("criterion", "add", f"INT8 holds on {metric}", "-e", "e2", "--metric", f"change.{metric}", "--at-most", "5",
                    "--id", cid, "-q", "q1")[0] == 0
    for fp32, int8 in (((75.69, 1.01, 7.1), (75.94, 1.21, 7.2)), ((75.71, 1.0, 7.1), (76.02, 1.26, 6.8)), ((75.7, 1.02, 7.0), (75.98, 1.24, 7.4))):
        assert repo("run", "add", "e2", *(f"{arm}.{m}={v}" for arm, vals in (("fp32", fp32), ("int8", int8)) for m, v in zip(("top1", "loss", "latency_ms"), vals)))[0] == 0
    code, env = repo("page")
    text = Path(env.data["path"]).read_text(encoding="utf-8")
    two = {f["criterion"]: f for f in state().status()["findings"] if f["criterion"] in ("cg", "cl", "cu")}
    assert {c: f["result"] for c, f in two.items()} == {"cg": "gain", "cl": "loss", "cu": "unresolved"}
    assert sum(_verdict_agrees(text, f) for f in two.values()) >= 3, "the pin reads each rule's grid cell too (FIX5)"


def test_a_rule_met_on_evidence_with_a_problem_is_never_green(repo):
    """Green is a rule met with nothing wrong with what it rests on, as the page's findings and rb open draw it. When the
    source of a setting e2 reads comes to state another value, c3 still meets its rule, and the page draws it dim
    wherever it draws it: its finding, its pass bar in e2's record, and its count on the catalogue's rows of e2, of its
    two explicitly linked questions (q1a and q1). What is sound beside it stays good, e1's rules met and the gate q1 counts
    beside c3, each across the interval of three runs, and the rule missed stays bad."""
    Path("configs").mkdir()
    Path("configs/train.yaml").write_text("optim:\n  lr: 0.001\n", encoding="utf-8")
    assert repo("criterion", "add", "top1 clears the bar", "-e", "e1", "--metric", "top1", "--at-least", "0.6", "--role", "gate",
                "--id", "g1", "-q", "q1")[0] == 0
    assert repo("question", "add", "does it hold up", "--parent", "q1", "--id", "q1a")[0] == 0
    assert repo("experiment", "add", "the follow-up", "--id", "e2", "--hypothesis", "it holds up", "-q", "q1a")[0] == 0
    assert repo("spec", "set", "e2", "optim.lr", "--source", "configs/train.yaml#optim.lr")[0] == 0
    assert repo("criterion", "add", "top1 holds again", "-e", "e2", "--metric", "top1", "--at-least", "0.7", "--id", "c3", "-q", "q1a", "-q", "q1")[0] == 0
    for top1 in ("0.78", "0.8"):
        assert repo("run", "add", "e1", f"top1={top1}", "loss=0.4")[0] == 0
    for top1 in ("0.8", "0.82", "0.84"):
        assert repo("run", "add", "e2", f"top1={top1}")[0] == 0
    words = {"c1": "rule met", "g1": "gate met", "c3": "rule met"}
    # the finding cards (C's nodes.rs) say their verdict once (FIX2); a card is green ("crit is-good") only when sound

    def drawn() -> dict:
        """The class each met rule is drawn in, in its finding card and its experiment's list of criteria, and each count
        on its questions' rollups and its experiments' rows: "good" is green, any other word is not ("" is no colour,
        "mute" the dim of a list)."""
        code, env = repo("page")
        assert code == 0
        text = Path(env.data["path"]).read_text(encoding="utf-8")
        # A criterion linked to both parent and child has one canonical HTML anchor (the script's own markup aside).
        anchors = re.findall(r'\bid="([^\"]+)"', re.sub(r"<script>.*?</script>", "", text, flags=re.S))
        assert len(anchors) == len(set(anchors))

        def counts(html: str) -> dict:
            out = {}
            for part in html.split("<span> · </span>"):
                said = _plain(part).removeprefix("linked here: ").removeprefix("Rules: ").split(";")[0]
                if re.match(r"\d+ ", said):
                    m = re.search(r'class="sw ?(\w*)"', part)
                    out[said] = m.group(1) if m else ""
            return out

        lists = "".join(text.split(f'id="x-{e}:criteria"', 1)[1].split("</section>", 1)[0] for e in ("e1", "e2"))
        # the question's line is its card's, led by what it counts (plain words pass 3)
        rolls = {q: text.split(f'id="q-{q}"', 1)[1].split('<p class="roll stt"><span class="k">Rules: </span>', 1)[1].split("</p>", 1)[0] for q in ("q1", "q1a")}
        rows = {e: text.split(f'<div class="srow" data-go="x-{e}">', 1)[1].split('<span class="tl">', 1)[1].split("</span></div>", 1)[0] for e in ("e1", "e2")}
        return {
            "findings": {c: {"is-good": "good", "is-bad": "bad"}.get(re.search(rf'<div class="card crit ?([\w-]*)" id="c-{c}"', text).group(1), "")
                         for c in words},
            # an experiment's rules are drawn as Rules draws them (design pass 1, target 3)
            "bars": {c: re.search(rf'<div class="xrule" data-go="c-{c}">.*?<span class="sw ?(\w*)">{w}</span>', lists, re.S).group(1)
                     for c, w in words.items()},
            "does the page tell the truth": counts(rolls["q1"]), "does it hold up": counts(rolls["q1a"]),
            "the experiment": counts(rows["e1"]), "the follow-up": counts(rows["e2"]),
        }

    # a gate met is neutral (D4): no colour on its finding and in a count, the list's dim on its bar, never good
    # (a question's line is its card's: "Rules: 2 met", the gate's count the list's dim, plain words pass 3)
    good = {"findings": {**dict.fromkeys(words, "good"), "g1": ""}, "bars": {**dict.fromkeys(words, "good"), "g1": "mute"},
            "does the page tell the truth": {"2 met": "good", "1 not met": "bad", "1 gate met": "mute"},
            "the experiment": {"1 rule met": "good", "1 rule not met": "bad", "1 gate met": ""},
            "does it hold up": {"1 met": "good"}, "the follow-up": {"1 rule met": "good"}}
    assert drawn() == good
    Path("configs/train.yaml").write_text("optim:\n  lr: 0.002\n", encoding="utf-8")
    assert drawn() == {**good, "findings": {**good["findings"], "c3": ""}, "bars": {**good["bars"], "c3": "mute"},
                       "does the page tell the truth": {"2 met": "mute", "1 not met": "bad", "1 gate met": "mute"},
                       "does it hold up": {"1 met": "mute"}, "the follow-up": {"1 rule met": ""}}


def test_the_page_leaves_the_users_words_out(repo, capsys, monkeypatch):
    """When an agent acts on the user's own words and the user confirms the call, rb keeps them, and rb open and rb ui
    quote them after the reason. A page is sent to anyone, so it says the call and its reason, wherever it says the call
    (the decision under its question, the experiment record, a criterion's declaration), and none of the user's words
    or of the words an agent gave."""
    monkeypatch.setenv("CLAUDECODE", "1")
    assert repo("criterion", "add", "score holds", "-e", "e1", "--metric", "score", "--at-least", "0.9", "--id", "c3",
                "--words", "keep score >= 0.9 & say so if it slips")[0] == 0
    assert repo("run", "add", "e1", "top1=0.78", "loss=0.3", "score=0.93")[0] == 0
    code, env = repo("decide", "e1", "accept", "-m", "two runs above the bar", "--words", "if top1 holds on the second run, accept e1 for me")
    monkeypatch.delenv("CLAUDECODE")
    assert code == 0 and env.data["object"]["by"] == "agent:claude-code"
    code, env = repo("confirm", env.data["object"]["id"])                   # the user makes the call, on those words
    decided = env.data["confirmed"][0]["object"]
    code, env = repo("page")
    assert code == 0
    text = Path(env.data["path"]).read_text(encoding="utf-8")
    plain = _plain(text)
    # the call, with its reason the session's note, on its question's view and on its experiment's
    question = _plain(text.split('id="q-q1"', 1)[1].split("<article", 1)[0])
    # the call's line first, what it is on trailing (design pass 1, P3)
    assert f"accepted {decided['at'][:10]} · note: two runs above the bar e1" in question
    record = text.split('id="x-e1"', 1)[1].split("</article>", 1)[0]
    assert f"<dt>decision</dt><dd><span>accepted {decided['at'][:10]} · note: </span><span class=\"au\">two runs above the bar</span>" in record
    assert "your words" not in plain and "accept e1 for me" not in plain and "say so if it slips" not in text
    assert text.count("<script>") == 1  # only the fixed navigation script, never authored markup
    assert "under grant" not in plain
    assert decided["words"] == "if top1 holds on the second run, accept e1 for me"   # the record keeps them


def test_a_level_says_what_it_is_when_pointed_at(repo, capsys):
    """top1 read three times is a level with its interval: its standing says measured, and what that is on hover."""
    for top1 in ("0.78", "0.8"):
        assert repo("run", "add", "e1", f"top1={top1}", "loss=0.4")[0] == 0
    code, env = repo("page")
    assert code == 0
    text = Path(env.data["path"]).read_text(encoding="utf-8")
    assert re.search(r'<span class="term" data-hint="one arm on its own, with its interval, so neither better nor worse"[^>]*>measured</span>', text)
    stand = text.split('id="c-c1"', 1)[1].split('<p class="str">', 1)[1].split("</p>", 1)[0]
    assert stand.count(">measured<") == 0          # what the reading is is never its state (plain words pass 4)


def test_a_rule_met_only_on_its_estimate_is_never_drawn_green(repo, capsys):
    """top1 of 0.76 alone meets at least 0.7 on its estimate, with no interval to read, as loss of 0.4 meets at most 1:
    the page draws both dim, as it draws a rule with a problem under it, and a count of met rules is green only when
    every rule it counts is. With 0.62 and 0.95 beside it, top1's interval crosses 0.7 and it stays dim; loss always 0.4
    meets at most 1 across its whole interval, and turns green."""
    assert repo("criterion", "add", "loss stays under 1", "-e", "e1", "--metric", "loss", "--at-most", "1", "--id", "c3", "-q", "q1")[0] == 0

    def page() -> str:
        code, env = repo("page")
        assert code == 0
        return Path(env.data["path"]).read_text(encoding="utf-8")

    def finding(text: str, cid: str) -> str:
        return text.split(f'id="c-{cid}"', 1)[1].split('<div class="card', 1)[0]

    # the finding cards (C's nodes.rs) say their verdict once (FIX2): a rule met on its estimate alone says its interval
    # does not show it yet, and its card is never green; a rule met across its interval is
    def met(cid: str) -> bool:
        card = finding_card(cid)
        return card.startswith('<div class="card crit is-good"') and "<p class=\"str\">Met: " in card and "does not show it yet" not in card

    def dim(cid: str) -> bool:
        card = finding_card(cid)
        return not card.startswith('<div class="card crit is-good"') and "<p class=\"str\">Met: " in card and "its interval does not show it yet" in card

    def tallied(text: str) -> None:
        # the count of met rules is green only when every rule it counts is: here c1 is met on its estimate alone, so
        # the tally (on Research and All criteria), the question's linked-here line (rb's tallies, contract 1.6), e1's
        # row and the Overview's standing card (short) say it without green
        # (the question's line links its count where a readings group holds it: a link, never green)
        # (each tally led by what it counts: "Rules: 2 met", plain words pass 3)
        said = re.findall(r'<(?:span|a)((?: [\w-]+="[^"]*")*)>2 (?:rules )?met</', text)
        assert len(said) >= 4 and not [a for a in said if "good" in a], said
        assert re.search(r'class="sw mute"[^>]*>2 met<', text) and not re.search(r'class="sw good"[^>]*>2 met<', text)
        assert '<span class="tl"><span class="k">Rules: </span><span>2 rules met</span>' in text.split('<div class="srow" data-go="x-e1">', 1)[1].split("</div></div>", 1)[0]

    text = page()
    finding_card = lambda cid: text[text[:text.index(f'id="c-{cid}"')].rindex("<div"):].split('<div class="card', 2)[1].join(['<div class="card', ""])
    assert dim("c1") and dim("c3")
    assert not re.search(r'class="sw good" href="#c-\w+:rule">(?:criterion|rule) met<', text)
    tallied(text)
    for top1 in ("0.62", "0.95"):
        assert repo("run", "add", "e1", f"top1={top1}", "loss=0.4")[0] == 0
    text = page()
    assert dim("c1") and met("c3")
    tallied(text)
    # the experiment's criteria, each with its rule and its word: green for c3 alone
    # (drawn as Rules draws a rule, design pass 1, target 3: its state on the right, its rule as declared in Details)
    section = text.split('id="x-e1:criteria"', 1)[1].split("</section>", 1)[0]
    bars = re.findall(r'<div class="xrule" data-go="c-(\w+)">.*?<span class="sw ?(\w*)">', section, re.S)
    assert bars == [("c1", "mute"), ("c2", "bad"), ("c3", "good")], bars
    assert all(rule in _plain(text) for rule in ("top1 ≥ 0.7", "loss ≤ 0.1", "loss ≤ 1"))


def test_the_page_prints_and_travels_to_phones(repo, capsys):
    code, env = repo("page")
    assert code == 0
    text = Path(env.data["path"]).read_text(encoding="utf-8")
    assert "@media print" in text and "break-inside:avoid" in text
    # the same views work on a phone: one at a time in app mode, under 640 pixels a layout of its own
    assert "@media screen and (max-width:639px)" in text and "html.app.ready main .view:not(.on){display:none}" in text
    assert "user-select:all" in text                    # the command chip selects whole with one tap
    # The delight stays inert: a data-URI favicon, the wordmark initials with the blue full stop.
    assert 'rel="icon" href="data:image/svg+xml' in text
    assert 'class="rbwm"' in text and "rbdot" in text and "#4f7cf5" in text
    # a rule as declared sits in its row's Details, in the UI font (design pass 1, P4: monospace only for ids,
    # commands, paths and hashes)
    assert ":target" in text and "top1 ≥ 0.7" in text and '<span class="dim mono">top1 ≥ 0.7</span>' not in text


def test_the_page_is_one_self_contained_file_written_where_asked(repo, capsys, tmp_path):
    out = tmp_path / "p.html"
    code, env = repo("page", "--out", str(out))
    assert code == 0 and out.exists()
    text = out.read_text(encoding="utf-8")
    low = text.lower()
    assert low.count("<script>") == 1 and "<script src=" not in low
    script = text.split("<script>", 1)[1].split("</script>", 1)[0]
    assert "fetch(" not in script and "xmlhttprequest" not in script.lower()
    assert "http://" not in low and "https://" not in low
    assert text.startswith("<!doctype html>")
    code2, env2 = repo("page", "--out", str(out))        # regenerating is idempotent apart from the clock
    assert code2 == 0


def test_views_keep_the_overview_small_and_deep_links_open_only_the_needed_evidence(repo, tmp_path):
    """The page's views (BP 2c: the shell's, in place of page.rs's tabs): every view is in the document, none hidden, so
    with no script it reads whole; the Overview does not silently become a second copy of the complete evidence or the
    experiment's design. In a browser the shipped script shows one view at a time from the nav, a deep link opens what
    it names (an experiment's view; a finding's card beside its question), and Back returns."""
    import json
    _, env = repo("page")
    page = Path(env.data["path"])
    text = page.read_text(encoding="utf-8")
    overview = text.split('<div class="sec" id="sec-overview">', 1)[1].split('<div class="sec" id="sec-research">', 1)[0]
    assert 'id="queue"' in overview and 'href="#q-q1"' in overview
    assert '<table' not in overview and "by record times" not in overview
    assert "it cannot lie by construction" not in overview      # the experiment's hypothesis is its view's, not the Overview's
    views = re.findall(r'<section class="view"[^>]*\bid="([^"]+)"', text)
    assert views == ["overview", "research", "criteria", "experiments", "runs", "resources", "history", "your-call", "about"], views
    assert not re.search(r'<(?:section|article) class="view[^"]*"[^>]*\bhidden\b', text)
    nav = text.split('<nav class="nav" id="nav"', 1)[1].split("</nav>", 1)[0]
    assert all(f'href="#{v}"' in nav for v in views if v not in ("overview", "about")) and 'href="#q-q1"' in nav and 'href="#x-e1"' in nav
    # About is reached from the top bar (design pass 1, P10)
    assert 'href="#about"' in text.split('<header class="top"', 1)[1].split("</header>", 1)[0] and 'href="#about"' not in nav
    import test_shell as shell
    node = shell._node()
    script = shell._cdp_script(tmp_path / "views.mjs", f"""
const url = {json.dumps(page.as_uri())};
await cdp.run(async () => {{
  const browser = await cdp.launchChrome();
  const tab = await browser.page();
  await tab.viewport(1280, 900);
  await tab.goto(url, {{ timeoutMs: 30000 }});
  await tab.settle();
  const shown = () => tab.ev("[...document.querySelectorAll('main .view.on')].map(v => v.id)");
  const current = () => tab.ev("[...document.querySelectorAll('#nav a[aria-current=page]')].map(a => a.getAttribute('href'))");
  const out = {{ first: await shown() }};
  await tab.ev("[...document.querySelectorAll('#nav a')].find(a => a.getAttribute('href') === '#research').click()");
  await tab.settle();
  out.research = await shown(); out.current = await current();
  await tab.ev("location.hash = '#x-e1'");
  await tab.settle();
  out.experiment = await shown();
  await tab.ev("location.hash = '#c-c1'");
  await tab.settle();
  out.card = await shown();
  out.inspector = await tab.ev("[...document.querySelectorAll('#insp [data-id]')].map(e => e.dataset.id).filter(id => id === 'c-c1').length ? 'c-c1' : ''");
  await tab.ev("history.back()");
  await tab.settle();
  out.back = await shown();
  // the keyboard: the nav's links take focus in turn (ArrowDown, Home, End), and Enter opens the one focused
  const focused = () => tab.ev("document.activeElement && document.activeElement.closest('#nav') ? document.activeElement.getAttribute('href') : ''");
  const links = await tab.ev("[...document.querySelectorAll('#nav a')].filter(a => a.offsetParent !== null).map(a => a.getAttribute('href'))");
  await tab.ev("[...document.querySelectorAll('#nav a')].filter(a => a.offsetParent !== null)[0].focus()");
  out.keys = [await focused()];
  await tab.key('ArrowDown', 'ArrowDown', 40); out.keys.push(await focused());
  await tab.key('End', 'End', 35); out.keys.push(await focused());
  await tab.key('Home', 'Home', 36); out.keys.push(await focused());
  out.links = links;
  await tab.ev("[...document.querySelectorAll('#nav a')].find(a => a.getAttribute('href') === '#runs').focus()");
  await tab.key('Enter', 'Enter', 13, '\\r');
  await tab.settle();
  out.entered = await shown();
  out.errors = tab.errors;
  return out;
}}, {{ timeoutMs: 120000 }});
""")
    code, said, err = shell._runner(node, script, env=dict(__import__("os").environ))
    assert code == 0 and said["ok"], err
    assert said["first"] == ["overview"] and said["research"] == ["research"] and said["current"] == ["#research"], said
    assert said["experiment"] == ["x-e1"] and said["card"] == ["q-q1"] and said["inspector"] == "c-c1", said
    assert said["back"] == ["x-e1"] and said["errors"] == [], said
    links = said["links"]
    assert said["keys"] == [links[0], links[1], links[-1], links[0]] and said["entered"] == ["runs"], said


def test_scoped_and_synthetic_rows_cannot_collide_or_lose_a_global_finding(repo):
    assert repo("question", "add", "A real question named unfiled", "--id", "unfiled")[0] == 0
    assert repo("experiment", "add", "An unfiled experiment", "--id", "e2")[0] == 0
    for q in ("b", "a-b", "gone"):
        assert repo("question", "add", q, "--id", q)[0] == 0
    assert repo("criterion", "add", "first scope", "-e", "e1", "--id", "c-a", "--metric", "top1", "--at-least", "0.7", "-q", "b")[0] == 0
    assert repo("criterion", "add", "second scope", "-e", "e1", "--id", "c", "--metric", "top1", "--at-least", "0.7", "-q", "a-b")[0] == 0
    assert repo("criterion", "file", "c1", "gone", "--why", "this criterion belongs here")[0] == 0
    assert repo("criterion", "file", "c1", "q1", "--remove", "--why", "move the interpretation")[0] == 0
    assert repo("retract", "gone", "--why", "withdraw the question")[0] == 0
    _, env = repo("page")
    text = Path(env.data["path"]).read_text(encoding="utf-8")
    ids = re.findall(r'\bid="([^"]+)"', re.sub(r"<script>.*?</script>", "", text, flags=re.S))
    assert len(ids) == len(set(ids))
    # a real question named unfiled is q-unfiled; rb's group of work filed under no question is #unfiled
    assert {"q-unfiled", "unfiled", "f-c-a:b", "f-c:a-b", "c-c1"} <= set(ids)
    assert 'href="#x-e2"' in text.split('<section class="unfiled" id="unfiled"', 1)[1].split("</section>", 1)[0]
    assert text.count('id="c-c1"') == 1


def test_expanded_comparison_keeps_same_variant_scopes_and_fixed_design_cells(repo):
    assert repo("experiment", "add", "A fixed comparison grid", "--id", "grid", "--candidate", "shared", "-q", "q1")[0] == 0
    for setting in ("width", "loops", "seed"):
        assert repo("spec", "set", "grid", setting, "--per-run")[0] == 0
    assert repo("spec", "set", "grid", "width", "--design-factor")[0] == 0
    assert repo("criterion", "add", "Compare loop counts within each width", "--id", "grid-c", "-e", "grid", "-q", "q1",
                "--metric", "loss", "--subject", "shared", "--reference", "shared", "--subject-where", "loops=8",
                "--reference-where", "loops=6", "--at-most", "0", "--each")[0] == 0
    for width, loops, loss in ((512, 6, 10), (512, 8, 7), (768, 6, 12), (768, 8, 13)):
        assert repo("run", "add", "grid", f"shared.loss={loss}", "--set", f"width={width}", "--set", f"loops={loops}", "--set", "seed=1")[0] == 0
    _, finding = repo("show", "grid-c")
    _, page = repo("page")
    text = Path(page.data["path"]).read_text(encoding="utf-8")
    plain = _plain(text)
    assert "subject where loops=8" in plain and "reference where loops=6" in plain
    assert finding.data["finding"]["says"]["units"] in plain
    assert "Fixed design cells" in plain and "Within-cell units" in plain
    assert "width 512" in plain and "width 768" in plain
    overview = text.split('<div class="sec" id="sec-overview">', 1)[1].split('<div class="sec" id="sec-research">', 1)[0]
    assert "Within-cell units" not in overview


def test_declared_comparison_leads_overview_and_its_authored_parts_are_earned(repo):
    assert repo("question", "add", "Does the direction reverse across budgets?", "--id", "budgets")[0] == 0
    words = "This is one side only; this interpretation alone cannot establish the cross-budget reversal."
    for i in (1, 2):
        assert repo("experiment", "add", f"Training budget {i}", "--id", f"budget-{i}", "--baseline", "old", "--candidate", "new")[0] == 0
        flags = ["-q", "budgets", "--when-met", words] if i == 1 else []
        assert repo("criterion", "add", f"Difference at budget {i}", "--id", f"budget-c{i}", "-e", f"budget-{i}",
                    "--metric", "change.loss", "--at-least", "0", *flags)[0] == 0
    assert repo("run", "add", "budget-1", "new.loss=2", "old.loss=1")[0] == 0
    assert repo("run", "add", "budget-2", "new.loss=1", "old.loss=2")[0] == 0
    _, rel = repo("criterion", "relate", "budget-c1", "reverses", "budget-c2", "--orientation", "same", "--why", "Align the budgets")
    summary = rel.data["relation"]["summary"]
    _, page = repo("page")
    text = Path(page.data["path"]).read_text(encoding="utf-8")
    overview = text.split('<div class="sec" id="sec-overview">', 1)[1].split('<div class="sec" id="sec-research">', 1)[0]
    assert summary in _plain(overview) and "1 declared comparison" in overview
    assert words not in _plain(overview)
    findings = text.split('<div class="nodes">', 1)[1]
    assert words in _plain(findings)
    assert 'href="#c-budget-c2"' in findings and 'id="c-budget-c2"' in findings
    assert f'<a class="ans" href="#q-budgets:rel">{html_mod.escape(summary, quote=True)}</a>' in overview and 'class="sw good"' not in overview


def test_an_export_with_an_address_offers_the_prompt_that_reruns_it(repo, tmp_path, tmp_path_factory, monkeypatch):
    """Rerun this research (REPRODUCE-WORDS.md): an export made with --url carries the panel once, its bar's button
    and its Overview's, the prompt with the addresses it was published at and the folder rb clone makes, and each
    experiment's own prompt; one without an address carries none. The prompt's commands are rb's: rb clone makes that
    folder, rb context reads there, and rb journal run there keeps a command in the clone's own journal."""
    code, env = repo("export", "--with-record", "--url", "https://lab.example/int8/")
    assert code == 0
    out = Path(env.data["path"])
    page = (out / "page.html").read_text(encoding="utf-8")
    plain = _plain(page)
    assert page.count('href="#rerun" data-rerun>Rerun this research</a>') == 2 and page.count('id="rerun"') == 1
    assert "Copy this prompt into your coding agent." in plain
    prompt = _plain(page.split('id="rerun-prompt">', 1)[1].split("</pre>", 1)[0])
    folder = state().project.id
    # the page by the one address the reader has: the folder it was published at, as the hub's rb-page meta names it
    assert prompt.startswith("Rerun this research with Rabbit Brain (rb). Its record: https://lab.example/int8/\n\n")
    # no remote here: the bundle holds the record alone, and step 2 says the code is its owner's to give
    assert (f"2. Clone the record into a new folder: rb clone https://lab.example/int8/bundle.zip {folder}. This bundle"
            " holds the record alone: the code is its owner's to give, and the runs need it.") in prompt
    assert "rb clone https://lab.example/int8/bundle.zip <another new folder> --code <that address> gets it at the commit" in prompt
    assert "This bundle names the code" not in prompt and "gets it too" not in prompt
    assert "where rb context names a command that puts a run's code here, run that one first." in prompt and "git " not in prompt
    assert "5. Then run rb status, and tell me which rules reproduced and which did not, with their numbers." in prompt
    assert "Run experiment e1, \u201cthe experiment\u201d: its commands in the order rb context names them" in _plain(page.split('id="x-e1:prompt">', 1)[1].split("</pre>", 1)[0])
    assert "The runs need the code; this bundle doesn't say where it is." in plain          # no remote here
    assert "—" not in page
    # with an origin a reader can use, the prompt says what the bundle names: rb clone gets the code, or the record alone
    subprocess.run(["git", "remote", "add", "origin", "https://github.com/ana/int8-resnet.git"], check=True, capture_output=True)
    code, env = repo("export", "--with-record", "--url", "https://lab.example/int8/", "--out", str(tmp_path / "named"))
    assert code == 0
    named = Path(env.data["path"])
    named_prompt = _plain((named / "page.html").read_text(encoding="utf-8").split('id="rerun-prompt">', 1)[1].split("</pre>", 1)[0])
    if json.loads((named / "manifest.json").read_text(encoding="utf-8")).get("repo"):
        assert (f"rb clone https://lab.example/int8/bundle.zip {folder}. This bundle names the code: rb clone gets it too,"
                " at the commit the record names, and puts the record in it.") in named_prompt
        assert "holds the record alone" not in named_prompt
    else:
        assert "holds the record alone" in named_prompt
    subprocess.run(["git", "remote", "remove", "origin"], check=True, capture_output=True)
    code, env = repo("export", "--with-record", "--out", str(tmp_path / "unpublished"))
    assert code == 0 and "Rerun this research" not in (Path(env.data["path"]) / "page.html").read_text(encoding="utf-8")
    # the steps work as written: the clone lands in the folder the prompt names, and rb works there
    away = tmp_path_factory.mktemp("away")                 # outside the project: a clone never lands inside one
    monkeypatch.chdir(away)
    assert main(["clone", str(out), folder]) == 0
    monkeypatch.chdir(away / folder)
    assert main(["context"]) == 0
    assert main(["journal", "run", "--", "git", "--version"]) == 0
