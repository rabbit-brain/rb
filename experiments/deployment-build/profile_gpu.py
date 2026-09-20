r"""Where the time goes in a RAFT forward pass on GPU, with the loop split into its parts.

Astra's item 3. Fixed checkpoint, precision, resolution and batch size; warmed; every region
CUDA-synchronised; repeated; distributions recorded rather than a single number.

Instrumentation changes what it measures: synchronising around each region serialises work that
otherwise overlaps, so the instrumented total exceeds the clean end-to-end time. That gap is measured
and reported rather than hidden, and the shares below are shares of the INSTRUMENTED total.
"""
from __future__ import annotations
import argparse, json, statistics as st, sys, time
from pathlib import Path
import torch
from rabbit_brain.config import load_config
from rabbit_brain.adapters import load_adapter
from rabbit_brain.runner import environment_info, set_seeds

ap = argparse.ArgumentParser()
ap.add_argument("--config", type=Path, required=True)
ap.add_argument("--checkpoint", type=Path, required=True)
ap.add_argument("--cases", type=Path, required=True)
ap.add_argument("--n", type=int, default=12)
ap.add_argument("--reps", type=int, default=5)
ap.add_argument("--iters", type=int, default=12)
ap.add_argument("--out", type=Path, required=True)
a = ap.parse_args()

ids = [l.strip() for l in a.cases.read_text().splitlines() if l.strip()][:a.n]
cfg = load_config(a.config); cfg.adapter.mixed_precision = False
adapter = load_adapter(cfg); dev = cfg.adapter.device or "cpu"
set_seeds(0); model = adapter.load(a.checkpoint, dev)
corr_mod = sys.modules["corr"]
cases = [c for c in adapter.cases() if c.id in set(ids)]
pairs = []
for c in cases:
    i1, i2 = adapter._load_pair(c, dev)
    pad = adapter._utils.InputPadder(i1.shape, mode="kitti")
    p1, p2 = pad.pad(i1, i2); pairs.append((c.id, p1, p2))
print(f"{len(pairs)} cases, padded {tuple(pairs[0][1].shape)}, {a.iters} iterations, {a.reps} reps, {dev}")

T: dict[str, list[float]] = {}
def region(name, fn):
    torch.cuda.synchronize(); t0 = time.perf_counter(); r = fn(); torch.cuda.synchronize()
    T.setdefault(name, []).append(time.perf_counter() - t0); return r

def instrumented(p1, p2, iters):
    with torch.no_grad():
        i1 = (2 * (p1 / 255.0) - 1.0).contiguous(); i2 = (2 * (p2 / 255.0) - 1.0).contiguous()
        f = region("encoder fnet", lambda: model.fnet([i1, i2]))
        f1, f2 = f[0].float(), f[1].float()
        corr_fn = region("correlation build", lambda: corr_mod.CorrBlock(f1, f2, radius=model.args.corr_radius))
        cn = region("encoder cnet", lambda: model.cnet(i1))
        net, inp = torch.split(cn, [model.hidden_dim, model.context_dim], dim=1)
        net, inp = torch.tanh(net), torch.relu(inp)
        coords0, coords1 = model.initialize_flow(i1)
        for _ in range(iters):
            coords1 = coords1.detach()
            corr = region("correlation lookup", lambda: corr_fn(coords1))
            out = region("refinement (update_block)", lambda: model.update_block(net, inp, corr, coords1 - coords0))
            net, up_mask, delta = out
            coords1 = coords1 + delta
        return region("upsampling (hoisted, 1 call)", lambda: model.upsample_flow(coords1 - coords0, up_mask))

def clean(p1, p2, iters):
    with torch.no_grad():
        i1 = (2 * (p1 / 255.0) - 1.0).contiguous(); i2 = (2 * (p2 / 255.0) - 1.0).contiguous()
        f1, f2 = model.fnet([i1, i2]); f1, f2 = f1.float(), f2.float()
        corr_fn = corr_mod.CorrBlock(f1, f2, radius=model.args.corr_radius)
        net, inp = torch.split(model.cnet(i1), [model.hidden_dim, model.context_dim], dim=1)
        net, inp = torch.tanh(net), torch.relu(inp)
        coords0, coords1 = model.initialize_flow(i1)
        for _ in range(iters):
            coords1 = coords1.detach()
            net, up_mask, delta = model.update_block(net, inp, corr_fn(coords1), coords1 - coords0)
            coords1 = coords1 + delta
        return model.upsample_flow(coords1 - coords0, up_mask)

for _, p1, p2 in pairs:                                  # warm
    instrumented(p1, p2, a.iters); clean(p1, p2, a.iters)
T.clear()

cleans = []
for r in range(a.reps):
    for _, p1, p2 in pairs:
        instrumented(p1, p2, a.iters)
        torch.cuda.synchronize(); t0 = time.perf_counter(); clean(p1, p2, a.iters); torch.cuda.synchronize()
        cleans.append(time.perf_counter() - t0)

per_pass = {k: (sum(v) / (a.reps * len(pairs))) for k, v in T.items()}
inst_total = sum(per_pass.values())
clean_med = st.median(cleans)
print(f"\ncomponent shares of the instrumented pass ({a.iters} iterations)")
rows = []
for k in sorted(per_pass, key=lambda x: -per_pass[x]):
    v = T[k]; n_calls = len(v) / (a.reps * len(pairs))
    rows.append({"component": k, "ms_per_pass": per_pass[k] * 1000, "share": per_pass[k] / inst_total,
                 "calls_per_pass": n_calls, "ms_per_call": st.median(v) * 1000})
    print(f"    {k:<30} {per_pass[k]*1000:7.2f} ms  {per_pass[k]/inst_total*100:5.1f}%   "
          f"{n_calls:4.0f} call(s) x {st.median(v)*1000:6.3f} ms")
loop = sum(per_pass[k] for k in per_pass if k in ("correlation lookup", "refinement (update_block)"))
print(f"\n    loop (lookup + refinement)     {loop*1000:7.2f} ms  {loop/inst_total*100:5.1f}% of the instrumented pass")
print(f"    instrumented total             {inst_total*1000:7.2f} ms")
print(f"    clean end-to-end (median)      {clean_med*1000:7.2f} ms")
print(f"    instrumentation overhead       {(inst_total-clean_med)*1000:+7.2f} ms "
      f"({(inst_total/clean_med-1)*100:+.1f}%), from serialising regions that otherwise overlap")
print(f"\n    loop share of the CLEAN pass, rescaled: {loop/clean_med*100:.1f}% "
      f"(upper bound; the sync gap is not attributable to one component)")
a.out.write_text(json.dumps({"iters": a.iters, "reps": a.reps, "cases": len(pairs), "rows": rows,
                             "instrumented_total_s": inst_total, "clean_median_s": clean_med,
                             "loop_share_instrumented": loop / inst_total,
                             "environment": environment_info(dev)}, indent=1) + "\n", encoding="utf-8")
print(f"\nwrote {a.out}")
