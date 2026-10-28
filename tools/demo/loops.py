#!/usr/bin/env python3
"""The onboarding loops: three short animations, each 5 to 8 seconds, seamless, with no voice and one caption line,
made with the demo's capture kit (walk.mjs, term.html, compose.py) on rb's real screens.

    python tools/demo/loops.py --record <study folder holding .rb/> --import-cast <import.cast> --rb-bin <dir holding rb> \
        --study-bin <dir holding the study's python> --out <dir> [--work <dir>] [--only import,panes,share]

- import: the import's cast, a time-lapse labelled with its speed, the agent working and rb's replies in its terminal.
- panes: the three panes (rb open with RB_TEST_SHELL=1 until the switch, rb open as it is after): a click on a
  finding, and the inspector showing its runs and Reproduce.
- share: rb sync --watch in a terminal beside rb open's live page; a new run of the study's next sweep is run for
  real and recorded, the page shows it, and the watch sends it. The watch sends to a local stand-in that takes the
  upload and answers with a link (there is no hub here); its address is shown as it is.

Each loop is captured for real, cut to its window, and closed on itself: its last half second fades into its first
frame, so it plays round with no jump. Out, per loop: <name>.webm (VP9) and <name>.mp4 (H.264) at 1280 wide,
<name>.gif at 800 wide under 3 MB, <name>.png (a poster frame), and <name>/ (the capture). The record is copied into
--work before anything opens it, as cut.py does.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
GIF_MAX = 3_000_000  # under 3 MB, read either way
FADE = 0.5


def _tool(name):
    spec = importlib.util.spec_from_file_location(f"demo_{name}", HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def duration(path: Path) -> float:
    return _tool("cut").duration(path)


def ff(*args: str) -> None:
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", *args], check=True)


def seamless(src: Path, start: float, length: float, out: Path, fade: float = FADE) -> None:
    """`length` seconds of `src` from `start`, closed on itself: the window runs from start + fade, and its last `fade`
    seconds dissolve into the first `fade` seconds of the capture, so its last frame is its first and it loops."""
    if length <= 2 * fade:
        raise SystemExit(f"a loop of {length} s is too short for its {fade} s seam")
    if start + length + fade > duration(src) + 0.05:
        raise SystemExit(f"{src} is {duration(src):.2f} s: a loop from {start} s for {length} s needs {start + length + fade:.2f} s")
    ff("-ss", f"{start:.3f}", "-t", f"{length + fade:.3f}", "-i", str(src), "-filter_complex",
       f"[0:v]fps=30,split[a][b];[a]trim=start={fade},setpts=PTS-STARTPTS[main];"
       f"[b]trim=end={fade},setpts=PTS-STARTPTS[head];"
       f"[main][head]xfade=transition=fade:duration={fade}:offset={length - fade:.3f},format=yuv420p[v]",
       "-map", "[v]", "-an", "-c:v", "libx264", "-crf", "16", "-pix_fmt", "yuv420p", str(out))


def caption_band(text: str, width: int, out: Path) -> int:
    """One caption line on a dark band `width` wide, laid under the screen so it covers nothing rb shows: its height."""
    from PIL import Image, ImageDraw, ImageFont

    size = max(14, round(width / 50))
    font = None
    for name in ("DejaVuSans-Bold.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "Arial Bold.ttf"):
        try:
            font = ImageFont.truetype(name, size)
            break
        except OSError:
            continue
    font = font or ImageFont.load_default()
    probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    l, t, r, b = probe.textbbox((0, 0), text, font=font)
    if r - l > width * 0.94:
        raise SystemExit(f"the caption is wider than the loop: {text!r}")
    band = round(size * 2.6 / 2) * 2
    img = Image.new("RGB", (width, band), (18, 20, 26))
    ImageDraw.Draw(img).text(((width - (r - l)) // 2 - l, (band - (b - t)) // 2 - t), text, font=font, fill=(244, 244, 246))
    img.save(out)
    return band


def master(loop: Path, caption: str | None, out: Path, width: int = 1280) -> None:
    """The loop at `width`, with its caption line on a band under it."""
    w = width
    probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                            "-of", "json", str(loop)], capture_output=True, text=True, check=True).stdout
    s = json.loads(probe)["streams"][0]
    h = round(s["height"] * w / s["width"] / 2) * 2
    if caption:
        png = out.with_suffix(".caption.png")
        band = caption_band(caption, w, png)
        ff("-i", str(loop), "-loop", "1", "-i", str(png), "-filter_complex",
           f"[0:v]scale={w}:{h}:flags=lanczos,pad={w}:{h + band}:0:0:0x12141a[s];[s][1:v]overlay=0:{h}:shortest=1,format=yuv420p[v]",
           "-map", "[v]", "-c:v", "libx264", "-crf", "16", "-pix_fmt", "yuv420p", str(out))
        png.unlink()
    else:
        ff("-i", str(loop), "-vf", f"scale={w}:{h}:flags=lanczos", "-c:v", "libx264", "-crf", "16", "-pix_fmt", "yuv420p", str(out))


def gif(src: Path, out: Path, width: int = 800, limit: int = GIF_MAX) -> dict:
    """A GIF of `src` at `width`, under `limit` bytes: fewer frames a second, then fewer colours, until it fits."""
    for fps, colors in ((15, 256), (12, 256), (12, 128), (10, 128), (10, 64), (8, 64), (8, 32)):
        ff("-i", str(src), "-filter_complex",
           f"[0:v]fps={fps},scale={width}:-2:flags=lanczos,split[a][b];[a]palettegen=max_colors={colors}:stats_mode=diff[p];"
           f"[b][p]paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle", "-loop", "0", str(out))
        if out.stat().st_size < limit:
            return {"fps": fps, "colors": colors}
    raise SystemExit(f"{out} is {out.stat().st_size} bytes even at 8 fps and 32 colours: shorten the loop")


def render(loop: Path, caption: str | None, poster_at: float, out_dir: Path, name: str) -> dict:
    """Every file one loop is published as: <name>.mp4, .webm, .gif and .png, with what each is."""
    out_dir.mkdir(parents=True, exist_ok=True)
    m = out_dir / f"{name}.master.mp4"
    master(loop, caption, m)
    mp4, webm, g, png = (out_dir / f"{name}.{x}" for x in ("mp4", "webm", "gif", "png"))
    ff("-i", str(m), "-c:v", "libx264", "-crf", "20", "-preset", "slow", "-pix_fmt", "yuv420p", "-r", "30",
       "-movflags", "+faststart", "-an", str(mp4))
    ff("-i", str(m), "-c:v", "libvpx-vp9", "-crf", "33", "-b:v", "0", "-row-mt", "1", "-pix_fmt", "yuv420p", "-r", "30",
       "-an", str(webm))
    got = gif(m, g)
    ff("-ss", f"{min(poster_at, duration(m) - 0.05):.3f}", "-i", str(m), "-frames:v", "1", str(png))
    m.unlink()
    return {"name": name, "seconds": round(duration(mp4), 2), "caption": caption,
            "files": {p.name: p.stat().st_size for p in (webm, mp4, g, png)}, "gif": got}


# ---------------------------------------------------------------- the three loops

LOOPS = {
    "import": {
        "caption": "The project is recorded with rb's own commands.",
        # the cast's stretch where the agent writes the questions, settings, criteria and runs
        "cast": {"from": 104, "to": 200, "speed": 12, "gap": 2},
        "window": 6.5,
    },
    "panes": {
        "caption": "Click a finding: the runs it rests on, and how to run them again.",
        "window": 8.0,
        # on the demo record: the question view, c-pt-1024's row, and its experiment e-ptq, whose Reproduce the
        # inquiry's rail links to
        "view": "q-q-calib",
        "finding": "c-c-pt-1024",
        "experiment": "x-e-ptq",
    },
    "share": {
        "caption": "A new run lands, is recorded, and rb sync --watch sends it",
        "window": 7.5,
        "page": (1100, 860),
        "view": "q-q-scheme",
        "term_view": (900, 860),
        "term": (96, 34),
        "font": 14,
        "limit": 200,
        # each command, what shows once it has done its part, and how long that may take
        "commands": [
            ("rb sync --watch &", r"Synced at .*privately", 30),
            ("rb journal run -- python sweep.py configs/sweep.yaml --only seed=1 --only calib_images=128 --threads 8",
             r"Next: rb reconcile.*int8-cifar\$", 240),
            ("r=results/r032_int8_pc_c128_s1; rb run add e-ptq --from $r/metrics.json --config $r/config.yaml"
             " --set seed=1 --set scheme=per_channel --set calib_images=128 --set calib_seed=0",
             r"c-pc-128 criterion undetermined", 60),
        ],
        "sent_within": 120,
        "hold": 1.2,
    },
}


def capture(steps: list, out: Path, files: Path | None, python: str, width: int = 1440, height: int = 900) -> Path:
    """One walk of `steps`, composed at 30 fps: the video's path, its marks beside it."""
    out.mkdir(parents=True, exist_ok=True)
    beats = {"width": width, "height": height, "scale": 2, "beats": [{"name": out.name, "steps": steps}]}
    (out / "beats.json").write_text(json.dumps(beats, indent=1))
    argv = ["node", str(HERE / "walk.mjs"), "--script", str(out / "beats.json"), "--out", str(out / "walk")]
    if files:
        argv += ["--serve", str(files)]
    subprocess.run(argv, check=True, timeout=900)
    subprocess.run([python, str(HERE / "compose.py"), str(out / "walk"), str(out / "screen.mp4"), "--width", "1920"],
                   check=True, timeout=1800)
    return out / "screen.mp4"


def marks(video: Path) -> dict:
    p = video.with_suffix(".marks.jsonl")
    return {m["label"]: m["t"] for m in map(json.loads, p.read_text().splitlines())} if p.exists() else {}


def loop_import(a, out: Path) -> dict:
    spec = LOOPS["import"]
    files = out / "import" / "files"
    files.mkdir(parents=True, exist_ok=True)
    shutil.copy(a.import_cast, files / "import.cast")
    c = spec["cast"]
    steps = [{"goto": f"/demo/term.html?cast=/files/import.cast&from={c['from']}&to={c['to']}&speed={c['speed']}"
                      f"&gap={c['gap']}&font=16&mask=ids&title=int8-cifar"},
             {"wait": 300}, {"mark": "start"}, {"play": True}, {"mark": "end"}]
    video = capture(steps, out / "import", files, a.python)
    t = marks(video)
    loop = out / "import" / "loop.mp4"
    seamless(video, t["start"] + 0.2, spec["window"], loop)
    return render(loop, spec["caption"], spec["window"] / 2, out, "import")


def start_open(a, out: Path, name: str):
    """A fresh copy of the record under --work (out/work by default) and rb open on it, in a clean terminal, with the
    test switch (RB_TEST_SHELL=1) only for an rb whose own `/` is not yet the shell: `--shell auto` starts rb open as
    the researcher does and asks for the switch only when its `/` draws no shell (no navigator): (folder, process,
    address)."""
    cut = _tool("cut")
    work = (a.work.resolve() if a.work else out / "work") / name / a.record.resolve().name
    cut.fresh_copy(a.record, work)
    proc, url = cut.start_open(work, a.rb_bin, a.shell == "on")
    try:
        shell = a.shell != "auto" or draws_shell(url)
    except Exception:
        proc.terminate()  # rb open answered no page: it is stopped, not left serving
        proc.wait(timeout=10)
        raise
    if not shell:
        proc.terminate()
        proc.wait(timeout=10)
        proc, url = cut.start_open(work, a.rb_bin, True)
    return work, proc, url


def draws_shell(url: str) -> bool:
    """rb open's `/` is the shell: its page holds the navigator."""
    with urllib.request.urlopen(url, timeout=10) as r:
        return 'id="nav"' in r.read().decode(errors="replace")


def readable(css: str) -> str:
    """A page expression: the part `css` names draws its words on lines, not in a column a few characters wide. Each of
    its runs of text longer than 12 characters has a line wider than 60 px (measured on the text's own line boxes: a grid
    can draw an element's bare text in a narrow cell of a wide element)."""
    return (f"(e => {{ if (!e) return false; const w = document.createTreeWalker(e, NodeFilter.SHOW_TEXT); const r = document.createRange();"
            f" for (let n = w.nextNode(); n; n = w.nextNode()) {{ if (n.textContent.trim().length <= 12) continue; r.selectNodeContents(n);"
            f" if (![...r.getClientRects()].some(b => b.width > 60)) return false; }} return true; }})(document.querySelector({json.dumps(css)}))")


def loop_panes(a, out: Path) -> dict:
    spec = LOOPS["panes"]
    work, proc, url = start_open(a, out, "panes")
    view, finding, exp = spec["view"], spec["finding"], spec["experiment"]
    card = lambda id_: (f"[...document.querySelectorAll('#insp .card')].some(c => c.offsetParent && "
                        f"(c.dataset.id === {json.dumps(id_)} || c.id === {json.dumps(id_)}))")
    try:
        row = f'#{view} li[data-go="{finding}"] a.stm'
        steps = [{"goto": f"{url}#{view}"}, {"waitFor": "document.querySelector('#insp') && document.querySelector('#nav')"},
                 # the row where the click will put it, so the loop's first frame is its last
                 {"eval": f"document.querySelector({json.dumps(row)}).scrollIntoView({{block: 'center'}})"},
                 {"wait": 500}, {"mark": "start"}, {"wait": 200},
                 # the finding's row in the working view: its card opens in the inspector
                 {"click": row, "ms": 200, "settle": 250},
                 {"waitFor": card(finding), "timeout": 8000}, {"wait": 600},
                 # the runs it rests on; then, from the inquiry's rail, how to run them again: the experiment's Reproduce
                 {"click": f'#insp a[href="#{finding}:runs"]', "ms": 200, "settle": 250}, {"wait": 700},
                 {"key": "Escape"}, {"wait": 300},
                 {"click": f'#insp a[href="#{exp}:reproduce"]', "ms": 200, "settle": 250},
                 {"waitFor": f"!!document.querySelector('#insp [data-id=\"{exp}:reproduce\"]')", "timeout": 8000},
                 {"wheelTo": f'#insp [data-id="{exp}:reproduce"]', "top": 150, "ms": 250},
                 {"mark": "reproduce"}, {"waitFor": readable(f'#insp [data-id="{exp}:reproduce"]'), "timeout": 1500},
                 {"wait": 1400}, {"mark": "end"}]
        try:
            video = capture(steps, out / "panes", None, a.python)
        except subprocess.CalledProcessError:
            said = (out / "panes" / "walk" / "marks.jsonl").read_text()
            if '"reproduce"' in said:
                raise SystemExit(f"the Reproduce part is drawn too narrow to read (its words a few characters to a "
                                 f"line): no loop is made of it; see {out / 'panes' / 'walk' / 'frames'}")
            raise SystemExit(f"the panes walk failed: {out / 'panes' / 'walk'}; the inspector's cards (the finding's, and the experiment's Reproduce) "
                             f"must be in this build of rb open")
    finally:
        proc.terminate()
        proc.wait(timeout=10)
    t = marks(video)
    length = min(spec["window"], t["end"] - t["start"] - FADE)
    if length < 5:
        raise SystemExit(f"the panes walk lasted {length:.1f} s: a loop is 5 to 8 s")
    loop = out / "panes" / "loop.mp4"
    seamless(video, t["start"], length, loop)
    return render(loop, spec["caption"], length * 0.75, out, "panes")


class _Hub:
    """A local stand-in for the hub, so rb sync --watch has somewhere to send: it answers who the credential is and
    takes each upload, answering with a link on 127.0.0.1. It keeps what came (when, and its bytes' length)."""

    def __init__(self):
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        got = self.got = []

        class H(BaseHTTPRequestHandler):
            def _json(self, code, obj):
                data = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                origin = f"http://127.0.0.1:{self.server.server_address[1]}"
                if self.path == "/api/hub/whoami":
                    return self._json(200, {"handle": "ana", "base": f"{origin}/u/ana", "publish": f"{origin}/api/hub/investigations"})
                self._json(404, {"error": "not here"})

            def do_PUT(self):
                import io
                import zipfile
                body = self.rfile.read(int(self.headers.get("content-length", "0")))
                with zipfile.ZipFile(io.BytesIO(body)) as z:
                    man = json.loads(z.read(next(n for n in z.namelist() if n.endswith("manifest.json"))))
                got.append({"bytes": len(body), "project": man["project"]["id"]})
                self._json(201, {"url": f"http://127.0.0.1:{self.server.server_address[1]}/u/ana/{man['project']['id']}"})

            def log_message(self, *x):
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()


def play_share(a, work: Path, cast_path: Path, hub: _Hub, ready) -> float:
    """The terminal beside the page, typed for real: rb sync --watch, the next sweep's first cell run under rb's watch,
    and the run recorded; it ends once the watch has sent the change. The wall time the cast began."""
    import time

    take = _tool("import_take")
    spec = LOOPS["share"]
    env = take.clean_env(a.rb_bin, a.study_bin, str(Path.home()))
    env.update({"PS1": f"{work.name}$ ", "RB_HUB_URL": hub.url, "RB_HUB_TOKEN": "rb_demo_local",
                "no_proxy": "127.0.0.1,localhost"})
    prompt = re.escape(env["PS1"].strip())
    cols, rows = spec["term"]
    ready()
    wall0 = time.time()
    cast = take.Cast(cast_path, cols, rows, work.name)
    s = take.Session(work, env, cast, cols, rows, None)
    try:
        if not s.wait_for(prompt, 10):
            raise SystemExit("bash showed no prompt")
        s.pump(0.8)
        for cmd, until, limit in spec["commands"]:
            cast.mark(f"cmd {cmd}")
            s.type(cmd + "\r")
            if not s.wait_for(until, limit):
                raise SystemExit(f"{cmd!r}: no {until!r} within {limit} s:\n{s.text()}")
            s.pump(1.2)
        # the watch sends the change at its next look, a minute at most after its last upload
        end = time.monotonic() + spec["sent_within"]
        while len(hub.got) < 2 and time.monotonic() < end:
            s.pump(0.5)
        if len(hub.got) < 2:
            raise SystemExit(f"rb sync --watch did not send the change within {spec['sent_within']} s:\n{s.text()}")
        s.pump(2.5)
        cast.mark("end")
        s.send("kill %1\r")
        s.pump(1.0)
    finally:
        cast.close()
        s.child.close(force=True)
    return wall0


def loop_share(a, out: Path) -> dict:
    import threading
    import time

    spec = LOOPS["share"]
    d = out / "share"
    d.mkdir(parents=True, exist_ok=True)
    work, proc, url = start_open(a, out, "share")
    hub = _Hub()
    page_walk = d / "page"
    steps = [{"goto": f"{url}#{spec['view']}"}, {"waitFor": "document.querySelector('#nav')"}, {"wait": 1000}, {"mark": "ready"},
             {"wait": spec["limit"] * 1000}]
    # the page is captured for `limit` seconds, more than the terminal takes; the composite keeps the terminal's span
    (d / "page").mkdir(parents=True, exist_ok=True)
    beats = {"width": spec["page"][0], "height": spec["page"][1], "scale": 2, "beats": [{"name": "page", "steps": steps}]}
    (d / "page.json").write_text(json.dumps(beats, indent=1))
    walk = subprocess.Popen(["node", str(HERE / "walk.mjs"), "--script", str(d / "page.json"), "--out", str(page_walk / "walk")])
    seen = {}

    def ready():
        # the terminal starts once the page is up; the walk's clock is read from its "ready" mark as it lands
        end = time.time() + 120
        while time.time() < end:
            for line in (page_walk / "walk" / "marks.jsonl").read_text().splitlines() if (page_walk / "walk" / "marks.jsonl").exists() else []:
                m = json.loads(line)
                if m["label"] == "ready":
                    seen["t0"] = time.time() - m["t"]
                    return
            if walk.poll() is not None:
                raise SystemExit(f"the page's walk ended ({walk.returncode}) before it was ready")
            time.sleep(0.05)
        raise SystemExit("rb open's page never got ready")

    try:
        wall0 = play_share(a, work, d / "share.cast", hub, ready)
        # the terminal's span ends at its "end" marker, once the watch has said it sent the change
        cast_len = next(e[0] for e in map(json.loads, (d / "share.cast").read_text().splitlines()[1:]) if e[1] == "m" and e[2] == "end")
        time.sleep(1.5)
    finally:
        try:
            walk.wait(timeout=spec["limit"] + 120)
        except subprocess.TimeoutExpired:
            walk.kill()
        proc.terminate()
        proc.wait(timeout=10)
        hub.close()
    if len(hub.got) < 2:
        raise SystemExit(f"rb sync --watch sent {len(hub.got)} times; the loop needs the first sync and the one after the run")
    (d / "files").mkdir(exist_ok=True)
    shutil.copy(d / "share.cast", d / "files" / "share.cast")
    tw, th = spec["term_view"]
    term = capture([{"goto": f"/demo/term.html?cast=/files/share.cast&font={spec['font']}&title={work.name}"}, {"wait": 300},
                    {"mark": "start"}, {"play": True}, {"wait": 500}], d / "term", d / "files", a.python, tw, th)
    subprocess.run([a.python, str(HERE / "compose.py"), str(page_walk / "walk"), str(d / "page.mp4"), "--width", str(spec["page"][0] * 2),
                    "--from", "0"], check=True, timeout=1800)
    # one clock for both: the wall time. The cast's time c is the terminal video's start mark plus c; the page video's
    # time is the wall time less when its walk began
    c0 = 0.3
    span = cast_len - c0
    left_at = marks(term)["start"] + c0
    right_at = wall0 + c0 - seen["t0"]
    # the last screen, the watch's second sync beside the page as it now reads, is held before the loop closes
    speed = span / (spec["window"] + FADE - spec["hold"])
    both = d / "both.mp4"
    ff("-ss", f"{left_at:.3f}", "-t", f"{span:.3f}", "-i", str(term), "-ss", f"{right_at:.3f}", "-t", f"{span:.3f}", "-i", str(d / "page.mp4"),
       "-filter_complex", "[0:v]fps=30,setpts=PTS-STARTPTS[l];[1:v]fps=30,setpts=PTS-STARTPTS[r];"
       "[l][r]scale2ref=oh*mdar:ih[l2][r2];[l2][r2]hstack=inputs=2,scale=trunc(iw/2)*2:trunc(ih/2)*2,"
       f"setpts=PTS/{speed:.4f},fps=30,tpad=stop_mode=clone:stop_duration={spec['hold']}[v]", "-map", "[v]", "-an", "-c:v", "libx264", "-crf", "16", "-pix_fmt", "yuv420p", str(both))
    loop = d / "loop.mp4"
    seamless(both, 0.0, min(spec["window"], duration(both) - FADE - 0.05), loop)
    caption = f"{spec['caption']} · time-lapse {round(speed)}×"
    made = render(loop, caption, spec["window"] * 0.8, out, "share")
    made.update({"speed": round(speed, 2), "real_seconds": round(span, 1), "uploads": hub.got})
    return made


def keep(out: Path, made: list) -> None:
    """loops.json says every loop in `out`: the ones made now replace their earlier entries, the rest stay."""
    path = out / "loops.json"
    was = json.loads(path.read_text()) if path.exists() else []
    now = {m["name"]: m for m in made}
    kept = [now.pop(m["name"], m) for m in was] + list(now.values())
    path.write_text(json.dumps(kept, indent=1) + "\n")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--record", type=Path)
    ap.add_argument("--import-cast", type=Path)
    ap.add_argument("--rb-bin")
    ap.add_argument("--study-bin")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--work", type=Path)
    ap.add_argument("--only")
    ap.add_argument("--shell", choices=["auto", "on", "off"], default="auto",
                    help="rb open with RB_TEST_SHELL=1 (auto: only when rb open's own / draws no shell, an rb from before the switch)")
    ap.add_argument("--python", default=sys.executable, help="the Python with Pillow that runs compose.py")
    a = ap.parse_args(argv)
    only = a.only.split(",") if a.only else list(LOOPS)
    if set(only) - set(LOOPS):
        raise SystemExit(f"--only names no loop: {', '.join(sorted(set(only) - set(LOOPS)))} (the loops: {', '.join(LOOPS)})")
    need = {"import": ["import_cast"], "panes": ["record", "rb_bin"], "share": ["record", "rb_bin", "study_bin"]}
    for name in only:
        missing = [f"--{k.replace('_', '-')}" for k in need[name] if getattr(a, k) is None]
        if missing:
            raise SystemExit(f"the {name} loop needs {', '.join(missing)}")
    out = a.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    made = []
    for name in only:
        made.append({"import": loop_import, "panes": loop_panes, "share": loop_share}[name](a, out))
        print(f"{name}: {made[-1]['seconds']} s, " + ", ".join(f"{k} {v} bytes" for k, v in made[-1]["files"].items()))
    keep(out, made)
    return 0


if __name__ == "__main__":
    sys.exit(main())
