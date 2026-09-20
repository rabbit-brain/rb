r"""Check that frozen_rule.py reproduces the analysis that chose its threshold.

This runs no model. It applies the frozen rule to the archived 8-iteration trajectories and rebuilds
the reported table from the archived per-count errors. If the numbers here do not match the write-up,
the frozen file and the analysis have drifted apart and nothing downstream can be trusted.

It imports the constants rather than restating them, which is the whole point: there is one
definition of the policy and everything reads it.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import frozen_rule as P

EXPECTED = {"holdout": {"continue": 42, "mean_iters": 9.68, "delta_epe": 0.0486}}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sweep", type=Path, nargs="+", required=True,
                    help="iteration_sweep outputs, e.g. sweep/iters.config.json sweep/iters.holdout.json")
    a = ap.parse_args()
    print(f"score {P.SCORE_NAME}, theta {P.THETA}, branch at {P.BRANCH_AT} of {P.MAX_ITERS}, "
          f"allowance {P.ALLOWANCE_PX} px\n")
    failures = 0
    for path in a.sweep:
        half = "holdout" if "holdout" in path.name else ("config" if "config" in path.name else path.stem)
        cases = json.loads(path.read_text())["cases"]
        its, epe_pol, epe_base, cont = [], [], [], 0
        for cid, rec in sorted(cases.items()):
            traj = rec[str(P.BRANCH_AT)]["trajectory"]
            if not traj or len(traj) < P.BRANCH_AT:
                raise SystemExit(f"{cid}: trajectory at {P.BRANCH_AT} iterations is missing or short")
            go = P.should_continue(traj[P.BRANCH_AT - 1])
            cont += go
            its.append(P.MAX_ITERS if go else P.BRANCH_AT)
            epe_pol.append(rec[str(P.MAX_ITERS)]["error"] if go else rec[str(P.BRANCH_AT)]["error"])
            epe_base.append(rec[str(P.MAX_ITERS)]["error"])
        mi = statistics.mean(its)
        mp, mb = statistics.mean(epe_pol), statistics.mean(epe_base)
        d = mp - mb
        meets = d <= P.ALLOWANCE_PX
        print(f"{half:8} n={len(its):3}  continue={cont:3}  mean iterations={mi:.2f}  "
              f"saving={(P.MAX_ITERS - mi) / P.MAX_ITERS * 100:.1f}%")
        print(f"         EPE {mp:.4f} vs fixed {P.MAX_ITERS} {mb:.4f}, delta {d:+.4f}, "
              f"slack {P.ALLOWANCE_PX - d:+.4f}  {'MEETS' if meets else 'MISSES'}")
        exp = EXPECTED.get(half)
        if exp:
            ok = (cont == exp["continue"] and abs(mi - exp["mean_iters"]) < 5e-3
                  and abs(d - exp["delta_epe"]) < 5e-5)
            print(f"         against the write-up: {'MATCHES' if ok else 'DIFFERS'} "
                  f"(expected continue={exp['continue']}, mean iterations={exp['mean_iters']}, "
                  f"delta={exp['delta_epe']:+.4f})")
            failures += not ok
        print()
    if failures:
        print(f"{failures} half/halves disagree with the write-up. The frozen file and the "
              f"analysis have drifted.")
        return 1
    print("The frozen policy reproduces the analysis it was derived from.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
