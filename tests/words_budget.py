"""The words budget (UX5 section 5): how many of rb's own words a reader meets on a view. One count on every surface:
the view's visible text, less every text the record holds as someone's (a title, a question, a statement, a reason, a
note, a quote, an interpretation, a gate), less command lines and tokens that are ids, paths, names or numbers; what is
left is counted as words."""
import json
import re
from html.parser import HTMLParser

AUTHOR_KEYS = ("title", "text", "statement", "question", "why", "words", "note", "quote", "when_met", "if_holds", "if_not", "hypothesis", "purpose", "label")
NAME_KEYS = ("id", "criterion", "experiment", "subject", "name", "metric", "group", "path", "folder", "unit", "key")
WORD = re.compile(r"[A-Za-z][A-Za-z'’-]*")
CMD = re.compile(r"\b(?:rb|git) [a-z][\w-]*(?: [a-z][\w-]*(?=\s|$))?(?: (?:<[^>]*>|--?[\w-]+|[^\s·]*[\d/._=@#:-][^\s·]*))*")


def _walk(v, out_auth: set, out_names: set) -> None:
    if isinstance(v, dict):
        for k, x in v.items():
            if isinstance(x, str) and x:
                if k in AUTHOR_KEYS:
                    out_auth.add(x)
                if k in NAME_KEYS:
                    out_names.add(x)
            _walk(x, out_auth, out_names)
    elif isinstance(v, list):
        for x in v:
            _walk(x, out_auth, out_names)


def theirs(status: dict) -> tuple:
    """The author texts and the names a record holds (rb status --json's data); the overview's own labels (a strip
    group's "passed", a rests-on mark's "planned first") are rb's words, never subtracted."""
    auth, names = set(), set()
    ov = status.get("overview") or {}
    rb_own = {g.get("label", "") for g in (ov.get("strip") or {}).get("groups", [])} | {r.get("label", "") for r in ov.get("rests", [])}
    for o in ov.get("others", []):
        rb_own |= {g.get("label", "") for g in (o.get("strip") or {}).get("groups", [])}
    _walk(status, auth, names)
    auth -= rb_own
    for x in status.get("experiments", []):
        for v in x.get("variants", []):
            names.add(v.get("name", ""))
        names.update(x.get("varies", []))
    for m in status.get("metrics", []):
        names.add(m.get("id", ""))
    return auth, {n for n in names if n}


def rbs_words(text: str, status: dict) -> list:
    """The words of rb's own in `text`, a view's visible text."""
    auth, names = theirs(status)
    for a in sorted(auth, key=len, reverse=True):
        if len(a) > 2:
            text = text.replace(a, " ")
    # a command is no one's words: a "$ " line, and each command a line names (`rb show <id>`, `rb status --full`,
    # `git show 2982f0fae9b4`), its words around it counted
    lines = [CMD.sub(" ", ln) for ln in text.splitlines() if not re.match(r"\s*\$ ", ln)]
    out = []
    for tok in " ".join(lines).split():
        bare = tok.strip(".,;:()[]{}'\"…›")
        if not bare or bare in names or "/" in bare or "=" in bare or "_" in bare or "." in bare.strip(".") or re.search(r"\d", bare):
            continue
        if any(bare == n.split(".")[-1] for n in names):
            continue
        out.extend(WORD.findall(bare))
    return out


class _Text(HTMLParser):
    """The visible text of an HTML fragment: no script, style, svg title, a visually hidden line or an aria label."""

    SKIP = {"script", "style", "title"}

    def __init__(self) -> None:
        super().__init__()
        self.parts, self.skip = [], []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        hidden = tag in self.SKIP or "vh" in (a.get("class") or "").split() or "hidden" in a or tag == "details" and False
        if tag in ("br", "p", "li", "div", "h1", "h2", "h3", "h6", "tr", "text"):
            self.parts.append("\n")
        self.skip.append(hidden or (self.skip[-1] if self.skip else False))

    def handle_endtag(self, tag):
        if self.skip:
            self.skip.pop()

    def handle_data(self, data):
        if not (self.skip and self.skip[-1]):
            self.parts.append(data)


def html_text(fragment: str) -> str:
    p = _Text()
    p.feed(fragment)
    return "".join(p.parts)


def overview_text(page: str) -> str:
    """The Overview's visible text and the left pane's (its Research tab), with the inspector closed; a key folded
    one click away is not on the screen."""
    i = page.index('id="sec-overview"')
    j = page.index('id="sec-research"', i)
    ov = re.sub(r"<details class=\"figkey\"[^>]*>.*?</details>", "", page[i:j], flags=re.S)
    n = page.index('<nav class="nav"')
    m = page.index("</nav>", n)
    # the Files tree under Record opens on a click: folded, it is not on the screen
    nav = re.sub(r'<div class="ftree".*?</ul><p class="fnone"[^>]*>.*?</p></div>', "", page[n:m], flags=re.S)
    return html_text(ov) + "\n" + html_text(nav)


def screen_text(app) -> str:
    """rb ui's screen as plain text, from Textual's own screenshot: each row's text in order."""
    import html as _html
    svg = app.export_screenshot()
    rows: dict = {}
    for m in re.finditer(r'<text[^>]*?x="([\d.]+)"[^>]*?y="([\d.]+)"[^>]*>(.*?)</text>', svg, re.S):
        rows.setdefault(float(m.group(2)), []).append((float(m.group(1)), _html.unescape(re.sub(r"<[^>]+>", "", m.group(3)))))
    return "\n".join("".join(t for _, t in sorted(v)).replace("\xa0", " ").rstrip() for _, v in sorted(rows.items()))


def home_story_text(app) -> str:
    """rb ui's home as its Overview: the left panel's tabs and pin, the story (the question, the strip, the decisions,
    the chart, what it rests on) and the key line. The Research tab's rows (today's catalogue, which round 2 makes the
    nav) and the card beside the story (the card's own budget, round 2) are not the home's words."""
    from textual.widgets import Footer, OptionList, Static
    s = app.screen
    out = []
    for wid in ("#tabs", "#pin", "#story-head", "#story-rests", "#story-decisions-head"):
        w = s.query_one(wid, Static)
        if w.display:
            out.append(str(w.content))
    for wid in ("#chart-list", "#decisions-list"):
        ol = s.query_one(wid, OptionList)
        out.extend(ol.get_option_at_index(i).prompt.plain if hasattr(ol.get_option_at_index(i).prompt, "plain") else str(ol.get_option_at_index(i).prompt)
                   for i in range(ol.option_count))
    out.append(" ".join(b.binding.description for b in app.active_bindings.values() if b.binding.show))
    return "\n".join(out)
