# raft-kitti: model comparison

Run: 20260918-1856-raft-small · rb 0.2.0.dev1 · finished 2026-09-18T18:56:44+00:00
Report rendered by rb 0.2.2 from this run's bundle.json and record.json; the numbers are the run's, the wording and any later annotations are this version's.
Command: `rb run --baseline raft/models/raft-things.pth --candidate raft/models/raft-small.pth --quiet`
Baseline checkpoint: raft/models/raft-things.pth (sha256 fcfa4125d6418f4d…)
Candidate checkpoint: raft/models/raft-small.pth (sha256 c7d41b9cc88442bb…)
Environment: python 3.11.10 · Linux-6.8.0-134-generic-x86_64-with-glibc2.35 · torch 2.4.1+cu124 · cuda 12.4 · NVIDIA RTX A5000
Model code: raft @ 2888e15a51fa
Trajectories: recorded. Recorded once per refinement iteration.

## Verdict

Not ready: 155 error regressions. Start with 000103_10.

Evaluated by rb run: both checkpoints were run on the case set and the trajectories recorded by the adapter.

Current: raft-things
Candidate: raft-small
Dataset: kitti2015-train
Metric: mean endpoint error (px), lower is better.

Mean of case errors: 5.40 → 8.46 px. Cases are weighted equally; this is not a pooled per-pixel mean.
Regression threshold: increase greater than 0.3 px.
155 of 200 cases regress on error.
Stability limits: late revision ≤ 25%, reversals ≤ 2. 0 of 200 cases with trajectories are unstable; 0 of those pass on error.
Borderline: 6 cases (000167_10, 000083_10, 000144_10, 000008_10, 000118_10, 000049_10), 4 of them flagged, turn on a margin of less than a tenth of a limit (one reversal, for the reversal limit). The same checkpoints on another GPU or torch build give per-case values that differ by about that much, so a re-run elsewhere may sort these cases the other way; the environment line above says which machine this was.

| Case | Current (px) | Candidate (px) | Change (px) | Error | Candidate late revision | Stability |
|---|---:|---:|---:|---|---|---|
| 000103_10 | 17.45 | 41.86 | 24.41 | regression | 11% · 0 rev. | settled |
| 000095_10 | 2.97 | 25.18 | 22.21 | regression | 19% · 1 rev. | settled |
| 000190_10 | 15.03 | 33.78 | 18.74 | regression | 15% · 0 rev. | settled |
| 000090_10 | 7.31 | 21.63 | 14.32 | regression | 12% · 0 rev. | settled |
| 000148_10 | 7.82 | 21.87 | 14.05 | regression | 15% · 0 rev. | settled |
| 000181_10 | 13.90 | 25.60 | 11.69 | regression | 11% · 0 rev. | settled |
| 000062_10 | 10.42 | 22.03 | 11.61 | regression | 11% · 0 rev. | settled |
| 000185_10 | 7.06 | 18.19 | 11.12 | regression | 9% · 0 rev. | settled |
| 000102_10 | 19.94 | 30.95 | 11.02 | regression | 8% · 0 rev. | settled |
| 000179_10 | 11.61 | 22.36 | 10.75 | regression | 10% · 0 rev. | settled |
| 000192_10 | 14.42 | 24.97 | 10.55 | regression | 9% · 0 rev. | settled |
| 000081_10 | 6.68 | 16.92 | 10.24 | regression | 12% · 0 rev. | settled |
| 000111_10 | 6.16 | 16.05 | 9.90 | regression | 11% · 1 rev. | settled |
| 000146_10 | 6.63 | 16.25 | 9.62 | regression | 9% · 0 rev. | settled |
| 000180_10 | 15.67 | 25.29 | 9.61 | regression | 10% · 0 rev. | settled |
| 000147_10 | 12.04 | 21.43 | 9.40 | regression | 9% · 0 rev. | settled |
| 000184_10 | 8.35 | 17.46 | 9.11 | regression | 9% · 0 rev. | settled |
| 000073_10 | 9.76 | 18.80 | 9.04 | regression | 7% · 0 rev. | settled |
| 000188_10 | 34.53 | 43.54 | 9.00 | regression | 15% · 1 rev. | settled |
| 000194_10 | 11.97 | 20.44 | 8.48 | regression | 14% · 0 rev. | settled |
| 000110_10 | 3.25 | 11.44 | 8.19 | regression | 8% · 0 rev. | settled |
| 000091_10 | 6.91 | 15.07 | 8.17 | regression | 10% · 1 rev. | settled |
| 000080_10 | 5.32 | 13.41 | 8.10 | regression | 10% · 0 rev. | settled |
| 000101_10 | 11.57 | 18.92 | 7.35 | regression | 11% · 0 rev. | settled |
| 000039_10 | 4.18 | 11.15 | 6.97 | regression | 10% · 0 rev. | settled |
| 000191_10 | 18.67 | 25.54 | 6.87 | regression | 9% · 0 rev. | settled |
| 000174_10 | 12.90 | 19.65 | 6.75 | regression | 6% · 0 rev. | settled |
| 000183_10 | 11.04 | 17.71 | 6.67 | regression | 8% · 0 rev. | settled |
| 000078_10 | 6.82 | 13.47 | 6.66 | regression | 10% · 0 rev. | settled |
| 000072_10 | 19.76 | 26.28 | 6.51 | regression | 12% · 0 rev. | settled |
| 000195_10 | 22.96 | 29.42 | 6.46 | regression | 17% · 0 rev. | settled |
| 000196_10 | 16.63 | 23.05 | 6.42 | regression | 6% · 0 rev. | settled |
| 000070_10 | 11.91 | 18.30 | 6.40 | regression | 10% · 0 rev. | settled |
| 000068_10 | 10.17 | 16.27 | 6.10 | regression | 11% · 0 rev. | settled |
| 000107_10 | 4.76 | 10.82 | 6.07 | regression | 9% · 0 rev. | settled |
| 000182_10 | 14.92 | 20.99 | 6.07 | regression | 14% · 0 rev. | settled |
| 000074_10 | 10.57 | 16.52 | 5.95 | regression | 7% · 0 rev. | settled |
| 000040_10 | 4.47 | 10.31 | 5.84 | regression | 9% · 0 rev. | settled |
| 000129_10 | 6.10 | 11.93 | 5.83 | regression | 7% · 0 rev. | settled |
| 000100_10 | 16.69 | 22.45 | 5.76 | regression | 13% · 0 rev. | settled |
| 000108_10 | 4.60 | 10.20 | 5.60 | regression | 8% · 0 rev. | settled |
| 000063_10 | 10.11 | 15.68 | 5.57 | regression | 7% · 0 rev. | settled |
| 000041_10 | 5.71 | 11.27 | 5.56 | regression | 10% · 0 rev. | settled |
| 000109_10 | 3.88 | 9.41 | 5.53 | regression | 7% · 0 rev. | settled |
| 000197_10 | 4.60 | 10.08 | 5.49 | regression | 10% · 1 rev. | settled |
| 000064_10 | 7.50 | 12.78 | 5.28 | regression | 6% · 0 rev. | settled |
| 000198_10 | 4.00 | 9.11 | 5.10 | regression | 10% · 0 rev. | settled |
| 000189_10 | 14.34 | 19.41 | 5.07 | regression | 10% · 0 rev. | settled |
| 000178_10 | 3.12 | 7.79 | 4.67 | regression | 7% · 0 rev. | settled |
| 000079_10 | 4.68 | 9.30 | 4.62 | regression | 11% · 0 rev. | settled |
| 000106_10 | 3.79 | 8.40 | 4.61 | regression | 9% · 0 rev. | settled |
| 000175_10 | 11.55 | 15.91 | 4.36 | regression | 11% · 0 rev. | settled |
| 000042_10 | 7.94 | 12.15 | 4.21 | regression | 10% · 0 rev. | settled |
| 000066_10 | 8.73 | 12.94 | 4.21 | regression | 6% · 0 rev. | settled |
| 000033_10 | 6.81 | 11.01 | 4.19 | regression | 8% · 0 rev. | settled |
| 000186_10 | 3.77 | 7.88 | 4.11 | regression | 6% · 0 rev. | settled |
| 000036_10 | 2.58 | 6.66 | 4.08 | regression | 7% · 0 rev. | settled |
| 000099_10 | 13.43 | 17.46 | 4.03 | regression | 7% · 0 rev. | settled |
| 000176_10 | 31.11 | 34.96 | 3.84 | regression | 12% · 0 rev. | settled |
| 000032_10 | 7.75 | 11.32 | 3.57 | regression | 7% · 0 rev. | settled |
| 000038_10 | 3.80 | 7.37 | 3.57 | regression | 8% · 0 rev. | settled |
| 000045_10 | 6.26 | 9.83 | 3.57 | regression | 10% · 0 rev. | settled |
| 000069_10 | 8.66 | 12.21 | 3.55 | regression | 7% · 0 rev. | settled |
| 000029_10 | 10.17 | 13.70 | 3.53 | regression | 9% · 0 rev. | settled |
| 000055_10 | 5.88 | 9.36 | 3.48 | regression | 9% · 0 rev. | settled |
| 000077_10 | 3.44 | 6.89 | 3.45 | regression | 7% · 0 rev. | settled |
| 000193_10 | 20.64 | 23.98 | 3.33 | regression | 14% · 0 rev. | settled |
| 000187_10 | 6.37 | 9.67 | 3.30 | regression | 7% · 0 rev. | settled |
| 000061_10 | 15.95 | 19.22 | 3.27 | regression | 9% · 0 rev. | settled |
| 000167_10 | 0.89 | 4.04 | 3.15 | regression | 8% · 2 rev. | settled (borderline) |
| 000006_10 | 11.25 | 14.32 | 3.07 | regression | 5% · 0 rev. | settled |
| 000060_10 | 8.74 | 11.78 | 3.04 | regression | 7% · 0 rev. | settled |
| 000056_10 | 6.87 | 9.78 | 2.91 | regression | 6% · 0 rev. | settled |
| 000000_10 | 8.89 | 11.61 | 2.72 | regression | 11% · 0 rev. | settled |
| 000177_10 | 4.10 | 6.63 | 2.52 | regression | 6% · 0 rev. | settled |
| 000158_10 | 3.37 | 5.84 | 2.46 | regression | 5% · 0 rev. | settled |
| 000120_10 | 4.57 | 7.03 | 2.46 | regression | 7% · 0 rev. | settled |
| 000076_10 | 1.95 | 4.25 | 2.30 | regression | 7% · 0 rev. | settled |
| 000075_10 | 1.87 | 4.13 | 2.25 | regression | 8% · 0 rev. | settled |
| 000093_10 | 3.34 | 5.59 | 2.25 | regression | 7% · 0 rev. | settled |
| 000035_10 | 2.30 | 4.54 | 2.25 | regression | 5% · 0 rev. | settled |
| 000028_10 | 5.07 | 7.29 | 2.23 | regression | 5% · 0 rev. | settled |
| 000071_10 | 3.34 | 5.55 | 2.21 | regression | 8% · 0 rev. | settled |
| 000128_10 | 2.27 | 4.45 | 2.18 | regression | 4% · 0 rev. | settled |
| 000054_10 | 4.07 | 6.18 | 2.11 | regression | 5% · 0 rev. | settled |
| 000157_10 | 5.44 | 7.49 | 2.05 | regression | 5% · 0 rev. | settled |
| 000001_10 | 5.75 | 7.71 | 1.96 | regression | 6% · 0 rev. | settled |
| 000067_10 | 8.94 | 10.84 | 1.89 | regression | 6% · 0 rev. | settled |
| 000172_10 | 1.58 | 3.45 | 1.87 | regression | 4% · 0 rev. | settled |
| 000020_10 | 4.33 | 6.17 | 1.84 | regression | 6% · 0 rev. | settled |
| 000030_10 | 7.56 | 9.40 | 1.84 | regression | 12% · 0 rev. | settled |
| 000143_10 | 3.62 | 5.40 | 1.78 | regression | 6% · 0 rev. | settled |
| 000037_10 | 3.67 | 5.42 | 1.76 | regression | 6% · 0 rev. | settled |
| 000031_10 | 5.64 | 7.37 | 1.74 | regression | 9% · 0 rev. | settled |
| 000136_10 | 6.48 | 8.21 | 1.73 | regression | 5% · 0 rev. | settled |
| 000034_10 | 2.12 | 3.83 | 1.71 | regression | 6% · 0 rev. | settled |
| 000027_10 | 5.00 | 6.70 | 1.70 | regression | 5% · 0 rev. | settled |
| 000142_10 | 2.55 | 4.24 | 1.69 | regression | 4% · 0 rev. | settled |
| 000088_10 | 3.73 | 5.24 | 1.50 | regression | 6% · 0 rev. | settled |
| 000105_10 | 6.02 | 7.48 | 1.46 | regression | 10% · 0 rev. | settled |
| 000160_10 | 1.55 | 2.95 | 1.40 | regression | 4% · 0 rev. | settled |
| 000058_10 | 24.61 | 26.01 | 1.39 | regression | 8% · 0 rev. | settled |
| 000173_10 | 4.10 | 5.49 | 1.39 | regression | 5% · 0 rev. | settled |
| 000026_10 | 1.83 | 3.20 | 1.37 | regression | 4% · 0 rev. | settled |
| 000132_10 | 3.54 | 4.90 | 1.36 | regression | 5% · 0 rev. | settled |
| 000169_10 | 2.32 | 3.67 | 1.36 | regression | 6% · 0 rev. | settled |
| 000145_10 | 5.48 | 6.78 | 1.30 | regression | 14% · 0 rev. | settled |
| 000019_10 | 3.55 | 4.84 | 1.29 | regression | 5% · 0 rev. | settled |
| 000065_10 | 10.61 | 11.81 | 1.20 | regression | 7% · 0 rev. | settled |
| 000121_10 | 1.44 | 2.63 | 1.19 | regression | 4% · 0 rev. | settled |
| 000131_10 | 2.13 | 3.26 | 1.13 | regression | 5% · 0 rev. | settled |
| 000127_10 | 1.44 | 2.55 | 1.11 | regression | 3% · 0 rev. | settled |
| 000199_10 | 1.88 | 2.93 | 1.05 | regression | 4% · 0 rev. | settled |
| 000018_10 | 6.75 | 7.77 | 1.02 | regression | 6% · 0 rev. | settled |
| 000159_10 | 2.33 | 3.35 | 1.02 | regression | 4% · 0 rev. | settled |
| 000025_10 | 2.01 | 2.99 | 0.98 | regression | 3% · 0 rev. | settled |
| 000085_10 | 1.41 | 2.37 | 0.96 | regression | 8% · 0 rev. | settled |
| 000117_10 | 1.39 | 2.33 | 0.94 | regression | 4% · 0 rev. | settled |
| 000084_10 | 3.37 | 4.26 | 0.89 | regression | 6% · 0 rev. | settled |
| 000124_10 | 1.08 | 1.96 | 0.88 | regression | 4% · 0 rev. | settled |
| 000125_10 | 1.01 | 1.73 | 0.72 | regression | 3% · 0 rev. | settled |
| 000155_10 | 0.47 | 1.14 | 0.67 | regression | 11% · 0 rev. | settled |
| 000126_10 | 2.45 | 3.09 | 0.64 | regression | 5% · 0 rev. | settled |
| 000010_10 | 1.11 | 1.74 | 0.64 | regression | 3% · 0 rev. | settled |
| 000007_10 | 0.94 | 1.55 | 0.61 | regression | 5% · 0 rev. | settled |
| 000133_10 | 1.85 | 2.46 | 0.61 | regression | 2% · 0 rev. | settled |
| 000119_10 | 0.91 | 1.47 | 0.56 | regression | 4% · 0 rev. | settled |
| 000022_10 | 1.32 | 1.88 | 0.56 | regression | 3% · 0 rev. | settled |
| 000162_10 | 1.41 | 1.95 | 0.53 | regression | 3% · 0 rev. | settled |
| 000116_10 | 1.22 | 1.71 | 0.49 | regression | 4% · 0 rev. | settled |
| 000161_10 | 1.41 | 1.89 | 0.48 | regression | 3% · 0 rev. | settled |
| 000149_10 | 2.13 | 2.61 | 0.48 | regression | 5% · 0 rev. | settled |
| 000043_10 | 1.04 | 1.52 | 0.47 | regression | 10% · 0 rev. | settled |
| 000097_10 | 0.46 | 0.92 | 0.46 | regression | 9% · 0 rev. | settled |
| 000113_10 | 1.56 | 2.01 | 0.45 | regression | 8% · 0 rev. | settled |
| 000098_10 | 0.85 | 1.28 | 0.43 | regression | 2% · 0 rev. | settled |
| 000024_10 | 1.14 | 1.57 | 0.43 | regression | 5% · 0 rev. | settled |
| 000130_10 | 0.62 | 1.03 | 0.42 | regression | 2% · 0 rev. | settled |
| 000171_10 | 0.79 | 1.20 | 0.41 | regression | 2% · 0 rev. | settled |
| 000134_10 | 2.49 | 2.90 | 0.40 | regression | 5% · 0 rev. | settled |
| 000154_10 | 1.04 | 1.44 | 0.40 | regression | 3% · 0 rev. | settled |
| 000123_10 | 1.16 | 1.57 | 0.40 | regression | 3% · 0 rev. | settled |
| 000015_10 | 0.68 | 1.06 | 0.39 | regression | 6% · 0 rev. | settled |
| 000003_10 | 1.21 | 1.58 | 0.37 | regression | 4% · 0 rev. | settled |
| 000016_10 | 0.36 | 0.73 | 0.37 | regression | 7% · 0 rev. | settled |
| 000014_10 | 0.56 | 0.93 | 0.37 | regression | 4% · 0 rev. | settled |
| 000005_10 | 2.04 | 2.40 | 0.36 | regression | 4% · 0 rev. | settled |
| 000163_10 | 1.80 | 2.14 | 0.34 | regression | 4% · 0 rev. | settled |
| 000170_10 | 0.82 | 1.16 | 0.34 | regression | 3% · 0 rev. | settled |
| 000150_10 | 1.79 | 2.12 | 0.33 | regression | 5% · 0 rev. | settled |
| 000023_10 | 3.40 | 3.73 | 0.33 | regression | 3% · 0 rev. | settled |
| 000089_10 | 0.60 | 0.93 | 0.33 | regression | 9% · 0 rev. | settled |
| 000083_10 | 0.72 | 1.04 | 0.32 | regression | 3% · 0 rev. | settled (borderline) |
| 000144_10 | 0.61 | 0.93 | 0.32 | regression | 6% · 0 rev. | settled (borderline) |
| 000008_10 | 1.03 | 1.33 | 0.30 | regression | 12% · 2 rev. | settled (borderline) |

45 more cases, none of them flagged: all cases are in bundle.json and `rb findings <run> --filter all`.

## Saved checks
No saved checks for this project.

The check runner evaluates these supplied metrics and trajectories against the limits. It does not run inference or certify a model for deployment.

## Convergence on this case set

Median / 90th percentile / max per model. Late share and reversals are what the stability limits read; a reversal is an iteration whose update grew by more than 5% over the previous one, which is a size, not a direction. Last update, late-to-early ratio, direction reversals (the separate measure: share of consecutive updates pointing in opposite ways) and the mean distance of intermediate estimates from the final one are the absolute statistics from the update fields, in the trajectory's unit.

| Model | Cases | Late share | Reversals | Last update (px) | Late/early | Direction reversals | Distance from final (px) |
|---|---:|---|---|---|---|---|---|
| raft-things | 200 | 5% / 8% / 23% | 0 / 0 / 1 | 0.302 / 1.152 / 26.888 | 0.040 / 0.083 / 0.481 | 44% / 54% / 61% | 2.598 / 8.447 / 63.417 |
| raft-small | 200 | 6% / 11% / 20% | 0 / 0 / 2 | 0.464 / 1.496 / 11.836 | 0.065 / 0.136 / 0.388 | 41% / 63% / 72% | 4.120 / 9.533 / 31.622 |

Limits in force: late share ≤ 25%, reversals ≤ 2, trajectory regression off (`max_trajectory_regression` in rb.toml), last update not limited.

## Reproduce

- Ranked queue under the same limits: `rb findings 20260918-1856-raft-small --max-regression 0.3 --max-late-share 0.25 --max-reversals 2`
- One case with its evidence and reasoning: `rb case 20260918-1856-raft-small <case_id>`
- Saved checks against this run: `rb check run 20260918-1856-raft-small --checks checks.json`
- Definitions: `rb docs`. Schemas: `rb schema bundle|findings|checks|record`.
