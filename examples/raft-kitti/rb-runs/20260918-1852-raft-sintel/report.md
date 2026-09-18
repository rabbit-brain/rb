# raft-kitti: model comparison

Run: 20260918-1852-raft-sintel · rb 0.2.0.dev1 · finished 2026-09-18T18:52:58+00:00
Command: `rb run --baseline raft/models/raft-things.pth --candidate raft/models/raft-sintel.pth --quiet`
Baseline checkpoint: raft/models/raft-things.pth (sha256 fcfa4125d6418f4d…)
Candidate checkpoint: raft/models/raft-sintel.pth (sha256 90630d2e7d488a0d…)
Environment: python 3.11.10 · Linux-6.8.0-134-generic-x86_64-with-glibc2.35 · torch 2.4.1+cu124 · cuda 12.4 · NVIDIA RTX A5000
Model code: raft @ 2888e15a51fa
Trajectories: recorded. Recorded once per refinement iteration.

## Verdict

Not ready: 1 error regression. Start with 000145_10.

Evaluated by rb run: both checkpoints were run on the case set and the trajectories recorded by the adapter.

Current: raft-things
Candidate: raft-sintel
Dataset: kitti2015-train
Metric: mean endpoint error (px), lower is better.

Mean of case errors: 5.40 → 1.52 px. Cases are weighted equally; this is not a pooled per-pixel mean.
Regression threshold: increase greater than 0.3 px.
1 of 200 cases regress on error.
Stability limits: late revision ≤ 25%, reversals ≤ 2. 0 of 200 cases with trajectories are unstable; 0 of those pass on error.

| Case | Current (px) | Candidate (px) | Change (px) | Error | Candidate late revision | Stability |
|---|---:|---:|---:|---|---|---|
| 000145_10 | 5.48 | 5.89 | 0.41 | regression | 4% · 0 rev. | settled |
| 000138_10 | 0.28 | 0.20 | -0.08 | stable | 4% · 0 rev. | settled |
| 000097_10 | 0.46 | 0.36 | -0.10 | stable | 4% · 0 rev. | settled |
| 000155_10 | 0.47 | 0.37 | -0.10 | stable | 4% · 0 rev. | settled |
| 000051_10 | 0.57 | 0.45 | -0.12 | stable | 3% · 0 rev. | settled |
| 000139_10 | 0.25 | 0.13 | -0.12 | stable | 3% · 0 rev. | settled |
| 000016_10 | 0.36 | 0.23 | -0.13 | stable | 4% · 0 rev. | settled |
| 000082_10 | 0.36 | 0.23 | -0.14 | stable | 2% · 0 rev. | settled |
| 000165_10 | 0.42 | 0.27 | -0.14 | stable | 1% · 0 rev. | settled |
| 000140_10 | 0.46 | 0.31 | -0.15 | stable | 1% · 0 rev. | settled |
| 000092_10 | 0.25 | 0.09 | -0.16 | stable | 6% · 0 rev. | settled |
| 000115_10 | 0.51 | 0.34 | -0.17 | stable | 3% · 0 rev. | settled |
| 000135_10 | 0.42 | 0.24 | -0.18 | stable | 1% · 0 rev. | settled |
| 000011_10 | 0.44 | 0.26 | -0.19 | stable | 3% · 0 rev. | settled |
| 000012_10 | 0.56 | 0.37 | -0.19 | stable | 5% · 0 rev. | settled |
| 000156_10 | 0.28 | 0.08 | -0.19 | stable | 8% · 0 rev. | settled |
| 000059_10 | 0.75 | 0.56 | -0.20 | stable | 2% · 0 rev. | settled |
| 000089_10 | 0.60 | 0.39 | -0.20 | stable | 5% · 0 rev. | settled |
| 000130_10 | 0.62 | 0.40 | -0.22 | stable | 1% · 0 rev. | settled |
| 000050_10 | 0.39 | 0.17 | -0.22 | stable | 2% · 0 rev. | settled |
| 000168_10 | 0.99 | 0.77 | -0.22 | stable | 2% · 0 rev. | settled |
| 000096_10 | 0.47 | 0.24 | -0.23 | stable | 4% · 0 rev. | settled |
| 000048_10 | 0.63 | 0.40 | -0.23 | stable | 3% · 0 rev. | settled |
| 000151_10 | 0.53 | 0.29 | -0.23 | stable | 2% · 0 rev. | settled |
| 000118_10 | 0.75 | 0.51 | -0.24 | stable | 2% · 0 rev. | settled |
| 000053_10 | 0.50 | 0.25 | -0.24 | stable | 3% · 0 rev. | settled |
| 000014_10 | 0.56 | 0.31 | -0.25 | stable | 3% · 0 rev. | settled |
| 000166_10 | 0.61 | 0.36 | -0.26 | stable | 1% · 0 rev. | settled |
| 000152_10 | 0.68 | 0.42 | -0.26 | stable | 1% · 0 rev. | settled |
| 000057_10 | 0.72 | 0.46 | -0.26 | stable | 1% · 0 rev. | settled |
| 000024_10 | 1.14 | 0.87 | -0.26 | stable | 1% · 0 rev. | settled |
| 000144_10 | 0.61 | 0.34 | -0.27 | stable | 3% · 0 rev. | settled |
| 000087_10 | 0.69 | 0.41 | -0.28 | stable | 2% · 0 rev. | settled |
| 000002_10 | 0.64 | 0.35 | -0.29 | stable | 2% · 0 rev. | settled |
| 000171_10 | 0.79 | 0.50 | -0.29 | stable | 1% · 0 rev. | settled |
| 000083_10 | 0.72 | 0.42 | -0.30 | stable | 1% · 0 rev. | settled |
| 000119_10 | 0.91 | 0.61 | -0.30 | improved | 2% · 0 rev. | settled |
| 000007_10 | 0.94 | 0.61 | -0.33 | improved | 1% · 0 rev. | settled |
| 000098_10 | 0.85 | 0.51 | -0.34 | improved | 2% · 0 rev. | settled |
| 000015_10 | 0.68 | 0.31 | -0.37 | improved | 4% · 0 rev. | settled |

160 more cases, none of them flagged: all cases are in bundle.json and `rb findings <run> --filter all`.

## Saved checks
No saved checks for this project.

The check runner evaluates these supplied metrics and trajectories against the limits. It does not run inference or certify a model for deployment.

## Convergence on this case set

Median / 90th percentile / max per model. Late share and reversals are what the stability limits read; last update, late-to-early ratio, direction reversals (share of consecutive updates pointing in opposite directions) and the mean distance of intermediate estimates from the final one are the absolute statistics from the update fields, in the trajectory's unit.

| Model | Cases | Late share | Reversals | Last update (px) | Late/early | Direction reversals | Distance from final (px) |
|---|---:|---|---|---|---|---|---|
| raft-things | 200 | 5% / 8% / 23% | 0 / 0 / 1 | 0.302 / 1.152 / 26.888 | 0.040 / 0.083 / 0.481 | 44% / 54% / 61% | 2.598 / 8.447 / 63.417 |
| raft-sintel | 200 | 2% / 4% / 9% | 0 / 0 / 0 | 0.122 / 0.407 / 2.302 | 0.016 / 0.031 / 0.092 | 42% / 45% / 54% | 1.458 / 4.799 / 14.027 |

Limits in force: late share ≤ 25%, reversals ≤ 2, last update not limited (set `max_last_update` in rb.toml or `--max-last-update`).

## Reproduce

- Ranked queue under the same limits: `rb findings 20260918-1852-raft-sintel --max-regression 0.3 --max-late-share 0.25 --max-reversals 2`
- One case with its evidence and reasoning: `rb case 20260918-1852-raft-sintel <case_id>`
- Saved checks against this run: `rb check run 20260918-1852-raft-sintel --checks checks.json`
- Definitions: `rb docs`. Schemas: `rb schema bundle|findings|checks|record`.
