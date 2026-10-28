"""The int8-cifar study the demo brings into rb. tools/demo/make_study.py builds the repository
an agent leaves after two weeks of one real study; here it is built at its tiny configuration (random images, a short
schedule) and each item the demo plan lists is checked in what it left: the code, the sweep's SQLite database, 30 to 40 run
folders with the grid the study needs, the crashed run and its rerun, notes.md's past decision, the dated history
with the mid-study normalization change that later runs read and earlier ones did not, the agent's long log, and
numbers that are the code's own output. Every name and number is the study's; the researcher is made up.

    python tests/test_demo_record.py /srv/rb/demo/int8-cifar      # the same checks on the full build"""
import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

import yaml

MAKE_STUDY = Path(__file__).resolve().parents[1] / "tools" / "demo" / "make_study.py"
CALIBRATION = {128, 512, 1024, 2048}
SEEDS = {0, 1, 2}
CODE = ["train.py", "quantize.py", "eval.py", "sweep.py", "configs/train.yaml", "configs/data.yaml", "configs/quant.yaml",
        "configs/eval.yaml", "configs/sweep.yaml", "requirements.txt", "README.md", "notes.md"]


def git(root, *args):
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=True).stdout


def when(text):
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def runs(root):
    """Each run folder: its name, the config it resolved, its metrics (None when it did not finish), its log."""
    out = []
    for folder in sorted((root / "results").glob("r*_*")):
        if folder.is_dir():
            metrics = folder / "metrics.json"
            log = next(folder.glob("*.log"))
            out.append({"name": folder.name, "folder": folder, "config": yaml.safe_load((folder / "config.yaml").read_text()),
                        "metrics": json.loads(metrics.read_text()) if metrics.exists() else None, "log": log.read_text()})
    return out


def head_yaml(root, rel):
    return yaml.safe_load((root / rel).read_text())


def normalization_change(root):
    """The commit that changed configs/data.yaml's normalization, with the values before and after."""
    commits = git(root, "log", "--reverse", "--format=%H %aI", "--", "configs/data.yaml").split("\n")
    previous = None
    for sha, date in (c.split() for c in commits if c):
        now = yaml.safe_load(git(root, "show", f"{sha}:configs/data.yaml"))["normalize"]
        if previous is not None and now != previous:
            return {"sha": sha, "date": when(date), "before": previous, "after": now}
        previous = now
    raise AssertionError("no commit changes configs/data.yaml's normalization")


def check_code(root):
    tracked = set(git(root, "ls-files").split())
    missing = [p for p in CODE if p not in tracked]
    assert not missing, f"not committed: {missing}"
    assert re.search(r"^torch==\d", (root / "requirements.txt").read_text(), re.M)
    readme = (root / "README.md").read_text()
    assert "keep a ResNet-20 within 1 point of FP32 top-1 on CIFAR-10?" in readme and "91.25%" in readme, "the question and the reference"
    left = git(root, "status", "--porcelain").splitlines()
    assert left == [" M configs/sweep.yaml"], f"the tree as left: {left}"
    planned = head_yaml(root, "configs/sweep.yaml")
    ran = {r["config"]["from"] for r in runs(root)
           if r["config"]["kind"] == "quantize" and r["config"]["quant"]["scheme"] in planned["grid"]["scheme"]}
    assert not set(planned["from"].values()) & ran, "the sweep it set up last is not run"
    for run in runs(root):
        script = run["config"]["command"].split()[1]
        assert script in ("train.py", "quantize.py") and (root / script).exists(), (run["name"], run["config"]["command"])


def check_grid(root):
    every = runs(root)
    assert 30 <= len(every) <= 40, f"{len(every)} run folders"
    main_epochs = head_yaml(root, "configs/train.yaml")["epochs"]
    final_norm = head_yaml(root, "configs/data.yaml")["normalize"]
    fp32 = {}
    for r in every:
        c = r["config"]
        if c["kind"] == "train" and r["metrics"] and c["train"]["epochs"] == main_epochs and c["data"]["normalize"] == final_norm:
            assert c["seed"] not in fp32, f"two finished FP32 runs on seed {c['seed']}"
            fp32[c["seed"]] = f"results/{r['name']}"
    assert set(fp32) == SEEDS, f"FP32 at the final settings on seeds {sorted(fp32)}"

    def int8(scheme, sources):
        cells = {}
        for r in every:
            c = r["config"]
            if (c["kind"] == "quantize" and r["metrics"] and c["quant"]["scheme"] == scheme and c["from"] in sources
                    and c["quant"]["calibration"]["subset_seed"] == 0):
                cells.setdefault((c["seed"], c["quant"]["calibration"]["images"]), []).append(r["name"])
        return cells

    per_tensor = int8("per_tensor", set(fp32.values()))
    assert set(per_tensor) == {(s, n) for s in SEEDS for n in CALIBRATION}, sorted(per_tensor)
    assert all(len(v) == 1 for v in per_tensor.values()), per_tensor
    channel_sources = {r["config"]["from"] for r in every if r["config"]["kind"] == "quantize"
                       and r["config"]["quant"]["scheme"] == "per_channel"}
    assert len(channel_sources) == 1, f"per-channel runs start from {sorted(channel_sources)}: one seed, one model"
    source = next(iter(channel_sources))
    per_channel, against = int8("per_channel", {source}), int8("per_tensor", {source})
    assert {n for _, n in per_channel} == CALIBRATION and {n for _, n in against} == CALIBRATION, (per_channel, against)
    assert len({s for s, _ in per_channel}) == 1
    return fp32, source


def check_database(root):
    db = root / "results" / "runs.db"
    assert db.read_bytes()[:16] == b"SQLite format 3\x00"
    assert "results/runs.db" in git(root, "ls-files") and not git(root, "status", "--porcelain", "results/runs.db")
    assert not (root / "results" / "runs.db-wal").exists() and not (root / "results" / "runs.db-journal").exists()
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute("SELECT * FROM runs")]
    con.close()
    assert len(rows) >= 20, f"{len(rows)} rows"
    by_name = {r["name"]: r for r in runs(root)}
    for row in rows:
        assert row["status"] in ("done", "crashed"), row
        run = by_name[Path(row["folder"]).name]
        assert row["seed"] == run["config"]["seed"] and row["command"] == run["config"]["command"], row
        assert row["started_at"] <= row["finished_at"]
        if row["status"] == "done":
            m = run["metrics"]
            assert row["fp32_top1"] == m["fp32"]["top1"] and row["int8_top1"] == m.get("int8", {}).get("top1"), (row, m)
        if row["stage"] == "quantize":
            q = run["config"]["quant"]
            assert (row["scheme"], row["calib_images"], row["calib_seed"]) == (q["scheme"], q["calibration"]["images"],
                                                                               q["calibration"]["subset_seed"]), row
    return rows


def check_crash(root):
    unfinished = [r for r in runs(root) if r["metrics"] is None]
    assert len(unfinished) == 1, [r["name"] for r in unfinished]
    crash = unfinished[0]
    assert crash["config"]["kind"] == "train"
    epochs_done = re.findall(r"epoch (\d+)/(\d+) done", crash["log"])
    assert epochs_done and int(epochs_done[-1][0]) < int(epochs_done[-1][1]), "its log stops partway"
    rows = {r["run_id"]: r for r in check_database(root)}
    row = rows[crash["name"]]
    assert row["status"] == "crashed" and row["exit_code"] == -9, row
    reruns = [r for r in rows.values() if r["rerun_of"] == crash["name"]]
    assert len(reruns) == 1 and reruns[0]["status"] == "done" and reruns[0]["seed"] == row["seed"], reruns
    assert reruns[0]["started_at"] > row["finished_at"]
    return crash["name"], reruns[0]["run_id"]


def check_notes(root):
    notes = (root / "notes.md").read_text()
    decisions = re.findall(r"^(\d{4}-\d{2}-\d{2}): Decision: (.+)$", notes, re.M)
    assert len(decisions) == 1, decisions
    day, words = decisions[0]
    added = git(root, "log", "--reverse", "--format=%ad", "--date=short", "-S", "Decision:", "--", "notes.md").split()
    assert added and added[0] == day, (day, added)
    epochs = int(re.search(r"trains for (\d+) epochs?\b", words).group(1))
    assert epochs == head_yaml(root, "configs/train.yaml")["epochs"]
    assert "Decision:" not in (root / "logs" / "agent.log").read_text(), "the decision is the researcher's own line"


def check_history(root, fp32=None, channel_source=None):
    log = [line.split(" ", 3) for line in git(root, "log", "--reverse", "--format=%H %aI %an %s").splitlines()]
    assert len(log) >= 10
    dates = [when(d) for _, d, _, _ in log]
    assert dates == sorted(dates) and len(set(dates)) == len(dates)
    assert (dates[-1] - dates[0]).days >= 10, f"{dates[0]} to {dates[-1]}"
    assert {who for _, _, who, _ in log} == {"ana"}
    change = normalization_change(root)
    assert change["sha"] in {sha for sha, _, _, _ in log[1:]}, "the change is mid-study"
    before = [r for r in runs(root) if when(r["config"]["started_at"]) < change["date"]]
    after = [r for r in runs(root) if when(r["config"]["started_at"]) > change["date"]]
    assert before and after and len(before) + len(after) == len(runs(root))
    assert all(r["config"]["data"]["normalize"] == change["before"] for r in before), "earlier runs read the old values"
    assert all(r["config"]["data"]["normalize"] == change["after"] for r in after), "later runs read the new ones"
    if fp32:                                     # the grid's FP32 models came after the change, per-channel's before it
        assert {f"results/{r['name']}" for r in after} >= set(fp32.values())
        assert channel_source in {f"results/{r['name']}" for r in before}
    assert all(r["config"]["from"] in {f"results/{b['name']}" for b in before}
               for r in runs(root) if r["config"].get("quant", {}).get("scheme") == "per_channel")
    for r in runs(root):
        added = git(root, "log", "--diff-filter=A", "--format=%aI", "--", f"results/{r['name']}/config.yaml").split()
        assert added, f"{r['name']} is not committed"
        end = (r["metrics"] or {}).get("finished_at") or r["config"]["started_at"]
        assert when(added[-1]) >= when(end), (r["name"], added, end)


def check_agent_log(root, full):
    agent = (root / "logs" / "agent.log").read_text()
    lines = agent.splitlines()
    assert len(lines) >= (2000 if full else 400), f"{len(lines)} lines"
    assert "logs/agent.log" not in git(root, "ls-files")
    assert len(re.findall(r"^\S+ \S+  user ", agent, re.M)) >= 8, "the researcher's instructions"
    printed = {re.sub(r"^\[r\d{3}_[^\]]+\] ", "", line[27:]) for line in lines if not line.startswith("#")}
    for r in runs(root):
        assert f"{'':19}  {'':5} " not in r["log"]
        missing = [row for row in r["log"].splitlines() if row not in printed]
        assert not missing, (r["name"], missing[:3])


def check_numbers(root, fp32, channel_source, python=sys.executable):
    """eval.py gives back, from a run's saved model, the number its metrics.json holds: an FP32 model, a per-tensor
    and a per-channel INT8 model."""
    every = [r for r in runs(root) if r["metrics"]]
    picks = [next(r for r in every if f"results/{r['name']}" == fp32[1])]
    for scheme, source in (("per_tensor", fp32[1]), ("per_channel", channel_source)):
        picks.append(next(r for r in every if r["config"].get("from") == source and r["config"]["quant"]["scheme"] == scheme))
    for r in picks:
        out = subprocess.run([python, "eval.py", f"results/{r['name']}"], cwd=root, capture_output=True, text=True, check=True,
                             env={**os.environ, "OMP_NUM_THREADS": "2"})
        got = json.loads(out.stdout.splitlines()[-1])["metrics"]["top1"]
        want = r["metrics"]["int8" if r["config"]["kind"] == "quantize" else "fp32"]["top1"]
        assert got == want, (r["name"], got, want)


def check_timeline(root):
    first = when(git(root, "log", "--reverse", "--format=%aI").split()[0])
    last = when(git(root, "log", "-1", "--format=%aI").strip())
    for p in [*(root / "results").rglob("*"), root / "notes.md", root / "logs" / "agent.log"]:
        t = datetime.fromtimestamp(p.stat().st_mtime, first.tzinfo)
        assert first - timedelta(days=1) <= t <= last + timedelta(days=1), (p, t)
    for r in runs(root):
        assert first <= when(r["config"]["started_at"]) <= last


def problems(root, full):
    """What the repository at root lacks of what the demo plan lists, one line per item."""
    found = []

    def item(name, check, *args):
        try:
            return check(root, *args)
        except (AssertionError, KeyError, StopIteration, OSError, subprocess.CalledProcessError) as e:
            found.append(f"{name}: {type(e).__name__} {e}")

    item("the code", check_code)
    grid = item("the grid", check_grid)
    item("the sweep's database", check_database)
    item("the crash and its rerun", check_crash)
    item("notes.md's decision", check_notes)
    item("the history and the normalization change", check_history, *(grid or ()))
    item("the agent's log", check_agent_log, full)
    item("the scenario's times", check_timeline)
    if grid:
        item("the numbers", check_numbers, *grid)
    return found


if __name__ == "__main__":
    root = Path(sys.argv[1]).resolve()
    found = problems(root, full="--tiny" not in sys.argv)
    print("\n".join(found) or f"{root}: holds what the demo plan lists")
    sys.exit(1 if found else 0)

import importlib.util  # noqa: E402
import shutil  # noqa: E402
import time  # noqa: E402

import pytest  # noqa: E402

# found, never imported: torch in a pytest worker counts toward the peak memory Linux reports for each rb it starts
_TORCH_MISSING = [m for m in ("torch", "torchvision") if importlib.util.find_spec(m) is None]


@pytest.mark.skipif(bool(_TORCH_MISSING), reason=f"the study runs on PyTorch CPU: {', '.join(_TORCH_MISSING)} not installed")
def test_the_study_holds_what_the_plan_lists(tmp_path):
    """One build (each xdist worker would build its own for a module fixture), then every item the demo plan lists."""
    root = tmp_path / "int8-cifar"
    built = subprocess.run([sys.executable, str(MAKE_STUDY), "--out", str(root), "--tiny", "--threads", "4"],
                           capture_output=True, text=True)
    assert built.returncode == 0, built.stdout[-2000:] + built.stderr[-2000:]
    assert problems(root, full=False) == []


# ---- the demo's kit: the import's driver and the capture (tools/demo/import_take.py, walk.mjs, compose.py)

def _tool(name):
    spec = importlib.util.spec_from_file_location(f"demo_{name}", MAKE_STUDY.parent / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_import_plays_the_researcher_in_a_terminal_no_agent_session_left_its_marks_on(tmp_path, monkeypatch):
    """The take is started from an agent session on the box: the researcher's terminal must carry none of its variables,
    so rb init (and the researcher's later calls) are recorded as the researcher, and the agent it starts is its own."""
    from rbtest import AGENT_VARS, RB_BIN

    take = _tool("import_take")
    for var in AGENT_VARS:
        monkeypatch.setenv(var, "1")
    rb_bin = Path(RB_BIN).parent if RB_BIN else Path(sys.executable).parent
    env = take.clean_env(str(rb_bin), None, str(tmp_path))
    assert not set(env) & set(AGENT_VARS)
    assert "~" not in env["PS1"] and "/" not in env["PS1"], "the prompt claims no folder: a take is not in the home folder"
    assert env["CLAUDE_CODE_BASH_EDIT_DIFF"] == "0", "the agent's screen shows no diff of .rb/log.jsonl, whose lines name the writer"
    repo = tmp_path / "int8-resnet"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "ana@example.org"], cwd=repo, check=True)
    rb = str(rb_bin / "rb") if not RB_BIN else RB_BIN
    done = subprocess.run([rb, "init", "INT8 ResNet-50", "--json"], cwd=repo, env=env, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout)["data"]["actor"]["id"] == "human:ana"


def test_a_take_is_kept_as_an_asciinema_v2_cast(tmp_path):
    take = _tool("import_take")
    cast = take.Cast(tmp_path / "t.cast", 120, 30, "INT8 ResNet-50")
    cast.event("o", "$ rb init\r\n")
    cast.event("i", "y")
    cast.event("o", "")
    cast.mark("brief")
    cast.close()
    lines = [json.loads(l) for l in (tmp_path / "t.cast").read_text().splitlines()]
    assert lines[0]["version"] == 2 and (lines[0]["width"], lines[0]["height"]) == (120, 30)
    assert [(k, d) for _, k, d in lines[1:]] == [("o", "$ rb init\r\n"), ("i", "y"), ("m", "brief")]
    times = [t for t, _, _ in lines[1:]]
    assert times == sorted(times)


@pytest.mark.skipif(shutil.which("ffmpeg") is None or importlib.util.find_spec("PIL") is None, reason="compose.py needs ffmpeg and Pillow")
def test_compose_holds_each_frame_until_the_next_and_draws_the_walks_cursor(tmp_path):
    """Frames come only when the screen changes: each 30 fps frame shows the latest one sent, with the cursor where the
    walk's own mouse events put it, and only there."""
    from PIL import Image

    walk = tmp_path / "walk"
    (walk / "frames").mkdir(parents=True)
    (walk / "walk.json").write_text(json.dumps({"width": 80, "height": 60, "scale": 2, "frames": 2}))
    Image.new("RGB", (160, 120), (0, 0, 0)).save(walk / "frames" / "000001.jpg", quality=95)
    Image.new("RGB", (160, 120), (0, 0, 200)).save(walk / "frames" / "000002.jpg", quality=95)
    (walk / "frames.jsonl").write_text('{"file": "frames/000001.jpg", "t": 0}\n{"file": "frames/000002.jpg", "t": 0.5}\n')
    (walk / "cursor.jsonl").write_text('{"t": 0.2, "x": 20, "y": 20, "down": false}\n')
    (walk / "marks.jsonl").write_text('{"t": 0, "label": "beat a"}\n{"t": 1.0, "label": "end"}\n')
    out = tmp_path / "a.mp4"
    _tool("compose").compose(walk, out, 30, None, None, None)
    probe = subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-show_entries", "stream=width,height,nb_read_frames,r_frame_rate",
                            "-of", "json", str(out)], capture_output=True, text=True, check=True)
    stream = json.loads(probe.stdout)["streams"][0]
    assert (stream["width"], stream["height"], stream["r_frame_rate"], int(stream["nb_read_frames"])) == (160, 120, "30/1", 31)

    def frame(at):
        png = tmp_path / f"{at}.png"
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-ss", str(at), "-i", str(out), "-frames:v", "1", str(png)], check=True)
        return Image.open(png).convert("RGB")

    early, late = frame(0.1), frame(0.8)
    assert max(early.getpixel((100, 100))) < 40, "before the second frame, the first holds"
    assert early.getpixel((44, 56))[0] < 60, "no cursor before the walk moved one"
    assert late.getpixel((100, 100))[2] > 150, "the second frame, held to the end"
    assert min(late.getpixel((44, 56))) > 180, "the cursor, white, below and right of its tip at (40, 40)"
    assert [json.loads(l)["label"] for l in out.with_suffix(".marks.jsonl").read_text().splitlines()] == ["beat a", "end"]


@pytest.mark.skipif(shutil.which("node") is None or shutil.which("google-chrome") is None, reason="walk.mjs needs node and Chrome")
def test_the_walk_clicks_the_words_it_names_not_the_card_around_them(tmp_path):
    """A click by words lands on the smallest element that holds them (the link), at its centre, with a real mouse event,
    and every frame and cursor position is kept on one clock."""
    (tmp_path / "page.html").write_text(
        '<!doctype html><body style="margin:0"><script>addEventListener("load", () => setTimeout(() => '
        'document.body.style.background = "#20242c", 250))</script><div id="card" style="width:1000px;height:600px;border:1px solid #888">'
        '<a href="#ok" style="display:inline-block;margin:500px 0 0 860px">Explore evidence →</a></div></body>')
    (tmp_path / "beats.json").write_text(json.dumps({"width": 1200, "height": 800, "scale": 1, "beats": [
        {"name": "card", "steps": [{"goto": "/files/page.html"}, {"click": {"text": "Explore evidence →"}},
                                   {"waitFor": "location.hash === '#ok'", "timeout": 3000}, {"mark": "shot clicked"}]}]}))
    out = tmp_path / "walk"
    done = subprocess.run(["node", str(MAKE_STUDY.parent / "walk.mjs"), "--script", str(tmp_path / "beats.json"), "--out", str(out),
                           "--serve", str(tmp_path)], capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stdout + done.stderr
    walked = json.loads((out / "walk.json").read_text())
    assert walked["error"] is None and walked["frames"] >= 1
    cursor = [json.loads(l) for l in (out / "cursor.jsonl").read_text().splitlines()]
    pressed = [c for c in cursor if c["down"]]
    assert pressed and 860 < pressed[0]["x"] < 1000 and 500 < pressed[0]["y"] < 540, pressed[:1]
    labels = [json.loads(l)["label"] for l in (out / "marks.jsonl").read_text().splitlines()]
    assert labels == ["beat card", "shot clicked", "end"]
    if importlib.util.find_spec("PIL") is not None:
        from PIL import Image
        first = json.loads((out / "frames.jsonl").read_text().splitlines()[0])["file"]
        assert Image.open(out / first).convert("RGB").getpixel((5, 5)) != (255, 255, 255), "the capture opens on the page, not Chrome's blank one"


@pytest.mark.skipif(importlib.util.find_spec("PIL") is None, reason="contact.py needs Pillow")
def test_the_contact_sheet_shows_the_frame_on_screen_at_each_marker(tmp_path):
    from PIL import Image

    walk = tmp_path / "walk"
    (walk / "frames").mkdir(parents=True)
    for k, colour in enumerate([(200, 0, 0), (0, 200, 0), (0, 0, 200)], 1):
        Image.new("RGB", (64, 40), colour).save(walk / "frames" / f"{k:06d}.png")
    (walk / "frames.jsonl").write_text("".join(json.dumps({"file": f"frames/{k:06d}.png", "t": t}) + "\n" for k, t in [(1, 0), (2, 1), (3, 2)]))
    (walk / "marks.jsonl").write_text('{"t": 0.5, "label": "shot overview"}\n{"t": 1.2, "label": "beat x"}\n{"t": 2.5, "label": "shot finding"}\n')
    contact = _tool("contact")
    assert contact.main([str(walk), str(tmp_path / "sheet.png"), "--cols", "2", "--width", "64"]) == 0
    first, second = Image.open(tmp_path / "sheet-00.png"), Image.open(tmp_path / "sheet-01.png")
    assert first.getpixel((5, 5))[:3] == (200, 0, 0) and second.getpixel((5, 5))[:3] == (0, 0, 200)
    assert not (tmp_path / "sheet-02.png").exists()


@pytest.mark.skipif(any(importlib.util.find_spec(m) is None for m in ("pexpect", "pyte")), reason="the takes need pexpect and pyte")
def test_a_terminal_beat_types_each_command_for_real_and_keeps_its_output(tmp_path):
    repo = tmp_path / "int8-resnet"
    repo.mkdir()
    (repo / "results.txt").write_text("top1 76.13\n")
    cast = tmp_path / "beat.cast"
    _tool("term_take").record(repo, cast, ["cat results.txt", "echo done"], None, 0.2, 80, 24)
    lines = [json.loads(l) for l in cast.read_text().splitlines()]
    out = "".join(d for _, k, d in lines[1:] if k == "o")
    typed = "".join(d for _, k, d in lines[1:] if k == "i")
    assert "top1 76.13" in out and "int8-resnet$ " in out
    assert typed == "cat results.txt\recho done\r"
    assert [d for _, k, d in lines[1:] if k == "m"] == ["cmd cat results.txt", "cmd echo done", "end"]


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="cut.py needs ffmpeg")
def test_a_segment_lasts_as_long_as_its_voice_line_with_its_last_frame_held(tmp_path):
    video, wav = tmp_path / "screen.mp4", tmp_path / "voice.wav"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=160x120:rate=30", "-t", "1", "-pix_fmt", "yuv420p", str(video)], check=True)
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000", "-t", "2", str(wav)], check=True)
    cut = _tool("cut")
    assert cut.fit(video, wav, tmp_path / "long.mp4") == pytest.approx(2.6, abs=0.01)
    assert cut.duration(tmp_path / "long.mp4") == pytest.approx(2.6, abs=0.1)
    assert cut.fit(video, None, tmp_path / "silent.mp4") == pytest.approx(1.0, abs=0.05), "no voice: the screen's own length"
    streams = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "json", str(tmp_path / "silent.mp4")],
                                        capture_output=True, text=True, check=True).stdout)["streams"]
    assert sorted(s["codec_type"] for s in streams) == ["audio", "video"], "a silent segment still carries audio, so segments join"


def _cdnjs_reachable():
    import socket
    try:
        socket.create_connection(("cdnjs.cloudflare.com", 443), timeout=3).close()
        return True
    except OSError:
        return False


@pytest.mark.skipif(shutil.which("node") is None or shutil.which("google-chrome") is None or not _cdnjs_reachable(),
                    reason="the player needs node, Chrome and cdnjs (xterm.js, pinned by its hash)")
def test_the_player_scrolls_a_long_output_at_its_pace_and_shows_its_caption(tmp_path):
    lines = "".join(f"line {k}\r\n" for k in range(60))
    (tmp_path / "beat.cast").write_text(json.dumps({"version": 2, "width": 80, "height": 24}) + "\n" + json.dumps([0.5, "o", lines]) + "\n")
    (tmp_path / "beats.json").write_text(json.dumps({"width": 1000, "height": 700, "scale": 1, "beats": [{"name": "t", "steps": [
        {"goto": "/demo/term.html?cast=/files/beat.cast&pace=40&caption=Which%20results%3F"}, {"play": True},
        {"waitFor": "document.getElementById('caption').style.visibility === 'visible' && window.__length > 1.4"}]}]}))
    done = subprocess.run(["node", str(MAKE_STUDY.parent / "walk.mjs"), "--script", str(tmp_path / "beats.json"), "--out", str(tmp_path / "w"),
                           "--serve", str(tmp_path)], capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stdout + done.stderr
    marks = [json.loads(l) for l in (tmp_path / "w" / "marks.jsonl").read_text().splitlines()]
    assert marks[-1]["t"] - marks[0]["t"] > 1.4, "60 lines at 40 a second take a second and a half, not one frame"


def test_the_cut_copies_the_record_where_it_is_told_keeping_its_name(tmp_path):
    """rb open's place line shows the copy's path, so the cut puts it where the owner asks."""
    cut = _tool("cut")
    record = tmp_path / "takes" / "4" / "int8-resnet"
    assert cut.work_dir(tmp_path / "out", tmp_path / "demo", record) == (tmp_path / "demo" / "int8-resnet").resolve()
    assert cut.work_dir(tmp_path / "out", None, record) == tmp_path / "out" / "work" / "int8-resnet"


def test_a_segment_speaks_the_line_its_mode_shows():
    """rb open as it is has no composer, so its decision beat says what its screen shows; the shell's says what the shell's does."""
    cut = _tool("cut")
    seg = {"voice": {"rb_open_today": "rb gives me the command", "shell": "I choose to investigate"}}
    assert cut.voice_line(seg, "rb_open_today") == "rb gives me the command"
    assert cut.voice_line(seg, "shell") == "I choose to investigate"
    assert cut.voice_line({"voice": "one line"}, "shell") == "one line"
    spec = json.loads((MAKE_STUDY.parent / "cut.json").read_text())
    for s in spec["segments"]:
        for mode in ("rb_open_today", "shell"):
            assert cut.voice_line(s, mode).strip(), (s["id"], mode)


def _end_watch(out: Path, until: Path, changes: int, within: float = 60) -> None:
    """Ends a watch step once the walk has marked `changes` changes, then 2 s on for one it should not have: a fixed
    wait loses the race on a loaded machine, where Chrome is slow to start."""
    marks, deadline = out / "marks.jsonl", time.monotonic() + within
    while time.monotonic() < deadline and not (marks.is_file() and sum('"change"' in l for l in marks.read_text().splitlines()) >= changes):
        time.sleep(0.1)
    time.sleep(2)
    until.write_text("")


def _walk(tmp_path, steps, *extra, timeout=120):
    (tmp_path / "beats.json").write_text(json.dumps({"width": 800, "height": 600, "scale": 1, "beats": [{"name": "t", "steps": steps}]}))
    return subprocess.run(["node", str(MAKE_STUDY.parent / "walk.mjs"), "--script", str(tmp_path / "beats.json"), "--out", str(tmp_path / "w"),
                           "--serve", str(tmp_path / "files"), *extra], capture_output=True, text=True, timeout=timeout)


_NEEDS_CHROME = pytest.mark.skipif(shutil.which("node") is None or shutil.which("google-chrome") is None, reason="walk.mjs needs node and Chrome")


@_NEEDS_CHROME
def test_the_walks_server_answers_inside_its_folders_only_and_survives_a_malformed_address(tmp_path):
    (tmp_path / "files").mkdir()
    (tmp_path / "files" / "page.html").write_text("<!doctype html><body>INT8 ResNet-50</body>")
    (tmp_path / "files-private").mkdir()
    (tmp_path / "files-private" / "x.txt").write_text("not the walk's")
    done = _walk(tmp_path, [{"goto": "/files/page.html"},
                            {"print": "Promise.all(['/files/..%2ffiles-private%2fx.txt', '/files/%E0%A4%A', '/files/page.html']"
                                      ".map(u => fetch(u).then(r => r.status)))"}])
    assert done.returncode == 0, done.stdout + done.stderr
    assert "[404,400,200]" in done.stdout


@_NEEDS_CHROME
def test_a_still_move_leaves_the_page_where_it_is_and_refuses_a_part_off_screen(tmp_path):
    """A zoomed beat frames a part already on screen: its moves keep the page still (a plain move scrolls the part to the
    middle), and a still move to a part off the screen stops the walk rather than film the wrong place."""
    (tmp_path / "files").mkdir()
    (tmp_path / "files" / "p.html").write_text("<!doctype html><body style='margin:0'><p id='a' style='margin:300px 0 0'>INT8 ResNet-50</p>"
                                               "<p id='b' style='margin:2000px 0'>human:ana</p></body>")
    still = _walk(tmp_path, [{"goto": "/files/p.html"}, {"move": "#a", "still": True}, {"print": "scrollY"}])
    assert still.returncode == 0, still.stderr[-600:]
    assert any(l.strip() == "0" for l in still.stdout.splitlines()), still.stdout
    plain = _walk(tmp_path, [{"goto": "/files/p.html"}, {"move": "#a"}, {"print": "scrollY"}])
    assert plain.returncode == 0 and not any(l.strip() == "0" for l in plain.stdout.splitlines()), plain.stdout
    off = _walk(tmp_path, [{"goto": "/files/p.html"}, {"move": "#b", "still": True}])
    assert off.returncode != 0 and "not wholly on screen" in off.stderr, off.stderr[-600:]
    click = _walk(tmp_path, [{"goto": "/files/p.html"}, {"click": "#a", "still": True}, {"print": "scrollY"}])
    assert click.returncode == 0 and any(l.strip() == "0" for l in click.stdout.splitlines()), click.stdout + click.stderr[-600:]
    off = _walk(tmp_path, [{"goto": "/files/p.html"}, {"click": "#b", "still": True}])
    assert off.returncode != 0 and "not wholly on screen" in off.stderr, off.stderr[-600:]


@_NEEDS_CHROME
def test_a_walk_ends_when_a_page_never_answers(tmp_path):
    import socket
    import threading
    (tmp_path / "files").mkdir()
    silent = socket.socket()
    silent.bind(("127.0.0.1", 0))
    silent.listen(4)
    held = []
    threading.Thread(target=lambda: held.append(silent.accept()), daemon=True).start()
    try:
        done = _walk(tmp_path, [{"goto": "/files/none.html"}, {"goto": f"http://127.0.0.1:{silent.getsockname()[1]}/"}], "--timeout", "4", timeout=90)
    finally:
        silent.close()
    assert done.returncode == 1 and "did not answer within 4 s" in done.stderr, done.stderr[-600:]
    assert json.loads((tmp_path / "w" / "walk.json").read_text())["error"]


@_NEEDS_CHROME
def test_a_click_by_words_refuses_two_matches_and_a_covered_target(tmp_path):
    (tmp_path / "files").mkdir()
    (tmp_path / "files" / "two.html").write_text("<!doctype html><body><button>Investigate</button><p>x</p><button>Investigate</button></body>")
    (tmp_path / "files" / "cover.html").write_text(
        '<!doctype html><body><button style="margin:200px">Investigate</button>'
        '<div style="position:fixed;inset:0;background:rgba(0,0,0,.4)"></div></body>')
    two = _walk(tmp_path, [{"goto": "/files/two.html"}, {"click": {"text": "Investigate"}}])
    assert two.returncode == 1 and "2 elements hold these words" in two.stderr, two.stderr[-600:]
    covered = _walk(tmp_path, [{"goto": "/files/cover.html"}, {"click": {"text": "Investigate"}}])
    assert covered.returncode == 1 and "is covered by DIV" in covered.stderr, covered.stderr[-600:]
    still = _walk(tmp_path, [{"goto": "/files/two.html"}, {"wait": 300}])
    assert still.returncode == 0 and (tmp_path / "w" / "cursor.jsonl").read_text() == "", "no cursor in a beat nobody points in"


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="cut.py reads the voice takes with ffprobe")
def test_a_cut_names_what_it_lacks_before_it_captures_anything(tmp_path):
    cut = _tool("cut")
    base = ["--record", str(tmp_path / "missing-record"), "--import-cast", "x", "--pain-cast", "x", "--rb-bin", "x", "--out", str(tmp_path / "out")]
    with pytest.raises(SystemExit, match="--only names no segment: payof"):
        cut.main(base + ["--only", "pitch,payof"])
    (tmp_path / "voice").mkdir()
    (tmp_path / "voice" / "pitch.wav").write_bytes(b"")
    with pytest.raises(SystemExit, match="no voice take for turns, summary"):
        cut.main(base + ["--voice", str(tmp_path / "voice")])
    with pytest.raises(SystemExit, match="pitch.wav holds no sound"):
        cut.main(base + ["--voice", str(tmp_path / "voice"), "--only", "pitch"])
    (tmp_path / "voice" / "pain.wav").write_bytes(b"")
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({"width": 800, "height": 600, "scale": 1, "segments": [
        {"id": "pain", "voice": "x", "steps": [{"wait": 1}]}, {"id": "decision", "voice": "y", "variants": {"rb_open_today": [{"wait": 1}], "shell": []}}]}))
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000", "-t", "1",
                    str(tmp_path / "voice" / "pain.wav")], check=True)
    with pytest.raises((SystemExit, FileNotFoundError)) as left:  # the shell has no decision steps yet: no take is asked for it, so it gets past the check
        cut.main(base + ["--voice", str(tmp_path / "voice"), "--mode", "shell", "--spec", str(spec)])
    assert "voice take" not in str(left.value)
    assert list((tmp_path / "out" / "segments").iterdir()) == [], "nothing captured"


@pytest.mark.skipif(shutil.which("node") is None or shutil.which("google-chrome") is None or not _cdnjs_reachable(),
                    reason="the player needs node, Chrome and cdnjs")
def test_the_player_masks_a_session_id_even_split_across_chunks_and_says_so(tmp_path):
    (tmp_path / "files").mkdir()
    head = json.dumps({"version": 2, "width": 100, "height": 10})
    # split across two chunks, and with a cursor move in the middle, as Claude Code's redraw left one in the real cast
    # split across two chunks, with a cursor move in the middle, then drawn again by a redraw that writes only the
    # cells that changed (as Claude Code's does): the id is whole only on the screen
    events = [[0.1, "o", "/tmp/scratch/9520f854-dc\u001b[25Gcb-42"], [0.2, "o", "70-b2b2-5d9c9194b062/st.json \U0001F407\r\nINT8 ResNet-50 top1 76.13"],
              [0.3, "o", "\u001b[3;1H/tmp/other/1111aaaa-22X2-bbbb-3333-cccc4444dddd/x"], [0.4, "o", "\u001b[3;23H2"]]
    (tmp_path / "files" / "t.cast").write_text(head + "\n" + "".join(json.dumps(e) + "\n" for e in events))
    done = _walk(tmp_path, [{"goto": "/demo/term.html?cast=/files/t.cast&mask=ids"}, {"play": True}, {"wait": 300},
                            {"print": "[document.querySelector('.xterm-rows').innerText, document.getElementById('lapse').textContent,"
                                      " [...document.querySelectorAll('.idmask span')].map(b => [b.textContent, Math.round(b.getBoundingClientRect().top)])]"}])
    assert done.returncode == 0, done.stderr[-600:]
    rows, label, boxes = json.loads(next(l for l in done.stdout.splitlines() if l.startswith("[")))
    # the terminal holds what the cast drew, so the agent's own redraws stay right ...
    assert "/tmp/scratch/9520f854-dccb-4270-b2b2-5d9c9194b062/st.json" in rows and "/tmp/other/1111aaaa-2222-bbbb-3333-cccc4444dddd/x" in rows, rows
    # ... and every hex character of both ids, the one the stream never held whole too, is under a dot laid over it
    assert len(boxes) == 2 * 32 and {b[0] for b in boxes} == {"•"} and len({b[1] for b in boxes}) == 2
    assert "INT8 ResNet-50 top1 76.13" in rows and label.endswith("ids masked") and "session" not in label and "time-lapse" not in label, label



@pytest.mark.skipif(shutil.which("node") is None or shutil.which("google-chrome") is None or not _cdnjs_reachable(),
                    reason="the player needs node, Chrome and cdnjs")
def test_the_player_blanks_the_agents_permission_footer_in_the_screens_last_rows(tmp_path):
    """The agent's footer ("bypass permissions on (shift+tab to cycle)") reads as an agent running unchecked: the
    agent cut blanks its row, drawn in two writes as Claude Code draws it, and leaves the rest of the screen alone."""
    (tmp_path / "files").mkdir()
    head = json.dumps({"version": 2, "width": 64, "height": 6})
    events = [[0.1, "o", "INT8 ResNet-50: shift+tab to cycle views\r\n\u2500\u2500\u2500\r\n\u276f \r\n\u2500\u2500\u2500\r\n  \u23f5\u23f5 bypass permissions on"],
              [0.2, "o", "\u001b[37m (shift+tab to cycle)\u001b[0m"]]
    (tmp_path / "files" / "t.cast").write_text(head + "\n" + "".join(json.dumps(e) + "\n" for e in events))
    probe = ("[document.getElementById('lapse').textContent, [...document.querySelectorAll('.idmask .footer')]"
             ".map(b => Math.round((b.getBoundingClientRect().top - document.querySelector('.xterm-screen').getBoundingClientRect().top)"
             " / (document.querySelector('.xterm-screen').clientHeight / 6)))]")
    agent_cut = (MAKE_STUDY.parent / "agent_cut.py").read_text()
    assert "mask=ids,footer" in agent_cut, "the agent cut asks the player for both masks"
    for masks, rows in (("ids,footer", [4]), ("ids", [])):
        done = _walk(tmp_path, [{"goto": f"/demo/term.html?cast=/files/t.cast&mask={masks}"}, {"play": True}, {"wait": 300}, {"print": probe}])
        assert done.returncode == 0, done.stderr[-600:]
        label, hidden = json.loads(next(l for l in done.stdout.splitlines() if l.startswith("[")))
        assert hidden == rows, (masks, hidden)
        assert label == "ids masked", label

@_NEEDS_CHROME
def test_a_walk_leaves_no_browser_and_no_profile_behind_and_ends_when_chrome_never_starts(tmp_path):
    (tmp_path / "files").mkdir()
    (tmp_path / "files" / "two.html").write_text("<!doctype html><button>Investigate</button><button>Investigate</button>")
    import tempfile
    # a short folder: Chrome refuses a profile whose socket path would pass the system's limit (pytest's paths are long)
    scratch = Path(tempfile.mkdtemp(prefix="wk", dir="/tmp"))
    env = dict(os.environ, TMPDIR=str(scratch))
    (tmp_path / "beats.json").write_text(json.dumps({"width": 800, "height": 600, "scale": 1, "beats": [
        {"name": "t", "steps": [{"goto": "/files/two.html"}, {"click": {"text": "Investigate"}}]}]}))
    walk = ["node", str(MAKE_STUDY.parent / "walk.mjs"), "--script", str(tmp_path / "beats.json"), "--out", str(tmp_path / "w"), "--serve", str(tmp_path / "files")]
    failed = subprocess.run(walk, capture_output=True, text=True, timeout=120, env=env)
    assert failed.returncode == 1
    assert [p.name for p in scratch.iterdir() if p.name.startswith("walk-")] == [], "the walk's Chrome profile is removed once Chrome has closed"
    leftover = subprocess.run(["pgrep", "-f", f"user-data-dir={scratch}"], capture_output=True, text=True).stdout.split()
    assert leftover == [], "no Chrome of this walk outlives it"
    fake = tmp_path / "fake-chrome.sh"
    fake.write_text(f"#!/bin/sh\necho $$ > {tmp_path}/fake.pid\nexec sleep 300\n")
    fake.chmod(0o755)
    hung = subprocess.run(walk + ["--chrome", str(fake)], capture_output=True, text=True, timeout=120, env=env)
    assert hung.returncode != 0 and "Chrome did not start within 30 s" in hung.stderr, hung.stderr[-400:]
    pid = int((tmp_path / "fake.pid").read_text())
    assert subprocess.run(["kill", "-0", str(pid)], capture_output=True).returncode != 0, "the browser that never started is stopped"
    if sys.platform.startswith("linux"):
        # and gone before the walk ends, however busy the machine: run under a child subreaper, a browser the walk only
        # signalled and never saw exit would be left to the subreaper as a zombie, still there to `kill -0`
        reaper = ("import ctypes, os, subprocess, sys\n"
                  "ctypes.CDLL(None).prctl(36, 1, 0, 0, 0)\n"
                  "r = subprocess.run(sys.argv[2:], capture_output=True)\n"
                  "pid = int(open(sys.argv[1]).read())\n"
                  "alive = subprocess.run(['kill', '-0', str(pid)], capture_output=True).returncode == 0\n"
                  "print(r.returncode, alive)\n")
        (tmp_path / "fake.pid").unlink()
        done = subprocess.run([sys.executable, "-c", reaper, str(tmp_path / "fake.pid"), *walk, "--chrome", str(fake)],
                              capture_output=True, text=True, timeout=120, env=env)
        assert done.stdout.split() == ["1", "False"], (done.stdout, done.stderr[-400:])
    long = tmp_path / ("x" * 80)
    long.mkdir()
    early = subprocess.run(walk, capture_output=True, text=True, timeout=120, env=dict(os.environ, TMPDIR=str(long)))
    assert early.returncode != 0 and [p.name for p in long.iterdir() if p.name.startswith("walk-")] == [], \
        "a walk whose Chrome fails at its start still removes its profile"
    shutil.rmtree(scratch, ignore_errors=True)


@_NEEDS_CHROME
def test_a_click_aims_at_the_words_and_skips_what_an_ancestor_hides(tmp_path):
    (tmp_path / "files").mkdir()
    (tmp_path / "files" / "row.html").write_text(
        '<!doctype html><body style="margin:0"><details><summary style="display:block;width:760px;padding:10px">Record a call</summary>'
        '<p>rb decide c-ref</p></details><div style="opacity:0"><button>Investigate</button></div><button id="seen">Investigate</button>'
        '<script>let hit = []; addEventListener("click", e => hit.push([Math.round(e.clientX), e.target.id || e.target.tagName]));</script></body>')
    done = _walk(tmp_path, [{"goto": "/files/row.html"}, {"click": {"text": "Record a call"}}, {"click": {"text": "Investigate"}},
                            {"print": "hit"}])
    assert done.returncode == 0, done.stderr[-600:]
    hits = json.loads(next(l for l in done.stdout.splitlines() if l.startswith("[")))
    assert hits[0][0] < 200 and hits[0][1] == "SUMMARY", "on the words at the row's start, not the empty middle of a wide row"
    assert hits[1][1] == "seen", "the button inside a transparent parent is not there to click"


@_NEEDS_CHROME
@pytest.mark.skipif(importlib.util.find_spec("PIL") is None, reason="reads a frame with Pillow")
def test_a_capture_opens_on_the_page_once_its_content_has_drawn(tmp_path):
    from PIL import Image
    (tmp_path / "files").mkdir()
    (tmp_path / "files" / "late.html").write_text(
        '<!doctype html><body style="margin:0;background:#fff"><script>addEventListener("load", () => setTimeout(() => {'
        'document.body.style.background = "#20242c"; document.body.dataset.ready = "1"}, 900))</script></body>')
    done = _walk(tmp_path, [{"goto": "/files/late.html"}, {"waitFor": "document.body.dataset.ready === '1'"}, {"wait": 300}])
    assert done.returncode == 0, done.stderr[-600:]
    first = json.loads((tmp_path / "w" / "frames.jsonl").read_text().splitlines()[0])["file"]
    assert Image.open(tmp_path / "w" / first).convert("RGB").getpixel((5, 5)) != (255, 255, 255)


def test_the_cut_never_replaces_the_record_or_a_folder_it_did_not_make(tmp_path):
    cut = _tool("cut")
    record = tmp_path / "demo" / "int8-resnet"
    (record / ".rb").mkdir(parents=True)
    (record / ".rb" / "project.yaml").write_text("rb: 1\n")
    with pytest.raises(SystemExit, match="would hold the record itself"):
        cut.fresh_copy(record, cut.work_dir(tmp_path, tmp_path / "demo", record))
    assert (record / ".rb" / "project.yaml").exists()
    mine = cut.work_dir(tmp_path, tmp_path / "w", record)
    cut.fresh_copy(record, mine)
    cut.fresh_copy(record, mine)
    assert (mine / ".rb" / "project.yaml").exists() and (mine / cut.MARK).exists()
    other = tmp_path / "other" / "int8-resnet"
    other.mkdir(parents=True)
    (other / "keep.txt").write_text("someone's")
    with pytest.raises(SystemExit, match="is not a copy cut.py made"):
        cut.fresh_copy(record, other)
    assert (other / "keep.txt").read_text() == "someone's"


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="cut.py needs ffmpeg")
def test_a_voice_starts_at_its_segments_marker_once_the_screen_shows_it(tmp_path):
    video, wav = tmp_path / "screen.mp4", tmp_path / "voice.wav"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=black:size=160x120:rate=30", "-t", "1", "-pix_fmt", "yuv420p", str(video)], check=True)
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000", "-t", "2", str(wav)], check=True)
    (tmp_path / "screen.marks.jsonl").write_text('{"t": 0.0, "label": "beat a"}\n{"t": 1.5, "label": "voice"}\n')
    cut = _tool("cut")
    assert cut.voice_start(video) == 1.5
    assert cut.fit(video, wav, tmp_path / "seg.mp4") == pytest.approx(4.1, abs=0.01), "the marker, the line, a breath"
    loud = subprocess.run(["ffmpeg", "-v", "info", "-ss", "1.7", "-t", "1", "-i", str(tmp_path / "seg.mp4"), "-af", "volumedetect", "-f", "null", "-"],
                          capture_output=True, text=True).stderr
    before = subprocess.run(["ffmpeg", "-v", "info", "-t", "1.4", "-i", str(tmp_path / "seg.mp4"), "-af", "volumedetect", "-f", "null", "-"],
                            capture_output=True, text=True).stderr
    level = lambda out: float(re.search(r"max_volume: (-?[0-9.]+|-inf) dB", out).group(1).replace("-inf", "-200"))
    assert level(before) < -60 < level(loud), "silent before the marker, the line after it"


def test_a_cast_beat_starts_its_voice_at_its_own_seconds_once_its_terminal_shows_what_the_line_says(tmp_path):
    """A cast plays in one step and drops no marker: the segment's voice_at says when its line starts (the pain's names
    the data config and the agent log, which its terminal shows only seconds in); a marker, where there is one, wins."""
    cut = _tool("cut")
    video = tmp_path / "screen.mp4"
    assert cut.voice_start(video, {"voice_at": 4.0}) == 4.0 and cut.voice_start(video, {}) == 0.0
    (tmp_path / "screen.marks.jsonl").write_text('{"t": 1.5, "label": "voice"}\n')
    assert cut.voice_start(video, {"voice_at": 4.0}) == 1.5
    spec = json.loads((MAKE_STUDY.parent / "cut.json").read_text())
    pain = next(s for s in spec["segments"] if s["id"] == "pain")
    assert pain.get("voice_at", 0) >= 3.5, "the pain's line waits for its terminal"


@pytest.mark.skipif(shutil.which("ffmpeg") is None or importlib.util.find_spec("PIL") is None, reason="cut.py needs ffmpeg and Pillow")
def test_the_cut_holds_a_segments_voice_until_its_voice_at_when_it_drops_no_marker(tmp_path):
    """Run whole: a segment whose screen drops no marker (here one captured elsewhere, as the share beat is) gets its line
    at its voice_at seconds, silent before, in the cut it makes."""
    fake = tmp_path / "bin" / "rb"
    fake.parent.mkdir()
    fake.write_text("#!/usr/bin/env python3\nimport http.server, sys\nclass H(http.server.BaseHTTPRequestHandler):\n"
                    "    def do_GET(self):\n        self.send_response(200); self.end_headers(); self.wfile.write(b'ok')\n"
                    "    def log_message(self, *a): pass\nhttp.server.HTTPServer(('127.0.0.1', int(sys.argv[3])), H).serve_forever()\n")
    fake.chmod(0o755)
    record = tmp_path / "int8-resnet"
    (record / ".rb").mkdir(parents=True)
    casts = [tmp_path / "pain.cast", tmp_path / "import.cast"]
    for c in casts:
        c.write_text("{}\n")
    seg = tmp_path / "captured"
    seg.mkdir()
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=black:size=1920x1200:rate=30", "-t", "4",
                    "-pix_fmt", "yuv420p", str(seg / "screen.mp4")], check=True)
    voice = tmp_path / "voice"
    voice.mkdir()
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000", "-t", "1",
                    str(voice / "share.wav")], check=True)
    spec = tmp_path / "cut.json"
    spec.write_text(json.dumps({"width": 1440, "height": 900, "scale": 2, "segments": [
        {"id": "share", "beat": "1:58 the share", "voice": "x", "voice_at": 2.0, "external": True, "steps": [{"wait": 1}]}]}))
    out = tmp_path / "out"
    assert _tool("cut").main(["--record", str(record), "--import-cast", str(casts[1]), "--pain-cast", str(casts[0]),
                              "--rb-bin", str(fake.parent), "--out", str(out), "--work", str(tmp_path / "w"), "--spec", str(spec),
                              "--voice", str(voice), "--share-segment", str(seg)]) == 0
    level = lambda ss, t: float(re.search(r"max_volume: (-?[0-9.]+|-inf) dB", subprocess.run(
        ["ffmpeg", "-v", "info", "-ss", str(ss), "-t", str(t), "-i", str(out / "cut.mp4"), "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True, text=True).stderr).group(1).replace("-inf", "-200"))
    assert level(0, 1.9) < -60 < level(2.1, 0.8), "silent until voice_at, the line after it"


@pytest.mark.skipif(shutil.which("ffmpeg") is None or importlib.util.find_spec("PIL") is None, reason="contact.py needs ffmpeg and Pillow")
def test_a_contact_sheet_of_a_video_whose_sound_outlasts_its_picture_takes_only_frames_there_are(tmp_path):
    """A cut's sound can end a few milliseconds after its last frame: the sheet stops at the picture's end."""
    video = tmp_path / "cut.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=black:size=320x200:rate=30", "-f", "lavfi",
                    "-i", "sine=frequency=440:sample_rate=48000", "-t", "4", "-map", "0:v", "-map", "1:a", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-af", "apad=pad_dur=0.2", "-shortest", str(video)], check=True)
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(video), "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono",
                    "-filter_complex", "[0:a][1:a]concat=n=2:v=0:a=1,atrim=0:4.2[a]", "-map", "0:v", "-map", "[a]", "-c:v", "copy",
                    "-c:a", "aac", str(tmp_path / "long.mp4")], check=True)
    done = subprocess.run([sys.executable, str(MAKE_STUDY.parent / "contact.py"), "--video", str(tmp_path / "long.mp4"),
                           str(tmp_path / "sheet.png"), "--every", "4"], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr[-400:]
    assert "1 frames" in done.stdout, done.stdout


def test_the_cut_says_what_rb_open_answered_when_it_says_no(tmp_path):
    fake = tmp_path / "rb"
    # the body comes a while after the status line, as it can on a loaded machine: the cut must wait for it
    fake.write_text("#!/usr/bin/env python3\nimport http.server, sys, time\nclass H(http.server.BaseHTTPRequestHandler):\n"
                    "    def do_GET(self):\n        self.send_response(404); self.end_headers(); self.wfile.flush(); time.sleep(1)\n"
                    "        self.wfile.write(b'{\"error\": \"The shell renderer is not built yet\"}')\n"
                    "    def log_message(self, *a): pass\n"
                    "http.server.HTTPServer(('127.0.0.1', int(sys.argv[3])), H).serve_forever()\n")
    fake.chmod(0o755)
    folder = tmp_path / "int8-resnet"
    folder.mkdir()
    with pytest.raises(SystemExit, match="rb open answered 404 .*not built yet"):
        _tool("cut").start_open(folder, str(tmp_path), True)


def test_the_cut_starts_rb_open_with_no_test_switch_since_its_root_is_the_shell(tmp_path):
    """rb open's / is the shell for everyone: the cut runs it as the researcher does, in shell mode too."""
    seen = tmp_path / "env.json"
    fake = tmp_path / "rb"
    fake.write_text("#!/usr/bin/env python3\nimport http.server, json, os, sys\n"
                    f"open({str(seen)!r}, 'w').write(json.dumps(dict(os.environ)))\n"
                    "class H(http.server.BaseHTTPRequestHandler):\n"
                    "    def do_GET(self):\n        self.send_response(200); self.end_headers(); self.wfile.write(b'ok')\n"
                    "    def log_message(self, *a): pass\n"
                    "http.server.HTTPServer(('127.0.0.1', int(sys.argv[3])), H).serve_forever()\n")
    fake.chmod(0o755)
    record = tmp_path / "int8-resnet"
    (record / ".rb").mkdir(parents=True)
    (record / ".rb" / "project.yaml").write_text("rb: 1\nkind: project\n")
    spec = tmp_path / "cut.json"
    spec.write_text(json.dumps({"width": 1440, "height": 900, "scale": 2, "segments": [{"id": "standing"}]}))
    casts = [tmp_path / "pain.cast", tmp_path / "import.cast"]
    for c in casts:
        c.write_text("{}\n")
    try:
        _tool("cut").main(["--record", str(record), "--import-cast", str(casts[1]), "--pain-cast", str(casts[0]),
                           "--rb-bin", str(tmp_path), "--out", str(tmp_path / "out"), "--work", str(tmp_path / "w" / "int8-resnet"),
                           "--mode", "shell", "--spec", str(spec)])
    except (SystemExit, Exception):
        pass  # a cut with no segment to film ends however it ends; what rb open was started with is the point
    env = json.loads(seen.read_text())
    assert "RB_TEST_SHELL" not in env


def test_the_loops_ask_for_the_test_switch_only_when_rb_opens_root_draws_no_shell(tmp_path):
    """--shell auto: rb open as the researcher starts it, and the switch only for an rb from before the switch, whose `/`
    draws the shell only with it (an rb that still holds the test entry but draws the shell at `/` gets no switch)."""
    loops = _tool("loops")
    for old_rb, want in ((False, [False]), (True, [False, True])):
        d = tmp_path / ("old" if old_rb else "new")
        d.mkdir()
        seen = d / "env.jsonl"
        shell_when = "'RB_TEST_SHELL' in os.environ" if old_rb else "True"
        (d / "rb").write_text("#!/usr/bin/env python3\nimport http.server, json, os, sys\n# RB_TEST_SHELL (the entry is still in the binary)\n"
                              f"open({str(seen)!r}, 'a').write(json.dumps('RB_TEST_SHELL' in os.environ) + '\\n')\n"
                              f"shell = {shell_when}\n"
                              "class H(http.server.BaseHTTPRequestHandler):\n"
                              "    def do_GET(self):\n        self.send_response(200); self.end_headers()\n"
                              "        self.wfile.write(b'<nav id=\"nav\"></nav>' if shell else b'<main>INT8 ResNet-50</main>')\n"
                              "    def log_message(self, *a): pass\n"
                              "http.server.HTTPServer(('127.0.0.1', int(sys.argv[3])), H).serve_forever()\n")
        (d / "rb").chmod(0o755)
        record = d / "int8-resnet"
        (record / ".rb").mkdir(parents=True)
        a = argparse.Namespace(work=d / "w", record=record, rb_bin=str(d), shell="auto")
        work, proc, url = loops.start_open(a, d / "out", "panes")
        try:
            with urllib.request.urlopen(url, timeout=10) as r:
                assert b'id="nav"' in r.read()
        finally:
            proc.terminate()
            proc.wait(timeout=10)
        assert [json.loads(l) for l in seen.read_text().splitlines()] == want, old_rb


def test_the_loops_stop_rb_open_when_its_root_answers_no_page(tmp_path):
    """--shell auto asks rb open's / for its page; one that answers an error is stopped, not left serving."""
    loops = _tool("loops")
    pid = tmp_path / "pid"
    (tmp_path / "rb").write_text("#!/usr/bin/env python3\nimport http.server, os, sys\n"
                                 f"open({str(pid)!r}, 'w').write(str(os.getpid()))\n"
                                 "class H(http.server.BaseHTTPRequestHandler):\n"
                                 "    def do_GET(self):\n        self.send_response(200 if self.path == '/' and not os.path.exists(%r) else 500); self.end_headers()\n"
                                 "    def log_message(self, *a): pass\n"
                                 "http.server.HTTPServer(('127.0.0.1', int(sys.argv[3])), H).serve_forever()\n" % str(tmp_path / "fail"))
    (tmp_path / "rb").chmod(0o755)
    record = tmp_path / "int8-resnet"
    (record / ".rb").mkdir(parents=True)
    cut = _tool("cut")
    start = cut.start_open

    def failing_after_start(*args, **kw):
        proc, url = start(*args, **kw)
        (tmp_path / "fail").write_text("")
        return proc, url
    loops._tool = lambda name: type("M", (), {"start_open": staticmethod(failing_after_start), "fresh_copy": staticmethod(cut.fresh_copy)})
    a = argparse.Namespace(work=tmp_path / "w", record=record, rb_bin=str(tmp_path), shell="auto")
    with pytest.raises(urllib.error.HTTPError):
        loops.start_open(a, tmp_path / "out", "panes")
    with pytest.raises(ProcessLookupError):
        os.kill(int(pid.read_text()), 0)


@_NEEDS_CHROME
def test_the_time_lapse_label_tells_the_sessions_own_clock_when_it_starts_midway(tmp_path):
    """A beat played from 1:40 into the recording says 1:40, not the time since the beat began."""
    (tmp_path / "files").mkdir()
    head = json.dumps({"version": 2, "width": 80, "height": 10})
    events = [[1.0, "o", "rb init\r\n"], [100.0, "o", "INT8 ResNet-50 top1 76.13\r\n"], [102.4, "o", "rb status\r\n"]]
    (tmp_path / "files" / "t.cast").write_text(head + "\n" + "".join(json.dumps(e) + "\n" for e in events))
    done = _walk(tmp_path, [{"goto": "/demo/term.html?cast=/files/t.cast&from=99.5&speed=4"}, {"play": True}, {"wait": 200},
                            {"print": "document.getElementById('lapse').textContent"}])
    assert done.returncode == 0, done.stderr[-600:]
    label = json.loads(next(l for l in done.stdout.splitlines() if l.startswith('"')))
    assert label.startswith("time-lapse, 4× · 1:42 into the recording"), label


# ---------------------------------------------------------------- the onboarding loops (loops.py)

_NEEDS_FFMPEG_PIL = pytest.mark.skipif(shutil.which("ffmpeg") is None or importlib.util.find_spec("PIL") is None,
                                       reason="loops.py needs ffmpeg and Pillow")


def _frame(video: Path, at: float, png: Path):
    from PIL import Image
    # a negative time counts from the end
    seek = ["-sseof", f"{at:.3f}"] if at < 0 else ["-ss", f"{at:.3f}"]
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", *seek, "-i", str(video), "-frames:v", "1", str(png)], check=True)
    return Image.open(png).convert("L")


def _differ(a, b) -> float:
    from PIL import ImageChops, ImageStat
    return ImageStat.Stat(ImageChops.difference(a, b)).mean[0]


@_NEEDS_FFMPEG_PIL
def test_a_loop_ends_on_its_own_first_frame_so_it_plays_round_with_no_jump(tmp_path):
    loops = _tool("loops")
    src = tmp_path / "src.mp4"
    # a moving picture: every frame differs from the one a second before
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=30", "-t", "4",
                    "-pix_fmt", "yuv420p", str(src)], check=True)
    loops.seamless(src, 0.5, 2.5, tmp_path / "loop.mp4")
    assert abs(loops.duration(tmp_path / "loop.mp4") - 2.5) < 0.1
    first = _frame(tmp_path / "loop.mp4", 0, tmp_path / "a.png")
    last = _frame(tmp_path / "loop.mp4", -0.04, tmp_path / "b.png")
    middle = _frame(tmp_path / "loop.mp4", 1.25, tmp_path / "c.png")
    assert _differ(first, middle) > 6, "the source moves"
    # the loop opens on the source at start + seam (1.0 s) and ends on the frame just before it, so it plays round
    assert _differ(first, _frame(src, 1.0, tmp_path / "s1.png")) < 1.5
    assert _differ(last, _frame(src, 0.955, tmp_path / "s0.png")) < 2.5
    # a window too short for its seam, or past the source's end, is refused before anything is made
    with pytest.raises(SystemExit, match="too short"):
        loops.seamless(src, 0, 0.8, tmp_path / "x.mp4")
    with pytest.raises(SystemExit, match="needs"):
        loops.seamless(src, 2, 2.5, tmp_path / "x.mp4")


@_NEEDS_FFMPEG_PIL
def test_a_loop_is_published_as_webm_mp4_a_gif_under_its_limit_and_a_poster_with_its_caption_under_the_screen(tmp_path):
    loops = _tool("loops")
    src = tmp_path / "src.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=size=640x400:rate=30", "-t", "2",
                    "-pix_fmt", "yuv420p", str(src)], check=True)
    made = loops.render(src, "INT8 ResNet-50: a finding, its runs", 1.0, tmp_path / "out", "t")
    assert set(made["files"]) == {"t.webm", "t.mp4", "t.gif", "t.png"}
    probe = lambda f: json.loads(subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                                                 "stream=codec_name,width,height", "-of", "json", str(tmp_path / "out" / f)],
                                                capture_output=True, text=True, check=True).stdout)["streams"][0]
    assert probe("t.webm")["codec_name"] == "vp9" and probe("t.mp4")["codec_name"] == "h264"
    assert probe("t.webm")["width"] == probe("t.mp4")["width"] == 1280 and probe("t.gif")["width"] == 800
    # the caption is a band under the screen: the screen keeps its whole height, scaled
    # "under 3 MB" holds read either way, 3,000,000 bytes or 3 MiB
    assert probe("t.mp4")["height"] > 800 and made["files"]["t.gif"] < loops.GIF_MAX <= 3_000_000
    # a GIF over its limit is made again with fewer frames and colours until it fits
    tight = loops.gif(tmp_path / "out" / "t.mp4", tmp_path / "small.gif", limit=made["files"]["t.gif"] // 3)
    assert (tight["fps"], tight["colors"]) != (15, 256) and (tmp_path / "small.gif").stat().st_size < made["files"]["t.gif"] // 3


def test_rb_sync_sends_to_the_share_loops_local_stand_in_and_is_given_its_link(tmp_path, monkeypatch):
    """The share loop's stand-in speaks what rb sync asks of a hub: who the credential is, then the upload, answered
    with a link on 127.0.0.1, which rb says as it is."""
    from rbtest import AGENT_VARS, command, environment

    loops = _tool("loops")
    hub = loops._Hub()
    try:
        env = {k: v for k, v in environment().items() if k not in AGENT_VARS}
        env.update({"RB_HUB_URL": hub.url, "RB_HUB_TOKEN": "rb_demo_local", "RB_CONFIG_DIR": str(tmp_path / "cfg"),
                    "no_proxy": "127.0.0.1,localhost", "GIT_CONFIG_GLOBAL": str(tmp_path / "gitconfig"), "GIT_CONFIG_NOSYSTEM": "1"})
        (tmp_path / "gitconfig").write_text("[user]\n\temail = ana@example.com\n\tname = ana\n")
        for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
            env.pop(k, None)
        repo = tmp_path / "p"
        repo.mkdir()
        run = lambda *argv: subprocess.run([*command(), *argv], cwd=repo, env=env, capture_output=True, text=True, timeout=120)
        subprocess.run(["git", "init", "-q"], cwd=repo, env=env, check=True)
        assert run("init", "INT8 ResNet-50", "--id", "int8-resnet50", "--no-agents").returncode == 0
        done = run("sync")
        assert done.returncode == 0, done.stderr
        assert f"Synced, privately: {hub.url}/u/ana/int8-resnet50" in done.stdout + done.stderr
        assert hub.got == [{"bytes": hub.got[0]["bytes"], "project": "int8-resnet50"}] and hub.got[0]["bytes"] > 0
    finally:
        hub.close()


def test_the_loops_say_what_each_needs_before_they_capture_anything(tmp_path):
    loops = _tool("loops")
    with pytest.raises(SystemExit, match="--only names no loop: intro"):
        loops.main(["--out", str(tmp_path), "--only", "import,intro"])
    with pytest.raises(SystemExit, match="the share loop needs --record, --rb-bin, --study-bin"):
        loops.main(["--out", str(tmp_path), "--only", "share"])
    assert not any(tmp_path.iterdir())



def test_no_loops_caption_names_who_made_a_write():
    """A caption is read under the screen: it says what rb does, never whose writes they are."""
    for name, loop in _tool("loops").LOOPS.items():
        words = re.findall(r"[a-z]+", loop["caption"].lower())
        assert not {"agent", "agents", "session", "claude", "codex", "cursor", "human", "ana"} & set(words), (name, loop["caption"])
        # nor does anything stand as the writer of a record: a record is recorded, by no one named
        assert not re.search(r"\b(?:rb|it|they|we|i|agent)\s+(?:records?|writes?|recorded|wrote)\b", loop["caption"].lower()), (name, loop["caption"])


@_NEEDS_CHROME
def test_a_cast_that_plays_longer_than_one_browser_call_plays_to_its_end(tmp_path):
    """A terminal beat's playback can outlast the 30 s within which one call to Chrome must answer: the walk waits for it."""
    (tmp_path / "files").mkdir()
    head = json.dumps({"version": 2, "width": 80, "height": 10})
    events = [[0.2, "o", "rb sync --watch\r\n"], [32.0, "o", "INT8 ResNet-50 top1 76.13\r\n"]]
    (tmp_path / "files" / "t.cast").write_text(head + "\n" + "".join(json.dumps(e) + "\n" for e in events))
    done = _walk(tmp_path, [{"goto": "/demo/term.html?cast=/files/t.cast"}, {"play": True}, {"mark": "played"},
                            {"print": "document.querySelector('.xterm-rows').innerText"}], timeout=180)
    assert done.returncode == 0, done.stderr[-600:]
    assert "INT8 ResNet-50 top1 76.13" in done.stdout
    marks = {m["label"]: m["t"] for m in map(json.loads, (tmp_path / "w" / "marks.jsonl").read_text().splitlines())}
    assert marks["played"] > 31


@_NEEDS_CHROME
def test_a_walk_wheels_a_pane_until_the_named_part_sits_where_it_asks(tmp_path):
    """The inspector's run card changes height between builds: the walk turns the wheel until Reproduce is where it
    asks, not by a fixed distance."""
    (tmp_path / "files").mkdir()
    (tmp_path / "files" / "p.html").write_text(
        "<!doctype html><body style='margin:0'><aside id=insp style='position:fixed;right:0;top:0;width:400px;height:100vh;overflow:auto'>"
        "<p id=top style='margin:0;height:40px'>c-pt-1024</p><div style='height:1234px'>INT8 ResNet-50 runs</div>"
        "<h3 id=repro style='margin:0'>Reproduce this run</h3><div style='height:2000px'></div></aside></body>")
    done = _walk(tmp_path, [{"goto": "/files/p.html"}, {"move": "#top"}, {"wheelTo": "#repro", "top": 140},
                            {"print": "[Math.round(document.querySelector('#repro').getBoundingClientRect().top), document.querySelector('#insp').scrollTop]"}])
    assert done.returncode == 0, done.stderr[-600:]
    top, scrolled = json.loads(next(l for l in done.stdout.splitlines() if l.startswith("[")))
    assert abs(top - 140) < 4 and scrolled > 1000, (top, scrolled)


@_NEEDS_CHROME
def test_the_panes_loop_tells_a_readable_reproduce_part_from_one_wrapped_to_a_few_characters(tmp_path):
    """A loop never holds on words drawn a few characters to a line: the panes loop checks the part it shows."""
    loops = _tool("loops")
    (tmp_path / "files").mkdir()
    body = "<p>ok · commit 2982f0fae9 · INT8 ResNet-50</p><p>unknown · the run was made before it was recorded</p>"
    # as the run card broke: a wide grid item whose bare text lands in a narrow anonymous cell
    grid = "<li style='display:grid;grid-template-columns:22px 1fr;word-break:break-all'>ok · commit 2982f0fae9 · INT8 ResNet-50</li>"
    (tmp_path / "files" / "p.html").write_text(f"<!doctype html><div id=wide style='width:400px'>{body}</div>"
                                               f"<div id=narrow style='width:22px;word-break:break-all'>{body}</div>"
                                               f"<ul id=grid style='width:400px'>{grid}</ul>")
    done = _walk(tmp_path, [{"goto": "/files/p.html"}, {"print": f"[{loops.readable('#wide')}, {loops.readable('#narrow')}, {loops.readable('#grid')}, {loops.readable('#none')}]"}])
    assert done.returncode == 0, done.stderr[-600:]
    assert json.loads(next(l for l in done.stdout.splitlines() if l.startswith("["))) == [True, False, False, False]


def test_a_loop_made_again_alone_replaces_its_own_entry_and_keeps_the_others(tmp_path):
    loops = _tool("loops")
    loops.keep(tmp_path, [{"name": "import", "seconds": 6.5}, {"name": "share", "seconds": 7.5}])
    loops.keep(tmp_path, [{"name": "panes", "seconds": 7.1}])
    loops.keep(tmp_path, [{"name": "share", "seconds": 7.4}])
    assert json.loads((tmp_path / "loops.json").read_text()) == [
        {"name": "import", "seconds": 6.5}, {"name": "share", "seconds": 7.4}, {"name": "panes", "seconds": 7.1}]


@_NEEDS_CHROME
def test_a_cast_is_played_only_once_its_last_output_is_drawn(tmp_path):
    """xterm.js parses and draws a write after it is handed over: the player says it has played (and a walk reads the
    screen) only once its last output is on the screen. Read in the same turn the player says so."""
    (tmp_path / "files").mkdir()
    head = json.dumps({"version": 2, "width": 80, "height": 10})
    events = [[0.1, "o", "rb sync --watch\r\n"], [0.4, "o", "INT8 ResNet-50 top1 76.13\r\n"]]
    (tmp_path / "files" / "t.cast").write_text(head + "\n" + "".join(json.dumps(e) + "\n" for e in events))
    done = _walk(tmp_path, [{"goto": "/demo/term.html?cast=/files/t.cast"}, {"waitFor": "window.__ready === true"},
                            {"print": "window.__start().then(() => document.querySelector('.xterm-rows').innerText)"}])
    assert done.returncode == 0, done.stderr[-600:]
    assert "INT8 ResNet-50 top1 76.13" in done.stdout, done.stdout[-400:]


@_NEEDS_CHROME
def test_a_cast_started_midway_is_ready_only_once_what_came_before_is_drawn(tmp_path):
    """from=<s> writes what came before at once: the page says it is ready only once that is on the screen."""
    (tmp_path / "files").mkdir()
    head = json.dumps({"version": 2, "width": 80, "height": 10})
    events = [[0.1, "o", "rb sync --watch\r\n"], [0.4, "o", "INT8 ResNet-50 top1 76.13\r\n"], [5.0, "o", "done\r\n"]]
    (tmp_path / "files" / "blank.html").write_text("<!doctype html><body></body>")
    (tmp_path / "files" / "t.cast").write_text(head + "\n" + "".join(json.dumps(e) + "\n" for e in events))
    # a fresh frame, read in the same turn it says it is ready
    read = ("new Promise(r => { const f = document.createElement('iframe'); f.style.cssText = 'width:900px;height:400px';"
            " f.src = '/demo/term.html?cast=/files/t.cast&from=1'; document.body.appendChild(f);"
            " const t = () => f.contentWindow && f.contentWindow.__ready === true"
            " ? r(f.contentDocument.querySelector('.xterm-rows').innerText) : setTimeout(t, 0); t(); })")
    done = _walk(tmp_path, [{"goto": "/files/blank.html"}, {"print": read}])
    assert done.returncode == 0, done.stderr[-600:]
    assert "INT8 ResNet-50 top1 76.13" in done.stdout, done.stdout[-400:]


@_NEEDS_CHROME
def test_a_lead_step_puts_the_pointer_where_the_beat_before_left_it_hovering_from_the_first_frame(tmp_path):
    """A beat that starts where the one before it ended starts with the pointer there too: a lead pointAt puts it on the
    element (scrolled into view as a move would) before filming, with the page's hover state, and the capture draws it
    from the first frame; no move is filmed to get there."""
    (tmp_path / "files").mkdir()
    (tmp_path / "files" / "p.html").write_text(
        "<!doctype html><style>body{margin:0;height:3000px} #n{position:absolute;top:1500px;left:300px;color:black}"
        " #n:hover{color:rgb(0,0,255)}</style><body><span id=n>-0.1733</span></body>")
    done = _walk(tmp_path, [{"goto": "/files/p.html"}, {"pointAt": "#n", "lead": True}, {"wait": 300},
                            {"print": "[scrollY > 0, getComputedStyle(document.getElementById('n')).color]"}])
    assert done.returncode == 0, done.stderr[-600:]
    assert '[true,"rgb(0, 0, 255)"]' in done.stdout, done.stdout
    cursor = [json.loads(l) for l in (tmp_path / "w" / "cursor.jsonl").read_text().splitlines()]
    assert len(cursor) == 1 and cursor[0]["t"] < 0.2, cursor
    assert 300 <= cursor[0]["x"] <= 360 and 0 < cursor[0]["y"] < 600, cursor
    # filmed, the jump is in the capture too: the drawn pointer is where the page's hover is
    again = _walk(tmp_path, [{"goto": "/files/p.html"}, {"wait": 200}, {"pointAt": "#n"}, {"wait": 200}])
    assert again.returncode == 0, again.stderr[-600:]
    cursor = [json.loads(l) for l in (tmp_path / "w" / "cursor.jsonl").read_text().splitlines()]
    assert cursor and 300 <= cursor[-1]["x"] <= 360 and cursor[-1]["t"] >= 0.2, cursor


@_NEEDS_CHROME
@pytest.mark.skipif(importlib.util.find_spec("PIL") is None, reason="reads the first frame with Pillow")
def test_a_walks_lead_steps_set_up_the_page_before_its_first_frame(tmp_path):
    """The shell's pointer guide shows on a first visit: a cut sets it seen and loads the page again before filming, so
    no frame shows the page before that (here: red until the page has been told, green after)."""
    from PIL import Image
    (tmp_path / "files").mkdir()
    (tmp_path / "files" / "p.html").write_text(
        "<!doctype html><body style='margin:0;height:100vh'><script>document.body.style.background ="
        " localStorage.getItem('rb.guide.1') ? 'rgb(0,160,0)' : 'rgb(200,0,0)'</script>INT8 ResNet-50</body>")
    done = _walk(tmp_path, [{"goto": "/files/p.html"}, {"eval": "localStorage.setItem('rb.guide.1', 'ended')", "lead": True},
                            {"goto": "/files/p.html", "lead": True}, {"wait": 300}])
    assert done.returncode == 0, done.stdout + done.stderr
    frames = [json.loads(l)["file"] for l in (tmp_path / "w" / "frames.jsonl").read_text().splitlines()]
    assert frames
    for f in frames:
        r, g, b = Image.open(tmp_path / "w" / f).convert("RGB").getpixel((5, 5))
        assert g > r, f"{f} shows the page before its lead steps ran"
    bad = _walk(tmp_path, [{"goto": "/files/p.html"}, {"click": "body", "lead": True}])
    assert bad.returncode != 0 and "a lead step only loads or sets up" in bad.stderr
    late = _walk(tmp_path, [{"goto": "/files/p.html"}, {"wait": 100}, {"eval": "document.title = 'filmed'", "lead": True}])
    assert late.returncode != 0 and "a lead step comes only after the first goto" in late.stderr
    assert json.loads((tmp_path / "w" / "walk.json").read_text())["frames"] == 0


def test_every_browser_segment_has_shell_steps_that_set_the_guide_seen_before_filming():
    """The dry run on the shell (rb page draws it; rb open's own /, since A's 4b): each shell segment opens
    on the record with the pointer guide set seen in lead steps, so no segment's frames show the guide over what its
    voice line speaks of. Since Overview v2 the shell says the reference and the designs in the queue's beat, so those two
    segments are rb open as it was only."""
    spec = json.loads((MAKE_STUDY.parent / "cut.json").read_text())
    for s in spec["segments"]:
        if "variants" not in s:
            continue
        shell = s["variants"].get("shell") or []
        if s["id"] in ("reference", "waiting"):
            assert s["variants"].get("rb_open_today") and not shell, s["id"]
            continue
        assert shell, s["id"]
        assert shell[0] == {"goto": "{open}"}, s["id"]
        assert any(st.get("lead") and "rb.guide.1" in st.get("eval", "") for st in shell[1:3]), s["id"]
        assert shell[2] == {"goto": "{open}", "lead": True}, s["id"]
        assert any(st.get("mark") == "voice" for st in shell), s["id"]


def test_every_browser_beat_zooms_on_a_box_inside_the_frame_never_under_720_wide():
    """Each shell beat and the import's terminal frame the part their voice speaks of at about 1.5 to 2 times: a box of
    the frame's shape, inside the 1440 x 900 frame, never under 720 CSS pixels wide (twice the size at most)."""
    spec = json.loads((MAKE_STUDY.parent / "cut.json").read_text())
    cut, compose = _tool("cut"), _tool("compose")
    W, H = spec["width"], spec["height"]
    zoomed = {}
    for s in spec["segments"]:
        z = cut.zoom_for(s, "shell")
        if z:
            compose.check_box(z["box"], W, H)
            x, y, w, h = z["box"]
            assert x >= 0 and y >= 0 and x + w <= W and y + h <= H and w >= 720, s["id"]
            assert 1.4 <= W / w <= 2.0 and h * W == w * H, (s["id"], z["box"])
            zoomed[s["id"]] = z["box"]
    assert set(zoomed) == {"import", "standing", "integrity", "queue", "holds", "perchannel", "receipt", "call"}, zoomed
    # and the cut made by default is that one
    assert cut.DEFAULT_MODE == "shell" and all(cut.zoom_for(s, cut.DEFAULT_MODE) for s in spec["segments"] if s["id"] in zoomed)
    for box in ([-1, 0, 720, 450], [800, 500, 720, 450], [0, 0, 640, 400], [0, 0, 800, 400]):
        with pytest.raises(SystemExit):
            compose.check_box(box, W, H)


def test_the_shell_cut_reads_overview_v2_standing_then_integrity_then_the_queue_first():
    """Overview v2 (OVERVIEW-V2.md): the payoff opens on the standing card's line, then the integrity strip, then the queue,
    whose first item (the baseline that does not reproduce) is the turn; the call is made from that row's own button.
    Each line says the screen's own numbers (take 4, plain words: 8 of 13 rules not settled, 9 of 13 written after their results, no run's
    command, 90.48 against 91.25)."""
    cut = _tool("cut")
    spec = json.loads((MAKE_STUDY.parent / "cut.json").read_text())
    shell = [s["id"] for s in spec["segments"] if s.get("steps") or (s.get("variants") or {}).get("shell")]
    assert shell[:5] == ["pain", "import", "standing", "integrity", "queue"], shell
    assert shell.index("queue") < shell.index("call"), shell
    seg = {s["id"]: s for s in spec["segments"]}
    said = {k: cut.voice_line(seg[k], "shell") for k in ("standing", "integrity", "queue")}
    assert "No answer yet" in said["standing"] and "eight of thirteen rules are not settled" in said["standing"]
    assert "nine of thirteen rules were written after their results came in" in said["integrity"]
    assert "none of the runs has a command from when it ran" in said["integrity"] and "90.48" in said["queue"] and "91.25" in said["queue"]
    steps = json.dumps(seg["call"]["variants"]["shell"])
    assert "#queue li.qrow:nth-of-type(1) a.qbtn" in steps and "#your-call" not in steps
    # the call's zoom holds what its voice says rb shows: the command that records it (and how the call will read)
    assert "aside.insp p.willread" in steps and "aside.insp .cmdbox" in steps
    assert "the command that records it" in cut.voice_line(seg["call"], "shell")
    for k in ("standing", "integrity", "queue"):
        assert any(st.get("mark") == "subject" and "#overview" in json.dumps(st.get("rect")) for st in seg[k]["variants"]["shell"]), k


def test_the_receipt_follows_holds_on_the_same_criterion_and_frames_the_hashes_it_speaks_of():
    """The run behind the number just shown: the receipt beat comes right after holds and starts where holds ends (the
    criterion open in the inspector, set up before filming, never by a filmed step), and its voice speaks of the
    checkpoint and the result files with their sha256, which its subjects frame; it no longer repeats the integrity
    strip's point about commands."""
    cut = _tool("cut")
    spec = json.loads((MAKE_STUDY.parent / "cut.json").read_text())
    shell = [s["id"] for s in spec["segments"] if s.get("steps") or (s.get("variants") or {}).get("shell")]
    assert shell.index("receipt") == shell.index("holds") + 1, shell
    seg = {s["id"]: s for s in spec["segments"]}
    steps = seg["receipt"]["variants"]["shell"]
    filmed = [st for st in steps[1:] if not (st.get("lead") or "waitFor" in st)]
    lead = steps[:steps.index(filmed[0])]
    assert any(st.get("lead") and "c-c-pt-1024" in st.get("eval", "") for st in lead), lead
    assert not any("q-q-calib" in json.dumps(st) or "c-c-pt-1024'" in json.dumps(st.get("click", "")) for st in filmed), filmed
    holds_end = json.dumps(seg["holds"]["variants"]["shell"])
    assert "#q-q-calib" in holds_end and "#c-c-pt-1024" in holds_end
    # the pointer where holds leaves it, and each target scrolled as the filmed clicks scroll it
    last_move = [st["move"] for st in seg["holds"]["variants"]["shell"] if "move" in st][-1]
    assert any(st.get("lead") and st.get("pointAt") == last_move for st in lead), lead
    assert all("scrollIntoView" in st["eval"] for st in lead if st.get("lead") and ".click()" in st.get("eval", "")), lead
    said = cut.voice_line(seg["receipt"], "shell")
    assert "sha256" in said and "checkpoint" in said and "command" not in said
    rects = [json.dumps(st["rect"]) for st in steps if st.get("mark") == "subject"]
    assert any("(checkpoint)" in r for r in rects) and any("metrics.json" in r for r in rects), rects


def test_a_zoom_is_full_before_the_line_starts_and_eases_out_only_after_it_ends():
    """No zoom moves mid-sentence: full by the time the voice starts (a line marked earlier than the ease waits for it),
    and easing out only once the line and most of its breath are over."""
    cut, compose = _tool("cut"), _tool("compose")
    for screen, at, voice in [(3.0, 0.1, 2.0), (9.0, 2.0, 4.0), (2.0, 1.0, 6.5), (26.9, 0.0, 12.7)]:
        plan = cut.zoom_plan(screen, at, voice)
        assert plan["full"] == plan["at"] >= max(at, cut.EASE)
        assert plan["until"] >= plan["at"] + voice + 0.1, plan
        assert plan["length"] >= max(screen, plan["at"] + voice) and plan["until"] + cut.EASE <= plan["length"], plan
        z = {"full": plan["full"], "until": plan["until"], "ease": cut.EASE}
        assert compose.zoom_at(plan["at"], z) == 1 and compose.zoom_at(plan["at"] + voice, z) == 1
        assert compose.zoom_at(plan["full"] - cut.EASE, z) == 0 and compose.zoom_at(plan["until"] + cut.EASE, z) == 0
        assert 0 < compose.zoom_at(plan["full"] - cut.EASE / 2, z) < 1


def test_a_beat_whose_subject_is_drawn_outside_its_zoom_is_refused():
    cut = _tool("cut")
    cut.check_subject("holds", [720, 100, 720, 450], [{"t": 1, "label": "subject", "rect": [1040, 330, 120, 30]}])
    with pytest.raises(SystemExit, match="not inside its zoom box"):
        cut.check_subject("holds", [720, 100, 720, 450], [{"t": 1, "label": "subject", "rect": [300, 330, 120, 30]}])


@_NEEDS_FFMPEG_PIL
def test_compose_crops_the_real_frames_to_the_zoom_eases_back_and_keeps_the_cursor(tmp_path):
    """The zoom is the walk's own frames cropped and scaled: whole before, the box alone while full, whole again after,
    the walk's cursor drawn on it; a cursor outside the box while zoomed is refused, nothing made."""
    from PIL import Image

    walk = tmp_path / "walk"
    (walk / "frames").mkdir(parents=True)
    (walk / "walk.json").write_text(json.dumps({"width": 400, "height": 250, "scale": 1, "frames": 1}))
    img = Image.new("RGB", (400, 250), (200, 0, 0))
    img.paste((0, 0, 200), (200, 0, 400, 125))  # the top right quarter, blue: the box
    img.save(walk / "frames" / "000001.png")
    (walk / "frames.jsonl").write_text('{"file": "frames/000001.png", "t": 0}\n')
    (walk / "cursor.jsonl").write_text('{"t": 0.05, "x": 300, "y": 50, "down": false}\n')
    (walk / "marks.jsonl").write_text('{"t": 0, "label": "beat a"}\n{"t": 0.2, "label": "end"}\n')
    compose = _tool("compose")
    zoom = {"box": [200, 0, 200, 125], "full": 0.6, "until": 1.2, "ease": 0.4, "least": 100}
    out = tmp_path / "z.mp4"
    compose.compose(walk, out, 30, None, None, None, zoom, 2.0)
    assert _tool("cut").duration(out) == pytest.approx(2.0, abs=0.1), "held to its length, so the zoom eases out on screen"
    def rgb(t):
        _frame(out, t, tmp_path / f"{t}.png")
        return Image.open(tmp_path / f"{t}.png").convert("RGB")

    before, full, after = rgb(0.05), rgb(0.9), rgb(1.9)
    assert before.getpixel((20, 200))[0] > 150 and before.getpixel((380, 20))[2] > 150, "the whole frame before"
    assert full.getpixel((20, 200))[2] > 150 and full.getpixel((20, 20))[2] > 150, "only the box while full"
    assert min(full.getpixel((207, 123))) > 180, "the cursor, drawn where the walk put it, inside the box, twice the size"
    assert after.getpixel((20, 200))[0] > 150, "the whole frame again after"
    (walk / "cursor.jsonl").write_text('{"t": 0.05, "x": 100, "y": 200, "down": false}\n')
    with pytest.raises(SystemExit, match="outside the zoom box"):
        compose.compose(walk, tmp_path / "no.mp4", 30, None, None, None, zoom, 2.0)
    assert not (tmp_path / "no.mp4").exists()


@_NEEDS_CHROME
def test_a_walks_mark_keeps_where_the_part_it_names_is_drawn(tmp_path):
    (tmp_path / "files").mkdir()
    (tmp_path / "files" / "p.html").write_text(
        "<!doctype html><body style='margin:0'><div style='position:absolute;left:500px;top:300px;width:120px;height:40px'>"
        "INT8 ResNet-50 top1 76.13</div></body>")
    done = _walk(tmp_path, [{"goto": "/files/p.html"}, {"mark": "subject", "rect": {"text": "INT8 ResNet-50 top1 76.13"}}])
    assert done.returncode == 0, done.stderr[-600:]
    m = [json.loads(l) for l in (tmp_path / "w" / "marks.jsonl").read_text().splitlines() if '"subject"' in l]
    assert m and m[0]["rect"] == [500, 300, 120, 40], m
    bad = _walk(tmp_path, [{"goto": "/files/p.html"}, {"mark": "subject", "rect": "#nothing"}])
    assert bad.returncode != 0 and "no element" in bad.stderr


@_NEEDS_CHROME
@_NEEDS_FFMPEG_PIL
@pytest.mark.skipif(not _cdnjs_reachable(), reason="term.html loads xterm.js from cdnjs")
def test_the_share_beat_films_the_watch_the_new_run_and_the_link_showing_it(tmp_path):
    """share_beat.mjs, against the local stand-in of the hosted service: rb sync --watch and the new run typed in the
    terminal, the share link in a fresh browser, and a segment as cut.py keeps one, whose page shows the new run only
    once the watch has sent it."""
    import shlex
    from rbtest import command

    standin = _tool("share_standin").StandIn()
    try:
        repo, bin_ = tmp_path / "int8-resnet50", tmp_path / "bin"
        repo.mkdir()
        bin_.mkdir()
        (bin_ / "rb").write_text("#!/bin/sh\nexec env RB_PYTHON=" + shlex.quote(sys.executable) + " "
                                 + " ".join(shlex.quote(c) for c in command()) + ' "$@"\n')
        (bin_ / "rb").chmod(0o755)
        (tmp_path / "gitconfig").write_text("[user]\n\temail = ana@example.com\n\tname = ana\n")
        env = {"HOME": str(tmp_path), "PATH": f"{bin_}:/usr/bin:/bin", "GIT_CONFIG_GLOBAL": str(tmp_path / "gitconfig"),
               "GIT_CONFIG_NOSYSTEM": "1", "RB_ACTOR": "human:ana", "RB_TELEMETRY": "0"}
        rb = lambda *argv: subprocess.run([str(bin_ / "rb"), *argv], cwd=repo, env=env, capture_output=True, text=True, timeout=120)
        subprocess.run(["git", "init", "-q"], cwd=repo, env=env, check=True)
        for argv in (["init", "INT8 ResNet-50", "--id", "int8-resnet50", "--no-agents"],
                     ["experiment", "add", "INT8 against FP32 on ImageNet", "--id", "int8", "--baseline", "fp32", "--candidate", "int8", "--varies", "precision"],
                     ["run", "add", "int8", "fp32.top1=76.13", "int8.top1=75.98"]):
            done = rb(*argv)
            assert done.returncode == 0, done.stderr
        beat = {**os.environ, "RB_HUB_TOKEN": "rb_demo_local", "RB_CONFIG_DIR": str(tmp_path / "cfg"), "RB_TEST_SYNC_PACE": "0.1",
                "GIT_CONFIG_GLOBAL": str(tmp_path / "gitconfig"), "GIT_CONFIG_NOSYSTEM": "1"}
        film = lambda link, out: subprocess.run(
            ["node", str(MAKE_STUDY.parent / "share_beat.mjs"), "--rb", str(bin_ / "rb"), "--record", str(repo), "--link", link,
             "--hub", standin.url, "--out", str(out), "--python", sys.executable, "--window", "4",
             "--run", "rb run add int8 fp32.top1=76.13 int8.top1=76.01"], env=beat, capture_output=True, text=True, timeout=400)
        # a link to a page that already says the count the new run makes, and never changes, is no beat: nothing is filmed
        (tmp_path / "static").mkdir()
        (tmp_path / "static" / "s.html").write_text("<!doctype html><p>INT8 ResNet-50 · 1 experiment · 2 recorded runs</p>")
        # nor is a page that says the count before the run and draws no inspector: the beat shows rb's three panes
        (tmp_path / "static" / "narrow.html").write_text("<!doctype html><p>INT8 ResNet-50 · 1 experiment · 1 recorded run</p>")
        import functools
        import http.server
        import threading
        still = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(tmp_path / "static")))
        threading.Thread(target=still.serve_forever, daemon=True).start()
        try:
            bad = film(f"http://127.0.0.1:{still.server_address[1]}/s.html", tmp_path / "bad")
            narrow = film(f"http://127.0.0.1:{still.server_address[1]}/narrow.html", tmp_path / "narrow")
        finally:
            still.shutdown()
        assert bad.returncode != 0 and 'never said "1 recorded run" without "2 recorded runs"' in bad.stderr, bad.stderr[-800:]
        assert not (tmp_path / "bad" / "screen.mp4").exists()
        assert narrow.returncode != 0 and "does not show rb's three panes before the beat" in narrow.stderr, narrow.stderr[-800:]
        assert "1440 CSS px wide, the inspector not drawn" in narrow.stderr and not (tmp_path / "narrow" / "screen.mp4").exists()
        assert rb("status", "--json").returncode == 0
        out = tmp_path / "seg"
        done = film(standin.link, out)
        assert done.returncode == 0, (done.stdout + done.stderr)[-2000:]
        seg = json.loads((out / "segment.json").read_text())
        assert (seg["runs_before"], seg["since"], seg["until"]) == (1, "1 recorded run", "2 recorded runs") and seg["syncs"] >= 2, seg
        # the link was filmed as the hub serves it, and the words read inside its frame, never the page around it
        assert seg["read_in"] and "page" not in seg["read_in"], seg
        assert seg["shown"] in ("shown live", "shown after a reload") and seg["speed"] >= 1
        # the frame is laid out wide enough for rb's three panes (open.css: from 1280 CSS px), and its inspector is drawn,
        # before the new run and once it shows; the page is scaled into its slot beside the terminal, never laid out narrow
        kept = json.loads((out / "page" / "walk" / "walk.json").read_text())["kept"]
        for when in ("before", "after"):
            assert kept[when]["width"] >= 1280 and kept[when]["inspector"] is True, kept
            assert seg["layout"][when] == kept[when]
        assert seg["page"]["css"][0] >= 1280 and seg["page"]["slot"] == [1200, 1200], seg["page"]
        # synced once before filming, then the watch's first upload and the one that sent the new run
        assert len(standin.got) >= 3, standin.got
        assert "2 recorded runs" not in standin.pages[0].decode() and "2 recorded runs" in standin.pages[-1].decode()
        typed = "".join(d for _, k, d in (json.loads(l) for l in (out / "files" / "share.cast").read_text().splitlines()[1:]) if k == "o")
        assert "rb sync --watch &" in typed and "rb run add int8 fp32.top1=76.13 int8.top1=76.01" in typed
        assert len(re.findall(r"Synced at .*privately", typed)) >= 2, typed[-800:]
        probe = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=width,height,r_frame_rate:format=duration",
                                           "-of", "json", str(out / "screen.mp4")], capture_output=True, text=True, check=True).stdout)
        s = probe["streams"][0]
        assert (s["width"], s["height"], s["r_frame_rate"]) == (1920, 1200, "30/1")
        assert float(probe["format"]["duration"]) == pytest.approx(seg["seconds"], abs=0.3)
        assert [json.loads(l)["label"] for l in (out / "screen.marks.jsonl").read_text().splitlines()] == ["beat share", "voice"]
    finally:
        standin.close()


def test_the_share_stand_in_serves_the_link_as_the_hub_does_its_bar_over_rbs_page_in_a_sandboxed_frame(tmp_path):
    """The hub shows a record at one address: opened as a page (the link, or its /read) it is the hub's own page, a bar
    over an iframe with no allow-same-origin; only the frame's own request (Sec-Fetch-Dest: iframe) gets rb's page."""
    import io
    import urllib.request
    import zipfile

    standin = _tool("share_standin").StandIn()
    try:
        page = b"<!doctype html><p>INT8 ResNet-50 \xc2\xb7 22 recorded runs</p>"
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("x/manifest.json", json.dumps({"project": {"id": "int8-resnet50", "title": "INT8 ResNet-50 <on ImageNet>"}}))
            z.writestr("x/page.html", page)
        urllib.request.urlopen(urllib.request.Request(standin.url + "/api/hub/investigations/int8-resnet50", data=buf.getvalue(), method="PUT"))
        get = lambda path, dest=None: urllib.request.urlopen(urllib.request.Request(
            standin.url + path, headers={"Sec-Fetch-Dest": dest} if dest else {})).read()
        for path in ("/s/demo", "/s/demo/read"):
            for dest in ("document", None):
                hub = get(path, dest).decode()
                assert ('<iframe src="/s/demo/read" sandbox="allow-scripts allow-popups allow-popups-to-escape-sandbox" '
                        'title="INT8 ResNet-50 &lt;on ImageNet&gt;" class="view-frame"></iframe>') in hub, hub
                assert "<b>rb.</b><b>INT8 ResNet-50 &lt;on ImageNet&gt;</b>" in hub
                assert "Shared, read-only" in hub and re.search(r"synced \d{4}-\d\d-\d\d \d\d:\d\d UTC", hub) and "Made with rb" in hub
                assert "recorded runs" not in hub
            assert get(path, "iframe") == page
        # one without data-shell goes as it came, with no policy: document mode, as the hub serves it
        plain = urllib.request.urlopen(urllib.request.Request(standin.url + "/s/demo/read", headers={"Sec-Fetch-Dest": "iframe"}))
        assert plain.read() == page and plain.headers["content-security-policy"] is None
        # a page rb export wrote names its shell script on its <html> (data-shell) and carries none: as the hub does, the
        # frame gets the host's copy of rb's script inline at the end of its <head>, run by its hash alone, no network
        import base64
        import hashlib
        script = (MAKE_STUDY.parents[2] / "rust" / "src" / "shell" / "assets" / "script.js").read_bytes()
        shell = b'<!doctype html><html data-shell="sha256:00" lang="en"><head><style>p{}</style></head><body><p>22 recorded runs</p></body></html>'
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("x/manifest.json", json.dumps({"project": {"id": "int8-resnet50", "title": "INT8 ResNet-50"}}))
            z.writestr("x/page.html", shell)
        urllib.request.urlopen(urllib.request.Request(standin.url + "/api/hub/investigations/int8-resnet50", data=buf.getvalue(), method="PUT"))
        framed = urllib.request.urlopen(urllib.request.Request(standin.url + "/s/demo/read", headers={"Sec-Fetch-Dest": "iframe"}))
        assert framed.read() == shell.replace(b"</head>", b"<script>" + script + b"</script></head>")
        digest = base64.b64encode(hashlib.sha256(script).digest()).decode()
        assert framed.headers["content-security-policy"] == (
            "sandbox allow-scripts allow-popups allow-popups-to-escape-sandbox; default-src 'none'; "
            f"script-src 'sha256-{digest}'; connect-src 'none'; style-src 'unsafe-inline'; img-src data:; "
            "frame-ancestors 'self'; base-uri 'none'; form-action 'none'")
    finally:
        standin.close()


def test_the_share_beats_words_are_the_run_count_rbs_page_header_draws():
    """The share beat's before and after words default to the runs as rb's page header counts them (strings.rs's
    RECORDED_RUNS_N), so a change of that wording is a change of the beat, never a beat that waits for words no page says."""
    shell = (Path(__file__).resolve().parents[1] / "rust" / "src" / "shell" / "strings.rs").read_text()
    drawn = re.search(r'pub const RECORDED_RUNS_N: \(&str, &str\) = \("([^"]+)", "([^"]+)"\);', shell)
    beat = (MAKE_STUDY.parent / "share_beat.mjs").read_text()
    used = re.search(r'const RECORDED_RUNS_N = \["([^"]+)", "([^"]+)"\];', beat)
    assert drawn and used and used.groups() == drawn.groups(), (drawn, used)
    assert "const count = n => `${n} ${RECORDED_RUNS_N[n === 1 ? 0 : 1]}`;" in beat
    # every check of the page's words reads them inside the hub's frame
    assert "{ waitFor: before_, frame: true," in beat and "{ reloadUntil: shows, frame: true," in beat


@_NEEDS_CHROME
def test_a_walk_reads_words_inside_a_pages_frame_in_its_own_process_or_the_pages(tmp_path):
    """A frame step reads inside the page's iframe: a sandboxed one (its origin opaque, so the page around it cannot
    read it, and Chrome may give it a process of its own) and a same-origin one alike; a page with no frame is read
    itself, and a plain step still reads the top page."""
    (tmp_path / "files").mkdir()
    (tmp_path / "files" / "inner.html").write_text("<!doctype html><body><p>INT8 ResNet-50 · 22 recorded runs</p></body>")
    for name, attrs in (("sandboxed", ' sandbox="allow-scripts allow-popups allow-popups-to-escape-sandbox"'), ("same", "")):
        (tmp_path / "files" / f"{name}.html").write_text(
            f'<!doctype html><body><div class="bar">Shared, read-only</div><iframe src="/files/inner.html"{attrs} class="view-frame"></iframe></body>')
    (tmp_path / "files" / "flat.html").write_text("<!doctype html><body><p>INT8 ResNet-50 · 22 recorded runs</p></body>")
    words = "document.body.innerText.includes('22 recorded runs')"
    seen = {}
    for name in ("sandboxed", "same", "flat"):
        done = _walk(tmp_path, [{"goto": f"/files/{name}.html"}, {"waitFor": words, "frame": True, "timeout": 10000},
                                {"print": "document.body.innerText", "frame": True}, {"print": "document.body.innerText"}])
        assert done.returncode == 0, done.stdout + done.stderr
        assert '"INT8 ResNet-50 · 22 recorded runs"' in done.stdout
        seen[name] = json.loads((tmp_path / "w" / "walk.json").read_text())["read_in"]
        if name != "flat":
            assert '"Shared, read-only"' in done.stdout  # the plain step: the page around the frame
    assert seen["sandboxed"] in (["frame process"], ["frame world"]) and seen["same"] == ["frame world"] and seen["flat"] == ["page"], seen
    # the page around a sandboxed frame never holds the frame's words: without frame, the wait is not met
    bad = _walk(tmp_path, [{"goto": "/files/sandboxed.html"}, {"waitFor": words, "timeout": 2000}])
    assert bad.returncode != 0 and "timed out waiting" in bad.stderr
    # a frame a reader cannot see is never read (one holding the count the beat waits for, beside the visible one):
    # hidden by display, by visibility, by opacity, off screen, clipped away by its parent, or covered
    (tmp_path / "files" / "other.html").write_text("<!doctype html><body><p>INT8 ResNet-50 · 23 recorded runs</p></body>")
    unseen = {"display": 'style="display:none"', "visibility": 'style="visibility:hidden"', "opacity": 'style="opacity:0"',
              "offscreen": 'style="position:absolute;left:-5000px"', "clipped": None, "covered": None}
    for how, attrs in unseen.items():
        hide = f'<iframe src="/files/other.html" {attrs}></iframe>'
        if how == "clipped":
            hide = '<div style="height:0;overflow:hidden"><iframe src="/files/other.html"></iframe></div>'
        if how == "covered":
            hide = ('<div style="position:relative;width:300px;height:150px"><iframe src="/files/other.html"></iframe>'
                    '<div style="position:absolute;inset:0;background:#fff"></div></div>')
        (tmp_path / "files" / "hidden.html").write_text(
            f'<!doctype html><body>{hide}<iframe src="/files/inner.html" sandbox="allow-scripts" class="view-frame"></iframe></body>')
        done = _walk(tmp_path, [{"goto": "/files/hidden.html"}, {"waitFor": words, "frame": True, "timeout": 10000},
                                {"print": "document.body.innerText", "frame": True}])
        assert done.returncode == 0 and '"INT8 ResNet-50 · 22 recorded runs"' in done.stdout, (how, done.stdout + done.stderr)
        # and alone on the page, it is no frame: the page is read itself, which never holds the frame's words
        (tmp_path / "files" / "alone.html").write_text(f"<!doctype html><body>{hide}</body>")
        alone = _walk(tmp_path, [{"goto": "/files/alone.html"}, {"wait": 800},
                                 {"waitFor": "document.body.innerText.includes('23 recorded runs')", "frame": True, "timeout": 2000}])
        assert alone.returncode != 0 and "timed out waiting" in alone.stderr, (how, alone.stdout)
    # read_in says where the read the step went by was made: a page whose frame comes late is polled before it, and
    # the beat's words were read in the frame all the same
    (tmp_path / "files" / "late.html").write_text(
        "<!doctype html><body><script>setTimeout(() => { const f = document.createElement('iframe');"
        "f.setAttribute('sandbox', 'allow-scripts'); f.src = '/files/inner.html'; document.body.append(f); }, 1500);</script></body>")
    late = _walk(tmp_path, [{"goto": "/files/late.html"}, {"waitFor": words, "frame": True, "timeout": 8000}])
    assert late.returncode == 0, late.stdout + late.stderr
    assert json.loads((tmp_path / "w" / "walk.json").read_text())["read_in"] == ["frame process"]
    # a frame that loads another page of its own (its world in the page's process goes with its document) is read
    # anew, and a whole page reloaded is read in its new frame: the reload's frame says the new count
    (tmp_path / "files" / "count.html").write_text(
        "<!doctype html><body><p id=p></p><script>const n = new URLSearchParams(location.search).get('runs') || '22';"
        "document.getElementById('p').textContent = 'INT8 ResNet-50 · ' + n + ' recorded runs';"
        "if (n === '22' && location.search.includes('next')) setTimeout(() => { location.search = '?runs=23'; }, 1500);</script></body>")
    (tmp_path / "files" / "moves.html").write_text('<!doctype html><body><iframe src="/files/count.html?next"></iframe></body>')
    moved = _walk(tmp_path, [{"goto": "/files/moves.html"}, {"waitFor": words, "frame": True, "timeout": 5000},
                             {"waitFor": "document.body.innerText.includes('23 recorded runs')", "frame": True, "timeout": 8000}])
    assert moved.returncode == 0, moved.stdout + moved.stderr
    assert json.loads((tmp_path / "w" / "walk.json").read_text())["read_in"] == ["frame world"]
    for sandbox in (' sandbox="allow-scripts"', ""):
        (tmp_path / "files" / "again.html").write_text(
            "<!doctype html><body><script>const n = Number(sessionStorage.n || 0); sessionStorage.n = n + 1;"
            f"document.write('<iframe{sandbox} src=\"/files/count.html?runs=' + (22 + n) + '\"></iframe>');</script></body>")
        again = _walk(tmp_path, [{"goto": "/files/again.html"}, {"waitFor": words, "frame": True, "timeout": 5000},
                                 {"reloadUntil": "document.body.innerText.includes('23 recorded runs')", "frame": True, "live": 1500, "timeout": 8000}])
        assert again.returncode == 0 and "shown after a reload" in again.stdout, again.stdout + again.stderr
        assert json.loads((tmp_path / "w" / "walk.json").read_text())["read_in"] == (["frame process"] if sandbox else ["frame world"])
    (tmp_path / "files" / "two.html").write_text('<!doctype html><body><iframe src="/files/other.html"></iframe><iframe src="/files/inner.html"></iframe></body>')
    two = _walk(tmp_path, [{"goto": "/files/two.html"}, {"waitFor": words, "frame": True, "timeout": 2000}])
    assert two.returncode != 0 and "2 visible frames match" in two.stderr, two.stderr


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="cut.py reads the segment with ffprobe")
def test_a_cut_takes_the_share_beat_from_where_share_beat_wrote_it_or_says_it_has_none(tmp_path):
    spec = json.loads((MAKE_STUDY.parent / "cut.json").read_text())
    share = next(s for s in spec["segments"] if s["id"] == "share")
    assert share.get("external") and share["steps"][0]["goto"].startswith("/demo/card.html?hold="), "a placeholder until it is captured"
    assert [s["id"] for s in spec["segments"]][-2:] == ["share", "end"]
    cast = tmp_path / "a.cast"
    cast.write_text('{"version": 2, "width": 80, "height": 24}\n')
    with pytest.raises(SystemExit, match="holds no screen.mp4"):
        _tool("cut").main(["--record", str(tmp_path), "--import-cast", str(cast), "--pain-cast", str(cast), "--rb-bin", str(tmp_path),
                           "--out", str(tmp_path / "o"), "--share-segment", str(tmp_path / "nothing")])


@_NEEDS_CHROME
def test_the_time_lapse_label_can_sit_in_the_windows_bar_so_a_zoom_on_the_window_keeps_it(tmp_path):
    (tmp_path / "files").mkdir()
    head = json.dumps({"version": 2, "width": 80, "height": 10})
    (tmp_path / "files" / "t.cast").write_text(head + "\n" + json.dumps([1.0, "o", "INT8 ResNet-50 top1 76.13\r\n"]) + "\n")
    where = ("(() => { const l = document.getElementById('lapse'), w = document.getElementById('win').getBoundingClientRect(),"
             " b = l.getBoundingClientRect(); return [l.parentElement.id, getComputedStyle(l).visibility, l.textContent,"
             " b.top >= w.top && b.bottom <= w.bottom && b.left >= w.left && b.right <= w.right]; })()")
    done = _walk(tmp_path, [{"goto": "/demo/term.html?cast=/files/t.cast&speed=4&lapse=bar"}, {"play": True}, {"print": where}])
    assert done.returncode == 0, done.stderr[-600:]
    parent, shown, text, inside = json.loads(next(l for l in done.stdout.splitlines() if l.startswith("[")))
    assert (parent, shown, inside) == ("bar", "visible", True) and text.startswith("time-lapse, 4×"), (parent, shown, text, inside)


# ---------------------------------------------------------------- the film, v2 (FILM-V2.md: film.json, log.html, captions)

FILM = MAKE_STUDY.parent / "film.json"


def test_the_film_tells_its_story_in_the_beats_film_v2_names_each_with_a_caption():
    """FILM-V2's ten beats and the pitch and the end, in order, every one with a caption (a muted film still tells the
    story), and no beat claims what the screen does not show."""
    spec = json.loads(FILM.read_text())
    segs = {s["id"]: s for s in spec["segments"]}
    assert [s["id"] for s in spec["segments"]] == ["pitch", "turns", "summary", "import", "living", "rows", "oneseed", "baseline",
                                                    "goalposts", "holds", "receipt", "call", "share", "end"]
    assert all(s.get("caption", "").strip() and s.get("voice", "").strip() for s in spec["segments"])
    assert segs["pitch"]["title"] == "Rabbit Brain" and segs["pitch"].get("blur")
    # the turns: the study's own log, lit turn by turn up to turn 10, then turn 10's own reply
    assert "/files/agent.log" in segs["turns"]["steps"][0]["goto"] and "turns=10" in segs["turns"]["steps"][0]["goto"]
    # turn 10's reply as the log numbers it (sed -n): its opening line to its last bullet, not the git commit after it
    assert "reply=2784-2790" in segs["summary"]["steps"][0]["goto"]
    # no time-lapse longer than 6 s: the paste at its own speed, the rest at 48x
    q = dict(kv.split("=", 1) for kv in segs["import"]["steps"][0]["goto"].split("?", 1)[1].split("&"))
    assert (float(q["to"]) - float(q["lapse_from"])) / float(q["speed"]) <= 6
    # the screen's own words, where the voice says them
    steps = lambda k: json.dumps(segs[k]["steps"], ensure_ascii=False)
    assert "90.48" in segs["baseline"]["voice"] and "91.25" in segs["baseline"]["voice"] and "li.qrow:nth-of-type(1)" in steps("baseline")
    assert "nine of my thirteen" in segs["goalposts"]["voice"] and "9 of 13 rules were written after their results came in." in steps("goalposts")
    assert "x-e-ptq" in steps("rows") and "x-e-scheme-s0" in steps("rows")
    assert "one seed" in segs["oneseed"]["voice"] and "c-c-s0-pc-1024:runs" in steps("oneseed") and "p.stand" in steps("oneseed")
    # the screen names two normalizations, not which is older; and nothing says a quarter of the size
    assert "older" not in segs["rows"]["voice"] + segs["rows"]["caption"]
    assert "quarter" not in segs["living"]["caption"]
    assert all("rb_open_today" not in json.dumps(s) for s in spec["segments"])
    assert "uv tool install rabbit-brain" in (MAKE_STUDY.parent / "card.html").read_text()


@pytest.mark.skipif(importlib.util.find_spec("PIL") is None, reason="compose.py draws with Pillow")
def test_a_caption_is_large_white_type_on_a_dark_band_and_the_title_reads_over_any_screen(tmp_path):
    from PIL import Image
    compose = _tool("compose")
    png = tmp_path / "w.png"
    compose.overlay(png, 1920, 1080, caption="INT8 ResNet-50 holds on human:ana's runs")
    img = Image.open(png).convert("RGBA")
    assert img.size == (1920, 1080) and img.getpixel((960, 200))[3] == 0, "the screen above the band is untouched"
    r, g, b, a = img.getpixel((10, 1075))
    assert a > 200 and max(r, g, b) < 40, "a dark band at the bottom"
    rows = [y for y in range(1080) if any(img.getpixel((x, y))[:3] == (255, 255, 255) for x in range(0, 1920, 2))]
    assert rows and rows[-1] - rows[0] >= 0.7 * 44, "type at least 44 px at 1080p (its capitals about 0.7 of that)"
    compose.overlay(png, 1920, 1200, title="Rabbit Brain")
    assert Image.open(png).convert("RGBA").getpixel((40, 40))[3] > 120, "a scrim behind the wordmark"


@pytest.mark.skipif(shutil.which("ffmpeg") is None or importlib.util.find_spec("PIL") is None, reason="cut.py needs ffmpeg and Pillow")
def test_a_segment_carries_its_caption_on_every_frame_and_its_blur(tmp_path):
    from PIL import Image
    cut, compose = _tool("cut"), _tool("compose")
    video = tmp_path / "screen.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=white:size=1920x1200:rate=30", "-t", "1",
                    "-vf", "drawbox=x=960:y=0:w=960:h=1200:color=black:t=fill", "-pix_fmt", "yuv420p", str(video)], check=True)
    png = tmp_path / "words.png"
    compose.overlay(png, 1920, 1200, caption="INT8 ResNet-50: what holds")
    for blur, out in ((0, tmp_path / "sharp.mp4"), (12, tmp_path / "soft.mp4")):
        cut.fit(video, None, out, overlay=png, blur=blur)
        for at in (0.05, 0.9):
            f = _frame(out, at, tmp_path / "f.png")
            assert f.getpixel((300, 1190)) < 40 and f.getpixel((300, 300)) > 215, (blur, at, "the band on every frame, the screen above it")
        edge = Image.open(tmp_path / "f.png").convert("L").getpixel((950, 300))
        assert (edge > 230) if not blur else (60 < edge < 225), (blur, edge)


@_NEEDS_CHROME
def test_the_log_page_lights_each_user_turn_counts_it_and_fills_the_window_with_a_reply(tmp_path):
    (tmp_path / "files").mkdir()
    lines = ["# the agent's log"]
    for k in range(1, 4):
        lines += [f"2026-10-0{k} 01:00:00  user  Turn {k}: evaluate INT8 ResNet-50 on ImageNet."]
        lines += [f"2026-10-0{k} 01:00:0{j}  agent reply {k}.{j}" for j in range(1, 8)]
    (tmp_path / "files" / "agent.log").write_text("\n".join(lines) + "\n")
    lit = "[...document.querySelectorAll('.l.lit')].map(e => e.textContent).join('|') + ' / ' + document.getElementById('turn').textContent"
    done = _walk(tmp_path, [{"goto": "/demo/log.html?log=/files/agent.log&beat=turns&turns=3&travel=60&hold=60"}, {"play": True}, {"print": lit}])
    assert done.returncode == 0, done.stderr[-600:]
    assert "Turn 3: evaluate" in done.stdout and "/ turn 3" in done.stdout and "Turn 2" not in done.stdout, done.stdout
    # turn 2's reply is lines 11 to 17 (as sed -n counts them): they are what the summary shows, the rest faded
    done = _walk(tmp_path, [{"goto": "/demo/log.html?log=/files/agent.log&beat=summary&turns=2&reply=11-17&first=50&travel=50"}, {"play": True},
                            {"print": lit + " + ' / ' + document.getElementById('log').className + ' / ' + "
                                      "[...document.querySelectorAll('.l.reply')].map(e => e.dataset.n).join(',')"}])
    assert done.returncode == 0, done.stderr[-600:]
    assert "Turn 2: evaluate" in done.stdout and "/ turn 2 / only / 11,12,13,14,15,16,17" in done.stdout, done.stdout


@_NEEDS_CHROME
@pytest.mark.skipif(not _cdnjs_reachable(), reason="term.html loads xterm.js from cdnjs")
def test_a_cast_plays_at_its_own_speed_until_lapse_from_and_is_labelled_a_time_lapse_only_after(tmp_path):
    (tmp_path / "files").mkdir()
    events = [json.dumps([float(t), "o", f"step {t}\r\n"]) for t in range(1, 11)]
    (tmp_path / "files" / "c.cast").write_text(json.dumps({"version": 2, "width": 80, "height": 24}) + "\n" + "\n".join(events) + "\n")
    done = _walk(tmp_path, [{"goto": "/demo/term.html?cast=/files/c.cast&speed=10&lapse_from=4"},
                            {"waitFor": "window.__ready === true"},
                            {"print": "window.__length.toFixed(2) + ' ' + getComputedStyle(document.getElementById('lapse')).visibility"},
                            {"play": True}, {"print": "document.getElementById('lapse').textContent"}])
    assert done.returncode == 0, done.stderr[-600:]
    assert "4.60 hidden" in done.stdout, "4 s at its own speed, then 6 s at 10x, unlabelled until the lapse: " + done.stdout
    assert "time-lapse, 10×" in done.stdout


def test_the_films_words_are_the_plain_words_the_shell_draws():
    """Each beat that names a screen's words (the integrity line it points at, "can't tell yet" for a rule one seed
    leaves open, "The record will say:" on the call's card) uses the words the shell's own code draws, so the voice
    and the step's selector follow the screens' plain-words pass."""
    shell = Path(__file__).resolve().parents[1] / "rust" / "src" / "shell"
    drawn = "".join(p.read_text() for p in sorted(shell.glob("*.rs")))
    segs = {s["id"]: s for s in json.loads((MAKE_STUDY.parent / "film.json").read_text())["segments"]}
    pointed = json.dumps(segs["goalposts"]["steps"])
    assert "9 of 13 rules were written after their results came in." in pointed
    assert "rules were written after their results came in." in drawn and "criteria were declared after" not in pointed
    assert "we can't tell yet" in segs["oneseed"]["voice"] and "undetermined" not in segs["oneseed"]["voice"]
    assert "\"can't tell yet\"" in drawn
    # while the line is said the pointer rests on the rule's own "can't tell yet", and the zoom keeps it in frame
    st = segs["oneseed"]["steps"]
    chip = "main .view.on a.evs[href='#c-c-s0-pc-1024:rule']"
    v = st.index({"mark": "voice"})
    assert [s.get("move") for s in st[:v] if s.get("move")][-1] == chip and {"mark": "subject", "rect": chip} in st
    # the word's hint opens under it (to about y 560 on take 4's record): the zoom box reaches past it
    x, y, w, h = segs["oneseed"]["zoom"]["box"]
    assert y + h >= 600 and abs(w / h - 1440 / 900) < 0.01
    assert "aside.insp p.willread" in json.dumps(segs["call"]["steps"]) and "The record will say: " in drawn


@pytest.mark.skipif(shutil.which("ffmpeg") is None or importlib.util.find_spec("PIL") is None, reason="cut.py needs ffmpeg and Pillow")
def test_the_film_needs_no_pain_cast_reads_the_records_agent_log_and_burns_in_each_caption(tmp_path):
    fake = tmp_path / "bin" / "rb"
    fake.parent.mkdir()
    fake.write_text("#!/usr/bin/env python3\nimport http.server, sys\nclass H(http.server.BaseHTTPRequestHandler):\n"
                    "    def do_GET(self):\n        self.send_response(200); self.end_headers(); self.wfile.write(b'ok')\n"
                    "    def log_message(self, *a): pass\nhttp.server.HTTPServer(('127.0.0.1', int(sys.argv[3])), H).serve_forever()\n")
    fake.chmod(0o755)
    record = tmp_path / "int8-resnet"
    (record / ".rb").mkdir(parents=True)
    (record / "logs").mkdir()
    (record / "logs" / "agent.log").write_text("2026-10-05 01:10:00  user  Evaluate INT8 ResNet-50 on ImageNet.\n")
    (tmp_path / "import.cast").write_text("{}\n")
    seg = tmp_path / "captured"
    seg.mkdir()
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=white:size=1920x1200:rate=30", "-t", "1",
                    "-pix_fmt", "yuv420p", str(seg / "screen.mp4")], check=True)
    spec = tmp_path / "film.json"
    spec.write_text(json.dumps({"width": 1440, "height": 900, "scale": 2, "segments": [
        {"id": "share", "beat": "10 the share", "caption": "Live as the work goes on", "voice": "x", "external": True, "steps": [{"wait": 1}]}]}))
    out = tmp_path / "out"
    assert _tool("cut").main(["--record", str(record), "--import-cast", str(tmp_path / "import.cast"), "--rb-bin", str(fake.parent),
                              "--out", str(out), "--work", str(tmp_path / "w"), "--spec", str(spec), "--share-segment", str(seg)]) == 0
    assert (out / "files" / "agent.log").read_text() == (record / "logs" / "agent.log").read_text()
    assert json.loads((out / "cut.timing.json").read_text())["segments"][0]["caption"] == "Live as the work goes on"
    f = _frame(out / "cut.mp4", 0.5, tmp_path / "f.png")
    assert f.getpixel((300, 1190)) < 40 and f.getpixel((300, 300)) > 215, "the caption's band over the captured screen"


def test_the_part_a_voice_speaks_of_must_be_clear_of_its_captions_band():
    cut = _tool("cut")
    sha = [{"t": 1, "label": "subject", "rect": [760, 720, 400, 30]}]  # a receipt's sha256 line near the box's foot
    cut.check_subject("receipt", [720, 340, 720, 450], sha)
    with pytest.raises(SystemExit, match="under its caption's band"):
        cut.check_subject("receipt", [720, 340, 720, 450], sha, band=0.1)
    cut.check_subject("receipt", [720, 340, 720, 450], [{"t": 1, "label": "subject", "rect": [760, 600, 400, 30]}], band=0.1)


@pytest.mark.skipif(importlib.util.find_spec("PIL") is None, reason="compose.py draws with Pillow")
def test_a_caption_too_long_for_two_lines_is_refused_and_a_blank_one_draws_nothing(tmp_path):
    from PIL import Image
    compose = _tool("compose")
    with pytest.raises(SystemExit, match="does not fit two lines"):
        compose.overlay(tmp_path / "w.png", 1920, 1200, caption="INT8 ResNet-50 on ImageNet holds within a point " * 4)
    with pytest.raises(SystemExit, match="does not fit two lines"):
        compose.overlay(tmp_path / "w.png", 1920, 1200, caption="INT8" * 60)
    assert compose.overlay(tmp_path / "w.png", 1920, 1200, caption="   ") == 0
    assert Image.open(tmp_path / "w.png").convert("RGBA").getpixel((10, 1195))[3] == 0


def test_a_cut_refuses_a_segment_whose_file_is_not_there_before_it_films(tmp_path):
    record = tmp_path / "int8-resnet"
    (record / ".rb").mkdir(parents=True)
    (tmp_path / "import.cast").write_text("{}\n")
    spec = tmp_path / "s.json"
    base = ["--record", str(record), "--import-cast", str(tmp_path / "import.cast"), "--rb-bin", str(tmp_path / "no-rb"),
            "--out", str(tmp_path / "out"), "--work", str(tmp_path / "w"), "--spec", str(spec)]
    for name in ("pain.cast", "agent.log"):
        spec.write_text(json.dumps({"width": 1440, "height": 900, "scale": 2, "segments": [
            {"id": "a", "beat": "a", "voice": "x", "steps": [{"goto": f"/demo/term.html?cast=/files/{name}&speed=1"}]}]}))
        with pytest.raises(SystemExit, match=f"plays /files/{name}, and there is none"):
            _tool("cut").main(base)


# ---- the agent beats (follow-up 14): the agent's terminal in front of rb open's live page, on one clock

@_NEEDS_CHROME
def test_a_walk_watches_a_live_page_and_marks_each_swap_with_the_words_it_newly_shows(tmp_path):
    """rb open's live module swaps a new version of the page in and says so (rb:swapped): each swap is a change marker
    with the words that were not there before and where they are drawn. Words only moved by another part, a clock line
    (ignored) and text changed with no swap are no change. The page is filmed at 1440 CSS px into 1920 px, at a scale
    as a script writes it (1.3333333), whose frame size is no whole number until the walk rounds it."""
    (tmp_path / "files").mkdir()
    (tmp_path / "files" / "p.html").write_text("""<!doctype html><body style='margin:0'>
<div id='main'><p id='runs'>9 recorded runs</p><p id='a'>INT8 ResNet-50</p><p id='b'>c1 not met</p><p>Review c4</p>
<p data-live='scope'>looked at 07:30</p></div>
<div id='nav' style='position:absolute;top:0;right:0'><span>Runs</span> <span>10</span></div>
<script>
setTimeout(() => {
  const m = document.getElementById('main');
  m.querySelector('[data-live]').textContent = 'looked at 07:31';
  document.getElementById('b').textContent = 'unswapped words';
}, 300);
setTimeout(() => {
  const m = document.getElementById('main');
  m.replaceChildren(...new DOMParser().parseFromString(
    "<div><p id='b'>c1 not met</p><p id='runs'>10 recorded runs</p><p id='a'>INT8 ResNet-50</p><p>10</p><p data-live='scope'>looked at 07:32</p></div>",
    "text/html").body.firstChild.childNodes);
  document.dispatchEvent(new CustomEvent('rb:swapped', { cancelable: true }));
}, 900);
</script></body>""")
    until = tmp_path / "done"
    (tmp_path / "beats.json").write_text(json.dumps({"width": 1440, "height": 900, "scale": 1.3333333, "beats": [{"name": "t", "steps": [
        {"goto": "/files/p.html", "settle": 0}, {"watch": "#nav, #main", "ignore": "[data-live]", "until": str(until), "settle": 200}]}]}))
    walk = subprocess.Popen(["node", str(MAKE_STUDY.parent / "walk.mjs"), "--script", str(tmp_path / "beats.json"), "--out", str(tmp_path / "w"),
                             "--serve", str(tmp_path / "files")], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    _end_watch(tmp_path / "w", until, 1)
    out, err = walk.communicate(timeout=60)
    assert walk.returncode == 0, out + err
    changes = [json.loads(l) for l in (tmp_path / "w" / "marks.jsonl").read_text().splitlines() if '"change"' in l]
    # "10" was in the nav already: the pane's new "10" is new all the same, and drawn where the pane draws it; the
    # words gone are boxed where they were
    assert [(c["words"], c["gone"]) for c in changes] == [(["10 recorded runs", "10"], ["9 recorded runs", "Review c4"])], changes
    (x, y, w, h), (x2, y2, _, _), (_, was, _, _), (_, y4, _, _) = changes[0]["boxes"]
    assert 0 <= x < 200 and 30 < y < 140 and 50 < w < 400 and 0 < h < 40, changes[0]["boxes"]
    assert x2 < 200 and y < y2 and y2 > 100 and was < y and y4 > 100, changes[0]["boxes"]
    assert json.loads((tmp_path / "w" / "walk.json").read_text())["frames"] > 0


@_NEEDS_CHROME
def test_a_walk_keeps_the_header_row_of_a_table_whose_cell_changes(tmp_path):
    """A change in a table's cell keeps the table's header row (`near`), where a viewer reads what the cell is; a change
    outside any table keeps none."""
    (tmp_path / "files").mkdir()
    (tmp_path / "files" / "p.html").write_text("""<!doctype html><body style='margin:0'><div id='main'>
<p id='n'>9 runs</p><table id='grid' style='margin-top:200px'><thead><tr><th>INT8 ResNet-50 · top-1</th><th>FP32</th></tr></thead>
<tbody><tr><td id='cell'>Not run</td><td>76.13</td></tr></tbody></table></div>
<script>
setTimeout(() => { document.getElementById('cell').textContent = '75.88 · 3 seeds';
  document.dispatchEvent(new CustomEvent('rb:swapped')); }, 500);
setTimeout(() => { document.getElementById('n').textContent = '12 runs';
  document.dispatchEvent(new CustomEvent('rb:swapped')); }, 1500);
</script></body>""")
    until = tmp_path / "done"
    (tmp_path / "beats.json").write_text(json.dumps({"width": 800, "height": 600, "scale": 1, "beats": [{"name": "t", "steps": [
        {"goto": "/files/p.html", "settle": 0}, {"watch": "#main", "until": str(until), "settle": 100}]}]}))
    walk = subprocess.Popen(["node", str(MAKE_STUDY.parent / "walk.mjs"), "--script", str(tmp_path / "beats.json"), "--out", str(tmp_path / "w"),
                             "--serve", str(tmp_path / "files")], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    _end_watch(tmp_path / "w", until, 2)
    out, err = walk.communicate(timeout=60)
    assert walk.returncode == 0, out + err
    changes = [json.loads(l) for l in (tmp_path / "w" / "marks.jsonl").read_text().splitlines() if '"change"' in l]
    assert [c["words"] for c in changes] == [["75.88 · 3 seeds"], ["12 runs"]], changes
    (head,), cell = changes[0]["near"], changes[0]["boxes"][0]
    assert head[1] + head[3] <= cell[1] and head[1] > 150 and head[2] > cell[2], (head, cell)
    assert changes[1]["near"] == []


def test_the_agents_terminal_goes_where_no_change_is_and_a_moment_nothing_clears_is_refused():
    cut = _tool("agent_cut")
    size = (691, 520)
    assert cut.window_for([], size)[0] == "bottom right"
    # a change under the bottom right corner: the window goes bottom left; the clock badge beside it counts too
    br = cut.place("bottom right", size)
    assert cut.window_for([[br[0] + 100, br[1] + 100, 80, 20]], size)[0] == "bottom left"
    assert cut.window_for([[br[0] - 120, br[1] + 10, 60, 20]], size)[0] == "bottom left"
    assert cut.window_for([[br[0] - 120, br[1] + 10, 60, 20]], size, badge=False)[0] == "bottom right"
    # a count at the right edge, just above the window: the window is pulled in from the edge
    assert cut.window_for([[1849, br[1] - 20, 10, 20], [400, 878, 340, 21]], size)[0] == "bottom right, inset"
    # a grid row just above a bottom window is no longer under its clock
    assert cut.window_for([[br[0] + 100, br[1] - 50, 80, 20]], size)[0] == "bottom right"
    # the window stays where the moment before put it while that place is clear
    assert cut.window_for([], size, prefer="bottom left")[0] == "bottom left"
    assert cut.window_for([[40, 1100, 300, 20]], size, prefer="bottom left")[0] == "bottom right"
    # a box off the screen is not on it
    assert cut.window_for([[br[0] + 100, 5000, 80, 20]], size)[0] == "bottom right"
    everywhere = [cut.place(p, size) for p in cut.PLACES]
    with pytest.raises(SystemExit, match="no place for the terminal is clear"):
        cut.window_for(everywhere, size)
    # take 4's e1 view: the grid's last row just under the top right window, the rest of the frame changing too; the
    # window goes top right a step smaller, never over it
    tr = cut.place("top right", size)
    row = [tr[0] + 390, tr[1] + size[1] + 3, 55, 19]
    rest = [[0, 900, 1920, 20], [40, 120, 300, 20]]
    name, win = cut.window_for(rest + [row], size)
    assert name == "top right" and win[2] < size[0] and win[3] < size[1], (name, win)
    assert not cut.overlaps(cut.with_badge(win), row, cut.PAD)
    assert cut.window_for(rest, size) == ("top right", tr)
    # a top window's clock goes beside it, never under it: left of one on the right, right of one on the left
    bx, by = cut.badge_at(tr, 160)
    assert by == tr[1] and bx + 160 + 10 == tr[0]
    tl = cut.place("top left", size)
    assert cut.badge_at(tl, 160) == (tl[0] + tl[2] + 10, tl[1])
    assert cut.badge_at(br, 160) == (br[0] - 170, br[1])
    under = [tr[0] + 100, tr[1] + size[1] + 20, 80, 20]   # where the badge sat before: now clear of a full-size window
    assert not cut.overlaps(cut.with_badge(tr), under, 0)
    # take 4 at +65 min: the grid's changed cell on the left, the runs table's new column under the bottom places, and
    # the grid's header row (what the viewer reads the cell by) just under a full-size top right window: kept clear of
    # the header row too, the window goes a step smaller; so does a 16-row terminal's, its shadow short of the row
    cell, column = [625, 681, 360, 50], [1620, 910, 110, 290]
    head = [405, tr[1] + size[1] + 5, 1455, 40]
    assert cut.window_for([cell, column], size)[0] == "top right"
    name, win = cut.window_for([cell, column], size, near=[head])
    assert name == "top right" and win[3] < size[1] and not cut.overlaps(cut.with_badge(win), head, cut.NEAR_PAD), (name, win)
    short = (691, 382)
    head16 = [405, 497, 1453, 45]   # f43e92d's e1 view: the grid's thead at CSS (304, 373, 1090, 34), at 4/3
    # a step smaller, its shadow (SHADOW) short of the header row
    small = (round(short[0] * 0.95), round(short[1] * 0.95))
    runs_head = [405, 900, 1453, 40]   # the runs table's header row: its new column is a change too
    assert cut.window_for([cell, column], short, near=[head16, runs_head]) == ("top right", cut.place("top right", small))
    assert cut.place("top right", small)[1] + small[1] + cut.SHADOW <= head16[1] and cut.NEAR_PAD >= cut.SHADOW
    # the take's terminal is that short by default
    helped = subprocess.run([sys.executable, str(MAKE_STUDY.parent / "agent_take.py"), "--help"], capture_output=True, text=True)
    assert "(default 16)" in " ".join(helped.stdout.split()), helped.stdout
    assert cut.term_window(64, 16)[1] * size[0] / cut.term_window(64, 16)[0] <= short[1] + 1
    seg = {"moments": [{"cut": [0, 3], "clock": "+65 min", "window": tr, "boxes": [], "near": [head]}]}
    assert cut.check(seg) and "header row" in cut.check(seg)[0]
    seg = {"moments": [{"cut": [0, 3], "clock": "+12 min", "window": br, "badge": "bar", "boxes": [[br[0] + 5, br[1] + 5, 10, 10]]}]}
    assert cut.check(seg) and "covers" in cut.check(seg)[0]
    seg["moments"][0]["boxes"] = [[40, 100, 300, 20]]
    assert cut.check(seg) == []


def test_the_agent_segment_cuts_to_the_moments_the_page_changed_with_a_clock_and_no_blur():
    """The line said, then the page's changes (close ones one moment, at the last), the most telling three in time
    order, each at its own speed with the time since the line ("+12 min"), none past the session's end; each moment's
    window is clear of every change it shows, a jump showing all the page changed since the moment before it."""
    cut = _tool("agent_cut")
    take = {"page": {"t0_wall": 1000.0, "css": [1440, 900]}}
    marks = {"wall0": 1010.0, "marks": {"said": 5.0, "done": 2000.0}}   # said at wall 1015, done at 3010
    ch = lambda t, words, box=(320, 100, 120, 18), gone=(): {"t": t, "label": "change", "words": words, "gone": list(gone), "boxes": [list(box)]}
    changes = [ch(5, ["before the line"]), ch(15 + 300, ["10 recorded runs"]), ch(15 + 305, ["Runs 10"]),
               ch(15 + 720, ["11 recorded runs", "Runs 11", "c4 2 runs"]), ch(15 + 1500, ["12 recorded runs", "Runs 12"]),
               ch(15 + 1900, ["changed just now"]), ch(15 + 1925, [], gone=["Review c4"]),
               ch(15 + 1000, ["x"], box=(1100, 700, 300, 150)), ch(15 + 2100, ["after the session", "a", "b", "c"])]
    moments = cut.plan_agent(take, changes, marks, count=3, size=(691, 520))
    assert [m["clock"] for m in moments] == [None, "+5 min", "+12 min", "+25 min"]
    assert moments[0]["wall"][0] < 1015 < moments[0]["wall"][1] and moments[0]["words"] == []
    assert moments[1]["words"] == ["10 recorded runs", "Runs 10"] and moments[1]["wall"][0] < 1000 + 320 < moments[1]["wall"][1]
    # the jump to +25 min shows the change at +16 min too: its box is the window's to keep clear
    assert "x" in moments[3]["words"] and moments[3]["place"] == "bottom left"
    for m in moments:
        assert m["wall"][1] - m["wall"][0] < 5 and m["wall"][1] <= 3010
    seg = {"moments": [dict(m, cut=[0, 1], badge="bar") for m in moments]}
    assert cut.check(seg) == [] and cut.check_marks(seg, changes, 1000.0, 4 / 3) == []
    # a change while the line is said moves the opening's window; a word only gone counts as a change
    early = changes + [ch(17, ["Found in the repository 1"], box=(1000, 800, 200, 30))]
    assert cut.plan_agent(take, early, marks, count=3, size=(691, 520))[0]["place"] == "bottom left"
    gone = cut.plan_agent(take, changes, {"wall0": 1010.0, "marks": {"said": 5.0, "done": 1960.0}}, count=6, size=(691, 520))
    assert gone[-1]["gone"] == ["Review c4"] and gone[-1]["wall"][1] <= 2970
    # the cut reads the take's own markers again: a window over one of them is caught
    seg["moments"][2]["window"] = cut.place("bottom right", (691, 520))
    seg["moments"][2]["boxes"] = []
    seg["moments"][3]["window"] = cut.place("bottom right", (691, 520))
    assert any("(x)" in p for p in cut.check_marks(seg, changes, 1000.0, 4 / 3))
    assert cut.SHADOW <= cut.PAD
    # a swap that only moved rb open's relative clock is no moment
    clocked = changes + [ch(15 + 1200, ["·", "16 minutes ago"], gone=["just now"]) for _ in range(3)]
    assert [m["clock"] for m in cut.plan_agent(take, clocked, marks, count=3, size=(691, 520))] == [None, "+5 min", "+12 min", "+25 min"]
    assert [cut.clock(s, True) for s in (20, 89, 90, 720)] == ["+0:20", "+1:29", "+2 min", "+12 min"]
    # after the agent segment, the fresh opening is a jump: what the page changed in between is kept clear too
    fm = {"wall0": 1000.0, "marks": {"said": 10.0, "done": 80.0}, "cols": 64, "rows": 22}
    ev = [[11, "o", "thinking"], [40, "o", "the answer"], [79, "i", "/exit"]]
    jumped = cut.plan_fresh(take, [ch(-800, ["Your call 3"], box=(1000, 800, 200, 30))], fm, ev, after=150.0)
    assert jumped[0]["place"] == "bottom left"
    # a take filmed on another view has no marks for the Overview: --fresh-place keeps the window where it is asked,
    # both moments, while that is clear, and goes elsewhere where a change is under it
    asked = cut.plan_fresh(take, [], fm, ev, after=150.0, place="top right")
    assert [m["place"] for m in asked] == ["top right", "top right"]
    tr = cut.place("top right", (691, 520))
    under = cut.plan_fresh(take, [ch(-800, ["Your call 3"], box=[(v + 20) * 3 / 4 for v in tr[:2]] + [40, 15])], fm, ev, after=150.0, place="top right")
    assert under[0]["place"] != "top right"
    # a session cut short has no done marker: it ends at its last output
    assert cut.ends({"marks": {"said": 5.0}}, [[1, "o", "x"], [40, "o", "y"], [50, "i", "z"]]) == 40


def test_the_agent_segment_keeps_each_changed_tables_header_row_clear_in_every_moment():
    """A change's `near` boxes (the header row of the table its cell is in) are carried into each moment that shows it,
    kept clear there and in a fresh opening's jump, read again by check_marks, and written in segment.json's moments."""
    cut = _tool("agent_cut")
    take = {"page": {"t0_wall": 1000.0, "css": [1440, 900]}}
    marks = {"wall0": 1010.0, "marks": {"said": 5.0, "done": 2000.0}}
    br = cut.place("bottom right", (691, 520))
    head = [(v * 3 / 4) for v in (br[0] - 200, br[1] + 100, 900, 40)]   # under the bottom right window, CSS px
    change = {"t": 15 + 300, "label": "change", "words": ["36.27 · 3 seeds"], "gone": ["Not run"], "boxes": [[320, 100, 120, 18]], "near": [head]}
    moments = cut.plan_agent(take, [change], marks, count=3, size=(691, 520))
    assert moments[1]["near"] == [cut.scale_box(head, 4 / 3)] and moments[1]["place"] != "bottom right"
    seg = {"moments": [dict(m, cut=[0, 1], badge="bar") for m in moments]}
    assert cut.check(seg) == [] and cut.check_marks(seg, [change], 1000.0, 4 / 3) == []
    seg["moments"][1]["window"] = br
    assert any("header row" in p for p in cut.check_marks(seg, [change], 1000.0, 4 / 3))
    assert any("header row" in p for p in cut.check(seg))
    fm = {"wall0": 1000.0, "marks": {"said": 10.0, "done": 80.0}, "cols": 64, "rows": 22}
    ev = [[11, "o", "thinking"], [40, "o", "the answer"], [79, "i", "/exit"]]
    jumped = cut.plan_fresh(take, [dict(change, t=-800)], fm, ev, after=150.0, place="bottom right")
    assert jumped[0]["near"] and jumped[0]["place"] != "bottom right"


@pytest.mark.skipif(importlib.util.find_spec("pyte") is None, reason="the fresh answer's words are read off its screen with pyte")
def test_the_fresh_answers_moment_ends_while_its_screen_shows_the_words_the_beat_is_about():
    cut = _tool("agent_cut")
    take = {"page": {"t0_wall": 1000.0, "css": [1440, 900]}}
    # at most 2.5 s after they first show
    fm = {"wall0": 1000.0, "marks": {"said": 10.0, "done": 80.0}, "cols": 64, "rows": 22}
    ev = [[11, "o", "thinking"], [40, "o", "\r\n\x1b[2Ccriterion\x1b[13Gc-ref\x1b[K is far off"], [60, "o", "\r\nStill to do"], [79, "i", "/exit"]]
    tail, at = cut.plan_fresh(take, [], fm, ev), cut.plan_fresh(take, [], fm, ev, words="criterion c-ref")
    assert tail[1]["wall"][1] == 1060.8 and at[1]["wall"][1] == 1042.5 and at[1]["clock"] == "+0:30"
    # words a long answer scrolls away a second later: the moment ends as they go
    gone = ev[:2] + [[41, "o", "\r\n" * 30]] + ev[2:]
    assert cut.plan_fresh(take, [], fm, gone, words="criterion c-ref")[1]["wall"][1] == 1041.0
    # drawn in two chunks, the words are on the screen all the same; in no chunk and no screen, they are refused
    split = [[11, "o", "x"], [40, "o", "\r\ncriterion "], [40.2, "o", "c-ref is far off"]]
    assert cut.plan_fresh(take, [], fm, split, words="criterion c-ref")[1]["clock"] == "+0:30"
    with pytest.raises(SystemExit, match="never shows"):
        cut.plan_fresh(take, [], fm, ev, words="c1 is not met")


def test_an_agent_beat_never_clears_the_record_or_rb_and_never_films_a_port_another_page_answers(tmp_path):
    record = tmp_path / "math-prompts"
    (record / ".rb").mkdir(parents=True)
    rb = tmp_path / "rb"
    rb.write_text("#!/bin/sh\n")
    rb.chmod(0o755)
    base = ["node", str(MAKE_STUDY.parent / "agent_beat.mjs"), "--rb", str(rb), "--record", str(record), "--task", "Run worked examples."]
    for out, work in ((tmp_path, tmp_path / "w"), (record / "take", tmp_path / "w"), (tmp_path / "take", tmp_path)):
        r = subprocess.run(base + ["--out", str(out), "--work", str(work)], capture_output=True, text=True, timeout=60)
        assert r.returncode == 2 and ("holds the record or rb" in r.stderr), r.stderr
        assert (record / ".rb").is_dir() and rb.exists()
    import http.server
    import threading
    srv = http.server.HTTPServer(("127.0.0.1", 0), http.server.SimpleHTTPRequestHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        r = subprocess.run(base + ["--out", str(tmp_path / "take"), "--work", str(tmp_path / "w"), "--port", str(srv.server_address[1])],
                           capture_output=True, text=True, timeout=60)
    finally:
        srv.shutdown()
    assert r.returncode == 2 and "answers at" in r.stderr and "already" in r.stderr, r.stderr
    assert not (tmp_path / "w").exists()


@pytest.mark.skipif(importlib.util.find_spec("pexpect") is None or importlib.util.find_spec("pyte") is None,
                    reason="the session driver needs pexpect and pyte")
def test_the_agent_session_is_told_one_line_and_ends_once_quiet_with_the_runs_recorded(tmp_path):
    """A stand-in for Claude Code: it waits at its prompt, takes the line, works (its busy footer, cut by a narrow
    window) and records two runs. The driver types only the line and /exit, and ends once the runs are there and the
    session has gone quiet; asked for more runs than come, it ends at its limit and says so."""
    record = tmp_path / "math-prompts"
    (record / ".rb" / "experiments" / "e1" / "runs").mkdir(parents=True)
    fake = tmp_path / "bin"
    fake.mkdir()
    (fake / "claude").write_text("""#!/bin/bash
echo '? for shortcuts'
IFS= read -r line
printf '%s\\n' "$line" > said.txt
printf '\\e[24;1Hworking (esc to inte'; sleep 3
touch .rb/experiments/e1/runs/run1.yaml .rb/experiments/e1/runs/run2.yaml
printf '\\e[24;1H\\e[K\\e[3;1HRecorded both runs.\\n'
IFS= read -r line; IFS= read -r line
""")
    (fake / "claude").chmod(0o755)
    take = _tool("agent_take")
    cast = tmp_path / "agent.cast"
    bin_dir = str(Path(sys.executable).parent)
    code = take.main(["--folder", str(record), "--say", "Run worked examples on seeds 0, 1 and 2, and record each with rb.",
                      "--cast", str(cast), "--rb-bin", bin_dir, "--study-bin", str(fake),
                      "--until-runs", "2", "--quiet", "2", "--limit", "60"])
    assert code == 0, (tmp_path / "agent.log").read_text()
    assert "Run worked examples on seeds 0, 1 and 2" in (record / "said.txt").read_text()
    meta = json.loads((tmp_path / "agent.cast.json").read_text())
    assert 0 < meta["marks"]["said"] < meta["marks"]["done"] and meta["wall0"] > 1.7e9
    assert 3 + 2 <= meta["marks"]["done"] - meta["marks"]["said"] < 30
    assert "the session is quiet; the record holds 2 runs" in (tmp_path / "agent.log").read_text()
    typed = "".join(json.loads(l)[2] for l in cast.read_text().splitlines()[1:] if json.loads(l)[1] == "i")
    assert typed.startswith("claude --dangerously-skip-permissions\r") and "/exit" in typed
    for f in (record / ".rb" / "experiments" / "e1" / "runs").iterdir():
        f.unlink()
    (record / "said.txt").unlink()
    assert take.main(["--folder", str(record), "--say", "Run worked examples.", "--cast", str(tmp_path / "b.cast"),
                      "--rb-bin", bin_dir, "--study-bin", str(fake), "--until-runs", "3", "--quiet", "1",
                      "--limit", "8"]) == 1
    assert "the record holds 2 runs, not 3" in (tmp_path / "b.log").read_text()
    with pytest.raises(SystemExit, match="one plain line"):
        take.main(["--folder", str(record), "--say", "two\nlines", "--cast", str(cast), "--rb-bin", "x"])


@pytest.mark.skipif(shutil.which("node") is None or shutil.which("google-chrome") is None or shutil.which("ffmpeg") is None
                    or importlib.util.find_spec("PIL") is None or not _cdnjs_reachable(), reason="the cut needs node, Chrome, ffmpeg, Pillow and xterm.js")
@pytest.mark.parametrize("where", ["bottom", "top", "bar"])
def test_the_agent_cut_draws_the_page_whole_with_the_terminal_and_its_clock_in_front(tmp_path, monkeypatch, where):
    """A take made of two page frames (a white page, then one with a green line the swap drew) and a short cast: the
    cut is the opening and one jump, 30 fps at 1920 x 1200; the page shows where the window is not, the window where
    segment.json says, and the clock badge beside it; at the top (a change across the bottom, a table's header row
    under the top places), the badge beside it, and the window's shadow short of the header row; with page text in
    every spot beside the window, the clock is in the window's own bar and nothing is drawn over the page's text."""
    from PIL import Image
    cut = _tool("agent_cut")
    take = tmp_path / "take"
    walk = take / "page" / "walk"
    (walk / "frames").mkdir(parents=True)
    Image.new("RGB", (1920, 1200), (255, 255, 255)).save(walk / "frames" / "000001.jpg")
    img = Image.new("RGB", (1920, 1200), (255, 255, 255))
    img.paste((0, 160, 0), (100, 100, 700, 140))
    img.save(walk / "frames" / "000002.jpg")
    (walk / "frames.jsonl").write_text('{"file": "frames/000001.jpg", "t": 0}\n{"file": "frames/000002.jpg", "t": 200}\n')
    (walk / "cursor.jsonl").write_text("")
    boxes, near = [[75, 75, 450, 30]], []
    if where == "top":
        boxes.append([0, 600, 1440, 20])        # across the bottom places
        near.append([300, 495, 1000, 30])       # the header row, 16 px under a full-size top window: in its shadow
    # the page's text (CSS px): the green line, and with "bar" a line beside every spot the badge could take
    text = [[75, 75, 450, 30]]
    if where == "bar":
        text += [[0, y, 1440, 18] for y in range(0, 900, 20)]
    (walk / "marks.jsonl").write_text('{"t": 0, "label": "ready"}\n' + json.dumps({"t": 0, "label": "text", "text": text[:1]}) + "\n" + json.dumps(
        {"t": 200, "label": "change", "words": ["10 recorded runs"], "gone": ["9 recorded runs"], "boxes": boxes, "near": near, "text": text})
        + "\n{\"t\": 230, \"label\": \"end\"}\n")
    (walk / "walk.json").write_text(json.dumps({"width": 1440, "height": 900, "scale": 4 / 3, "frames": 2}))
    lines = [{"version": 2, "width": 64, "height": 24, "timestamp": 1792900000}, [1.0, "o", "math-prompts$ "], [5.0, "o", "Run worked examples.\r\n"],
             [100.0, "o", "Recorded run r013.\r\n"], [190.0, "o", "Recorded run r014.\r\n"], [215.0, "o", "Both recorded.\r\n"]]
    (take / "agent.cast").write_text("".join(json.dumps(l) + "\n" for l in lines))
    take_json = {"record": "math-prompts", "page": {"css": [1440, 900], "scale": 4 / 3, "t0_wall": 1000.0},
                 "casts": {"agent": {"wall0": 1000.0, "marks": {"said": 5.0, "done": 220.0}}}, "runs_before": 9, "runs_after": 11,
                 "made": "2026-10-25T08:00:00Z"}
    (take / "take.json").write_text(json.dumps(take_json))
    if where == "bar":   # paths given from the folder it runs in
        monkeypatch.chdir(tmp_path)
        assert cut.main(["take", "--out", "cut"]) == 0
    else:
        assert cut.main([str(take), "--out", str(tmp_path / "cut")]) == 0
    seg = json.loads((tmp_path / "cut" / "agent" / "segment.json").read_text())
    assert [m["clock"] for m in seg["moments"]] == [None, "+3 min"] and seg["marks_checked"] == 1
    probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v", "-show_entries", "stream=width,height,r_frame_rate:format=duration",
                            "-of", "json", str(tmp_path / "cut" / "agent" / "screen.mp4")], capture_output=True, text=True)
    info = json.loads(probe.stdout)
    assert (info["streams"][0]["width"], info["streams"][0]["height"], info["streams"][0]["r_frame_rate"]) == (1920, 1200, "30/1")
    assert abs(float(info["format"]["duration"]) - seg["seconds"]) < 0.2
    frame = tmp_path / "f.png"
    m = seg["moments"][1]
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-ss", f"{m['cut'][0] + 2.2:.2f}", "-i", str(tmp_path / "cut" / "agent" / "screen.mp4"),
                    "-frames:v", "1", str(frame)], check=True)
    px = Image.open(frame).convert("RGB")
    x, y, w, h = m["window"]
    assert px.getpixel((400, 120))[1] > 120 and px.getpixel((400, 120))[0] < 80          # the swap's green line, uncovered
    assert px.getpixel((50, 600)) > (235, 235, 235)                                      # the page where nothing is
    assert sum(px.getpixel((x + w // 2, y + h // 2))) < 200                              # the terminal, dark
    assert sum(px.getpixel((x - 60, y + h // 2))) > 600                                   # and the page beside it
    assert seg["moments"][1]["near"] == [[round(v * 4 / 3, 1) for v in b] for b in near]
    if where == "bar":
        assert m["badge"] == "bar" and m["text"] and cut.check(seg) == []
        assert sum(px.getpixel((x - 16, y + cut.BADGE_H // 2))) > 600                     # no badge over the page
        bar = [px.getpixel((bx, by)) for bx in range(x + w // 2, x + w - 8, 2) for by in range(y + 4, y + round(h * 30 / cut.term_window(64, 24)[1]) - 4)]
        assert sum(min(c) > 200 for c in bar) > 100, "no light clock in the window's bar"
        # a badge set beside the window over the page's text is refused
        moved = dict(seg, moments=[dict(m, badge=[x - 210, y, 200, cut.BADGE_H], text=[[x - 300, y + 10, 400, 20]])])
        assert any("meets the page's text" in p for p in cut.check(moved))
        assert any("no page text kept" in p for p in cut.check(dict(seg, moments=[dict(m, badge=[x - 210, y, 200, cut.BADGE_H], text=None)])))
    elif where == "bottom":
        assert m["place"].startswith("bottom") and m["badge"] != "bar" and m["badge"][:2] == list(cut.badge_at(m["window"], m["badge"][2]))
        assert sum(px.getpixel((x - 16, y + cut.BADGE_H // 2))) < 200                     # the clock badge beside it
        assert sum(px.getpixel((x + 30, y - 30))) > 600                                   # and nothing above it
    else:
        assert m["place"] == "top right" and h < seg["moments"][0]["window"][3], seg       # a step smaller
        assert sum(px.getpixel((x - 16, y + cut.BADGE_H // 2))) < 200                     # the clock badge beside it
        assert sum(px.getpixel((x + 30, y + h + 60))) > 600                               # nothing under it
        assert min(px.getpixel((x + w // 2, round(495 * 4 / 3) + 1))) >= 245              # no shadow on the header row


@_NEEDS_CHROME
def test_a_walk_boxes_a_new_rows_button_not_the_same_words_in_the_row_it_pushed_down(tmp_path):
    """Two rows of rb's queue each end with an "Inspect" button and carry no id of their own: a new row swapped in
    above the old one is the change, its button too, and the old row's button, only moved down, is not."""
    (tmp_path / "files").mkdir()
    row = "<div class='row'><span>{}</span> <button>Inspect</button></div>"
    old = row.format("Inspect a changed file nothing in the record cites")
    new = row.format("Record a result found in the repository") + old
    (tmp_path / "files" / "p.html").write_text(f"""<!doctype html><body style='margin:0;font:16px sans-serif'>
<section id='queue'>{old}</section>
<script>
setTimeout(() => {{
  document.getElementById('queue').innerHTML = {json.dumps(new)};
  document.dispatchEvent(new CustomEvent('rb:swapped', {{ cancelable: true }}));
}}, 700);
</script></body>""")
    until = tmp_path / "done"
    (tmp_path / "beats.json").write_text(json.dumps({"width": 800, "height": 600, "scale": 1, "beats": [{"name": "t", "steps": [
        {"goto": "/files/p.html", "settle": 0}, {"watch": "#queue", "until": str(until), "settle": 200}]}]}))
    walk = subprocess.Popen(["node", str(MAKE_STUDY.parent / "walk.mjs"), "--script", str(tmp_path / "beats.json"), "--out", str(tmp_path / "w"),
                             "--serve", str(tmp_path / "files")], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    _end_watch(tmp_path / "w", until, 1)
    out, err = walk.communicate(timeout=60)
    assert walk.returncode == 0, out + err
    (c,) = [json.loads(l) for l in (tmp_path / "w" / "marks.jsonl").read_text().splitlines() if '"change"' in l]
    assert c["words"] == ["Record a result found in the repository", "Inspect"] and c["gone"] == [], c
    (_, y1, _, _), (_, y2, _, _) = c["boxes"]
    assert abs(y1 - y2) < 6, c["boxes"]   # the new row's button, on the new row's line


def test_an_agent_beat_takes_a_link_to_the_record_for_the_record_and_clears_no_folder_that_is_not_a_take(tmp_path):
    real = tmp_path / "real" / "math-prompts"
    (real / ".rb").mkdir(parents=True)
    (real / "results").mkdir()
    (real / "results" / "keep").write_text("r013")
    link = tmp_path / "link"
    link.symlink_to(real)
    rb = tmp_path / "rb"
    rb.write_text("#!/bin/sh\n")
    rb.chmod(0o755)
    base = ["node", str(MAKE_STUDY.parent / "agent_beat.mjs"), "--rb", str(rb), "--record", str(link), "--task", "Run worked examples."]
    for out, work in ((tmp_path / "real", tmp_path / "w"), (real / "results", tmp_path / "w"), (tmp_path / "t", tmp_path / "real"), ("/", tmp_path / "w")):
        r = subprocess.run(base + ["--out", str(out), "--work", str(work)], capture_output=True, text=True, timeout=60)
        assert r.returncode == 2 and "holds the record or rb" in r.stderr, (out, r.stderr)
        assert (real / "results" / "keep").exists()
    mine = tmp_path / "notes"
    mine.mkdir()
    (mine / "draft.md").write_text("INT8 ResNet-50")
    r = subprocess.run(base + ["--out", str(mine), "--work", str(tmp_path / "w")], capture_output=True, text=True, timeout=60)
    assert r.returncode == 2 and "no earlier take's" in r.stderr and (mine / "draft.md").exists(), r.stderr


def test_an_agent_beat_films_the_agent_on_the_view_it_is_given_and_the_fresh_session_on_the_overview(tmp_path):
    """--start x-e1: the agent session is watched on experiment e1's view (its comparison grid), then the page goes
    back to the Overview, marked, before the fresh session; without it, one watch on the Overview as before. An address
    that is no view's is refused before anything is copied."""
    js = ("import { pageSteps } from " + json.dumps(str(MAKE_STUDY.parent / "agent_page.mjs")) + ";\n"
          "const a = process.argv[1] === 'null' ? null : process.argv[1];\n"
          "try { console.log(JSON.stringify(pageSteps({ base: 'http://127.0.0.1:8941/', start: a, agentDone: '/t/.agent-done', done: '/t/.done', timeoutMs: 1 }))); }\n"
          "catch (e) { console.log(JSON.stringify({ error: e.message })); }\n")

    def steps(start):
        r = subprocess.run(["node", "--input-type=module", "-e", js, "null" if start is None else start], capture_output=True, text=True, timeout=60)
        assert r.returncode == 0, r.stderr
        return json.loads(r.stdout)

    on = steps("x-e1")
    gotos = [s["goto"] for s in on if "goto" in s]
    assert gotos == ["http://127.0.0.1:8941/", "http://127.0.0.1:8941/#x-e1", "http://127.0.0.1:8941/"]
    watches = [s for s in on if "watch" in s]
    assert [w["until"] for w in watches] == ["/t/.agent-done", "/t/.done"]
    marks = [s["mark"] for s in on if "mark" in s]
    assert marks == ["ready", "overview"]
    # the first view is waited for by its own address, before the ready mark; the Overview comes after the agent's watch
    first_wait = next(s["waitFor"] for s in on if s.get("lead") and "waitFor" in s)
    assert '"x-e1"' in first_wait
    i_watch, i_back = on.index(watches[0]), [i for i, s in enumerate(on) if s.get("goto") == "http://127.0.0.1:8941/"][-1]
    assert i_watch < i_back < on.index({"mark": "overview"}) < on.index(watches[1])
    plain = steps(None)
    assert [s["goto"] for s in plain if "goto" in s] == ["http://127.0.0.1:8941/", "http://127.0.0.1:8941/"]
    assert [s["mark"] for s in plain if "mark" in s] == ["ready"]
    assert [s["until"] for s in plain if "watch" in s] == ["/t/.done"]
    for bad in ("x-e1\"><script>", "#x-e1", " x-e1", ""):
        assert "no view's address" in steps(bad).get("error", ""), bad

    record = tmp_path / "math-prompts"
    (record / ".rb").mkdir(parents=True)
    rb = tmp_path / "rb"
    rb.write_text("#!/bin/sh\n")
    rb.chmod(0o755)
    # the Overview is waited for as the view shown, not by its address alone (an address naming nothing shows it too)
    assert all("view.on" in s["waitFor"] for s in on if "waitFor" in s and "main" in s["waitFor"])
    assert '"overview"' in [s for s in on if "waitFor" in s][-1]["waitFor"]
    for start in ("x-e1&place=b", None):
        r = subprocess.run(["node", str(MAKE_STUDY.parent / "agent_beat.mjs"), "--rb", str(rb), "--record", str(record),
                            "--task", "Run worked examples.", "--out", str(tmp_path / "take"), "--work", str(tmp_path / "w"),
                            "--port", "1", "--start"] + ([start] if start else []), capture_output=True, text=True, timeout=60)
        assert r.returncode == 2 and "--start" in r.stderr, r.stderr
        assert not (tmp_path / "w").exists() and not (tmp_path / "take").exists()



def test_an_agent_beat_turns_spinner_tips_off_in_the_copys_settings_and_keeps_what_else_they_hold(tmp_path):
    """The filmed sessions show no "Tip: Did you know ..." lines: the copy's .claude/settings.json says so, beside what
    it held, and the record itself is left as it was."""
    record = tmp_path / "math-prompts"
    (record / ".rb").mkdir(parents=True)
    (record / ".claude").mkdir()
    (record / ".claude" / "settings.json").write_text('{"theme": "dark"}\n')
    rb = tmp_path / "rb"
    rb.write_text("#!/bin/sh\nexit 0\n")   # rb open never answers: the beat stops after the copy is set up
    rb.chmod(0o755)
    import socket
    with socket.socket() as sk:
        sk.bind(("127.0.0.1", 0))
        port = sk.getsockname()[1]
    r = subprocess.run(["node", str(MAKE_STUDY.parent / "agent_beat.mjs"), "--rb", str(rb), "--record", str(record),
                        "--task", "Run worked examples.", "--out", str(tmp_path / "take"), "--work", str(tmp_path / "w"),
                        "--port", str(port)], capture_output=True, text=True, timeout=120)
    assert r.returncode == 1 and "rb open did not answer" in r.stderr, r.stderr
    # with no rsync the take keeps no states, says so, and goes on as before
    nobin = tmp_path / "bin"
    nobin.mkdir()
    for tool in ("node", "cp", "sh", "ls", "wc", "mv", "taskset"):
        if shutil.which(tool):
            (nobin / tool).symlink_to(shutil.which(tool))
    r = subprocess.run([str(nobin / "node"), str(MAKE_STUDY.parent / "agent_beat.mjs"), "--rb", str(rb), "--record", str(record),
                        "--task", "Run worked examples.", "--out", str(tmp_path / "take"), "--work", str(tmp_path / "w"),
                        "--port", str(port)], capture_output=True, text=True, timeout=120, env={**os.environ, "PATH": str(nobin)})
    assert r.returncode == 1 and "rb open did not answer" in r.stderr and "keeps no states" in r.stderr, r.stderr
    copied = json.loads((tmp_path / "w" / "math-prompts" / ".claude" / "settings.json").read_text())
    assert copied == {"theme": "dark", "spinnerTipsEnabled": False}
    assert (record / ".claude" / "settings.json").read_text() == '{"theme": "dark"}\n'
    # settings that are no JSON are refused, never written over
    (record / ".claude" / "settings.json").write_text('{"theme": dark')
    r = subprocess.run(["node", str(MAKE_STUDY.parent / "agent_beat.mjs"), "--rb", str(rb), "--record", str(record),
                        "--task", "Run worked examples.", "--out", str(tmp_path / "take"), "--work", str(tmp_path / "w"),
                        "--port", str(port)], capture_output=True, text=True, timeout=120)
    assert r.returncode == 1 and "is no JSON" in r.stderr, r.stderr
    assert (tmp_path / "w" / "math-prompts" / ".claude" / "settings.json").read_text() == '{"theme": dark'


def test_an_agent_beat_given_a_view_the_page_links_nowhere_to_keeps_the_earlier_take(tmp_path):
    """--start x-e9 on a record whose page has no e9: refused once rb open answers, and the earlier take in --out is
    still there; --start with --fresh-only (no agent session to film on it) is refused before anything."""
    import socket
    record = tmp_path / "math-prompts"
    (record / ".rb").mkdir(parents=True)
    (record / ".claude" / "skills" / "rabbit-brain").mkdir(parents=True)
    site = tmp_path / "site"
    site.mkdir()
    (site / "index.html").write_text('<main id="main"><a href="#x-e1">e1</a></main>')
    rb = tmp_path / "bin" / "rb"
    rb.parent.mkdir()
    rb.write_text(f"""#!/bin/sh
[ "$1" = open ] && exec {sys.executable} -m http.server "$3" --bind 127.0.0.1 --directory {site}
exit 0
""")
    rb.chmod(0o755)
    with socket.socket() as so:
        so.bind(("127.0.0.1", 0))
        port = so.getsockname()[1]
    take = tmp_path / "take"
    take.mkdir()
    (take / "take.json").write_text("{}")
    (take / "agent.cast").write_text("take 3")
    base = ["node", str(MAKE_STUDY.parent / "agent_beat.mjs"), "--rb", str(rb), "--record", str(record), "--out", str(take),
            "--work", str(tmp_path / "w"), "--port", str(port)]
    r = subprocess.run(base + ["--task", "Run worked examples.", "--start", "x-e9"], capture_output=True, text=True, timeout=60)
    assert r.returncode == 2 and "links nowhere to #x-e9" in r.stderr, r.stderr
    assert (take / "agent.cast").read_text() == "take 3"
    r = subprocess.run(base + ["--fresh-only", "--start", "x-e1"], capture_output=True, text=True, timeout=60)
    assert r.returncode == 2 and "--fresh-only" in r.stderr, r.stderr
    assert (take / "agent.cast").read_text() == "take 3"


@_NEEDS_CHROME
def test_a_walk_keeps_every_line_of_text_on_screen_at_its_start_and_with_each_change(tmp_path):
    """A watch keeps the box of every line of text the window shows, outside the watched parts too (the page's title,
    its clock), at its start (a "text" marker), with each change, and whenever the text moves with no swap (a clock
    line redrawn in place), so a cut can keep its own marks off them; hidden text and text below the window are left
    out."""
    (tmp_path / "files").mkdir()
    (tmp_path / "files" / "p.html").write_text("""<!doctype html><body style='margin:0'>
<h1 id='title' style='margin:0;font-size:30px'>INT8 ResNet-50 on ImageNet</h1>
<div id='main'><p id='n'>9 runs</p><p style='display:none'>hidden words</p></div>
<p style='position:absolute;top:2000px'>below the window</p>
<script>
setTimeout(() => { document.getElementById('n').textContent = '12 runs';
  document.getElementById('main').insertAdjacentHTML('beforeend', '<p id=late>a new line</p>');
  document.dispatchEvent(new CustomEvent('rb:swapped')); }, 500);
setTimeout(() => { document.getElementById('title').textContent = 'INT8 ResNet-50 on ImageNet · changed 16 minutes ago'; }, 1500);
</script></body>""")
    until = tmp_path / "done"
    (tmp_path / "beats.json").write_text(json.dumps({"width": 800, "height": 600, "scale": 1, "beats": [{"name": "t", "steps": [
        {"goto": "/files/p.html", "settle": 0}, {"watch": "#main", "until": str(until), "settle": 100}]}]}))
    walk = subprocess.Popen(["node", str(MAKE_STUDY.parent / "walk.mjs"), "--script", str(tmp_path / "beats.json"), "--out", str(tmp_path / "w"),
                             "--serve", str(tmp_path / "files")], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    _end_watch(tmp_path / "w", until, 1)
    out, err = walk.communicate(timeout=60)
    assert walk.returncode == 0, out + err
    marks = [json.loads(l) for l in (tmp_path / "w" / "marks.jsonl").read_text().splitlines()]
    texts = [m for m in marks if m["label"] == "text"]
    start, moved = texts[0], texts[-1]
    (change,) = [m for m in marks if m["label"] == "change"]
    assert len(start["text"]) == 2 and len(change["text"]) == 3, (start["text"], change["text"])
    # the title redrawn in place, with no swap, is kept too, from when it moved, wider
    assert change["t"] + 0.6 < moved["t"] and moved["text"][0][2] > start["text"][0][2] + 100, (change["t"], moved)
    title, n = start["text"]
    assert title[1] < 10 and title[2] > 300 and 20 < title[3] < 50 and n[1] > title[1] + title[3], start["text"]
    assert all(0 <= x and x + w <= 800 and 0 <= y and y + h <= 600 for x, y, w, h in change["text"]), change["text"]


def test_the_agent_cuts_clock_sits_beside_the_window_only_where_the_page_is_blank():
    """The clock badge goes to the first spot beside the window that meets no line of text the page shows in the
    moment (level with its top, then its bottom, then above and under it), and none of what changes; where every spot
    meets text, or the walk kept no text, it goes in the window's own bar. The text read is the walk's kept last at the
    moment's start and each kept until its end."""
    cut = _tool("agent_cut")
    tr = cut.place("top right", (691, 382))
    x, y, w, h = tr
    m = {"window": tr, "clock": "+52 min", "boxes": [], "near": [], "text": []}
    assert cut.badge_for(m, 160) == [x - 170, y, 160, cut.BADGE_H]
    # take 5's +52 min: the page title left of the window's top; its bottom-level spot is blank
    m["text"] = [[540, 105, 700, 45]]
    assert cut.badge_for(m, 160) == [x - 170, y + h - cut.BADGE_H, 160, cut.BADGE_H]
    m["text"] += [[400, y + h - 60, 900, 40]]
    assert cut.badge_for(m, 160) == [x + w - 160, y - 10 - cut.BADGE_H, 160, cut.BADGE_H]   # above it, at its outer edge
    m["text"] += [[1600, 20, 280, 30]]                                                     # rb's top bar there
    assert cut.badge_for(m, 160) == [x + w - 160, y + h + 10, 160, cut.BADGE_H]            # under it
    m["text"] += [[400, y + h + 12, 1500, 20]]
    assert cut.badge_for(m, 160) == "bar"
    assert cut.badge_for(dict(m, text=None), 160) == "bar"
    # a change where the badge would go is kept clear too
    assert cut.badge_for({"window": tr, "clock": "+1 min", "boxes": [[x - 100, y + 5, 40, 20]], "text": []}, 160)[1] != y
    assert cut.covers(dict(m, badge="bar")) == [tr] and cut.covers(dict(m, badge=[1, 2, 3, 4])) == [tr, [1, 2, 3, 4]]
    marks = [{"t": 0, "label": "text", "text": [[0, 0, 30, 15]]}, {"t": 10, "label": "change", "text": [[0, 30, 30, 15]]},
             {"t": 20, "label": "change", "text": [[0, 60, 30, 15]]}, {"t": 30, "label": "change", "words": ["x"]}]
    assert cut.texts_for(marks, 100.0, 2, 112, 125) == [[0, 60, 60, 30], [0, 120, 60, 30]]
    assert cut.texts_for(marks, 100.0, 2, 95, 99) is None


@pytest.mark.skipif(importlib.util.find_spec("PIL") is None, reason="the page's frames are read with Pillow")
def test_the_agent_cuts_clock_beside_the_window_needs_blank_page_in_every_frame_and_check_refuses_one_that_is_not(tmp_path):
    """Text the walk never read (rb open's "changed 2 minutes ago" redrawn in place, a placeholder) shows in the page's
    own frames: a spot beside the window is taken only where every frame the moment shows is one colour under it, and
    the cut's checks read the take again, refusing a clock over text in the frames or the walk's text, a clock beside
    the window with no text kept, off the frame or over the window."""
    from PIL import Image, ImageDraw
    cut = _tool("agent_cut")
    walk = tmp_path / "walk"
    (walk / "frames").mkdir(parents=True)
    tr = cut.place("top right", (691, 382))
    x, y, w, h = tr
    spot = [x - 170, y, 160, cut.BADGE_H]
    for i in (1, 2):
        im = Image.new("RGB", cut.FRAME, (255, 255, 255))
        if i == 2:   # "changed 16 minutes ago" grown under the spot, after the moment's start, with no swap
            ImageDraw.Draw(im).text((spot[0] + 20, spot[1] + 15), "changed 16 minutes ago", fill=(90, 90, 90))
        im.save(walk / "frames" / f"00000{i}.jpg", quality=80)
    (walk / "frames.jsonl").write_text('{"file": "frames/000001.jpg", "t": 0}\n{"file": "frames/000002.jpg", "t": 50}\n')
    (walk / "marks.jsonl").write_text(json.dumps({"t": 0, "label": "text", "text": [[10, 10, 100, 20]]}) + "\n")
    assert cut.page_blank(walk, 10, 40, spot) and not cut.page_blank(walk, 10, 60, spot) and not cut.page_blank(walk, 60, 70, spot)
    assert not cut.page_blank(walk, -5, -1, spot)                         # before the first frame: nothing to read
    # a glyph as faint as rb's faintest once Chrome's antialiasing and JPEG soften it (its crumb's "/ ", #d1d9e0, spans
    # about 41 in a take's frame; this one, drawn solid, about 35) is text; a #f6f8fa panel is blank page
    from PIL import ImageFont
    font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 17)
    for name, draw, blank in (("faint", lambda d: d.text((spot[0] + 30, spot[1] + 12), "/ e1", font=font, fill=(220, 226, 232)), False),
                              ("panel", lambda d: d.rectangle([spot[0] - 20, spot[1] - 20, spot[0] + 300, spot[1] + 80], fill=(0xf6, 0xf8, 0xfa)), True)):
        im = Image.new("RGB", cut.FRAME, (255, 255, 255))
        draw(ImageDraw.Draw(im))
        im.save(walk / "frames" / f"{name}.jpg", quality=90)
        (walk / f"{name}.jsonl").write_text(json.dumps({"file": f"frames/{name}.jpg", "t": 0}) + "\n")
        assert cut.page_blank(walk, 1, 2, spot, cut.load(walk / f"{name}.jsonl")) is blank, name
    m = {"window": tr, "clock": "+1 min", "boxes": [], "near": [], "text": [], "wall": [1010.0, 1060.0], "cut": [0, 3]}
    assert cut.badge_for(m, 160, blank=lambda b: cut.page_blank(walk, 10, 40, b)) == spot
    assert cut.badge_for(m, 160, blank=lambda b: cut.page_blank(walk, 10, 60, b)) != spot
    seg = {"moments": [dict(m, badge=spot)]}
    assert cut.check(seg) == []
    assert any("not blank" in p for p in cut.check_page(seg, walk, 1000.0, 1))
    assert cut.check_page({"moments": [dict(m, badge=spot, wall=[1010.0, 1040.0])]}, walk, 1000.0, 1) == []
    # the walk's own text read again: a line kept under the spot is refused even where the segment's text says none
    (walk / "marks.jsonl").write_text(json.dumps({"t": 0, "label": "text", "text": [spot]}) + "\n")
    assert any("meets text" in p for p in cut.check_page({"moments": [dict(m, badge=spot, wall=[1010.0, 1040.0])]}, walk, 1000.0, 1))
    # a clock beside the window with no text kept (a segment from before the walk kept it), off the frame, or over the
    # window, is refused; one in the window's bar is not
    assert any("no page text kept" in p for p in cut.check({"moments": [dict(m, text=None, badge=spot)]}))
    assert any("no page text kept" in p for p in cut.check({"moments": [{k: v for k, v in m.items() if k != "text"}]}))
    assert any("off the frame or over the window" in p for p in cut.check({"moments": [dict(m, badge=[x + 10, y + 10, 160, 46])]}))
    assert any("off the frame or over the window" in p for p in cut.check({"moments": [dict(m, badge=[1900, 1190, 160, 46])]}))
    assert cut.check({"moments": [dict(m, text=None, badge="bar")]}) == []


@pytest.mark.skipif(shutil.which("node") is None or shutil.which("google-chrome") is None or shutil.which("ffmpeg") is None
                    or importlib.util.find_spec("PIL") is None or not _cdnjs_reachable(),
                    reason="the terminal is drawn by xterm.js in Chrome and made a video with ffmpeg and Pillow")
def test_the_terminal_window_keeps_its_width_with_a_long_title_and_a_clock_in_its_bar(tmp_path):
    """The window's bar holds its title, the clock and its label: a long title is cut short, never widening the
    window, which the cut scales as if it were its usual width."""
    cut = _tool("agent_cut")
    cast = tmp_path / "t.cast"
    cast.write_text(json.dumps({"version": 2, "width": 64, "height": 16}) + "\n" + json.dumps([1.0, "o", "rb status\r\n"]) + "\n")
    _, rect = cut.render_term(tmp_path / "w", cast, 0, 2, 64, 16, "int8-resnet50-imagenet-calibration-sweep-final-record", "google-chrome", badge="+120 min")
    assert abs(rect[2] / 2 - cut.term_window(64, 16)[0]) <= 1, rect


@pytest.mark.skipif(shutil.which("node") is None or shutil.which("rsync") is None, reason="agent_states.mjs needs node and rsync")
def test_an_agent_beat_keeps_every_state_the_copy_passes_through_and_a_later_change_leaves_a_kept_state_as_it_was(tmp_path):
    """agent_states.mjs keeps the whole copy each time it moves, so the page can be walked again on a later build: a
    file appended to in place afterwards leaves the state kept before it as it was, a file rewritten at the same size
    and time is kept by its bytes, a file unchanged is linked rather than copied again, a config outside .rb/ is kept
    too, rb's cache and past-version checkouts keep no state and are in none, a copy that fails is said and kept on a
    later look, and it ends once told to, keeping the last move. agent_beat starts it on the copy before rb open and
    says in take.json when the states were not all kept, failing nothing filmed."""
    folder = tmp_path / "int8-resnet50"
    (folder / ".rb" / "cache").mkdir(parents=True)
    (folder / ".git" / "rb" / "views").mkdir(parents=True)
    (folder / "configs").mkdir()
    (folder / ".rb" / "log.jsonl").write_text('{"op": "init"}\n')
    (folder / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    (folder / "configs" / "train.yaml").write_text("optim:\n  lr: 0.0001\n")
    (folder / "README.md").write_text("INT8 ResNet-50 on ImageNet\n")
    out, stop = tmp_path / "states", tmp_path / "stop"
    keeper = subprocess.Popen(["node", str(MAKE_STUDY.parent / "agent_states.mjs"), "--folder", str(folder), "--out", str(out), "--stop", str(stop)],
                              stderr=subprocess.PIPE, text=True)

    def states(n, wait=100):
        for _ in range(wait):
            lines = (out / "states.jsonl").read_text().splitlines() if (out / "states.jsonl").exists() else []
            if len(lines) >= n:
                return [json.loads(x) for x in lines]
            time.sleep(0.1)
        raise AssertionError(f"fewer than {n} states")

    try:
        states(1)
        (folder / ".rb" / "cache" / "hash").write_text("x")   # rb's own cache and checkouts: no new state
        (folder / ".git" / "rb" / "views" / "c1").write_text("x")
        time.sleep(2.5)
        assert len(states(1)) == 1
        with open(folder / ".rb" / "log.jsonl", "a") as f:   # appended in place, as rb writes its log
            f.write('{"op": "criterion", "actor": "human:ana"}\n')
        states(2)
        # the same size and the same time, other bytes (a ref moved within one second)
        head = folder / ".git" / "HEAD"
        st = head.stat()
        head.write_text("ref: refs/heads/int8\n")
        os.utime(head, ns=(st.st_atime_ns, st.st_mtime_ns))
        states(3)
        mode = (folder / "README.md").stat().st_mode
        os.chmod(folder / "README.md", 0)   # a file it cannot read: that state is kept without it, marked, said once
        states(4)
        os.chmod(folder / "README.md", mode)
        states(5)
        (folder / "configs" / "train.yaml").write_text("optim:\n  lr: 0.0003\n")   # outside .rb/ and .git/
        for skipped in (".venv", "__pycache__"):
            (folder / skipped).mkdir()
            (folder / skipped / "big.bin").write_text("x")
        states(6)
        for skipped in (".venv", "__pycache__"):   # a change there alone keeps no state
            (folder / skipped / "big.bin").write_text("xy")
        time.sleep(6.5)
        assert len(states(6)) == 6
        (folder / "empty").mkdir()   # an empty folder made alone does
        states(7)
        (folder / "results").mkdir()
        (folder / "results" / "metrics.json").write_text('{"val_loss": 0.987}\n')
        stop.write_text("")
        keeper.wait(timeout=30)
    finally:
        keeper.kill()
    said = keeper.stderr.read()
    assert keeper.returncode == 0, said
    kept = states(8)
    assert [s["n"] for s in kept] == [0, 1, 2, 3, 4, 5, 6, 7]
    assert (out / "0" / ".rb" / "log.jsonl").read_text() == '{"op": "init"}\n'
    assert (out / "1" / ".rb" / "log.jsonl").read_text().count("\n") == 2
    assert (out / "1" / ".git" / "HEAD").read_text() == "ref: refs/heads/main\n"
    assert (out / "2" / ".git" / "HEAD").read_text() == "ref: refs/heads/int8\n"
    if os.geteuid() != 0:
        assert said.count("kept without what rsync could not read") == 1, said
        assert any("README.md" in x for x in kept[3]["partial"]) and not (out / "3" / "README.md").exists()
        assert all("partial" not in k for k in kept[:3] + kept[4:])
    assert (out / "4" / "README.md").exists() and "0.0001" in (out / "4" / "configs" / "train.yaml").read_text()
    assert "0.0003" in (out / "5" / "configs" / "train.yaml").read_text() and not (out / "5" / "empty").exists() and (out / "6" / "empty").is_dir()
    assert not (out / "5" / ".venv").exists() and not (out / "5" / "__pycache__").exists()
    assert not (out / "6" / "results").exists() and (out / "7" / "results" / "metrics.json").exists()
    assert not any((out / str(i) / ".rb" / "cache").exists() or (out / str(i) / ".git" / "rb" / "views").exists() for i in range(8))
    assert (out / "0" / "README.md").stat().st_ino == (out / "2" / "README.md").stat().st_ino
    assert (out / "4" / "README.md").stat().st_ino == (out / "7" / "README.md").stat().st_ino
    assert (out / "0" / "README.md").stat().st_ino != (folder / "README.md").stat().st_ino
    # a cap reached is said, in states.jsonl and by its exit, and its last state is kept for the end
    capped = subprocess.Popen(["node", str(MAKE_STUDY.parent / "agent_states.mjs"), "--folder", str(folder), "--out", str(tmp_path / "capped"),
                               "--stop", str(tmp_path / "stop2"), "--max", "2"], stderr=subprocess.PIPE, text=True)
    try:
        time.sleep(2)
        with open(folder / ".rb" / "log.jsonl", "a") as f:
            f.write('{"op": "run"}\n')
        time.sleep(2)
        with open(folder / ".rb" / "log.jsonl", "a") as f:
            f.write('{"op": "run", "seed": 2}\n')
        time.sleep(2)
        (tmp_path / "stop2").write_text("")
        capped.wait(timeout=30)
    finally:
        capped.kill()
    assert capped.returncode == 3 and "keeping only the last one more" in capped.stderr.read()
    lines = [json.loads(x) for x in (tmp_path / "capped" / "states.jsonl").read_text().splitlines()]
    assert [x.get("n", "max") for x in lines] == [0, "max", 1]
    assert '"seed": 2' in (tmp_path / "capped" / "1" / ".rb" / "log.jsonl").read_text()
    # a keeper whose beat is gone ends, keeping the last state
    pidfile = tmp_path / "pid"
    subprocess.run(["sh", "-c", f'node {MAKE_STUDY.parent / "agent_states.mjs"} --folder {folder} --out {tmp_path / "orphan"} --stop {tmp_path / "never"} & echo $! > {pidfile}; sleep 2'], timeout=30)
    pid = int(pidfile.read_text())
    for _ in range(100):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.1)
    else:
        os.kill(pid, 9)
        raise AssertionError("the keeper outlived the process that started it")
    beat = (MAKE_STUDY.parent / "agent_beat.mjs").read_text()
    assert beat.index('"agent_states.mjs"') < beat.index('spawn(rb, ["open"')
    assert "states_failed: statesFailed" in beat and 'failed = failed || "the record\'s states' not in beat
    assert "detached: true });\nconst keeperDone" in beat and "process.kill(-keeper.pid" in beat
    assert beat.index('existsSync(join(statesDir, "states.jsonl"))') < beat.index('spawn(rb, ["open"')
