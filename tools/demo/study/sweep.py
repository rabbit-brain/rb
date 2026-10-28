"""Runs every cell of a sweep config, a few at a time, and records each run in results/runs.db.

    python sweep.py configs/sweep.yaml --jobs 4 --threads 2
    python sweep.py configs/sweep.yaml --only seed=1         # one cell again; a crashed run of it is named as rerun_of

A cell is one train.py or quantize.py run in a new folder results/rNNN_<name>. Its row says when it started and
finished, its exit code, and its top-1 numbers read back from the folder's metrics.json."""
import argparse
import itertools
import json
import os
import re
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path

from common import ROOT, git_state, load_yaml

DB = ROOT / "results" / "runs.db"
SCHEMA = """CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY,
  stage TEXT NOT NULL,
  seed INTEGER NOT NULL,
  scheme TEXT,
  calib_images INTEGER,
  calib_seed INTEGER,
  from_run TEXT,
  folder TEXT NOT NULL,
  command TEXT NOT NULL,
  git_commit TEXT,
  status TEXT NOT NULL,
  exit_code INTEGER,
  fp32_top1 REAL,
  int8_top1 REAL,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  rerun_of TEXT
)"""
CELL_KEYS = ("stage", "seed", "scheme", "calib_images", "calib_seed")
lock = threading.Lock()


def stamp():
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())


def db(sql, args=()):
    with lock:
        con = sqlite3.connect(DB)
        try:
            con.execute(SCHEMA)
            rows = con.execute(sql, args).fetchall()
            con.commit()
            return rows
        finally:
            con.close()


def next_number():
    taken = [int(m.group(1)) for p in (ROOT / "results").glob("r*_*") if (m := re.match(r"r(\d+)_", p.name))]
    return max(taken, default=0) + 1


def cells(cfg, only):
    grid = cfg["grid"]
    defaults = {"scheme": None, "calib_images": None, "calib_seed": None}
    if cfg["stage"] == "quantize":                 # what a cell leaves out, it runs at configs/quant.yaml's value
        quant = load_yaml("configs/quant.yaml")
        defaults = {"scheme": quant["scheme"], "calib_images": quant["calibration"]["images"],
                    "calib_seed": quant["calibration"]["subset_seed"]}
    for values in itertools.product(*grid.values()):
        cell = {"stage": cfg["stage"], **defaults, **dict(zip(grid, values))}
        if all(str(cell.get(k)) == v for k, v in only):
            yield cell


def name_of(cell):
    if cell["stage"] == "train":
        return f"fp32_s{cell['seed']}"
    scheme = {"per_tensor": "pt", "per_channel": "pc"}[cell["scheme"]]
    draw = f"_d{cell['calib_seed']}" if cell.get("calib_seed") else ""
    return f"int8_{scheme}_c{cell['calib_images']}_s{cell['seed']}{draw}"


def command_of(cell, cfg, folder):
    if cell["stage"] == "train":
        return ["python", "train.py", "--seed", str(cell["seed"]), "--out", folder]
    cmd = ["python", "quantize.py", "--from", cfg["from"][cell["seed"]], "--scheme", cell["scheme"],
           "--calib-images", str(cell["calib_images"])]
    if cell.get("calib_seed") is not None:
        cmd += ["--calib-seed", str(cell["calib_seed"])]
    return cmd + ["--out", folder]


def run_cell(cell, cfg, number, threads):
    run_id = f"r{number:03d}_{name_of(cell)}"
    folder = f"results/{run_id}"
    cmd = command_of(cell, cfg, folder)
    crashed = db("SELECT run_id FROM runs WHERE status = 'crashed' AND stage = ? AND seed = ? AND scheme IS ? AND "
                 "calib_images IS ? AND calib_seed IS ? ORDER BY started_at DESC LIMIT 1",
                 tuple(cell.get(k) for k in CELL_KEYS))
    db("INSERT INTO runs (run_id, stage, seed, scheme, calib_images, calib_seed, from_run, folder, command, git_commit, "
       "status, started_at, rerun_of) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'running', ?, ?)",
       (run_id, *(cell.get(k) for k in CELL_KEYS), cfg.get("from", {}).get(cell["seed"]) if cell["stage"] == "quantize" else None,
        folder, " ".join(cmd), git_state()["commit"], stamp(), crashed[0][0] if crashed else None))
    print(f"[{run_id}] start: {' '.join(cmd)}", flush=True)
    env = {**os.environ, "OMP_NUM_THREADS": str(threads), "MKL_NUM_THREADS": str(threads)}
    proc = subprocess.Popen([sys.executable, *cmd[1:]], cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    for line in proc.stdout:
        print(f"[{run_id}] {line.rstrip()}", flush=True)
    code = proc.wait()
    metrics_file = ROOT / folder / "metrics.json"
    metrics = json.loads(metrics_file.read_text(encoding="utf-8")) if code == 0 and metrics_file.exists() else None
    status = "done" if metrics else "crashed"
    db("UPDATE runs SET status = ?, exit_code = ?, fp32_top1 = ?, int8_top1 = ?, finished_at = ? WHERE run_id = ?",
       (status, code, (metrics or {}).get("fp32", {}).get("top1"), (metrics or {}).get("int8", {}).get("top1"), stamp(), run_id))
    print(f"[{run_id}] {status} (exit {code})", flush=True)
    return status


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("config")
    ap.add_argument("--jobs", type=int, default=1)
    ap.add_argument("--threads", type=int, default=2, help="threads per run")
    ap.add_argument("--only", action="append", default=[], help="key=value: run only the cells that match")
    args = ap.parse_args()
    cfg = load_yaml(args.config)
    cfg["from"] = {int(k): v for k, v in (cfg.get("from") or {}).items()}
    only = [tuple(o.split("=", 1)) for o in args.only]
    todo = list(cells(cfg, only))
    if not todo:
        raise SystemExit("no cell of the grid matches")
    (ROOT / "results").mkdir(exist_ok=True)
    first = next_number()
    print(f"sweep: {len(todo)} runs, {args.jobs} at a time, threads per run: {args.threads}", flush=True)
    statuses = []
    pending = list(enumerate(todo))
    running = []
    while pending or running:
        while pending and len(running) < args.jobs:
            i, cell = pending.pop(0)
            t = threading.Thread(target=lambda c=cell, n=first + i: statuses.append(run_cell(c, cfg, n, args.threads)))
            t.start()
            running.append(t)
        time.sleep(0.2)
        running = [t for t in running if t.is_alive()]
    done = statuses.count("done")
    print(f"sweep: {done} done, {len(statuses) - done} crashed", flush=True)
    return 0 if done == len(statuses) else 1


if __name__ == "__main__":
    sys.exit(main())
