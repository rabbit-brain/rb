"""Hoisted upsampling, applied to BOTH baseline and policy, with interleaved timing.

In upstream RAFT the per-iteration `flow_up` is only appended to `flow_predictions` for the training
loss; it never feeds the next iteration. Under final-output-only inference eleven of twelve calls are
dead work. Hoisting it out is therefore exact, and it must be applied to the baseline too, otherwise
the policy is measured against a strawman.

Timing is INTERLEAVED, one run of each configuration per round, because this container drifts about
10% between invocations and block-timed configurations inherit that drift as a fake effect.
"""
from __future__ import annotations
import statistics, sys, time
from pathlib import Path
import torch

sys.path.insert(0, "/home/claude/exp")
from rabbit_brain.config import load_config
from rabbit_brain.adapters import load_adapter
from rabbit_brain.runner import set_seeds

ROUNDS = 5
THETA = 0.7017        # frozen on the configuration half, u8, one-sided guard z = 1.645
CONTINUE_RATE = 0.42  # what that threshold continues on the held-out half
SCALE = 8.0

cfg = load_config(Path("rb.toml"))
adapter = load_adapter(cfg); adapter._import()
set_seeds(0)
model = adapter.load(Path("ckpt/raft-things.pth"), "cpu")
case = list(adapter.cases())[0]
im1, im2 = adapter._load_pair(case, "cpu")
padder = adapter._utils.InputPadder(im1.shape, mode="kitti")
im1p, im2p = padder.pad(im1, im2)
corr_mod = sys.modules["corr"]
print(f"case {case.id}, padded {tuple(im1p.shape)}, CPU, threads {torch.get_num_threads()}\n")


def raft(im1p, im2p, iters=12, hoist=True, theta=None, iters_lo=8):
    """One loop. hoist=True upsamples once at the end. theta set makes it the streaming policy."""
    with torch.no_grad():
        i1 = (2 * (im1p / 255.0) - 1.0).contiguous(); i2 = (2 * (im2p / 255.0) - 1.0).contiguous()
        f1, f2 = model.fnet([i1, i2]); f1, f2 = f1.float(), f2.float()
        corr_fn = corr_mod.CorrBlock(f1, f2, radius=model.args.corr_radius)
        net, inp = torch.split(model.cnet(i1), [model.hidden_dim, model.context_dim], dim=1)
        net, inp = torch.tanh(net), torch.relu(inp)
        coords0, coords1 = model.initialize_flow(i1)
        flow_up = None; n_done = 0
        for itr in range(iters):
            coords1 = coords1.detach()
            corr = corr_fn(coords1)
            net, up_mask, delta = model.update_block(net, inp, corr, coords1 - coords0)
            coords1 = coords1 + delta
            n_done = itr + 1
            if not hoist:
                flow_up = model.upsample_flow(coords1 - coords0, up_mask)
            if theta is not None and n_done == iters_lo:
                d = delta.detach().float() * SCALE                    # the controller, priced in
                if float((d ** 2).sum(dim=-3).sqrt().mean().item()) < theta:
                    break
        if hoist:
            flow_up = model.upsample_flow(coords1 - coords0, up_mask)
        return flow_up, n_done


with torch.no_grad():
    stock12 = model(im1p, im2p, iters=12, test_mode=True)[1]
    stock8 = model(im1p, im2p, iters=8, test_mode=True)[1]
h12, _ = raft(im1p, im2p, 12, hoist=True)
h8, _ = raft(im1p, im2p, 8, hoist=True)
pc, nc = raft(im1p, im2p, 12, hoist=True, theta=float("-inf"))
ps, ns = raft(im1p, im2p, 12, hoist=True, theta=float("inf"))
print("exactness of the hoist and the policy, bitwise against stock RAFT")
print(f"    hoisted 12 == stock 12 : {torch.equal(h12, stock12)}")
print(f"    hoisted  8 == stock  8 : {torch.equal(h8, stock8)}")
print(f"    policy always-continue == stock 12 : {torch.equal(pc, stock12)}  ({nc} iterations)")
print(f"    policy never-continue  == stock  8 : {torch.equal(ps, stock8)}  ({ns} iterations)")

CONFIGS = {
    "stock 12 (upsample every iter)": lambda: model(im1p, im2p, iters=12, test_mode=True),
    "optimised 12 (hoisted)":         lambda: raft(im1p, im2p, 12, hoist=True),
    "optimised  8 (hoisted)":         lambda: raft(im1p, im2p, 8, hoist=True),
    "policy, continued to 12":        lambda: raft(im1p, im2p, 12, hoist=True, theta=float("-inf")),
    "policy, stopped at 8":           lambda: raft(im1p, im2p, 12, hoist=True, theta=float("inf")),
}
for f in CONFIGS.values():
    with torch.no_grad(): f()                                        # warm every path

runs = {k: [] for k in CONFIGS}
keys = list(CONFIGS)
with torch.no_grad():
    for r in range(ROUNDS):
        for k in keys[r % len(keys):] + keys[:r % len(keys)]:        # rotate the order each round
            t0 = time.perf_counter(); CONFIGS[k](); runs[k].append(time.perf_counter() - t0)

print(f"\ninterleaved timing, {ROUNDS} rounds, order rotated each round")
med = {}
for k in keys:
    med[k] = statistics.median(runs[k])
    print(f"    {k:<32} median {med[k]:.3f} s  min {min(runs[k]):.3f}  "
          f"spread {max(runs[k]) - min(runs[k]):.3f}")

o12, o8 = med["optimised 12 (hoisted)"], med["optimised  8 (hoisted)"]
pc_t, ps_t = med["policy, continued to 12"], med["policy, stopped at 8"]
s12 = med["stock 12 (upsample every iter)"]
print(f"\n    the hoist alone, on the baseline: {(s12 - o12) / s12 * 100:+.1f}% "
      f"({s12:.3f} -> {o12:.3f} s)")
print(f"    controller overhead: continuing {pc_t - o12:+.4f} s, stopping {ps_t - o8:+.4f} s")

exp = CONTINUE_RATE * pc_t + (1 - CONTINUE_RATE) * ps_t
print(f"\n    policy at the frozen {CONTINUE_RATE:.0%} continue rate: {exp:.3f} s per case")
print(f"      vs optimised fixed 12 : {(o12 - exp) / o12 * 100:+.1f}%   <- the comparison that counts")
print(f"      vs stock fixed 12     : {(s12 - exp) / s12 * 100:+.1f}%   (inflated by the hoist)")
b = (o12 - o8) / 4
print(f"\n    hoisted per-iteration cost {b:.4f} s; loop share at 12 iterations "
      f"{b * 12 / o12 * 100:.1f}% (was 58.4% unhoisted)")
