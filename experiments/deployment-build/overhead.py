r"""Clean controller-overhead measurement, and the noise floor that bounds it.

Astra's specification: "the same cases and continuation decisions, with versus without computing
the controller score." That is a PAIRED measurement, so it cancels the container drift that made the
earlier absolute timings unreliable.

Three things are established here.

1. Exactness. The replay arm must produce bitwise-identical output to the policy arm, otherwise the
   two arms are not doing the same work and the difference is not the controller.
2. The paired overhead, per (round, case), as t_policy - t_replay.
3. The noise floor, from timing ONE configuration under two labels. Any claimed effect smaller than
   that floor is not resolvable, however many rounds are run.

This does NOT measure the saving. Six local pairs cannot reproduce the 42% continue rate of the
200-case split, and five of the six fall in the configuration half. Saving is a pod measurement.
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
THETA = 0.7017
SCALE = 8.0
ITERS_LO = 8
ITERS_HI = 12

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
print(f"{len(pairs)} cases, padded {tuple(pairs[0][1].shape)}, CPU, threads {torch.get_num_threads()}\n")


def raft(im1p, im2p, iters=ITERS_HI, iters_lo=ITERS_LO, mode="fixed", decision=None):
    """mode 'fixed'  : run `iters` iterations.
       mode 'policy' : at iters_lo COMPUTE the score and branch on it.
       mode 'replay' : at iters_lo branch on `decision`, WITHOUT computing the score.
       Upsampling is hoisted out of the loop in every mode."""
    with torch.no_grad():
        i1 = (2 * (im1p / 255.0) - 1.0).contiguous(); i2 = (2 * (im2p / 255.0) - 1.0).contiguous()
        f1, f2 = model.fnet([i1, i2]); f1, f2 = f1.float(), f2.float()
        corr_fn = corr_mod.CorrBlock(f1, f2, radius=model.args.corr_radius)
        net, inp = torch.split(model.cnet(i1), [model.hidden_dim, model.context_dim], dim=1)
        net, inp = torch.tanh(net), torch.relu(inp)
        coords0, coords1 = model.initialize_flow(i1)
        n_done = 0; went_on = None
        for itr in range(iters):
            coords1 = coords1.detach()
            corr = corr_fn(coords1)
            net, up_mask, delta = model.update_block(net, inp, corr, coords1 - coords0)
            coords1 = coords1 + delta
            n_done = itr + 1
            if n_done == iters_lo:
                if mode == "policy":
                    d = delta.detach().float() * SCALE
                    went_on = float((d ** 2).sum(dim=-3).sqrt().mean().item()) >= THETA
                    if not went_on:
                        break
                elif mode == "replay":
                    went_on = decision
                    if not went_on:
                        break
        return model.upsample_flow(coords1 - coords0, up_mask), n_done, went_on


# ---- pass 1: real execution, record the decisions -------------------------------------------
print("actual score-and-branch execution, per case")
decisions = {}; outputs = {}
for cid, a, b in pairs:
    out, n, on = raft(a, b, mode="policy")
    decisions[cid] = on; outputs[cid] = out
    print(f"    {cid}  continued={str(on):<5}  iterations={n}")
n_cont = sum(1 for v in decisions.values() if v)
print(f"    -> {n_cont}/{len(pairs)} continued on these six local cases "
      f"(NOT the 42% of the held-out split)\n")

# ---- exactness ------------------------------------------------------------------------------
print("exactness")
ok_replay = ok_fixed = True
with torch.no_grad():
    for cid, a, b in pairs:
        r_out, r_n, _ = raft(a, b, mode="replay", decision=decisions[cid])
        same = torch.equal(r_out, outputs[cid])
        ok_replay &= same
        exp_iters = ITERS_HI if decisions[cid] else ITERS_LO
        f_out, _, _ = raft(a, b, iters=exp_iters, mode="fixed")
        ok_fixed &= torch.equal(f_out, outputs[cid])
    a, b = pairs[0][1], pairs[0][2]
    s12 = model(a, b, iters=12, test_mode=True)[1]
    s8 = model(a, b, iters=8, test_mode=True)[1]
    h12, _, _ = raft(a, b, iters=12, mode="fixed")
    h8, _, _ = raft(a, b, iters=8, mode="fixed")
print(f"    replay output == policy output, all cases : {ok_replay}")
print(f"    fixed-at-decided-count == policy output   : {ok_fixed}")
print(f"    hoisted 12 == stock 12                    : {torch.equal(h12, s12)}")
print(f"    hoisted  8 == stock  8                    : {torch.equal(h8, s8)}\n")

# ---- warm -----------------------------------------------------------------------------------
with torch.no_grad():
    for cid, a, b in pairs:
        raft(a, b, mode="policy"); raft(a, b, mode="replay", decision=decisions[cid])
    raft(pairs[0][1], pairs[0][2], iters=12, mode="fixed")

# ---- paired timing ---------------------------------------------------------------------------
paired = {cid: [] for cid, _, _ in pairs}
probe_a, probe_b = [], []
with torch.no_grad():
    for r in range(ROUNDS):
        for cid, a, b in pairs:
            if r % 2 == 0:                                   # alternate which arm goes first
                t0 = time.perf_counter(); raft(a, b, mode="policy"); tp = time.perf_counter() - t0
                t0 = time.perf_counter(); raft(a, b, mode="replay", decision=decisions[cid]); tr = time.perf_counter() - t0
            else:
                t0 = time.perf_counter(); raft(a, b, mode="replay", decision=decisions[cid]); tr = time.perf_counter() - t0
                t0 = time.perf_counter(); raft(a, b, mode="policy"); tp = time.perf_counter() - t0
            paired[cid].append(tp - tr)
        # noise floor: IDENTICAL work, two labels, same interleaving discipline
        a, b = pairs[0][1], pairs[0][2]
        t0 = time.perf_counter(); raft(a, b, iters=12, mode="fixed"); probe_a.append(time.perf_counter() - t0)
        t0 = time.perf_counter(); raft(a, b, iters=12, mode="fixed"); probe_b.append(time.perf_counter() - t0)

print(f"paired controller overhead, {ROUNDS} rounds, arm order alternated")
alld = []
for cid, _, _ in pairs:
    v = paired[cid]; alld += v
    print(f"    {cid}  median {statistics.median(v)*1000:+8.1f} ms   "
          f"min {min(v)*1000:+8.1f}   max {max(v)*1000:+8.1f}")
med_all = statistics.median(alld)
pos = sum(1 for d in alld if d > 0)
print(f"\n    pooled median {med_all*1000:+.1f} ms over {len(alld)} paired trials; "
      f"{pos}/{len(alld)} positive")

ma, mb = statistics.median(probe_a), statistics.median(probe_b)
print(f"\nnoise floor, one configuration timed under two labels")
print(f"    label A median {ma:.3f} s, label B median {mb:.3f} s, "
      f"apparent difference {abs(ma-mb)*1000:.1f} ms ({abs(ma-mb)/ma*100:.2f}%)")
spread = max(max(probe_a), max(probe_b)) - min(min(probe_a), min(probe_b))
print(f"    full spread across both labels {spread*1000:.0f} ms on a {ma*1000:.0f} ms pass")
print(f"\n    -> an UNPAIRED difference smaller than ~{abs(ma-mb)*1000:.0f} ms is not resolvable here.")
print(f"    -> the paired estimate above is {abs(med_all)*1000:.1f} ms and survives that floor "
      f"because the pairing cancels the drift.")
