"""rb ui's evidence: the card of what has focus, as fields (UX5 2.1 and 2.3), drawn from rb's card model (crate::card)
and, for a question, from the Overview of that question. Every value the card cites (an id, a path and its lines, a
run, a commit, a setting) is a row that takes focus; Enter opens it in the same pane and Escape goes back, so nothing
cited leads nowhere (the owner, Nov 11: "navigable, enter-able").

The pane keeps a trail of what it opened: the first card is what the story's or the catalogue's row named, and each
Enter on a citation adds one. Escape takes the last one off; at the first, Escape is the screen's own."""
from __future__ import annotations

from typing import Any, Callable, Optional

from rich.text import Text
from textual import events
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.message import Message
from textual.widgets import Static


def subject_of(opens: Optional[str]) -> Optional[tuple]:
    """What an item's `opens` command opens in rb ui: ("card", subject) for `rb show <x>`, ("long", subject) for
    `rb show <x> --long`, ("command", text) for any other command (a git command to copy), None for nothing."""
    if not opens:
        return None
    if opens.startswith("rb show "):
        rest = opens[len("rb show "):].strip()
        if rest.endswith(" --long"):
            return ("long", rest[: -len(" --long")].strip())
        return ("card", rest)
    return ("command", opens)


class Cite(Static, can_focus=True):
    """One citation on a card: focusable, Enter (or a click) opens it."""
    BINDINGS = [Binding("enter", "open", "open", show=False)]

    class Opened(Message):
        def __init__(self, cite: "Cite") -> None:
            super().__init__()
            self.cite = cite

    def __init__(self, label: Text, target: tuple, classes: str = "", **kw: Any) -> None:
        super().__init__(label, classes=f"cite {classes}".strip(), **kw)
        self.target = target

    def action_open(self) -> None:
        self.post_message(Cite.Opened(self))

    async def on_click(self, _event: events.Click) -> None:
        self.focus()
        self.post_message(Cite.Opened(self))


def _figure(fig: dict, width: int) -> Text:
    """A card's result as the home's chart draws a row: the zone, the check's line, the range and the result, then its
    mark with its word where the pane has room for it."""
    from rabbit_brain.tui import MARK_WORD, STATE_MARK, check_colour, glyphs, ov_bar, ov_domain
    lo, hi = ov_domain([fig])
    mark = STATE_MARK.get(fig.get("state"), "□")
    word = f" {MARK_WORD.get(mark, '')}" if width - len(MARK_WORD.get(mark, '')) - 3 >= 12 else ""
    t = ov_bar(fig, lo, hi, max(width - len(word), 12))
    t.append(" ")
    t.append(mark + word, style=check_colour(fig.get("state")))      # the mark with its word, as the strip says it
    return glyphs(t)


# what a mark on a card's item says, in words beside it (rb's card model: card.rs KEY), so no MARKS field is needed to
# read one (no glossary, show instead: the orchestrator, Nov 12 20:35 +08)
ITEM_WORDS = {"○": "not checked against a source", "?": "not known", "✓": "unchanged since it was recorded",
              "△": "changed since", "∅": "gone", "⊘": "ignored by git"}
TRAIL_WORDS = {"±": "with changes no commit holds"}


def said_item(text: str) -> str:
    """A card item with its mark's word: '■ c1' is '■ passed · c1', '○ fp32.precision' 'fp32.precision · not checked
    against a source' (the mark kept before it), 'a1b2c3 ±' 'a1b2c3 ± with changes no commit holds'."""
    from rabbit_brain.tui import MARK_WORD
    mark, _, rest = text.partition(" ")
    if rest and mark in MARK_WORD:
        return f"{mark} {MARK_WORD.get(mark, '')} · {rest}"
    if rest and mark in ITEM_WORDS and not rest[:1].isdigit():
        return f"{mark} {rest} · {ITEM_WORDS[mark]}"
    head, _, last = text.rpartition(" ")
    if head and last in TRAIL_WORDS:
        return f"{text} {TRAIL_WORDS[last]}"
    return text


def card_widgets(card: dict, width: int) -> list:
    """The card's fields, in order: the title, then each field's label and value, then its items, each that opens a
    citation of its own. The CLI's hint under OPEN ("rb show <an id or path above> opens it") is the CLI's: here every
    id above takes focus itself, so it is left out."""
    from rabbit_brain.tui import ACCENT, DIM, TEAL
    out: list = [Static(Text(card.get("title") or card.get("id", ""), style="bold"), classes="ev-title")]
    for f in card.get("fields", []):
        if f.get("key") == "marks":
            continue                    # a list of what marks mean: each mark says its word beside it instead
        label, value = f.get("label", ""), f.get("value", "")
        if f.get("key") == "statement" and value == card.get("title"):
            continue                    # the title already says it
        items = [i for i in f.get("items", []) if i.get("opens") or not i.get("text", "").startswith("·")]
        if f.get("key") == "open":
            target = subject_of(f.get("opens"))
            if target is not None:
                out.append(Cite(Text.assemble(("OPEN", ACCENT), "  ", ("the long form", "underline")), target, classes="ev-open"))
            continue
        head = Text()
        if label:
            head.append(f"{label:<9}", style=ACCENT)
        if value:
            head.append(value, style="" if label else "bold")
        target = subject_of(f.get("opens")) if value else None
        if target is not None:
            out.append(Cite(Text.assemble((f"{label:<9}" if label else "", ACCENT), (value, "underline")), target, classes="ev-field"))
        elif head.plain:
            out.append(Static(head, classes="ev-field"))
        if f.get("figure"):
            fig = Text(" " * 9)
            fig.append_text(_figure(f["figure"], width - 9 - 3))
            out.append(Static(fig, classes="ev-figure"))
        for i in items:
            target = subject_of(i.get("opens"))
            text = Text(" " * 9)
            said = i.get("text", "") if f.get("key") == "lines" else said_item(i.get("text", ""))
            if target is None:
                text.append(said, style=DIM)
                out.append(Static(text, classes="ev-item"))
            else:
                text.append(said, style=f"underline {TEAL}")
                out.append(Cite(text, target, classes="ev-item"))
    return out


def question_widgets(ov: dict, width: int, findings: Optional[dict] = None) -> list:
    """A question's card (UX lead, Nov 12 05:30, ruling 8): its words, its strip, then its checks as the home's chart
    draws them, each row opening that check's card; the long form one key away."""
    from rabbit_brain.tui import ACCENT, DIM, glyphs, ov_chart, ov_wrap, strip_lines
    q = ov.get("question") or {}
    out: list = []
    head = Text()
    for line in ov_wrap(q.get("text", ""), width - 2):
        head.append(f"┃ {line}\n", style="bold")
    for line in strip_lines(f"{(q.get('state') or '').capitalize()} · ", (ov.get("strip") or {}).get("groups", []), width - 2, findings):
        head.append_text(line)
        head.append("\n")
    head.rstrip()
    out.append(Static(glyphs(head), classes="ev-title"))
    rows = ov_chart(list(ov.get("chart", [])), width - 2, findings)
    for line, cid in rows:
        out.append(Cite(glyphs(line), ("card", cid), classes="ev-row") if cid else Static(glyphs(line), classes="ev-item"))
    untested = [g for g in (ov.get("strip") or {}).get("groups", []) if g.get("state") == "untested"]
    for g in untested:
        if g.get("checks"):
            # the checks with no results yet, under those words, as a chart's panel stands under its head
            out.append(Static(Text(g.get("label") or "no results yet", style=DIM), classes="ev-item"))
        for c in g.get("checks", []):
            out.append(Cite(Text.assemble(("  □ ", DIM), (c.get("id", ""), "underline")), ("card", c.get("id", "")), classes="ev-row"))
    if q.get("id"):
        out.append(Cite(Text.assemble(("OPEN", ACCENT), "  ", ("the long form", "underline")), ("long", q["id"]), classes="ev-open"))
    return out


class Evidence(VerticalScroll):
    """The evidence pane: the card of a subject, and the trail of citations opened from it. `load(subject)` gives back
    the card's widgets and its title, or None where nothing can be drawn by that name."""

    def __init__(self, load: Callable[[str], Optional[tuple]], **kw: Any) -> None:
        super().__init__(**kw)
        self.load = load
        self.trail: list[str] = []
        self.can_focus = False

    def show(self, subject: Optional[str], push: bool = False) -> bool:
        """Draw `subject`'s card, as the first of a new trail or (push) one more on it. False where rb has no card."""
        if subject is None:
            self.remove_children()
            self.trail = []
            self.border_title = ""
            return True
        got = self.load(subject)
        if got is None:
            return False
        widgets, title = got
        self.remove_children()
        self.mount(*widgets)
        self.border_title = title
        self.trail = [*self.trail, subject] if push else [subject]
        self.scroll_home(animate=False)
        return True

    def back(self) -> Optional[str]:
        """One step back along the trail: the subject shown now, or None where the trail is at its first card."""
        if len(self.trail) < 2:
            return None
        was = self.trail.pop()
        here = self.trail.pop()
        self.show(here, push=True)
        for c in self.query(Cite):
            if c.target == ("card", was):
                self.call_after_refresh(c.focus)        # back on the card, the citation it came from still focused
                break
        return here

    def first_cite(self) -> Optional[Cite]:
        cites = list(self.query(Cite))
        return cites[0] if cites else None
