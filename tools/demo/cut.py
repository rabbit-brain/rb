#!/usr/bin/env python3
"""The demo's cut in one command: every segment of cut.json captured for real, composed at 30 fps, fitted to its
voice line, joined, with a contact sheet and the timing of each beat.

    python tools/demo/cut.py --record <study folder holding .rb/> --import-cast <import.cast> \
        --rb-bin <dir holding rb> --out <dir> [--voice synth|<dir of <segment>.wav>] [--share-segment <dir>] \
        [--only pitch,turns,...] [--spec cut.json --pain-cast <pain.cast> --mode shell|rb_open_today]

- The record is copied first (a beat may write to it), into --work (out/work by default; rb open's place line shows
  its path, so a short one reads better), and rb open runs on the copy, on 127.0.0.1, in a clean terminal
  (no agent-session variable). rb open's `/` is the shell for everyone, so no test switch is set in either mode.
- Each segment is one walk (walk.mjs) and one video (compose.py). A segment with no steps for the mode is left out and
  said so.
- The voice: `synth` speaks each segment's line with ffmpeg's flite, a placeholder for timing only; a folder holds the
  owner's takes, one WAV per segment (pain.wav, import.wav, ...). A segment lasts as long as its voice line and a
  breath, its last frame held, or as long as its screen if that is longer.
- Out: <out>/cut.mp4 (1920x1200, H.264, AAC), <out>/cut.timing.json (each beat's start and length), <out>/sheet.png
  and its frames, and <out>/segments/<id>/ (the walk, the video, the voice).
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _tool(name):
    spec = importlib.util.spec_from_file_location(f"demo_{name}", HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def duration(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True, check=True).stdout
    return float(out)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_open(folder: Path, rb_bin: str, test_switch: bool = False):
    """rb open on the folder, as the researcher starts it; `test_switch` (loops.py, for an rb built before rb open drew
    the shell by default) sets RB_TEST_SHELL=1."""
    env = _tool("import_take").clean_env(rb_bin, None, str(Path.home()))
    env.pop("PS1", None)
    if test_switch:
        env["RB_TEST_SHELL"] = "1"
    port = free_port()
    proc = subprocess.Popen([str(Path(rb_bin) / "rb"), "open", "--port", str(port)], cwd=folder, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    url = f"http://127.0.0.1:{port}/"
    for _ in range(100):
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                if r.status == 200:
                    return proc, url
        except urllib.error.HTTPError as e:
            # rb open answered, and said no (the shell not built, say): its own words, read whole before it is
            # stopped, since its body may still be on the way after the status line
            try:
                body = e.read().decode(errors="replace")[:400]
            except OSError:
                body = ""  # a body that never ends: the status is said alone
            proc.kill()
            raise SystemExit(f"rb open answered {e.code} at {url}: {body}")
        except Exception:
            time.sleep(0.1)
    proc.kill()
    raise SystemExit(f"rb open did not answer at {url}: {proc.stdout.read().decode(errors='replace')[-800:]}")


def synth(text: str, wav: Path) -> None:
    safe = text.replace("\\", " ").replace(":", " ").replace("'", "’")
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"flite=text='{safe}':voice=slt",
                    "-ar", "48000", "-ac", "1", str(wav)], check=True)


def voice_start(video: Path, seg: dict | None = None) -> float:
    """When the voice starts in a segment: at its `voice` marker (once its screen shows what the line says), else at the
    segment's `voice_at` seconds (a cast plays in one step, so it cannot drop a marker where its screen shows what the
    line says), else at 0."""
    marks = video.with_suffix(".marks.jsonl")
    for line in (marks.read_text().splitlines() if marks.exists() else []):
        m = json.loads(line)
        if m["label"] == "voice":
            return max(0.0, float(m["t"]))
    return max(0.0, float((seg or {}).get("voice_at", 0.0)))


def fit(video: Path, wav: Path | None, out: Path, breath: float = 0.6, at: float | None = None,
        overlay: Path | None = None, blur: float = 0) -> float:
    """The segment as long as its voice and a breath (its last frame held), or its screen if that is longer; the voice
    starts at the segment's `voice` marker (or at `at`). `blur` softens the screen (the pitch's still), and `overlay`
    (compose.py --overlay: the beat's caption, the pitch's wordmark) is laid over every frame of it."""
    v = duration(video)
    at = (voice_start(video) if at is None else at) if wav else 0.0
    a = at + duration(wav) + breath if wav else 0.0
    length = max(v, a)
    cmd = ["ffmpeg", "-loglevel", "error", "-y", "-i", str(video)]
    cmd += ["-i", str(wav)] if wav else ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono"]
    cmd += ["-i", str(overlay)] if overlay else []
    vchain = f"[0:v]tpad=stop_mode=clone:stop_duration={max(0.0, length - v) + 0.05:.3f},fps=30"
    vchain += f",gblur=sigma={blur}" if blur else ""
    vchain += "[s];[s][2:v]overlay=0:0:eof_action=repeat[v];" if overlay else "[v];"
    cmd += ["-filter_complex", vchain + f"[1:a]adelay={int(at * 1000)}:all=1,apad,aresample=48000[a]", "-map", "[v]", "-map", "[a]", "-t", f"{length:.3f}",
            "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", str(out)]
    subprocess.run(cmd, check=True)
    return length


EASE = 0.4  # seconds a zoom takes to ease in, and out
# the demo is the shell, and its beats are the ones zoomed on what their voice says; rb_open_today is rb open as it was
DEFAULT_MODE = "shell"


def zoom_for(seg: dict, mode: str) -> dict | None:
    """A segment's zoom in this mode: {"box": [x, y, w, h], ...} in the walk's CSS pixels, one per mode where the
    segment has steps per mode, else one for its steps; none, and the segment shows the whole frame."""
    z = seg.get("zoom")
    if not z:
        return None
    return z if "box" in z else z.get(mode)


def zoom_plan(screen: float, voice_at: float, voice: float | None, breath: float = 0.6, ease: float = EASE) -> dict:
    """When a zoomed segment's voice starts, how long the segment lasts, and when the zoom is full and when it starts
    easing out: it is full by the time the line starts and eases out only after it ends, so it never moves
    mid-sentence (a line marked to start before the ease is done waits for it)."""
    at = max(voice_at, ease)
    length = max(screen, at + voice + breath) if voice else max(screen, at + breath)
    until = max(at, length - ease - 0.05)
    return {"at": round(at, 3), "length": round(length, 3), "full": round(at, 3), "until": round(until, 3), "ease": ease}


def check_subject(seg_id: str, box, marks: list[dict], band: float = 0.0) -> None:
    """The part a segment's voice speaks of (its walk's `subject` markers, each with where that part was drawn) is
    inside the segment's zoom box, and above its caption's band (`band`: the share of the frame's height it covers)."""
    x, y, w, h = box
    for m in marks:
        if m["label"] == "subject" and "rect" in m:
            rx, ry, rw, rh = m["rect"]
            if not (rx >= x - 0.5 and ry >= y - 0.5 and rx + rw <= x + w + 0.5 and ry + rh <= y + h + 0.5):
                raise SystemExit(f"{seg_id}: the part its voice speaks of, drawn at {m['rect']}, is not inside its zoom box {list(box)}")
            if ry + rh > y + h * (1 - band) + 0.5:
                raise SystemExit(f"{seg_id}: the part its voice speaks of, drawn at {m['rect']}, is under its caption's band "
                                 f"(below {y + h * (1 - band):.0f} in its zoom box {list(box)})")


def work_dir(out: Path, work: Path | None, record: Path) -> Path:
    """Where the record's copy goes: under --work, else out/work, keeping the record folder's own name."""
    return (work.resolve() if work else out / "work") / record.resolve().name


MARK = ".rb-demo-cut-copy"  # the file cut.py leaves in a copy it made, so it only ever replaces its own


def fresh_copy(record: Path, work: Path) -> None:
    """A fresh copy of the record at `work`: never the record itself, nor a folder cut.py did not make."""
    rec, w = record.resolve(), work.resolve()
    if w == rec or rec in w.parents or w in rec.parents:
        raise SystemExit(f"--work {work.parent} would hold the record itself ({rec}): give another folder")
    if w.exists():
        if not (w / MARK).exists():
            raise SystemExit(f"{w} is not a copy cut.py made: move it, or give another --work")
        shutil.rmtree(w)
    w.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["cp", "-a", str(rec), str(w)], check=True)
    (w / MARK).write_text("a copy of " + str(rec) + " made by tools/demo/cut.py; it replaces this folder on its next run\n")


def voice_line(seg: dict, mode: str) -> str:
    """A segment's voice: one line, or one per mode when the screens differ (rb open as it is has no composer)."""
    v = seg["voice"]
    return v[mode] if isinstance(v, dict) else v


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--record", required=True, type=Path)
    ap.add_argument("--import-cast", required=True, type=Path)
    ap.add_argument("--pain-cast", type=Path, help="the pain beat's cast (cut.json's storyboard; film.json has none)")
    ap.add_argument("--rb-bin", required=True)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--mode", default=DEFAULT_MODE, choices=["shell", "rb_open_today"])
    ap.add_argument("--voice")
    ap.add_argument("--only")
    ap.add_argument("--work", type=Path, help="where the record's copy goes (its folder name is kept); rb open's place line shows this path")
    ap.add_argument("--share-segment", type=Path, help="the share beat as share_beat.mjs wrote it (screen.mp4 and its marks);"
                    " without it, the segment's own steps are a placeholder")
    ap.add_argument("--spec", type=Path, default=HERE / "film.json",
                    help="the film's beats: film.json (FILM-V2, the default), or cut.json (dry-run-9's storyboard)")
    ap.add_argument("--python", default=sys.executable, help="the Python with Pillow that runs compose.py and contact.py")
    a = ap.parse_args(argv)

    spec = json.loads(a.spec.read_text())
    compose = _tool("compose")
    for seg in spec["segments"]:
        z = zoom_for(seg, a.mode)
        if z:
            compose.check_box(z["box"], spec["width"], spec["height"])
    # what a cut needs is checked before anything is captured
    only = set(a.only.split(",")) if a.only else None
    ids = [seg["id"] for seg in spec["segments"]]
    if only and only - set(ids):
        raise SystemExit(f"--only names no segment: {', '.join(sorted(only - set(ids)))} (the segments: {', '.join(ids)})")
    if a.voice and a.voice != "synth":
        wanted = [seg["id"] for seg in spec["segments"] if (not only or seg["id"] in only)
                  and (seg.get("steps") or seg.get("variants", {}).get(a.mode))]
        missing = [i for i in wanted if not (Path(a.voice) / f"{i}.wav").exists()]
        if missing:
            raise SystemExit(f"no voice take for {', '.join(missing)} in {a.voice}")
        for i in wanted:
            try:
                if duration(Path(a.voice) / f"{i}.wav") <= 0:
                    raise ValueError
            except Exception:
                raise SystemExit(f"{Path(a.voice) / (i + '.wav')} holds no sound ffprobe can read")
    if a.share_segment and not (a.share_segment / "screen.mp4").exists():
        raise SystemExit(f"--share-segment {a.share_segment} holds no screen.mp4: share_beat.mjs writes one")
    out = a.out.resolve()
    (out / "segments").mkdir(parents=True, exist_ok=True)
    files = out / "files"
    files.mkdir(exist_ok=True)
    if a.pain_cast:
        shutil.copy(a.pain_cast, files / "pain.cast")
    shutil.copy(a.import_cast, files / "import.cast")
    work = work_dir(out, a.work, a.record)
    fresh_copy(a.record, work)
    # the study's own agent log, as the record's repository holds it (film.json's turn beats read it)
    if (work / "logs" / "agent.log").exists():
        shutil.copy(work / "logs" / "agent.log", files / "agent.log")
    # every file a segment plays is here before anything is filmed (a pain beat with no --pain-cast, a turn beat
    # with no agent log in the record)
    for seg in spec["segments"]:
        if only and seg["id"] not in only:
            continue
        steps = seg.get("steps") or seg.get("variants", {}).get(a.mode) or []
        if seg.get("external") and a.share_segment:
            continue
        for name in sorted(set(re.findall(r"/files/([^&\s\"']+)", json.dumps(steps)))):
            if not (files / name).exists():
                raise SystemExit(f"{seg['id']}: it plays /files/{name}, and there is none (give --pain-cast or --import-cast, "
                                 "or a record whose logs/agent.log is there)")
    proc, url = start_open(work, a.rb_bin)
    timing, parts = [], []
    try:
        for seg in spec["segments"]:
            if only and seg["id"] not in only:
                continue
            steps = seg.get("steps") or seg.get("variants", {}).get(a.mode) or []
            if not steps:
                print(f"{seg['id']}: no steps for {a.mode}, left out")
                continue
            d = out / "segments" / seg["id"]
            if d.exists():
                shutil.rmtree(d)
            d.mkdir(parents=True)
            outside = seg.get("external") and a.share_segment
            if not outside:
                text = json.dumps(steps).replace("{open}", url)
                beats = {"width": spec["width"], "height": spec["height"], "scale": spec["scale"],
                         "beats": [{"name": seg["id"], "steps": json.loads(text)}]}
                (d / "beats.json").write_text(json.dumps(beats, indent=1))
                subprocess.run(["node", str(HERE / "walk.mjs"), "--script", str(d / "beats.json"), "--out", str(d / "walk"),
                                "--serve", str(files)], check=True, timeout=600)
            if seg.get("external") and not a.share_segment:
                print(f"{seg['id']}: a placeholder (no --share-segment)")
            wav = None
            if a.voice == "synth":
                wav = d / "voice.wav"
                synth(voice_line(seg, a.mode), wav)
            elif a.voice:
                wav = Path(a.voice) / f"{seg['id']}.wav"
                if not wav.exists():
                    raise SystemExit(f"no voice take for {seg['id']}: {wav}")
            # the beat's words over it, made first, so the part its voice speaks of is checked to be clear of them
            words, band = None, 0.0
            if (seg.get("caption") or "").strip() or seg.get("title"):
                words = d / "words.png"
                ow = 1920
                oh = round(spec["height"] * ow / spec["width"] / 2) * 2
                made = subprocess.run([a.python, str(HERE / "compose.py"), "--overlay", str(words), "--width", str(ow), "--height", str(oh),
                                       *(["--caption", seg["caption"]] if seg.get("caption") else []),
                                       *(["--title", seg["title"]] if seg.get("title") else []),
                                       *(["--sub", seg["sub"]] if seg.get("sub") else [])], check=True, capture_output=True, text=True)
                band = json.loads(made.stdout.strip().splitlines()[-1])["band"] / oh
            argv = [a.python, str(HERE / "compose.py"), str(d / "walk"), str(d / "screen.mp4"), "--width", "1920"]
            zoom, plan, at = zoom_for(seg, a.mode), None, None
            if zoom:
                # the voice's marker, and the screen's own length, on compose's clock (from the walk's first frame)
                t0, t1 = compose.span(d / "walk")
                walk_marks = [dict(m, t=m["t"] - t0) for m in compose.load(d / "walk" / "marks.jsonl")]
                check_subject(seg["id"], zoom["box"], walk_marks, band)
                marked = [m["t"] for m in walk_marks if m["label"] == "voice"]
                plan = zoom_plan(t1 - t0, max(0.0, marked[0]) if marked else 0.0, duration(wav) if wav else None)
                at = plan["at"]
                argv += ["--zoom", json.dumps({"box": zoom["box"], "full": plan["full"], "until": plan["until"], "ease": plan["ease"]}),
                         "--length", str(plan["length"])]
            if outside:
                # captured elsewhere (share_beat.mjs, on the Mac): its video and its marks as it wrote them
                shutil.copy(a.share_segment / "screen.mp4", d / "screen.mp4")
                if (a.share_segment / "screen.marks.jsonl").exists():
                    shutil.copy(a.share_segment / "screen.marks.jsonl", d / "screen.marks.jsonl")
            else:
                subprocess.run(argv, check=True, timeout=1800)
            length = fit(d / "screen.mp4", wav, d / "segment.mp4", at=at if at is not None else voice_start(d / "screen.mp4", seg),
                         overlay=words, blur=seg.get("blur", 0))
            start = sum(t["length"] for t in timing)
            timing.append({"id": seg["id"], "beat": seg["beat"], **({"caption": seg["caption"]} if seg.get("caption") else {}), "start": round(start, 2), "length": round(length, 2),
                           "screen": round(duration(d / "screen.mp4"), 2), "voice": round(duration(wav), 2) if wav else None,
                           **({"zoom": {"box": zoom["box"], "full": plan["full"], "until": plan["until"]}} if zoom else {}),
                           **({"external": "captured" if outside else "placeholder"} if seg.get("external") else {})})
            parts.append(d / "segment.mp4")
            print(f"{seg['id']}: {length:.1f} s")
    finally:
        proc.terminate()
        proc.wait(timeout=10)
    if not parts:
        raise SystemExit("no segment was captured")
    inputs = sum((["-i", str(p)] for p in parts), [])
    chain = "".join(f"[{k}:v][{k}:a]" for k in range(len(parts))) + f"concat=n={len(parts)}:v=1:a=1[v][a]"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", *inputs, "-filter_complex", chain, "-map", "[v]", "-map", "[a]",
                    "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p", "-r", "30", "-c:a", "aac", "-b:a", "160k",
                    "-movflags", "+faststart", str(out / "cut.mp4")], check=True)
    (out / "cut.timing.json").write_text(json.dumps({"mode": a.mode, "voice": a.voice or "none", "length": round(duration(out / "cut.mp4"), 2),
                                                      "segments": timing}, indent=1) + "\n")
    subprocess.run([a.python, str(HERE / "contact.py"), "--video", str(out / "cut.mp4"), str(out / "sheet.png"), "--every", "4",
                    "--cols", "4", "--width", "480"], check=True)
    print(f"cut: {out / 'cut.mp4'} ({duration(out / 'cut.mp4'):.1f} s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
