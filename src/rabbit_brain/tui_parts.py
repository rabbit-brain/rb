"""rb ui's lines as the parity spec draws them (TUI-PARITY sections 0, 2 and 3): the header's two rows, the tab row, the
Your call head and rows, every footer and the key list. Each is a pure function of what rb gives over the bridge and the
width, so a test reads the same characters the screen draws. Words come from the bridge; these functions place them.
"""
from __future__ import annotations

from typing import Optional

from rich.text import Text

DIM = "grey62"
GOOD = "#7ee787"
BAD = "#ff7b72"
WARN = "#f0883e"
INK = ""

# a state's mark and its colour (section 0 item 7)
MARK_COLOUR = {"■": GOOD, "▲": BAD, "◧": WARN, "□": DIM}

# every footer, by context, as section 3 measures it: (key, label) pairs, the key bold and the label dim
FOOTERS: dict[str, list[tuple[str, str]]] = {
    "home.decision": [("⏎", "open"), ("d", "decide"), ("c", "confirm"), ("p", "copy prompt"), ("?", "keys"), ("q", "quit")],
    "home.design": [("⏎", "open"), ("c", "confirm"), ("p", "copy prompt"), ("?", "keys"), ("q", "quit")],
    "home.chip": [("⏎", "filter"), ("c", "confirm"), ("p", "copy prompt"), ("?", "keys"), ("q", "quit")],
    "home.chip_on": [("⏎", "show all"), ("c", "confirm"), ("p", "copy prompt"), ("esc", "show all"), ("?", "keys"), ("q", "quit")],
    "home.filtered": [("⏎", "open"), ("c", "confirm"), ("esc", "show all"), ("?", "keys"), ("q", "quit")],
    "home.row": [("⏎", "open"), ("c", "confirm"), ("p", "copy prompt"), ("?", "keys"), ("q", "quit")],
    "head.git": [("⏎", "where"), ("c", "confirm"), ("p", "copy prompt"), ("?", "keys"), ("q", "quit")],
    "head.as_of": [("⏎", "look again"), ("c", "confirm"), ("p", "copy prompt"), ("?", "keys"), ("q", "quit")],
    "tree.row": [("⏎", "open"), ("c", "confirm"), ("p", "copy prompt"), ("?", "keys"), ("q", "quit")],
    "tree.resource": [("⏎", "open"), ("g", "get it"), ("c", "confirm"), ("p", "copy prompt"), ("?", "keys"), ("q", "quit")],
    "catalogue.resource": [("⏎", "open"), ("g", "get it"), ("esc", "back"), ("?", "keys"), ("q", "quit")],
    "catalogue.next": [("⏎", "open"), ("p", "copy prompt"), ("esc", "back"), ("?", "keys"), ("q", "quit")],
    "catalogue.row": [("⏎", "open"), ("esc", "back"), ("?", "keys"), ("q", "quit")],
    "yourcall.decision": [("⏎", "open"), ("d", "decide"), ("c", "confirm"), ("esc", "back"), ("?", "keys"), ("q", "quit")],
    "yourcall.row": [("⏎", "open"), ("c", "confirm"), ("esc", "back"), ("?", "keys"), ("q", "quit")],
    "also": [("⏎", "open"), ("esc", "back"), ("?", "keys"), ("q", "quit")],
    "armed": [("↓", "more"), ("esc", "back"), ("?", "keys"), ("q", "quit")],
    "card.decide": [("⏎", "open"), ("←→", "tabs"), ("d", "decide"), ("p", "copy command"), ("esc", "back"), ("?", "keys"), ("q", "quit")],
    "card": [("⏎", "open"), ("←→", "tabs"), ("p", "copy command"), ("esc", "back"), ("?", "keys"), ("q", "quit")],
    "card.design": [("⏎", "open"), ("←→", "tabs"), ("c", "confirm"), ("p", "copy command"), ("esc", "back"), ("?", "keys"), ("q", "quit")],
    "card.question.next": [("⏎", "open"), ("←→", "tabs"), ("p", "copy prompt"), ("l", "long form"), ("esc", "back"), ("?", "keys"), ("q", "quit")],
    "card.question": [("⏎", "open"), ("←→", "tabs"), ("p", "copy command"), ("l", "long form"), ("esc", "back"), ("?", "keys"), ("q", "quit")],
    "card.resource.next": [("⏎", "open"), ("p", "copy prompt"), ("esc", "back"), ("?", "keys"), ("q", "quit")],
    "card.resource": [("⏎", "open"), ("g", "get it"), ("p", "copy command"), ("esc", "back"), ("?", "keys"), ("q", "quit")],
    "card.resource.plain": [("⏎", "open"), ("p", "copy command"), ("esc", "back"), ("?", "keys"), ("q", "quit")],
    "long": [("⏎", "open"), ("p", "copy command"), ("esc", "back"), ("?", "keys"), ("q", "quit")],
    "files": [("⏎", "open"), ("esc", "back"), ("?", "keys"), ("q", "quit")],
    "files.found": [("⏎", "record or leave out"), ("esc", "back"), ("?", "keys"), ("q", "quit")],
    "found": [("y", "record it"), ("n", "leave out"), ("esc", "back")],
    "found.note": [("⏎", "record"), ("esc", "back")],
    "where": [("⏎", "copy"), ("esc", "back"), ("?", "keys"), ("q", "quit")],
    "continue": [("⏎", "open"), ("p", "copy prompt"), ("esc", "close"), ("?", "keys"), ("q", "quit")],
    "prompt": [("⏎", "back")],
    "decide": [("a", "accept"), ("r", "reject"), ("i", "investigate"), ("esc", "back")],
    "decide.why": [("⏎", "record"), ("esc", "back")],
    "keys": [("esc", "close"), ("q", "quit")],
}


# the keys that write to the record: rb ui offers them only where it started, live (another place or a past version is
# only read), so away from there no footer names them
WRITE_KEYS = ("y", "n", "c", "d")
# the key that copies a prompt or a command: a past version offers none, so its footers name it not
COPY_KEYS = ("p",)


def footer_pairs(context: str, at_start: bool = True, past: bool = False) -> list[tuple[str, str]]:
    """The footer for `context`, without the keys that write where the screen does not show where it started, and at a
    past version without p, which copies nothing there (guarantee 38: a past version offers no command to copy)."""
    if at_start:
        return FOOTERS[context]
    if context == "files.found":
        context = "files"
    return [(k, label) for k, label in FOOTERS[context] if k not in WRITE_KEYS and not (past and k in COPY_KEYS)]


def armed_footer(subject: str) -> list[tuple[str, str]]:
    """c armed, its listing seen to its end: `c confirm e1`."""
    return [("c", f"confirm {subject}"), ("esc", "back"), ("?", "keys"), ("q", "quit")]


def footer_plain(pairs: list[tuple[str, str]], width: int) -> str:
    """The footer's characters: each key and its label, two spaces apart; under 80 columns the keys alone."""
    if width < 80:
        return " ".join(k for k, _ in pairs)
    return "  ".join(f"{k} {label}" for k, label in pairs)


def footer_text(pairs: list[tuple[str, str]], width: int) -> Text:
    """The footer as drawn: keys bold, labels dim, starting at column 2 (the widget's own margin)."""
    t = Text(no_wrap=True, overflow="crop")
    for n, (k, label) in enumerate(pairs):
        if n:
            t.append(" " if width < 80 else "  ")
        t.append(k, style="bold")
        if width >= 80:
            t.append(f" {label}", style=DIM)
    return t


def chip(t: Text, words: str, style: str = DIM, bracket: str = DIM) -> None:
    t.append("[", style=bracket)
    t.append(words, style=style)
    t.append("]", style=bracket)


def key(t: Text, k: str) -> None:
    t.append(k, style="bold")


def header_rows(title: str, head: dict, width: int) -> tuple[Text, Text]:
    """Row 0: the title, then at the right `[main · c07560f] w` and the amber `[± 11 not committed]` while record files
    are; row 1: the About strip, whole facts in order, as many as fit (M12, M22). `width` is the screen's columns; each
    row starts at column 1 and ends by the last column but one."""
    room = width - 2
    git = head.get("git") or ""
    dirty = head.get("dirty") or ""
    right = Text(no_wrap=True)
    if git:
        chip(right, git, INK)
        right.append(" ")
        key(right, "w")
    if dirty:
        right.append("  ")
        chip(right, dirty, WARN)
    room_title = max(room - len(right.plain) - (2 if right.plain else 0), 4)
    row0 = Text(no_wrap=True, overflow="crop")
    row0.append(cut_words(title, room_title), style="bold")
    if right.plain:
        row0.append(" " * max(room - len(row0.plain) - len(right.plain), 2))
        row0.append_text(right)
    # row 1: the facts in order, each whole; the last that does not fit drops, with what comes after it
    facts: list[Text] = []
    if head.get("afterwards"):
        f = Text()
        chip(f, head["afterwards"], WARN)
        facts.append(f)
    if head.get("as_of"):
        f = Text(head["as_of"])
        f.append("  ")
        key(f, "r")
        facts.append(f)
    if head.get("since"):
        facts.append(Text(head["since"], style=DIM))
    if head.get("version") and width >= 120:      # rb's version drops below 120 columns (M2)
        facts.append(Text(head["version"], style=DIM))
    row1 = Text(no_wrap=True, overflow="crop")
    for f in facts:
        gap = 3 if row1.plain else 0
        if len(row1.plain) + gap + len(f.plain) > room:
            continue          # a fact that does not fit drops; a later shorter one still may
        if gap:
            row1.append(" " * gap)
        row1.append_text(f)
    return row0, row1


def cut_words(text: str, room: int) -> str:
    """The text cut between words to `room` columns, ending in `…` where cut."""
    if len(text) <= room:
        return text
    if room <= 1:
        return "…"[:room]
    cut = text[: room - 1]
    if " " in cut and not text[room - 1] == " ":
        cut = cut[: cut.rfind(" ")]
    return cut.rstrip(" ,:;·") + "…"


def files_label(changed: int) -> str:
    return f"Files ±{changed}" if changed else "Files"


def tab_row(open_tab: str, changed: int, calls: int, width: int) -> Text:
    """Row 2 below 120 columns (M2): `Overview  Research  t   Files ±1  f`, and `Your call 4  y` ending at the last
    column but one. The open tab is the only reverse video."""
    t = Text(no_wrap=True, overflow="crop")
    t.append(" ")
    t.append("Overview", style="reverse" if open_tab == "overview" else "")
    t.append("  ")
    t.append("Research", style="reverse" if open_tab == "research" else "")
    t.append("  ")
    key(t, "t")
    t.append("   ")
    files = files_label(changed)
    t.append("Files", style="reverse" if open_tab == "files" else "")
    if files != "Files":
        t.append(files[5:], style=WARN)
    t.append("  ")
    key(t, "f")
    pin = Text()
    pin.append(f"Your call {calls}", style="bold")
    pin.append("  ")
    key(pin, "y")
    room = width - 2
    if len(t.plain) + 2 + len(pin.plain) <= room:
        t.append(" " * (room - len(t.plain) - len(pin.plain)))
        t.append_text(pin)
    return t


def left_title(open_tab: str, changed: int) -> Text:
    """The left pane's title at 120 columns: `Research  t  Files ±1  f`, the open tab in reverse video."""
    t = Text(no_wrap=True)
    t.append("Research", style="reverse" if open_tab == "research" else "")
    t.append("  ")
    key(t, "t")
    t.append("  ")
    t.append("Files", style="reverse" if open_tab == "files" else "")
    files = files_label(changed)
    if files != "Files":
        t.append(files[5:], style=WARN)
    t.append("  ")
    key(t, "f")
    return t


def pin(label: str, n: int, k: str, bold: bool) -> Text:
    """A pin: `Your call 4  y` (bold) or `also open 4  a` (dim)."""
    t = Text(no_wrap=True)
    t.append(f"{label} {n}", style="bold" if bold else DIM)
    t.append("  ")
    key(t, k)
    return t


def your_call_head(yc: dict, filt: Optional[str], focus_chip: Optional[str], also: Optional[int], width: int) -> Text:
    """`YOUR CALL 4  [3 decisions]  [1 design to confirm]` and, below 120 columns, `also open 4  a` at its end. A chip
    that filters is in reverse video; the focused chip carries `▌`'s highlight in the screen's own style."""
    t = Text(no_wrap=True, overflow="crop")
    t.append("YOUR CALL", style="bold")
    t.append(f" {yc.get('count', 0)}", style="bold")
    for c in yc.get("chips") or []:
        if len(t.plain) + 4 + len(c.get("said", "")) > width:
            break                               # a chip that does not fit whole stays off the line (the list keeps its rows)
        t.append("  ")
        on = filt == c.get("kind")
        style = "reverse" if on else ""
        if focus_chip == c.get("kind") and not on:
            style = "underline"
        t.append("[", style=DIM if not on else "reverse")
        t.append(c.get("said", ""), style=style)
        t.append("]", style=DIM if not on else "reverse")
    if also is not None:
        tail = Text()
        tail.append(f"also open {also}", style=DIM)
        tail.append("  ")
        key(tail, "a")
        if len(t.plain) + 3 + len(tail.plain) <= width:
            t.append("   ")
            t.append_text(tail)
    return t


WORD_COLOUR = {"not met": BAD, "not reproduced": BAD, "gate missed": BAD, "failed": BAD, "can't tell yet": WARN,
               "no results yet": DIM, "met": GOOD, "reproduced": GOOD, "passed": GOOD, "design": "", "to confirm": ""}


def call_row(r: dict, width: int, focused: bool, word_w: int, mini: Optional[Text] = None) -> Text:
    """One Your call row (M1, M2): `▌`, the mark and its word, `[claim]` or `▸ e1`, the title cut between words, `›`.
    A mini bar sits before `›` where the row has room for it (76 columns or more)."""
    t = Text(no_wrap=True, overflow="crop")
    t.append("▌" if focused else " ", style="bold")
    mark = r.get("mark") or ""
    word = r.get("word") or ""
    lead = f"{mark} {word}" if mark else word
    t.append(mark, style=MARK_COLOUR.get(mark, ""))
    if mark:
        t.append(" ")
    t.append(word, style=WORD_COLOUR.get(word, ""))
    t.append(" " * max(word_w - len(lead), 0) + "  ")
    if r.get("claim"):
        t.append("[claim]", style=DIM)
        t.append(" ")
    elif r.get("kind") == "design":
        t.append(f"▸ {r.get('subject')}", style=DIM)
        t.append(" ")
    tail = Text()
    if mini is not None and mini.plain:
        tail.append("  ")
        tail.append_text(mini)
    tail.append(" ›", style=DIM)
    room = width - len(t.plain) - len(tail.plain)
    title = cut_words(r.get("title") or "", max(room, 4))
    t.append(title)
    t.append(" " * max(room - len(title), 0))
    t.append_text(tail)
    return t


# the key list (M15): every key with three words at most, in its order
KEYS: list[tuple[str, str]] = [
    ("⏎", "open"), ("esc", "back"), ("↑ ↓ j k", "move"), ("← →", "across, tabs"), ("tab ⇧tab", "next, previous"),
    ("home end", "first, last"), ("t", "Research"), ("f", "Files"), ("y", "Your call"), ("a", "also open"),
    ("w", "where"), ("r", "look again"), ("c", "confirm"), ("d", "decide"), ("p", "copy"), ("g", "get it"),
    ("l", "long form"), ("W", "figure's words"), ("?", "keys"), ("q", "quit"),
]


def key_lines(version: str) -> list[Text]:
    """The key panel's lines: its head with rb's version, then each key right-aligned in a column of 9 and its label."""
    out = []
    head = Text(no_wrap=True)
    head.append(" KEYS", style="bold")
    head.append(" " * 23)
    head.append(version, style=DIM)
    out.append(head)
    for k, label in KEYS:
        t = Text(no_wrap=True)
        t.append(k.rjust(9), style="bold")
        t.append("  ")
        t.append(label, style=DIM)
        out.append(t)
    return out


def meter(n: int, of: int) -> str:
    """A count as a meter (A10): at most 10 cells; up to 10 items one cell each; past 10 proportional, empty only at 0
    and full only when complete."""
    if of <= 0:
        return ""
    if of <= 10:
        return "▰" * n + "▱" * (of - n)
    filled = 0 if n <= 0 else 10 if n >= of else min(9, max(1, round(10 * n / of)))
    return "▰" * filled + "▱" * (10 - filled)


def rests_lines(rests: list, width: int, good: str = GOOD, bad: str = BAD) -> list[Text]:
    """What it rests on (M1, M2): each item its words with its meter after the label, whole on its line; items two
    spaces apart (three below 120 columns' story), wrapped between items."""
    items: list[Text] = []
    for r in rests:
        said = r.get("text", "").replace(" ✓ ", " ").replace(" ✗ ", " ")
        label = r.get("label", "")
        t = Text(no_wrap=True)
        if r.get("of") is not None and said.startswith(label + " "):
            t.append(label + " ")
            m = meter(int(r.get("n") or 0), int(r.get("of") or 0))
            t.append(m.replace("▱", ""), style="")
            t.append(m.replace("▰", ""), style=DIM)
            t.append(said[len(label):], style=good if r.get("ok") is True else bad if r.get("ok") is False else "")
        else:
            t.append(said)
        items.append(t)
    lines: list[Text] = []
    gap = "  " if width <= 60 else "   "
    for t in items:
        if lines and len(lines[-1].plain) + len(gap) + len(t.plain) <= width:
            lines[-1].append(gap)
            lines[-1].append_text(t)
        else:
            lines.append(t.copy())
    return lines
