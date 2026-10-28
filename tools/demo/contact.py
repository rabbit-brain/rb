#!/usr/bin/env python3
"""A contact sheet of a walk, or of a composed video: the frame on screen at each timing marker, labelled, in a grid.

    python tools/demo/contact.py <walk dir> <sheet.png> [--marks shot] [--cols 3] [--width 720]
    python tools/demo/contact.py --video <cut.mp4> <sheet.png> [--every 5] [--cols 4] [--width 480]

A walk's frame for a marker is the latest frame sent at or before it (as compose.py holds it). Each tile is also saved
whole beside the sheet (<sheet>-NN.png), so each frame can be read at its own size. Needs Pillow (and ffmpeg for --video).
"""
from __future__ import annotations

import argparse
import bisect
import json
import subprocess
import sys
import tempfile
from pathlib import Path


def tiles_from_walk(walk: Path, prefix: str) -> list[tuple[str, Path]]:
    frames = [json.loads(l) for l in (walk / "frames.jsonl").read_text().splitlines() if l.strip()]
    marks = [json.loads(l) for l in (walk / "marks.jsonl").read_text().splitlines() if l.strip()]
    ft = [f["t"] for f in frames]
    out = []
    for m in marks:
        if m["label"].startswith(prefix):
            i = max(0, bisect.bisect_right(ft, m["t"]) - 1)
            out.append((f'{m["t"]:.1f}s {m["label"][len(prefix):].strip() or m["label"]}', walk / frames[i]["file"]))
    return out


def tiles_from_video(video: Path, every: float, tmp: Path) -> list[tuple[str, Path]]:
    # the picture's own length: a cut's sound can run past its last frame, where no frame is to be had
    probe = lambda entries, *sel: subprocess.run(["ffprobe", "-v", "error", *sel, "-show_entries", entries, "-of", "csv=p=0", str(video)],
                                                 capture_output=True, text=True, check=True).stdout.strip()
    v = probe("stream=duration", "-select_streams", "v:0")
    length = float(v) if v and v != "N/A" else float(probe("format=duration"))
    out, t = [], 0.0
    while t < length:
        png = tmp / f"{t:08.2f}.png"
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-ss", f"{t}", "-i", str(video), "-frames:v", "1", str(png)], check=True)
        out.append((f"{int(t) // 60}:{int(t) % 60:02d}", png))
        t += every
    return out


def sheet(tiles: list[tuple[str, Path]], out: Path, cols: int, width: int) -> None:
    from PIL import Image, ImageDraw

    if not tiles:
        raise SystemExit("no frames to put on the sheet")
    imgs = []
    for k, (label, path) in enumerate(tiles):
        im = Image.open(path).convert("RGB")
        im.save(out.with_name(f"{out.stem}-{k:02d}.png"))
        h = round(im.height * width / im.width)
        imgs.append((label, im.resize((width, h), Image.LANCZOS)))
    th = max(i.height for _, i in imgs) + 28
    rows = (len(imgs) + cols - 1) // cols
    canvas = Image.new("RGB", (cols * (width + 12) + 12, rows * (th + 12) + 12), (235, 236, 240))
    d = ImageDraw.Draw(canvas)
    for k, (label, im) in enumerate(imgs):
        x, y = 12 + (k % cols) * (width + 12), 12 + (k // cols) * (th + 12)
        d.text((x + 2, y + 6), label, fill=(20, 20, 20))
        canvas.paste(im, (x, y + 28))
    canvas.save(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("src", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--video", action="store_true")
    ap.add_argument("--marks", default="shot")
    ap.add_argument("--every", type=float, default=5.0)
    ap.add_argument("--cols", type=int, default=3)
    ap.add_argument("--width", type=int, default=720)
    a = ap.parse_args(argv)
    with tempfile.TemporaryDirectory() as tmp:
        tiles = tiles_from_video(a.src, a.every, Path(tmp)) if a.video else tiles_from_walk(a.src, a.marks)
        sheet(tiles, a.out, a.cols, a.width)
    print(f"{a.out}: {len(tiles)} frames")
    return 0


if __name__ == "__main__":
    sys.exit(main())
