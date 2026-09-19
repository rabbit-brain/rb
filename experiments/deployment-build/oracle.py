"""Oracle iteration-allocation analysis on the frozen truncation splits.

Development analysis on previously examined cases. Not a pre-registered test.

For each case, g_i = EPE_i(8) - EPE_i(12): the benefit of the four extra iterations. The oracle gives
those four extra iterations to the K cases with the largest g_i, so

    mean iterations = 8 + 4K/N
    mean EPE        = mean EPE(8) - (1/N) * sum over the top K of g_i

This is the ceiling: it assumes perfect foreknowledge of which cases will benefit. The comparison that
matters is not the oracle against fixed 8 and fixed 12, but the oracle against a UNIFORM iteration
count at the same mean cost, which is why the sweep measured 9, 10 and 11 as well.
"""
import json
import sys
from pathlib import Path

ALLOWANCE = 0.05  # px of mean EPE, against the fixed 12-iteration reference


def load(p):
    d = json.load(open(p))
    return {cid: {int(k): v for k, v in per.items()} for cid, per in d["cases"].items()}


def mean(xs):
    return sum(xs) / len(xs)


def uniform_curve(cases, counts):
    return {n: mean([c[n]["error"] for c in cases.values()]) for n in counts}


def uniform_at_cost(curve, cost):
    """Linear interpolation of the fixed-iteration curve at a fractional iteration count."""
    ns = sorted(curve)
    if cost <= ns[0]:
        return curve[ns[0]]
    if cost >= ns[-1]:
        return curve[ns[-1]]
    for a, b in zip(ns, ns[1:]):
        if a <= cost <= b:
            t = (cost - a) / (b - a)
            return curve[a] + t * (curve[b] - curve[a])
    return None


def report(name, path):
    cases = load(path)
    counts = sorted(next(iter(cases.values())).keys())
    n = len(cases)
    curve = uniform_curve(cases, counts)
    g = sorted(((c[8]["error"] - c[12]["error"], cid) for cid, c in cases.items()), reverse=True)
    gs = [x for x, _ in g]
    e8, e12 = curve[8], curve[12]

    print("\n" + "=" * 78)
    print(f"{name}   n = {n}")
    print("\n  fixed iteration counts")
    for m in counts:
        print(f"    {m:2d} iterations   mean EPE {curve[m]:.4f} px" +
              ("   (reference)" if m == 12 else f"   (+{curve[m] - e12:.4f} vs 12)"))

    pos = sum(1 for x in gs if x > 0)
    print(f"\n  cases where 12 beats 8: {pos}/{n}    total benefit {sum(gs):.4f} px    "
          f"mean {mean(gs):.4f} px")

    def oracle_epe(K):
        return e8 - sum(gs[:K]) / n

    def oracle_cost(K):
        return 8 + 4 * K / n

    print("\n  oracle curve (perfect foreknowledge)")
    print(f"    {'K':>4} {'mean iters':>11} {'oracle EPE':>11} {'uniform EPE':>12} {'oracle - uniform':>17}")
    for K in list(range(0, n + 1, max(1, n // 10))) + ([n] if n % max(1, n // 10) else []):
        if K > n:
            continue
        cost = oracle_cost(K)
        u = uniform_at_cost(curve, cost)
        print(f"    {K:>4} {cost:>11.2f} {oracle_epe(K):>11.4f} {u:>12.4f} {oracle_epe(K) - u:>17.4f}")

    Kstar = pos
    print(f"\n  oracle minimum at K = {Kstar} (every case with a positive benefit, and no others)")
    print(f"    mean iterations {oracle_cost(Kstar):.2f}, mean EPE {oracle_epe(Kstar):.4f} px, "
          f"which is {e12 - oracle_epe(Kstar):+.4f} px against fixed 12")

    target = e12 + ALLOWANCE
    Kmin = next((K for K in range(n + 1) if oracle_epe(K) <= target), None)
    print(f"\n  allowance: mean EPE within {ALLOWANCE} px of fixed 12 (<= {target:.4f} px)")
    if Kmin is None:
        print("    the oracle never gets there")
    else:
        cost = oracle_cost(Kmin)
        print(f"    oracle  : K = {Kmin}, mean iterations {cost:.2f}, "
              f"saving {12 - cost:.2f} iterations ({(12 - cost) / 12 * 100:.1f}%)")
    umin = next((m for m in counts if curve[m] <= target), None)
    if umin is None:
        print("    uniform : no fixed count in the sweep gets there")
    else:
        print(f"    uniform : {umin} iterations, mean EPE {curve[umin]:.4f} px, "
              f"saving {12 - umin} iterations ({(12 - umin) / 12 * 100:.1f}%)")
        if Kmin is not None:
            print(f"    the oracle's advantage over just using {umin} iterations everywhere: "
                  f"{umin - oracle_cost(Kmin):+.2f} iterations")

    print("\n  concentration of the benefit")
    tot = sum(x for x in gs if x > 0)
    for frac in (0.05, 0.10, 0.20, 0.50):
        k = max(1, int(round(frac * n)))
        print(f"    top {frac * 100:4.0f}% of cases ({k:3d}) hold {sum(gs[:k]) / tot * 100:5.1f}% "
              f"of the total positive benefit")
    print(f"    largest single benefit {gs[0]:.4f} px on case {g[0][1]}; "
          f"median {sorted(gs)[n // 2]:.4f}; most negative {gs[-1]:.4f} px on case {g[-1][1]}")


if __name__ == "__main__":
    base = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    report("CONFIGURATION HALF", base / "iters.config.json")
    report("HELD-OUT HALF", base / "iters.holdout.json")
