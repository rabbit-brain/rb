"""Compare two deployment builds of ONE checkpoint, and write a version-1 comparison for `rb import`.

`rb run` compares two checkpoints under one configuration. The deployment-build experiment
(`docs/deployment-experiment.md`) needs the opposite: one checkpoint under two configurations.
This driver supplies that, and does it by calling Rabbit Brain's own `evaluate_model`, the same
function `rb run` calls once per checkpoint, so the per-case errors and trajectories are produced
by the product's evaluation path and not by this script. Nothing here reimplements a metric.

    python3 build_compare.py --config rb.toml --checkpoint ckpt/raft-things.pth \
        --cases config-cases.txt \
        --reference iterations=12,mixed_precision=false \
        --build     iterations=12,mixed_precision=true \
        --out reference-vs-mixed.config.json --label mixed-precision

Then:  rb import reference-vs-mixed.config.json

The output pairs reference as `baseline` and the build under test as `candidate`, which is the
orientation the paired trajectory rule already expects: a trajectory regression is the candidate
moving more than the baseline did on the same case.
"""
from __future__ import annotations

import argparse
import json
import platform
import sys
import time
from pathlib import Path

from rabbit_brain import __version__
from rabbit_brain.config import load_config
from rabbit_brain.runner import environment_info, evaluate_model, hook_status, set_seeds


def overrides(text: str) -> dict:
    """`iterations=12,mixed_precision=false` -> {"iterations": 12, "mixed_precision": False}."""
    out: dict = {}
    for part in [p for p in text.split(",") if p.strip()]:
        key, _, raw = part.partition("=")
        key, raw = key.strip(), raw.strip()
        if raw.lower() in ("true", "false"):
            out[key] = raw.lower() == "true"
        elif raw.lstrip("-").isdigit():
            out[key] = int(raw)
        else:
            out[key] = raw
    return out


def build_adapter(cfg_path: Path, spec: dict, device: str | None):
    """A configured adapter for one build. Overrides land on [adapter] before the adapter is made."""
    cfg = load_config(cfg_path)
    for key, value in spec.items():
        if not hasattr(cfg.adapter, key):
            raise SystemExit(f"[adapter] has no field {key!r}; valid overrides are its own keys")
        setattr(cfg.adapter, key, value)
    if device:
        cfg.adapter.device = device
    from rabbit_brain.adapters import load_adapter  # local: importing torch is the slow part
    return cfg, load_adapter(cfg)


def _as_array(x):
    """A prediction's output as a plain float32 numpy array, batch dimension dropped."""
    import numpy as np
    if hasattr(x, "detach"):
        x = x.detach().float().cpu().numpy()
    a = np.asarray(x, dtype="float32")
    return a[0] if a.ndim == 4 and a.shape[0] == 1 else a


def evaluate_build(cfg_path: Path, spec: dict, checkpoint: Path, case_ids: list[str] | None,
                   device: str | None, role: str, seed: int, capture: dict | None = None) -> tuple[dict, dict, dict]:
    set_seeds(seed)
    cfg, adapter = build_adapter(cfg_path, spec, device)
    cases = list(adapter.cases())
    if case_ids is not None:
        keep = set(case_ids)
        cases = [c for c in cases if c.id in keep]
        missing = keep - {c.id for c in cases}
        if missing:
            raise SystemExit(f"{len(missing)} case id(s) in the list are not in the dataset, e.g. {sorted(missing)[:3]}")
    print(f"  {role}: {spec}, {len(cases)} cases", file=sys.stderr)
    if capture is not None:            # B1 needs the two builds' OUTPUTS, which are label-free;
        _inner = adapter.infer         # the per-case error uses ground truth and is NOT B1.
        def _infer(m, case, rec, __inner=_inner, __cap=capture):
            pred = __inner(m, case, rec)
            __cap[case.id] = _as_array(pred.output)
            return pred
        adapter.infer = _infer
    t0 = time.time()
    device = cfg.adapter.device or "cpu"
    model = adapter.load(checkpoint, device)
    results = evaluate_model(adapter, model, cases, record_trajectories=True, role=role,
                             progress=lambda m: print(f"  {m}", file=sys.stderr))
    status = hook_status(results, adapter.expected_iterations(), True)
    meta = {
        "role": role, "overrides": spec, "device": device, "seconds": round(time.time() - t0, 1),
        "adapter": adapter.describe() if hasattr(adapter, "describe") else {"id": cfg.adapter.id},
        "hook": status, "cases_evaluated": len(cases),
        "min_update_magnitude": min(
            (min(r["trajectory"]) for r in results.values() if r.get("trajectory")), default=None),
    }
    return results, meta, {"metric": cfg.metric.model_dump() if cfg.metric else None, "dataset": cfg.dataset.name}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", type=Path, required=True, help="rb.toml describing the model, dataset and metric")
    ap.add_argument("--checkpoint", type=Path, required=True, help="the ONE source checkpoint both builds use")
    ap.add_argument("--cases", type=Path, default=None, help="file with one case id per line (the split)")
    ap.add_argument("--reference", required=True, help="reference build, e.g. iterations=12,mixed_precision=false")
    ap.add_argument("--build", required=True, help="build under test, e.g. iterations=12,mixed_precision=true")
    ap.add_argument("--label", default="build", help="name for the build under test in the comparison")
    ap.add_argument("--device", default=None)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()

    if not a.checkpoint.exists():
        raise SystemExit(f"no such checkpoint: {a.checkpoint}")
    case_ids = None
    if a.cases:
        case_ids = [line.strip() for line in a.cases.read_text().splitlines() if line.strip()]
        print(f"case list: {a.cases} ({len(case_ids)} ids)", file=sys.stderr)

    ref_spec, build_spec = overrides(a.reference), overrides(a.build)
    if ref_spec == build_spec:
        raise SystemExit("reference and build are the same configuration; that comparison is empty")

    ref_out: dict = {}
    bld_out: dict = {}
    ref, ref_meta, shared = evaluate_build(a.config, ref_spec, a.checkpoint, case_ids, a.device, "reference", a.seed, ref_out)
    bld, bld_meta, _ = evaluate_build(a.config, build_spec, a.checkpoint, case_ids, a.device, a.label, a.seed, bld_out)

    # B1: mean endpoint difference between the two builds' outputs. No ground truth, no valid mask:
    # this is what a plain output comparison gives you, and the baseline the trajectory rule must beat.
    import numpy as np
    b1: dict = {}
    for cid in set(ref_out) & set(bld_out):
        x, y = ref_out[cid], bld_out[cid]
        if x.shape != y.shape:
            continue
        d = x - y
        if d.ndim >= 3 and d.shape[0] == 2:          # (2, H, W) flow: L2 over the vector axis
            d = np.sqrt((d ** 2).sum(axis=0))
        b1[cid] = float(np.abs(d).mean())

    cases, skipped = [], []
    for cid in sorted(set(ref) | set(bld)):
        r, b = ref.get(cid), bld.get(cid)
        if not r or not b or r.get("error") is None or b.get("error") is None:
            skipped.append(cid)
            continue
        entry = {"id": cid, "name": cid,
                 "baseline_error": float(r["error"]), "candidate_error": float(b["error"])}
        if cid in b1:
            entry["tags"] = [f"b1={b1[cid]:.6g}"]      # carried through import; the analysis reads it back
        if r.get("trajectory"):
            entry["baseline_trajectory"] = [float(v) for v in r["trajectory"]]
        if b.get("trajectory"):
            entry["candidate_trajectory"] = [float(v) for v in b["trajectory"]]
        cases.append(entry)

    metric = shared["metric"] or {}
    doc = {
        "version": 1,
        "project": f"{a.checkpoint.stem}-builds",
        "baseline": f"reference ({a.reference})",
        "candidate": f"{a.label} ({a.build})",
        "dataset": shared["dataset"],
        "metric": metric.get("id", "mean_endpoint_error"),
        "unit": metric.get("unit", "px"),
        "cases": cases,
        "notes": {
            "produced_by": f"build_compare.py via rabbit_brain {__version__} evaluate_model",
            "same_checkpoint": str(a.checkpoint),
            "checkpoint_is_identical_on_both_sides": True,
            "reference_build": ref_meta, "test_build": bld_meta,
            "case_list": str(a.cases) if a.cases else "all",
            "seed": a.seed, "skipped": skipped,
            "environment": environment_info(a.device), "host": platform.platform(),
        },
    }
    a.out.write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8")
    print(f"\nwrote {a.out}: {len(cases)} cases, {len(skipped)} skipped", file=sys.stderr)
    print(f"  reference hook: {ref_meta['hook']['status']}, {ref_meta['hook']['iterations']} iterations", file=sys.stderr)
    print(f"  {a.label} hook: {bld_meta['hook']['status']}, {bld_meta['hook']['iterations']} iterations", file=sys.stderr)
    print(f"  smallest update magnitude seen: reference {ref_meta['min_update_magnitude']}, "
          f"{a.label} {bld_meta['min_update_magnitude']}", file=sys.stderr)
    print(f"\nnext: rb import {a.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
