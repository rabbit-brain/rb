"""rb ui's panels over the home (TUI-PARITY mocks M13 to M18): Continue this research, Decide, the key list, the prompt
view where the terminal cannot be suspended, WHERE and also open. Each draws what the bridge gives, in the mock's rows,
with its own footer (section 3); none composes a sentence.
"""
from __future__ import annotations

from typing import Any, Callable, Optional

from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option

from . import tui_parts as parts

CHIP_SECONDS = 3.0


class Footer(Static):
    """A panel's footer: one row, keys bold, labels dim (section 3)."""

    DEFAULT_CSS = "Footer { dock: bottom; height: 1; padding: 0 1; }"

    def show(self, context: str, pairs: Optional[list] = None) -> None:
        width = self.app.size.width if self.app is not None else 80
        self.update(parts.footer_text(pairs if pairs is not None else parts.FOOTERS[context], width))


PANEL_CSS = """
{name} {{ align: left top; }}
{name} > #box {{ width: 78; max-width: 100%; height: auto; margin: 3 0 0 0; padding: 0 1; border: tall $accent; background: $surface; }}
{name} .head {{ height: 1; }}
"""


class ContinuePanel(ModalScreen[Optional[str]]):
    """Continue this research (M13): its head with the copy chip, the prompt, and Your call's chips; `p` copies again,
    `⏎` on a chip opens Your call filtered to it, `esc` closes."""

    DEFAULT_CSS = PANEL_CSS.format(name="ContinuePanel") + """
    ContinuePanel #chips { height: auto; margin-top: 1; border: none; background: transparent; padding: 0; }
    ContinuePanel #prompt { margin-top: 1; }
    """
    BINDINGS = [Binding("escape", "close", show=False), Binding("p", "copy", show=False), Binding("question_mark", "keys", show=False),
                Binding("q", "app.quit", show=False)]

    def __init__(self, prompt: str, chips: list, copy: Callable[[str], Optional[str]]) -> None:
        super().__init__()
        self.prompt, self.chips, self._copy = prompt, chips, copy

    def compose(self) -> ComposeResult:
        with Vertical(id="box"):
            yield Static(id="head", classes="head")
            yield Static(self.prompt, id="prompt")
            ol = OptionList(id="chips")
            for c in self.chips:
                t = Text()
                parts.chip(t, c.get("said", ""), "")
                ol.add_option(Option(t, id=c.get("kind")))
            yield ol
        yield Footer(id="panel-footer")

    def on_mount(self) -> None:
        self.paint_head(None)
        self.query_one(Footer).show("continue")
        self.query_one("#chips", OptionList).focus()
        self.action_copy()

    def paint_head(self, said: Optional[str]) -> None:
        width = 74
        t = Text(no_wrap=True)
        t.append("Continue this research", style="bold")
        if said:
            chip = f"[{said}]"
            t.append(" " * max(width - len(t.plain) - len(chip), 2))
            parts.chip(t, said, parts.GOOD)
        self.query_one("#head", Static).update(t)

    def action_copy(self) -> None:
        said = self._copy(self.prompt)
        self.paint_head(said)
        if said:
            self.set_timer(CHIP_SECONDS, lambda: self.paint_head(None))

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option_id)

    def action_close(self) -> None:
        self.dismiss(None)

    def action_keys(self) -> None:
        self.app.push_screen(KeyPanel(self.app))


class PromptView(ModalScreen[None]):
    """The prompt view where the terminal cannot be suspended (M16's fallback): the prompt alone, full screen, with no
    border or padding, and `⏎ back`; any key returns to the same screen and focus."""

    DEFAULT_CSS = """
    PromptView { background: $background; }
    PromptView #text { width: 100%; height: auto; }
    """

    def __init__(self, text: str) -> None:
        super().__init__()
        self.text = text

    def compose(self) -> ComposeResult:
        yield Static(Text(self.text, style="reverse"), id="text")
        yield Footer(id="panel-footer")

    def on_mount(self) -> None:
        self.query_one(Footer).show("prompt")

    def on_key(self, event: events.Key) -> None:
        event.stop()
        self.dismiss(None)


class KeyPanel(ModalScreen[None]):
    """The key list (M15): every key with three words at most; `?` again or `esc` closes it."""

    DEFAULT_CSS = """
    KeyPanel { align: center top; }
    KeyPanel > #box { width: 40; height: auto; padding: 0 0; border: tall $accent; background: $surface; }
    """
    BINDINGS = [Binding("escape", "close", show=False), Binding("question_mark", "close", show=False), Binding("q", "app.quit", show=False)]

    def __init__(self, app: Any = None) -> None:
        super().__init__()

    def compose(self) -> ComposeResult:
        from . import __version__
        t = Text()
        for n, line in enumerate(parts.key_lines(f"rb {__version__}")):
            if n:
                t.append("\n")
            t.append_text(line)
        with Vertical(id="box"):
            yield Static(t, id="keys")
        yield Footer(id="panel-footer")

    def on_mount(self) -> None:
        self.query_one(Footer).show("keys")

    def action_close(self) -> None:
        self.dismiss(None)


DECISIONS = (("a", "accept"), ("r", "reject"), ("i", "investigate"))


class DecidePanel(ModalScreen[Optional[tuple]]):
    """Decide (M14): accept, reject or investigate with `a` `r` `i`, then a one-line reason; `⏎` records it only with a
    choice and a reason, `esc` goes back with nothing written."""

    DEFAULT_CSS = PANEL_CSS.format(name="DecidePanel") + """
    DecidePanel #choices { margin-top: 1; height: 1; }
    DecidePanel #why-row { margin-top: 1; height: auto; }
    DecidePanel #why-label { width: 6; height: 1; }
    DecidePanel Input { border: none; border-bottom: outer $accent; background: transparent; padding: 0; height: 2; width: 1fr; }
    """
    BINDINGS = [Binding("escape", "close", show=False)]

    def __init__(self, subject: str, title: str) -> None:
        super().__init__()
        self.subject, self.title_ = subject, title
        self.choice: Optional[str] = None

    def compose(self) -> ComposeResult:
        from textual.containers import Horizontal
        with Vertical(id="box"):
            head = Text(no_wrap=True)
            head.append("Decide", style="bold")
            head.append(f"  {self.subject}", style=parts.DIM)
            yield Static(head, classes="head")
            yield Static(parts.cut_words(self.title_, 74), id="title")
            yield Static(id="choices")
            with Horizontal(id="why-row"):
                yield Static("why", id="why-label")
                yield Input(id="why")
        yield Footer(id="panel-footer")

    def on_mount(self) -> None:
        self.paint()
        self.query_one("#why", Input).can_focus = False
        self.set_focus(None)

    def paint(self) -> None:
        t = Text(no_wrap=True)
        t.append("▌" if self.choice is None else " ", style="bold")
        for n, (k, word) in enumerate(DECISIONS):
            if n:
                t.append("     ")
            chosen = self.choice == word
            t.append(k, style="bold reverse" if chosen else "bold")
            t.append(f" {word}", style="reverse" if chosen else parts.DIM)
        self.query_one("#choices", Static).update(t)
        self.query_one(Footer).show("decide" if self.choice is None else "decide.why")

    def on_key(self, event: events.Key) -> None:
        if isinstance(self.focused, Input) and event.key not in ("escape", "enter"):
            return
        pick = dict(DECISIONS).get(event.character or "")
        if pick and not isinstance(self.focused, Input):
            event.stop()
            self.choice = pick
            self.paint()
            why = self.query_one("#why", Input)
            why.can_focus = True
            why.focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        reason = event.value.strip()
        if self.choice and reason:
            self.dismiss((self.choice, reason))

    def action_close(self) -> None:
        self.dismiss(None)


class WherePanel(ModalScreen[Optional[str]]):
    """WHERE (M17): label and value rows, the branch, the commit with `⧉` (`⏎` copies its whole hash), the record files,
    the other files and one row per watched place. No git command, no sentence."""

    DEFAULT_CSS = PANEL_CSS.format(name="WherePanel") + """
    WherePanel #rows { height: auto; margin-top: 1; margin-bottom: 1; border: none; background: transparent; padding: 0; }
    WherePanel #rows > .option-list--option-highlighted { background: $boost; }
    """
    BINDINGS = [Binding("escape", "close", show=False), Binding("question_mark", "keys", show=False), Binding("q", "app.quit", show=False)]

    def __init__(self, rows: list, copy: Callable[[str], Optional[str]]) -> None:
        super().__init__()
        self.rows, self._copy = rows, copy

    def compose(self) -> ComposeResult:
        with Vertical(id="box"):
            yield Static(Text("Where", style="bold"), id="head", classes="head")
            yield OptionList(id="rows")
        yield Footer(id="panel-footer")

    def on_mount(self) -> None:
        self.paint()
        ol = self.query_one("#rows", OptionList)
        ol.highlighted = next((i for i, r in enumerate(self.rows) if r.get("copy")), 0)
        ol.focus()
        self.query_one(Footer).show("where")

    def paint(self, chip: Optional[str] = None) -> None:
        ol = self.query_one("#rows", OptionList)
        keep = ol.highlighted
        ol.clear_options()
        for r in self.rows:
            t = Text(no_wrap=True, overflow="ellipsis")
            t.append(r["label"].ljust(15), style=parts.DIM)
            t.append(r["value"], style=r.get("style", ""))
            if r.get("copy"):
                t.append("  ")
                if chip:
                    parts.chip(t, chip, parts.GOOD)
                else:
                    t.append("⧉", style=parts.DIM)
            ol.add_option(Option(t))
        if keep is not None:
            ol.highlighted = keep

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        r = self.rows[event.option_index]
        if r.get("copy"):
            said = self._copy(r["copy"])
            self.paint(said)
            if said:
                self.set_timer(CHIP_SECONDS, self.paint)
        elif r.get("opens"):
            self.dismiss(r["opens"])

    def action_close(self) -> None:
        self.dismiss(None)

    def action_keys(self) -> None:
        self.app.push_screen(KeyPanel())


def also_line(item: dict, width: int, focused: bool) -> Text:
    """One also-open row (M18): `mark  subject · state ›`, the subject cut between words."""
    t = Text(no_wrap=True, overflow="crop")
    t.append("▌" if focused else " ", style="bold")
    mark = item.get("mark") or " "
    t.append(mark, style=parts.WARN if mark in "‾?◧" else parts.DIM)
    t.append("  ")
    lead = {"experiment": "▸ ", "question": "┃ "}.get(item.get("kind") or "", "")
    state = f" · {item.get('state', '')}"
    room = width - len(t.plain) - len(lead) - len(state) - 2
    subject = parts.cut_words(item.get("subject") or "", max(room, 6))
    t.append(lead, style=parts.DIM)
    t.append(subject)
    t.append(state, style=parts.DIM)
    t.append(" " * max(width - len(t.plain) - 1, 1))
    t.append("›", style=parts.DIM)
    return t


class AlsoPanel(ModalScreen[Optional[str]]):
    """also open (M18): one line an item, `mark  subject · state ›`; `⏎` opens the item where its action is."""

    DEFAULT_CSS = """
    AlsoPanel { align: left top; }
    AlsoPanel > #box { width: 100%; height: 1fr; margin: 3 0 1 0; padding: 0; background: $background; }
    AlsoPanel #rows { height: auto; border: none; background: transparent; padding: 0; }
    AlsoPanel #rows > .option-list--option-highlighted { background: $boost; }
    """
    BINDINGS = [Binding("escape", "close", show=False), Binding("question_mark", "keys", show=False), Binding("q", "app.quit", show=False)]

    def __init__(self, items: list) -> None:
        super().__init__()
        self.items = items

    def compose(self) -> ComposeResult:
        with Vertical(id="box"):
            yield Static(Text(f"  ALSO OPEN {len(self.items)}", style="bold"), id="head")
            yield OptionList(id="rows")
        yield Footer(id="panel-footer")

    def on_mount(self) -> None:
        ol = self.query_one("#rows", OptionList)
        self.paint(0)
        ol.highlighted = 0 if self.items else None
        ol.focus()
        self.query_one(Footer).show("also")

    def paint(self, at: Optional[int]) -> None:
        ol = self.query_one("#rows", OptionList)
        width = max((self.app.size.width if self.app is not None else 80) - 5, 30)
        with self.prevent(OptionList.OptionHighlighted):
            ol.clear_options()
            for n, item in enumerate(self.items):
                ol.add_option(Option(also_line(item, width, n == at)))
            if at is not None and self.items:
                ol.highlighted = at

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        self.paint(event.option_index)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(self.items[event.option_index].get("opens"))

    def action_close(self) -> None:
        self.dismiss(None)

    def action_keys(self) -> None:
        self.app.push_screen(KeyPanel())


def listing_lines(listing: dict, width: int) -> list[Text]:
    """A waiting design as rows (M21): its kind and id, its title, the chip, then rb confirm's own lines for it (the
    bridge's `lines`, as `rb confirm <exp>` prints them), each whole: wrapped at the width, a word longer than a row
    broken across rows, never cut. VARIANTS, SETTINGS and CHECKS head the lines of their kind; the command that
    confirms it and the notes that stay not confirmed end it, as rb confirm ends its listing. `c` confirms by that
    command's hash, so what it confirms is what these rows show."""
    width = max(width, 20)
    out: list[Text] = [Text.assemble(("design", parts.DIM), "  ", (f"▸ {listing.get('experiment', '')}", ""))]
    for line in _rows(listing.get("title") or "", width - 2, width - 2):
        out.append(Text.assemble(("┃ ", "bold"), (line, "bold")))
    chip = Text()
    parts.chip(chip, "design not confirmed", style=parts.WARN)
    out += [chip, Text("")]
    heads = (("variants: ", "VARIANTS"), ("no variants", "VARIANTS"), ("setting ", "SETTINGS"), ("criterion ", "CHECKS"))
    shown: set = set()
    grouped = False
    for line in listing.get("lines") or []:
        head = next((h for start, h in heads if line.startswith(start)), None)
        if head is not None and head not in shown:
            if out[-1].plain:
                out.append(Text(""))
            out.append(Text(head, style="bold"))
            shown.add(head)
        elif head is None and grouped:
            out.append(Text(""))                        # the parts after the checks (gate, freeze, changes) stand apart
        grouped = head is not None
        out += [Text(row) for row in _hang(line, width)]
    tail = [f"confirm: {listing['confirm']}"] if listing.get("confirm") else []
    tail += [listing["notes_left"]] if listing.get("notes_left") else []
    if tail:
        out.append(Text(""))
    for line in tail:
        out += [Text(row, style=parts.DIM) for row in _hang(line, width)]
    return out


def _hang(line: str, width: int) -> list[str]:
    """One of rb confirm's lines as rows of at most `width`: the first indented two, the rest four, its characters as
    they are (a run of spaces kept), nothing cut."""
    rows = _rows(line, width - 2, width - 4)
    return ["  " + rows[0]] + ["    " + r for r in rows[1:]]


def _rows(text: str, first: int, rest: int) -> list[str]:
    """`text` as rows, the first at most `first` characters, the others `rest`: a row breaks at its last space, which
    the break takes (one space, as any wrap does; the rest of a run stays), or, where a word is longer than the room
    left, within the word, taking nothing. Joined back by those rules the rows are `text`, character for character."""
    first, rest = max(first, 8), max(rest, 8)
    rows: list[str] = []
    while True:
        room = rest if rows else first
        if len(text) <= room:
            return rows + [text]
        cut = text.rfind(" ", 1, room + 1)
        if cut > 0 and cut >= room // 3:
            rows.append(text[:cut])
            text = text[cut + 1:]
        else:
            rows.append(text[:room])
            text = text[room:]


class ConfirmListing(ModalScreen[Optional[str]]):
    """The first `c` on a design (M21): its listing full screen, as rows. The footer reads `↓ more` until the listing's
    last line has been on screen, then `c confirm e1`; only then does `c` confirm, by the listing's own command (its
    hash). `esc` disarms and writes nothing."""

    DEFAULT_CSS = """
    ConfirmListing { background: $background; }
    ConfirmListing #listing { height: 1fr; padding: 0 1 0 0; scrollbar-size-vertical: 1; }
    """
    BINDINGS = [Binding("escape", "back", show=False), Binding("c", "confirm", show=False),
                Binding("j,down", "scroll(1)", show=False), Binding("k,up", "scroll(-1)", show=False),
                Binding("question_mark", "key", show=False), Binding("q", "app.quit", show=False)]

    def __init__(self, listing: dict, reload: Optional[Callable[[], Optional[dict]]] = None) -> None:
        super().__init__()
        self.listing = listing
        self.reload = reload
        self.seen_end = False

    def compose(self) -> ComposeResult:
        from textual.containers import VerticalScroll
        with VerticalScroll(id="listing"):
            yield Static(id="body")
        yield Footer(id="panel-footer")

    def on_mount(self) -> None:
        self.paint()
        self.set_interval(0.15, self.check_end)
        if self.reload is not None:
            self.set_interval(1.0, self.look_again)

    def look_again(self) -> None:
        """A design a session changed while it is open is drawn again, as it reads now, and disarmed until its last line
        has been on screen again: `c` never confirms what was not shown."""
        try:
            now = self.reload()
        except Exception:  # noqa: BLE001 - the listing as drawn stays; rb confirm refuses a hash that moved
            return
        if not now:
            self.dismiss(None)
        elif dict(now).get("confirm") != self.listing.get("confirm"):
            self.listing, self.seen_end = dict(now), False
            self.query_one("#listing").scroll_home(animate=False)
            self.paint()

    def paint(self) -> None:
        width = max(self.app.size.width - 4, 40)
        body = Text("\n").join(listing_lines(self.listing, width))
        body.no_wrap, body.overflow = False, "fold"          # rows fit already; nothing is ever cut
        self.query_one("#body", Static).update(body)
        self.call_after_refresh(self.check_end)

    def check_end(self) -> None:
        box = self.query_one("#listing")
        if not self.seen_end and box.scroll_y >= box.max_scroll_y:
            self.seen_end = True
        self.query_one(Footer).show("", parts.armed_footer(self.listing.get("experiment", "")) if self.seen_end
                                    else parts.FOOTERS["armed"])

    def action_scroll(self, step: int) -> None:
        box = self.query_one("#listing")
        box.scroll_to(y=box.scroll_y + step, animate=False)
        self.call_after_refresh(self.check_end)

    def action_confirm(self) -> None:
        if self.seen_end:
            self.dismiss(self.listing.get("confirm") or None)

    def action_key(self) -> None:
        self.app.push_screen(KeyPanel())

    def action_back(self) -> None:
        self.dismiss(None)


class FoundPanel(ModalScreen[Optional[tuple]]):
    """A file rb found that no run records (M23): a small panel over its Files row. `metrics.json → a run of e1` says
    what it becomes; `y` asks for a one-line note and `⏎` records it, `n` leaves it out, `esc` goes back with nothing
    written. `y` and `n` work here and nowhere else (UX7 T1)."""

    DEFAULT_CSS = PANEL_CSS.format(name="FoundPanel") + """
    FoundPanel #note-row { height: auto; display: none; }
    FoundPanel #note-label { width: 7; height: 1; }
    FoundPanel Input { border: none; border-bottom: outer $accent; background: transparent; padding: 0; height: 2; width: 1fr; }
    """
    BINDINGS = [Binding("escape", "close", show=False)]

    def __init__(self, path: str, becomes: str) -> None:
        super().__init__()
        self.path, self.becomes = path, becomes
        self.noting = False

    def compose(self) -> ComposeResult:
        from textual.containers import Horizontal
        with Vertical(id="box"):
            yield Static(Text(self.path), classes="head")
            yield Static("")
            name = self.path.rsplit("/", 1)[-1]
            yield Static(Text.assemble(name, (" → ", parts.DIM), self.becomes))
            with Horizontal(id="note-row"):
                yield Static("note", id="note-label")
                yield Input(id="note")
        yield Footer(id="panel-footer")

    def on_mount(self) -> None:
        self.query_one("#note", Input).can_focus = False
        self.set_focus(None)
        self.query_one(Footer).show("found")

    def on_key(self, event: events.Key) -> None:
        if self.noting:
            return
        if event.character == "y":
            event.stop()
            self.noting = True
            self.query_one("#note-row").display = True
            note = self.query_one("#note", Input)
            note.can_focus = True
            note.focus()
            self.query_one(Footer).show("found.note")
        elif event.character == "n":
            event.stop()
            self.dismiss(("leave", ""))

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(("record", event.value.strip()))

    def action_close(self) -> None:
        self.dismiss(None)
