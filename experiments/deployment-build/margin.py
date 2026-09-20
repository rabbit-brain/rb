"""A conservative absolute cutoff, with the margin derived from the configuration half alone.

Astra's question: how much saving survives a threshold with accuracy headroom? The failed cutoff was
the SMALLEST continue-set meeting the allowance on the configuration half, which is calibrated to be
marginal by construction, so any sampling variation pushes it over.

The fix is not to pick a margin by looking at the holdout. The quantity the allowance constrains is

    D(theta) = mean over cases of [ g_i if the case is stopped, else 0 ]

the mean EPE increase caused by stopping. Its bootstrap standard error is exactly std(v)/sqrt(n) for
v_i = g_i * 1(stopped), so a one-sided guard is computable from the configuration half and nothing
else:  choose the smallest continue-set with  D_cfg + z * SE_cfg <= allowance.

Everything below the "FROZEN" line uses only the configuration half. The holdout is read once.
"""
import json, sys
from pathlib import Path
import numpy as np

ALLOW = 0.05


def load(p):
    d = json.load(open(p)); return {c: {int(k): v for k, v in per.items()} for c, per in d["cases"].items()}


def arrays(cases):
    ids = sorted(cases)
    u8 = np.array([cases[c][8]["trajectory"][-1] for c in ids])
    g = np.array([cases[c][8]["error"] - cases[c][12]["error"] for c in ids])
    e8 = np.array([cases[c][8]["error"] for c in ids])
    e12 = np.array([cases[c][12]["error"] for c in ids])
    return ids, u8, g, e8, e12


base = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
cfg_ids, U_c, G_c, E8c, E12c = arrays(load(base / "iters.config.json"))
hd_ids, U_h, G_h, E8h, E12h = arrays(load(base / "iters.holdout.json"))
n = len(cfg_ids)

# ---------------------------------------------------------------- FROZEN: configuration half only
order = np.argsort(-U_c)                      # candidate thresholds are the sorted scores
print("configuration half: smallest continue-set passing a one-sided guard\n")
print(f"  {'z':>5} {'K':>4} {'theta':>8} {'D_cfg':>8} {'SE':>7} {'D+zSE':>8} {'mean iters':>11} {'saving':>8}")
frozen = {}
for z in (0.0, 1.0, 1.645, 2.0, 2.5):
    pick = None
    for K in range(n + 1):
        theta = U_c[order[K - 1]] if K else np.inf
        v = np.where(U_c < theta, G_c, 0.0)          # harm contributed by the stopped cases
        D = v.mean(); SE = v.std() / np.sqrt(n)
        if D + z * SE <= ALLOW:
            pick = (K, theta, D, SE); break
    K, theta, D, SE = pick
    mi = 8 + 4 * K / n
    frozen[z] = theta
    print(f"  {z:>5.3f} {K:>4} {theta:>8.4f} {D:>8.4f} {SE:>7.4f} {D + z * SE:>8.4f} "
          f"{mi:>11.2f} {12 - mi:>7.2f} ({(12 - mi) / 12 * 100:.1f}%)")

# ---------------------------------------------------------------- holdout, read once per threshold
print("\nheld-out half: each frozen threshold applied unchanged\n")
target = E12h.mean() + ALLOW
print(f"  fixed 12 = {E12h.mean():.4f}, allowance <= {target:.4f}\n")
print(f"  {'z':>5} {'theta':>8} {'continues':>10} {'mean iters':>11} {'saving':>16} "
      f"{'mean EPE':>9} {'slack':>9}  verdict")
for z, theta in frozen.items():
    cont = U_h >= theta
    err = np.where(cont, E12h, E8h).mean()
    mi = 8 + 4 * cont.sum() / len(hd_ids)
    slack = target - err
    print(f"  {z:>5.3f} {theta:>8.4f} {cont.sum():>10} {mi:>11.2f} "
          f"{12 - mi:>8.2f} ({(12 - mi) / 12 * 100:>4.1f}%) {err:>9.4f} {slack:>+9.4f}  "
          f"{'MEETS' if slack >= 0 else 'MISSES'}")

print("\n  per-case harm at the z = 1.645 threshold")
theta = frozen[1.645]
stopped = U_h < theta
h = G_h[stopped]; pos = h[h > 0]
print(f"    stopped {stopped.sum()}, hurt {len(pos)}, mean {pos.mean():.3f}, "
      f"p90 {np.percentile(pos, 90):.3f}, max {pos.max():.3f}, >1px {(pos > 1).sum()}, "
      f"helped {(h < 0).sum()}")
