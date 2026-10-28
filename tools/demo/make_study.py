#!/usr/bin/env python3
"""Builds int8-cifar: the repository a research agent leaves after two weeks of one real study, for the demo.

    python tools/demo/make_study.py --out /srv/rb/demo/int8-cifar --data /srv/rb/demo/cache/cifar10
    python tools/demo/make_study.py --out <new folder> --tiny      # random images, a short schedule: the test's build

The study's code is tools/demo/study/. Every number in the repository comes from running that code here: training,
quantization and evaluation run for real, and each number the agent's lines quote is read from what the runs wrote.
The two weeks are a scenario: each step runs at its scenario time (the processes' clock is offset, commits carry that
time, files keep it as their mtime) for as long as it really takes. The researcher (ana, a made-up identity) writes
notes.md and gives the instructions; the agent writes the code, runs everything and keeps logs/agent.log.

The scenario: a smoke run and a pilot; the schedule decision in notes.md; a 30-epoch FP32 model on seed 0 with its
per-tensor and per-channel calibration sweeps (128 to 2048 images); then configs/data.yaml's normalization changes,
FP32 is trained again on seeds 0 to 2 (seed 1 is killed and rerun), and the per-tensor sweep runs on all three, with
two more calibration draws at 128 images. The per-channel runs are never repeated: they read the old normalization.
Last, the agent sets up per-channel on seeds 1 and 2 in configs/sweep.yaml and leaves it uncommitted, not run."""
import argparse
import difflib
import json
import os
import shlex
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
STUDY = HERE / "study"
LOCAL = timezone(timedelta(hours=8))                         # the researcher's clock; commits keep it, runs write UTC
START = datetime(2026, 10, 5, tzinfo=LOCAL)                  # a Monday, two weeks before the demo
RESEARCHER = ("ana", "ana@example.org")
CALIBRATION = [128, 512, 1024, 2048]
SEEDS = [0, 1, 2]
NORMALIZE_BEFORE = "normalize:\n  mean: [0.5, 0.5, 0.5]\n  std: [0.5, 0.5, 0.5]\n"
NORMALIZE_AFTER = "normalize:\n  mean: [0.4914, 0.4822, 0.4465]\n  std: [0.247, 0.2435, 0.2616]\n"
TINY_DATA = "dataset: fake\nfake:\n  train_size: 2048\n  test_size: 500\n"

CLOCK = '''\
# make_study.py's scenario clock: this process's wall clock runs DEMO_CLOCK_OFFSET seconds off the real one
import os
_offset = float(os.environ.get("DEMO_CLOCK_OFFSET") or 0)
if _offset:
    import datetime as _dt
    import time as _time
    _time_real, _time_ns_real = _time.time, _time.time_ns
    _gmtime, _localtime, _strftime = _time.gmtime, _time.localtime, _time.strftime
    _time.time = lambda: _time_real() + _offset
    _time.time_ns = lambda: _time_ns_real() + int(_offset * 1e9)
    _time.gmtime = lambda s=None: _gmtime(_time.time() if s is None else s)
    _time.localtime = lambda s=None: _localtime(_time.time() if s is None else s)
    _time.strftime = lambda f, t=None: _strftime(f, _time.localtime() if t is None else t)

    class _datetime(_dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return cls.fromtimestamp(_time.time(), tz)

        @classmethod
        def utcnow(cls):
            return cls.fromtimestamp(_time.time(), _dt.timezone.utc).replace(tzinfo=None)

        @classmethod
        def today(cls):
            return cls.now()

    _dt.datetime = _datetime
'''


def sweep_yaml(stage, grid, sources=None):
    lines = ["# one run per cell of the grid; results/runs.db keeps a row per run", f"stage: {stage}"]
    if sources:
        lines += ["from:", *(f"  {seed}: {run}" for seed, run in sources.items())]
    lines += ["grid:", *(f"  {key}: [{', '.join(str(v) for v in values)}]" for key, values in grid.items())]
    return "\n".join(lines) + "\n"


def epochs(n):
    return f"{n} epoch" + ("" if n == 1 else "s")


def readme(stage):
    """The README as it stood: its first `stage` sections after the introduction and Setup."""
    parts = (STUDY / "README.md").read_text(encoding="utf-8").split("\n## ")
    return "\n## ".join(parts[:2 + stage]).rstrip("\n") + "\n"


def children_running(parent, needle):
    """The processes parent started whose command line holds every word of needle: never another build's."""
    words = needle.split()
    found = []
    proc = Path("/proc")
    if proc.is_dir():
        for p in proc.iterdir():
            if p.name.isdigit():
                try:
                    ppid = int((p / "stat").read_text().rsplit(")", 1)[1].split()[1])
                    cmd = (p / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
                except (OSError, IndexError, ValueError):
                    continue
                if ppid == parent and all(w in cmd for w in words):
                    found.append(int(p.name))
        return found
    out = subprocess.run(["ps", "-eo", "pid=,ppid=,args="], capture_output=True, text=True).stdout
    rows = [line.split(None, 2) for line in out.splitlines()]
    return [int(r[0]) for r in rows if len(r) == 3 and int(r[1]) == parent and all(w in r[2] for w in words)]


class Study:
    def __init__(self, out, tiny, data, cap):
        self.out, self.tiny, self.data, self.cap = out, tiny, data, cap
        self.single = min(cap, 4)                                # one run on more than 4 threads trains no faster here
        # the kill lands some time after its epoch's line: it must leave epochs to spare, even on a loaded machine
        self.pilot, self.main, self.kill_after = (1, 4, 1) if tiny else (10, 30, 12)
        self.now = START.replace(hour=9, minute=10)
        self.shim = Path(tempfile.mkdtemp(prefix="int8-cifar-clock-"))
        (self.shim / "sitecustomize.py").write_text(CLOCK, encoding="utf-8")
        self.agent_log = out / "logs" / "agent.log"

    # the scenario's clock
    def at(self, day, hhmm):
        h, m = map(int, hhmm.split(":"))
        self.now = max((START + timedelta(days=day)).replace(hour=h, minute=m), self.now + timedelta(minutes=1))

    def tick(self, seconds):
        self.now += timedelta(seconds=seconds)

    def stamp(self, t=None):
        return (t or self.now).astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    def touch(self, path, t=None):
        s = (t or self.now).timestamp()
        os.utime(path, (s, s))

    def shift_mtimes(self, since, offset):
        """Files a process wrote get its scenario time, as if it had run then."""
        for top, dirs, files in os.walk(self.out):
            dirs[:] = [d for d in dirs if d not in (".git", "data")]
            for name in [*files, *dirs]:
                p = Path(top) / name
                st = p.stat()
                if st.st_mtime >= since - 1:
                    os.utime(p, (st.st_mtime + offset, st.st_mtime + offset))

    # what the agent's log keeps
    def line(self, kind, text, t=None):
        rows = text.splitlines() or [""]
        with open(self.agent_log, "a", encoding="utf-8", newline="\n") as f:
            f.write(f"{self.stamp(t)}  {kind:<5} {rows[0]}\n")
            for row in rows[1:]:
                f.write(f"{'':19}  {'':<5} {row}\n")

    def user(self, text):
        self.line("user", text)
        self.tick(45)

    def say(self, text):
        self.line("agent", text)
        self.tick(20)

    def write(self, rel, text=None):
        """The agent writes a file: the whole of a new one goes in its log, a diff of a changed one."""
        path = self.out / rel
        new = text if text is not None else (STUDY / rel).read_text(encoding="utf-8")
        old = path.read_text(encoding="utf-8") if path.exists() else None
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(new, encoding="utf-8", newline="\n")
        if old is None:
            self.line("write", f"{rel} ({len(new.splitlines())} lines)\n{new}")
        else:
            diff = difflib.unified_diff(old.splitlines(), new.splitlines(), f"a/{rel}", f"b/{rel}", lineterm="")
            self.line("edit", "\n".join(diff))
        self.touch(path)
        self.tick(20 + len(new) // 150)

    def edit(self, rel, old, new):
        text = (self.out / rel).read_text(encoding="utf-8")
        if text.count(old) != 1:
            raise SystemExit(f"make_study: {rel} does not hold exactly one {old!r}")
        self.write(rel, text.replace(old, new))

    def note(self, entry):
        """The researcher's own line in notes.md, in their words; the agent's log does not see it."""
        path = self.out / "notes.md"
        text = path.read_text(encoding="utf-8") if path.exists() else "# Notes\n"
        path.write_text(text + "\n" + entry + "\n", encoding="utf-8", newline="\n")
        self.touch(path)
        self.tick(120)

    def git(self, *args, **dates):
        """git in the study, as its researcher: an identity in the environment would stand in for the repository's."""
        env = {k: v for k, v in os.environ.items() if not k.startswith(("GIT_AUTHOR_", "GIT_COMMITTER_"))}
        r = subprocess.run(["git", *args], cwd=self.out, capture_output=True, text=True, env={**env, **dates})
        if r.returncode:
            raise SystemExit(f"make_study: git {' '.join(args)}: {r.stderr.strip()}")
        return r.stdout.strip()

    def commit(self, message):
        self.line("$", f"git add -A && git commit -m {shlex.quote(message)}")
        self.git("add", "-A")
        when = self.now.astimezone(LOCAL).isoformat()
        self.git("commit", "-q", "-m", message, GIT_AUTHOR_DATE=when, GIT_COMMITTER_DATE=when)
        self.line("|", f"[main {self.git('rev-parse', '--short', 'HEAD')}] {message}")
        self.tick(40)

    def run(self, command, threads=None, kill=None):
        """Runs a command in the repository at the scenario's time, its output into the agent's log as it comes."""
        self.line("$", command)
        argv = shlex.split(command)
        if argv[0] == "python":
            argv[0] = sys.executable
        offset = self.now.timestamp() - time.time()
        env = {**os.environ, "PYTHONPATH": str(self.shim), "DEMO_CLOCK_OFFSET": repr(offset), "PYTHONUNBUFFERED": "1",
               "OMP_NUM_THREADS": str(threads or self.single), "MKL_NUM_THREADS": str(threads or self.single)}
        started = time.time()
        proc = subprocess.Popen(argv, cwd=self.out, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        done = threading.Event()
        if kill:
            threading.Thread(target=self.kill_when, args=(*kill, proc.pid, done), daemon=True).start()
        lines = []
        for raw in proc.stdout:
            lines.append(raw.rstrip("\n"))
            self.line("|", lines[-1], datetime.fromtimestamp(time.time() + offset, timezone.utc))
        code = proc.wait()
        done.set()
        self.now = datetime.fromtimestamp(time.time() + offset, timezone.utc)
        self.shift_mtimes(started, offset)
        if code:
            self.line("|", f"(exit {code})")
        self.tick(10)
        return code, lines

    def kill_when(self, folder, text, sweep, done):
        """Kills the run the sweep started into folder once its log holds text: the crash the study reruns."""
        log = self.out / folder / "train.log"
        while not done.is_set():
            if log.exists() and text in log.read_text(encoding="utf-8"):
                for pid in children_running(sweep, f"train.py --out {folder}"):
                    os.kill(pid, signal.SIGKILL)
                return
            time.sleep(0.2)

    # what the runs wrote, for the agent's lines
    def metrics(self, run):
        return json.loads((self.out / "results" / run / "metrics.json").read_text(encoding="utf-8"))

    def rows(self, where="1 = 1"):
        con = sqlite3.connect(f"file:{self.out / 'results' / 'runs.db'}?mode=ro", uri=True)
        try:
            con.row_factory = sqlite3.Row
            return [dict(r) for r in con.execute(f"SELECT * FROM runs WHERE {where} ORDER BY run_id")]
        finally:
            con.close()

    def run_ok(self, command, **kw):
        code, lines = self.run(command, **kw)
        if code:
            raise SystemExit(f"make_study: {command} exited {code}")
        return lines

    def sweep(self, jobs, extra="", kill=None):
        """The sweep, as many runs at a time as the grid has (at most jobs), all of them within the build's threads."""
        jobs = max(1, min(jobs, self.cap))
        threads = max(1, min(4, self.cap // jobs))
        return self.run(f"python sweep.py configs/sweep.yaml{extra} --jobs {jobs} --threads {threads}", threads=threads, kill=kill)

    def build(self):
        out = self.out
        out.mkdir(parents=True)
        (out / "logs").mkdir()
        self.agent_log.write_text("# the agent's log for int8-cifar; times are UTC\n", encoding="utf-8")
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", RESEARCHER[0])
        self.git("config", "user.email", RESEARCHER[1])
        self.git("config", "commit.gpgsign", "false")
        if self.data:
            shutil.copytree(Path(self.data) / "cifar-10-batches-py", out / "data" / "cifar-10-batches-py")
            for p in (out / "data").rglob("*"):
                self.touch(p, START.replace(hour=8))
        pilot, main = self.pilot, self.main

        # Mon, day 0: the FP32 baseline
        self.user("This repo is for one question: does INT8 post-training quantization keep a ResNet-20 within 1 point "
                  "of FP32 top-1 on CIFAR-10? CPU only, fbgemm. Start with the FP32 baseline: train and eval scripts, "
                  "settings in configs/, one folder per run under results/. CIFAR-10 is in data/.")
        _, versions = self.run('python -c "import torch, torchvision, yaml; print(torch.__version__, torchvision.__version__, '
                               'yaml.__version__, torch.backends.quantized.supported_engines)"')
        torch_v, vision_v, yaml_v = versions[-1].split()[:3]
        self.say("I'll write models.py (ResNet-20 as in He et al. 2016, with 1x1 projection shortcuts so that every op "
                 "has an fbgemm kernel later), data.py (CIFAR-10 in memory, crop and flip per batch, no loader "
                 "workers), common.py, train.py, eval.py and the configs.")
        self.write("README.md", readme(1))
        self.write("requirements.txt", f"torch=={torch_v.split('+')[0]}\ntorchvision=={vision_v.split('+')[0]}\npyyaml=={yaml_v}\n")
        self.write(".gitignore")
        for rel in ("common.py", "models.py", "data.py", "train.py", "eval.py", "configs/train.yaml", "configs/eval.yaml"):
            self.write(rel)
        data_yaml = (STUDY / "configs/data.yaml").read_text(encoding="utf-8")
        if self.tiny:
            data_yaml = data_yaml.replace("dataset: cifar10\nroot: data\ndownload: false\n", TINY_DATA)
        self.write("configs/data.yaml", data_yaml)
        train_yaml = (STUDY / "configs/train.yaml").read_text(encoding="utf-8")
        if self.tiny:
            self.write("configs/train.yaml", train_yaml.replace("epochs: 10", f"epochs: {pilot}").replace("log_every: 50", "log_every: 4"))
        self.note("2026-10-05: Starting int8-cifar; the question is in the README. Reference: ResNet-20 at 91.25% "
                  "(He et al. 2016, table 6).")
        self.commit("FP32 ResNet-20 on CIFAR-10: model, data, train and eval scripts")
        self.run_ok("python train.py --seed 0 --epochs 1 --out results/r001_fp32_smoke")
        m = self.metrics("r001_fp32_smoke")
        self.say(f"Smoke run: 1 epoch, test top-1 {m['fp32']['top1']:.2f}. The pipeline runs end to end. "
                 f"Next, a pilot of {epochs(pilot)} on seed 0.")
        self.at(0, "10:40")
        self.run_ok(f"python train.py --seed 0 --out results/r002_fp32_s0_e{pilot}")
        m = self.metrics(f"r002_fp32_s0_e{pilot}")
        self.say(f"Pilot, seed 0, {epochs(pilot)}: test top-1 {m['fp32']['top1']:.2f}, final train loss {m['train_loss']:.4f}.")
        self.commit(f"Smoke run and FP32 pilot of {epochs(pilot)}, seed 0")

        # Tue, day 1: INT8 on the pilot
        self.at(1, "10:05")
        self.user("Good. Add post-training INT8 with fbgemm, per-tensor weights first, and try it on the pilot model.")
        self.say("Adding quantize.py and configs/quant.yaml: batch norm folded into the convolutions, a histogram "
                 "observer for activations, symmetric qint8 weights, calibration on training images without "
                 "augmentation. It evaluates the FP32 model and the INT8 model on the same test images.")
        self.write("quantize.py")
        self.write("configs/quant.yaml")
        self.write("README.md", readme(2))
        self.commit("Post-training INT8 with fbgemm: quantize.py and configs/quant.yaml")
        pilot_int8 = f"r003_int8_pt_c512_s0_e{pilot}"
        self.run_ok(f"python quantize.py --from results/r002_fp32_s0_e{pilot} --scheme per_tensor --calib-images 512 "
                    f"--out results/{pilot_int8}")
        m = self.metrics(pilot_int8)
        self.say(f"Per-tensor INT8 on the pilot, 512 calibration images: FP32 {m['fp32']['top1']:.2f}, "
                 f"INT8 {m['int8']['top1']:.2f}. Loading the saved INT8 model back to check it:")
        lines = self.run_ok(f"python eval.py results/{pilot_int8}")
        again = json.loads(lines[-1])["metrics"]["top1"]
        self.say(f"Reloaded: top-1 {again:.2f}" + (", the number the run wrote." if again == m["int8"]["top1"]
                                                    else f"; the run wrote {m['int8']['top1']:.2f}."))
        self.commit("INT8 per-tensor on the pilot model")

        # Wed, day 2: the schedule decision, the 30-epoch model
        self.at(2, "09:30")
        self.note(f"2026-10-07: Decision: from now on every FP32 baseline trains for {epochs(main)}, not the paper's "
                  "64k iterations (about 182 epochs). We compare INT8 with our own FP32, so a short schedule is "
                  "enough, and the paper's 91.25 stays a reference, not a target.")
        self.user("I put the schedule decision in notes.md. Set the baselines to that and train seed 0.")
        self.edit("configs/train.yaml", f"epochs: {pilot}\n", f"epochs: {main}\n")
        self.run_ok("python train.py --seed 0 --out results/r004_fp32_s0")
        m = self.metrics("r004_fp32_s0")
        self.say(f"Seed 0, {epochs(main)}: test top-1 {m['fp32']['top1']:.2f}.")
        self.commit(f"FP32 baseline of {epochs(main)}, seed 0")

        # Thu, day 3: the calibration sweep, per-tensor
        self.at(3, "09:45")
        self.user("Sweep the calibration size on that model: 128, 512, 1024 and 2048 images, per-tensor.")
        self.say("Adding sweep.py: it runs every cell of a grid from a config, a few at a time, and keeps one row per "
                 "run in results/runs.db with its status and top-1.")
        self.write("sweep.py")
        self.write("configs/sweep.yaml", sweep_yaml("quantize", {"scheme": ["per_tensor"], "calib_images": CALIBRATION,
                                                                  "seed": [0]}, {0: "results/r004_fp32_s0"}))
        self.write("README.md", readme(3))
        self.commit("sweep.py: a grid of runs, one row each in results/runs.db")
        self.sweep(4)
        self.say(self.calibration_line("per_tensor", 0))
        self.commit("Calibration sweep, per-tensor, seed 0: 128 to 2048 images")

        # Fri, day 4: per-channel, seed 0 only
        self.at(4, "10:20")
        self.note("2026-10-09: Per-channel weights only on seed 0 for now. If it looks different from per-tensor, we add seeds.")
        self.user("Now the same sweep with per-channel weights. Seed 0 only for now.")
        self.edit("configs/sweep.yaml", "scheme: [per_tensor]", "scheme: [per_channel]")
        self.sweep(4)
        self.say(self.calibration_line("per_channel", 0) + " " + self.calibration_line("per_tensor", 0, short=True))
        self.commit("Calibration sweep, per-channel, seed 0")

        # Mon, day 7: the normalization changes; FP32 on three seeds, one killed
        self.at(7, "09:40")
        self.user("The normalization should use the CIFAR-10 channel mean and std, not 0.5. Change it, then train "
                  "seeds 0, 1 and 2.")
        self.edit("configs/data.yaml", NORMALIZE_BEFORE, NORMALIZE_AFTER)
        self.say("configs/data.yaml now normalizes with the CIFAR-10 training-set channel mean and std.")
        self.commit("Normalize with the CIFAR-10 channel mean and std")
        self.run_ok("python train.py --seed 0 --epochs 1 --out results/r013_fp32_smoke")
        m = self.metrics("r013_fp32_smoke")
        self.say(f"One-epoch check with the new normalization: test top-1 {m['fp32']['top1']:.2f}. Starting the three seeds.")
        self.write("configs/sweep.yaml", sweep_yaml("train", {"seed": SEEDS}))
        self.sweep(3, kill=("results/r015_fp32_s1", f"epoch {self.kill_after}/{main} done"))
        killed = self.rows("run_id = 'r015_fp32_s1'")[0]
        if killed["status"] != "crashed":
            raise SystemExit("make_study: r015_fp32_s1 finished before it could be killed")
        last = [ln for ln in (out / "results/r015_fp32_s1/train.log").read_text(encoding="utf-8").splitlines() if " done: " in ln]
        reached = last[-1].split("epoch ")[1].split("/")[0] if last else "0"
        if int(reached) >= main:
            raise SystemExit(f"make_study: r015_fp32_s1 was killed after its last epoch ({reached} of {main})")
        self.say(f"r015_fp32_s1 (seed 1) stopped with exit {killed['exit_code']} after epoch {reached} of {main} and "
                 f"wrote no metrics.json. r014 (seed 0) and r016 (seed 2) finished: test top-1 "
                 f"{self.metrics('r014_fp32_s0')['fp32']['top1']:.2f} and {self.metrics('r016_fp32_s2')['fp32']['top1']:.2f}. "
                 "I'll run seed 1 again.")
        self.commit("FP32 seeds 0 to 2 with the new normalization (seed 1 was killed)")

        # Tue, day 8: seed 1 again
        self.at(8, "09:30")
        self.user("Rerun seed 1.")
        self.sweep(1, extra=" --only seed=1")
        row = self.rows("run_id = 'r017_fp32_s1'")[0]
        self.say(f"r017_fp32_s1: test top-1 {row['fp32_top1']:.2f}. runs.db names it as the rerun of {row['rerun_of']}.")
        self.commit("Rerun FP32 seed 1")

        # Wed, day 9: evaluation batch, then the per-tensor sweep on three seeds
        self.at(9, "09:50")
        self.user("Run the per-tensor calibration sweep on all three seeds.")
        self.say("Evaluation is most of each quantize run's time. Checking that batches of 512 give the same top-1 "
                 "as 256 before I switch.")
        before = json.loads(self.run_ok("python eval.py results/r014_fp32_s0")[-1])["metrics"]
        self.edit("configs/eval.yaml", "batch_size: 256", "batch_size: 512")
        after = json.loads(self.run_ok("python eval.py results/r014_fp32_s0")[-1])["metrics"]
        if after == before:
            self.say(f"The same: top-1 {after['top1']:.2f}, top-5 {after['top5']:.2f} at both sizes. Keeping 512.")
            self.commit("Evaluate in batches of 512")
        else:
            self.say(f"Not the same (top-1 {before['top1']:.2f} at 256, {after['top1']:.2f} at 512). Keeping 256.")
            self.edit("configs/eval.yaml", "batch_size: 512", "batch_size: 256")
        self.write("configs/sweep.yaml", sweep_yaml("quantize", {"scheme": ["per_tensor"], "calib_images": CALIBRATION,
                                                                  "seed": SEEDS},
                                                    {0: "results/r014_fp32_s0", 1: "results/r017_fp32_s1",
                                                     2: "results/r016_fp32_s2"}))
        self.sweep(4)
        for seed in SEEDS:
            self.say(self.calibration_line("per_tensor", seed, after="r017"))
        self.commit("Calibration sweep, per-tensor, seeds 0 to 2")

        # Thu, day 10: two more calibration draws at 128 images
        self.at(10, "10:10")
        self.user("Is 128 images noisy? Try two more calibration draws on seed 0.")
        self.write("configs/sweep.yaml", sweep_yaml("quantize", {"scheme": ["per_tensor"], "calib_images": [128],
                                                                  "calib_seed": [1, 2], "seed": [0]},
                                                    {0: "results/r014_fp32_s0"}))
        self.sweep(2)
        draws = self.rows("stage = 'quantize' AND seed = 0 AND calib_images = 128 AND from_run = 'results/r014_fp32_s0'")
        self.say("128 calibration images on seed 0, by draw: "
                 + ", ".join(f"draw {r['calib_seed']} {r['int8_top1']:.2f}" for r in draws)
                 + f" (FP32 {draws[0]['fp32_top1']:.2f}).")
        self.commit("Two more calibration draws at 128 images, seed 0")

        # Fri, day 11: where it stands
        self.at(11, "11:00")
        self.user("Summarize where we are.")
        self.write("summarize.py")
        self.run_ok("python summarize.py")
        self.say(self.standing())
        self.note("2026-10-16: Next: write up INT8 against FP32 for the group meeting.")
        self.commit("summarize.py: one line per finished run")

        # the next sweep, set up and not run: the agent leaves it uncommitted
        self.user("Next week: per-channel on seeds 1 and 2 as well. Set up the sweep config, but don't run it yet.")
        self.write("configs/sweep.yaml", sweep_yaml("quantize", {"scheme": ["per_channel"], "calib_images": CALIBRATION,
                                                                  "seed": [1, 2]},
                                                    {1: "results/r017_fp32_s1", 2: "results/r016_fp32_s2"}))
        self.say("configs/sweep.yaml now holds per-channel on seeds 1 and 2, from r017 and r016: 8 runs. "
                 "Not run and not committed.")
        self.touch(self.agent_log)
        shutil.rmtree(self.shim, ignore_errors=True)

    def calibration_line(self, scheme, seed, short=False, after=None):
        where = f"stage = 'quantize' AND scheme = '{scheme}' AND seed = {seed} AND calib_seed = 0 AND status = 'done'"
        if after:
            where += f" AND run_id > '{after}'"
        rows = sorted(self.rows(where), key=lambda r: r["calib_images"])
        cells = ", ".join(f"{r['calib_images']} images {r['int8_top1']:.2f}" for r in rows)
        name = scheme.replace("_", "-")
        if short:
            return f"Per-tensor on the same model: {cells}."
        return f"INT8 {name}, seed {seed}, from {rows[0]['from_run']} (FP32 {rows[0]['fp32_top1']:.2f}): {cells}."

    def standing(self):
        fp32 = {s: self.metrics(r)["fp32"]["top1"] for s, r in ((0, "r014_fp32_s0"), (1, "r017_fp32_s1"), (2, "r016_fp32_s2"))}
        lines = ["Where it stands, from the run folders:",
                 f"- FP32, {epochs(self.main)}, seeds 0 to 2 (r014, r017, r016): "
                 + ", ".join(f"{fp32[s]:.2f}" for s in SEEDS) + "."]
        for seed in SEEDS:
            lines.append(f"- {self.calibration_line('per_tensor', seed, after='r017')}")
        lines.append(f"- {self.calibration_line('per_channel', 0)}")
        lines.append("- r015 (seed 1) was killed; r017 is its rerun.")
        return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", required=True, type=Path, help="a folder that does not exist yet")
    ap.add_argument("--data", help="a folder holding torchvision's cifar-10-batches-py (not with --tiny)")
    ap.add_argument("--tiny", action="store_true", help="random images and a short schedule")
    ap.add_argument("--threads", type=int, default=8, help="threads for the whole build, never more")
    ap.add_argument("--cores", help="CPUs to keep the build on, as 8-15 or 0,2,4 (Linux)")
    args = ap.parse_args()
    if args.out.exists():
        raise SystemExit(f"make_study: {args.out} exists; give a new folder")
    if not args.tiny and not args.data:
        raise SystemExit("make_study: --data <folder with cifar-10-batches-py> (or --tiny)")
    if args.cores:
        cores = set()
        for part in args.cores.split(","):
            lo, _, hi = part.partition("-")
            cores.update(range(int(lo), int(hi or lo) + 1))
        os.sched_setaffinity(0, cores)
    t0 = time.time()
    Study(args.out.resolve(), args.tiny, args.data, args.threads).build()
    print(f"make_study: built {args.out} in {time.time() - t0:.0f} s")


if __name__ == "__main__":
    main()
