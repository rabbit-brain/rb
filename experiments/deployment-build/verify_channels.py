"""Correctness checks for the corrected recorder channel, on real weights and real images.

Four things have to be true before the ablation means anything:

  1. instrumentation does not change the model's output, bitwise, not merely in aggregate EPE;
  2. the corrected channel's last cumulative field IS the model's output, bitwise, so the channel is
     the output's own movement and not a reconstruction of it;
  3. the differences sum back to that output, so `convergence()`'s cumulative-sum reconstruction of the
     estimate trajectory is the real one;
  4. both channels have exactly one value per iteration.

Run from a directory with rb.toml, raft/, data/ and ckpt/.
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import torch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dual_recorder import DualChannelRecorder                     # noqa: E402
from rabbit_brain.config import load_config                        # noqa: E402
from rabbit_brain.adapters import load_adapter                     # noqa: E402
from rabbit_brain.recorder import TrajectoryRecorder               # noqa: E402
from rabbit_brain.runner import set_seeds                          # noqa: E402


def digest(t: torch.Tensor) -> str:
    return hashlib.sha256(t.detach().cpu().contiguous().numpy().tobytes()).hexdigest()[:16]


def main() -> int:
    cfg = load_config(Path("rb.toml"))
    mixed = "--mixed" in sys.argv
    if mixed:
        cfg.adapter.mixed_precision = True
    adapter = load_adapter(cfg)
    ckpt = Path("ckpt/raft-things.pth")
    set_seeds(0)
    model = adapter.load(ckpt, "cpu")
    iters = adapter.expected_iterations()
    cases = list(adapter.cases())[:2]
    print(f"checkpoint {ckpt}, {iters} iterations, mixed_precision={cfg.adapter.mixed_precision}, "
          f"{len(cases)} case(s)\n")

    ok = True
    for case in cases:
        # --- pass 1: the shipped path, coarse channel only
        set_seeds(0)
        plain = TrajectoryRecorder(scale=adapter.trajectory_scale)
        pred_plain = adapter.infer(model, case, plain)

        # --- pass 2: the same inference with both channels attached
        set_seeds(0)
        dual = DualChannelRecorder(coarse_scale=adapter.trajectory_scale)
        im1, _ = adapter._load_pair(case, "cpu")
        padder = adapter._utils.InputPadder(im1.shape, mode="kitti")
        with dual.attached(model):
            pred_dual = adapter.infer(model, case, dual.coarse)
        dual.finish(padder, expect=iters)

        out_p, out_d = pred_plain.output, pred_dual.output
        same_out = torch.equal(out_p, out_d)
        final = dual.final_output(padder)
        same_final = final is not None and torch.equal(final, out_d)

        states = torch.cumsum(torch.stack([f[0] if f.dim() == 4 else f for f in dual.fine._fields]), 0)
        recon = float((states[-1] - out_d.to(states.dtype)).abs().max().item())
        scale = float(out_d.abs().max().item())

        lens = (len(dual.coarse.values), len(dual.fine.values))
        good = same_out and same_final and lens == (iters, iters) and recon <= 1e-3

        print(f"case {case.id}")
        print(f"  output unchanged by instrumentation : {'yes' if same_out else 'NO'}  "
              f"({digest(out_p)} vs {digest(out_d)})")
        print(f"  corrected channel's last state IS the output : {'yes' if same_final else 'NO'}")
        print(f"  differences sum back to the output  : max |error| {recon:.3e} px "
              f"(output max {scale:.2f} px)")
        print(f"  values per channel                  : coarse {lens[0]}, corrected {lens[1]} "
              f"(expected {iters})")
        print(f"  coarse    {[round(v, 3) for v in dual.coarse.values]}")
        print(f"  corrected {[round(v, 3) for v in dual.fine.values]}")
        cc, fc = dual.coarse.convergence(), dual.fine.convergence()
        if cc and fc:
            print(f"  reversal rate   coarse {cc['sign_reversal_rate']:.4f}   corrected {fc['sign_reversal_rate']:.4f}")
            print(f"  mean cos        coarse {cc['mean_cos']:.4f}   corrected {fc['mean_cos']:.4f}")
            print(f"  displacement    coarse {cc['displacement_mean']:.4f}   corrected {fc['displacement_mean']:.4f}")
        print(f"  => {'OK' if good else 'FAILED'}\n")
        ok = ok and good
        dual.reset()

    print("ALL CHECKS PASSED" if ok else "CHECKS FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
