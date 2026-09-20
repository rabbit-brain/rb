"""How much of a RAFT forward pass is the refinement loop?

The adaptive-allocation idea only pays if the loop is a large enough share of end-to-end latency.
Total time should be a + b*n: `a` is everything done once (feature encoder, context encoder,
correlation volume) and `b*n` is the loop. Fit that line and the share falls out.

CPU only. The split on a GPU is different and needs its own measurement; this establishes the
structure and the method, not the number that matters.
"""
from __future__ import annotations
import statistics, sys, time
from pathlib import Path
import torch

sys.path.insert(0, "/home/claude/exp")
from rabbit_brain.config import load_config
from rabbit_brain.adapters import load_adapter
from rabbit_brain.recorder import TrajectoryRecorder
from rabbit_brain.runner import set_seeds

COUNTS = [2, 4, 8, 12, 16]
REPEATS = 3

cfg = load_config(Path("rb.toml"))
adapter = load_adapter(cfg)
set_seeds(0)
model = adapter.load(Path("ckpt/raft-things.pth"), "cpu")
case = list(adapter.cases())[0]
im1, im2 = adapter._load_pair(case, "cpu")
padder = adapter._utils.InputPadder(im1.shape, mode="kitti")
im1p, im2p = padder.pad(im1, im2)
print(f"case {case.id}, padded {tuple(im1p.shape)}, torch {torch.__version__}, "
      f"threads {torch.get_num_threads()}\n")

with torch.no_grad():
    model(im1p, im2p, iters=4, test_mode=True)          # warm up

times = {}
for n in COUNTS:
    runs = []
    for _ in range(REPEATS):
        t0 = time.perf_counter()
        with torch.no_grad():
            model(im1p, im2p, iters=n, test_mode=True)
        runs.append(time.perf_counter() - t0)
    times[n] = statistics.median(runs)
    print(f"  {n:>2} iterations: median {times[n]:.3f} s   (runs {[round(r,3) for r in runs]})")

xs = list(times)
ys = [times[n] for n in xs]
mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sum((x - mx) ** 2 for x in xs)
a = my - b * mx
print(f"\n  fit: total = {a:.3f} s + {b:.4f} s per iteration")
ss_res = sum((y - (a + b * x)) ** 2 for x, y in zip(xs, ys))
ss_tot = sum((y - my) ** 2 for y in ys)
print(f"  R^2 = {1 - ss_res / ss_tot:.4f}")

print(f"\n  at 12 iterations: once-only {a:.3f} s, loop {b * 12:.3f} s, "
      f"loop share s = {b * 12 / (a + b * 12):.3f}")
s = b * 12 / (a + b * 12)
print("\n  what a mean-iteration saving is worth at this share")
print(f"    {'rule':<26} {'mean iters':>11} {'iters saved':>12} {'latency saved':>14}")
for label, m in (("oracle", 8.84), ("C  B + history", 9.20), ("B  latest update", 9.68),
                 ("fixed 8 everywhere", 8.00)):
    frac = (12 - m) / 12
    print(f"    {label:<26} {m:>11.2f} {frac * 100:>11.1f}% {s * frac * 100:>13.1f}%")
print("\n  loop share needed for a 10% end-to-end saving, before any decision overhead")
for label, m in (("oracle", 8.84), ("C  B + history", 9.20), ("B  latest update", 9.68)):
    print(f"    {label:<26} s >= {0.10 / ((12 - m) / 12):.3f}")
