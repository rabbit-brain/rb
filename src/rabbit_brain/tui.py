"""The user's screen: `rb` in their own terminal, next to the agent's tab.

A control room, not a feed: named panels whose rows hold still while their standings change.
YOUR CALL and the catalogue on the left; the highlighted call's full card on the right, live;
what rb observed along the bottom. y and n act on the highlighted call from anywhere. On a narrow
terminal the panels stack and enter opens the card full screen. A call here is something rb found
in the repository and not in the record: y records it, n leaves it out. A call an agent session
proposed, or a past call it mentioned, waits in a quiet group of its own until c confirms the
highlighted one, through the same rb confirm the user would type. w lists the places rb can show (each worktree
of the clone, by its branch and path) and then the versions of the record shown: enter shows one, read only, and
escape goes back. y, n and c act only on the record the screen started on; elsewhere a call's card gives the one
command that confirms it there.

This is a renderer. Everything it shows is asked of rb (the home view and each card, as rb works
them out: `_rb.call`), and everything it does runs through the same code path as the CLI: y runs
`rb run add ...` in a subprocess, in the user's own environment, so what gets recorded, and as
whom, is decided by rb, never by this screen.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Optional

from rich.text import Text
from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.widget import Widget
from textual.widgets import Footer, Input, OptionList, Static
from textual.widgets.option_list import Option

from . import _rb
from .errors import RBError

DIR = ".rb"
DIM = "grey62"
GOOD = "#7ee787"        # a rule met, and nothing less
BAD = "#ff7b72"         # a claim's rule missed, not reproduced
WARN = "#f0883e"        # a blocker with a named fix
ACCENT = "#e3b341"      # the gold thread: panel titles, and what rb itself brings you
TEAL = "#39c5cf"        # ids and timestamps
NARROW = 96             # below this the panels stack and cards open full screen
SHORT = 32              # below this the quiet lines under the work keep to one line each, cut


class View(dict):
    """What rb answers for the screen (the home view, a card, a row of either): its fields, by name."""

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name) from None


def _view(v: Any) -> Any:
    if isinstance(v, dict):
        return View({k: _view(x) for k, x in v.items()})
    if isinstance(v, list):
        return [_view(x) for x in v]
    return v


def ask(root: Path, op: str, *, _place: Optional[str] = None, _at: Optional[str] = None, **args: Any) -> Any:
    """Ask rb about the record at `root`, as the user at their own terminal; with `_place` or `_at`, about the place or
    the past version op places lists, read there and never written."""
    return _view(_rb.call(op, args, root=root, via="cli", place=_place, at=_at))


def _inspected(root: Path) -> None:
    """The screen showed the record: once a session, without the record's id, title or content. Rust checks local
    consent, and any delivery it starts runs in the background."""
    try:
        _rb.call("telemetry_inspected", root=root, via="cli")
    except Exception:  # analytics can never stop the screen
        pass


def home_view(root: Path, since_baseline: Optional[int] = None, place: Optional[str] = None, at: Optional[str] = None) -> View:
    return ask(root, "home", since_baseline=since_baseline, _place=place, _at=at)


def found_card(root: Path, request_id: str, place: Optional[str] = None, at: Optional[str] = None) -> View:
    """The card of something rb found in the repository. KeyError when nothing found goes by that id now."""
    c = ask(root, "card", ref=request_id, _place=place, _at=at)
    if c is None:
        raise KeyError(request_id)
    return c


def object_card(root: Path, subject: str, place: Optional[str] = None, at: Optional[str] = None) -> View:
    """The card of a record on the catalogue. KeyError when nothing there goes by that name."""
    c = ask(root, "object_card", subject=subject, _place=place, _at=at)
    if c is None:
        raise KeyError(subject)
    return c


def places_view(root: Path, place: Optional[str] = None) -> View:
    """The places the screen can show, and the versions of the record of `place` (by default the one it started on)."""
    return ask(root, "places", place=place or "")


def short(p: View) -> str:
    """A place as the user's screens name it: its branch, then its path in short (rb lists both forms)."""
    return p.get("label_short") or p.label


def update_line(root: Path, started: Optional[str]) -> tuple:
    """The quiet line once an upgrade of rb was installed while the screen is open ("✔ Update installed · Restart to
    update"), in rb's words, "" when there is none or rb cannot say; and the version the screen started as, which the
    first ask learns (rb on disk is the new one once an upgrade replaced it)."""
    try:
        said = ask(root, "update_notice", started=started or "")
        return said.get("line") or "", started or said.get("running")
    except Exception:  # noqa: BLE001 - a courtesy line never takes the screen down
        return "", started


def place_line(info: View, place: Optional[str], at: Optional[str]) -> str:
    """Which place and version the screen shows, in rb's words: the place's branch and path, with what it is when it is
    not the record the screen started on; a past version as rb says one. A single checkout says its branch, else its
    folder's name; with more places, or a past version, each is named in short. Away from where the screen started, it says it is
    read only and how to go back before what rb says of the place."""
    shown = next((p for p in info.places if p.id == place), None) or next((p for p in info.places if p.record == "this"), None)
    if shown is None:
        return ""
    back = f"esc: back to {back_to(info)}"
    if at:
        version = next((v for v in info.versions if v.sha == at), None)
        if version is not None:
            return f"{version.get('shown_short') or version.shown} · {back}"
    if len(info.places) <= 1:
        # a single checkout by its branch, else its folder's name: w lists it with its whole path
        return shown.branch or PurePosixPath(shown.path).name
    return short(shown) if shown.record == "this" else f"{short(shown)} · read only · {back} · {shown.said}"


def back_to(info: View) -> str:
    """Where escape goes back to: the place the screen started on, by its branch (else as it is named)."""
    start = next((p for p in info.places if p.record == "this"), None)
    return "" if start is None else (start.branch or short(start))


def _esc(text: str) -> str:
    return text.replace("[", r"\[")


# The words of a headline that take a colour, as every surface colours them (the binary's page and rb open read the
# same three sets, and its tests hold these to them): a rule met, a rule missed, and what cannot be judged or is a
# priority call. Everything else is dim, a gate met among it: a gate is policy, not a finding.
GOOD_WORDS = {"meets", "reproduced"}
BAD_WORDS = {"misses", "not reproduced"}
WARN_WORDS = {"gate missed", "undetermined", "unresolved", "point only", "gate undetermined"}


def _word(part: str) -> str:
    """One part of a headline without its count: '2 criteria met' is 'meets'."""
    word = part.strip()
    head, _, rest = word.partition(" ")
    word = rest if head.isdigit() else word
    return {"criterion met": "meets", "criteria met": "meets",
            "criterion not met": "misses", "criteria not met": "misses",
            "criterion undetermined": "undetermined", "criteria undetermined": "undetermined",
            "criterion untested": "untested", "criteria untested": "untested"}.get(word, word)


def standing_class(part: str) -> str:
    """How one part of a headline is coloured: good for a rule met, bad for one missed, warn for a gate missed or a
    reading that cannot tell, dim for everything else. A count prefix ('2 criteria met') is read past."""
    word = _word(part)
    if word in GOOD_WORDS:
        return "good"
    if word in BAD_WORDS:
        return "bad"
    if word in WARN_WORDS:
        return "warn"
    return "dim"


def rule_label(word: str, count: int) -> str:
    noun = "criterion" if count == 1 else "criteria"
    return {"meets": f"{noun} met", "misses": f"{noun} not met",
            "undetermined": f"{noun} undetermined", "untested": f"{noun} untested"}.get(word, word)


def _colour(part: str, muted=()) -> str:
    """One part of a headline, coloured the way every surface colours it. A met word in `muted` counts a finding that
    is not green (a problem under it, or met only on its estimate), so it is not drawn green."""
    cls = standing_class(part)
    if cls == "good" and _word(part) in muted:
        return DIM
    return {"good": GOOD, "bad": BAD, "warn": WARN}.get(cls, DIM)


def _bar_colour(standing: str, muted=()) -> str:
    return _colour(standing.split(" · ")[0], muted)


def _short_count(part: str) -> str:
    """A count of criteria in the catalogue's short words: '2 criteria not met' is '2 not met'. Anything else stays."""
    count, _, rest = part.partition(" ")
    noun, _, word = rest.partition(" ")
    if count.isdigit() and noun in ("criterion", "criteria") and word in ("met", "not met", "undetermined", "untested"):
        return f"{count} {word}"
    return part


def standing_ways(standing: str, muted=(), *, short: bool = False) -> list[Text]:
    """The ways a catalogue row's standing can be drawn, longest first, each part coloured as rb's own words for it are,
    in short count words if `short`: all of it; then, where it counts criteria, without the words after its counts (the
    lock word), then without the words before them too (the state word), ending in a '…' where they were."""
    parts = standing.split(" · ")
    counted = [i for i, part in enumerate(parts) if part[:1].isdigit()]
    ways = [parts]
    if counted:
        for kept in (parts[:counted[-1] + 1], parts[counted[0]:counted[-1] + 1]):
            if kept != ways[-1]:
                ways.append(kept)
    drawn = []
    for kept in ways:
        out = Text(no_wrap=True, overflow="ellipsis")
        for i, part in enumerate(kept):
            if i:
                out.append(" · ", style=DIM)
            out.append(_short_count(part) if short else part, style=_colour(part, muted))
        if len(kept) < len(parts):
            out.append(" …", style=DIM)
        drawn.append(out)
    return drawn


def standing_text(standing: str, muted=(), *, short: bool = False, room: Optional[int] = None, spare: int = 0) -> Text:
    """A catalogue row's standing in `room` columns: the longest of its ways that fits. Where none does, its counts
    alone, with up to `spare` columns more (what its title can give way to them), cut at their end where even that is
    too little, each part keeping its colour."""
    ways = standing_ways(standing, muted, short=short)
    if room is None:
        return ways[0]
    for out in ways:
        if len(out) <= room:
            return out
    out = ways[-1].copy()
    if any(part[:1].isdigit() for part in standing.split(" · ")):
        room += max(spare, 0)
    if len(out) > room:
        out.truncate(max(room, 1), overflow="ellipsis")
    return out


def tally_text(tally, muted=(), *, short: bool = False) -> Text:
    """The line counting the record's criteria by their reading, each count coloured as rb's own words for it are, in
    short count words if `short`."""
    out = Text(no_wrap=True, overflow="ellipsis")
    out.append("criteria", style=DIM)
    out.append("   ")
    for i, (word, n) in enumerate(tally):
        if i:
            out.append(" · ", style=DIM)
        said = f"{n} {rule_label(word, n)}"
        out.append(_short_count(said) if short else said, style=_colour(word, muted))
    return out


def build_card_widgets(c: View) -> list[Widget]:
    """The card, as widgets: used by the full-screen card and by the home screen's side panel,
    so the two can never disagree about what a call shows."""
    out: list[Widget] = [
        Static(Text(c.title, style="bold", no_wrap=True, overflow="ellipsis"), classes="card-title"),
        Static(f"[{DIM}]{_esc(c.who_line)}[/{DIM}]"),
    ]
    if c.lead:
        out.append(Static(_esc(c.lead), classes="lead"))
    for s in c.sentences:
        out.append(Static(_esc(s), classes="lead"))     # the brief, full ink: the state is the content
    for f in c.facts:
        out.append(Static(f"[{DIM}]{_esc(f)}[/{DIM}]", classes="block"))
    if c.runs:
        out.append(Static(f"\n[{ACCENT}]Its runs (open one for its receipt):[/{ACCENT}]", classes="section"))
        for r in c.runs:
            out.append(RunLine(r["label"], r["id"]))
    if c.reports:
        out.append(Static("\n" + _esc(c.reports)))
    if c.pass_bars:
        out.append(Static(f"\n[{ACCENT}]{_esc(c.bars_label)}[/{ACCENT}]", classes="section"))
        for p in c.pass_bars:
            colour = _bar_colour(p.standing, p.get("muted", ()))
            bar = Horizontal(
                Static(f"[b {TEAL}]{_esc(p.criterion_id)}[/b {TEAL}]", classes="bar-id"),
                Static(f"{_esc(p.text)}\n[{DIM}]{_esc(p.rule)}[/{DIM}] · [{colour}]{_esc(p.standing)}[/{colour}]",
                       classes="bar-body"),
                classes="bar",
            )
            out.append(bar)
    for b in c.blockers:
        out.append(Static(f"[{WARN}]{_esc(b)}[/{WARN}]", classes="block"))
    for look in c.looks:
        out.append(Static(f"[{WARN}]{_esc(look)}[/{WARN}]", classes="look"))
    return out


# ---------------------------------------------------------------- settling, shared by card and home


def run_settle(app: App, root: Path, argv: list[str], word: str, after: Callable[[], None]) -> None:
    """The same entry the user would type. The subprocess inherits this terminal's environment,
    so rb decides who is calling; the screen asserts nothing."""
    env = _rb.environment()
    env["RB_TELEMETRY_SURFACE"] = "tui"
    r = subprocess.run([*_rb.command(), *argv], cwd=root, env=env,
                       capture_output=True, encoding="utf-8", errors="replace", timeout=120, **_rb.QUIET)
    if r.returncode == 0:
        app.notify(f"{word}.", severity="information")
        after()
    else:
        tail = (r.stdout + r.stderr).strip().splitlines()
        app.notify(tail[-1][:120] if tail else "it did not run", severity="warning", timeout=8)


# what a yes records, said at the moment of commitment; who it is recorded as is rb's to decide, and no screen says it
YES_RECORDS = "recorded as a run, with this note, through the same rb run add anyone would type"


def prompt_yes(screen: Screen, root: Path, card: View, after: Callable[[], None]) -> None:
    """y on something rb found: record it as a run, with an optional note, through the rb run add it proposes, run in
    the checkout rb found it in (`run_in`: the record's, or the worktree the command that printed it ran in), whose
    paths and code it is, wherever the screen was started."""
    if card.kind != "record":
        return

    def go(note: Optional[str]) -> None:
        if note is None:
            return
        argv = list(card.argv) + (["--note", note] if note else [])
        run_settle(screen.app, Path(card.get("run_in") or root), argv, f"{card.request_id} recorded", after)
    screen.app.push_screen(Reason("record it: a note?", allow_empty=True, sub=YES_RECORDS), go)


def prompt_no(screen: Screen, root: Path, card: View, after: Callable[[], None]) -> None:
    """n on something rb found: leave it out of this machine's screen, with an optional reason."""
    if card.kind != "record":
        return

    def go(why: Optional[str]) -> None:
        if why is None:
            return
        try:
            ask(root, "dismiss", key=card.dismiss_key, why=why)
        except Exception as exc:  # noqa: BLE001 - a failed dismissal is said, never fatal
            screen.app.notify(str(exc)[:120], severity="warning", timeout=8)
            return
        screen.app.notify(f"{card.request_id} left out.", severity="information")
        after()
    screen.app.push_screen(Reason("leave it out: why?", allow_empty=True,
                                  sub="hidden from this machine's screen; the file itself stays where it is"), go)


class RunLine(Static):
    """One run on an experiment's card, openable: a click (terminals stream the mouse) opens the
    run's receipt as its own card. The label carries the run's words; the id stays in the wiring."""

    def __init__(self, label: str, ev_id: str) -> None:
        super().__init__(f"[{DIM}]{_esc(label)}[/{DIM}]", classes="runline")
        self._ev = ev_id

    async def on_click(self) -> None:
        root = getattr(self.screen, "root", None)
        if root is not None:
            self.screen.app.push_screen(ObjectScreen(root, self._ev, getattr(self.screen, "place", None), getattr(self.screen, "at", None)))


class Chip(Static):
    """The key, clickable. Terminals stream mouse events to the app (Textual turns that on for us),
    so a chip is just the binding with a hit area: it runs the same screen action the key runs and
    owns no behaviour of its own. Keyboard-first stays true; the mouse simply stops being ignored."""

    def __init__(self, key: str, label: str, action: str) -> None:
        super().__init__(f"[b {ACCENT}]{key}[/b {ACCENT}] {_esc(label)}", classes=f"chip chip-{action}")
        self._action = action

    async def on_click(self) -> None:
        await self.screen.run_action(self._action)


class Reason(ModalScreen[Optional[str]]):
    """One line, recorded as the user's own words. Escape cancels. A call needs words;
    recording a fact does not, so `allow_empty` lets enter mean "go, no note"."""

    def __init__(self, prompt: str, draft: str = "", allow_empty: bool = False, sub: str = "") -> None:
        super().__init__()
        self.prompt, self.draft, self.allow_empty, self.sub = prompt, draft, allow_empty, sub

    def compose(self) -> ComposeResult:
        with Vertical(id="reason-box"):
            yield Static(f"[b]{_esc(self.prompt)}[/b]")
            if self.sub:
                yield Static(f"[{DIM}]{_esc(self.sub)}[/{DIM}]")
            yield Input(value=self.draft, placeholder="a note, or just enter" if self.allow_empty else "why, in your own words", id="reason")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        value = event.value.strip()
        self.dismiss(value if (value or self.allow_empty) else None)

    def key_escape(self, event: events.Key) -> None:
        event.stop()               # consumed here: escape must never fall through and close the card below
        self.dismiss(None)


class CardScreen(Screen):
    """A found item's card: what rb observed, what it reports, and what the numbers will be read
    against. The keys say what they do here: y records it, n leaves it out, on the record the screen
    started on only (another place or a past version is only read)."""
    BINDINGS = [Binding("y", "yes", "record it"), Binding("n", "no", "leave it out"),
                Binding("escape,q", "back", "back")]

    def __init__(self, root: Path, request_id: str, place: Optional[str] = None, at: Optional[str] = None) -> None:
        super().__init__()
        self.root, self.request_id = root, request_id
        self.place, self.at = place, at
        self.card = found_card(root, request_id, place, at)

    def check_action(self, action: str, parameters: tuple) -> Optional[bool]:
        if action in ("yes", "no") and (self.place or self.at):
            return False            # unbound away from the record the screen started on
        return True

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="card"):
            for w in build_card_widgets(self.card):
                yield w
            if not (self.place or self.at):
                with Horizontal(classes="chip-row"):
                    yield Chip("y", self._label("y"), "yes")
                    yield Chip("n", self._label("n"), "no")
        yield Footer()

    def _label(self, key: str) -> str:
        return next((b.description for b in self.BINDINGS if b.key == key), key)

    def action_back(self) -> None:
        self.app.pop_screen()

    def _after(self) -> None:
        self.app.pop_screen()
        home = self.app.screen
        if isinstance(home, HomeScreen):
            home.refresh_home()

    def action_yes(self) -> None:
        prompt_yes(self, self.root, self.card, self._after)

    def action_no(self) -> None:
        prompt_no(self, self.root, self.card, self._after)


def card_screen(root: Path, request_id: str, place: Optional[str] = None, at: Optional[str] = None) -> CardScreen:
    return CardScreen(root, request_id, place, at)


class ObjectScreen(Screen):
    """A question, experiment, criterion, run or resource from the catalogue, opened. Read-only: the record is
    changed through calls and commands, never by browsing it."""
    BINDINGS = [Binding("escape,q", "back", "back")]

    def __init__(self, root: Path, subject: str, place: Optional[str] = None, at: Optional[str] = None) -> None:
        super().__init__()
        self.root, self.subject, self.place, self.at = root, subject, place, at
        self.card = object_card(root, subject, place, at)

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="card"):
            for w in build_card_widgets(self.card):
                yield w
        yield Footer()

    def action_back(self) -> None:
        self.app.pop_screen()


def pending_widgets(row: View, at_start: bool, alone: bool = False) -> list:
    """A call that waits for a confirmation, as its card says it: what it is, its words, and how it is confirmed (where
    the screen started, c and the command; elsewhere, the one command that confirms it there). `alone`: shown full
    screen, with no list beside it, so c confirms this one rather than the highlighted one."""
    kind = CONFIRM_KINDS.get(row.kind, CONFIRM_KINDS["past"])
    hint = {"gate": CONFIRM_GATE, "design": CONFIRM_DESIGN}.get(row.kind, CONFIRM_RECORDS)
    if row.kind == "design" and not any(str(line).startswith("gate: ") for line in (row.get("lines") or [])):
        hint = CONFIRM_DESIGN_NO_GATE              # "with its gate" only where it has one
    if alone:
        hint = hint.replace("c confirms the highlighted one", "c confirms this one", 1)
    said = f"{_esc(hint)}: {_esc(row.confirm)}" if at_start else _esc(row.confirm)
    # a design reads part by part, as rb confirm lists what c confirms
    parts = [Static(f"[{DIM}]{_esc(line)}[/{DIM}]", classes="block") for line in (row.get("lines") or [])]
    # a past version offers no command to copy (guarantee 38): its rows come with none, and the card says none
    how = [Static(f"[{DIM}]{said}[/{DIM}]", classes="lead")] if row.confirm else []
    return [Static(Text(kind, style="bold"), classes="card-title"), Static(_esc(row.text), classes="lead"), *parts, *how]


class PendingScreen(Screen):
    """A call that waits for a confirmation, opened full screen on a narrow terminal, where no card sits beside the
    list: the same lines as that card. c confirms the one it shows, as c beside the list does; one a write changed
    while it was open is shown as it reads now, and the next c says so before the c after it confirms it. escape goes
    back."""
    BINDINGS = [Binding("escape,q", "back", "back"), Binding("c", "confirm", "confirm")]

    def __init__(self, row: View, at_start: bool, confirm: Optional[Callable[[View], None]] = None) -> None:
        super().__init__()
        self.row, self.at_start, self.confirm = row, at_start, confirm
        self.seen = True            # the user has the row it shows in view

    def check_action(self, action: str, parameters: tuple) -> Optional[bool]:
        if action == "confirm" and (not self.at_start or self.confirm is None):
            return False            # unbound away from the record the screen started on
        return True

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="card"):
            for w in pending_widgets(self.row, self.at_start, alone=True):
                yield w
        yield Footer()

    def show(self, row: View) -> None:
        """The same one as it reads now, after a write changed it while it was open."""
        if row.id != self.row.id:
            self.seen = False
        self.row = row
        card = self.query_one("#card", VerticalScroll)
        if not card.is_attached:
            return                                  # a write seen as the screen closes: nothing left to draw on
        card.remove_children()
        card.mount(*pending_widgets(row, self.at_start, alone=True))

    def action_confirm(self) -> None:
        if self.confirm is None or not self.at_start:
            return
        if not self.seen:
            self.seen = True
            name = {"gate": f"{self.row.id.split('@')[0]}'s gate", "design": f"{self.row.id.split('@')[0]}'s design"}.get(self.row.kind, self.row.id)
            self.app.notify(f"{name} changed since it was shown: c again confirms it as it reads now.", timeout=8)
            return
        self.confirm(self.row)

    def action_back(self) -> None:
        self.app.pop_screen()


class OpenItemsScreen(Screen):
    """What is still open in the record, beside the user's calls, and the raw activity tail, glanced at, never managed
    from here. Subordinate on purpose: the home screen is the state of the research, not a feed."""
    BINDINGS = [Binding("escape,q,a", "back", "back")]

    def __init__(self, items: list[str], activity: list[str]) -> None:
        super().__init__()
        self.items, self.activity = items, activity

    def compose(self) -> ComposeResult:
        with VerticalScroll():
            yield Static("[b]Open in the record[/b]\n")
            for line in self.items:
                yield Static(f"  {_esc(line)}")
            if self.activity:
                yield Static(f"\n[b]Activity[/b]  [{DIM}]what rb observed, newest last[/{DIM}]\n")
                for line in self.activity:
                    yield Static(f"  [{DIM}]{_esc(line)}[/{DIM}]")
        yield Footer()

    def action_back(self) -> None:
        self.app.pop_screen()


# what a confirmation records, said where c acts; who it is recorded as is rb's to decide, and no screen says it
CONFIRM_RECORDS = "c confirms the highlighted one: the call is made, through the same rb confirm anyone would type"
CONFIRM_GATE = "c confirms the highlighted one: the gate is yours, with its words, through the same rb confirm anyone would type"
CONFIRM_DESIGN = "c confirms the highlighted one: the design is yours, as listed here, with its gate, through the same rb confirm anyone would type"
CONFIRM_DESIGN_NO_GATE = "c confirms the highlighted one: the design is yours, as listed here, through the same rb confirm anyone would type"
# what c confirms, by the kind of row that waits
CONFIRM_NOUNS = {"gate": "gate", "design": "design"}
# each kind of row that waits, as its card titles it
CONFIRM_KINDS = {"proposed": "Proposed, not made", "past": "A past call mentioned, not confirmed", "gate": "A gate set, not confirmed",
                 "design": "A design, not confirmed"}


class PlacesScreen(ModalScreen[Optional[tuple]]):
    """The places the screen can show, as rb lists them (each worktree by its branch and path, with what it is), then
    the versions of the record shown. enter shows the highlighted one, escape goes back. A place with no record of its
    own to show is listed and cannot be chosen."""
    BINDINGS = [Binding("escape,q,w", "back", "back")]

    def __init__(self, info: View, place: Optional[str], at: Optional[str]) -> None:
        super().__init__()
        self.info, self.place, self.at = info, place, at

    def compose(self) -> ComposeResult:
        with Vertical(id="places-box"):
            with Vertical(id="where-panel"):
                yield OptionList(id="where")
                yield Static(id="where-detail")     # the highlighted place's whole path, as git lists it
            with Vertical(id="versions-panel"):
                yield OptionList(id="versions")

    def on_mount(self) -> None:
        self.query_one("#where-panel").border_title = "WHERE"
        where = self.query_one("#where", OptionList)
        shown = self.place or next((p.id for p in self.info.places if p.record == "this"), None)
        # a single checkout is named as it always was, with nothing under it
        named = short if len(self.info.places) > 1 else (lambda p: p.label)
        for p in self.info.places:
            where.add_option(Option(Text(f"{named(p)}: {p.said}", no_wrap=True, overflow="ellipsis"), id=f"p:{p.id}", disabled=not p.readable))
        ids = [p.id for p in self.info.places]
        if shown in ids:
            where.highlighted = ids.index(shown)
        self._detail(where.highlighted)
        versions = self.query_one("#versions", OptionList)
        panel = self.query_one("#versions-panel")
        panel.border_title = "VERSION"
        for v in self.info.versions:
            versions.add_option(Option(Text(v.label + (f" · {v.note}" if v.get("note") else ""), no_wrap=True, overflow="ellipsis"), id=f"v:{v.sha}"))
        shas = [v.sha for v in self.info.versions]
        if self.at in shas:
            versions.highlighted = shas.index(self.at)
        panel.display = bool(shas)
        (versions if self.at in shas else where).focus()

    def _detail(self, index: Optional[int]) -> None:
        """Under the places, the highlighted one's whole path, as git lists it, and why it cannot be shown; nothing for
        a single checkout, whose one place is named whole."""
        many = len(self.info.places) > 1
        p = self.info.places[index] if many and index is not None and 0 <= index < len(self.info.places) else None
        said = "" if p is None else p.path + (f"\n{p.why_not}" if p.get("why_not") else "")
        detail = self.query_one("#where-detail", Static)
        detail.update(f"[{DIM}]{_esc(said)}[/{DIM}]" if said else "")
        detail.display = bool(said)

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        if event.option_list.id == "where":
            self._detail(event.option_index)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        ref = event.option_id or ""
        if ref.startswith("p:"):
            self.dismiss(("place", ref[2:]))
        elif ref.startswith("v:"):
            self.dismiss(("at", ref[2:]))

    def action_back(self) -> None:
        self.dismiss(None)


class HomeScreen(Screen):
    BINDINGS = [Binding("enter", "open", "open", priority=True),
                Binding("y", "yes", "yes"), Binding("n", "no", "no"),
                Binding("c", "confirm", "confirm"),
                Binding("a", "open_items", "open in the record"),
                Binding("w", "places", "where"),
                Binding("escape", "back_to_start", "back", show=False),
                Binding("r", "refresh", "refresh", show=False), Binding("q", "app.quit", "quit")]

    def __init__(self, root: Path, here: Optional[Path] = None) -> None:
        super().__init__()
        self.root = root                              # the record the screen started on, which y, n and c act on
        self.here = here or root                      # the checkout the user is in, where c runs rb confirm
        self.place: Optional[str] = None              # another place shown, by the id rb lists, or None
        self.at: Optional[str] = None                 # a past version shown, by its commit, or None
        old, lines = ask(root, "mark_seen")           # the user looked; since runs from their last look
        self._since_baseline = old if old >= 0 else lines
        self.home = home_view(root, self._since_baseline)
        self._places: Optional[View] = None
        self._asks: list[View] = []
        self._object_refs: dict[str, str] = {}       # a catalogue row can share its object with another question's row
        self._side_for: Optional[str] = None
        # what waits for a confirmation that c may confirm: the one the user was shown, by its id (a design's and a
        # gate's name what was shown), and the one shown before a write changed it
        self._in_view: Optional[str] = None
        self._was_in_view: Optional[str] = None

    @property
    def at_start(self) -> bool:
        """Whether the screen shows the record it started on, live: the one y, n and c act on."""
        return self.place is None and self.at is None

    def check_action(self, action: str, parameters: tuple) -> Optional[bool]:
        if action in ("yes", "no", "confirm") and not self.at_start:
            return False            # unbound away from the record the screen started on
        if action == "back_to_start" and self.at_start:
            return False
        return True

    def action_back_to_start(self) -> None:
        """escape, away from where the screen started: back to that record, live."""
        self.switch(None, None)

    def _home_view(self) -> View:
        return home_view(self.root, self._since_baseline, self.place, self.at)

    def _object_card(self, subject: str) -> View:
        return object_card(self.root, subject, self.place, self.at)

    def _found_card(self, request_id: str) -> View:
        return found_card(self.root, request_id, self.place, self.at)

    def compose(self) -> ComposeResult:
        # The work leads: the catalogue fills the screen, with the highlighted row's card beside
        # it. What waits on the user (what rb found and nobody recorded) is a strip under the work,
        # present only while something does, never the room's centrepiece.
        with Horizontal(id="head-row"):
            yield Static(id="head")
            yield Static(id="since")
        yield Static(id="place")              # which place and version the screen shows: one quiet line
        yield Static(id="began")              # where the record began, when runs came before it: one quiet line
        with Horizontal(id="top"):
            with Vertical(id="tree-panel"):
                yield OptionList(id="tree-list")
            yield VerticalScroll(id="side-card")
        yield Static(id="tally")
        with Vertical(id="calls-panel"):
            yield OptionList(id="calls")
        with Vertical(id="pending-panel"):
            yield OptionList(id="pending")
        yield Static(id="quiet")
        yield Static(id="scope")              # the changed files nothing cites, counted, and what rb looks at: quiet
        yield Static(id="update")             # an upgrade installed while the screen is open: one quiet line
        with Vertical(id="live-panel"):
            yield Static(id="live-body")
        yield Footer()

    def on_mount(self) -> None:
        self._tick = 0
        self.query_one("#tree-panel").border_title = "CATALOGUE"
        self.query_one("#live-panel").border_title = "OBSERVED"
        self.query_one("#side-card").can_focus = False     # tab goes list to list, never into the preview
        self._load_places()
        if self.at_start and self._scan():                 # look once on opening, as rb status does
            self.home = self._home_view()
        self._fill()
        self._paint_update()
        self.set_interval(2.0, self._poll)
        self._stamp = self._mtimes()
        self.call_after_refresh(_inspected, self.root)   # the session's one inspection; repaints and polls never count

    # ------------------------------------------------------------ watching

    def _load_places(self) -> None:
        try:
            self._places = places_view(self.root, self.place)
        except Exception:  # noqa: BLE001 - the place line is a courtesy: the record still shows without it
            self._places = None
        line = place_line(self._places, self.place, self.at) if self._places is not None else ""
        widget = self.query_one("#place", Static)
        # away from where the screen started: in the warning colour, wrapped rather than cut
        widget.set_class(bool(line) and not self.at_start, "away")
        color = WARN if widget.has_class("away") else DIM
        widget.update(f"[{color}]{_esc(line)}[/{color}]" if line else "")
        widget.display = bool(line)

    def _shown_root(self) -> Optional[Path]:
        """The record shown, live: its root, whose log the screen watches; None for a past version, which never moves."""
        if self.at:
            return None
        if self.place is None:
            return self.root
        p = next((p for p in (self._places.places if self._places is not None else []) if p.id == self.place), None)
        return Path(p.root) if p is not None and p.root else None

    def _mtimes(self) -> tuple:
        root = self._shown_root()
        log = root / DIR / "log.jsonl" if root is not None else None
        return (log.stat().st_mtime if log is not None and log.exists() else 0,)

    def _poll(self) -> None:
        self._tick = getattr(self, "_tick", 0) + 1
        if self._tick % 15 == 0:                       # every 30s: whether an upgrade was installed meanwhile
            self._paint_update()
        moved = False
        if self._tick % 5 == 0 and self.at_start:      # every 10s: look at the repository too, where the screen started
            moved = self._scan()
        now = self._mtimes()
        if moved or now != getattr(self, "_stamp", None):
            self._stamp = now
            self.refresh_home()
        elif self._tick % 5 == 0 and self.at_start and getattr(self, "_scope", None):
            self._paint_scope(self.home, self._scope)        # a look that found nothing still says when

    def _paint_update(self) -> None:
        widget = self.query_one("#update", Static)
        line, self._started = update_line(self.root, getattr(self, "_started", None))
        widget.update(f"[{DIM}]{_esc(line)}[/{DIM}]" if line else "")
        widget.display = bool(line)

    def _scan(self) -> bool:
        try:
            s = ask(self.root, "scan")
            self._scope = (s.get("scope"), s.get("scope_short"))
            return bool(s["new"] or s["changed"] or s["gone"] or s["git_moved"] or s.get("tree_changed"))
        except Exception:  # noqa: BLE001 - watching must never take the screen down
            return False

    def refresh_home(self) -> None:
        self.home = self._home_view()
        self._load_places()                      # its counts move with the writes there
        if self.at_start:
            try:
                ask(self.root, "mark_seen")      # they are looking now; the next session's since starts here
            except Exception:  # noqa: BLE001
                pass
        self._fill()

    def switch(self, place: Optional[str], at: Optional[str]) -> None:
        """Show another place, or a past version of the record shown, by what rb lists; the record the screen started
        on is the place None. What rb cannot show is said, and the screen stays where it was."""
        this = next((p.id for p in (self._places.places if self._places is not None else []) if p.record == "this"), None)
        place = None if place == this else place
        was = (self.place, self.at)
        self.place, self.at = place, at
        try:
            self.home = self._home_view()
        except Exception as exc:  # noqa: BLE001 - a place that cannot be shown is said, never fatal
            self.place, self.at = was
            self.app.notify(str(getattr(exc, "message", None) or exc)[:160], severity="warning", timeout=8)
            return
        self._load_places()
        self._stamp = self._mtimes()
        self.refresh_bindings()
        self._fill()

    def action_places(self) -> None:
        try:
            info = places_view(self.root, self.place)
        except Exception as exc:  # noqa: BLE001
            self.app.notify(str(exc)[:120], severity="warning", timeout=8)
            return

        def go(choice: Optional[tuple]) -> None:
            if choice is None:
                return
            kind, ref = choice
            if kind == "place":
                self.switch(ref, None)
            else:
                self.switch(self.place, ref)
        self.app.push_screen(PlacesScreen(info, self.place, self.at), go)

    # ------------------------------------------------------------ painting

    @property
    def _wide(self) -> bool:
        return self.size.width >= NARROW

    def on_resize(self, _event) -> None:
        self._fill()

    def _paint_scope(self, h: Any, looked: Optional[tuple] = None) -> None:
        """The count of changed files nothing cites and the scope line, dim, `looked` (the line, and its short form) as
        the last look said it; on a short screen rb's short form of each, on one line, so the catalogue keeps its rows."""
        widget = self.query_one("#scope", Static)
        scope, short = looked or (h.get("scope"), h.get("scope_short"))
        widget.set_class(self.size.height < SHORT, "-short")
        if self.size.height < SHORT:
            said = [line for line in (h.get("uncited_short"), short) if line]
            widget.update("\n".join(f"[{DIM}]{_esc(line)}[/{DIM}]" for line in said))
        else:
            said = [line for line in (h.get("uncited_line"), scope) if line]
            widget.update("\n".join(f"[{DIM}]{_esc(line)}[/{DIM}]" for line in said))
        widget.display = bool(said)

    def _fill(self) -> None:   # not named _render: that is Textual's own leaf-paint hook
        h = self.home
        self._asks = list(h.found)
        wide = self._wide
        if self.has_class("-narrow") == wide:
            # no card beside the catalogue below NARROW, and the catalogue takes its room: lay the rows again once it has
            self.set_class(not wide, "-narrow")
            self.call_after_refresh(self._fill)
        self.query_one("#head", Static).update(Text(h.title, style="bold", no_wrap=True, overflow="ellipsis"))
        self.query_one("#since", Static).update(f"[{DIM}]{_esc(h.since)}[/{DIM}]" if h.since else "")
        began = self.query_one("#began", Static)
        began.update(f"[{DIM}]{_esc(h.get('began') or '')}[/{DIM}]" if h.get("began") else "")
        began.display = bool(h.get("began"))
        muted = h.get("muted", ())
        tally = tally_text(h.tally, muted)
        if len(tally) > self.size.width:        # one line as wide as the screen: past it, every count in short words
            tally = tally_text(h.tally, muted, short=True)
        self.query_one("#tally", Static).update(tally if h.tally else "")
        calls_panel = self.query_one("#calls-panel")
        calls_panel.border_title = f"YOUR CALL ({len(self._asks)})"
        calls_panel.border_subtitle = h.weak_identity or ""      # a weak name is said where y acts
        calls = self.query_one("#calls", OptionList)
        calls.clear_options()
        quiet = self.query_one("#quiet", Static)
        # the calls rb status lists for the user, under what rb found or in its place; green only while nothing waits
        # on you and every met rule is green
        colour = ACCENT if h.get("waiting") or h.get("pending") else DIM if muted else GOOD
        quiet.update(f"[{colour}]{_esc(h.quiet)}[/{colour}]" if h.quiet else "")
        quiet.display = bool(h.quiet)
        self._paint_scope(h, getattr(self, "_scope", None) if self.at_start else None)
        if self._asks:
            calls_panel.display = True
            width = min(max(len(a.title) for a in self._asks) + 4, 44)
            for a in self._asks:
                t = Text(no_wrap=True, overflow="ellipsis", end="")
                t.append(a.title.ljust(max(width, len(a.title) + 2)), style=ACCENT)     # what rb itself brings you, then a gap
                t.append(a.hint, style=DIM)
                calls.add_option(Option(t, id=a.request_id))
            if calls.highlighted is None:
                calls.highlighted = 0
            focused = self.focused
            arrived = not getattr(self, "_had_asks", False)
            if arrived or focused is None or getattr(focused, "id", None) not in ("calls", "tree-list", "pending"):
                calls.focus()          # a call arriving takes focus; a mere repaint never steals it
        else:
            calls_panel.display = False
        # what waits for a confirmation: a quiet group of its own, never one of the calls above, and it takes no focus
        pending_panel = self.query_one("#pending-panel")
        rows = list(h.get("pending", []))
        pending = self.query_one("#pending", OptionList)
        keep_pending = pending.highlighted
        pending.clear_options()
        pending_panel.display = bool(rows)
        if rows:
            pending_panel.border_title = f"TO CONFIRM ({len(rows)})"
            pending_panel.border_subtitle = "c confirms the highlighted one" if self.at_start else ""
            for row in rows:
                label = {"proposed": "proposed, not made", "gate": "a gate, not confirmed", "design": "a design, not confirmed"}.get(row.kind, "a past call, not confirmed")
                t = Text(no_wrap=True, overflow="ellipsis", end="")
                t.append(f"{label}  ", style=DIM)
                t.append(row.text, style=DIM)
                pending.add_option(Option(t, id=f"p:{row.id}"))
            # a refresh's highlight is no move of the user's: it never redraws the card beside the lists
            with self.prevent(OptionList.OptionHighlighted):
                pending.highlighted = min(keep_pending, len(rows) - 1) if keep_pending is not None else 0
        self._pending = rows
        self._settle_in_view(rows)
        tree_panel = self.query_one("#tree-panel")
        tree_panel.border_subtitle = h.get("open_line") or ""
        tree = self.query_one("#tree-list", OptionList)
        keep = tree.highlighted
        keep_id = tree.get_option_at_index(keep).id if keep is not None and keep < tree.option_count else None
        tree.clear_options()
        self._object_refs = {}
        occurrences: dict[str, int] = {}
        measured = tree.size.width
        w_tree = measured - 2 if measured > 30 else self.size.width - 10
        width = w_tree - 1           # a row's columns; the last stays free for the scrollbar
        gutters = [f"{'  ' * r.depth}{r.kind}" for r in h.tree]
        kind_w = max(map(len, gutters), default=0) + 2      # the kind column: as wide as this tree's widest
        laid = []
        for r, gutter in zip(h.tree, gutters):
            head = kind_w + len(r.id) + 2
            floor = min(len(r.label), 16)
            # the standing takes what it needs before the title is cut; the title keeps 16 columns, or a shorter one all
            laid.append((r, gutter, head, floor, max(width - head - floor - 1, 1)))
        short = any(len(r.standing) > room for r, *_, room in laid)    # one row short of room: every row counts short
        for r, gutter, head, floor, room in laid:
            # counts that do not fit beside the title's floor take what they need of it, down to its last column
            standing = standing_text(r.standing, r.get("muted", ()), short=short, room=room, spare=width - head - 2 - room)
            left = width - head - len(standing)
            room_title = max(left - 2, floor) if left - 1 >= floor else max(left - 1, 1)
            label = r.label if len(r.label) <= room_title else r.label[: room_title - 1] + "…"
            pad = " " * max(width - head - len(label) - len(standing), 1)
            t = Text(no_wrap=True, overflow="ellipsis", end="")
            t.append(f"{gutter:<{kind_w}}", style=DIM)
            t.append(r.id, style=TEAL)
            t.append("  ")
            t.append(label, style="bold" if r.kind == "question" else ("italic" if r.kind == "resource" else ""))
            t.append(pad)
            t.append(standing)
            occurrence = occurrences.get(r.id, 0) + 1
            occurrences[r.id] = occurrence
            option_id = f"o:{r.id}" + (f":{occurrence}" if occurrence > 1 else "")
            self._object_refs[option_id] = r.id
            tree.add_option(Option(t, id=option_id))
        if tree.option_count:
            tree.highlighted = (list(self._object_refs).index(keep_id) if keep_id in self._object_refs
                                else min(keep, tree.option_count - 1) if keep is not None else 0)
        self.query_one("#live-panel").border_subtitle = ""
        live = self.query_one("#live-body", Static)
        w_right = max(self.size.width - 12, 40)
        lines = []
        for entry in h.live:
            at, rest = (entry[:5], entry[6:]) if len(entry) > 6 else ("", entry)
            body = rest if len(rest) <= w_right - 8 else rest[: w_right - 9] + "…"
            lines.append(f"[{TEAL}]{_esc(at)}[/{TEAL}] [{DIM}]{_esc(body)}[/{DIM}]")
        for line in h.unplaced:
            body = line if len(line) <= w_right - 12 else line[: w_right - 13] + "…"
            lines.append(f"[{ACCENT}]unpl.[/{ACCENT}] [{DIM}]{_esc(body)}[/{DIM}]")
        live.update("\n".join(lines) if lines else f"[{DIM}]quiet[/{DIM}]")
        if wide:
            # The preview follows the hand: the focused list's highlight fills the side card, and
            # with nothing focused the work is what shows, not the queue.
            focused_id = getattr(self.focused, "id", None)
            if focused_id == "pending" and self._pending:
                self._show_side(f"p:{self._pending[min(pending.highlighted or 0, len(self._pending) - 1)].id}")
            elif self._asks and (focused_id == "calls" or not tree.option_count):
                self._show_side(self._asks[min(calls.highlighted or 0, len(self._asks) - 1)].request_id)
            elif tree.option_count:
                if focused_id not in ("calls", "tree-list"):
                    tree.focus()
                opt = tree.get_option_at_index(tree.highlighted or 0)
                self._show_side(opt.id)
            else:
                self._show_side(None)
        self._had_asks = bool(self._asks)

    def _settle_in_view(self, rows: list) -> None:
        """A write that changed what the user was shown (a design's or a gate's id names what it reads) leaves nothing c
        may confirm until it is shown again; a full-screen card of it shows it as it reads now, or closes."""
        ids = [r.id for r in rows]
        if self._in_view is not None and self._in_view not in ids:
            self._was_in_view, self._in_view = self._in_view, None
        top = self.app.screen if self.app is not None else None
        if isinstance(top, PendingScreen) and top.row.id not in ids:
            same = next((r for r in rows if r.kind == top.row.kind and r.id.split("@")[0] == top.row.id.split("@")[0]), None)
            if same is not None:
                top.show(same)
            else:
                self.app.pop_screen()

    def _show_pending(self, row: View) -> None:
        """Show one that waits for a confirmation: beside the list, or full screen where no card sits beside it."""
        if self._wide:
            self._show_side(f"p:{row.id}")
            self._in_view = row.id if self._side_for == f"p:{row.id}" else None
        else:
            self.app.push_screen(PendingScreen(row, self.at_start, self._confirm_shown))
            self._in_view = row.id

    def _show_side(self, ref: Optional[str]) -> None:
        if ref and ref.startswith("o:") and ref not in self._object_refs:
            return                                  # an event queued for a row removed by a refresh
        pane = self.query_one("#side-card", VerticalScroll)
        if not pane.is_attached:
            return                                  # an event queued as the screen closes: nothing left to draw on
        panel_title = ""
        try:
            pane.remove_children()
            self._side_for = None
            if ref is None:
                pane.mount(Static(f"[{DIM}]The record is quiet. A call opens here when one arrives.[/{DIM}]"))
            elif ref.startswith("p:"):
                row = next((r for r in getattr(self, "_pending", []) if f"p:{r.id}" == ref), None)
                if row is None:
                    return
                panel_title = "TO CONFIRM"
                pane.mount(*pending_widgets(row, self.at_start))
                self._side_for = ref
            elif ref.startswith("o:"):
                c = self._object_card(self._object_refs[ref])
                self._side_for = ref
                panel_title = c.who_line.split(" · ")[0].upper()
                pane.mount(*build_card_widgets(c))
            else:
                c = self._found_card(ref)
                self._side_for = ref
                panel_title = "FOUND BY RB"
                pane.mount(*build_card_widgets(c))
        except Exception:  # noqa: BLE001 - a half-readable call still leaves the screen standing
            pane.mount(Static(f"[{DIM}]this cannot be drawn right now; enter opens it[/{DIM}]"))
        pane.border_title = panel_title

    # ------------------------------------------------------------ acting

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        # while the group of what waits has focus, the card beside it stays on its row, whatever a refresh highlights
        if getattr(self.focused, "id", None) == "pending" and getattr(event.option_list, "id", None) != "pending":
            return
        if event.option_id and self._wide:
            self._show_side(event.option_id)
            if event.option_id.startswith("p:") and self._side_for == event.option_id:
                self._in_view = event.option_id[2:]

    def on_descendant_focus(self, event: events.DescendantFocus) -> None:
        # the group of what waits takes focus with its highlighted row's card beside it, so c acts on what is in view
        rows = getattr(self, "_pending", [])
        if getattr(event.widget, "id", None) == "pending" and rows and self._wide:
            self._show_pending(rows[min(event.widget.highlighted or 0, len(rows) - 1)])

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if not event.option_id:
            return
        if event.option_id.startswith("o:"):
            subject = self._object_refs.get(event.option_id)
            if subject is not None:
                self.app.push_screen(ObjectScreen(self.root, subject, self.place, self.at))
        elif event.option_id.startswith("p:"):
            # c confirms it; enter opens nothing more than the card beside it, or that card where none sits beside
            row = next((r for r in getattr(self, "_pending", []) if f"p:{r.id}" == event.option_id), None)
            if row is not None and not self._wide:
                self._show_pending(row)
        else:
            self.app.push_screen(card_screen(self.root, event.option_id, self.place, self.at))

    def _highlighted(self) -> Optional[View]:
        calls = self.query_one("#calls", OptionList)
        if calls.highlighted is not None and self._asks:
            return self._asks[min(calls.highlighted, len(self._asks) - 1)]
        return None

    def action_open(self) -> None:
        tree = self.query_one("#tree-list", OptionList)
        if tree.has_focus and tree.highlighted is not None:
            opt = tree.get_option_at_index(tree.highlighted)
            subject = self._object_refs.get(opt.id)
            if subject is not None:
                self.app.push_screen(ObjectScreen(self.root, subject, self.place, self.at))
            return
        pending, rows = self.query_one("#pending", OptionList), getattr(self, "_pending", [])
        if pending.has_focus and rows:
            if not self._wide:          # no card beside the list: the call's card, full screen
                self._show_pending(rows[min(pending.highlighted or 0, len(rows) - 1)])
            return
        item = self._highlighted()
        if item:
            self.app.push_screen(card_screen(self.root, item.request_id, self.place, self.at))

    def action_yes(self) -> None:
        item = self._highlighted()
        if item and self.at_start:
            try:
                prompt_yes(self, self.root, found_card(self.root, item.request_id), self.refresh_home)
            except Exception as exc:  # noqa: BLE001
                self.app.notify(str(exc)[:120], severity="warning", timeout=8)

    def action_no(self) -> None:
        item = self._highlighted()
        if item and self.at_start:
            try:
                prompt_no(self, self.root, found_card(self.root, item.request_id), self.refresh_home)
            except Exception as exc:  # noqa: BLE001
                self.app.notify(str(exc)[:120], severity="warning", timeout=8)

    def action_confirm(self) -> None:
        """c on what waits for a confirmation: rb confirm <id> (a gate's <experiment>@<hash>) for the highlighted one, in
        the user's own environment."""
        rows = getattr(self, "_pending", [])
        if not rows or not self.at_start:
            return
        pending = self.query_one("#pending", OptionList)
        row = rows[min(pending.highlighted or 0, len(rows) - 1)]
        name = {"gate": f"{row.id.split('@')[0]}'s gate", "design": f"{row.id.split('@')[0]}'s design"}.get(row.kind, row.id)
        if getattr(self.focused, "id", None) != "pending" or self._in_view != row.id:
            # c confirms only what the user has in view: this press takes them to the group and shows the highlighted
            # one, beside the list or, where no card sits beside it, full screen. One a write changed since it was shown
            # is shown again, as it reads now, before c confirms it; rb confirm refuses a key changed in between.
            was = self._was_in_view
            changed = was is not None and was != row.id and was.split("@")[0] == row.id.split("@")[0] and "@" in row.id
            pending.focus()
            self._show_pending(row)
            if changed:
                self.app.notify(f"{name} changed since it was shown: c again confirms it as it reads now.", timeout=8)
            else:
                self.app.notify(f"c again confirms the highlighted {CONFIRM_NOUNS.get(row.kind, 'call')}.", timeout=6)
            self._was_in_view = None
            return
        self._confirm_shown(row)

    def _confirm_shown(self, row: View) -> None:
        """rb confirm <id> for one the user has in view, in the user's own environment."""
        name = {"gate": f"{row.id.split('@')[0]}'s gate", "design": f"{row.id.split('@')[0]}'s design"}.get(row.kind, row.id)
        run_settle(self.app, self.here, ["confirm", row.id], f"{name} confirmed", self.refresh_home)

    def action_open_items(self) -> None:
        st = ask(self.root, "status", shown=True, _place=self.place, _at=self.at)       # drawn as every screen draws text
        items = [i["what"] for i in st.get("open", []) if i.get("who") != "human"]     # all but the user's calls
        activity = []
        try:
            for e in ask(self.root, "journal", last=12, _place=self.place, _at=self.at):
                at, op = str(e.get("at", ""))[11:16], e.get("op")
                if op == "file":
                    activity.append(f"{at}  {e.get('change')}  {e.get('path')}")
                elif op == "git":
                    activity.append(f"{at}  git  commit {(e.get('commit') or 'none')[:10]}" + (", dirty" if e.get("dirty") else ""))
                elif op == "run":
                    activity.append(f"{at}  ran  {e.get('command')} (exit {e.get('exit')})")
        except Exception:  # noqa: BLE001
            pass
        self.app.push_screen(OpenItemsScreen(items, activity))

    def action_refresh(self) -> None:
        if self.at_start:
            self._scan()         # r means reconcile: look at the repository, then repaint what changed
        self.refresh_home()


class RBApp(App):
    TITLE = "rb"
    CSS = f"""
    Screen {{ padding: 0 1; }}
    #head-row {{ height: 1; margin-top: 1; }}
    #head {{ width: 1fr; text-wrap: nowrap; text-overflow: ellipsis; }}
    #since {{ width: auto; margin-left: 2; }}
    #began {{ height: auto; text-wrap: nowrap; text-overflow: ellipsis; }}
    #place {{ height: auto; text-wrap: nowrap; text-overflow: ellipsis; }}
    #place.away {{ text-wrap: wrap; text-overflow: fold; }}
    #tally {{ height: 1; margin-bottom: 1; text-wrap: nowrap; text-overflow: ellipsis; }}
    #top {{ height: 1fr; }}
    #tree-panel {{ width: 58%; min-width: 40; height: 100%; border: round #3d4450; border-title-color: {ACCENT};
                   border-subtitle-color: #8b949e; padding: 0 1; }}
    #tree-list {{ height: 1fr; border: none; background: transparent; scrollbar-size-vertical: 1;
                  scrollbar-color: #3d4450; scrollbar-background: transparent; }}
    #tree-list > .option-list--option-highlighted {{ background: $boost; }}
    #side-card {{ width: 1fr; height: 100%; margin-left: 1; border: round #3d4450; border-title-color: {ACCENT}; padding: 0 2;
                  scrollbar-size-vertical: 1; scrollbar-color: #3d4450; scrollbar-background: transparent; }}
    HomeScreen.-narrow #tree-panel {{ width: 100%; }}
    HomeScreen.-narrow #side-card {{ display: none; }}
    #calls-panel {{ height: auto; max-height: 8; margin-top: 1; border: round #4d4636; border-title-color: {ACCENT};
                    border-subtitle-color: {WARN}; padding: 0 1; }}
    #calls {{ height: auto; max-height: 6; border: none; background: transparent; }}
    #calls > .option-list--option-highlighted {{ background: $boost; }}
    #pending-panel {{ height: auto; max-height: 7; margin-top: 1; border: round #3d4450; border-title-color: #8b949e;
                      border-subtitle-color: #8b949e; padding: 0 1; }}
    #pending {{ height: auto; max-height: 5; border: none; background: transparent; }}
    #pending > .option-list--option-highlighted {{ background: $boost; }}
    #quiet {{ height: auto; padding: 0 1; margin-top: 1; }}
    #scope {{ height: auto; padding: 0 1; }}
    #update {{ height: auto; padding: 0 1; }}
    #scope.-short {{ text-wrap: nowrap; text-overflow: ellipsis; }}
    #live-panel {{ height: auto; max-height: 6; margin-top: 1; border: round #3d4450;
                   border-title-color: {ACCENT}; padding: 0 1; }}
    #card {{ padding: 1 0; }}
    .runline {{ padding: 0 0 0 2; }}
    .runline:hover {{ background: $boost; }}
    .card-title {{ margin-top: 1; }}
    .lead {{ margin-top: 1; }}
    .look {{ margin-top: 1; }}
    .block {{ margin-left: 2; }}
    .bar {{ height: auto; margin-top: 1; }}
    .bar-id {{ width: 11; }}
    .bar-body {{ width: 1fr; }}
    .chip-row {{ height: auto; margin-top: 1; }}
    .chip {{ width: auto; padding: 0 2; margin-right: 2; border: round #4d4636; }}
    .chip:hover {{ background: $boost; border: round {ACCENT}; }}
    #reason-box {{ max-width: 76; width: 100%; height: auto; padding: 1 2; border: tall {ACCENT}; background: $surface; }}
    #reason-box Input {{ border: none; background: $boost; padding: 0 1; }}
    Reason {{ align: center middle; }}
    PlacesScreen {{ align: center middle; }}
    #places-box {{ max-width: 110; width: 100%; height: auto; max-height: 90%; background: $surface; padding: 0 1; }}
    #where-panel, #versions-panel {{ height: auto; border: round #3d4450; border-title-color: {ACCENT}; padding: 0 1; }}
    #where, #versions {{ height: auto; max-height: 12; border: none; background: transparent; }}
    #where-detail {{ height: auto; padding: 0 1; }}
    #where > .option-list--option-highlighted, #versions > .option-list--option-highlighted {{ background: $boost; }}
    """

    def __init__(self, root: Path, here: Optional[Path] = None) -> None:
        super().__init__()
        self.root, self.here = root, here
        self.failed: Optional[RBError] = None

    def on_mount(self) -> None:
        try:
            home = HomeScreen(self.root, self.here)
        except RBError as err:      # a record rb cannot read: said as rb says it, once the terminal is the user's again
            self.failed = err
            self.exit(return_code=err.exit_code)
            return
        self.push_screen(home)

    def _handle_exception(self, error: Exception) -> None:
        """What no handler caught, wherever the screen met it (a repaint after a look, r, a card): a record rb cannot
        read, as a hand edit or a newer rb leaves it, closes the screen and is said as rb says it, as on opening; anything
        else is Textual's to report, with its code."""
        cause: Optional[BaseException] = error
        for _ in range(8):          # the error, or one it was raised from
            if cause is None or isinstance(cause, RBError):
                break
            cause = cause.__cause__ or cause.__context__
        if isinstance(cause, RBError):
            if self.failed is None:
                self.failed = cause
            self.exit(return_code=cause.exit_code)
            return
        super()._handle_exception(error)


# what rb ui says in an agent session (cmd_ui, rust/src/cmds/info.rs), said the same when the screen is started here
AGENT_SESSION = "rb ui is an interactive screen, and this is an agent session: rb status prints the same state."


def main(argv: Optional[list] = None) -> int:
    """`python -m rabbit_brain.tui <root> [<here>]`: what rb runs to open the screen on the record at <root>, from the
    checkout <here> the user is in (by default the record's own)."""
    argv = list(sys.argv[1:] if argv is None else argv)
    for stream in (sys.stdout, sys.stderr):
        try:    # a redirected stream on Windows is in the system's code page, which has no arrow: say it without one
            stream.reconfigure(errors="replace")
        except Exception:  # noqa: BLE001 - a stream that cannot be set stays as it is
            pass
    return run_ui(Path(argv[0]) if argv else Path.cwd(), Path(argv[1]) if len(argv) > 1 else None)


def refused(err: RBError) -> int:
    """What rb ui says when it cannot show the record, as every rb command says a failure: its message, then the fix."""
    print(f"rb ui: {err.message}", file=sys.stderr)
    if err.fix:
        print(f"  → {err.fix}", file=sys.stderr)
    return err.exit_code


def run_ui(root: Path, here: Optional[Path] = None) -> int:
    try:
        who = ask(root, "who")      # rb answers, and knows who is looking, before the screen takes the terminal
        if who.get("runtime") or who.get("agent_session"):
            raise RBError("E_USAGE", AGENT_SESSION)     # the user's controls are not an agent's to press
        ask(root, "project")        # a record to show, asked before the screen writes its seen marker
    except RBError as err:
        return refused(err)
    app = RBApp(root, here)
    app.run()
    # a screen that ended on an error ends rb ui with its code, never 0
    return refused(app.failed) if app.failed is not None else (app.return_code or 0)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
