"""Config, run folders, logs and git state, shared by train.py, quantize.py, eval.py and sweep.py."""
import json
import os
import shlex
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_yaml(path):
    with open(ROOT / path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def dump_yaml(obj, path):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        yaml.safe_dump(obj, f, sort_keys=False, default_flow_style=None)


def dump_json(obj, path):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, indent=2)
        f.write("\n")


def git_state():
    """The commit HEAD is at, and whether tracked files differ from it."""
    def git(*args):
        r = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)
        return r.stdout.strip() if r.returncode == 0 else None
    head = git("rev-parse", "HEAD")
    dirty = git("status", "--porcelain", "--untracked-files=no")
    return {"commit": head, "dirty": bool(dirty)}


def command_line():
    return " ".join(["python", *(shlex.quote(a) for a in sys.argv)])


class Log:
    """Writes each line to stdout and to the run's log file, with the time."""

    def __init__(self, path):
        self.f = open(path, "a", encoding="utf-8", newline="\n")

    def __call__(self, msg):
        line = f"[{time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime())}] {msg}"
        print(line, flush=True)
        self.f.write(line + "\n")
        self.f.flush()


def run_folder(out):
    out = Path(out)
    if out.exists() and any(out.iterdir()):
        raise SystemExit(f"{out} already holds a run; give a new --out")
    out.mkdir(parents=True, exist_ok=True)
    return out


def set_threads():
    import torch
    n = os.environ.get("OMP_NUM_THREADS")
    if n:
        torch.set_num_threads(int(n))
    return torch.get_num_threads()


def file_kb(path):
    return round(os.path.getsize(path) / 1024, 1)
