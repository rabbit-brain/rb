# raft-kitti: model comparison

Run: 20260918-1854-raft-kitti · rb 0.2.0.dev1 · finished 2026-09-18T18:54:47+00:00
Report rendered by rb 0.2.1 from this run's bundle.json and record.json; the numbers are the run's, the wording and any later annotations are this version's.
Command: `rb run --baseline raft/models/raft-things.pth --candidate raft/models/raft-kitti.pth --quiet`
Baseline checkpoint: raft/models/raft-things.pth (sha256 fcfa4125d6418f4d…)
Candidate checkpoint: raft/models/raft-kitti.pth (sha256 b9d170362415e1a2…)
Environment: python 3.11.10 · Linux-6.8.0-134-generic-x86_64-with-glibc2.35 · torch 2.4.1+cu124 · cuda 12.4 · NVIDIA RTX A5000
Model code: raft @ 2888e15a51fa
Trajectories: recorded. Recorded once per refinement iteration.

## Verdict

Not ready: 1 unstable case that passes on error. Start with 000079_10.

Evaluated by rb run: both checkpoints were run on the case set and the trajectories recorded by the adapter.

Current: raft-things
Candidate: raft-kitti
Dataset: kitti2015-train
Metric: mean endpoint error (px), lower is better.

Mean of case errors: 5.40 → 0.61 px. Cases are weighted equally; this is not a pooled per-pixel mean.
Regression threshold: increase greater than 0.3 px.
0 of 200 cases regress on error.
Stability limits: late revision ≤ 25%, reversals ≤ 2. 1 of 200 cases with trajectories are unstable; 1 of those pass on error.
Borderline: 6 cases (000079_10, 000082_10, 000016_10, 000165_10, 000135_10, 000050_10), 1 of them flagged, turn on a margin of less than a tenth of a limit (one reversal, for the reversal limit). The same checkpoints on another GPU or torch build give per-case values that differ by about that much, so a re-run elsewhere may sort these cases the other way; the environment line above says which machine this was.

| Case | Current (px) | Candidate (px) | Change (px) | Error | Candidate late revision | Stability |
|---|---:|---:|---:|---|---|---|
| 000079_10 | 4.68 | 0.78 | -3.90 | improved | 4% · 3 rev. | unstable (borderline) |
| 000138_10 | 0.28 | 0.10 | -0.18 | stable | 5% · 0 rev. | settled |
| 000139_10 | 0.25 | 0.06 | -0.19 | stable | 4% · 0 rev. | settled |
| 000092_10 | 0.25 | 0.03 | -0.22 | stable | 5% · 0 rev. | settled |
| 000156_10 | 0.28 | 0.02 | -0.25 | stable | 6% · 0 rev. | settled |
| 000082_10 | 0.36 | 0.09 | -0.27 | stable | 2% · 0 rev. | settled (borderline) |
| 000016_10 | 0.36 | 0.07 | -0.29 | stable | 4% · 0 rev. | settled (borderline) |
| 000165_10 | 0.42 | 0.12 | -0.29 | stable | 1% · 0 rev. | settled (borderline) |
| 000135_10 | 0.42 | 0.13 | -0.30 | stable | 1% · 0 rev. | settled (borderline) |
| 000097_10 | 0.46 | 0.13 | -0.33 | improved | 3% · 0 rev. | settled |
| 000140_10 | 0.46 | 0.12 | -0.33 | improved | 0% · 0 rev. | settled |
| 000011_10 | 0.44 | 0.10 | -0.34 | improved | 3% · 0 rev. | settled |
| 000050_10 | 0.39 | 0.05 | -0.34 | improved | 7% · 2 rev. | settled (borderline) |
| 000151_10 | 0.53 | 0.16 | -0.37 | improved | 1% · 0 rev. | settled |
| 000155_10 | 0.47 | 0.10 | -0.37 | improved | 3% · 0 rev. | settled |
| 000130_10 | 0.62 | 0.24 | -0.37 | improved | 1% · 0 rev. | settled |
| 000115_10 | 0.51 | 0.13 | -0.38 | improved | 3% · 0 rev. | settled |
| 000096_10 | 0.47 | 0.07 | -0.40 | improved | 3% · 0 rev. | settled |
| 000087_10 | 0.69 | 0.26 | -0.43 | improved | 1% · 0 rev. | settled |
| 000152_10 | 0.68 | 0.25 | -0.43 | improved | 1% · 0 rev. | settled |
| 000166_10 | 0.61 | 0.18 | -0.44 | improved | 1% · 0 rev. | settled |
| 000014_10 | 0.56 | 0.13 | -0.44 | improved | 3% · 0 rev. | settled |
| 000057_10 | 0.72 | 0.28 | -0.44 | improved | 1% · 0 rev. | settled |
| 000053_10 | 0.50 | 0.05 | -0.44 | improved | 3% · 0 rev. | settled |
| 000144_10 | 0.61 | 0.16 | -0.45 | improved | 3% · 0 rev. | settled |
| 000083_10 | 0.72 | 0.25 | -0.46 | improved | 1% · 0 rev. | settled |
| 000002_10 | 0.64 | 0.16 | -0.47 | improved | 1% · 0 rev. | settled |
| 000051_10 | 0.57 | 0.08 | -0.48 | improved | 3% · 0 rev. | settled |
| 000059_10 | 0.75 | 0.26 | -0.49 | improved | 1% · 0 rev. | settled |
| 000089_10 | 0.60 | 0.10 | -0.49 | improved | 3% · 0 rev. | settled |
| 000118_10 | 0.75 | 0.25 | -0.50 | improved | 1% · 0 rev. | settled |
| 000012_10 | 0.56 | 0.04 | -0.53 | improved | 4% · 0 rev. | settled |
| 000048_10 | 0.63 | 0.08 | -0.55 | improved | 3% · 0 rev. | settled |
| 000171_10 | 0.79 | 0.22 | -0.56 | improved | 1% · 0 rev. | settled |
| 000007_10 | 0.94 | 0.35 | -0.59 | improved | 2% · 0 rev. | settled |
| 000015_10 | 0.68 | 0.08 | -0.59 | improved | 3% · 0 rev. | settled |
| 000009_10 | 0.85 | 0.23 | -0.61 | improved | 2% · 0 rev. | settled |
| 000170_10 | 0.82 | 0.18 | -0.64 | improved | 1% · 0 rev. | settled |
| 000119_10 | 0.91 | 0.25 | -0.65 | improved | 2% · 0 rev. | settled |
| 000098_10 | 0.85 | 0.19 | -0.66 | improved | 1% · 0 rev. | settled |

160 more cases, none of them flagged: all cases are in bundle.json and `rb findings <run> --filter all`.

## Saved checks
No saved checks for this project.

The check runner evaluates these supplied metrics and trajectories against the limits. It does not run inference or certify a model for deployment.

## Convergence on this case set

Median / 90th percentile / max per model. Late share and reversals are what the stability limits read; last update, late-to-early ratio, direction reversals (share of consecutive updates pointing in opposite directions) and the mean distance of intermediate estimates from the final one are the absolute statistics from the update fields, in the trajectory's unit.

| Model | Cases | Late share | Reversals | Last update (px) | Late/early | Direction reversals | Distance from final (px) |
|---|---:|---|---|---|---|---|---|
| raft-things | 200 | 5% / 8% / 23% | 0 / 0 / 1 | 0.302 / 1.152 / 26.888 | 0.040 / 0.083 / 0.481 | 44% / 54% / 61% | 2.598 / 8.447 / 63.417 |
| raft-kitti | 200 | 1% / 3% / 7% | 0 / 0 / 3 | 0.074 / 0.183 / 0.662 | 0.009 / 0.022 / 0.061 | 42% / 47% / 53% | 0.958 / 2.139 / 4.333 |

Limits in force: late share ≤ 25%, reversals ≤ 2, trajectory regression off (`max_trajectory_regression` in rb.toml), last update not limited.

## Reproduce

- Ranked queue under the same limits: `rb findings 20260918-1854-raft-kitti --max-regression 0.3 --max-late-share 0.25 --max-reversals 2`
- One case with its evidence and reasoning: `rb case 20260918-1854-raft-kitti <case_id>`
- Saved checks against this run: `rb check run 20260918-1854-raft-kitti --checks checks.json`
- Definitions: `rb docs`. Schemas: `rb schema bundle|findings|checks|record`.
