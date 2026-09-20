"""Component profile, retained-state cost, and an IMPLEMENTED streaming stopping policy, timed.

Astra's instructions 2, 3 and 4, on CPU. The GPU numbers are the ones that decide the question and
are not available here; this fixes the method, separates the components, and measures the policy
rather than predicting it from a fit.

The policy implemented is the STREAMING one: a threshold applied independently to each case, decided
from that case's own step-8 score, continuing the same execution without restarting. That is the
deployable execution model. The batch quantile rule is a different policy and its retained state is
measured here rather than assumed free.
"""
from __future__ import annotations
import statistics, sys, time
from pathlib import Path
import torch

sys.path.insert(0, "/home/claude/exp")
from rabbit_brain.config import load_config
from rabbit_brain.adapters import load_adapter
from rabbit_brain.runner import set_seeds

REPEATS = 3
THETA = 0.963          # frozen on the configuration half, u8, absolute rule
SCALE = 8.0            # trajectory_scale: delta_flow is at 1/8 resolution

cfg = load_config(Path("rb.toml"))
adapter = load_adapter(cfg)
set_seeds(0)
model = adapter.load(Path("ckpt/raft-things.pth"), "cpu")
adapter._import()
case = list(adapter.cases())[0]
im1, im2 = adapter._load_pair(case, "cpu")
padder = adapter._utils.InputPadder(im1.shape, mode="kitti")
im1p, im2p = padder.pad(im1, im2)
print(f"case {case.id}, padded {tuple(im1p.shape)}, torch {torch.__version__}, "
      f"threads {torch.get_num_threads()}, CPU\n")

# ---------------------------------------------------------------- component profile
acc = {}
def timed(name, fn):
    def wrapper(*a, **k):
        t0 = time.perf_counter(); out = fn(*a, **k); acc[name] = acc.get(name, 0.0) + time.perf_counter() - t0
        return out
    return wrapper

corr_mod = sys.modules.get("corr")
orig = {"fnet": model.fnet.forward, "cnet": model.cnet.forward,
        "update": model.update_block.forward, "upsample": model.upsample_flow}
if corr_mod:
    orig["corr_init"] = corr_mod.CorrBlock.__init__
    orig["corr_call"] = corr_mod.CorrBlock.__call__

def instrument(on):
    model.fnet.forward = timed("encoder fnet", orig["fnet"]) if on else orig["fnet"]
    model.cnet.forward = timed("encoder cnet", orig["cnet"]) if on else orig["cnet"]
    model.update_block.forward = timed("update block", orig["update"]) if on else orig["update"]
    model.upsample_flow = timed("upsample_flow", orig["upsample"]) if on else orig["upsample"]
    if corr_mod:
        corr_mod.CorrBlock.__init__ = timed("corr build", orig["corr_init"]) if on else orig["corr_init"]
        corr_mod.CorrBlock.__call__ = timed("corr lookup", orig["corr_call"]) if on else orig["corr_call"]

with torch.no_grad():
    model(im1p, im2p, iters=4, test_mode=True)                      # warm up

instrument(True); acc.clear()
with torch.no_grad():
    t0 = time.perf_counter(); model(im1p, im2p, iters=12, test_mode=True); total12 = time.perf_counter() - t0
instrument(False)
print(f"component profile, one 12-iteration forward, total {total12:.3f} s")
named = sum(acc.values())
for k, v in sorted(acc.items(), key=lambda x: -x[1]):
    print(f"    {k:<14} {v:>7.3f} s   {v / total12 * 100:>5.1f}%")
print(f"    {'unattributed':<14} {total12 - named:>7.3f} s   {(total12 - named) / total12 * 100:>5.1f}%")
loop = acc.get("update block", 0) + acc.get("corr lookup", 0) + acc.get("upsample_flow", 0)
print(f"\n    per-iteration work (update + lookup + upsample): {loop:.3f} s, {loop/total12*100:.1f}%")
print(f"    of which upsample_flow {acc.get('upsample_flow',0):.3f} s. In test_mode only the LAST")
print(f"    upsample is used, so 11 of 12 calls are dead work a deployment would hoist out.")

# ---------------------------------------------------------------- retained state for a batch rule
if corr_mod:
    with torch.no_grad():
        f1, f2 = model.fnet([2 * (im1p / 255.0) - 1.0, 2 * (im2p / 255.0) - 1.0])
        cb = corr_mod.CorrBlock(f1.float(), f2.float(), radius=model.args.corr_radius)
        pyr = sum(t.numel() * t.element_size() for t in cb.corr_pyramid)
        cn = model.cnet(2 * (im1p / 255.0) - 1.0)
        ctx = cn.numel() * cn.element_size()
    print(f"\nretained state a BATCH quantile rule must hold per case until the decision")
    print(f"    correlation pyramid {pyr/1e6:>8.1f} MB")
    print(f"    context + hidden    {ctx/1e6:>8.1f} MB")
    print(f"    total               {(pyr+ctx)/1e6:>8.1f} MB per case, times the batch width")

# ---------------------------------------------------------------- implemented streaming policy
def run_policy(im1p, im2p, theta, iters_lo=8, iters_hi=12):
    """Run iters_lo, score from the last coarse update, and continue the SAME execution if it clears."""
    with torch.no_grad():
        i1 = 2 * (im1p / 255.0) - 1.0; i2 = 2 * (im2p / 255.0) - 1.0
        i1, i2 = i1.contiguous(), i2.contiguous()
        f1, f2 = model.fnet([i1, i2]); f1, f2 = f1.float(), f2.float()
        corr_fn = corr_mod.CorrBlock(f1, f2, radius=model.args.corr_radius)
        cn = model.cnet(i1)
        net, inp = torch.split(cn, [model.hidden_dim, model.context_dim], dim=1)
        net, inp = torch.tanh(net), torch.relu(inp)
        coords0, coords1 = model.initialize_flow(i1)
        flow_up = None; last = None
        for itr in range(iters_hi):
            coords1 = coords1.detach()
            corr = corr_fn(coords1)
            flow = coords1 - coords0
            net, up_mask, delta = model.update_block(net, inp, corr, flow)
            coords1 = coords1 + delta
            flow_up = model.upsample_flow(coords1 - coords0, up_mask)
            last = delta
            if itr + 1 == iters_lo:
                d = last.detach().float() * SCALE                       # the controller, priced in
                score = float((d ** 2).sum(dim=-3).sqrt().mean().item())
                if score < theta:
                    return flow_up, iters_lo, score
        return flow_up, iters_hi, score

with torch.no_grad():
    ref12 = model(im1p, im2p, iters=12, test_mode=True)[1]
    ref8 = model(im1p, im2p, iters=8, test_mode=True)[1]
always, _, sc = run_policy(im1p, im2p, theta=float("-inf"))
never, _, _ = run_policy(im1p, im2p, theta=float("inf"))
print(f"\npolicy correctness: always-continue == iters 12 bitwise: {torch.equal(always, ref12)}; "
      f"never-continue == iters 8 bitwise: {torch.equal(never, ref8)}")
print(f"this case's step-8 score {sc:.4f} vs threshold {THETA} -> "
      f"{'continue' if sc >= THETA else 'stop'}")

def med(fn):
    runs = []
    for _ in range(REPEATS):
        t0 = time.perf_counter(); fn(); runs.append(time.perf_counter() - t0)
    return statistics.median(runs), runs

t12, r12 = med(lambda: model(im1p, im2p, iters=12, test_mode=True) if torch.no_grad() else None)
with torch.no_grad():
    t12, r12 = med(lambda: model(im1p, im2p, iters=12, test_mode=True))
    t8, r8 = med(lambda: model(im1p, im2p, iters=8, test_mode=True))
    tp_c, rp_c = med(lambda: run_policy(im1p, im2p, theta=float("-inf")))
    tp_s, rp_s = med(lambda: run_policy(im1p, im2p, theta=float("inf")))

print(f"\nmeasured, median of {REPEATS}")
print(f"    plain inference, 12 iterations   {t12:.3f} s   {[round(x,3) for x in r12]}")
print(f"    plain inference,  8 iterations   {t8:.3f} s   {[round(x,3) for x in r8]}")
print(f"    policy, continued to 12          {tp_c:.3f} s   {[round(x,3) for x in rp_c]}")
print(f"    policy, stopped at 8             {tp_s:.3f} s   {[round(x,3) for x in rp_s]}")
print(f"\n    controller overhead when continuing: {tp_c - t12:+.4f} s")
print(f"    controller overhead when stopping  : {tp_s - t8:+.4f} s")
q = 0.47
exp_policy = q * tp_c + (1 - q) * tp_s
print(f"\n    at the frozen 47% continue rate: expected {exp_policy:.3f} s per case")
print(f"    against plain 12 at {t12:.3f} s -> {(t12 - exp_policy) / t12 * 100:+.1f}% latency")
mix = 0.449 * (t12 - (t12 - t8) / 4) + 0.551 * t12
print(f"    against the free 11/12 mixture at {mix:.3f} s -> "
      f"{(mix - exp_policy) / mix * 100:+.1f}% latency")
