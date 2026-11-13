"""rb ui's check figure and the preview of the focused row (TUI-PARITY F2.3, F3.4, M1, M4, M5): the check's number as
`◆` (a claim) or the zone that passes as `░`, 0 as a dim `┆`, rb's number as `●` with its range `├─┤` (both ends
labelled under it) or `○` with none. Every number and word is the bridge's: a chart row of the Overview (est_text,
range_text, result_text, edge_text) and rb's card model; these functions only place them.
"""
from __future__ import annotations

import re
from typing import Any, Optional

from rich.text import Text

from rabbit_brain.tui_parts import BAD, DIM, GOOD, MARK_COLOUR, WARN, chip, cut_words

STATE_MARK = {"meets": "■", "misses": "▲", "undetermined": "◧", "untested": "□"}
STATE_WORD: dict = {}      # each mark's word, from the bridge (home.ui.states, rb status's strip words): set_words fills it


def set_words(states: list) -> None:
    """The marks' words as the bridge gives them (TUI-PARITY G5); rb ui holds none of its own."""
    for s in states or []:
        if s.get("mark") and s.get("word"):
            STATE_WORD[s["mark"]] = s["word"]


def _num(v) -> Optional[float]:
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def claim_text(row: dict) -> str:
    """The check's own number as the record declares it (3.6, 49.6): a difference keeps its sign."""
    t = _num(row.get("threshold"))
    if t is None:
        return ""
    s = repr(float(t))
    s = s[:-2] if s.endswith(".0") else s
    return ("+" + s) if row.get("difference") and t > 0 else s.replace("-", "−")


def edge_label(row: dict) -> str:
    """The zone's edge as the bridge words it, its number alone ('from +5' is '+5'; '49.55 to 49.65' its first end)."""
    m = re.search(r"[+−-]?\d[\d.,]*", row.get("edge_text") or "")
    return m.group(0) if m else claim_text(row)


def domain(row: dict) -> tuple[float, float]:
    vals = [v for v in (row.get("lo"), row.get("hi"), row.get("est")) if _num(v) is not None]
    t, tol = _num(row.get("threshold")), _num(row.get("tol")) or 0.0
    if t is not None:
        vals += [t - tol, t + tol]
    if row.get("difference"):
        vals.append(0.0)
    if not vals:
        return 0.0, 1.0
    lo, hi = min(vals), max(vals)
    if hi - lo < 1e-12:
        lo, hi = lo - 1.0, hi + 1.0
    pad = (hi - lo) * 0.08
    return lo - pad, hi + pad


def _put(cells: list, styles: list, i: int, ch: str, style: str) -> None:
    if 0 <= i < len(cells):
        cells[i] = ch
        styles[i] = style


def _row(cells: list, styles: list) -> Text:
    t = Text(no_wrap=True, overflow="crop")
    for c, s in zip(cells, styles):
        t.append(c, style=s)
    return t


def _labels(width: int, marks: list[tuple[int, str]]) -> str:
    """A line of labels, each centred on its column where it fits, never over another."""
    line = [" "] * width
    taken = -1
    for col, text in sorted(marks):
        start = max(min(col - len(text) // 2, width - len(text)), 0)
        if start <= taken:
            start = taken + 2
        if start + len(text) > width:
            continue
        line[start:start + len(text)] = list(text)
        taken = start + len(text) - 1
    return "".join(line)


def figure_lines(row: dict, width: int, label_w: int = 10, said: bool = False) -> list[Text]:
    """A check's figure on one axis (M1's preview, M4's card): the axis labels with a dim `W`, the check's row (`claimed`
    with `◆`, or `passes` with `░`), rb's row (`●` and its range, or `○`), and the range's ends under it. `said`
    puts each row's number in its label column (the card has room for it)."""
    unit = row.get("measure_unit") or ""
    claim = bool(row.get("claim")) and row.get("side") == "eq"
    check_label = "claimed" if claim else "passes"
    if said and claim:
        check_label = f"claimed  {claim_text(row)}{'' if unit in ('%', '') else ' '}{unit}"
    rb_label = f"rb       {row.get('result_text') or ''}" if said else "rb"
    if said:
        label_w = max(label_w, len(check_label) + 3, len(rb_label) + 3)
    bar_w = max(width - label_w - 2, 8)
    lo, hi = domain(row)
    col = lambda x: int(round((x - lo) / (hi - lo) * (bar_w - 1)))
    zero = col(0.0) if row.get("difference") else None
    t, tol = _num(row.get("threshold")), _num(row.get("tol")) or 0.0
    # the check's row
    cells, styles = [" "] * bar_w, [""] * bar_w
    if zero is not None:
        _put(cells, styles, zero, "┆", DIM)
    axis = [] if zero is None else [(zero, "0")]
    if t is not None and claim:
        _put(cells, styles, col(t), "◆", GOOD)
        if not said:
            axis.append((col(t), claim_text(row)))
    elif t is not None:
        side = row.get("side")
        a, b = (col(t), bar_w - 1) if side == "ge" else (0, col(t)) if side == "le" else (col(t - tol), col(t + tol))
        for i in range(a, b + 1):
            if cells[i] == " ":
                _put(cells, styles, i, "░", GOOD)
        axis.append((col(t) if side in ("ge", "le") else a, edge_label(row)))
    # rb's row
    rcells, rstyles = [" "] * bar_w, [""] * bar_w
    if zero is not None:
        _put(rcells, rstyles, zero, "┆", DIM)
    est, rlo, rhi = _num(row.get("est")), _num(row.get("lo")), _num(row.get("hi"))
    state_col = MARK_COLOUR.get(STATE_MARK.get(row.get("state"), "□"), "")
    ends: list = []
    if rlo is not None and rhi is not None:
        a, b = col(rlo), col(rhi)
        b = max(b, a + 2)
        for i in range(a + 1, b):
            _put(rcells, rstyles, i, "─", "")
        _put(rcells, rstyles, a, "├", "")
        _put(rcells, rstyles, b, "┤", "")
        lo_t, _, hi_t = (row.get("range_text") or "").partition("…")
        ends = [(a, lo_t), (b, hi_t)] if lo_t and hi_t else []
    if est is not None:
        e = col(est)
        if claim and t is not None and col(t) == e:
            pass                                        # a claim and rb's number in one cell: the check's row has its ◆
        _put(rcells, rstyles, e, "●" if rlo is not None else "○", state_col)
    lw = label_w
    out: list[Text] = []
    top = Text(" " * lw + _labels(bar_w, axis), no_wrap=True, overflow="crop")
    pad = width - len(top.plain) - 1
    top.append(" " * max(pad, 0))
    top.append("W", style=DIM)
    out.append(top)
    line = Text(f"{check_label:<{lw}}", style=DIM, no_wrap=True, overflow="crop")
    line.append_text(_row(cells, styles))
    out.append(line)
    line = Text(f"{rb_label:<{lw}}", style=DIM, no_wrap=True, overflow="crop")
    line.append_text(_row(rcells, rstyles))
    out.append(line)
    if ends:
        out.append(Text(" " * lw + _labels(bar_w, ends), no_wrap=True, overflow="crop"))
    return out


def state_chips(row: dict, card: Optional[dict]) -> Text:
    """The chip row under a check's title: its mark and word, `[claim]` or its role, `▸` and its experiment (A14)."""
    mark = STATE_MARK.get(row.get("state"), "□")
    t = Text(no_wrap=True, overflow="crop")
    t.append(f"{mark} {STATE_WORD.get(mark, '')}".rstrip(), style=MARK_COLOUR.get(mark, ""))
    items = []
    for f in (card or {}).get("fields", []):
        if f.get("key") == "check":
            items = [i.get("text", "") for i in f.get("items", [])]
    t.append("  ")
    if row.get("claim"):
        chip(t, "claim")
    elif items:
        chip(t, items[0])
    exp = items[1] if len(items) > 1 else ""
    if exp:
        t.append("  ")
        t.append(f"▸ {exp}", style=DIM)
    return t


def crumb_of(card: Optional[dict]) -> str:
    """The crumb of a card (F2.1): its ancestor question and its id, `q1 › c2`; never its title."""
    card = card or {}
    cid = card.get("id", "")
    head = (card.get("fields") or [{}])[0]
    ups = [i.get("text", "") for i in head.get("items", []) if re.fullmatch(r"q[\w-]*", i.get("text", "") or "")
           and (i.get("opens") or "").startswith("rb show ")]
    return f"{ups[0]} › {cid}" if ups else cid


def card_field(card: Optional[dict], key: str) -> Optional[dict]:
    return next((f for f in (card or {}).get("fields", []) if f.get("key") == key), None)


def result_chips(row: dict, card: Optional[dict], finding: Optional[dict] = None) -> list[tuple[str, str, str]]:
    """The Result tab's chips (F2.3): (words, colour, what ⏎ opens). F's own chips where the bridge gives them; else each
    from the card model's field that says it."""
    out: list = []
    if finding and finding.get("chips"):
        for c in finding["chips"]:
            kind, words = c.get("kind", ""), c.get("text", "")
            opens = {"late": "", "problem": "Source", "unchecked": "Source", "undecided": "decide"}.get(kind, "")
            out.append((words + (" ›" if opens else ""), WARN if kind in ("late", "problem", "unchecked") else "", opens))
    else:
        w = card_field(card, "written")
        if w and (w.get("value") or "").startswith("‾"):
            words = (w["value"].split("·")[0]).lstrip("‾ ").strip()
            out.append((words, WARN, ""))
        s = card_field(card, "settings")
        unchecked = [i for i in (s or {}).get("items", []) if (i.get("text") or "").startswith(("○", "?"))]
        if unchecked:
            out.append((f"? {len(unchecked)} not checked ›", WARN, "Source"))
        d = card_field(card, "decision")
        if d and d.get("value") == "none" and row.get("state") != "untested":
            out.append((f"{d.get('said') or 'no decision'} ›", "", "decide"))
    u = card_field(card, "units")
    if u and u.get("value"):
        out.append((f"{u['value']} ›", "", "Runs"))
    return out


def chip_lines(chips: list, width: int, focus: Optional[int] = None) -> list[Text]:
    """Chips on as many lines as they need, never cut: each whole on its line."""
    lines: list[Text] = []
    cur = Text(no_wrap=True, overflow="crop")
    for i, (words, style, _opens) in enumerate(chips):
        piece = Text(no_wrap=True)
        chip(piece, words, style=("reverse " + style).strip() if i == focus else style)
        if cur.plain and len(cur.plain) + 2 + len(piece.plain) > width:
            lines.append(cur)
            cur = Text(no_wrap=True, overflow="crop")
        if cur.plain:
            cur.append("  ")
        cur.append_text(piece)
    if cur.plain:
        lines.append(cur)
    return lines


def wrap_words(text: str, width: int) -> list[str]:
    lines, cur = [], ""
    for w in text.split():
        if cur and len(cur) + 1 + len(w) > width:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}" if cur else w
    if cur:
        lines.append(cur)
    return lines or [""]


def preview_lines(row: dict, card: Optional[dict], width: int, finding: Optional[dict] = None) -> list[Text]:
    """The preview of a focused check (M1's right pane): its title whole, its chip row, the figure, its chips. No
    sentence; the figure says the reading."""
    out: list[Text] = [Text(line) for line in wrap_words(row.get("label") or (card or {}).get("title", ""), width)]
    out.append(Text(""))
    out.append(state_chips(row, card))
    out.append(Text(""))
    out.extend(figure_lines(row, width))
    out.append(Text(""))
    out.extend(chip_lines(result_chips(row, card, finding), width))
    return out


__all__ = ["figure_lines", "preview_lines", "result_chips", "chip_lines", "state_chips", "wrap_words", "claim_text",
           "STATE_MARK", "STATE_WORD", "BAD", "cut_words", "set_words"]


# ---------------------------------------------------------------- the open card (F2, M4 to M6)

from dataclasses import dataclass, field as _field  # noqa: E402

from textual import events  # noqa: E402
from textual.binding import Binding  # noqa: E402
from textual.message import Message  # noqa: E402
from textual.widgets import Static  # noqa: E402


@dataclass
class CardData:
    """What an open check card draws, every part from the bridge: its card model, its chart row, its finding's chips,
    its runs as table rows, the question it is used by."""
    subject: str
    card: dict
    row: dict
    crumb: str
    finding: Optional[dict] = None
    runs: list = _field(default_factory=list)        # dicts: run, variant, values {setting: value}, number
    run_cols: list = _field(default_factory=list)    # the per-run settings shown (seed), in order
    metric: str = ""
    question: tuple = ("", "")                       # (id, its words)
    working: list = _field(default_factory=list)     # (mark, line): rb's working, folded under Source
    tabs: tuple = ("Result", "Rule", "Runs", "Used by", "Source")
    # a question's, an experiment's or a resource's card (M8, M20, M11): the same frame, its parts given whole
    kind: str = "check"
    chips: Optional[Text] = None                     # the chip row under the title
    counts: dict = _field(default_factory=dict)      # a tab's count, said after its name (dim at 0)
    bodies: Any = _field(default_factory=dict)       # a tab's lines: (Text, action or None); or a function of the width
    prompt: Optional[str] = None                     # the Next step box under the first tab, what p copies
    get_it: Optional[str] = None                     # a resource's Get it address, what g opens


def run_rows(run_cards: list) -> tuple[list, list, str]:
    """The Runs tab's table (F2.5) from each run's card: its variant and number from `numbers`, its per-run values."""
    rows, cols, metric = [], [], ""
    for rc in run_cards:
        rid = rc.get("id", "")
        per = {}
        for f in rc.get("fields", []):
            if f.get("key") == "per_run":
                for i in f.get("items", []):
                    k, _, v = (i.get("text") or "").partition(" = ")
                    per[k] = v
        nums = [i.get("text", "") for f in rc.get("fields", []) if f.get("key") == "numbers" for i in f.get("items", [])]
        variant, number = "", ""
        if nums:
            name, _, val = nums[0].partition(" = ")
            variant, _, metric = name.rpartition(".") if "." in name else ("", "", name)
            number = val.split(" ")[0]
        rows.append({"run": rid, "variant": variant, "values": per, "number": number})
    seen = {}
    for r in rows:
        for k, v in r["values"].items():
            seen.setdefault(k, set()).add(v)
    cols = [k for k, vs in seen.items() if len(vs) > 1]
    return rows, cols, metric


class CardView(Static, can_focus=True):
    """An open check card (M4, M5): row 0 the crumb with `⧉` at the right, the title, its state chips, the tab row with
    the open tab underlined, then one panel. `←` `→` change the tab (the row wraps), `↑` `↓` `j` `k` move along the
    panel's items, `⏎` acts on the focused one, `esc` goes back."""

    BINDINGS = [Binding("left", "tab(-1)", show=False), Binding("right", "tab(1)", show=False),
                Binding("up", "move(-1)", show=False), Binding("down", "move(1)", show=False),
                Binding("k", "move(-1)", show=False), Binding("j", "move(1)", show=False),
                Binding("home", "move(-999)", show=False), Binding("end", "move(999)", show=False),
                Binding("enter", "act", show=False)]

    class Act(Message):
        def __init__(self, view: "CardView", what: tuple) -> None:
            super().__init__()
            self.view, self.what = view, what

    def __init__(self, data: CardData, past: bool = False, **kw) -> None:
        super().__init__(**kw)
        self.data = data
        self.past = past                  # shown at a past version: nothing on it to copy (no ⧉, p copies nothing)
        self.tab = 0
        self.at = 0                       # the focused item of the panel (0: the crumb's question)
        self.unfolded = False
        self.flash: Optional[str] = None
        self._items: list = []

    # what each tab holds: (lines, items), an item (line index, action)
    def _lines(self, width: int) -> tuple[list, list]:
        d, w = self.data, max(width, 30)
        lines: list[Text] = []
        items: list = []
        top = Text(no_wrap=True, overflow="crop")
        q, _, cid = d.crumb.rpartition(" › ")
        if q:
            top.append(q, style="underline")
            top.append(f" › {cid}")
        else:
            top.append(cid)
        mark = f"[{self.flash}]" if self.flash and not d.prompt else ("" if self.past else "⧉")
        if mark:
            top.append(" " * max(w - len(top.plain) - len(mark), 1))
            top.append(mark, style=GOOD if mark != "⧉" else DIM)
        lines.append(top)
        items.append((0, ("open", q) if q else (("none",) if self.past else ("copy", f"rb show {d.subject}"))))
        for t in wrap_words(d.card.get("title", ""), w - 2):
            lines.append(Text.assemble(("┃ ", "bold"), (t, "bold")))
        lines.append(Text(""))
        if d.kind != "check":
            self._kind_lines(w, lines, items)
            return lines, items
        lines.append(state_chips(d.row, d.card))
        lines.append(Text(""))
        counts = {"Runs": len(d.runs)}
        tabs = Text(no_wrap=True, overflow="crop")
        under = Text(no_wrap=True)
        for n, name in enumerate(d.tabs):
            label = f"{name} {counts[name]}" if name in counts else name
            if n:
                tabs.append("  ")
                under.append("  ")
            empty = name in counts and not counts[name]
            tabs.append(label, style="bold" if n == self.tab else (DIM if empty else ""))
            under.append(("━" if n == self.tab else " ") * len(label))
        lines.append(tabs)
        lines.append(under)
        lines.append(Text(""))
        getattr(self, f"_tab_{d.tabs[self.tab].split()[0].lower()}")(w, lines, items)
        return lines, items

    def _kind_lines(self, w: int, lines: list, items: list) -> None:
        """A question's, an experiment's or a resource's card under its title: its chips, its tab row (where it has
        tabs), the open tab's lines, and under the first tab its Next step box."""
        d = self.data
        if d.chips is not None:
            lines.append(d.chips)
            lines.append(Text(""))
        if d.tabs:
            tabs = Text(no_wrap=True, overflow="crop")
            under = Text(no_wrap=True)
            for n, name in enumerate(d.tabs):
                label = f"{name} {d.counts[name]}" if name in d.counts else name
                if n:
                    tabs.append("  ")
                    under.append("  ")
                empty = name in d.counts and not d.counts[name]
                tabs.append(label, style="bold" if n == self.tab else (DIM if empty else ""))
                under.append(("━" if n == self.tab else " ") * len(label))
            lines.append(tabs)
            lines.append(under)
            lines.append(Text(""))
        name = d.tabs[self.tab] if d.tabs else ""
        bodies = d.bodies(w - 1) if callable(d.bodies) else d.bodies
        for text, act in bodies.get(name, []):
            if isinstance(text, tuple):         # drawn at the card's own width: ("outline", name, rows)
                lines.extend(outline_lines(text[1], w, text[2]))
                continue
            if act is not None:
                items.append((len(lines), act[:2]))
            lines.append(Text.assemble(" ", text) if text.plain else text)
            # a row's hover words (N2), one dim line under it while it has focus
            if act is not None and len(act) > 2 and act[2] and len(items) - 1 == self.at:
                lines.append(Text("   " + act[2], style=DIM))
        if self.tab == 0 and d.prompt:
            lines.append(Text(""))
            lines.extend(box_lines("Next step", d.prompt, w, f"[{self.flash}]" if self.flash else "⧉"))

    def _tab_result(self, w: int, lines: list, items: list) -> None:
        d = self.data
        lines.extend(figure_lines(d.row, w, said=w >= 60))
        lines.append(Text(""))
        chips = result_chips(d.row, d.card, d.finding)
        start = len(lines)
        focus = self.at - len(items) if self.at >= len(items) else None
        cl = chip_lines(chips, w, None)
        # each chip an item, on the line it sits on
        n = 0
        for i, line in enumerate(cl):
            for _ in range(line.plain.count("[")):
                if n < len(chips):
                    what = chips[n][2]
                    items.append((start + i, ("tab", what) if what in self.data.tabs else (what,) if what else ("none",)))
                    n += 1
        lines.extend(cl)
        del focus

    def _tab_rule(self, w: int, lines: list, items: list) -> None:
        d = self.data
        passes = card_field(d.card, "passes") or {}
        said = passes.get("value") or ""
        lines.append(Text(said.lstrip("░ ").strip()))
        lines.append(Text(""))
        band_w = 11
        t, tol = _num(d.row.get("threshold")), _num(d.row.get("tol")) or 0.0
        if t is not None and d.row.get("side") == "eq":
            lines.append(Text(" " * (8 + band_w // 2 - len(claim_text(d.row)) // 2) + claim_text(d.row)))
            band = Text("passes  ", style=DIM)
            band.append("░" * (band_w // 2), style=GOOD)
            band.append("◆", style=GOOD)
            band.append("░" * (band_w // 2), style=GOOD)
            lines.append(band)
            ends = re.findall(r"[+−-]?\d[\d.,]*", d.row.get("edge_text") or said)
            if len(ends) >= 2:
                lines.append(Text(" " * 8 + f"{ends[0]} {ends[1]}"))
        elif t is not None:
            band = Text("passes  ", style=DIM)
            band.append("░" * band_w, style=GOOD)
            band.append(f"  {d.row.get('edge_text') or ''}")
            lines.append(band)
        written = card_field(d.card, "written") or {}
        _, _, when = (written.get("value") or "").partition("·")
        if when.strip():
            lines.append(Text(""))
            lines.append(Text(when.strip().replace("──▶", "──→")))

    def _tab_runs(self, w: int, lines: list, items: list) -> None:
        d = self.data
        if not d.runs:
            lines.append(Text("╌╌ runs " + "╌" * max(w - 9, 0), style=DIM))
            return
        vw = max([len("prompt")] + [len(r["variant"]) for r in d.runs]) + 2
        head = Text(f"{'prompt':<{vw}}", style=DIM)
        for c in d.run_cols:
            head.append(f"{c:>6}   ", style=DIM)
        head.append(f"{d.metric or 'number':>10}   run", style=DIM)
        lines.append(head)
        for r in d.runs:
            t = Text(f"{r['variant']:<{vw}}", no_wrap=True, overflow="crop")
            for c in d.run_cols:
                t.append(f"{r['values'].get(c, ''):>6}   ")
            t.append(f"{r['number']:>{max(len(d.metric), 6) + 4}}   ")
            t.append(r["run"], style=DIM)
            items.append((len(lines), ("open", r["run"])))
            lines.append(t)

    def _tab_used(self, w: int, lines: list, items: list) -> None:
        qid, words = self.data.question
        if not qid:
            return
        wrapped = wrap_words(words, max(w - len(qid) - 8, 20))
        first = Text.assemble(("┃ ", "bold"), (qid, "bold"), "  ", wrapped[0], ("  ›", DIM))
        items.append((len(lines), ("open", qid)))
        lines.append(first)
        for more in wrapped[1:]:
            lines.append(Text(" " * (len(qid) + 4) + more))

    def _tab_source(self, w: int, lines: list, items: list) -> None:
        d = self.data
        cited = card_field(d.card, "cited")
        code = card_field(d.card, "code")
        for i in (cited or {}).get("items", []):
            t = Text(no_wrap=True)
            missing = i.get("missing")
            chip(t, ("∅ " if missing else "") + (i.get("text") or ""), style=WARN if missing else "")
            items.append((len(lines), ("open", i.get("text"))))
            lines.append(t)
        for i in (code or {}).get("items", []):
            t = Text(i.get("text", ""))
            if not self.past:
                t.append("  ⧉", style=DIM)
                items.append((len(lines), ("copy", i.get("text", ""))))
            lines.append(t)
        lines.append(Text(""))
        items.append((len(lines), ("fold",)))
        lines.append(Text(("▼ " if self.unfolded else "▶ ") + "How rb worked it out"))
        if self.unfolded:
            for mark, line in d.working:
                for n, part in enumerate(wrap_words(line, w - 4)):
                    lines.append(Text.assemble(("  " + (mark if not n else " ") + " ", DIM), part))

    def render(self) -> Text:
        lines, items = self._lines(self.size.width or 80)
        self._items = items
        if self.data.kind != "check" and self.at == 0 and len(items) > 1 and not getattr(self, "_moved", False):
            self.at = 1                         # a question's, experiment's or resource's card: focus on its first row
        self.at = min(self.at, max(len(items) - 1, 0))
        if items:
            li = items[self.at][0]
            if li > 0 and self.data.kind not in ("check",):
                lines[li] = Text.assemble(("▌", "bold"), lines[li][1:])
            elif li > 0:
                lines[li] = Text.assemble(("▌", "bold"), lines[li])
            else:
                lines[0].stylize("bold", 0, len(self.data.crumb.rpartition(" › ")[0]))
        # taller than the screen: a window that keeps the focused line in view (the crumb's row stays)
        h = self.size.height or len(lines)
        if len(lines) > h and h > 2:
            li = items[self.at][0] if items else 0
            off = getattr(self, "_off", 0)
            if li - off >= h - 1:
                off = li - h + 2 + (1 if self.at == len(items) - 1 else 0)
            if li - 1 < off:
                off = max(li - 1, 0)
            off = max(0, min(off, len(lines) - h + 1))
            self._off = off
            lines = lines[:1] + lines[1 + off:off + h]
        out = Text(no_wrap=True, overflow="crop")
        for n, line in enumerate(lines):
            if n:
                out.append("\n")
            out.append_text(line)
        return out

    def action_tab(self, step: int) -> None:
        self._moved = False
        if not self.data.tabs:
            return
        self.tab = (self.tab + step) % len(self.data.tabs)
        self.at = 0
        self.refresh()

    def action_move(self, step: int) -> None:
        self._moved = True
        if self._items:
            self.at = max(0, min(self.at + step, len(self._items) - 1))
            self.refresh()

    def action_act(self) -> None:
        if not self._items:
            return
        what = self._items[self.at][1]
        if what[0] == "tab":
            self.tab = self.data.tabs.index(what[1])
            self.at = 0
            if what[1] == "Source":
                self.unfolded = True
            self.refresh()
        elif what[0] == "tabname":
            if what[1] in self.data.tabs:
                self.tab, self.at = self.data.tabs.index(what[1]), 0
                self.refresh()
        elif what[0] == "fold":
            self.unfolded = not self.unfolded
            self.refresh()
        elif what[0] != "none":
            self.post_message(CardView.Act(self, what))

    def show_flash(self, said: str) -> None:
        self.flash = said
        self.refresh()
        self.set_timer(3.0, self._unflash)

    def _unflash(self) -> None:
        self.flash = None
        self.refresh()

    def on_click(self, event: events.Click) -> None:
        self.focus()


# ---------------------------------------------------------------- previews of a question and a resource (M7, M9)


def box_lines(title: str, text: str, width: int, corner: str = "⧉", style: str = "") -> list[Text]:
    """A rounded box with its title in the top border and `⧉` (what `p` copies) in the bottom one (M7, M8, M11)."""
    inner = max(width - 4, 10)
    top = Text(f"╭─ {title} " + "─" * max(width - len(title) - 5, 0) + "╮", style=style)
    out = [top]
    for line in wrap_words(text, inner):
        out.append(Text.assemble(("│ ", style), f"{line:<{inner}}", (" │", style)))
    out.append(Text("╰" + "─" * max(width - len(corner) - 5, 0) + f" {corner} ─╯", style=style))
    return out


def outline_lines(name: str, width: int, rows: int = 3) -> list[Text]:
    """Where a missing thing would sit, a dim dashed outline (F5.1): three rows, or one where room is short."""
    if rows == 1:
        return [Text(f"╌╌ {name} " + "╌" * max(width - len(name) - 4, 0), style=DIM)]
    out = [Text(f"┌╌ {name} " + "╌" * max(width - len(name) - 5, 0) + "┐", style=DIM)]
    out += [Text("╎" + " " * max(width - 2, 0) + "╎", style=DIM) for _ in range(rows - 2)]
    out.append(Text("└" + "╌" * max(width - 2, 0) + "┘", style=DIM))
    return out


def chip_text(words: str, style: str = DIM) -> Text:
    t = Text()
    chip(t, words, style=style)
    return t


def question_preview(words: str, marks: Optional[Text], empty_chip: str, next_step: Optional[str], cont: Optional[str],
                     width: int, flash: Optional[str] = None) -> list[Text]:
    """A focused question's preview (M7, F3.4): its words whole; its marks, or its empty chip with the dashed outlines
    of what is missing and the Next step box; the story's own question shows its Continue this research box instead."""
    out = [Text.assemble(("┃ ", "bold"), (line, "bold")) for line in wrap_words(words, width - 2)]
    out.append(Text(""))
    if cont:
        out.extend(box_lines("Continue this research", cont, width, flash and f"[{flash}]" or "⧉"))
        return out
    if marks is not None and marks.plain:
        out.append(marks)
        return out
    out.append(chip_text(empty_chip))
    out.append(Text(""))
    out.extend(outline_lines("checks", width))
    out.extend(outline_lines("experiment", width))
    if next_step:
        out.append(Text(""))
        out.extend(box_lines("Next step", next_step, width, flash and f"[{flash}]" or "⧉"))
    return out


def resource_preview(facts: dict, width: int, flash: Optional[str] = None, next_step: Optional[str] = None) -> list[Text]:
    """A focused resource's preview (M9, Q3): its name, its kind, its Get it chip with the address under it (or no
    download link, the owner-only missing state and the Next step), and its runs and checks as counts that open."""
    out = [Text(line) for line in wrap_words(facts.get("name") or facts.get("id", ""), width)]
    out.append(Text(""))
    if facts.get("type"):
        out.append(chip_text(facts["type"]))
    got = facts.get("get_it")
    if got:
        out.append(chip_text(flash or f"↗ Get it · {got.get('label')}", style=GOOD if flash else ""))
        address = (got.get("address") or "").split("://", 1)[-1]
        out.extend(Text(line, style=DIM) for line in [address[i:i + width] for i in range(0, len(address), width)])
    else:
        out.append(chip_text("no download link", style=WARN))
        if facts.get("missing"):
            out.append(Text(facts["missing"], style=DIM))
    out.append(Text(""))
    out.append(Text.assemble(f"{facts.get('runs', 0)} runs", (" ›", DIM), "   ", f"{facts.get('checks', 0)} checks", (" ›", DIM)))
    if not got and next_step:
        out.append(Text(""))
        out.extend(box_lines("Next step", next_step, width, flash and f"[{flash}]" or "⧉"))
    return out
