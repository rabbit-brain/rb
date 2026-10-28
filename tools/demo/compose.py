#!/usr/bin/env python3
"""Turns a walk (walk.mjs's frames, with their times) into constant 30 fps H.264, and draws the walk's cursor on it.

    python tools/demo/compose.py <walk dir> <out.mp4> [--fps 30] [--width 1920] [--from <s>] [--to <s>]
    python tools/demo/compose.py --side <left.mp4> <right.mp4> <out.mp4>     (two beats side by side, the same height)
    python tools/demo/compose.py --overlay <out.png> [--width 1920 --height 1200] [--caption <words>] [--title <words> --sub <words>]

A zoom frames the part of the screen a beat's voice speaks of: the real frames cropped to a box (in the walk's CSS
pixels, the frame's own shape) and scaled to the whole frame, easing in so it is full by `full` and easing out from
`until` (seconds from the video's start), each ease `ease` seconds long. The cursor is drawn before the crop, so it is
the one the walk moved; while the zoom is on, every cursor position must be inside the box, or nothing is made.
`length` holds the last frame until then, so a zoom can ease out after the screen's last change.

The screencast sends a frame only when the screen changes, so each output frame shows the latest frame sent at or
before its time. The cursor is drawn where walk.mjs's own mouse events put it (cursor.jsonl), and only here: rb's
pages never hold one. Needs Pillow and ffmpeg. marks.jsonl is copied beside the video, its times from the video's start.
"""
from __future__ import annotations

import argparse
import bisect
import json
import shutil
import subprocess
import sys
from pathlib import Path


def load(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()] if path.exists() else []


def arrow(scale: float):
    """The cursor's outline, its tip at (0, 0), in screen pixels."""
    pts = [(0, 0), (0, 17), (4.5, 13), (7.5, 20), (10, 19), (7, 12.5), (12.5, 12.5)]
    return [(x * scale, y * scale) for x, y in pts]


def span(walk: Path, start: float | None = None, end: float | None = None) -> tuple[float, float]:
    """The walk's first and last moment as compose draws them: its first frame (or `start`) to its last frame or mark."""
    frames, marks = load(walk / "frames.jsonl"), load(walk / "marks.jsonl")
    if not frames:
        raise SystemExit(f"{walk}: no frames")
    t_start = start if start is not None else max(0.0, frames[0]["t"])
    t_end = end if end is not None else max(frames[-1]["t"], marks[-1]["t"] if marks else 0)
    return t_start, t_end


def _ease(k: float) -> float:
    k = min(1.0, max(0.0, k))
    return k * k * (3 - 2 * k)


def zoom_at(t: float, zoom: dict | None) -> float:
    """How far in the zoom is at `t`: 0 (the whole frame) to 1 (the box), eased in up to `full`, out from `until`."""
    if not zoom:
        return 0.0
    e = zoom.get("ease", 0.4)
    return min(_ease((t - (zoom["full"] - e)) / e), 1 - _ease((t - zoom["until"]) / e))


def check_box(box, width: int, height: int, least: int = 720) -> None:
    """A zoom box stays inside the frame, keeps the frame's shape, and is never under `least` CSS pixels wide."""
    x, y, w, h = box
    if not (x >= 0 and y >= 0 and x + w <= width and y + h <= height):
        raise SystemExit(f"the zoom box {box} leaves the {width}x{height} frame")
    if w < least:
        raise SystemExit(f"the zoom box {box} is {w} px wide, under {least}")
    if abs(w * height - h * width) > width:
        raise SystemExit(f"the zoom box {box} is not the frame's shape ({width}:{height})")


def compose(walk: Path, out: Path, fps: int, width: int | None, start: float | None, end: float | None,
            zoom: dict | None = None, length: float | None = None) -> None:
    from PIL import Image, ImageDraw

    meta = json.loads((walk / "walk.json").read_text())
    frames = load(walk / "frames.jsonl")
    if not frames:
        raise SystemExit(f"{walk}: no frames")
    cursor = load(walk / "cursor.jsonl")
    marks = load(walk / "marks.jsonl")
    scale = meta["scale"]
    ft = [f["t"] for f in frames]
    ct = [c["t"] for c in cursor]
    t_start, t_end = span(walk, start, end)
    if length is not None:
        t_end = max(t_end, t_start + length)
    # the first frames can come before the device scale took hold: every frame is drawn at the walk's own size
    W, H = round(meta["width"] * scale), round(meta["height"] * scale)
    ow = width or W
    oh = round(H * ow / W / 2) * 2
    if zoom:
        check_box(zoom["box"], meta["width"], meta["height"], zoom.get("least", 720))
        bx, by, bw, bh = zoom["box"]
        # the arrow is drawn below and right of its tip: a tip this far inside the box shows the whole arrow
        held = [c for c in cursor if c["t"] - t_start <= zoom["until"] + zoom.get("ease", 0.4)]
        before = [c for c in held if zoom_at(c["t"] - t_start, zoom) == 0 and c["t"] - t_start < zoom["full"]]
        during = [c for c in held if zoom_at(c["t"] - t_start, zoom) > 0]
        # the cursor shown when the zoom starts is the last one before it, unless one is shown during it
        seen = ([before[-1]] if before else []) + during
        for c in seen:
            if not (bx <= c["x"] <= bx + bw - 14 and by <= c["y"] <= by + bh - 22):
                raise SystemExit(f"the cursor at ({c['x']:.0f}, {c['y']:.0f}), {c['t'] - t_start:.2f} s in, is outside the zoom box "
                                 f"{zoom['box']}: move it into the box before the zoom, or move the box")
    vf = ["-vf", f"scale={ow}:{oh}:flags=lanczos"] if not zoom else []
    size = f"{W}x{H}" if not zoom else f"{ow}x{oh}"
    ff = subprocess.Popen(["ffmpeg", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", size,
                           "-r", str(fps), "-i", "-", *vf, "-c:v", "libx264",
                           "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out)],
                          stdin=subprocess.PIPE)
    shape = arrow(scale * 1.15)
    cache_key, cache_bytes, base_key, base_img = None, None, None, None
    n = int((t_end - t_start) * fps) + 1
    for i in range(n):
        t = t_start + i / fps
        fi = max(0, bisect.bisect_right(ft, t) - 1)
        ci = bisect.bisect_right(ct, t) - 1
        c = cursor[ci] if ci >= 0 else None
        z = round(zoom_at(i / fps, zoom), 4)
        key = (fi, None if c is None else (round(c["x"], 1), round(c["y"], 1), c["down"]), z)
        if key != cache_key:
            if base_key != fi:
                base_img = Image.open(walk / frames[fi]["file"]).convert("RGB")
                if base_img.size != (W, H):
                    base_img = base_img.resize((W, H), Image.LANCZOS)
                base_key = fi
            img = base_img.copy()
            if c is not None:
                d = ImageDraw.Draw(img)
                x, y = c["x"] * scale, c["y"] * scale
                if c["down"]:
                    r = 11 * scale
                    d.ellipse([x - r, y - r, x + r, y + r], outline=(40, 120, 255), width=max(2, int(2 * scale)))
                pts = [(x + px, y + py) for px, py in shape]
                d.polygon(pts, fill=(255, 255, 255), outline=(0, 0, 0))
                d.line(pts + [pts[0]], fill=(0, 0, 0), width=max(1, int(scale)))
            if zoom:
                # from the whole frame (z 0) to the box (z 1), in screen pixels, then scaled to the output's size
                crop = [(1 - z) * whole + z * v * scale for whole, v in zip((0, 0, W, H), (bx, by, bx + bw, by + bh))]
                img = img.resize((ow, oh), Image.LANCZOS, box=tuple(crop))
            cache_key, cache_bytes = key, img.tobytes()
        ff.stdin.write(cache_bytes)
    ff.stdin.close()
    if ff.wait() != 0:
        raise SystemExit("ffmpeg failed")
    out.with_suffix(".marks.jsonl").write_text(
        "".join(json.dumps({"t": round(m["t"] - t_start, 3), "label": m["label"], **({"rect": m["rect"]} if "rect" in m else {})}) + "\n"
                for m in marks if t_start <= m["t"] <= t_end))


FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans{}.ttf"


def _wrap(draw, text: str, font, width: float) -> list[str]:
    lines, cur = [], ""
    for word in text.split():
        trial = f"{cur} {word}".strip()
        if cur and draw.textlength(trial, font=font) > width:
            lines.append(cur)
            cur = word
        else:
            cur = trial
    return lines + ([cur] if cur else [])


def overlay(out: Path, width: int, height: int, caption: str | None = None, title: str | None = None,
            sub: str | None = None) -> int:
    """The film's words over a beat (FILM-V2): `caption` at the bottom, white on a dark band, its type at least 44 px
    at 1080p (so a muted film still tells the story), and `title` with `sub` large in the middle (the pitch's
    wordmark). A transparent PNG the frame's size, laid over the beat's video by cut.py; returns the band's height."""
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    k = height / 1080
    if title:
        # a scrim, so the wordmark reads over any screen
        d.rectangle([0, 0, width, height], fill=(15, 17, 21, 170))
        tf = ImageFont.truetype(FONT.format("-Bold"), round(118 * k))
        sf = ImageFont.truetype(FONT.format(""), round(46 * k))
        tw = d.textlength(title, font=tf)
        y = height * 0.36
        d.text(((width - tw) / 2, y), title, font=tf, fill=(255, 255, 255, 255))
        if sub:
            for i, line in enumerate(_wrap(d, sub, sf, width * 0.8)):
                lw = d.textlength(line, font=sf)
                d.text(((width - lw) / 2, y + 170 * k + i * 62 * k), line, font=sf, fill=(232, 233, 238, 255))
    caption = (caption or "").strip() or None
    if caption:
        size = round(50 * k)
        f = ImageFont.truetype(FONT.format("-Bold"), size)
        lines = _wrap(d, caption, f, width * 0.94)
        pad, gap = round(26 * k), round(12 * k)
        band = pad * 2 + len(lines) * size + (len(lines) - 1) * gap
        if len(lines) > 2 or any(d.textlength(line, font=f) > width * 0.94 for line in lines):
            raise SystemExit(f"the caption {caption!r} does not fit two lines across the frame: shorten it")
        d.rectangle([0, height - band, width, height], fill=(10, 11, 14, 222))
        for i, line in enumerate(lines):
            lw = d.textlength(line, font=f)
            d.text(((width - lw) / 2, height - band + pad + i * (size + gap) - round(4 * k)), line, font=f, fill=(255, 255, 255, 255))
    img.save(out)
    return band if caption else 0


def side(left: Path, right: Path, out: Path) -> None:
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(left), "-i", str(right), "-filter_complex",
                    "[0:v][1:v]scale2ref=oh*mdar:ih[a][b];[a][b]hstack=inputs=2,scale=trunc(iw/2)*2:trunc(ih/2)*2,fps=30",
                    "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p", "-shortest", str(out)], check=True)


def main(argv=None) -> int:
    if not shutil.which("ffmpeg"):
        raise SystemExit("compose.py needs ffmpeg")
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "--overlay":
        ap = argparse.ArgumentParser()
        ap.add_argument("--overlay", type=Path, required=True)
        ap.add_argument("--width", type=int, default=1920)
        ap.add_argument("--height", type=int, default=1200)
        ap.add_argument("--caption")
        ap.add_argument("--title")
        ap.add_argument("--sub")
        a = ap.parse_args(argv)
        print(json.dumps({"band": overlay(a.overlay, a.width, a.height, a.caption, a.title, a.sub)}))
        return 0
    if argv and argv[0] == "--side":
        side(Path(argv[1]), Path(argv[2]), Path(argv[3]))
        return 0
    ap = argparse.ArgumentParser()
    ap.add_argument("walk", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--width", type=int)
    ap.add_argument("--from", dest="start", type=float)
    ap.add_argument("--to", dest="end", type=float)
    ap.add_argument("--zoom", type=json.loads, help='{"box": [x, y, w, h], "full": s, "until": s, "ease": s}')
    ap.add_argument("--length", type=float, help="hold the last frame until this many seconds from the start")
    a = ap.parse_args(argv)
    compose(a.walk, a.out, a.fps, a.width, a.start, a.end, a.zoom, a.length)
    return 0


if __name__ == "__main__":
    sys.exit(main())
