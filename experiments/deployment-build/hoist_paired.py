r"""Paired measurement of the upsampling hoist, same design as overhead.py.

The 4.5% figure was a difference of medians between two separately-timed blocks on one case. The
same run produced a physically impossible -60 ms elsewhere, so a 174 ms difference deserves a paired
check before it is quoted as an independent optimisation.
"""
from __future__ import annotations
import statistics, sys, time
from pathlib import Path
import torch

sys.path.insert(0, "/home/claude/exp")
from rabbit_brain.config import load_config
from rabbit_brain.adapters import load_adapter
from rabbit_brain.runner import set_seeds

ROUNDS = 6
cfg = load_config(Path("rb.toml"))
adapter = load_adapter(cfg); adapter._import()
set_seeds(0)
model = adapter.load(Path("ckpt/raft-things.pth"), "cpu")
corr_mod = sys.modules["corr"]
pairs = []
for case in adapter.cases():
    im1, im2 = adapter._load_pair(case, "cpu")
    padder = adapter._utils.InputPadder(im1.shape, mode="kitti")
    a, b = padder.pad(im1, im2)
    pairs.append((case.id, a, b))


def raft(im1p, im2p, iters=12, hoist=True):
    with torch.no_grad():
        i1 = (2 * (im1p / 255.0) - 1.0).contiguous(); i2 = (2 * (im2p / 255.0) - 1.0).contiguous()
        f1, f2 = model.fnet([i1, i2]); f1, f2 = f1.float(), f2.float()
        corr_fn = corr_mod.CorrBlock(f1, f2, radius=model.args.corr_radius)
        net, inp = torch.split(model.cnet(i1), [model.hidden_dim, model.context_dim], dim=1)
        net, inp = torch.tanh(net), torch.relu(inp)
        coords0, coords1 = model.initialize_flow(i1)
        up = None
        for itr in range(iters):
            coords1 = coords1.detach()
            corr = corr_fn(coords1)
            net, up_mask, delta = model.update_block(net, inp, corr, coords1 - coords0)
            coords1 = coords1 + delta
            if not hoist:
                up = model.upsample_flow(coords1 - coords0, up_mask)
        if hoist:
            up = model.upsample_flow(coords1 - coords0, up_mask)
        return up


with torch.no_grad():
    ok = all(torch.equal(raft(a, b, 12, True), raft(a, b, 12, False)) for _, a, b in pairs)
    print(f"hoisted output == every-iteration output, all {len(pairs)} cases: {ok}\n")
    for _, a, b in pairs:                                            # warm
        raft(a, b, 12, True); raft(a, b, 12, False)

paired = {cid: [] for cid, _, _ in pairs}
base = {cid: [] for cid, _, _ in pairs}
with torch.no_grad():
    for r in range(ROUNDS):
        for cid, a, b in pairs:
            if r % 2 == 0:
                t0 = time.perf_counter(); raft(a, b, 12, False); tn = time.perf_counter() - t0
                t0 = time.perf_counter(); raft(a, b, 12, True);  th = time.perf_counter() - t0
            else:
                t0 = time.perf_counter(); raft(a, b, 12, True);  th = time.perf_counter() - t0
                t0 = time.perf_counter(); raft(a, b, 12, False); tn = time.perf_counter() - t0
            paired[cid].append(tn - th); base[cid].append(tn)

print(f"paired saving from hoisting, {ROUNDS} rounds, order alternated")
alld, allb = [], []
for cid, _, _ in pairs:
    v, bl = paired[cid], base[cid]; alld += v; allb += bl
    m, mb = statistics.median(v), statistics.median(bl)
    print(f"    {cid}  median {m*1000:+7.1f} ms on a {mb*1000:6.1f} ms pass  = {m/mb*100:+5.2f}%   "
          f"({sum(1 for x in v if x>0)}/{len(v)} positive)")
md, mbb = statistics.median(alld), statistics.median(allb)
print(f"\n    pooled median {md*1000:+.1f} ms on a {mbb*1000:.1f} ms pass = {md/mbb*100:+.2f}% "
      f"of the unhoisted pass; {sum(1 for x in alld if x>0)}/{len(alld)} positive")
print(f"    pod single-case block timing gave +4.5% at 1248x376; this is 128x256, "
      f"where the loop is a smaller share of the pass")
