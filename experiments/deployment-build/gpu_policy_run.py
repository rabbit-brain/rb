r"""Execute the frozen policy on full-resolution cases and time it, paired, against the equally
optimised baseline. This closes the gap between the policy evaluated for accuracy and the
implementation measured for speed: both come from this one run, on the same cases.

It answers four questions and refuses to conflate them.

1. What does the frozen rule actually decide, per case, at full resolution? Recorded, with the score.
2. What is the error of what it emitted, per case, against the same baseline? Recorded.
3. What does it cost, per case, paired against the hoisted fixed-12 baseline? Measured.
4. What does the controller alone cost on GPU, where `.item()` forces a device synchronisation?
   Measured by replaying each case's own decision without computing the score, which is the only
   comparison in which the two arms do identical work.

Nothing here chooses anything. The threshold, the score, the guard and the branch point all come
from frozen_rule.py, whose sha256 is written into the output.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
import time
from pathlib import Path

import numpy as np
import torch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import frozen_rule as P

from rabbit_brain.config import load_config
from rabbit_brain.adapters import load_adapter
from rabbit_brain.runner import environment_info, set_seeds


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", type=Path, required=True)
    ap.add_argument("--checkpoint", type=Path, required=True)
    ap.add_argument("--cases", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--rounds", type=int, default=5)
    ap.add_argument("--archive", type=Path, default=None,
                    help="iteration_sweep JSON, to cross-check predicted against executed decisions")
    a = ap.parse_args()

    frozen_hash = sha256(Path(__file__).resolve().parent / "frozen_rule.py")
    print(f"frozen_rule.py sha256 {frozen_hash}")
    print(f"score {P.SCORE_NAME}, theta {P.THETA}, z {P.Z}, branch at {P.BRANCH_AT} of {P.MAX_ITERS}, "
          f"allowance {P.ALLOWANCE_PX} px\n")

    ids = {ln.strip() for ln in a.cases.read_text().splitlines() if ln.strip()}
    cfg = load_config(a.config)
    cfg.adapter.mixed_precision = False
    adapter = load_adapter(cfg)
    device = cfg.adapter.device or "cpu"
    set_seeds(0)
    model = adapter.load(a.checkpoint, device)
    corr_mod = sys.modules["corr"]
    if getattr(model.args, "alternate_corr", False):
        raise SystemExit("this loop assumes CorrBlock; alternate_corr is set")
    on_cuda = device.startswith("cuda")
    if not on_cuda:
        print("WARNING: not running on CUDA. The synchronisation question this run exists to "
              "answer is a GPU question.\n", file=sys.stderr)

    cases = [c for c in adapter.cases() if c.id in ids]
    missing = ids - {c.id for c in cases}
    if missing:
        raise SystemExit(f"{len(missing)} case id(s) not in the dataset, e.g. {sorted(missing)[:3]}")
    cases = [c for c in cases if c.gt is not None]
    print(f"{len(cases)} labelled cases on {device}, {a.rounds} timing rounds\n")

    prepared = []
    for c in cases:
        im1, im2 = adapter._load_pair(c, device)
        padder = adapter._utils.InputPadder(im1.shape, mode="kitti")
        p1, p2 = padder.pad(im1, im2)
        flow_gt, valid = adapter._frame_utils.readFlowKITTI(c.gt)
        flow_gt = torch.from_numpy(np.array(flow_gt).astype(np.float32)).permute(2, 0, 1)
        valid = torch.from_numpy(np.array(valid)).float()
        prepared.append((c.id, p1, p2, padder, flow_gt, valid))

    def epe(flow_up, padder, flow_gt, valid) -> float:
        """RAFT's validate_kitti formula, identical to RaftAdapter.metric_value."""
        flow = padder.unpad(flow_up[0]).detach().cpu()
        e = torch.sum((flow - flow_gt) ** 2, dim=0).sqrt().view(-1)
        return float(e[valid.view(-1) >= 0.5].mean().item())

    def run(p1, p2, mode: str, forced: bool | None = None):
        """mode 'policy'  : compute the frozen score at BRANCH_AT and branch on it.
           mode 'replay'  : branch on `forced` WITHOUT computing the score.
           mode 'fixed'   : run `forced` iterations unconditionally.
           Upsampling is hoisted in every mode, so the arms differ only where intended."""
        with torch.no_grad():
            i1 = (2 * (p1 / 255.0) - 1.0).contiguous()
            i2 = (2 * (p2 / 255.0) - 1.0).contiguous()
            f1, f2 = model.fnet([i1, i2]); f1, f2 = f1.float(), f2.float()
            corr_fn = corr_mod.CorrBlock(f1, f2, radius=model.args.corr_radius)
            net, inp = torch.split(model.cnet(i1), [model.hidden_dim, model.context_dim], dim=1)
            net, inp = torch.tanh(net), torch.relu(inp)
            coords0, coords1 = model.initialize_flow(i1)
            limit = forced if mode == "fixed" else P.MAX_ITERS
            score = None; went_on = None; n = 0
            for itr in range(limit):
                coords1 = coords1.detach()
                corr = corr_fn(coords1)
                net, up_mask, delta = model.update_block(net, inp, corr, coords1 - coords0)
                coords1 = coords1 + delta
                n = itr + 1
                if mode != "fixed" and n == P.BRANCH_AT:
                    if mode == "policy":
                        score = P.score_from_delta(delta)          # the .item() sync lives here
                        went_on = P.should_continue(score)
                    else:
                        went_on = forced
                    if not went_on:
                        break
            return model.upsample_flow(coords1 - coords0, up_mask), n, went_on, score

    # ---- decisions, errors, and exactness (untimed) -------------------------------------------
    print("executing the frozen rule, per case")
    rec: dict[str, dict] = {}
    exact_ok = True
    with torch.no_grad():
        for cid, p1, p2, padder, gt, val in prepared:
            out_p, n_p, on, sc = run(p1, p2, "policy")
            out_b, n_b, _, _ = run(p1, p2, "fixed", forced=P.MAX_ITERS)
            out_r, _, _, _ = run(p1, p2, "replay", forced=on)
            stock = model(p1, p2, iters=(P.MAX_ITERS if on else P.BRANCH_AT), test_mode=True)[1]
            stock12 = model(p1, p2, iters=P.MAX_ITERS, test_mode=True)[1]
            ok = bool(torch.equal(out_p, stock) and torch.equal(out_r, out_p)
                      and torch.equal(out_b, stock12))
            exact_ok &= ok
            rec[cid] = {"score": sc, "continued": bool(on), "iterations": n_p,
                        "epe_policy": epe(out_p, padder, gt, val),
                        "epe_baseline": epe(out_b, padder, gt, val),
                        "bitwise_ok": ok}
    n_cont = sum(1 for r in rec.values() if r["continued"])
    mean_it = statistics.mean(r["iterations"] for r in rec.values())
    mp = statistics.mean(r["epe_policy"] for r in rec.values())
    mb = statistics.mean(r["epe_baseline"] for r in rec.values())
    print(f"    continued {n_cont}/{len(rec)}, mean iterations {mean_it:.2f} "
          f"({(P.MAX_ITERS - mean_it) / P.MAX_ITERS * 100:.1f}% fewer)")
    print(f"    mean EPE policy {mp:.4f} px, baseline {mb:.4f} px, "
          f"delta {mp - mb:+.4f}, slack {P.ALLOWANCE_PX - (mp - mb):+.4f}  "
          f"{'MEETS' if (mp - mb) <= P.ALLOWANCE_PX else 'MISSES'}")
    print(f"    bitwise: policy == stock at its own count, replay == policy, "
          f"baseline == stock 12, all cases: {exact_ok}\n")

    # ---- cross-check against the offline analysis ---------------------------------------------
    if a.archive and a.archive.exists():
        arch = json.loads(a.archive.read_text())
        agree = dis = absent = 0
        for cid, r in rec.items():
            t = arch.get("cases", {}).get(cid, {}).get(str(P.BRANCH_AT), {}).get("trajectory")
            if not t:
                absent += 1; continue
            pred = P.should_continue(t[P.BRANCH_AT - 1])
            agree += pred == r["continued"]; dis += pred != r["continued"]
        print(f"predicted vs executed decisions: {agree} agree, {dis} disagree, {absent} absent")
        if dis:
            print("    a disagreement means the archive's trajectory and this run's score differ; "
                  "investigate before trusting either\n")
        else:
            print("    the offline analysis and the live implementation decide identically\n")

    # ---- paired timing -------------------------------------------------------------------------
    def timed(fn):
        if on_cuda:
            torch.cuda.synchronize()
        t0 = time.perf_counter()
        fn()
        if on_cuda:
            torch.cuda.synchronize()
        return time.perf_counter() - t0

    with torch.no_grad():                                          # warm every path
        for cid, p1, p2, _, _, _ in prepared:
            run(p1, p2, "policy"); run(p1, p2, "fixed", forced=P.MAX_ITERS)
            run(p1, p2, "replay", forced=rec[cid]["continued"])

    vs_base = {cid: [] for cid in rec}
    vs_repl = {cid: [] for cid in rec}
    abs_pol = {cid: [] for cid in rec}
    abs_bas = {cid: [] for cid in rec}
    with torch.no_grad():
        for r in range(a.rounds):
            for cid, p1, p2, _, _, _ in prepared:
                on = rec[cid]["continued"]
                if r % 2 == 0:
                    tp = timed(lambda: run(p1, p2, "policy"))
                    tb = timed(lambda: run(p1, p2, "fixed", forced=P.MAX_ITERS))
                    tr = timed(lambda: run(p1, p2, "replay", forced=on))
                else:
                    tr = timed(lambda: run(p1, p2, "replay", forced=on))
                    tb = timed(lambda: run(p1, p2, "fixed", forced=P.MAX_ITERS))
                    tp = timed(lambda: run(p1, p2, "policy"))
                vs_base[cid].append(tb - tp); vs_repl[cid].append(tp - tr)
                abs_pol[cid].append(tp); abs_bas[cid].append(tb)

    for cid in rec:
        rec[cid]["latency_policy_s"] = statistics.median(abs_pol[cid])
        rec[cid]["latency_baseline_s"] = statistics.median(abs_bas[cid])
        rec[cid]["saving_vs_baseline_s"] = statistics.median(vs_base[cid])
        rec[cid]["controller_cost_s"] = statistics.median(vs_repl[cid])

    tot_p = sum(r["latency_policy_s"] for r in rec.values())
    tot_b = sum(r["latency_baseline_s"] for r in rec.values())
    allr = [d for v in vs_repl.values() for d in v]
    ctrl = statistics.median(allr)
    print(f"paired timing, {a.rounds} rounds, arm order alternated, "
          f"{'CUDA-synchronised' if on_cuda else 'CPU'}")
    print(f"    total over {len(rec)} cases: policy {tot_p:.3f} s, baseline {tot_b:.3f} s")
    print(f"    MEASURED SAVING {(tot_b - tot_p) / tot_b * 100:+.1f}%   "
          f"(mean per case {tot_p / len(rec) * 1000:.1f} ms vs {tot_b / len(rec) * 1000:.1f} ms)")
    print(f"    controller cost, policy minus replay, paired: median {ctrl * 1000:+.2f} ms "
          f"({sum(1 for d in allr if d > 0)}/{len(allr)} positive, "
          f"{ctrl / (tot_b / len(rec)) * 100:+.2f}% of a baseline pass)")

    out = {"frozen_policy_sha256": frozen_hash,
           "policy": {"score": P.SCORE_NAME, "theta": P.THETA, "z": P.Z,
                      "branch_at": P.BRANCH_AT, "max_iters": P.MAX_ITERS,
                      "allowance_px": P.ALLOWANCE_PX, "frozen_on": P.FROZEN_ON},
           "case_list": str(a.cases), "checkpoint": str(a.checkpoint), "rounds": a.rounds,
           "summary": {"n": len(rec), "continued": n_cont, "mean_iterations": mean_it,
                       "mean_epe_policy": mp, "mean_epe_baseline": mb, "delta_epe": mp - mb,
                       "meets_allowance": (mp - mb) <= P.ALLOWANCE_PX,
                       "bitwise_ok": exact_ok,
                       "total_latency_policy_s": tot_p, "total_latency_baseline_s": tot_b,
                       "measured_saving_frac": (tot_b - tot_p) / tot_b,
                       "controller_cost_median_s": ctrl},
           "cases": rec, "environment": environment_info(device)}
    a.out.write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")
    print(f"\nwrote {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
