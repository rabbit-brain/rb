"""Astra's rungs B and C, as far as the existing archive can carry them.

Development analysis on previously examined cases. Not a pre-registered test.

B = the latest update alone. C = B plus inexpensive history summaries. Both are fitted on the
configuration half and judged on the held-out half, and both are judged on the DECISIONS they
produce (which cases get the extra four iterations, and the mean EPE that results) rather than on
correlation with g.

Two limits this archive imposes, stated up front:
  - these are COARSE-channel update magnitudes, the shipped recorder's channel, not the
    full-resolution updates Astra's capture specifies;
  - magnitudes only, so no direction. Rung D (turning, signed area) cannot be built from this at all.
"""
import json
import sys
from pathlib import Path

import numpy as np

ALLOWANCE = 0.05


def load(p):
    d = json.load(open(p))
    return {cid: {int(k): v for k, v in per.items()} for cid, per in d["cases"].items()}


def feats(t):
    t = np.asarray(t, dtype=float)
    n = len(t)
    total = float(t.sum()) or 1e-12
    start = (n * 2) // 3
    rev = float(sum(1 for i in range(1, n) if t[i] > t[i - 1] * 1.05 + 1e-9))
    peak = int(np.argmax(t))
    return {
        "last": float(t[-1]),                      # rung B: the latest update, alone
        "total": total,                            # history summaries from here down
        "last_over_first": float(t[-1] / (t[0] or 1e-12)),
        "late_share": float(t[start:].sum() / total),
        "reversals": rev,
        "peak_frac": peak / (n - 1),
    }


B_COLS = ["last"]
C_COLS = ["last", "total", "last_over_first", "late_share", "reversals", "peak_frac"]


def design(cases, cols):
    ids = list(cases)
    X = np.array([[feats(cases[c][8]["trajectory"])[k] for k in cols] for c in ids])
    g = np.array([cases[c][8]["error"] - cases[c][12]["error"] for c in ids])
    return ids, X, g


def fit_predict(Xtr, ytr, Xte):
    mu, sd = Xtr.mean(0), Xtr.std(0)
    sd[sd == 0] = 1.0
    A = np.hstack([np.ones((len(Xtr), 1)), (Xtr - mu) / sd])
    B = np.hstack([np.ones((len(Xte), 1)), (Xte - mu) / sd])
    w, *_ = np.linalg.lstsq(A, ytr, rcond=None)
    return B @ w


def curve(cases, ids, order_idx):
    n = len(ids)
    e8 = np.mean([cases[c][8]["error"] for c in ids])
    gain = np.array([cases[ids[i]][8]["error"] - cases[ids[i]][12]["error"] for i in order_idx])
    return e8 - np.concatenate([[0.0], np.cumsum(gain)]) / n


def k_for(c, target):
    idx = np.where(c <= target)[0]
    return int(idx[0]) if len(idx) else None


def spearman(a, b):
    ra = np.argsort(np.argsort(a)).astype(float)
    rb = np.argsort(np.argsort(b)).astype(float)
    return float(np.corrcoef(ra, rb)[0, 1])


def main(base):
    cfg, hold = load(base / "iters.config.json"), load(base / "iters.holdout.json")
    n = len(hold)
    e12 = np.mean([c[12]["error"] for c in hold.values()])
    target = e12 + ALLOWANCE
    ids_h = list(hold)

    g_h = np.array([hold[c][8]["error"] - hold[c][12]["error"] for c in ids_h])
    oracle = curve(hold, ids_h, np.argsort(-g_h))
    Ko = k_for(oracle, target)

    print(f"held out, n = {n}, fixed 12 = {e12:.4f}, allowance <= {target:.4f} px")
    print(f"\n  {'rung':<34} {'target':<6} {'rho':>7} {'K':>5} {'mean iters':>11} {'saving':>8}")
    print(f"  {'oracle (perfect foreknowledge)':<34} {'-':<6} {'-':>7} {Ko:>5} {8 + 4 * Ko / n:>11.2f} "
          f"{12 - (8 + 4 * Ko / n):>8.2f}")

    rows = {}
    for label, cols in (("B  latest update alone", B_COLS), ("C  B + history summaries", C_COLS)):
        for tgt in ("rank", "raw"):
            ids_c, Xc, gc = design(cfg, cols)
            _, Xh, _ = design(hold, cols)
            ytr = np.argsort(np.argsort(gc)).astype(float) if tgt == "rank" else gc
            pred = fit_predict(Xc, ytr, Xh)
            cur = curve(hold, ids_h, np.argsort(-pred))
            K = k_for(cur, target)
            rho = spearman(pred, g_h)
            rows[(label, tgt)] = (K, cur, rho)
            ks = str(K) if K is not None else "never"
            mi = f"{8 + 4 * K / n:.2f}" if K is not None else "-"
            sv = f"{12 - (8 + 4 * K / n):.2f}" if K is not None else "-"
            print(f"  {label:<34} {tgt:<6} {rho:>7.3f} {ks:>5} {mi:>11} {sv:>8}")

    print(f"  {'uniform (fixed count, 8 to 11)':<34} {'-':<6} {'-':>7} {'never':>5} {'12.00':>11} {'0.00':>8}")

    print("\n  decisions, not correlations: mean EPE at matched mean cost")
    print(f"    {'K':>4} {'mean iters':>11} {'B (rank)':>10} {'C (rank)':>10} {'oracle':>9}")
    Bc, Cc = rows[("B  latest update alone", "rank")][1], rows[("C  B + history summaries", "rank")][1]
    for K in range(0, n + 1, 10):
        print(f"    {K:>4} {8 + 4 * K / n:>11.2f} {Bc[K]:>10.4f} {Cc[K]:>10.4f} {oracle[K]:>9.4f}")

    print("\n  overlap of the chosen sets with the oracle's, at the K each rung needs")
    for label in ("B  latest update alone", "C  B + history summaries"):
        K = rows[(label, "rank")][0]
        if K is None:
            continue
        ids_c, Xc, gc = design(cfg, B_COLS if label.startswith("B") else C_COLS)
        _, Xh, _ = design(hold, B_COLS if label.startswith("B") else C_COLS)
        ytr = np.argsort(np.argsort(gc)).astype(float)
        pred = fit_predict(Xc, ytr, Xh)
        chosen = set(np.argsort(-pred)[:K].tolist())
        best = set(np.argsort(-g_h)[:K].tolist())
        print(f"    {label:<34} K={K:>3}  {len(chosen & best)}/{K} of the oracle's top-{K}")


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else "."))
