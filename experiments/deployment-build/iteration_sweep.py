"""Per-case error and trajectory at each iteration count, for the oracle-allocation analysis.

Development artifact on previously examined cases, not a pre-registered test. It exists because an
oracle curve over "8 iterations plus 4 more for K cases" is uninterpretable without the fixed
9, 10 and 11 points: selective allocation is only interesting if a uniform intermediate count does
not already buy the same thing.

One model load, one case list, `iterations` swapped between passes, so every number comes from the
product's own `evaluate_model` on the same objects.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from rabbit_brain.config import load_config
from rabbit_brain.runner import environment_info, evaluate_model, set_seeds


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", type=Path, required=True)
    ap.add_argument("--checkpoint", type=Path, required=True)
    ap.add_argument("--cases", type=Path, required=True)
    ap.add_argument("--iters", default="8,9,10,11,12")
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()

    ids = {line.strip() for line in a.cases.read_text().splitlines() if line.strip()}
    counts = [int(x) for x in a.iters.split(",")]

    cfg = load_config(a.config)
    cfg.adapter.mixed_precision = False
    from rabbit_brain.adapters import load_adapter
    adapter = load_adapter(cfg)
    device = cfg.adapter.device or "cpu"
    set_seeds(0)
    model = adapter.load(a.checkpoint, device)
    cases = [c for c in adapter.cases() if c.id in ids]
    missing = ids - {c.id for c in cases}
    if missing:
        raise SystemExit(f"{len(missing)} case id(s) not in the dataset, e.g. {sorted(missing)[:3]}")
    print(f"{len(cases)} cases on {device}, iteration counts {counts}", file=sys.stderr)

    out: dict = {"case_list": str(a.cases), "checkpoint": str(a.checkpoint),
                 "iteration_counts": counts, "seed": 0, "cases": {}}
    for n in counts:
        adapter.iterations = n
        set_seeds(0)
        t0 = time.time()
        res = evaluate_model(adapter, model, cases, record_trajectories=True, role=f"iters={n}")
        print(f"  iters={n}: {len(res)} cases in {time.time() - t0:.0f}s", file=sys.stderr)
        for cid, r in res.items():
            out["cases"].setdefault(cid, {})[str(n)] = {
                "error": r["error"], "trajectory": r["trajectory"], "fired": r.get("fired")}
    out["environment"] = environment_info(device)
    a.out.write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {a.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
