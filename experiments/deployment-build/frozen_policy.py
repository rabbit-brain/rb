"""A frozen stopping policy: threshold chosen on the configuration half, judged on the held-out half.

Astra's correction. Choosing the SCORE on one half and then reading its CUTOFF off the other half's
curve is not a calibrated policy; it is a ranking curve with the answer peeked at. Here the score and
the numeric threshold are both fixed on the configuration half and never touched again.

Also answers: does u8 alone match (u7+u8)/2, and does the result depend on case 000104_10.

Baselines include a RANDOMISED MIXTURE of adjacent fixed counts, which is what a fractional compute
budget actually buys for free. "Uniform saves nothing" was true only of integer counts.
"""
from __future__ import annotations
import json, sys
from pathlib import Path
import numpy as np

ALLOWANCE = 0.05
OUTLIER = "000104_10"


def load(p):
    d = json.load(open(p))
    return {c: {int(k): v for k, v in per.items()} for c, per in d["cases"].items()}


def scores(cases, kind):
    out = {}
    for cid, per in cases.items():
        t = per[8]["trajectory"]
        out[cid] = t[-1] if kind == "u8" else (t[-2] + t[-1]) / 2.0
    return out


def epe(cases, cid, n):
    return cases[cid][n]["error"]


def policy_result(cases, sc, theta, drop=None):
    ids = [c for c in cases if c != drop]
    n = len(ids)
    cont = [c for c in ids if sc[c] >= theta]
    mean_iters = (8 * (n - len(cont)) + 12 * len(cont)) / n
    err = np.mean([epe(cases, c, 12) if c in set(cont) else epe(cases, c, 8) for c in ids])
    e12 = np.mean([epe(cases, c, 12) for c in ids])
    harm = np.array([epe(cases, c, 8) - epe(cases, c, 12) for c in ids if c not in set(cont)])
    return dict(n=n, k=len(cont), mean_iters=mean_iters, err=float(err), e12=float(e12),
                slack=float(e12 + ALLOWANCE - err), harm=harm)


def freeze(cases, sc):
    """Smallest continue-set on THIS half that meets the allowance; the threshold is its score value."""
    ids = sorted(cases, key=lambda c: -sc[c])
    n = len(ids)
    e8 = np.mean([epe(cases, c, 8) for c in ids])
    e12 = np.mean([epe(cases, c, 12) for c in ids])
    gains = np.cumsum([epe(cases, c, 8) - epe(cases, c, 12) for c in ids])
    for K in range(n + 1):
        err = e8 - (gains[K - 1] if K else 0.0) / n
        if err <= e12 + ALLOWANCE:
            return (sc[ids[K - 1]] if K else float("inf")), K, float(err)
    return float("-inf"), n, float(e12)


def main(base):
    cfg, hold = load(base / "iters.config.json"), load(base / "iters.holdout.json")
    counts = sorted(next(iter(hold.values())).keys())
    ue = {m: float(np.mean([epe(hold, c, m) for c in hold])) for m in counts}
    e12 = ue[12]
    target = e12 + ALLOWANCE

    print(f"held out: n = {len(hold)}, fixed 12 = {e12:.4f} px, allowance <= {target:.4f} px\n")

    print("  free baseline: randomised mixture of adjacent fixed counts")
    for lo, hi in ((10, 11), (11, 12)):
        p = (target - ue[hi]) / (ue[lo] - ue[hi]) if ue[lo] != ue[hi] else 0.0
        p = max(0.0, min(1.0, p))
        if ue[lo] + 1e-12 >= target >= ue[hi] - 1e-12:
            mi = hi - p * (hi - lo)
            print(f"    {lo}/{hi} mixture, weight {p:.3f} on {lo}: mean iters {mi:.2f}, "
                  f"saving {12 - mi:.2f} ({(12 - mi) / 12 * 100:.1f}%)")
    print()

    for kind, label in (("u8", "u8 alone (the last update)"),
                        ("late", "(u7+u8)/2  (late_update)")):
        sc_c, sc_h = scores(cfg, kind), scores(hold, kind)
        theta, Kc, errc = freeze(cfg, sc_c)
        r = policy_result(hold, sc_h, theta)
        met = "MEETS" if r["slack"] >= 0 else "MISSES"
        print(f"  {label}")
        print(f"    threshold frozen on the configuration half: {theta:.4f}  "
              f"(continues {Kc}/100 there, achieving {errc:.4f} px)")
        print(f"    held out: continues {r['k']}/{r['n']}, mean iters {r['mean_iters']:.2f}, "
              f"saving {12 - r['mean_iters']:.2f} ({(12 - r['mean_iters']) / 12 * 100:.1f}%)")
        print(f"    held out mean EPE {r['err']:.4f} px, allowance slack {r['slack']:+.4f} px  "
              f"=> {met}")
        h = r["harm"]
        pos = h[h > 0]
        print(f"    per-case harm among the {len(h)} stopped: "
              f"{len(pos)} hurt, mean {pos.mean() if len(pos) else 0:.3f} px, "
              f"p90 {np.percentile(pos, 90) if len(pos) else 0:.3f}, max {pos.max() if len(pos) else 0:.3f}")
        print(f"      hurt by >0.5 px: {(pos > 0.5).sum()}   >1 px: {(pos > 1.0).sum()}   "
              f"cases the stop HELPED: {(h < 0).sum()}, best {h.min():.3f} px")
        d = policy_result(hold, sc_h, theta, drop=OUTLIER)
        stopped_outlier = sc_h[OUTLIER] < theta
        print(f"    without {OUTLIER} (which the policy {'stops' if stopped_outlier else 'continues'}): "
              f"mean EPE {d['err']:.4f}, slack {d['slack']:+.4f} px => "
              f"{'MEETS' if d['slack'] >= 0 else 'MISSES'}")
        print()


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else "."))
