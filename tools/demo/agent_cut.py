#!/usr/bin/env python3
"""The demo's agent and fresh segments (follow-up 14), cut from one agent_beat.mjs take: rb open's page fills the
frame, and the agent's terminal floats in front of it, picture in picture.

    python tools/demo/agent_cut.py <take dir> [--out <dir>] [--moments 3] [--chrome <path>]

- agent (about 15 s): the line the agent is told, then the moments the page changed by itself (rb open's live
  module swapping a new version in; walk.mjs's "change" markers), each a few seconds at its own speed, cut together
  with a clock the viewer reads ("+12 min": the time since the agent was told) on each moment after the first, beside
  the window where the page is blank all through the moment (no line of text the walk kept), else in the window's bar. The
  agent's text is never sped into a blur: each moment plays at 1x.
- fresh (about 6 s): the fresh session asked where the research stands, then its answer as it ends ("+0:40").
- The terminal never covers what changes: for each moment the window goes to the first place (bottom right, then
  pulled in from the right edge, bottom left, top right, pulled in, top left) clear of every box the moment's changes
  draw on screen, after a jump all the page changed since the moment before; a moment no place clears is refused. segment.json keeps each moment's window, the boxes and their words, so a test reads every one.
- Every id in the terminal shaped like a UUID is masked (term.html's mask=ids) and the window's bar says so; the row
  that names the agent's permission mode (Claude Code's footer) is blanked (mask=footer).
Writes <out>/agent/ and <out>/fresh/, each as cut.py keeps a captured segment: screen.mp4 (1920 x 1200, 30 fps),
screen.marks.jsonl and segment.json, and <out>/sheet/ with one frame per moment.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
FRAME = (1920, 1200)
PIP_SHARE = 0.36          # the terminal window's width, of the frame's
MARGIN = 40               # frame px from the frame's edge
PAD = 28                  # frame px kept clear around a change's box
SHADOW = 24               # how far the window's shadow reaches, inside PAD
NEAR_PAD = SHADOW         # and around a changed table's header row (what the viewer reads the changed cell by): the shadow
                          # stops short of it
# where the window may go, in the order it is tried: the corners, and the right ones pulled in from the edge (rb's
# counts sit at the working view's right edge)
PLACES = ("bottom right", "bottom right, inset", "bottom left", "top right", "top right, inset", "top left")
INSET = 120
# where no place is clear at full size, the window a step smaller in each place before none: 34% and 32% of the frame
# keep the terminal's text readable (a page whose grid header row sits just under a top window)
SHRINK = (1.0, 0.95, 0.9)
TERM_FONT = 13
BADGE_H = 46
BADGE_W = 200             # the widest clock badge ("+120 min" is 175 px)
TEXT_PAD = 6              # frame px kept clear between a clock badge beside the window and any text on the page
BLANK_SPREAD = 28         # a page frame is blank under a box where no colour channel there spans more than this (JPEG noise
                          # spans under 10 on a panel; rb's faintest glyph, its #d1d9e0 crumb slash, spans about 41)
CLOCK_WORDS = re.compile(r"^(·|just now|(changed )?(an?|\d+) (second|minute|hour|day|week)s? ago)$")


def load(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()] if path.exists() else []


def place(name: str, size: tuple[int, int]) -> list[int]:
    w, h = size
    x = FRAME[0] - (INSET if "inset" in name else MARGIN) - w if "right" in name else MARGIN
    y = FRAME[1] - MARGIN - h if "bottom" in name else MARGIN + 64
    return [x, y, w, h]


def overlaps(a, b, pad: float = 0) -> bool:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return ax - pad < bx + bw and bx < ax + aw + pad and ay - pad < by + bh and by < ay + ah + pad


def on_screen(box, size=FRAME) -> list[float] | None:
    """The part of a box the frame shows, or None."""
    x, y, w, h = box
    l, t, r, b = max(x, 0), max(y, 0), min(x + w, size[0]), min(y + h, size[1])
    return [l, t, r - l, b - t] if r > l and b > t else None


def window_for(boxes: list, size: tuple[int, int], badge: bool = True, prefer: str | None = None,
               near: list = ()) -> tuple[str, list[int]]:
    """The first place for the window (its clock badge too) clear of every box on screen, and of every changed table's
    header row (`near`), the place it was in the moment before first (a window that stays put reads better than one
    that hops); where none is clear at full size, each place again with the window a step smaller (SHRINK); SystemExit
    when none is clear at all."""
    shown = [b for b in (on_screen(b) for b in boxes) if b]
    heads = [b for b in (on_screen(b) for b in near) if b]
    for k in SHRINK:
        sized = (round(size[0] * k), round(size[1] * k))
        for name in ([prefer] if prefer in PLACES else []) + list(PLACES):
            win = place(name, sized)
            whole = with_badge(win) if badge else win
            if not any(overlaps(whole, b, PAD) for b in shown) and not any(overlaps(whole, b, NEAR_PAD) for b in heads):
                return name, win
    raise SystemExit(f"no place for the terminal is clear of what changes: {shown}, header rows {heads}")


def badge_at(win: list[int], bw: int = BADGE_W) -> tuple[int, int]:
    """Where a clock badge `bw` px wide goes: beside the window, level with its top (left of it, right of one on the
    left), so the window and its clock take no more height than the window (a grid's rows above a bottom window, its
    header row under a top one, stay in sight)."""
    x, y, w, h = win
    return (x + w + 10, y) if x + w / 2 < FRAME[0] / 2 else (x - 10 - bw, y)


def badge_spots(win: list[int], bw: int) -> list[list[int]]:
    """Where a clock badge `bw` px wide may go beside the window, in the order tried: level with its top (badge_at), level
    with its bottom, then above and under it at its outer edge; each inside the frame."""
    x, y, w, h = win
    bx, by = badge_at(win, bw)
    edge = x + w - bw if x + w / 2 >= FRAME[0] / 2 else x
    spots = [[bx, by, bw, BADGE_H], [bx, y + h - BADGE_H, bw, BADGE_H], [edge, y - 10 - BADGE_H, bw, BADGE_H], [edge, y + h + 10, bw, BADGE_H]]
    return [b for b in spots if 8 <= b[0] and b[0] + bw <= FRAME[0] - 8 and 8 <= b[1] and b[1] + BADGE_H <= FRAME[1] - 8]


def badge_for(m: dict, bw: int, blank=None) -> list[int] | str:
    """A clock moment's badge: the first spot beside the window (badge_spots) where the page is blank, clear of every
    line of text the page shows in the moment (`text`), of what changes and of its header rows, and, with `blank` (a
    box's test on the page's own frames), blank in every frame the moment shows; else "bar", the clock in the window's
    own bar. A moment with no text kept (a take filmed before the walk kept it) has it in the bar."""
    if m.get("text") is None:
        return "bar"
    for b in badge_spots(m["window"], bw):
        if not (any(overlaps(b, t, TEXT_PAD) for t in m["text"]) or any(overlaps(b, c, PAD) for c in m["boxes"])
                or any(overlaps(b, n, NEAR_PAD) for n in m.get("near", []))) and (blank is None or blank(b)):
            return b
    return "bar"


def page_blank(walk: Path, a: float, b: float, box, frames: list | None = None, cache: dict | None = None) -> bool:
    """Whether the page is one colour (give or take JPEG's noise, BLANK_SPREAD) under `box` and TEXT_PAD around it, in
    frame px, in every page frame shown from walk time `a` to `b`: the frame in place at `a`, and each after it until
    `b`. Text the walk never read (a clock line rb open redraws with no swap, a field's placeholder) shows here."""
    from PIL import Image
    frames = load(walk / "frames.jsonl") if frames is None else frames
    shown = [f for f in frames if f["t"] <= a][-1:] + [f for f in frames if a < f["t"] <= b]
    if not shown:
        return False
    cache = {} if cache is None else cache
    for f in shown:
        if f["file"] not in cache:
            cache[f["file"]] = Image.open(walk / f["file"]).convert("RGB")
        im = cache[f["file"]]
        k = im.size[0] / FRAME[0]
        x, y, w, h = box
        crop = im.crop((max(0, round((x - TEXT_PAD) * k)), max(0, round((y - TEXT_PAD) * k)),
                        min(im.size[0], round((x + w + TEXT_PAD) * k)), min(im.size[1], round((y + h + TEXT_PAD) * k))))
        if any(hi - lo > BLANK_SPREAD for lo, hi in crop.getextrema()):
            return False
    return True


def covers(m: dict) -> list[list[float]]:
    """What a moment draws over the page: its window, and its clock badge beside it (a segment that keeps no `badge`
    has it level with the window's top, at its widest)."""
    if not m.get("clock") or m.get("badge") == "bar":
        return [m["window"]]
    if isinstance(m.get("badge"), list):
        return [m["window"], m["badge"]]
    return [with_badge(m["window"])]


def texts_for(marks: list[dict], page0: float, k: float, a: float, b: float) -> list[list[float]] | None:
    """Every line of text the page shows from wall time `a` to `b`, in frame px: the walk's text kept last at or before
    `a`, and each kept after it until `b` (a "text" marker where a watch starts, and each change's); None where the walk
    kept none by then."""
    kept = sorted(((page0 + x["t"], x["text"]) for x in marks if x.get("text") is not None), key=lambda p: p[0])
    before = [p for p in kept if p[0] <= a]
    use = before[-1:] + [p for p in kept if a < p[0] <= b]
    return [scale_box(t, k) for _, ts in use for t in ts] if before else None


def with_badge(win: list[int]) -> list[int]:
    """The window and its clock badge (badge_at, at its widest), as one box."""
    x, y, w, h = win
    bx, by = badge_at(win)
    l, t = min(x, bx), min(y, by)
    return [l, t, max(x + w, bx + BADGE_W) - l, max(y + h, by + BADGE_H) - t]


def scale_box(box, k: float) -> list[float]:
    return [round(v * k, 1) for v in box]


def clock(seconds: float, minutes: bool) -> str:
    """The time since the line was said: "+12 min", or "+0:40" for under a minute and a half (or with no minutes)."""
    if minutes and seconds >= 90:
        return f"+{round(seconds / 60)} min"
    s = max(1, round(seconds))
    return f"+{s // 60}:{s % 60:02d}"


def check_marks(segment: dict, changes: list[dict], page0: float, k: float) -> list[str]:
    """The take's own change markers read again: each one a moment shows, clear of that moment's window."""
    bad, ms = [], segment["moments"]
    for i, m in enumerate(ms):
        for c in changes:
            if since(ms, i) < page0 + c["t"] <= m["wall"][1] or page0 + c["t"] == m["wall"][0]:
                for b in c["boxes"]:
                    s = on_screen(scale_box(b, k))
                    if s and any(overlaps(whole, s, PAD) for whole in covers(m)):
                        bad.append(f"the change at {c['t']:.1f} s ({', '.join(c.get('words', [])[:3])}) is under the terminal at {m['window']}")
                for b in c.get("near", []):
                    s = on_screen(scale_box(b, k))
                    if s and any(overlaps(whole, s, NEAR_PAD) for whole in covers(m)):
                        bad.append(f"the header row over the change at {c['t']:.1f} s is under the terminal at {m['window']}")
    return bad


def check(segment: dict) -> list[str]:
    """Every moment's window (and badge) clear of every change box it shows, and a clock badge beside the window clear
    of every line of text on the page: the problems, none when it holds."""
    bad = []
    for m in segment["moments"]:
        for whole in covers(m):
            for b in m["boxes"]:
                s = on_screen(b)
                if s and overlaps(whole, s, PAD):
                    bad.append(f"moment at {m['cut'][0]} s: the terminal at {m['window']} covers {s}")
            for b in m.get("near", []):
                s = on_screen(b)
                if s and overlaps(whole, s, NEAR_PAD):
                    bad.append(f"moment at {m['cut'][0]} s: the terminal at {m['window']} covers the header row {s}")
        if m.get("clock") and m.get("badge") != "bar":
            badge = m["badge"] if isinstance(m.get("badge"), list) else None
            if badge is None or m.get("text") is None:
                bad.append(f"moment at {m['cut'][0]} s: the clock {m['clock']} sits beside the window with no page text kept to check it by")
                continue
            x, y, w, h = badge
            if x < 0 or y < 0 or x + w > FRAME[0] or y + h > FRAME[1] or overlaps(badge, m["window"]):
                bad.append(f"moment at {m['cut'][0]} s: the clock {m['clock']} at {badge} is off the frame or over the window")
            for t in m["text"]:
                if overlaps(badge, t, TEXT_PAD):
                    bad.append(f"moment at {m['cut'][0]} s: the clock {m['clock']} at {badge} meets the page's text at {t}")
                    break
    return bad


def check_page(segment: dict, walk: Path, page0: float, k: float) -> list[str]:
    """Each clock beside the window read again against the take itself: clear of every line of text the walk kept in
    its moment (texts_for, from the walk's markers), and over blank page in every frame the moment shows (page_blank)."""
    bad, marks, frames = [], load(walk / "marks.jsonl"), load(walk / "frames.jsonl")
    for m in segment["moments"]:
        if not m.get("clock") or not isinstance(m.get("badge"), list):
            continue
        a, b = m["wall"]
        text = texts_for(marks, page0, k, a, b)
        if text is None or any(overlaps(m["badge"], t, TEXT_PAD) for t in text):
            bad.append(f"moment at {m['cut'][0]} s: the clock {m['clock']} at {m['badge']} meets text the walk kept, or the walk kept none")
        if not page_blank(walk, a - page0, b - page0, m["badge"], frames):
            bad.append(f"moment at {m['cut'][0]} s: the page under the clock {m['clock']} at {m['badge']} is not blank in every frame")
    return bad


def since(moments: list[dict], i: int) -> float:
    """From when a moment shows the page's changes: its own start, or, after a jump, the end of the moment before it
    (the jump shows at once all the page changed in between)."""
    return moments[i]["wall"][0] if i == 0 or not moments[i]["clock"] else min(moments[i - 1]["wall"][1], moments[i]["wall"][0])


def settle(moments: list[dict], changes: list[dict], page0: float, k: float, size, first: str | None = None) -> list[dict]:
    """Each moment's boxes and words: every change the page made that the moment shows, and its window clear of them
    (the first moment's in `first` where that is clear, each later one where the one before it was)."""
    for i, m in enumerate(moments):
        inside = [c for c in changes if since(moments, i) < page0 + c["t"] <= m["wall"][1] or page0 + c["t"] == m["wall"][0]]
        m["boxes"] = [scale_box(b, k) for c in inside for b in c["boxes"]]
        m["words"] = [w for c in inside for w in c.get("words", [])]
        m["gone"] = [w for c in inside for w in c.get("gone", [])]
        m["near"] = [scale_box(b, k) for c in inside for b in c.get("near", [])]
        m["place"], m["window"] = window_for(m["boxes"], size, badge=bool(m["clock"]), prefer=moments[i - 1]["place"] if i else first,
                                             near=m["near"])
    return moments


def ends(cast_marks: dict, events: list | None = None) -> float:
    """When the session ended, in the cast's seconds: its `done` marker, else its last output (a session cut short)."""
    if "done" in cast_marks["marks"]:
        return cast_marks["marks"]["done"]
    return max((t for t, kind, _ in events or [] if kind == "o"), default=cast_marks["marks"]["said"] + 5)


def plan_agent(take: dict, changes: list[dict], cast_marks: dict, count: int = 3, size=(691, 520),
               lead=0.6, opening=4.0, before=1.0, after=2.5, group=20.0, events: list | None = None) -> list[dict]:
    """The agent segment's moments, on the wall clock: the line said, then up to `count` page changes, none past the
    session's end (after it, the cast holds only the driver's /exit)."""
    page0 = take["page"]["t0_wall"]
    k = FRAME[0] / take["page"]["css"][0]
    said = cast_marks["wall0"] + cast_marks["marks"]["said"]
    done = cast_marks["wall0"] + ends(cast_marks, events)
    seen = sorted((dict(c, wall=page0 + c["t"]) for c in changes
                   if said + opening + before < page0 + c["t"] <= done and (c.get("words") or c.get("gone"))), key=lambda c: c["wall"])
    # changes close together are one moment, at the last of them (the page as it settles)
    groups: list[list[dict]] = []
    for c in seen:
        if groups and c["wall"] - groups[-1][-1]["wall"] <= group:
            groups[-1].append(c)
        else:
            groups.append([c])
    # the moments that change the most words, kept in time order; words that are only the page's own clock ("16 minutes
    # ago", "just now") or a separator count for nothing, and a change of nothing else is no moment
    weight = lambda g: sum(not CLOCK_WORDS.match(w) for c in g for w in c.get("words", []) + c.get("gone", []))
    chosen = sorted(sorted((g for g in groups if weight(g)), key=lambda g: -weight(g))[:count], key=lambda g: g[0]["wall"])
    moments = [{"wall": [said - lead, said + opening], "clock": None}]
    for g in chosen:
        at = g[-1]["wall"]
        end = min(at + after, done)
        moments.append({"wall": [end - before - after, end], "clock": clock(at - said, True)})
    return settle(moments, changes, page0, k, size)


def plain(text: str) -> str:
    """A cast chunk's words, without its escape sequences (a cursor move reads as a space)."""
    text = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", " ", text)
    return re.sub(r"\x1b[()][A-Z0-9]|\x1b\][^\x07]*\x07", "", text)


def on_screen_span(events: list, cols: int, rows: int, words: str, said: float, done: float) -> tuple[float, float]:
    """When the words first show on the session's screen (the cast replayed as a terminal draws it) and when they
    leave it, in the cast's seconds; SystemExit when they never show."""
    import pyte
    screen = pyte.Screen(cols, rows)
    stream = pyte.Stream(screen)
    want = re.sub(r"\s+", " ", words).strip()
    on = None
    for t, kind, d in events:
        if kind != "o" or t > done:
            continue
        stream.feed(d)
        if t <= said:
            continue
        shown = want in re.sub(r"\s+", " ", " ".join(screen.display))
        if shown and on is None:
            on = t
        elif not shown and on is not None:
            return on, t
    if on is None:
        raise SystemExit(f"the fresh session never shows {words!r}")
    return on, done


def plan_fresh(take: dict, changes: list[dict], cast_marks: dict, events: list, size=(691, 520),
               lead=0.6, asked=2.6, tail=4.0, words: str | None = None, after: float | None = None,
               place: str | None = None) -> list[dict]:
    """The fresh segment: the question sent, then the answer as it ends, or, with `words`, while it shows them on the
    screen (the part of a long answer the beat is about); words it never shows are refused. `after` (the wall time the
    agent segment ends) makes the opening a jump from there: all the page changed since is kept clear too. `place` is
    where the window goes while that is clear (a take filmed on another view has no change marks for the Overview, so
    what the agent changed there is known only by looking: --fresh-place keeps it in sight)."""
    page0 = take["page"]["t0_wall"]
    k = FRAME[0] / take["page"]["css"][0]
    w0 = cast_marks["wall0"]
    said, done = cast_marks["marks"]["said"], ends(cast_marks, events)
    last = max((t for t, kind, _ in events if kind == "o" and t <= done), default=done)
    if words:
        on, off = on_screen_span(events, cast_marks.get("cols", 64), cast_marks.get("rows", 22), words, said, done)
        # the moment ends while the words are still there, at most 2.5 s after they first show
        last = min(on + 2.5, off) - 0.8
    moments = [{"wall": [w0 + said - lead, w0 + said + asked], "clock": None},
               {"wall": [w0 + last - tail + 0.8, w0 + min(last + 0.8, done)], "clock": clock((on if words else last) - said, False)}]
    settled = settle(moments, changes, page0, k, size, first=place)
    if after is not None:
        jump = [c for c in changes if after < page0 + c["t"] <= moments[0]["wall"][1]]
        boxes = settled[0]["boxes"] + [scale_box(b, k) for c in jump for b in c["boxes"]]
        near = settled[0]["near"] + [scale_box(b, k) for c in jump for b in c.get("near", [])]
        settled[0]["boxes"], settled[0]["near"], settled[0]["place"] = boxes, near, None
        settled[0]["place"], settled[0]["window"] = window_for(boxes, size, badge=False, prefer=place, near=near)
    return settled


# ---- rendering
def run(argv, **kw):
    r = subprocess.run([str(a) for a in argv], **kw)
    if r.returncode != 0:
        raise SystemExit(f"{argv[0]} {' '.join(map(str, argv[1:4]))} ... failed ({r.returncode})")
    return r


def term_window(cols: int, rows: int) -> tuple[int, int]:
    """term.html's window for a cast of this size at TERM_FONT, in CSS px (DejaVu Sans Mono's cell, the padding, the bar)."""
    return round(cols * TERM_FONT * 0.602 + 24), round(rows * 15 + 20 + 30)   # xterm.js draws a row 15 px high at 13 px


def render_term(work: Path, cast: Path, a: float, b: float, cols: int, rows: int, title: str, chrome: str,
                badge: str | None = None) -> tuple[Path, list]:
    """The terminal from `a` to `b` (the cast's seconds) at its own speed, ids masked, with `badge` (a clock) in its own
    bar: a video and the window's box."""
    work.mkdir(parents=True, exist_ok=True)
    files = work / "files"
    files.mkdir(exist_ok=True)
    shutil.copy(cast, files / "t.cast")
    w, h = term_window(cols, rows)
    from urllib.parse import quote
    beats = {"width": w + 80, "height": h + 80, "scale": 2, "beats": [{"name": "term", "steps": [
        {"goto": f"/demo/term.html?cast=/files/t.cast&from={a:.3f}&to={b:.3f}&speed=1&font={TERM_FONT}&lapse=bar&mask=ids,footer&title={quote(title)}" + (f"&badge={quote(badge)}" if badge else "")},
        {"waitFor": "window.__ready === true"}, {"wait": 300}, {"mark": "win", "rect": "#win"}, {"mark": "start"}, {"play": True}, {"wait": 200}]}]}
    (work / "beats.json").write_text(json.dumps(beats))
    run(["node", HERE / "walk.mjs", "--script", work / "beats.json", "--out", work / "walk", "--serve", files, "--chrome", chrome],
        stdout=subprocess.DEVNULL)
    marks = load(work / "walk" / "marks.jsonl")
    start = next(m["t"] for m in marks if m["label"] == "start")
    rect = next(m["rect"] for m in marks if m["label"] == "win")
    if abs(rect[2] - w) > 1:
        raise SystemExit(f"the terminal window drew {rect[2]} px wide, not {w}: its bar holds more than fits (title {title!r}, clock {badge!r})")
    run([sys.executable, HERE / "compose.py", work / "walk", work / "term.mp4", "--from", f"{start:.3f}", "--to", f"{start + (b - a):.3f}"])
    return work / "term.mp4", [round(v * 2) for v in rect]


def pngs(work: Path, size: tuple[int, int], label: str | None) -> dict:
    """The window's rounded mask, its shadow, and the clock badge, as PNGs."""
    from PIL import Image, ImageDraw, ImageFilter, ImageFont
    w, h = size
    r = round(10 * w / 560)
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, w - 1, h - 1], r, fill=255)
    mask.save(work / "mask.png")
    # the shadow's whole canvas reaches SHADOW px past the window, never past the PAD kept clear around a change
    s = SHADOW
    shadow = Image.new("RGBA", (w + 2 * s, h + 2 * s), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle([s, s + 6, s + w, s + h + 6], r, fill=(0, 0, 0, 120))
    shadow.filter(ImageFilter.GaussianBlur(8)).save(work / "shadow.png")
    out = {"mask": work / "mask.png", "shadow": work / "shadow.png", "shadow_off": s}
    if label:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 26)
        tw = round(font.getlength(label))
        if tw + 36 > BADGE_W:
            raise SystemExit(f"the clock badge {label!r} is wider than the {BADGE_W} px kept for it")
        badge = Image.new("RGBA", (tw + 36, BADGE_H), (0, 0, 0, 0))
        d = ImageDraw.Draw(badge)
        d.rounded_rectangle([0, 0, tw + 35, BADGE_H - 1], BADGE_H // 2, fill=(22, 24, 29, 230))
        d.text((18, BADGE_H / 2), label, font=font, fill=(255, 255, 255, 255), anchor="lm")
        badge.save(work / "badge.png")
        out["badge"], out["badge_w"] = work / "badge.png", tw + 36
    return out


def render(take_dir: Path, name: str, moments: list[dict], cast: Path, cols: int, rows: int, title: str,
           out: Path, chrome: str, voice_at: float = 0.4) -> dict:
    take = json.loads((take_dir / "take.json").read_text())
    page0 = take["page"]["t0_wall"]
    wall0 = take["casts"][name]["wall0"]
    walk = take_dir / "page" / "walk"
    work = out / "work"
    shutil.rmtree(work, ignore_errors=True)
    pieces, at = [], 0.0
    k = FRAME[0] / take["page"]["css"][0]
    walk_marks, walk_frames = load(walk / "marks.jsonl"), load(walk / "frames.jsonl")
    for i, m in enumerate(moments):
        cache = {}   # the frames one moment shows, decoded once for its spots
        a, b = m["wall"]
        mw = work / f"m{i}"
        mw.mkdir(parents=True)
        x, y, w, h = m["window"]
        p = pngs(mw, (w, h), m["clock"])
        if m["clock"]:
            # the clock beside the window where the page is blank all through the moment, else in the window's bar
            m["text"] = texts_for(walk_marks, page0, k, a, b)
            m["badge"] = badge_for(m, p["badge_w"], blank=lambda box: page_blank(walk, a - page0, b - page0, box, walk_frames, cache))
            if m["badge"] == "bar":
                p.pop("badge")
        run([sys.executable, HERE / "compose.py", walk, mw / "page.mp4", "--from", f"{a - page0:.3f}", "--to", f"{b - page0:.3f}"])
        term, (cx, cy, cw, ch) = render_term(mw / "term", cast, a - wall0, b - wall0, cols, rows, title, chrome,
                                             badge=m["clock"] if m.get("badge") == "bar" else None)
        so = p["shadow_off"]
        bx, by = m["badge"][:2] if "badge" in p else (x, y)
        inputs = ["-i", mw / "page.mp4", "-i", term, "-loop", "1", "-i", p["shadow"], "-loop", "1", "-i", p["mask"]]
        graph = (f"[1:v]crop={cw}:{ch}:{cx}:{cy},scale={w}:{h}:flags=lanczos,format=rgba[t];[3:v]format=gray[m];[t][m]alphamerge[tm];"
                 f"[0:v]fps=30,format=rgba[p];[p][2:v]overlay={x - so}:{y - so}:shortest=1[s];[s][tm]overlay={x}:{y}:eof_action=repeat[v0]")
        if "badge" in p:
            inputs += ["-loop", "1", "-i", p["badge"]]
            graph += f";[v0][4:v]overlay={bx}:{by}:shortest=1[v1];[v1]format=yuv420p[v]"
        else:
            graph += ";[v0]format=yuv420p[v]"
        run(["ffmpeg", "-loglevel", "error", "-y", *inputs, "-filter_complex", graph, "-map", "[v]", "-t", f"{b - a:.3f}",
             "-r", "30", "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p", mw / "piece.mp4"])
        pieces.append(mw / "piece.mp4")
        m["cut"] = [round(at, 3), round(at + b - a, 3)]
        at += b - a
    out.mkdir(parents=True, exist_ok=True)
    (work / "list.txt").write_text("".join(f"file '{p}'\n" for p in pieces))
    run(["ffmpeg", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", work / "list.txt", "-c:v", "libx264",
         "-crf", "18", "-pix_fmt", "yuv420p", "-r", "30", "-movflags", "+faststart", out / "screen.mp4"])
    marks = [{"t": 0, "label": f"beat {name}"}, {"t": voice_at, "label": "voice"}]
    marks += [{"t": m["cut"][0], "label": "moment", "clock": m["clock"]} for m in moments]
    (out / "screen.marks.jsonl").write_text("".join(json.dumps(x) + "\n" for x in marks))
    seg = {"id": name, "speed": 1, "seconds": round(at, 2), "frame": list(FRAME), "page": {"css": take["page"]["css"]},
           "terminal": {"cols": cols, "rows": rows, "font": TERM_FONT, "share": PIP_SHARE},
           "moments": [{f: m[f] for f in ("wall", "cut", "clock", "place", "window", "badge", "boxes", "near", "words", "gone", "text") if f in m}
                       for m in moments],
           "runs_before": take["runs_before"], "runs_after": take["runs_after"], "made": take["made"]}
    changes = [x for x in walk_marks if x["label"] == "change"]
    bad = check(seg) + check_marks(seg, changes, page0, k) + check_page(seg, walk, page0, k)
    seg["marks_checked"] = len(changes)
    if bad:
        raise SystemExit("\n".join(bad))
    (out / "segment.json").write_text(json.dumps(seg, indent=1) + "\n")
    # one frame per moment, as it ends (the page settled after its change, the answer's words on screen)
    sheet = out.parent / "sheet"
    sheet.mkdir(parents=True, exist_ok=True)
    for i, m in enumerate(moments):
        t = m["cut"][1] - 0.15
        run(["ffmpeg", "-loglevel", "error", "-y", "-ss", f"{t:.2f}", "-i", out / "screen.mp4", "-frames:v", "1", sheet / f"{name}-{i}.png"])
    shutil.rmtree(work, ignore_errors=True)
    return seg


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("take", type=Path)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--moments", type=int, default=3)
    ap.add_argument("--chrome", default="google-chrome")
    ap.add_argument("--fresh-words", help="the fresh segment's answer moment ends as the answer first shows these words")
    ap.add_argument("--fresh-place", choices=PLACES, help="where the fresh segment's window goes while that is clear of what changes")
    a = ap.parse_args(argv)
    a.take = a.take.resolve()   # ffmpeg's concat list reads a relative path from the list's own folder
    take = json.loads((a.take / "take.json").read_text())
    out = (a.out or a.take / "cut").resolve()
    changes = [m for m in load(a.take / "page" / "walk" / "marks.jsonl") if m["label"] == "change"]
    segs = {}
    for name in ("agent", "fresh"):
        if name not in take["casts"]:
            continue
        cast = a.take / f"{name}.cast"
        head, *rest = cast.read_text().splitlines()
        cols, rows = json.loads(head)["width"], json.loads(head)["height"]
        tw, th = term_window(cols, rows)
        w = round(FRAME[0] * PIP_SHARE)
        size = (w, round(w * th / tw))
        cm = take["casts"][name]
        if name == "agent":
            moments = plan_agent(take, changes, cm, a.moments, size, events=[json.loads(l) for l in rest if l.strip()])
        else:
            agent_end = segs["agent"]["moments"][-1]["wall"][1] if "agent" in segs else None
            moments = plan_fresh(take, changes, cm, [json.loads(l) for l in rest if l.strip()], size, words=a.fresh_words, after=agent_end, place=a.fresh_place)
        seg = segs[name] = render(a.take, name, moments, cast, cols, rows, take["record"], out / name, a.chrome)
        print(f"{name}: {out / name / 'screen.mp4'} ({seg['seconds']} s, {len(seg['moments'])} moments: "
              + ", ".join(f"{m['clock'] or 'start'} {m['place']}" for m in seg["moments"]) + ")")
    return 0


if __name__ == "__main__":
    sys.exit(main())
