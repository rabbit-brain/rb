"""The user's screen: `rb` in their own terminal, next to the agent's tab.

A control room, not a feed: named panels whose rows hold still while their standings change.
YOUR CALL and the catalogue on the left; the highlighted call's full card on the right, live;
what rb observed along the bottom. y and n act on the highlighted call from anywhere. On a narrow
terminal the panels stack and enter opens the card full screen. A call here is something rb found
in the repository and not in the record: y records it, n leaves it out. A call an agent session
proposed, or a past call it mentioned, waits in a quiet group of its own until c confirms the
highlighted one, through the same rb confirm the user would type. w lists the places rb can show (each worktree
of the clone, by its branch and path) and then the versions of the record shown: enter shows one, read only, and
escape goes back. y, n, c and d act only on the record the screen started on; elsewhere a call's card gives the one
command that confirms it there.

This is a renderer. Everything it shows is asked of rb (the home view and each card, as rb works
them out: `_rb.call`), and everything it does runs through the same code path as the CLI: y runs
`rb run add ...` in a subprocess, in the user's own environment, so what gets recorded, and as
whom, is decided by rb, never by this screen.
"""
from __future__ import annotations

import re
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
from . import clip
from . import tui_panels as panels
from . import tui_parts as parts
from .errors import RBError
from .evidence import Cite, Evidence, card_widgets, question_widgets, subject_of
from .tui_card import (CardData, CardView, STATE_MARK, STATE_WORD, chip_text, claim_text, set_words, crumb_of, outline_lines, preview_lines,
                       question_preview, resource_preview, run_rows, wrap_words)

DIR = ".rb"
DIM = "grey62"
GOOD = "#7ee787"        # a rule met, and nothing less
BAD = "#ff7b72"         # a claim's rule missed, not reproduced
WARN = "#f0883e"        # a blocker with a named fix
ACCENT = "#e3b341"      # the gold thread: panel titles, and what rb itself brings you
TEAL = "#39c5cf"        # ids and timestamps
NARROW = 120            # three panes from here (UX5 2.3); below it the catalogue is a key away and a card opens over the story
# the lists a repaint leaves the focus on: the user moved it there
FOCUS_KEEPS = ("calls", "tree-list", "files-list", "chart-list", "decisions-list", "others-list")
SHORT_HOME = 30         # below this the story keeps to its share of the rows and the catalogue keeps the rest
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


def home_view(root: Path, since_baseline: Optional[int] = None, place: Optional[str] = None, at: Optional[str] = None,
              story: Optional[str] = None) -> View:
    """The home; with `story`, a top-level question's id, that question as the Overview's story (R21)."""
    h = ask(root, "home", since_baseline=since_baseline, story=story or "", _place=place, _at=at)
    set_words(((h.get("ui") or {}).get("states")) or [])
    return h


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


def card_model(root: Path, subject: str, place: Optional[str] = None, at: Optional[str] = None) -> Optional[View]:
    """rb's card of `subject` as fields and cites (UX5 2.1), or None where rb has no card by that name."""
    try:
        return ask(root, "card_model", subject=subject, _place=place, _at=at)
    except RBError:
        return None


def question_overview(root: Path, question: str, place: Optional[str] = None, at: Optional[str] = None) -> View:
    """The Overview of one question, its sub-questions' checks with its own: what its card draws."""
    return ask(root, "overview", question=question, _place=place, _at=at)


def places_view(root: Path, place: Optional[str] = None, detail: bool = False) -> View:
    """The places the screen can show, and the versions of the record of `place` (by default the one it started on);
    with `detail`, each place's evidence, which only WHERE prints."""
    return ask(root, "places", place=place or "", detail=detail)


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


def place_line(info: View, place: Optional[str], at: Optional[str], width: int = 0) -> str:
    """Which place and version the screen shows, in rb's words. The place it started on is its face, as rb made it: its
    branch ("no branch" when detached, its folder's name only where git cannot say), then how its checkout holds the
    record; within `width` the branch gives way with an ellipsis, never the words. A past version is named by the day
    it was committed. Away from where the screen started, it says it is read only and how to go back before what rb
    says of the place."""
    shown = next((p for p in info.places if p.id == place), None) or next((p for p in info.places if p.record == "this"), None)
    if shown is None:
        return ""
    back = f"esc: back to {back_to(info)}"
    if at:
        version = next((v for v in info.versions if v.sha == at), None)
        if version is not None:
            return f"{version.get('shown_day') or version.get('shown_short') or version.shown} · {back}"
    face = shown.get("face") or ""
    if shown.record == "this" and face:
        return fit_face(face, (shown.get("checkout") or {}).get("said") or "", width)
    if len(info.places) <= 1:
        # a single checkout by its branch, "no branch" on a detached HEAD, its folder's name only where git cannot say
        # (rb names it so: its checkout's name): w lists it with its whole path
        return (shown.get("checkout") or {}).get("name") or shown.branch or PurePosixPath(shown.path).name
    return short(shown) if shown.record == "this" else f"{short(shown)} · read only · {back} · {shown.said}"


def fit_face(face: str, words: str, width: int) -> str:
    """The face within `width` cells: the name before the words gives way first, with an ellipsis; the words never."""
    if width <= 0 or len(face) <= width or not words or not face.endswith(f" · {words}"):
        return face
    name, room = face[: -len(words) - 3], width - len(words) - 3
    return f"{name[: room - 1]}…{face[len(name):]}" if room >= 2 else words


def back_to(info: View) -> str:
    """Where escape goes back to: the place the screen started on, by its branch (else as it is named)."""
    start = next((p for p in info.places if p.record == "this"), None)
    return "" if start is None else (start.branch or short(start))


def utc_minute(at: str) -> str:
    """"09:14" of "2026-11-08T10:14:03+01:00": the minute in UTC."""
    from datetime import datetime, timezone
    try:
        t = datetime.fromisoformat(at.replace("Z", "+00:00"))
    except ValueError:
        return at[11:16]
    return (t.astimezone(timezone.utc) if t.tzinfo else t).strftime("%H:%M")


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


# rb's rule words as every screen says them (strings.rs plain_word, which rb status, rb context, the page and rb open
# say): the noun is "rule", undetermined reads "can't tell yet", untested "not tested". Display only: the colours and
# every key still read rb's own words.
_PLAIN = {"criteria met": ("rule met", "rules met"), "criterion met": ("rule met", "rules met"),
          "criteria not met": ("rule not met", "rules not met"), "criterion not met": ("rule not met", "rules not met"),
          "criteria undetermined": "can't tell yet", "criterion undetermined": "can't tell yet",
          "undetermined": "can't tell yet", "criteria untested": "not tested", "criterion untested": "not tested",
          "untested": "not tested", "gate undetermined": "gate, can't tell yet", "gate untested": "gate not tested",
          "estimate meets criterion": "estimate meets the rule",
          "recorded values meet criterion": "recorded values meet the rule",
          "point only": "no interval", "No interval": "no interval",
          "Retrospective": "rule written after the results", "retrospective": "rule written after the results"}

# A rule word as a tally says it, short (views.rs short_rule): "met", "not met", "can't tell yet", "not tested".
_SHORT_RULE = {"meets": "met", "misses": "not met", "undetermined": "can't tell yet", "untested": "not tested"}

RULES_LEAD = "Rules: "


def plain_word(phrase: str) -> str:
    """One part of rb's standing as the screens say it: '2 criteria met' is '2 rules met', '1 criterion undetermined'
    '1 can't tell yet'. A leading count is kept; any other phrase ('not reproduced', 'gate missed') is as rb gives it."""
    head, _, rest = phrase.partition(" ")
    n, rest = (head, rest) if head.isdigit() else (None, phrase)
    said = _PLAIN.get(rest, rest)
    if isinstance(said, tuple):
        said = said[0] if n in (None, "1") else said[1]
    return f"{n} {said}" if n is not None else said


def tally_word(word: str, n) -> str:
    """One count of the tally as the page and rb status say it: ('meets', 1) is '1 met', ('undetermined', 1) '1 can't
    tell yet', ('gate undetermined', 2) '2 gate, can't tell yet'."""
    return plain_word(f"{n} {_SHORT_RULE.get(word, word)}")


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
    """A count of criteria in the catalogue's short words, as a tally says it: '2 criteria not met' is '2 not met',
    '1 criterion undetermined' '1 can't tell yet'. Anything else as the screens say it (plain_word)."""
    count, _, rest = part.partition(" ")
    noun, _, word = rest.partition(" ")
    if count.isdigit() and noun in ("criterion", "criteria") and word in ("met", "not met", "undetermined", "untested"):
        return tally_word({"met": "meets", "not met": "misses"}.get(word, word), count)
    return plain_word(part)


def said_part(part: str, short: bool = False) -> str:
    """One part of rb's standing as a catalogue row draws it: in short count words if `short`, else plain."""
    return _short_count(part) if short else plain_word(part)


def said_lead(line: str) -> str:
    """rb's clauses (split at '; ') as the screens say them: a clause that is a rule word ('criterion untested') in
    plain words, and one that leads with one ('criterion met: estimate 0.785 ≥ 0.7') with its lead so ('rule met: ...',
    views.rs said_lead), the rest as rb gives it. Only rb's own words go through it, never text someone wrote."""
    out = []
    for clause in line.split("; "):
        end = "." if clause.endswith(".") else ""
        bare = clause[:-1] if end else clause
        lead, colon, rest = bare.partition(": ")
        if plain_word(bare) != bare:
            out.append(plain_word(bare) + end)
        elif colon and plain_word(lead) != lead:
            out.append(f"{plain_word(lead)}: {rest}{end}")
        else:
            out.append(clause)
    return "; ".join(out)


def said_sentence(sentence: str, statement: str) -> str:
    """A finding's sentence on a card ('<statement>. <rb's clauses>'): the statement as written, rb's clauses after it
    as the screens say them (said_lead). A sentence that does not open with the statement is left as rb gives it."""
    head = statement.strip()
    head = head if head.endswith((".", "?", "!", ":")) else head + "."
    if not head or head == "." or not sentence.startswith(head + " "):
        return sentence
    return f"{head} {said_lead(sentence[len(head) + 1:])}"


def said_standing(standing: str, short: bool = False) -> str:
    """A whole standing as a catalogue row draws it, every part said as the screens say it."""
    return " · ".join(said_part(part, short) for part in standing.split(" · "))


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
            out.append(said_part(part, short), style=_colour(part, muted))
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


def tally_text(tally, muted=()) -> Text:
    """The line counting the record's rules by their reading, as the page and rb status say it ('Rules: 1 met · 1 can't
    tell yet'), each count coloured as rb's own words for it are. Its words are short already."""
    out = Text(no_wrap=True, overflow="ellipsis")
    out.append(RULES_LEAD, style=DIM)
    for i, (word, n) in enumerate(tally):
        if i:
            out.append(" · ", style=DIM)
        out.append(tally_word(word, n), style=_colour(word, muted))
    return out


# ---------------------------------------------------------------- the Overview in text figures (UX5 1.5)

STATE_COLOUR = {"meets": GOOD, "misses": BAD, "undetermined": WARN, "untested": DIM}

# the palettes (DESIGN-UX4 1.8): the dark one the screen was drawn in, and one for light themes, which read on white
DARK = {"DIM": "grey62", "GOOD": "#7ee787", "BAD": "#ff7b72", "WARN": "#f0883e", "ACCENT": "#e3b341", "TEAL": "#39c5cf"}
LIGHT = {"DIM": "#57606a", "GOOD": "#1a7f37", "BAD": "#cf222e", "WARN": "#9a6700", "ACCENT": "#8250df", "TEAL": "#0969da"}


def set_palette(dark: bool) -> None:
    """Draw in the palette for the theme: every colour the screen names is one of these, read when a line is drawn."""
    pal = DARK if dark else LIGHT
    globals().update(pal)
    STATE_COLOUR.update({"meets": pal["GOOD"], "misses": pal["BAD"], "undetermined": pal["WARN"], "untested": pal["DIM"]})


# the ASCII forms of rb's own marks (DESIGN-UX4 1.4): where RB_ASCII=1 or the terminal's encoding is not UTF-8. One
# character for one, so a line's colours stay where they are; the author's and the files' words are never changed
ASCII = str.maketrans({"■": "#", "▲": "^", "◧": "%", "□": ".", "●": "*", "○": "o", "░": "~", "┆": ":", "├": "|", "┤": "|",
                       "─": "-", "‾": "_", "✓": "+", "✗": "x", "┃": "|"})


def ascii_wanted() -> bool:
    import os
    if os.environ.get("RB_ASCII", "") not in ("", "0"):
        return True
    enc = (getattr(sys.stdout, "encoding", None) or "utf-8").lower().replace("-", "")
    return enc not in ("utf8", "utf8sig")


def glyphs(t: Any) -> Any:
    """`t` (a Text or a str) in rb's marks, or their ASCII forms where the terminal asks for them."""
    if not ascii_wanted():
        return t
    if isinstance(t, str):
        return t.translate(ASCII)
    out = t.copy()
    out.plain = t.plain.translate(ASCII)
    return out
STATE_MARK = {"meets": "■", "misses": "▲", "undetermined": "◧", "untested": "□"}


def check_colour(state: Optional[str], finding: Any = None) -> str:
    """A check's square in the colour every surface gives its reading: green only for a rule met with nothing wrong
    with what it rests on (the finding's `good`), a gate neutral when met and a warning when missed (a gate is policy,
    not a finding: D4)."""
    if finding is not None and finding.get("role") == "gate":
        return WARN if state == "misses" else DIM
    if state == "meets" and finding is not None and not finding.get("good"):
        return DIM
    return STATE_COLOUR.get(state or "", DIM)


def cut_words(text: str, room: int) -> str:
    """`text` in `room` columns, cut only between words, with '…' where it is cut; a first word longer than the room is
    the one thing cut inside a word."""
    if len(text) <= room:
        return text
    out = ""
    for word in text.split():
        if len(out) + len(word) + (1 if out else 0) + 1 > room:
            break
        out = f"{out} {word}" if out else word
    return (out or text[: max(room - 1, 1)]) + "…"


ROW_STATES = (("untested", "□"), ("undetermined", "◧"), ("unresolved", "◧"), ("can't tell", "◧"),
              ("not met", "▲"), ("not reproduced", "▲"), ("missed", "▲"), ("met", "■"), ("reproduced", "■"))


# each mark's word, as the strip says it beside its squares: a mark never stands alone on the screen (no glossary,
# show instead: the orchestrator, Nov 12 20:35 +08)
MARK_WORD = STATE_WORD          # each mark's word, the bridge's (home.ui.states)


def row_marks(standing: str, muted=()) -> Text:
    """A catalogue row's counts as the strip says them: each state's mark, count and word, ■ 4 passed  ▲ 1 failed, in
    the colour rb gives the words they stand for, two spaces apart."""
    counts: dict = {}
    colours: dict = {}
    for part in standing.split(" · "):
        m = re.match(r"(\d+) (.+)$", part.strip())
        if not m:
            continue
        mark = next((g for w, g in ROW_STATES if w in m.group(2)), None)
        if mark is None:
            continue
        counts[mark] = counts.get(mark, 0) + int(m.group(1))
        colour = _colour(part, muted)
        if mark not in colours or (colour == GOOD and mark == "■"):
            colours[mark] = colour
    t = Text()
    for mark in ("■", "▲", "◧", "□"):
        if mark in counts:
            if t.plain:
                t.append("  ")
            t.append(f"{mark} {counts[mark]} {MARK_WORD.get(mark, '')}", style=colours[mark])
    return t


def strip_marks(groups: list, findings: Optional[dict] = None) -> Text:
    """A top-level question's catalogue row as its strip counts it (ruling 9): each group's mark, count and word,
    ■ 2 passed  ▲ 2 failed, its sub-questions' checks included, in the colour its squares take: green only where every
    check that is no gate is green (one met on evidence with a problem dims the count), a gate counting only where the
    group holds gates alone."""
    findings = findings or {}
    t = Text()
    for g in groups:
        checks = g.get("checks", [])
        if not checks:
            continue
        fs = [findings.get(c.get("id") if isinstance(c, dict) else c) for c in checks]
        colours = [check_colour(g.get("state"), f) for f in fs if not (f is not None and f.get("role") == "gate")]
        colours = colours or [check_colour(g.get("state"), f) for f in fs]
        colour = colours[0] if len(set(colours)) == 1 else DIM
        if t.plain:
            t.append("  ")
        t.append(f"{g['mark']} {len(checks)} {g.get('label') or MARK_WORD.get(g['mark'], '')}".rstrip(), style=colour)
    return t


def fold_marks(marks: Text, width: int, indent: str) -> Text:
    """`marks` (groups two spaces apart) in `width` columns: a group never cut or split, one that does not fit going
    whole to the next line, under the first, after `indent`."""
    out = Text(end="")
    line = 0
    for k, part in enumerate(marks.split("  ", allow_blank=False)):
        part.end = ""
        if k and line + 2 + len(part) <= width:
            out.append("  ")
            line += 2
        elif k:
            out.append(f"\n{indent}")
            line = 0
        out.append_text(part)
        line += len(part)
    return out


def strip_lines(lead: str, groups: list, width: int, findings: Optional[dict] = None) -> list:
    """The strip after `lead` ('Open · '): each group its squares, each in its check's colour, then its count and
    label, a group never cut or split, two spaces apart (one where two would not fit); a group that does not fit on the
    line goes whole to the next, under the first."""
    findings = findings or {}
    first = Text()
    word = lead.rstrip(" ·")
    if word:
        # a question's state is an amber chip (TUI-PARITY A7, N5): a reversed word would read as a selected button
        first.append("[", style=DIM)
        first.append(word, style=WARN)
        first.append("]", style=DIM)
        first.append("  ")
    lead = " " * len(first.plain)
    lines: list = [first]
    for g in groups:
        checks = g.get("checks", [])
        part = Text()
        # one mark per check up to 10, then one mark and the count (A7)
        for c in (checks if len(checks) <= 10 else checks[:1]):
            cid = c.get("id") if isinstance(c, dict) else c
            part.append(g["mark"], style=check_colour(g.get("state"), findings.get(cid)))
        part.append(f" {len(checks)} {g['label']}")
        # groups two spaces apart, one where two would wrap and one fits (UX lead, Nov 12 07:15 +08), else the next line
        if len(lines[-1]) > len(lead) and len(lines[-1]) + 1 + len(part) > width:
            lines.append(Text(" " * len(lead)))
        elif len(lines[-1]) > len(lead):
            lines[-1].append("  " if len(lines[-1]) + 2 + len(part) <= width else " ")
        lines[-1].append_text(part)
    return lines


def ov_domain(rows: list) -> tuple:
    """A panel's domain, as rb status draws it (overview.rs domain): its numbers, its checks' edges, 0 for a difference."""
    dom = []
    for r in rows:
        dom += [r.get(k) for k in ("est", "lo", "hi", "threshold") if r.get(k) is not None]
        if r.get("threshold") is not None and r.get("tol") is not None:
            dom += [r["threshold"] - r["tol"], r["threshold"] + r["tol"]]
        if r.get("difference"):
            dom.append(0.0)
    lo, hi = min(dom), max(dom)
    pad = (hi - lo) * 0.15 if hi > lo else abs(lo) * 0.05 if lo != 0 else 0.5
    return lo - pad, hi + pad


def ov_claim(r: dict) -> bool:
    """A check of a claimed number (a cited `=` rule): its number is `◆`, never a zone."""
    return bool(r.get("claim")) and r.get("side") == "eq"


def ov_bar(r: dict, lo: float, hi: float, w: int) -> Text:
    """One row's bar (TUI-PARITY X4, section 0 item 7): `░` (green) the zone that passes, `◆` (green) a claimed number,
    `┆` (dim) 0, `├─●─┤` rb's number and its range with both ends, `○` a number with no range yet. Where a claim and rb's
    number fall in one cell, the cell draws `◆`."""
    w = max(w, 8)
    x = lambda v: int(min(max(round((v - lo) / (hi - lo) * (w - 1)), 0), w - 1))
    cells = [" "] * w
    t = r.get("threshold")
    claim = ov_claim(r)
    if t is not None and not claim:
        tol = r.get("tol") or 0.0
        side = r.get("side")
        span = range(x(t), w) if side in ("ge", "gt") else range(0, x(t) + 1) if side in ("le", "lt") else range(x(t - tol), x(t + tol) + 1)
        for i in span:
            cells[i] = "░"
    if r.get("difference") and lo <= 0 <= hi:
        cells[x(0.0)] = "┆"
    est = r.get("est") or 0.0
    if r.get("lo") is not None and r.get("hi") is not None and r["hi"] > r["lo"]:
        i, j = x(r["lo"]), x(r["hi"])
        if j - i < 2:                                       # a range always draws both ends, at least ├●┤
            i, j = (i, i + 2) if i + 2 < w else (j - 2, j)
        for k in range(i, j + 1):
            cells[k] = "─"
        cells[i], cells[j] = "├", "┤"
        cells[min(max(x(est), i + 1), j - 1)] = "●"
    else:
        cells[x(est)] = "○"
    if t is not None and claim:
        cells[x(t)] = "◆"
    colour = check_colour(r.get("state"))
    out = Text(no_wrap=True, overflow="crop")
    for c in cells:
        out.append(c, style=GOOD if c in "░◆" else DIM if c == "┆" else colour if c in "●○" else "")
    return out


def ov_labels(r: dict, lo: float, hi: float, w: int) -> list:
    """What a bar's marks stand for, by column: 0 over `┆`, the zone's edge over its start, a claim's number over `◆`."""
    x = lambda v: int(min(max(round((v - lo) / (hi - lo) * (w - 1)), 0), w - 1))
    from .tui_card import claim_text, edge_label
    marks = []
    t = r.get("threshold")
    if t is not None and ov_claim(r):
        marks.append((x(t), claim_text(r)))
    elif t is not None and (r.get("edge_text") or "") and not (r.get("difference") and t == 0):
        side = r.get("side")
        edge = x(t) if side in ("ge", "gt", "le", "lt") else x(t - (r.get("tol") or 0.0))
        marks.append((edge, edge_label(r)))
    if r.get("difference") and lo <= 0 <= hi:
        marks.append((x(0.0), "0"))
    return marks


def ov_wrap(text: str, width: int) -> list:
    lines: list = []
    for word in text.split():
        if lines and len(lines[-1]) + 1 + len(word) <= width:
            lines[-1] += " " + word
        else:
            lines.append(word)
    return lines


def ov_axis(r: dict, lo: float, hi: float, w: int, lead: int) -> Optional[Text]:
    """Over a bar, the numbers its marks stand for (A8, X4): 0 over `┆`, the zone's edge over where it starts, a claim's
    number right above its `◆`, each centred on its column; None where there is nothing to label."""
    w = max(w, 8)
    marks = ov_labels(r, lo, hi, w)
    if not marks:
        return None
    cells = [" "] * (w + 12)
    for at, label in marks:             # the check's number first: where two would touch, it stands
        start = min(max(at - len(label) // 2, 0), len(cells) - len(label))
        if any(c != " " for c in cells[max(start - 1, 0): start + len(label) + 1]):
            continue
        cells[start: start + len(label)] = list(label)
    t = Text(" " * lead, no_wrap=True, overflow="crop")
    t.append("".join(cells).rstrip(), style=DIM)
    return t


def ov_chart(rows: list, width: int, findings: Optional[dict] = None) -> list:
    """The chart as (Text, check id) rows in `width` columns (A8, M1, M2): a panel head per measure with a dim `W` at its
    right end and, where every row's rule was written after its results, `‾` with those words once; then each check.
    At 76 columns or more a check is one line: its label cut between words, the bar, its mark and word, its number and
    its range. Narrower, its label wraps whole over the bar. The labels of a bar's marks sit on the line above it."""
    out: list = []
    one_line = width >= 72                 # an 80-column screen: the story is 76 wide, its rows 74 inside the list
    labw = 26 if one_line else 0
    lead = 2 + labw + (1 if one_line else 0)
    i = 0
    while i < len(rows):
        head = rows[i].get("group", "")
        j = i
        while j < len(rows) and rows[j].get("group", "") == head:
            j += 1
        panel = rows[i:j]
        lo, hi = ov_domain(panel)
        all_late = all(bool(r.get("late")) for r in panel)

        def tail_of(r: dict, with_range: bool) -> str:
            mark = STATE_Mark(r)
            est = r.get("result_text") or r.get("est_text", "")
            rng = r.get("range_text") or "no range yet"
            late = "‾ " if r.get("late") and not all_late else ""
            return f" {late}{mark} {MARK_WORD.get(mark, '')}  {est}" + (f"  {rng}" if with_range or not r.get("range_text") else "")
        for with_range, bar in ((True, 12), (True, 8), (False, 8)):
            tails = [tail_of(r, with_range) for r in panel]
            if width - lead - max(len(t) for t in tails) >= bar:
                below = False
                break
        else:
            below = True
        bw = max(width - lead - (0 if below else max(len(t) for t in tails)), 8)
        h = Text(no_wrap=True, overflow="crop")
        h.append(head, style=DIM)
        late = f"‾ {LATE_WORDS[0]}"
        if all_late and len(head) + 3 + len(late) + 3 <= width:
            h.append("   ")
            h.append("‾", style=WARN)
            h.append(f" {LATE_WORDS[0]}", style=DIM)
        h.append(" " * max(width - len(h.plain) - 1, 1))
        h.append("W", style=DIM)
        out.append((h, None))
        if all_late and len(head) + 3 + len(late) + 3 > width:
            out.append((Text.assemble(("‾", WARN), (f" {LATE_WORDS[0]}", DIM)), ""))
        for r, tail in zip(panel, tails):
            label = r.get("label", "")
            took = bool(r.get("taken_out"))
            claim = bool(r.get("claim"))
            line = Text(no_wrap=True, overflow="crop")
            if not one_line:
                for k, a in enumerate(ov_wrap(label, width - (8 if claim else 0))):
                    if claim and k == 0:
                        line.append("[claim] ", style=DIM)
                    line.append(f"{a}\n")
            if took:
                line.append("  (rests on a write git took out of the record)\n", style=WARN)
            axis = ov_axis(r, lo, hi, bw, lead)
            if axis is not None:
                line.append_text(axis)
                line.append("\n")
            if one_line:
                short = cut_words(label, labw - (8 if claim else 0))
                line.append("  ")
                if claim:
                    line.append("[claim] ", style=DIM)
                line.append(short + " " * max(labw - len(short) - (8 if claim else 0), 0) + " ")
            else:
                line.append("  ")
            line.append_text(ov_bar(r, lo, hi, bw))
            line.append("\n" + " " * (lead - 1) if below else " ")
            colour = check_colour(r.get("state"), (findings or {}).get(r.get("id")))
            body = tail[1:]
            if body.startswith("‾ "):
                line.append("‾ ", style=WARN)
                body = body[2:]
            word = body.find("  ")
            line.append(body[:word], style=colour)      # the mark and its word, in the check's colour
            line.append(body[word:])
            out.append((line, r.get("id")))
        i = j
    return out


NOT_TESTED = "Not tested yet"          # F's empty-state chip (strings.rs NOT_TESTED_YET), until the bridge gives it


def whole_cut(text: str, room: int) -> str:
    """A title cut only between words: where not even its first word fits, `…` alone."""
    cut = cut_words(text, max(room, 1))
    head = cut[:-1] if cut.endswith("…") else cut
    if cut.endswith("…") and (not head.strip() or len(head) < len(text) and text[len(head)] != " " and not head.endswith(" ")):
        return "…"
    return cut


def standing_states(standing: str, muted=()) -> dict:
    """rb's count of a row (its standing, `open · 2 reproduced · 1 criterion not met`) as checks by state, so a row
    says rb's own numbers; a muted met count stands for checks that are not green."""
    out: dict = {}
    for part in (standing or "").split(" · "):
        n, _, words = part.partition(" ")
        if not n.isdigit():
            continue
        mark = next((g for w, g in ROW_STATES if w in words), None)
        st = next((k for k, v in STATE_MARK.items() if v == mark), None)
        if st:
            for i in range(int(n)):
                hit = st == "meets" and any(m and (m == st or m in words) for m in muted)
                out[f"{part}:{i}"] = "meets-muted" if hit else st
    return out


def mini_bar(r: dict, cells: int = 10) -> Optional[Text]:
    """A Your call row's mini bar (F3.3, M2): 10 cells, `●` rb's number with `├─┤` its range or `○` with none, `◆` the
    check's number; None where the row has no number."""
    if not r or r.get("est") is None:
        return None
    lo, hi = ov_domain([r])
    x = lambda v: int(min(max(round((v - lo) / (hi - lo) * (cells - 1)), 0), cells - 1))
    c = [" "] * cells
    if r.get("lo") is not None and r.get("hi") is not None and r["hi"] > r["lo"]:
        i, j = x(r["lo"]), x(r["hi"])
        if j - i < 2:
            i, j = (i, i + 2) if i + 2 < cells else (j - 2, j)
        for k in range(i, j + 1):
            c[k] = "─"
        c[i], c[j] = "├", "┤"
        c[min(max(x(r["est"]), i + 1), j - 1)] = "●"
    else:
        c[x(r["est"])] = "○"
    if r.get("threshold") is not None:
        c[x(r["threshold"])] = "◆"
    t = Text(no_wrap=True)
    for ch in c:
        t.append(ch, style=GOOD if ch == "◆" else check_colour(r.get("state")) if ch in "●○" else "")
    return t


def compact_marks(states: dict, findings: Optional[dict] = None) -> Text:
    """A tree row's marks and counts (A7): `■ 2  ▲ 2  ◧ 1`, in that order, each mark in its checks' colour (green only
    where every check it counts is a rule met with nothing wrong under it); nothing for none."""
    t = Text()
    findings = findings or {}
    for st in ("meets", "misses", "undetermined", "untested"):
        ids = [c for c, v in states.items() if v in (st, f"{st}-muted")]
        if ids:
            if t.plain:
                t.append("  ")
            # a met gate is neutral by design (D4): it leaves the colour to the claims beside it
            claims = [c for c in ids if (findings.get(c) or {}).get("role") != "gate"] or ids
            colours = {DIM if states[c].endswith("-muted") else check_colour(st, findings.get(c)) for c in claims}
            t.append(STATE_MARK[st], style=colours.pop() if len(colours) == 1 else DIM)
            t.append(f" {len(ids)}")
    return t


def STATE_Mark(r: dict) -> str:
    return STATE_MARK.get(r.get("state"), "□")


def file_mark_words(m: dict) -> str:
    """A file's mark in words that fit a row, its count in them (rb files' words: files.rs MARKS): 'cited 3 times',
    'source of 6 settings'; a mark this screen has no short words for says rb's own."""
    n = m.get("n")
    if m.get("key") == "cited" and n is not None:
        return f"cited {'once' if n == 1 else f'{n} times'}"
    if m.get("key") == "setting" and n is not None:
        return f"source of {n} setting{'' if n == 1 else 's'}"
    short = {"changed": "changed since its run"}.get(m.get("key"))
    return short or (f"{n} " if n is not None else "") + m.get("said", "")


# a rule written after its results, in the words every screen says it (strings.rs plain_word: Retrospective)
LATE_WORDS = ("written after the results", "written after the results")


def build_card_widgets(c: View) -> list[Widget]:
    """The card, as widgets: used by the full-screen card and by the home screen's side panel,
    so the two can never disagree about what a call shows."""
    out: list[Widget] = [
        Static(Text(c.title, style="bold", no_wrap=True, overflow="ellipsis"), classes="card-title"),
        Static(f"[{DIM}]{_esc(c.who_line)}[/{DIM}]"),
    ]
    if c.lead:
        out.append(Static(_esc(said_standing(c.lead)), classes="lead"))      # a rule's words as rb open's card says them
    for s in c.sentences:
        out.append(Static(_esc(said_sentence(s, c.title)), classes="lead"))     # the brief, full ink: the state is the content
    for f in c.facts:
        key, colon, said = f.partition(": ")
        if key in ("rule", "result") and colon:
            f = f"{key}: {said_lead(said_standing(said))}"
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
                Static(f"{_esc(p.text)}\n[{DIM}]{_esc(p.rule)}[/{DIM}] · [{colour}]{_esc(said_standing(p.standing))}[/{colour}]",
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


class Pin(Static):
    """The pinned Your call, clickable: it opens Your call's full list, as y does."""

    async def on_click(self) -> None:
        await self.screen.run_action("your_call")


class ObservedLine(Static, can_focus=True):
    """OBSERVED (X10): one focusable line; ⏎ or a click opens the list of what rb observed."""

    BINDINGS = [Binding("enter", "open_list", show=False)]

    async def on_click(self) -> None:
        await self.screen.run_action("observed")

    async def action_open_list(self) -> None:
        await self.screen.run_action("observed")


class CountLine(Static, can_focus=True):
    """A line of counts that open (N5): the strip's groups or what the work rests on, each a span that `←` `→` move
    along and `⏎` opens (its checks as a list, or the card its count opens)."""

    BINDINGS = [Binding("left", "move(-1)", show=False), Binding("right", "move(1)", show=False),
                Binding("enter", "act", show=False)]

    def __init__(self, **kw) -> None:
        super().__init__(**kw)
        self.base = Text()
        self.spans: list = []           # (start, end, action) in base's plain text
        self.at = 0

    def show(self, base: Text, spans: list) -> None:
        self.base, self.spans = base, spans
        self.at = min(self.at, max(len(spans) - 1, 0))
        self._paint()

    def _paint(self) -> None:
        t = self.base.copy()
        if self.has_focus and self.spans:
            a, b, _ = self.spans[self.at]
            t.stylize("bold underline", a, b)
        self.update(t)

    def on_focus(self) -> None:
        self._paint()

    def on_blur(self) -> None:
        self._paint()

    def action_move(self, step: int) -> None:
        if self.spans:
            self.at = max(0, min(self.at + step, len(self.spans) - 1))
            self._paint()

    def action_act(self) -> None:
        if self.spans:
            self.screen.open_count(self.spans[self.at][2])

    def on_click(self, event: events.Click) -> None:
        self.focus()


def spans_of(text: Text, parts_in_order: list) -> list:
    """Each (words, action) found in `text` in order, as (start, end, action): a count's place on its line."""
    plain, at, out = text.plain, 0, []
    for words, action in parts_in_order:
        first, last = words if isinstance(words, tuple) else (words, "")
        i = plain.find(first, at)
        if i < 0:
            continue
        end = i + len(first)
        if last:
            j = plain.find(last, end)
            end = j + len(last) if j >= 0 else end
        out.append((i, end, action))
        at = end
    return out


class HeadLine(Static):
    """Row 0: a click on it is w's, WHERE (M12: the git chip opens it)."""

    async def on_click(self) -> None:
        await self.screen.run_action("where")


class PlaceLine(Static):
    """The place line, clickable: a click runs w's action, as a chip runs its key's, and owns no behaviour of its own."""

    async def on_click(self) -> None:
        await self.screen.run_action("places")


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


class EvidenceScreen(Screen):
    """Below NARROW, a card opens over the story (UX5 2.3): the same fields and citations as the evidence pane, Enter
    on a citation opens it here, escape goes back along the trail and then to the story; l opens the long form."""
    BINDINGS = [Binding("escape", "back", "back"), Binding("l", "long", "long form"), Binding("q", "back", "back", show=False)]

    def __init__(self, home: "HomeScreen", subject: str) -> None:
        super().__init__()
        self.home, self.subject = home, subject

    def compose(self) -> ComposeResult:
        yield Evidence(self.home.evidence_for, id="over-card")
        yield Footer()

    def on_mount(self) -> None:
        ev = self.query_one(Evidence)
        ev.show(self.subject)
        first = ev.first_cite()
        if first is not None:
            first.focus()

    def on_cite_opened(self, message: Cite.Opened) -> None:
        self.home.open_target(message.cite.target, self.query_one(Evidence))

    def action_back(self) -> None:
        if self.query_one(Evidence).back() is None:
            self.app.pop_screen()

    def action_long(self) -> None:
        trail = self.query_one(Evidence).trail
        if trail:
            self.home.open_long(trail[-1])


class LongScreen(Screen):
    """The long form of something rb ui draws no card of its own for, as rb show --long prints it. escape goes back."""
    BINDINGS = [Binding("escape,q", "back", "back")]

    def __init__(self, subject: str, text: str) -> None:
        super().__init__()
        self.subject, self.text = subject, text

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="card"):
            yield Static(Text(self.text))
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

    def __init__(self, items: list[str], activity: list[str], looked: Optional[list] = None) -> None:
        super().__init__()
        self.items, self.activity, self.looked = items, activity, list(looked or [])

    def compose(self) -> ComposeResult:
        with VerticalScroll():
            yield Static("[b]Open in the record[/b]\n")
            for line in self.items:
                yield Static(f"  {_esc(line)}")
            if self.activity:
                yield Static(f"\n[b]Activity[/b]  [{DIM}]what rb observed, newest last[/{DIM}]\n")
                for line in self.activity:
                    yield Static(f"  [{DIM}]{_esc(line)}[/{DIM}]")
            if self.looked:
                # the home's one-line forms, whole: what changed that nothing cites, and where rb looks
                yield Static("\n[b]Where rb looks[/b]\n", id="looked-head")
                for line in self.looked:
                    yield Static(f"  [{DIM}]{_esc(line)}[/{DIM}]", classes="looked")
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
        with VerticalScroll(id="places-box"):
            with Vertical(id="where-panel"):
                yield OptionList(id="where")
                yield Static(id="where-detail")     # the highlighted place's whole path, as git lists it
            with Vertical(id="versions-panel"):
                yield OptionList(id="versions")

    def on_mount(self) -> None:
        self.query_one("#where-panel").border_title = "WHERE"
        where = self.query_one("#where", OptionList)
        shown = self.place or next((p.id for p in self.info.places if p.record == "this"), None)
        # each place's row as rb made it: its label, then what rb says of it in the words every place says them
        named = short if len(self.info.places) > 1 else (lambda p: p.label)
        for p in self.info.places:
            row = p.get("row") or f"{named(p)}: {p.said}"
            where.add_option(Option(Text(row, no_wrap=True, overflow="ellipsis"), id=f"p:{p.id}", disabled=not p.readable))
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
        """Under the places, the highlighted one's lines as rb made them: its whole path, as git lists it, then how its
        checkout holds the record (its commit, one step down from the words), and why it cannot be shown."""
        p = self.info.places[index] if index is not None and 0 <= index < len(self.info.places) else None
        fold = list(p.get("fold") or [p.path]) if p is not None else []
        lines = [] if p is None else (fold[:1] + list(p.evidence) if p.get("evidence") else fold) + ([p.why_not] if p.get("why_not") else [])
        said = "\n".join(lines)
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


# the key (? on the home): one row per key, its label three words or fewer; no sentence (the UX lead, Nov 12 00:15
# +08), and only the keys: what a mark or a word means, the screen shows beside it (no glossary, show instead: the
# orchestrator, Nov 12 20:35 +08)
DECISION_ROWS = 3          # the home's Decisions rows, as the web's Overview shows them

KEYS = (("Tab", "next item"), ("Shift-Tab", "previous item"), ("Enter", "open"), ("Esc", "back"),
        ("f", "Research or Files"), ("c", "confirm"), ("y", "Your call"), ("y", "record found"), ("n", "leave out"), ("w", "where"),
        ("a", "still open"), ("r", "look again"), ("W", "figure's words"), ("?", "this key"), ("q", "quit"))


def key_text() -> Text:
    """The key as rows: each key, then its label."""
    t = Text()
    t.append("KEYS\n", style=ACCENT)
    for k, label in KEYS:
        t.append(f"{k:>10}", style="bold")
        t.append(f"  {label}\n")
    t.rstrip()
    return t


class KeyScreen(ModalScreen[None]):
    """The key: which key does what. escape closes it."""
    BINDINGS = [Binding("escape", "close", "close"), Binding("question_mark", "close", "close", show=False)]

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="key-box"):
            yield Static(glyphs(key_text()))

    def action_close(self) -> None:
        self.app.pop_screen()


class FileScreen(Screen):
    """A file's card (UX5 1.4): its place, its marks with their words, the lines the record cites from it and what
    cites it, as `rb files <path>` prints them. escape goes back."""
    BINDINGS = [Binding("escape", "back", "back")]

    def __init__(self, f: dict, citations: dict) -> None:
        super().__init__()
        self.f, self.citations = f, citations

    def compose(self) -> ComposeResult:
        f = self.f
        t = Text()
        t.append(f.get("folder", ""), style=DIM)
        t.append(f.get("name", ""), style="bold")
        t.append("\n\n")
        for m in f.get("marks", []):
            t.append(f"{m['glyph']} {file_mark_words(m)}\n")
        for key, c in self.citations.items():
            if c.get("path") != f.get("path"):
                continue
            t.append(f"\n{key}\n", style=TEAL)
            for line in c.get("lines", []):
                on = line.get("cited")
                t.append(f"{line.get('n'):>5}{' ▸' if on else '  '} {line.get('text', '')}\n", style="" if on else DIM)
        if f.get("cited_by"):
            t.append("\nCited by\n", style=ACCENT)
            for b in f["cited_by"]:
                t.append(f"  {b['kind']} {b['id']}\n")
        with VerticalScroll():
            yield Static(t, id="card")
        yield Footer()

    def action_back(self) -> None:
        self.app.pop_screen()


def card_footer(view: CardView, at_start: bool) -> str:
    """The open card's footer (section 3): d decide on a check that waits on a decision, on its Result tab; a question's,
    an experiment's and a resource's by what they hold. c and d only where the screen started, live."""
    d = view.data
    if d.kind == "question":
        return "card.question.next" if d.prompt and view.tab == 0 else "card.question"
    if d.kind == "resource":
        # g only where the resource has a download link to open
        return "card.resource.next" if d.prompt else ("card.resource" if d.get_it else "card.resource.plain")
    if d.kind == "long":
        return "long"
    if d.kind == "experiment":
        return "card.design" if d.card.get("design_waits") and at_start else "card"
    if not at_start:
        return "card"
    waits = any(w[2] == "decide" for w in __import__("rabbit_brain.tui_card", fromlist=["x"]).result_chips(view.data.row, view.data.card, view.data.finding))
    return "card.decide" if waits and view.tab == 0 else "card"


def check_row(cid: str, r: dict, width: int, idw: int) -> Text:
    """One check on a question's or an experiment's card (M20): its mark and word, its title cut between words (with
    `[claim]`), its id dim at the right and `›`, in `width` columns."""
    mark = STATE_MARK.get(r.get("state"), "□")
    t = Text(no_wrap=True, overflow="crop")
    t.append(f"{mark} {STATE_WORD.get(mark, ''):<14}  ", style=parts.MARK_COLOUR.get(mark, ""))
    label = ("[claim] " if r.get("claim") else "") + (r.get("label") or "")
    room = max(width - 18 - idw - 4, 10)
    t.append(f"{parts.cut_words(label, room) if label else '':<{room}}")
    t.append(f"  {cid:>{idw}}", style=DIM)
    t.append(" ›", style=DIM)
    return t


def card_copy_text(view: CardView) -> str:
    """What p copies on an open card (section 0 item 5): its Next step where it has one, else the command that shows it;
    at a past version nothing (guarantee 38: a past version offers no command to copy)."""
    if view.past:
        return ""
    return view.data.prompt if view.data.prompt and view.tab == 0 else f"rb show {view.data.subject}"


def card_act(home: "HomeScreen", view: CardView, what: tuple) -> None:
    """⏎ on a card's item: open a card (a check swaps in place), Decide, or copy by the p routes."""
    if what[0] == "open":
        subject = what[1]
        if subject and home.card_data(subject) is not None:
            data = home.card_data(subject)
            view.data, view.tab, view.at = data, 0, 0
            view.refresh()
            return
        if subject:
            if isinstance(home.app.screen, CheckCardScreen):
                home.app.pop_screen()
            home.close_card(refocus=False)
            if subject.startswith("q") and subject in {r.id for r in home.home.tree}:
                home.open_long(subject)
            else:
                home.app.push_screen(EvidenceScreen(home, subject))
    elif what[0] == "copy":
        said = home.copy_text(what[1])
        if said:
            view.show_flash(said)
    elif what[0] == "long":
        home.open_long(what[1])
    elif what[0] == "getit":
        said = clip.open_address(what[1]) or home.copy_text(what[1])
        if said:
            view.show_flash(said)
    elif what[0] == "confirm" and home.at_start:
        home.confirm_design(what[1])
    elif what[0] == "decide" and home.at_start:
        home.decide_on(view.data.subject, view.data.card.get("title", ""), after=lambda: view.refresh())


class CheckCardScreen(Screen):
    """Below 120 columns a check's card is full screen (M5): the same card, its footer under it."""
    BINDINGS = [Binding("escape", "back", show=False), Binding("p", "copy", show=False), Binding("d", "decide", show=False),
                Binding("g", "get_it", show=False), Binding("c", "confirm", show=False), Binding("l", "long", show=False),
                Binding("question_mark", "key", show=False), Binding("q", "app.quit", show=False)]
    DEFAULT_CSS = """
    CheckCardScreen #card-view { height: 1fr; padding: 0; }
    CheckCardScreen #card-footer { height: 1; padding: 0 0 0 1; }
    """

    def __init__(self, home: "HomeScreen", data: CardData, tab: int = 0) -> None:
        super().__init__()
        self.home, self.data, self.tab = home, data, tab

    def compose(self) -> ComposeResult:
        view = CardView(self.data, past=self.home.at is not None, id="card-view")
        view.tab = self.tab
        yield view
        yield Static(id="card-footer")

    def check_action(self, action: str, parameters: tuple) -> Optional[bool]:
        if action in ("confirm", "decide") and not self.home.at_start:
            return False            # unbound away from the record the screen started on, as on the home
        if action == "copy" and self.home.at is not None:
            return False            # a past version offers nothing to copy
        return True

    def on_mount(self) -> None:
        self.query_one(CardView).focus()
        self.set_interval(0.3, self._paint)
        self._paint()

    def _paint(self) -> None:
        view = self.query_one(CardView)
        self.query_one("#card-footer", Static).update(parts.footer_text(parts.footer_pairs(card_footer(view, self.home.at_start), self.home.at_start,
                                                                                           past=self.home.at is not None), self.app.size.width))

    def on_card_view_act(self, message: CardView.Act) -> None:
        card_act(self.home, message.view, message.what)

    def action_back(self) -> None:
        self.app.pop_screen()

    def action_copy(self) -> None:
        view = self.query_one(CardView)
        text = card_copy_text(view)
        if not text:
            return
        said = self.home.copy_text(text)
        if said:
            view.show_flash(said)

    def action_get_it(self) -> None:
        view = self.query_one(CardView)
        if view.data.get_it:
            card_act(self.home, view, ("getit", view.data.get_it))

    def action_confirm(self) -> None:
        view = self.query_one(CardView)
        if card_footer(view, self.home.at_start) == "card.design":
            self.home.confirm_design(view.data.subject)

    def action_long(self) -> None:
        view = self.query_one(CardView)
        if view.data.kind == "question":
            self.home.open_long(view.data.subject)

    def action_decide(self) -> None:
        if card_footer(self.query_one(CardView), self.home.at_start) == "card.decide":
            view = self.query_one(CardView)
            self.home.decide_on(view.data.subject, view.data.card.get("title", ""), after=view.refresh)

    def action_key(self) -> None:
        self.app.push_screen(panels.KeyPanel())


class HomeScreen(Screen):
    BINDINGS = [Binding("enter", "open", "open", priority=True), Binding("t", "catalogue", "research"),
                Binding("y", "your_call", "Your call"),
                Binding("c", "confirm", "confirm"),
                Binding("a", "open_items", "also open"),
                Binding("w", "where", "where"),
                Binding("left", "chip(-1)", show=False), Binding("right", "chip(1)", show=False),
                Binding("j", "line(1)", show=False), Binding("k", "line(-1)", show=False), Binding("p", "copy", "copy"), Binding("d", "decide", "decide"),
                Binding("f", "files", "files"), Binding("g", "get_it", show=False),
                Binding("l", "long", "long form", show=False), Binding("W", "words", "words", show=False),
                Binding("question_mark", "key", "key", show=False),
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
        self._tab = "research"                        # the left panel's tab: research or files
        self._words = False                           # W: each figure's words under it
        self._files: Optional[list] = None            # the Files tab's rows, asked of rb when it is first shown
        self._story: Optional[str] = None             # another top-level question made the story (R21), or rb's pick
        self._came_from: Optional[OptionList] = None  # the list whose row's card the focus went into, for escape

    @property
    def at_start(self) -> bool:
        """Whether the screen shows the record it started on, live: the one y, n and c act on."""
        return self.place is None and self.at is None

    def check_action(self, action: str, parameters: tuple) -> Optional[bool]:
        if action in ("yes", "no", "confirm", "decide") and not self.at_start:
            return False            # unbound away from the record the screen started on
        if action == "copy" and self.at is not None:
            return False            # a past version offers no command to copy
        if action == "back_to_start" and self.at_start and self._story is None and not self._escapable():
            return False
        return True

    def _escapable(self) -> bool:
        """Whether escape has a step to go back here: the catalogue over the story, a card's citation in focus, or Your
        call's full list in Decisions' place."""
        return (bool(self.query("#card-view")) or self.has_class("-catalogue") or isinstance(self.focused, Cite) or getattr(self, "_decisions_open", False)
                or bool(getattr(self, "_filter", None)))

    def action_back_to_start(self) -> None:
        """escape, one step back: along the evidence pane's trail, then from it to the row it came from; the catalogue
        off the story; from another question made the story, back to rb's own; away from where the screen started,
        back to that record, live."""
        if self.close_card():
            return
        if isinstance(self.focused, Cite):
            pane = self.query_one("#side-card", Evidence)
            if pane.back() is None:
                self._first_list().focus() if self._came_from is None else self._came_from.focus()
            return
        if getattr(self, "_filter", None):
            self._filter = None                 # esc shows all again (F3.1)
            self._fill_story(self.home)
            self._paint_footer()
            return
        if self.has_class("-catalogue"):
            self._close_catalogue()
            return
        if getattr(self, "_decisions_open", False):
            # Your call's full list gives the home its three rows back
            self._decisions_open = False
            self._fill_story(self.home)
            self.query_one("#decisions-list", OptionList).focus()
            return
        if self._story is not None:
            self._story = None
            self.home = self._home_view()
            self.refresh_bindings()
            self._fill()
            self.query_one("#others-list", OptionList).focus()
            return
        self.switch(None, None)

    def make_story(self, question: str) -> None:
        """Enter on one of Other questions: that question is the Overview's story; escape goes back."""
        was = self._story
        self._story = question
        try:
            self.home = self._home_view()
        except Exception as exc:  # noqa: BLE001 - a story that cannot be shown is said, never fatal
            self._story = was
            self.app.notify(str(getattr(exc, "message", None) or exc)[:160], severity="warning", timeout=8)
            return
        self._decisions_open = False
        self.refresh_bindings()
        self._fill()
        self._first_list().focus()

    def _home_view(self) -> View:
        return home_view(self.root, self._since_baseline, self.place, self.at, self._story)

    def _object_card(self, subject: str) -> View:
        return object_card(self.root, subject, self.place, self.at)

    def _found_card(self, request_id: str) -> View:
        return found_card(self.root, request_id, self.place, self.at)

    def compose(self) -> ComposeResult:
        # The work leads: the catalogue fills the screen, with the highlighted row's card beside
        # it. What waits on the user (what rb found and nobody recorded) is a strip under the work,
        # present only while something does, never the room's centrepiece.
        yield HeadLine(id="head")             # row 0: the title, the git chip with w, the uncommitted chip (M12)
        yield Static(id="about")              # row 1: the About strip, whole facts in order (M12)
        yield PlaceLine(id="place")           # away from where the screen started: which place and version it shows
        yield Static(id="took")               # what git took out of the record, which rb kept: one line
        # the Overview first (UX5 1.5, round 1): the question, its strip, the decisions, the chart and what it rests on;
        # under it the left panel's two tabs (Research, Files: f switches them) beside the card. Round 2 sets them side
        # by side as three panes
        # below NARROW the left pane is the header's tab row (R22): t brings the catalogue over the story, f switches it
        yield Static(id="tabrow")
        # three panes (UX5 2.3): the left pane's tabs, the story in the middle, the evidence on the right
        with Horizontal(id="panes"):
            with Vertical(id="tree-panel"):
                yield Static(id="tabs")       # Research | Files, as in PostHog
                yield OptionList(id="tree-list")
                yield OptionList(id="files-list")
                yield Pin(id="pin-also")      # also open, pinned at the panel's foot: a opens its list
                yield Pin(id="pin")           # Your call, pinned at the panel's foot under both tabs: y opens its list
            with Vertical(id="middle"):
                yield CountLine(id="story-head")      # the question and its strip, which never scroll out of view
                # the decisions under the strip, the first thing a reader acts on (strip and decisions first); their
                # head and count never scroll out of view either
                yield Static(id="story-decisions-head")
                with VerticalScroll(id="story", can_focus=False):     # tab goes to its lists, never into its frame
                    yield OptionList(id="decisions-list")
                    yield OptionList(id="chart-list")
                    yield CountLine(id="story-rests")
                    yield OptionList(id="others-list")   # the other top-level questions, each with its strip (R21)
            yield Evidence(self.evidence_for, id="side-card")
        # what waits for a confirmation is a row of Decisions (R12), and the home counts nothing record-wide (R21): no
        # TO CONFIRM group, no rule tally, no line of calls under the work
        with Vertical(id="calls-panel"):
            yield OptionList(id="calls")
        yield Static(id="scope")              # the changed files nothing cites, counted, and what rb looks at: quiet
        yield Static(id="update")             # an upgrade installed while the screen is open: one quiet line
        yield ObservedLine(id="observed")     # OBSERVED: rb's newest observation, one line, + more opens the list (X10)
        yield Static(id="footer")             # one footer per context (section 3): keys bold, labels dim

    AUTO_FOCUS = None          # the focus starts on Your call's first row, set once the rows are laid

    def on_mount(self) -> None:
        self._tick = 0
        self.query_one("#tree-panel").border_title = "CATALOGUE"
        self._load_places()
        if self.at_start and self._scan():                 # look once on opening, as rb status does
            self.home = self._home_view()
        self._fill()
        self.call_after_refresh(lambda: self._first_list().focus())
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
        self._paint_place()

    def _paint_place(self) -> None:
        line = place_line(self._places, self.place, self.at, self.size.width - 2) if self._places is not None else ""
        widget = self.query_one("#place", Static)
        # away from where the screen started: in the warning colour, wrapped rather than cut
        widget.set_class(bool(line) and not self.at_start, "away")
        color = WARN if widget.has_class("away") else DIM
        widget.update(f"[{color}]{_esc(line)}[/{color}]" if line else "")
        # at the place the screen started, live, the header's git chip says it (M12): the line shows only away from there
        widget.display = bool(line) and not self.at_start

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
            self._as_of = s.get("as_of")
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
            info = places_view(self.root, self.place, detail=True)
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
        return (self.app.size.width if self.app is not None else self.size.width) >= NARROW

    def on_resize(self, _event) -> None:
        self._paint_place()
        self._fill()

    def _paint_scope(self, h: Any, looked: Optional[tuple] = None) -> None:
        """The count of changed files nothing cites and the scope line, dim, `looked` (the line, and its short form) as
        the last look said it: rb's short form of each, one line each, so the Overview keeps its rows (the story has the
        priority, at every height); their whole words are one key away, on a's screen."""
        widget = self.query_one("#scope", Static)
        scope, short = looked or (h.get("scope"), h.get("scope_short"))
        widget.set_class(True, "-short")
        said = [line for line in (h.get("uncited_short"), short) if line]
        widget.update("")
        widget.display = False          # no sentence under the panes (G3): the header's `as of` and Files ±n say them
        as_of = getattr(self, "_as_of", None)
        if as_of and looked is not None and (h.get("head") or {}).get("as_of") != as_of:
            h["head"] = {**(h.get("head") or {}), "as_of": as_of}       # a look that found nothing still says when
            width = self.app.size.width if self.app is not None else self.size.width
            self.query_one("#about", Static).update(parts.header_rows(h.title, h["head"], width)[1])
        self._scope_whole = [line for line in (h.get("uncited_line"), scope) if line]

    def tree_states(self, h: View) -> dict:
        """Each tree row's checks by state (A7, UX7 T9c): a question's whole subtree (`<q>`), its own checks without its
        sub-questions' (`own:<q>`), and an experiment's (`exp:<e>`), from each question's strip and each finding."""
        out: dict = {}
        qs = [r for r in h.tree if r.kind == "question"]
        for r in qs:
            try:
                groups = ((question_overview(self.root, r.id, self.place, self.at).get("strip")) or {}).get("groups", [])
            except Exception:  # noqa: BLE001 - a question rb cannot draw counts nothing
                groups = []
            out[r.id] = {(c.get("id") if isinstance(c, dict) else c): g.get("state") for g in groups for c in g.get("checks", [])}
        stack: list = []
        children: dict = {}
        for r in h.tree:
            if r.kind != "question":
                continue
            del stack[r.depth:]
            if stack:
                children.setdefault(stack[-1], []).append(r.id)
            stack.append(r.id)
        for r in qs:
            below = set().union(*[set(out.get(c, {})) for c in children.get(r.id, [])]) if children.get(r.id) else set()
            out[f"own:{r.id}"] = {c: st for c, st in out[r.id].items() if c not in below}
        allstates = {}
        for r in qs:
            allstates.update(out[r.id])
        for b in h.get("briefs") or []:
            for e in b.get("experiments") or []:
                for f in e.get("findings") or []:
                    cid = f.get("criterion_id")
                    out.setdefault(f"exp:{e.get('id')}", {})[cid] = allstates.get(cid, f.get("rule_outcome"))
        return out

    def _fill(self) -> None:   # not named _render: that is Textual's own leaf-paint hook
        h = self.home
        self._asks = list(h.found)
        gave_calls = False            # whether this fill gave the calls the focus (Textual sets it after this fill)
        wide = self._wide
        if self.has_class("-narrow") == wide:
            # no card beside the catalogue below NARROW, and the catalogue takes its room: lay the rows again once it has
            self.set_class(not wide, "-narrow")
            self.call_after_refresh(self._fill)
        row0, row1 = parts.header_rows(h.title, h.get("head") or {}, self.app.size.width if self.app is not None else self.size.width)
        self.query_one("#head", Static).update(row0)
        self.query_one("#about", Static).update(row1)
        took = self.query_one("#took", Static)
        took.update(f"[{WARN}]{_esc(h.get('taken_out') or '')}[/{WARN}]" if h.get("taken_out") else "")
        took.display = bool(h.get("taken_out"))
        calls_panel = self.query_one("#calls-panel")
        calls_panel.border_title = f"YOUR CALL ({len(self._asks)})"
        calls_panel.border_subtitle = h.weak_identity or ""      # a weak name is said where y acts
        calls = self.query_one("#calls", OptionList)
        calls.clear_options()
        self._paint_scope(h, getattr(self, "_scope", None) if self.at_start else None)
        if False:                  # found files are rows of Files now (X2, M23): no second Your call box on the home
            calls_panel.display = True
            width = min(max(len(a.title) for a in self._asks) + 4, 44)
            for a in self._asks:
                t = Text(no_wrap=True, overflow="ellipsis", end="")
                t.append(a.title.ljust(max(width, len(a.title) + 2)), style=ACCENT)     # what rb itself brings you, then a gap
                t.append(a.hint, style=DIM)
                calls.add_option(Option(t, id=a.request_id))
            if calls.highlighted is None:
                calls.highlighted = 0
            # what rb found never takes the focus: it starts on Your call's first row (section 0 item 3)
        else:
            calls_panel.display = False
        # what waits for a confirmation: rows of Decisions (R12), never one of the calls above
        rows = list(h.get("pending", []))
        self._pending = rows
        self._settle_in_view(rows)
        tree_panel = self.query_one("#tree-panel")
        tree_panel.border_subtitle = ""
        tree = self.query_one("#tree-list", OptionList)
        keep = tree.highlighted
        keep_id = tree.get_option_at_index(keep).id if keep is not None and keep < tree.option_count else None
        tree.clear_options()
        self._object_refs = {}
        occurrences: dict[str, int] = {}
        measured = tree.size.width
        self._laid_w = measured                # the width these rows are laid for; a later layout lays them again
        width = (measured if measured > 10 else self.size.width - 4) - 1     # the last column stays free for the scrollbar
        cuts = [cut_words(r.label, max(width - 2 * r.depth, 8)) for r in h.tree]
        # a cut title two different records' rows share (one record listed under two questions reads as itself)
        twice = {c for c in cuts if len({r.id for r, x in zip(h.tree, cuts) if x == c}) > 1}
        # a top-level question's row counts what its strip counts, its sub-questions' checks too (ruling 9)
        ov = h.get("overview") or {}
        strips = {o.get("id"): (o.get("strip") or {}).get("groups", []) for o in ov.get("others") or []}
        if ov.get("question"):
            strips[ov["question"].get("id")] = (ov.get("strip") or {}).get("groups", [])
        ov = h.get("overview") or {}
        strips = {o.get("id") for o in ov.get("others") or []} | ({ov["question"].get("id")} if ov.get("question") else set())
        found = self._findings(h)
        states = self.tree_states(h)
        resources = {r.get("id"): r for r in (h.get("ui") or {}).get("resources") or []}
        cut_of = lambda r: ("┃ " if r.kind == "question" else "▸ " if r.kind == "experiment" else "") + (f"{r.id} " if r.kind == "experiment" else "") + whole_cut(r.label, max(width - 2 * r.depth - (2 if r.kind == "question" else len(r.id) + 3 if r.kind == "experiment" else 0), 1))
        cut_rows = [(cut_of(r), r.id) for r in h.tree if r.kind != "resource"]
        twice_cut = {c for c, _ in cut_rows if len({i for x, i in cut_rows if x == c}) > 1}
        parent: list = []                 # the question each depth sits under
        res_rows = [r for r in h.tree if r.kind == "resource"]
        for r in [r for r in h.tree if r.kind != "resource"]:
            del parent[r.depth:]
            indent = "  " * r.depth
            mark = "┃ " if r.kind == "question" else "▸ " if r.kind == "experiment" else ""
            name = f"{r.id} " if r.kind == "experiment" else ""
            # the title on its line, cut only between words; its marks and counts on the line under it (A7)
            room = max(width - len(indent) - len(mark) - len(name), 1)
            label = whole_cut(r.label, room)
            if (mark + name + label) in twice_cut:      # two rows never read the same: each adds its id
                label = whole_cut(r.label, max(room - len(r.id) - 3, 1)) + f" ({r.id})"
            t = Text(no_wrap=True, overflow="ellipsis", end="")
            t.append(indent)
            t.append(mark, style="bold" if r.kind == "question" else DIM)
            t.append(name, style=DIM)
            t.append(label, style="bold" if r.kind == "question" else "")
            if r.kind == "question":
                # rb's own count: a top-level question's strip (its sub-questions' checks too), else its standing
                got = (states.get(r.id, {}) if r.depth == 0 and r.id in strips else standing_states(r.standing, r.get("muted", ())))
                parent.append(r.id)
            else:
                under = parent[-1] if parent else None
                mine = states.get(f"own:{under}") if under else None
                got = {c: st for c, st in states.get(f"exp:{r.id}", {}).items() if mine is None or c in mine}
            marks = compact_marks(got, found)
            t.append(f"\n{indent}  ")
            if marks.plain:
                t.append_text(marks)
            elif r.kind == "question":
                t.append("[", style=DIM)
                t.append(NOT_TESTED, style=DIM)
                t.append("]", style=DIM)
            occurrence = occurrences.get(r.id, 0) + 1
            occurrences[r.id] = occurrence
            option_id = f"o:{r.id}" + (f":{occurrence}" if occurrence > 1 else "")
            self._object_refs[option_id] = r.id
            tree.add_option(Option(glyphs(t), id=option_id))
        if res_rows:
            tree.add_option(Option(Text(""), id="res-gap", disabled=True))
            tree.add_option(Option(Text(f"Resources {len(res_rows)}", style="bold"), id="res-head", disabled=True))
            for r in res_rows:
                facts = resources.get(r.id) or {}
                t = Text(no_wrap=True, overflow="ellipsis", end="")
                name = cut_words(r.label, max(width - 2, 8))
                if len({x.id for x in res_rows if cut_words(x.label, max(width - 2, 8)) == name}) > 1:
                    name = cut_words(r.label, max(width - 5 - len(r.id), 4)) + f" ({r.id})"
                t.append(f"  {name}")
                state = facts.get("state") or ""
                t.append(f"\n    {state}", style="" if state.startswith("↗") else DIM)
                option_id = f"o:{r.id}"
                self._object_refs[option_id] = r.id
                tree.add_option(Option(glyphs(t), id=option_id))
        if tree.option_count:
            tree.highlighted = (list(self._object_refs).index(keep_id) if keep_id in self._object_refs
                                else min(keep, tree.option_count - 1) if keep is not None else 0)
        self._fill_story(h)
        if self._tab == "files" and self._files is not None:
            self._fill_files()             # its FOUND rows follow what rb found as the screen looks again
        self._paint_observed(h)
        if wide:
            # The preview follows the hand: the focused list's highlight fills the side card, and
            # with nothing focused the work is what shows, not the queue.
            focused_id = "calls" if gave_calls else getattr(self.focused, "id", None)
            if self._asks and (focused_id == "calls" or not tree.option_count):
                self._show_side(self._asks[min(calls.highlighted or 0, len(self._asks) - 1)].request_id)
            elif focused_id in ("chart-list", "decisions-list"):
                lst = self.query_one(f"#{focused_id}", OptionList)
                opt = lst.get_option_at_index(lst.highlighted) if lst.highlighted is not None else None
                if opt is not None and opt.id and opt.id.startswith(("o:", "p:")):
                    self._show_side(opt.id)        # the story's row the user is on keeps its card beside it
            elif tree.option_count:
                opt = tree.get_option_at_index(tree.highlighted or 0)
                self._show_side(opt.id)
            else:
                self._show_side(None)
        focused_id = "calls" if gave_calls else getattr(self.focused, "id", None)
        if focused_id not in FOCUS_KEEPS and not isinstance(self.focused, Cite):
            self._first_list().focus()
        self._had_asks = bool(self._asks)
        self.call_after_refresh(self._paint_footer)

    def _paint_observed(self, h: View) -> None:
        """OBSERVED (X10): rb's newest observation on one line, its time and words, and `+n more ›` where more are."""
        lines = [e for e in h.live] + [f"unpl. {u}" for u in h.unplaced]
        t = Text(no_wrap=True, overflow="ellipsis")
        if lines:
            entry = lines[-1]
            at, rest = (entry[:5], entry[6:].strip()) if len(entry) > 6 and entry[2:3] == ":" else ("", entry)
            if at:
                t.append(at, style=TEAL)
                t.append("  ")
            t.append(rest, style=DIM)
            if len(lines) > 1:
                t.append(f"   +{len(lines) - 1} more ›", style=DIM)
        self.query_one("#observed", Static).update(glyphs(t))
        self.query_one("#observed", Static).display = bool(lines)

    def _footer_context(self) -> str:
        """Which footer the focus calls for (section 3)."""
        f = self.focused
        fid = getattr(f, "id", None)
        opened = getattr(self, "_decisions_open", False)
        if fid == "decisions-list":
            lst = self.query_one("#decisions-list", OptionList)
            opt = lst.get_option_at_index(lst.highlighted) if lst.highlighted is not None and lst.option_count else None
            oid = (opt.id or "") if opt is not None else ""
            filt = getattr(self, "_filter", None)
            if oid == "yc-head":
                return "home.chip_on" if filt and filt == getattr(self, "_chip_at", None) else "home.chip"
            if filt:
                return "home.filtered"
            if oid.startswith("o:"):
                return "yourcall.decision" if opened else "home.decision"
            if oid.startswith(("p:", "decision-")):
                return "yourcall.row" if opened else "home.design"
            return "yourcall.row" if opened else "home.row"
        if fid == "tree-list":
            narrow = self.has_class("-narrow")
            tree = self.query_one("#tree-list", OptionList)
            opt = tree.get_option_at_index(tree.highlighted) if tree.highlighted is not None and tree.option_count else None
            subject = self._object_refs.get((opt.id or "") if opt is not None else "")
            ui = self.home.get("ui") or {}
            if any(r.get("id") == subject and r.get("get_it") for r in ui.get("resources") or []):
                return "catalogue.resource" if narrow else "tree.resource"
            if narrow and subject in (ui.get("next_steps") or {}):
                return "catalogue.next"
            return "catalogue.row" if narrow else "tree.row"
        if fid == "files-list":
            fl = self.query_one("#files-list", OptionList)
            opt = fl.get_option_at_index(fl.highlighted) if fl.highlighted is not None and fl.option_count else None
            return "files.found" if opt is not None and (opt.id or "").startswith("found:") else "files"
        if isinstance(f, CardView):
            return card_footer(f, self.at_start)
        if isinstance(f, Cite):
            return "card"
        return "home.row"

    def _paint_footer(self) -> None:
        width = self.app.size.width if self.app is not None else self.size.width
        self.query_one("#footer", Static).update(parts.footer_text(parts.footer_pairs(self._footer_context(), self.at_start, past=self.at is not None), width))

    def _first_list(self) -> OptionList:
        """Where the focus starts: the story's decisions, else its chart (the Overview is the home), else the catalogue."""
        for lid in ("#decisions-list", "#chart-list"):
            lst = self.query_one(lid, OptionList)
            if lst.display and lst.option_count:
                return lst
        return self.query_one("#files-list" if self._tab == "files" else "#tree-list", OptionList)

    @staticmethod
    def _findings(h: View) -> dict:
        """Each criterion's finding in the home view, by its id: whether a met rule is sound (`good`) and its role."""
        out: dict = {}
        for b in h.get("briefs") or []:
            for e in b.get("experiments") or []:
                for f in e.get("findings") or []:
                    out.setdefault(f.get("criterion_id"), f)
        return out

    def _fill_story(self, h: View) -> None:
        """The Overview in text figures (UX5 1.5): the question, its state and the strip, the chart (each row a link),
        what it rests on, and the decisions with their count; the left panel's tabs and the pinned Your call."""
        ov = h.get("overview") or {}
        q = ov.get("question") or {}
        # the story's columns: the chart list's own, once laid out; else the screen less the left panel and the card
        laid = self.query_one("#chart-list", OptionList).size.width
        self._chart_w = laid
        width = max(laid - 2 if laid > 20 else self.size.width - 6, 30)
        # a short screen (80x24): the strip and the decisions first, then the chart as far as its share of the rows
        # goes, and the catalogue under it keeps its rows (the story folds to its strip and decisions while the
        # catalogue has the focus). What the decisions already list (TO CONFIRM, rb's calls line) and the observed
        # panel give way; the record's line, the scope lines and what rb found and nobody recorded stay
        short = self.size.height < SHORT_HOME
        self.set_class(short, "-short")
        head = Text()
        for line in ov_wrap(q.get("text") or ov.get("project", ""), width - 2):
            head.append(f"┃ {line}\n", style="bold")
        findings = self._findings(h)
        if q:
            lead = f"{(q.get('state') or '').capitalize()} · "
            for k, line in enumerate(strip_lines(lead, (ov.get("strip") or {}).get("groups", []), width - 2, findings)):
                if k or not short:                  # M2: at 80x24 the strip sits right under the question
                    head.append("\n")
                head.append_text(line)
            if self._words:
                head.append(f"\n({(ov.get('strip') or {}).get('said', '')})", style=DIM)
        groups = [(f"{len(g.get('checks', []))} {g.get('label', '')}",
                   ("checks", [c.get("id") if isinstance(c, dict) else c for c in g.get("checks", [])], g.get("label", "")))
                  for g in (ov.get("strip") or {}).get("groups", [])] if q else []
        head = glyphs(head)
        self.query_one("#story-head", CountLine).show(head, spans_of(head, groups))
        entries = ov_chart(list(ov.get("chart", [])), width, findings)
        self._has_chart = bool(ov.get("chart"))
        # what it rests on: each count with its meter (A10), whole on its line
        rests = Text()
        for k, line in enumerate(parts.rests_lines(list(ov.get("rests", [])), width + 2)):
            if k:
                rests.append("\n")
            rests.append_text(line)
        if self._words:
            for r in ov.get("rests", []):
                rests.append(f"\n({r.get('said', '')})", style=DIM)
        rests = glyphs(rests)
        self.query_one("#story-rests", CountLine).show(
            rests, spans_of(rests, [((r.get("label", ""), r.get("text", "").replace(" ✓ ", " ").replace(" ✗ ", " ")[len(r.get("label", "")):].strip()),
                                     ("opens", r.get("opens") or "", r.get("key") or "")) for r in ov.get("rests", [])]))
        self._has_rests = bool(q)
        self._fold_story()
        self.call_after_refresh(self._size_story)
        decisions = list(ov.get("decisions", []))
        # Your call (F3, M1 to M3): its head with the chips that filter, then one line a row, from the bridge
        self.query_one("#story-decisions-head", Static).display = False
        items = self._your_call_items(h, width)
        self._decision_items = items
        # the left panel: its tabs, and Your call pinned with the decisions' count, the same number rb status gives
        tabs = Text()
        for name in ("research", "files"):
            tabs.append(f" {name.capitalize()} ", style=f"bold reverse {ACCENT}" if self._tab == name else DIM)
            tabs.append(" ")
        self.query_one("#tabs", Static).update(tabs)
        # below NARROW the tabs are the header's row, each with the key that brings it (R22, M2): the open tab the only
        # reverse video, Your call's pin at the row's end
        yc = (h.get("ui") or {}).get("your_call") or {}
        calls_n = yc.get("count", len(decisions))
        changed = len(h.found) + (h.get("uncited") or 0)
        open_tab = (self._tab if self.has_class("-catalogue") else "overview")
        self.query_one("#tabrow", Static).update(parts.tab_row(open_tab, changed, calls_n, self.app.size.width if self.app is not None else self.size.width))
        self._fill_others(list(ov.get("others", [])), width, findings)
        self._fill_lists(items, entries, q.get("id"), width)
        also = len((h.get("ui") or {}).get("also_open") or [])
        self.query_one("#pin", Static).update(parts.pin("Your call", calls_n, "y", True))
        self.query_one("#pin-also", Static).update(parts.pin("also open", also, "a", False))
        self.query_one("#tree-panel").border_title = parts.left_title(self._tab, changed)
        self.query_one("#tree-list", OptionList).display = self._tab == "research"
        self.query_one("#files-list", OptionList).display = self._tab == "files"

    def _your_call_items(self, h: View, width: int) -> list:
        """Your call's rows as the decisions list holds them: the head (`YOUR CALL 4  [3 decisions]  [1 design to
        confirm]`, below 120 columns `also open 4  a` at its end), then a row per decision and per design, each one line,
        the highlighted one with `▌`; `[decided 1]` after them where calls were made."""
        ui = h.get("ui") or {}
        yc = ui.get("your_call") or {}
        filt = getattr(self, "_filter", None)
        dl = self.query_one("#decisions-list", OptionList)
        w = max((dl.size.width or width), 40)
        also = None if self._wide else len(ui.get("also_open") or [])
        head = Text("" if self._wide else " ", no_wrap=True, overflow="crop").append_text(parts.your_call_head(yc, filt, getattr(self, "_chip_at", None) if self.focused is dl and self._on_head() else None, also, w - 1))
        items: list = [[(head, "yc-head", False)]]
        rows = [r for r in yc.get("rows") or [] if filt is None or r.get("kind") == filt]
        word_w = max([len(f"{r.get('mark')} {r.get('word')}" if r.get("mark") else r.get("word", "")) for r in rows] or [0])
        pending = list(h.get("pending", []))
        at = dl.highlighted if self.focused is dl else None
        for k, r in enumerate(rows):
            if r.get("kind") == "decide":
                oid = f"o:{r.get('subject')}:dec{k}"
                self._object_refs[oid] = r.get("subject")
            elif r.get("kind") == "design":
                row = next((p for p in pending if p.id.split("@")[0] == r.get("subject") and p.kind == "design"), None)
                oid = f"p:{row.id}" if row is not None else f"decision-{k}"
            else:
                oid = f"p:{r.get('id')}"
            mini = mini_bar(self.chart_row(r.get("subject"))) if not self._wide and w >= 70 and r.get("kind") == "decide" else None
            items.append([(glyphs(parts.call_row(r, w, at == len(items), word_w, mini)), oid, False)])
        if yc.get("decided"):
            t = Text()
            parts.chip(t, yc["decided"], parts.DIM)
            items.append([(Text("  ").append_text(t), "decided", False)])
        self._yc_rows = rows
        return items

    def _mark_focus(self, dl: OptionList) -> None:
        """`▌` on the highlighted Your call row and on no other (G7), redrawn in place as the highlight moves."""
        rows = getattr(self, "_yc_rows", [])
        if not rows:
            return
        w = max((dl.size.width or 60), 40)
        word_w = max(len(f"{r.get('mark')} {r.get('word')}" if r.get("mark") else r.get("word", "")) for r in rows)
        focused = self.focused is dl
        for i in range(1, min(dl.option_count, len(rows) + 1)):
            if dl.get_option_at_index(i).id in ("decisions-more", "decided"):
                break
            r = rows[i - 1]
            mini = mini_bar(self.chart_row(r.get("subject"))) if not self._wide and w >= 70 and r.get("kind") == "decide" else None
            dl.replace_option_prompt_at_index(i, glyphs(parts.call_row(r, w, focused and dl.highlighted == i, word_w, mini)))

    def toggle_filter(self) -> None:
        """⏎ on a Your call chip: only its kind's rows; ⏎ again (or esc) shows all (F3.1)."""
        chips = [c.get("kind") for c in ((self.home.get("ui") or {}).get("your_call") or {}).get("chips") or []]
        if not chips:
            return
        at = getattr(self, "_chip_at", None) or chips[0]
        self._filter = None if getattr(self, "_filter", None) == at else at
        self._chip_at = at
        self._fill_story(self.home)
        dl = self.query_one("#decisions-list", OptionList)
        dl.focus()
        dl.highlighted = 0
        self._paint_footer()

    def action_line(self, step: int) -> None:
        """j and k move by line, as ↓ and ↑ do (section 0 item 3)."""
        f = self.focused
        if isinstance(f, OptionList):
            f.action_cursor_down() if step > 0 else f.action_cursor_up()
        elif f is not None:
            self.focus_next() if step > 0 else self.focus_previous()

    def action_chip(self, step: int) -> None:
        """← → along the chips of Your call's head."""
        if self.focused is not self.query_one("#decisions-list", OptionList) or not self._on_head():
            return
        chips = [c.get("kind") for c in ((self.home.get("ui") or {}).get("your_call") or {}).get("chips") or []]
        if not chips:
            return
        at = getattr(self, "_chip_at", None)
        i = chips.index(at) if at in chips else -1
        self._chip_at = chips[max(0, min(len(chips) - 1, i + step))]
        self._fill_story(self.home)
        self._paint_footer()

    def _on_head(self) -> bool:
        dl = self.query_one("#decisions-list", OptionList)
        return dl.highlighted is not None and dl.option_count > 0 and dl.get_option_at_index(dl.highlighted).id == "yc-head"

    def _fill_others(self, others: list, width: int, findings: dict) -> None:
        """Other questions (R21): under Decisions, one row per other top-level question, its words then its strip, as
        rb status and the page give them; Enter makes it the story."""
        ol = self.query_one("#others-list", OptionList)
        keep = ol.highlighted
        ol.clear_options()
        if others:
            ol.add_option(Option(Text("OTHER QUESTIONS", style=ACCENT), id="others-head", disabled=True))
        for o in others:
            t = Text(no_wrap=False)
            for k, line in enumerate(ov_wrap(o.get("text", ""), width - 4)):
                t.append(("" if k == 0 else "\n") + f"┃ {line}", style="bold")
            for line in strip_lines(f"{(o.get('state') or '').capitalize()} · ", (o.get("strip") or {}).get("groups", []), width - 4, findings):
                t.append("\n  ")
                t.append_text(line)
            if self._words:
                t.append(f"\n  ({(o.get('strip') or {}).get('said', '')})", style=DIM)
            ol.add_option(Option(glyphs(t), id=f"q:{o.get('id')}"))
        ol.display = bool(others)
        self._others_need = sum(ol.get_option_at_index(i).prompt.plain.count("\n") + 1 for i in range(ol.option_count))
        if ol.option_count > 1:
            with self.prevent(OptionList.OptionHighlighted):
                ol.highlighted = min(keep, ol.option_count - 1) if keep else 1

    def _fill_lists(self, items: list, entries: list, question: Optional[str], width: int) -> None:
        """Decisions and the chart in the story's rows: all of both where they fit; else the first three decisions
        (all of them once their '+N more' row is opened) and as many chart rows as the rest leaves, each list's rest
        counted on its last row ('+3 more'). The chart's opens the question's card with every check."""
        room = getattr(self, "_story_room", None)
        dl = self.query_one("#decisions-list", OptionList)
        w = max(dl.size.width or width, 20)
        lines = lambda t: sum(max(1, -(-len(part) // w)) for part in t.plain.split("\n"))
        dec_need = [sum(lines(t) for t, _, _ in item) for item in items]
        chart_need = sum(t.plain.count("\n") + 1 for t, _ in entries)
        rests = (str(self.query_one("#story-rests", CountLine).render()).count("\n") + 2) if self._has_rests else 0
        shown_items = len(items)
        if not getattr(self, "_decisions_open", False):
            # the home shows Decisions as the web does: the first three rows of Your call's list, then "+n more", which
            # opens that list (UX lead, Nov 12 07:15 +08)
            shown_items = min(len(items), DECISION_ROWS + 1)       # the head, then three rows
            if room is not None and entries:
                # a short screen draws a chart row of the story all the same, as rb status does at 80x24: Decisions
                # folds sooner, down to its first row, to leave the chart its head and first row
                first = next((k for k, (_, cid) in enumerate(entries) if cid), None)
                if first is not None:
                    total = sum(1 for _, cid in entries if cid)
                    chart_first = sum(t.plain.count("\n") + 1 for t, _ in entries[: first + 1]) + (1 if total > 1 else 0)
                    used_by = lambda n: sum(dec_need[:n]) + (1 if n < len(items) else 0) + rests
                    while shown_items > 1 and room - used_by(shown_items) < chart_first:
                        shown_items -= 1
        keep = dl.highlighted
        dl.clear_options()
        for item in items[:shown_items]:
            for t, oid, disabled in item:
                dl.add_option(Option(glyphs(t), id=oid, disabled=disabled))
        rest = len([i for i in items[shown_items:] if i[0][1] != "decided"])
        if rest:
            dl.add_option(Option(Text(f"   +{rest} more", style=DIM), id="decisions-more"))
        dl.display = bool(items)
        if dl.option_count:
            # a refresh's highlight is no move of the user's: it never redraws the card beside the lists, nor puts a
            # row that waits in view for c
            with self.prevent(OptionList.OptionHighlighted):
                dl.highlighted = min(keep, dl.option_count - 1) if keep is not None else min(1, dl.option_count - 1)
        self.call_after_refresh(lambda: self._mark_focus(dl))
        used = sum(dec_need[:shown_items]) + (1 if shown_items < len(items) else 0) + rests + 2     # Your call's margins
        self._fill_chart(entries, question, None if room is None else room - used)

    def _open_decisions(self) -> None:
        """Your call's full list: every decision, confirmations included, each with its note, in Decisions' place (its
        '+N more' row opened; the chart then gives way, and the story scrolls). The home's rows are its first rows."""
        self._decisions_open = True
        self._fill_story(self.home)

    def action_your_call(self) -> None:
        """The pinned Your call (and y where no found row has the focus): its full list, focused on its first row."""
        if self.has_class("-catalogue"):
            self._close_catalogue()
        self._open_decisions()
        dl = self.query_one("#decisions-list", OptionList)
        if dl.option_count:
            dl.focus()
            dl.highlighted = min(1, dl.option_count - 1)   # its first row, past the head
            self.call_after_refresh(dl.focus)       # after the fill a narrow screen lays out again

    def _fill_chart(self, entries: list, question: Optional[str], room: Optional[int]) -> None:
        """The chart's rows in `room` rows (all where None); the rest counted on a last row ('+3 more')."""
        chart = self.query_one("#chart-list", OptionList)
        keep = chart.highlighted
        chart.clear_options()
        w = max(chart.size.width or getattr(self, "_chart_w", 0) or 40, 20)
        lines = lambda t: sum(max(1, -(-len(part) // w)) for part in t.plain.split("\n"))     # as the list wraps them
        total = sum(1 for _, cid in entries if cid)
        shown, at, cut = 0, 0, []
        for line, cid in entries:
            reserve = 1 if total - shown - (1 if cid else 0) > 0 else 0     # the '+N more' row, where rows are left
            if room is not None and at + lines(line) + reserve > room:
                break
            cut.append((line, cid))
            at += lines(line)
            shown += 1 if cid else 0
        while cut and not cut[-1][1]:
            cut.pop()                                           # never a panel's head (or its axis) with no row under it
        for k, (line, cid) in enumerate(cut):
            oid = f"o:{cid}:chart" if cid else f"chart-head-{k}"
            if cid:
                self._object_refs[oid] = cid
            chart.add_option(Option(glyphs(line), id=oid, disabled=not cid))
        if shown < total:
            oid = f"o:{question}:more" if question else "chart-more"
            if question:
                self._object_refs[oid] = question
            chart.add_option(Option(Text(f"  +{total - shown} more {'check' if total - shown == 1 else 'checks'}", style=DIM), id=oid, disabled=not question))
        if chart.option_count:
            # a row, never a panel's head, is highlighted: Tab onto the chart and Enter opens a check
            first = next((i for i in range(chart.option_count) if not chart.get_option_at_index(i).disabled), None)
            with self.prevent(OptionList.OptionHighlighted):
                chart.highlighted = min(keep, chart.option_count - 1) if keep is not None else first

    def _size_story(self) -> None:
        """The story's chart takes the rows its pane has once laid out (the middle pane, or the whole width below
        NARROW): Decisions first, then as many chart rows as fit, the rest counted; the screen never scrolls as a whole."""
        if not self.is_attached:
            return
        want = max(self.query_one("#story").size.height, 3)
        if getattr(self, "_story_room", None) != want:
            self._story_room = want
            self._fill_story(self.home)                 # the chart's rows, as many as now fit
        self.call_after_refresh(self._check_laid)

    def _check_laid(self) -> None:
        """The catalogue's rows fill the width they are drawn in: laid again once the layout settles at another."""
        if not self.is_attached:
            return
        tree, chart = self.query_one("#tree-list").size.width, self.query_one("#chart-list").size.width
        if tree not in (getattr(self, "_laid_w", None), 0) or chart not in (getattr(self, "_chart_w", None), 0):
            self._fill()

    def _fold_story(self, focused: Optional[str] = None) -> None:
        """The chart and what it rests on show wherever the story does (the catalogue has a pane, or a key, of its own)."""
        self.query_one("#chart-list", OptionList).display = getattr(self, "_has_chart", False)
        self.query_one("#story-rests", Static).display = getattr(self, "_has_rests", False)

    def _fill_files(self) -> None:
        """The Files tab: Cited, Changed, then every file by folder, each row its marks (as rb files prints them)."""
        if self._files is None:
            try:
                got = ask(self.root, "files", _place=self.place, _at=self.at)
                self._files = list(got.get("files", []))
                self._citations = dict(got.get("citations", {}))
            except Exception:  # noqa: BLE001 - a tab that cannot be read stays empty, the rest of the screen stands
                self._files, self._citations = [], {}
        fl = self.query_one("#files-list", OptionList)
        fl.clear_options()
        room = max((fl.size.width or self.query_one("#tree-list", OptionList).size.width or 30) - 2, 16)
        def marks(f, indent: str) -> str:
            # each mark with its words, one to a line, cut between words: no key explains a mark
            return "\n".join(indent + cut_words(f"{m['glyph']} {file_mark_words(m)}", room - len(indent))
                             for m in f.get("marks", []))
        def row(f, whole=True):
            t = Text(no_wrap=True, overflow="ellipsis")
            # the path on its own line, never wrapped mid-name: a long one gives way at its start, keeping its file
            name = f["path"] if whole else "  " + f["name"]
            t.append(name if len(name) <= room else ("" if whole else "  ") + "…" + name[len(name) - room + (2 if whole else 4):])
            if f.get("marks"):
                t.append("\n" + marks(f, "  " if whole else "    "), style=DIM)
            return t
        has = lambda f, k: any(m.get("key") == k for m in f.get("marks", []))
        # a file rb found that no run records is a row of Files (TUI-PARITY X2, M23, section 5 item 6): what it becomes,
        # and ⏎ opens its small panel, where y and n act
        found = [a for a in self._asks if list(a.get("argv") or [])[:2] == ["run", "add"]]
        if found:
            fl.add_option(Option(Text("FOUND", style=DIM), disabled=True))
            for a in found:
                argv = list(a.argv)
                path = argv[argv.index("--from") + 1] if "--from" in argv else a.title
                t = Text(no_wrap=True, overflow="ellipsis")
                t.append(path if len(path) <= room else "…" + path[len(path) - room + 1:])
                t.append(f"\n  → a run of {argv[2]}", style=DIM)
                fl.add_option(Option(t, id=f"found:{a.request_id}"))
        cited = sorted([f for f in self._files if has(f, "cited") or has(f, "setting")],
                       key=lambda f: -sum(m.get("n") or 0 for m in f.get("marks", []) if m.get("key") in ("cited", "setting")))
        changed = [f for f in self._files if has(f, "changed") or has(f, "uncommitted")]
        for title, group in (("CITED", cited), ("CHANGED", changed)):
            if group:
                fl.add_option(Option(Text(title, style=DIM), disabled=True))
                for f in group:
                    fl.add_option(Option(row(f), id=f"f:{f['path']}:{title}"))
        fl.add_option(Option(Text("FOLDERS", style=DIM), disabled=True))
        folder = None
        for f in sorted(self._files, key=lambda f: (f.get("folder", ""), f.get("name", ""))):
            if f.get("folder", "") != folder:
                folder = f.get("folder", "")
                fl.add_option(Option(Text(folder or "./", style="bold"), disabled=True))
            fl.add_option(Option(row(f, False), id=f"f:{f['path']}"))

    def action_files(self) -> None:
        """f: Files, never a toggle (F1.1): the left pane's Files tab, over the story below 120 columns."""
        self._tab = "files"
        self._fill_files()
        if not self._wide and not self.has_class("-catalogue"):
            self.set_class(True, "-catalogue")
            self.call_after_refresh(self._fill)
        self._fill_story(self.home)
        self.query_one("#files-list", OptionList).focus()

    def action_key(self) -> None:
        """?: every key with three words at most (M15); ? again or esc closes it."""
        self.app.push_screen(panels.KeyPanel())

    # ------------------------------------------------------------ copy, decide, where, also open (items 1, 6, 13, 14)

    def copy_text(self, text: str) -> Optional[str]:
        """p's routes (section 0 item 5): `copied`, `copied to tmux`, or None, after which the terminal was sent OSC 52 and
        the prompt view showed the text itself; no chip claims a copy rb ui cannot see."""
        said = clip.copy(text)
        if said:
            return said
        try:
            with self.app.suspend():
                sys.stdout.write(clip.prompt_view(text))
                sys.stdout.flush()
                clip.wait_for_a_key()
        except Exception:  # noqa: BLE001 - no suspend here (a test, a web driver): the prompt alone, full screen
            try:
                self.app._driver.write(clip.osc52(text))          # noqa: SLF001 - the terminal is the driver's
            except Exception:  # noqa: BLE001
                pass
            self.app.push_screen(panels.PromptView(text))
        return None

    def _next_step_in_focus(self) -> Optional[str]:
        """The Next step prompt of the focused item, where it has one (a question nothing tests, an experiment with no
        run), from the bridge."""
        steps = (self.home.get("ui") or {}).get("next_steps") or {}
        f = self.focused
        if isinstance(f, OptionList) and f.highlighted is not None and f.option_count:
            oid = f.get_option_at_index(f.highlighted).id or ""
            subject = self._object_refs.get(oid) or (oid[2:].split(":")[0] if oid.startswith("o:") else "")
            return steps.get(subject)
        return None

    def action_copy(self) -> None:
        """p: the focused Next step; else, on a card, the command that shows it; else Continue this research (M13). At a
        past version nothing: it offers no command to copy (guarantee 38)."""
        if self.at is not None:
            return
        if isinstance(self.focused, CardView):
            said = self.copy_text(card_copy_text(self.focused))
            if said:
                self.focused.show_flash(said)
            return
        step = self._next_step_in_focus()
        if step:
            said = self.copy_text(step)
            if said:
                self._flash_side(said)
            return
        if isinstance(self.focused, Cite) and self._side_for:
            subject = self._object_refs.get(self._side_for) or self._side_for.split(":")[1]
            said = self.copy_text(f"rb show {subject}")
            if said:
                self._flash_side(said)
            return
        ui = self.home.get("ui") or {}
        if not ui.get("continue"):
            return

        def go(kind: Optional[str]) -> None:
            if kind:
                self._filter = kind
                self.action_your_call()
        self.app.push_screen(panels.ContinuePanel(ui["continue"], ((ui.get("your_call") or {}).get("chips")) or [], self.copy_text), go)

    def _flash_side(self, said: str) -> None:
        """The chip a copy shows for 3 seconds: in place of the box's `⧉` where the preview has a box, else in the
        preview's border."""
        card = self.query_one("#side-card")
        was = card.border_subtitle
        self._flash = said
        if self._side_for:
            self._show_side(self._side_for)
        if "⧉" not in "".join(str(w.render()) for w in card.query(Static)):
            card.border_subtitle = f"[{said}]"

        def off() -> None:
            self._flash = None
            card.border_subtitle = was
            if self._side_for:
                self._show_side(self._side_for)
        self.set_timer(panels.CHIP_SECONDS, off)

    def _decision_in_focus(self) -> Optional[str]:
        dl = self.query_one("#decisions-list", OptionList)
        if self.focused is dl and dl.highlighted is not None and dl.option_count:
            oid = dl.get_option_at_index(dl.highlighted).id or ""
            if oid.startswith("o:"):
                return self._object_refs.get(oid) or oid.split(":")[1]
        return None

    def decide_on(self, subject: str, title: str, after: Optional[Callable[[], None]] = None) -> None:
        """Decide (M14) on `subject`, from its card: after rb decide, the card shows the decision."""
        if not self.at_start:
            return

        def go(choice: Optional[tuple]) -> None:
            if not choice:
                return
            action, why = choice

            def done() -> None:
                self.refresh_home()
                if after is not None:
                    after()
            run_settle(self.app, self.root, ["decide", subject, action, "--why", why], f"{action}ed {subject}" if action != "investigate" else f"investigating {subject}", done)
        self.app.push_screen(panels.DecidePanel(subject, title), go)

    def action_decide(self) -> None:
        """d on a decision row: accept, reject or investigate with a reason (M14), recorded with rb decide."""
        view = next(iter(self.query("#card-view")), None)
        if view is not None and self.focused is view:
            if card_footer(view, self.at_start) == "card.decide":
                self.decide_on(view.data.subject, view.data.card.get("title", ""), after=view.refresh)
            return
        subject = self._decision_in_focus()
        if subject is None or not self.at_start:
            return
        rows = ((self.home.get("ui") or {}).get("your_call") or {}).get("rows") or []
        title = next((r.get("title", "") for r in rows if r.get("subject") == subject), subject)
        dl = self.query_one("#decisions-list", OptionList)
        at = dl.highlighted or 0

        def go(choice: Optional[tuple]) -> None:
            if not choice:
                return
            action, why = choice

            def after() -> None:
                self.refresh_home()
                dl.focus()
                dl.highlighted = min(at, max(dl.option_count - 1, 0))
            run_settle(self.app, self.root, ["decide", subject, action, "--why", why], f"{action}ed {subject}" if action != "investigate" else f"investigating {subject}", after)
        self.app.push_screen(panels.DecidePanel(subject, title), go)

    def where_rows(self) -> list:
        """WHERE's rows (M17), from the place rb lists and the header's facts."""
        head = self.home.get("head") or {}
        info = self._places
        this = next((p for p in (info.places if info is not None else []) if p.record == "this"), None)
        co = (this or {}).get("checkout") or {}
        rows = [{"label": "branch", "value": co.get("name") or co.get("branch") or "", "opens": "places"},
                {"label": "commit", "value": (head.get("git") or "").split(" · ")[-1], "copy": head.get("commit") or ""}]
        n = co.get("record_files_not_committed") or 0
        if n:
            rows.append({"label": "record files", "value": f"± {n} not committed ›", "style": parts.WARN, "opens": "files"})
        else:
            rows.append({"label": "record files", "value": co.get("said") or ""})
        rows.append({"label": "other files", "value": "± not committed" if co.get("changes_outside_record") else "committed"})
        for p in (info.places if info is not None else []):
            rows.append({"label": "watched", "value": p.get("label_short") or p.get("label") or "", "opens": f"place:{p.id}"})
        return rows

    def action_where(self) -> None:
        """w: WHERE as rows (M17); ⏎ on commit copies its whole hash, on a watched place shows it."""
        self._load_places()

        def go(opens: Optional[str]) -> None:
            if opens == "files":
                self.action_files()
            elif opens == "places":
                self.action_places()        # each place and each version of the record, to show one, read only
            elif opens and opens.startswith("place:"):
                self.switch(opens[6:], None)
        self.app.push_screen(panels.WherePanel(self.where_rows(), self.copy_text), go)

    def action_words(self) -> None:
        """W: under each figure, the words it shows."""
        self._words = not self._words
        self._fill_story(self.home)

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

    def _confirm_list(self) -> OptionList:
        """The list c acts on: Decisions, which lists everything that waits for a confirmation (R12)."""
        return self.query_one("#decisions-list", OptionList)

    def _confirm_row(self) -> Optional[View]:
        """The one that waits that c acts on: the confirm list's highlighted row; on Decisions, its highlighted row where
        that is one, else its first that is (and the highlight moves there)."""
        rows = getattr(self, "_pending", [])
        if not rows:
            return None
        lst = self._confirm_list()
        ids = [lst.get_option_at_index(i).id for i in range(lst.option_count)]
        if not any((o or "").startswith("p:") for o in ids):
            self._open_decisions()                          # what waits sits under Decisions' '+N more': open it
            ids = [lst.get_option_at_index(i).id for i in range(lst.option_count)]
        at = lst.highlighted
        if at is None or at >= len(ids) or not (ids[at] or "").startswith("p:"):
            at = next((i for i, o in enumerate(ids) if (o or "").startswith("p:")), None)
            if at is None:
                return None
            with self.prevent(OptionList.OptionHighlighted):
                lst.highlighted = at
        return next((r for r in rows if f"p:{r.id}" == ids[at]), None)

    def _show_pending(self, row: View) -> None:
        """Show one that waits for a confirmation: beside the list, or full screen where no card sits beside it."""
        if self._wide:
            self._show_side(f"p:{row.id}")
            self._in_view = row.id if self._side_for == f"p:{row.id}" else None
        else:
            self.app.push_screen(PendingScreen(row, self.at_start, self._confirm_shown))
            self._in_view = row.id

    def _subject(self, ref: Optional[str]) -> Optional[str]:
        """What a row's option id names for the evidence pane: a record, a question, a file's path, one that waits for
        a confirmation (p:<id>) or something rb found (found:<id>)."""
        if ref is None:
            return None
        if ref.startswith("o:"):
            return self._object_refs.get(ref)
        if ref.startswith("q:"):
            return ref[2:]
        if ref.startswith("p:"):
            return ref
        if ref.startswith("f:"):
            return ref[2:].rsplit(":", 1)[0] if ref.count(":") > 1 else ref[2:]
        return f"found:{ref}"

    def evidence_for(self, subject: str) -> Optional[tuple]:
        """The widgets and the title of `subject`'s card: rb's card model where it has one (a check, run, setting,
        commit, file, folder, decision, experiment); a question's checks as chart rows; else the record's own card."""
        side = self.query_one("#side-card")
        inner = side.content_size.width
        width = (inner - 1 if inner >= 20 else max(side.size.width - 7, 24)) if self._wide else max(self.size.width - 6, 36)
        if subject.startswith("p:"):
            row = next((r for r in getattr(self, "_pending", []) if f"p:{r.id}" == subject), None)
            return None if row is None else (pending_widgets(row, self.at_start), "CONFIRM")
        if subject.startswith("found:"):
            return build_card_widgets(self._found_card(subject[6:])), "FOUND BY RB"
        ui = self.home.get("ui") or {}
        if subject in {r.id for r in self.home.tree if r.kind == "question"} and self._wide:
            # the preview of a question (M7, F3.4): its marks, or its chip, outlines and Next step; the story's own
            # question previews its Continue this research box, never the story's title, strip or chart
            states = self.tree_states(self.home).get(subject, {})
            story = ((self.home.get("overview") or {}).get("question") or {}).get("id")
            words = next((r.label for r in self.home.tree if r.id == subject), subject)
            lines = question_preview(words, compact_marks(states, self._findings(self.home)), NOT_TESTED,
                                     (ui.get("next_steps") or {}).get(subject), ui.get("continue") if subject == story else None,
                                     width + 1, getattr(self, "_flash", None))
            return [Static(t, classes="ev-item") for t in lines], self.crumb_in_tree(subject)
        if subject in {r.get("id") for r in ui.get("resources") or []} and self._wide:
            facts = next(r for r in ui["resources"] if r.get("id") == subject)
            lines = resource_preview(facts, width + 1, getattr(self, "_flash", None), (ui.get("next_steps") or {}).get(subject))
            return [Static(t, classes="ev-item") for t in lines], "Resources"
        if subject in {r.id for r in self.home.tree if r.kind == "question"}:
            ov = question_overview(self.root, subject, self.place, self.at)
            return question_widgets(ov, width, self._findings(self.home)), "QUESTION"
        card = card_model(self.root, subject, self.place, self.at)
        if card and card.get("kind") == "check" and self._wide:
            # the preview of a focused check (F3.4, M1): title, state chips, the figure and its chips; ⏎ opens the card
            row = self.chart_row(subject, card)
            chips = {"chips": card.get("chips")} if card.get("chips") is not None else None
            return [Static(t, classes="ev-item") for t in preview_lines(row, card, width + 1, chips)], crumb_of(card)
        if card:
            return card_widgets(card, width), (card.get("kind") or "").upper()
        try:
            c = self._object_card(subject)
        except Exception:  # noqa: BLE001 - nothing by that name: nothing drawn
            return None
        return build_card_widgets(c), c.who_line.split(" · ")[0].upper()

    def crumb_in_tree(self, subject: str) -> str:
        """A tree item's crumb (F2.1): the question it sits under and its id, `q1 › q3`."""
        stack: list = []
        for r in self.home.tree:
            if r.kind != "question":
                continue
            del stack[r.depth:]
            if r.id == subject:
                return f"{stack[-1]} › {subject}" if stack else subject
            stack.append(r.id)
        return subject

    def action_get_it(self) -> None:
        """g: open the focused resource's Get it address on this machine; over SSH, copy it by the p routes (L5)."""
        if isinstance(self.focused, CardView):
            if self.focused.data.get_it:
                card_act(self, self.focused, ("getit", self.focused.data.get_it))
            return
        tree = self.query_one("#tree-list", OptionList)
        if tree.highlighted is None or not tree.option_count:
            return
        subject = self._object_refs.get(tree.get_option_at_index(tree.highlighted).id or "")
        facts = next((r for r in (self.home.get("ui") or {}).get("resources") or [] if r.get("id") == subject), None)
        address = ((facts or {}).get("get_it") or {}).get("address")
        if not address:
            return
        said = clip.open_address(address) or self.copy_text(address)
        if said:
            self._flash = said
            self._show_side(self._side_for)
            def off() -> None:
                self._flash = None
                self._show_side(self._side_for)
            self.set_timer(panels.CHIP_SECONDS, off)

    def chart_row(self, cid: str, card: Optional[View] = None) -> dict:
        """A check's chart row as the Overview gives it; one the story does not chart is its card's figure, with the
        card's own words for its numbers."""
        for r in (self.home.get("overview") or {}).get("chart") or []:
            if r.get("id") == cid:
                return dict(r)
        card = card or card_model(self.root, cid, self.place, self.at) or {}
        res = next((f for f in card.get("fields", []) if f.get("key") == "result"), {}) or {}
        kind = next((f.get("value") for f in card.get("fields", []) if f.get("key") == "kind"), "")
        row = dict(res.get("figure") or {})
        said, _, rng = (res.get("value") or "").partition("  ")
        row.update(id=cid, label=card.get("title", ""), result_text=said, range_text=rng, claim=kind == "claim",
                   measure_unit=said.split(" ", 1)[1] if " " in said else "")
        return row

    def _show_side(self, ref: Optional[str]) -> None:
        if ref and ref.startswith("o:") and ref not in self._object_refs:
            return                                  # an event queued for a row removed by a refresh
        pane = self.query_one("#side-card", Evidence)
        if not pane.is_attached:
            return                                  # an event queued as the screen closes: nothing left to draw on
        try:
            shown = pane.show(self._subject(ref))
        except Exception:  # noqa: BLE001 - a half-readable card still leaves the screen standing
            pane.remove_children()
            pane.mount(Static(f"[{DIM}]this cannot be drawn right now[/{DIM}]"))
            shown = True
        self._side_for = ref if shown and ref else None

    def open_target(self, target: tuple, pane: Evidence) -> None:
        """A citation, opened: a card on the pane's trail, the long form, or a command (a git command) to copy."""
        kind, what = target
        if kind == "card":
            try:
                shown = pane.show(what, push=True)
            except Exception:  # noqa: BLE001
                shown = False
            if shown:
                first = pane.first_cite()
                if first is not None:
                    first.focus()
            else:
                self.app.notify(f"rb has no card of {what}", severity="warning", timeout=6)
        elif kind == "long":
            self.open_long(what)
        else:
            try:
                self.app.copy_to_clipboard(what)
            except Exception:  # noqa: BLE001
                pass
            self.app.notify(what, title="copied", timeout=6)

    def long_text(self, subject: str) -> Optional[str]:
        """`rb show <subject> --long`, as the CLI prints it, read where the screen shows the record; None where rb
        cannot say it (a past version or another place, which a command here does not read)."""
        if not self.at_start:
            return None
        try:
            r = subprocess.run([*_rb.command(), "show", subject, "--long"], cwd=self.root, env=_rb.environment(),
                               capture_output=True, encoding="utf-8", errors="replace", timeout=60)
        except Exception:  # noqa: BLE001
            return None
        return r.stdout if r.returncode in (0, 1) and r.stdout.strip() else None

    def open_count(self, action: tuple) -> None:
        """⏎ on a count (N5): a strip group opens its checks as a list; what the work rests on opens the card its count
        names (a question on its Checks tab, an experiment, the runs as the Runs table)."""
        if action[0] == "checks":
            data = self.list_card(action[1], action[2])
            if data is not None:
                self.app.push_screen(CheckCardScreen(self, data))
            return
        target = action[1]
        if target == "runs":
            exps = [r.id for r in self.home.tree if r.kind == "experiment"]
            target, tab = (exps[0], "Runs") if len(set(exps)) == 1 else ("", "")
        else:
            tab = "Checks" if target in {r.id for r in self.home.tree if r.kind == "question"} else ""
        data = self.card_data(target) if target else None
        if data is None:
            return
        self.app.push_screen(CheckCardScreen(self, data, data.tabs.index(tab) if tab in data.tabs else 0))

    def list_card(self, ids: list, label: str) -> Optional[CardData]:
        """A strip group's checks as a list (N5): each check's mark and word, its title and its id, ⏎ opening its card."""
        rows = {r.get("id"): r for r in (self.home.get("overview") or {}).get("chart") or []}
        if not ids:
            return None
        qid = ((self.home.get("overview") or {}).get("question") or {}).get("id") or ""

        def bodies(width: int) -> dict:
            idw = max(len(c) for c in ids)
            return {"": [(check_row(c, rows.get(c) or {"state": "untested"}, width, idw), ("open", c)) for c in ids]}

        return CardData(subject=qid, card={"title": f"{len(ids)} {label}", "id": qid}, row={}, crumb=qid, kind="long",
                        tabs=(), bodies=bodies)

    def long_card(self, qid: str) -> Optional[CardData]:
        """A question's long form (M19): one block per check, its mark, title and dim id, then labelled rows with a mark
        each, shown only where the record holds the value: Result, Rule, Written, Runs, Settings not checked, Source."""
        row = next((r for r in self.home.tree if r.id == qid and r.kind == "question"), None)
        if row is None:
            return None
        try:
            rows = list((question_overview(self.root, qid, self.place, self.at) or {}).get("chart") or [])
        except Exception:  # noqa: BLE001
            rows = []
        blocks = []
        for r in rows:
            cid = r.get("id", "")
            card = card_model(self.root, cid, self.place, self.at) or {}
            blocks.append((cid, r, {f.get("key"): f for f in card.get("fields", [])}))

        def bodies(width: int) -> dict:
            out: list = []
            idw = max([len(c) for c, _r, _f in blocks] or [0])
            for cid, r, f in blocks:
                mark = STATE_MARK.get(r.get("state"), "□")
                title = ("[claim] " if r.get("claim") else "") + (r.get("label") or "")
                lines = wrap_words(title, max(width - idw - 5, 20))
                for n, line in enumerate(lines):
                    t = Text(no_wrap=True, overflow="crop")
                    t.append(f"{mark} " if not n else "    ", style=parts.MARK_COLOUR.get(mark, "") if not n else "")
                    t.append(f"{line:<{max(width - idw - 4, 20)}}")
                    if not n:
                        t.append(f"  {cid:>{idw}}", style=DIM)
                    out.append((t, ("open", cid) if not n else None))
                said = []
                if r.get("result_text"):
                    said.append(("Result", Text.assemble((f"{mark} {STATE_WORD.get(mark, '')}", parts.MARK_COLOUR.get(mark, "")),
                                                         f"  {r.get('result_text')}", f"  {r.get('range_text') or ''}".rstrip()), None))
                passes = (f.get("passes") or {}).get("value") or ""
                if r.get("claim") and r.get("threshold") is not None:
                    edge = (r.get("edge_text") or "").replace(" to ", "…")
                    said.append(("Rule", Text.assemble(("◆", GOOD), f" {claim_text(r)}  {edge}".rstrip()), None))
                elif passes:
                    said.append(("Rule", Text.assemble(("░", GOOD), passes[1:] if passes.startswith("░") else " " + passes), None))
                written = (f.get("written") or {}).get("value") or ""
                if written:
                    when = written.split("·", 1)[1].strip() if "·" in written else written
                    said.append(("Written", Text.assemble(("‾ ", WARN if written.startswith("‾") else DIM), when.replace("──▶", "──→")), None))
                units = (f.get("units") or {}).get("value") or ""
                if units:
                    said.append(("Runs", Text.assemble(units, (" ›", DIM)), ("open", cid)))
                unchecked = [i for i in (f.get("settings") or {}).get("items", []) if "not checked" in (i.get("said") or "")
                             or (f.get("settings") or {}).get("said", "").startswith("○")]
                if unchecked:
                    said.append(("Settings", Text.assemble(("?", WARN), f" {len(unchecked)} not checked", (" ›", DIM)), ("open", cid)))
                for i in (f.get("cited") or {}).get("items", []):
                    path = (i.get("text") or "").split(":")[0]
                    gone = bool(path) and not (self.root / path).exists()
                    said.append(("Source", Text.assemble(("∅ " if gone else "", WARN), path or i.get("text", ""), (" ›", DIM)),
                                 ("open", i.get("text"))))
                for label, value, act in said:
                    out.append((Text.assemble("  ", (f"{label:<11}", DIM), value), act))
            return {"": out}

        return CardData(subject=qid, card={"title": row.label, "id": qid}, row={}, crumb=qid, kind="long", tabs=(), bodies=bodies)

    def open_long(self, subject: str) -> None:
        """l: the long form: a question's as labelled rows (M19); else the record's own card where rb ui has one, else
        rb show --long's text."""
        data = self.long_card(subject)
        if data is not None:
            self.app.push_screen(CheckCardScreen(self, data))
            return
        try:
            self.app.push_screen(ObjectScreen(self.root, subject, self.place, self.at))
            return
        except Exception:  # noqa: BLE001 - a setting, a decision, a path or a commit: rb show --long says it
            pass
        text = self.long_text(subject)
        if text is None:
            self.app.notify(f"rb show {subject} --long", title="in a terminal", timeout=8)
        else:
            self.app.push_screen(LongScreen(subject, text))

    def card_data(self, subject: str) -> Optional[CardData]:
        """An open card's parts (M4 to M6, M8, M11, M20), each from the bridge; None for what rb ui draws no card of."""
        if subject in {r.id for r in self.home.tree if r.kind == "question"}:
            return self.question_card(subject)
        if subject in {r.get("id") for r in (self.home.get("ui") or {}).get("resources") or []}:
            return self.resource_card(subject)
        card = card_model(self.root, subject, self.place, self.at)
        if card and card.get("kind") == "experiment":
            return self.experiment_card(subject, card)
        if not card or card.get("kind") != "check":
            return None
        row = self.chart_row(subject, card)
        runs_f = next((f for f in card.get("fields", []) if f.get("key") == "runs"), {}) or {}
        run_cards = []
        for i in runs_f.get("items", []):
            rc = card_model(self.root, i.get("text", ""), self.place, self.at)
            if rc:
                run_cards.append(rc)
        runs, cols, metric = run_rows(run_cards)
        head = (card.get("fields") or [{}])[0]
        qid = next((i.get("text") for i in head.get("items", []) if (i.get("text") or "").startswith("q")), "")
        qwords = next((r.get("label") or r.get("text") or "" for r in self.home.tree if r.get("id") == qid), "")
        cited = next((f for f in card.get("fields", []) if f.get("key") == "cited"), None)
        for i in (cited or {}).get("items", []):
            path = (i.get("text") or "").split(":")[0]
            i["missing"] = bool(path) and not (self.root / path).exists()
        marks = {"result": "●", "passes": "░", "units": "▰", "written": "‾", "settings": "?"}
        working = [(marks[f["key"]], f.get("said") or "") for f in card.get("fields", [])
                   if f.get("key") in marks and f.get("said") and f.get("said") != f.get("value")]
        return CardData(subject=subject, card=card, row=row, crumb=crumb_of(card), runs=runs, run_cols=cols,
                        metric=metric, question=(qid, qwords), working=working,
                        finding={"chips": card.get("chips")} if card.get("chips") is not None else None)

    def question_card(self, qid: str) -> CardData:
        """A question's card (M8): its crumb and words, its state chip, tabs Result, Checks, Experiments, Used by and
        Source; Result holds its chart (its checks, each opening its card), or the outlines and its Next step box."""
        tree = self.home.tree
        row = next(r for r in tree if r.id == qid)
        states = self.tree_states(self.home).get(qid, {})
        ui = self.home.get("ui") or {}
        findings = self._findings(self.home)
        try:
            ov = dict(question_overview(self.root, qid, self.place, self.at) or {})
        except Exception:  # noqa: BLE001 - an older rb: the card says what the tree knows
            ov = {}
        rows = list(ov.get("chart") or [])
        at = tree.index(row)
        exp_rows = []
        for r in tree[at + 1:]:
            if r.depth <= row.depth:
                break
            if r.kind == "experiment" and r.depth == row.depth + 1:
                exp_rows.append(r)
        crumb = self.crumb_in_tree(qid)
        up, _, _ = crumb.rpartition(" › ")
        upw = next((r.label for r in tree if r.id == up), "")
        outline_rows = 3 if self._wide else 1

        def bodies(width: int) -> dict:
            result: list = []
            if rows:
                drawn = ov_chart(rows, width, findings)
                # each check takes focus on the line that says its state, never on its axis labels
                said = {cid for t, cid in drawn if cid and any(w in t.plain for w in STATE_WORD.values())}
                for t, cid in drawn:
                    on = cid and (cid not in said or any(w in t.plain for w in STATE_WORD.values()))
                    result.append((t, ("open", cid) if on else None))
            else:
                result += [(("outline", "checks", outline_rows), None), (("outline", "experiment", outline_rows), None)]
            checks = [(check_row(r.get("id", ""), r, width, max(len(x.get("id", "")) for x in rows)), ("open", r.get("id")))
                      for r in rows]
            exps = []
            for r in exp_rows:
                t = Text(no_wrap=True, overflow="crop")
                t.append("▸ ", style=DIM)
                t.append(f"{r.id} ", style="bold")
                t.append(parts.cut_words(r.label, max(width - len(r.id) - 6, 10)))
                t.append(" ›", style=DIM)
                exps.append((t, ("open", r.id)))
            used = []
            if up:
                t = Text.assemble(("┃ ", "bold"), (up, "bold"), "  ", parts.cut_words(upw, max(width - len(up) - 8, 10)), ("  ›", DIM))
                used.append((t, ("open", up)))
            return {"Result": result, "Checks": checks, "Experiments": exps, "Used by": used}

        chips = compact_marks(states, findings)
        if not chips.plain:
            chips = chip_text(NOT_TESTED)
        story = ((self.home.get("overview") or {}).get("question") or {}).get("id")
        prompt = (ui.get("next_steps") or {}).get(qid) if not rows else None
        if prompt is None and qid == story and not rows:
            prompt = ui.get("continue")
        return CardData(subject=qid, card={"title": row.label, "id": qid}, row={}, crumb=crumb, kind="question",
                        tabs=("Result", "Checks", "Experiments", "Used by", "Source"), chips=chips,
                        counts={"Checks": len(rows), "Experiments": len(exp_rows)}, bodies=bodies, prompt=prompt)

    def experiment_card(self, eid: str, card: dict) -> CardData:
        """An experiment's card (M20): `▸ experiment` apart from its state chip, `[design not confirmed ›]` (c), its checks
        by title with their state, its settings with ✓ and ?, its runs; every word rb's card model's."""
        f = {x.get("key"): x for x in card.get("fields", [])}
        head = f.get("experiment") or {}
        chips = Text(no_wrap=True, overflow="crop")
        chips.append("▸ experiment", style=DIM)
        state = head.get("value") or ""
        if state:
            chips.append("  ")
            parts.chip(chips, state[:1].upper() + state[1:], style=WARN if state == "open" else "")
        design = next((p for p in getattr(self, "_pending", []) if p.kind == "design" and p.id.split("@")[0] == eid), None)
        if design is not None:
            chips.append("  ")
            parts.chip(chips, "design not confirmed ›" if self.at_start else "design not confirmed", style=WARN)
        overview_rows = {r.get("id"): r for r in (self.home.get("overview") or {}).get("chart") or []}
        marks = [((i.get("text") or "").partition(" ")[0], (i.get("text") or "").partition(" ")[2])
                 for i in (f.get("checks") or {}).get("items", [])]
        sf, rf = f.get("settings") or {}, f.get("runs") or {}

        def bodies(width: int) -> dict:
            idw = max([len(c) for _m, c in marks] or [0])
            checks = []
            for mark, cid in marks:
                r = dict(overview_rows.get(cid) or {})
                r.setdefault("state", next((k for k, v in STATE_MARK.items() if v == mark), "untested"))
                checks.append((check_row(cid, r, width, idw), ("open", cid)))
            settings: list = []
            if sf.get("value"):
                settings.append((Text(sf["value"].replace("○", "?")), None))
            for i in sf.get("items", []):
                mark, _, name = (i.get("text") or "").partition(" ")
                t = Text(no_wrap=True, overflow="crop")
                # F's words for a setting's status (rec-2 8842775, views.rs status_mark)
                glyph, word, style = {"○": ("?", "not checked", WARN), "?": ("?", "unknown", WARN),
                                      "↓": ("↓", "inherited", DIM)}.get(mark, ("✓", "checked", GOOD))
                t.append(f"{glyph} ", style=style)
                t.append(name)
                t.append(f" · {word}", style=DIM)
                said = i.get("said") or ""
                hover = said.split(": ", 1)[1] if ": " in said else ""
                target = (i.get("opens") or "").removeprefix("rb show ")
                settings.append((t, ("open", target, hover) if target else None))
            runs = []
            for i in rf.get("items", []):
                target = (i.get("opens") or "").removeprefix("rb show ")
                more = target.endswith("--long")
                runs.append((Text(i.get("text") or "", style=DIM if more else ""), ("long", eid) if more else ("open", target)))
            result = checks[:3] + ([(Text(f"  +{len(checks) - 3} more ›", style=DIM), ("tabname", "Checks"))] if len(checks) > 3 else [])
            if settings:
                result = result + [(Text(""), None)] + settings
            design_rows: list = []
            for key in ("hypothesis", "variants", "varies"):
                x = f.get(key)
                if not x:
                    continue
                value = x.get("value") or ", ".join(i.get("text", "") for i in x.get("items", []))
                for n, line in enumerate(wrap_words(value, max(width - 11, 20))):
                    design_rows.append((Text.assemble((f"{(x.get('label') or '').lower() if not n else '':<11}", DIM), line), None))
            if design is not None:
                design_rows.append((Text(""), None))
                t = Text()
                at_start = self.at_start                   # away from where it started the row says the state, and acts not
                parts.chip(t, "design not confirmed ›" if at_start else "design not confirmed", style=WARN)
                design_rows.append((t, ("confirm", eid) if at_start else None))
            return {"Result": result, "Checks": checks, "Runs": runs, "Design": design_rows}

        n_runs = (rf.get("value") or "").split(" ")[0]
        has_design = design is not None
        data = CardData(subject=eid, card=card, row={}, crumb=crumb_of(card), kind="experiment",
                        tabs=("Result", "Checks", "Runs", "Design", "Source"), chips=chips,
                        counts={"Checks": len(marks), "Runs": int(n_runs) if n_runs.isdigit() else len(rf.get("items", []))},
                        bodies=bodies)
        data.card = dict(card, design_waits=has_design)
        return data

    def resource_card(self, rid: str) -> CardData:
        """A resource's card (M11): crumb `Resources`, its name, its kind, Get it or no download link (the record holds
        no licence, so none is said), the missing line where it has no address, its runs and checks as counts that
        open, and the Next step box."""
        ui = self.home.get("ui") or {}
        facts = next(r for r in ui.get("resources") or [] if r.get("id") == rid)
        chips = Text(no_wrap=True, overflow="crop")
        for words, style in ((facts.get("type"), DIM), ):
            if words:
                parts.chip(chips, words, style=style)
        got = facts.get("get_it")
        chips.append("  ")
        parts.chip(chips, f"↗ Get it · {got.get('label')}" if got else "no download link", style="" if got else WARN)
        body: list = []
        if facts.get("missing") and not got:  # noqa: SIM102
            body.append((Text(facts["missing"], style=DIM), None))
            body.append((Text(""), None))
        if got:
            address = (got.get("address") or "")
            body.append((Text(address.split("://", 1)[-1], style=DIM), ("getit", address)))
            body.append((Text(""), None))
        counts = Text.assemble(f"{facts.get('runs', 0)} runs", (" ›", DIM), "   ", f"{facts.get('checks', 0)} checks", (" ›", DIM))
        body.append((counts, ("long", rid)))
        if got and facts.get("missing"):
            body.append((Text(""), None))
            body.append((Text(facts["missing"], style=DIM), None))
        return CardData(subject=rid, card={"title": facts.get("name") or rid, "id": rid}, row={}, crumb="Resources",
                        kind="resource", tabs=(), chips=chips, bodies={"": body},
                        prompt=None if got else (ui.get("next_steps") or {}).get(rid), get_it=(got or {}).get("address"))

    def open_card(self, subject: str, tab: int = 0) -> bool:
        """⏎ on a check: its card over the story and the preview at 120x40 (the tree stays), full screen below."""
        data = self.card_data(subject)
        if data is None:
            return False
        if self._wide:
            # a card already open takes the new one in place: a removed card leaves the DOM only after a refresh, so
            # mounting another `#card-view` beside it at once would be two with one id
            live = [v for v in self.query("#card-view") if not getattr(v, "rb_closed", False)]
            if not live and self.query("#card-view"):
                self.call_after_refresh(self.open_card, subject, tab)    # the one closing goes first
                return True
            self._card_from = self._card_from if getattr(self, "_card_from", None) else self.focused
            if live:
                view = live[0]
                view.data, view.past, view.tab, view.at, view.unfolded, view.flash = data, self.at is not None, tab, 0, False, None
                view.refresh(layout=True)
            else:
                view = CardView(data, past=self.at is not None, id="card-view")
                view.tab = tab
                self.query_one("#panes").mount(view)
            self.query_one("#middle").display = False
            self.query_one("#side-card").display = False
            view.focus()
        else:
            self.app.push_screen(CheckCardScreen(self, data))
        return True

    def close_card(self, refocus: bool = True) -> bool:
        views = [v for v in self.query("#card-view") if not getattr(v, "rb_closed", False)]
        if not views:
            return False
        for v in views:
            v.rb_closed = True
            v.remove()
        self.query_one("#middle").display = True
        self.query_one("#side-card").display = True
        back, self._card_from = getattr(self, "_card_from", None), None
        if refocus:
            (back if back is not None and back.is_attached else self._first_list()).focus()
        return True

    def on_card_view_act(self, message: CardView.Act) -> None:
        card_act(self, message.view, message.what)

    def open_evidence(self, ref: str) -> None:
        """Enter on a row: its card in the evidence pane, the focus on its first citation; below NARROW, over the story."""
        subject = self._subject(ref)
        if subject is None:
            return
        if self.open_card(subject):
            return
        if self._wide:
            if self._side_for != ref:
                self._show_side(ref)
            first = self.query_one("#side-card", Evidence).first_cite()
            if first is not None:
                first.focus()
        else:
            self.app.push_screen(EvidenceScreen(self, subject))

    def on_cite_opened(self, message: Cite.Opened) -> None:
        self.open_target(message.cite.target, self.query_one("#side-card", Evidence))

    def action_long(self) -> None:
        if isinstance(self.focused, CardView):
            if self.focused.data.kind in ("question", "check"):
                self.open_long(self.focused.data.subject)
            return
        pane = self.query_one("#side-card", Evidence)
        subject = pane.trail[-1] if pane.trail else None
        focused = self.focused
        if isinstance(focused, OptionList) and focused.highlighted is not None:
            subject = self._subject(focused.get_option_at_index(focused.highlighted).id) or subject
        if subject and not subject.startswith(("p:", "found:")):
            self.open_long(subject)

    def _close_catalogue(self) -> None:
        """esc off the catalogue below NARROW: the story back, Overview the open tab again."""
        self.set_class(False, "-catalogue")
        self.call_after_refresh(self._fill)
        self._first_list().focus()

    def action_catalogue(self) -> None:
        """t, below NARROW: the left pane's catalogue (Research or Files) over the story, its counts whole; again, or
        escape, gives the story back."""
        if self._tab != "research":
            self._tab = "research"          # t is Research at every width (F1.1); f is Files
            self._fill_story(self.home)
        if self._wide:
            self.query_one("#tree-list", OptionList).focus()
            return
        on = True                           # t opens Research, never toggles it off: esc goes back
        self.set_class(on, "-catalogue")
        if on:
            self.call_after_refresh(self._fill)
            self.query_one("#files-list" if self._tab == "files" else "#tree-list", OptionList).focus()
        else:
            self._first_list().focus()

    # ------------------------------------------------------------ acting

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        self.call_after_refresh(self._paint_footer)
        view = next(iter(self.query("#card-view")), None)
        if view is not None and event.option_list.id == "tree-list" and self._wide and event.option_id:
            # with a card open, moving in the tree swaps the card, on the same tab where the new card has it
            data = self.card_data(self._subject(event.option_id) or "")
            if data is not None:
                name = view.data.tabs[view.tab] if view.data.tabs else ""
                view.data, view.at, view._moved = data, 0, False
                view.tab = data.tabs.index(name) if name in data.tabs else 0
                view.refresh()
            return
        if event.option_list.id == "decisions-list":
            self._mark_focus(event.option_list)
        # while the group of what waits has focus, the card beside it stays on its row, whatever a refresh highlights
        confirm = self._confirm_list()
        if self.focused is confirm and event.option_list is not confirm:
            return
        if event.option_id and self._wide and not event.option_id.startswith(("chart-head", "chart-more", "decision", "others-")):
            self._show_side(event.option_id)
            if event.option_id.startswith("p:") and self._side_for == event.option_id:
                self._in_view = event.option_id[2:]

    def on_descendant_focus(self, event: events.DescendantFocus) -> None:
        self.call_after_refresh(self._paint_footer)
        self.call_after_refresh(lambda: self._mark_focus(self.query_one("#decisions-list", OptionList)))
        self._fold_story(getattr(event.widget, "id", None))
        # the group of what waits takes focus with its highlighted row's card beside it, so c acts on what is in view
        rows = getattr(self, "_pending", [])
        w = event.widget
        if w is self._confirm_list() and rows and self._wide:
            if w.highlighted is not None:
                row = next((r for r in rows if f"p:{r.id}" == w.get_option_at_index(w.highlighted).id), None)
                if row is not None:
                    self._show_pending(row)
                    return
        if getattr(w, "id", None) in ("decisions-list", "chart-list") and self._wide and w.highlighted is not None:
            oid = w.get_option_at_index(w.highlighted).id or ""
            if oid.startswith(("o:", "p:")):
                self._show_side(oid)                # the story's row the focus lands on, its card beside it

    def on_descendant_blur(self, event: events.DescendantBlur) -> None:
        self.call_after_refresh(lambda: self._mark_focus(self.query_one("#decisions-list", OptionList)))

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if not event.option_id:
            return
        if event.option_id.startswith("q:"):
            self.make_story(event.option_id[2:])
            return
        if event.option_id == "yc-head":
            self.toggle_filter()
            return
        if event.option_id.startswith("found:"):
            self.open_found(event.option_id[6:])
            return
        if event.option_id.startswith(("decision", "chart-", "others-")):
            if event.option_id == "decisions-more":
                self._open_decisions()
            return
        if event.option_id.startswith(("f:", "o:")):
            self._came_from = event.option_list
            self.open_evidence(event.option_id)
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
        if isinstance(self.focused, ObservedLine):
            self.action_observed()
            return
        if isinstance(self.focused, CountLine):
            self.focused.action_act()
            return
        # by the screen's focus as it stands when the key lands (a list's has_focus follows it a message later)
        for lid in ("#chart-list", "#decisions-list", "#others-list", "#files-list"):
            lst = self.query_one(lid, OptionList)
            if self.focused is lst and lst.highlighted is not None:
                opt = lst.get_option_at_index(lst.highlighted)
                if opt.id:
                    self.on_option_list_option_selected(OptionList.OptionSelected(lst, opt, lst.highlighted))
                return
        tree = self.query_one("#tree-list", OptionList)
        if self.focused is tree and tree.highlighted is not None:
            self._came_from = tree
            self.open_evidence(tree.get_option_at_index(tree.highlighted).id)
            return
        if isinstance(self.focused, Cite):
            self.focused.action_open()
            return
        item = self._highlighted()
        if item:
            self.app.push_screen(card_screen(self.root, item.request_id, self.place, self.at))

    def open_found(self, request_id: str) -> None:
        """⏎ on a found file's row in Files: its small panel (M23); y records it with a note, n leaves it out."""
        item = next((a for a in self._asks if a.request_id == request_id), None)
        if item is None or not self.at_start:
            return
        argv = list(item.argv)
        path = argv[argv.index("--from") + 1] if "--from" in argv else item.title
        card = found_card(self.root, request_id)

        def go(choice: Optional[tuple]) -> None:
            if not choice:
                return
            what, note = choice
            if what == "record":
                full = list(card.argv) + (["--note", note] if note else [])
                run_settle(self.app, Path(card.get("run_in") or self.root), full, f"{request_id} recorded", self.refresh_home)
            else:
                try:
                    ask(self.root, "dismiss", key=card.dismiss_key, why="")
                except Exception as exc:  # noqa: BLE001
                    self.app.notify(str(exc)[:120], severity="warning", timeout=8)
                    return
                self._files = None
                self.refresh_home()
        self.app.push_screen(panels.FoundPanel(path, f"a run of {argv[2]}"), go)

    def action_yes(self) -> None:
        self.action_your_call()                # y is Your call at every width (X2): a found file's y is in its panel
        return
        if self.focused is not self.query_one("#calls", OptionList):
            self.action_your_call()
            return
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
        if isinstance(self.focused, CardView):
            if card_footer(self.focused, self.at_start) == "card.design":
                self.confirm_design(self.focused.data.subject)
            return
        rows = getattr(self, "_pending", [])
        if not rows or not self.at_start:
            return
        pending = self._confirm_list()
        row = self._confirm_row()
        if row is None:
            return
        if row.kind == "design":
            self.confirm_design(row.id.split("@")[0])      # its listing; nothing is confirmed without it
            return
        name = {"gate": f"{row.id.split('@')[0]}'s gate", "design": f"{row.id.split('@')[0]}'s design"}.get(row.kind, row.id)
        if self.focused is not pending or self._in_view != row.id:
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

    def confirm_design(self, experiment: str) -> bool:
        """c on a design (M21, A11): its listing full screen; the second c, once the listing's last line has been on
        screen, runs the listing's own rb confirm <exp>@<hash>. False where rb gives no listing for it, and away from where
        the screen started: the listing is the start record's, so another place or a past version confirms nothing."""
        if not self.at_start:
            return False
        try:
            listing = ask(self.root, "design_listing", experiment=experiment)
        except Exception:  # noqa: BLE001 - an older rb with no listing op: the earlier flow confirms it
            return False
        if not listing:
            return False

        def go(command: Optional[str]) -> None:
            if not command:
                return
            argv = command.split()[1:] if command.startswith("rb ") else ["confirm", command]
            run_settle(self.app, self.here, argv, f"{experiment}'s design confirmed", self.refresh_home)
        self.app.push_screen(panels.ConfirmListing(dict(listing), lambda: ask(self.root, "design_listing", experiment=experiment)), go)
        return True

    def _confirm_shown(self, row: View) -> None:
        """rb confirm <id> for one the user has in view, in the user's own environment. A design is never confirmed
        from here: c on it, wherever it is pressed (beside the list, or the full screen ⏎ opens below the wide layout),
        shows its listing, and only the listing's c, once its last line has been on screen, confirms it."""
        if row.kind == "design":
            self.confirm_design(row.id.split("@")[0])
            return
        name = {"gate": f"{row.id.split('@')[0]}'s gate", "design": f"{row.id.split('@')[0]}'s design"}.get(row.kind, row.id)
        run_settle(self.app, self.here, ["confirm", row.id], f"{name} confirmed", self.refresh_home)

    def action_open_items(self) -> None:
        """a: also open (M18), the items that need no decision, one line each; ⏎ opens the item where its action is."""
        items = (self.home.get("ui") or {}).get("also_open") or []

        def go(opens: Optional[str]) -> None:
            if not opens:
                return
            if "/" in opens and not opens.startswith(("e", "c", "q")) or opens.endswith("/") or ", " in opens:
                self.action_files()
            else:
                self.open_evidence(f"o:{opens.split(', ')[0]}")
        self.app.push_screen(panels.AlsoPanel(items), go)

    def action_observed(self) -> None:
        """⏎ on OBSERVED: what rb observed, each line with its time."""
        self.action_open_items_words()

    def action_open_items_words(self) -> None:
        st = ask(self.root, "status", shown=True, _place=self.place, _at=self.at)       # drawn as every screen draws text
        items = [i["what"] for i in st.get("open", []) if i.get("who") != "human"]     # all but the user's calls
        activity = []
        try:
            for e in ask(self.root, "journal", last=12, _place=self.place, _at=self.at):
                at, op = f"{utc_minute(str(e.get('at', '')))} UTC", e.get("op")
                if op == "file":
                    activity.append(f"{at}  {e.get('change')}  {e.get('path')}")
                elif op == "git" and e.get("said"):      # a move of HEAD alone, in rb's words, with its commit
                    activity.append(f"{at}  git  {e.get('said')}")
                elif op == "run":
                    activity.append(f"{at}  ran  {e.get('command')} (exit {e.get('exit')})")
        except Exception:  # noqa: BLE001
            pass
        activity += [f"unplaced  {line}" for line in self.home.get("unplaced") or []]
        if self.home.get("since"):
            activity.insert(0, self.home.since)         # what was written since the user last looked, first
        self.app.push_screen(OpenItemsScreen(items, activity, getattr(self, "_scope_whole", [])))

    def action_refresh(self) -> None:
        if self.at_start:
            self._scan()         # r means reconcile: look at the repository, then repaint what changed
        self.refresh_home()


class RBApp(App):
    TITLE = "rb"
    ENABLE_COMMAND_PALETTE = False     # ^p stays off (F1.3, X9): every action is one key, shown where it works
    CSS = f"""
    Screen {{ padding: 0 1; }}
    #head, #about {{ height: 1; text-wrap: nowrap; }}
    #observed {{ height: 1; padding: 0 1; text-wrap: nowrap; }}
    #observed:focus {{ background: $boost; }}
    #footer {{ height: 1; padding: 0 1; text-wrap: nowrap; }}
    #pin-also {{ height: 2; padding-top: 1; }}
    #pin {{ height: 2; padding-top: 1; }}
    #took {{ height: auto; }}
    #place {{ height: auto; text-wrap: nowrap; text-overflow: ellipsis; }}
    #place.away {{ text-wrap: wrap; text-overflow: fold; }}
    #panes {{ height: 1fr; min-height: 6; }}
    #middle {{ width: 3fr; height: 100%; }}
    HomeScreen #card-view {{ width: 5fr; height: 100%; margin-left: 1; border: round #3d4450; padding: 0 1; }}
    #tabrow {{ height: 1; display: none; }}
    HomeScreen.-narrow #tabrow {{ display: block; }}
    HomeScreen.-narrow #tree-panel {{ display: none; }}
    HomeScreen.-narrow.-catalogue #tree-panel {{ display: block; width: 100%; }}
    HomeScreen.-narrow.-catalogue #middle {{ display: none; }}
    .cite:focus, .cite:hover {{ background: $boost; }}
    .ev-title {{ margin-bottom: 1; }}
    .ev-open {{ margin-top: 1; }}
    #tabs {{ height: 1; display: none; }}
    #files-list {{ height: 1fr; border: none; background: transparent; }}
    #story {{ width: 100%; height: 1fr; padding: 0 0 0 1; overflow-x: hidden; scrollbar-size-vertical: 0; }}
    #story-head {{ height: auto; margin-top: 1; padding: 0 1; }}
    #story-rests {{ height: auto; margin-top: 1; }}
    #story-decisions-head {{ height: auto; margin-top: 1; padding: 0 1; }}
    #chart-list, #decisions-list, #others-list {{ height: auto; border: none; background: transparent; padding: 0; }}
    #decisions-list {{ margin: 1 0 1 0; }}
    HomeScreen.-narrow #story {{ padding-left: 0; }}
    HomeScreen.-narrow #chart-list, HomeScreen.-narrow #story-rests, HomeScreen.-narrow #others-list {{ margin-left: 1; }}
    #chart-list > .option-list--option-highlighted, #decisions-list > .option-list--option-highlighted, #others-list > .option-list--option-highlighted, #files-list > .option-list--option-highlighted {{ background: $boost; }}
    HomeScreen.-short #story-head, HomeScreen.-short #story-decisions-head {{ margin-top: 0; }}
    #tree-panel {{ width: 32; height: 100%; border: round #3d4450; border-title-color: {ACCENT};
                   border-subtitle-color: #8b949e; padding: 0 1; }}
    #tree-list {{ height: 1fr; border: none; background: transparent; scrollbar-size-vertical: 1;
                  scrollbar-color: #3d4450; scrollbar-background: transparent; }}
    #tree-list > .option-list--option-highlighted {{ background: $boost; }}
    #side-card {{ width: 2fr; height: 100%; margin-left: 1; border: round #3d4450; border-title-color: {ACCENT}; padding: 0 2;
                  scrollbar-size-vertical: 1; scrollbar-color: #3d4450; scrollbar-background: transparent; }}
    HomeScreen.-narrow #side-card {{ display: none; }}
    #side-card .ev-item {{ text-wrap: nowrap; text-overflow: clip; }}
    #calls-panel {{ height: auto; max-height: 8; margin-top: 1; border: round #4d4636; border-title-color: {ACCENT};
                    border-subtitle-color: {WARN}; padding: 0 1; }}
    #calls {{ height: auto; max-height: 6; border: none; background: transparent; }}
    #calls > .option-list--option-highlighted {{ background: $boost; }}
    #scope {{ height: auto; padding: 0 1; }}
    #update {{ height: auto; padding: 0 1; }}
    #scope.-short {{ text-wrap: nowrap; text-overflow: ellipsis; }}
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
    KeyScreen {{ align: center middle; }}
    #key-box {{ width: 40; height: auto; max-height: 100%; padding: 1 2; border: tall {ACCENT}; background: $surface; }}
    PlacesScreen {{ align: center middle; }}
    #place:hover {{ background: $boost; }}
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
        set_palette(self.current_theme.dark)
        try:
            self.theme_changed_signal.subscribe(self, self._themed)
        except Exception:  # noqa: BLE001 - a Textual without the signal keeps the palette it opened with
            pass
        try:
            home = HomeScreen(self.root, self.here)
        except RBError as err:      # a record rb cannot read: said as rb says it, once the terminal is the user's again
            self.failed = err
            self.exit(return_code=err.exit_code)
            return
        self.push_screen(home)

    def _themed(self, theme: Any) -> None:
        """A light theme gets the light palette (DESIGN-UX4 1.8), a dark one the dark: the home draws again in it."""
        set_palette(getattr(theme, "dark", True))
        for screen in self.screen_stack:
            if isinstance(screen, HomeScreen):
                screen._fill()

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
