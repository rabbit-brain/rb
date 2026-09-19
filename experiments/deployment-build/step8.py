"""Can information available at iteration 8 capture the oracle's allocation room?

Development analysis on previously examined cases. Not a pre-registered test.

Every score below is computed from the 8-iteration run's own trajectory and nothing else: no ground
truth, no 12-iteration output. That is the information an adaptive scheme would actually have when it
decides whether to keep going. Scores are CHOSEN on the configuration half and REPORTED on the
held-out half, which does not make this a clean test (both halves have been examined) but does stop
the choice of score from being made on the numbers it is judged by.

Baselines, weakest to strongest:
  random   - allocate at random; expected mean EPE is linear from fixed 8 to fixed 12
  uniform  - just run a fixed intermediate count, interpolated from the measured 8..12 sweep
  oracle   - perfect foreknowledge of g_i
"""
import json
import sys
from pathlib import Path

ALLOWANCE = 0.05


def load(p):
    d = json.load(open(p))
    return {cid: {int(k): v for k, v in per.items()} for cid, per in d["cases"].items()}


def mean(xs):
    return sum(xs) / len(xs)


def spearman(x, y):
    def rank(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
                j += 1
            for k in range(i, j + 1):
                r[order[k]] = (i + j) / 2.0 + 1
            i = j + 1
        return r
    rx, ry = rank(x), rank(y)
    n = len(x)
    mx, my = mean(rx), mean(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    dx = sum((a - mx) ** 2 for a in rx) ** 0.5
    dy = sum((b - my) ** 2 for b in ry) ** 0.5
    return num / (dx * dy) if dx and dy else float("nan")


def features(t):
    """t is the 8-iteration run's trajectory: eight update magnitudes, nothing else."""
    n = len(t)
    total = sum(t) or 1e-12
    start = (n * 2) // 3                       # the product's own late-window rule
    quarter = max(1, n // 4)
    rev = sum(1 for i in range(1, n) if t[i] > t[i - 1] * 1.05 + 1e-9)
    return {
        "last_update": t[-1],
        "late_update": sum(t[-quarter:]) / quarter,
        "total_movement": total,
        "first_update": t[0],
        "late_share": sum(t[start:]) / total,
        "reversals": float(rev),
        "last_over_first": t[-1] / (t[0] or 1e-12),
        "last_x_total": t[-1] * total,
    }


def curves(cases, order, uniform):
    """Mean EPE after giving the extra four iterations to the first K cases in `order`."""
    n = len(cases)
    e8 = mean([c[8]["error"] for c in cases.values()])
    gain = [cases[cid][8]["error"] - cases[cid][12]["error"] for cid in order]
    out = []
    run = 0.0
    for K in range(n + 1):
        out.append(e8 - run / n)
        if K < n:
            run += gain[K]
    return out


def uniform_at(curve, cost):
    ns = sorted(curve)
    if cost <= ns[0]:
        return curve[ns[0]]
    if cost >= ns[-1]:
        return curve[ns[-1]]
    for a, b in zip(ns, ns[1:]):
        if a <= cost <= b:
            return curve[a] + (cost - a) / (b - a) * (curve[b] - curve[a])


def analyse(cases):
    n = len(cases)
    counts = sorted(next(iter(cases.values())).keys())
    ucurve = {m: mean([c[m]["error"] for c in cases.values()]) for m in counts}
    e8, e12 = ucurve[8], ucurve[12]
    g = {cid: c[8]["error"] - c[12]["error"] for cid, c in cases.items()}
    feats = {cid: features(c[8]["trajectory"]) for cid, c in cases.items()}
    names = sorted(next(iter(feats.values())).keys())
    ids = list(cases)
    return dict(n=n, ucurve=ucurve, e8=e8, e12=e12, g=g, feats=feats, names=names, ids=ids, cases=cases)


def k_for_allowance(curve, target):
    return next((K for K, v in enumerate(curve) if v <= target), None)


def report(tag, A, chosen=None):
    n, e8, e12 = A["n"], A["e8"], A["e12"]
    target = e12 + ALLOWANCE
    oracle_order = sorted(A["ids"], key=lambda c: -A["g"][c])
    oracle = curves(A["cases"], oracle_order, A["ucurve"])
    Ko = k_for_allowance(oracle, target)

    print("\n" + "=" * 78)
    print(f"{tag}   n = {n}   fixed 8 {e8:.4f}   fixed 12 {e12:.4f}   allowance <= {target:.4f} px")
    print(f"\n  {'score':<16} {'rho with g':>11} {'K for allowance':>16} {'mean iters':>11} {'saving':>9}")
    print(f"  {'oracle':<16} {'-':>11} {Ko:>16} {8 + 4 * Ko / n:>11.2f} {12 - (8 + 4 * Ko / n):>9.2f}")

    rows = []
    for name in A["names"]:
        vals = [A["feats"][c][name] for c in A["ids"]]
        gv = [A["g"][c] for c in A["ids"]]
        rho = spearman(vals, gv)
        order = sorted(A["ids"], key=lambda c: -A["feats"][c][name])
        cur = curves(A["cases"], order, A["ucurve"])
        K = k_for_allowance(cur, target)
        rows.append((name, rho, K, cur))
        ks = f"{K}" if K is not None else "never"
        mi = f"{8 + 4 * K / n:.2f}" if K is not None else "-"
        sv = f"{12 - (8 + 4 * K / n):.2f}" if K is not None else "-"
        mark = "  <= chosen" if chosen == name else ""
        print(f"  {name:<16} {rho:>11.3f} {ks:>16} {mi:>11} {sv:>9}{mark}")

    umin = next((m for m in sorted(A["ucurve"]) if A["ucurve"][m] <= target), None)
    print(f"  {'uniform (fixed)':<16} {'-':>11} {(str(umin) if umin else 'never'):>16} "
          f"{(f'{umin:.2f}' if umin else '-'):>11} {(f'{12 - umin:.2f}' if umin else '-'):>9}")

    if chosen:
        cur = dict((r[0], r[3]) for r in rows)[chosen]
        print(f"\n  {chosen} against the baselines, at matched mean cost")
        print(f"    {'K':>4} {'mean iters':>11} {'random':>9} {'uniform':>9} {chosen:>15} {'oracle':>9}")
        for K in range(0, n + 1, max(1, n // 10)):
            cost = 8 + 4 * K / n
            rnd = e8 - (K / n) * (e8 - e12)
            print(f"    {K:>4} {cost:>11.2f} {rnd:>9.4f} {uniform_at(A['ucurve'], cost):>9.4f} "
                  f"{cur[K]:>15.4f} {oracle[K]:>9.4f}")


if __name__ == "__main__":
    base = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    cfg = analyse(load(base / "iters.config.json"))
    print("CHOOSING THE SCORE ON THE CONFIGURATION HALF")
    report("CONFIGURATION HALF", cfg)
    # choose: best (smallest) K for the allowance on the configuration half, ties broken by rho
    best = None
    for name in cfg["names"]:
        order = sorted(cfg["ids"], key=lambda c: -cfg["feats"][c][name])
        K = k_for_allowance(curves(cfg["cases"], order, cfg["ucurve"]), cfg["e12"] + ALLOWANCE)
        rho = spearman([cfg["feats"][c][name] for c in cfg["ids"]], [cfg["g"][c] for c in cfg["ids"]])
        key = (K if K is not None else 10 ** 9, -rho)
        if best is None or key < best[0]:
            best = (key, name)
    chosen = best[1]
    print(f"\n\nCHOSEN ON THE CONFIGURATION HALF: {chosen}")
    hold = analyse(load(base / "iters.holdout.json"))
    report("HELD-OUT HALF", hold, chosen=chosen)
