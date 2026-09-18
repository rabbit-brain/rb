# Changelog

## 0.2.0 (in development): the runner
- `rb init` (rb.toml; `--demo` for a synthetic project), `rb doctor`, `rb verify-hook`, `rb run --baseline A --candidate B`.
- Adapters: `raft` (princeton-vl/RAFT and forks; KITTI-style datasets; trajectory via a forward hook on `update_block`, no code change; per-case EPE over valid pixels; unlabeled cases supported) and `synthetic` (test double); custom adapters as `package.module:Class`.
- `record.json` for runs: checkpoint sha256s, model-code git SHA, dataset hash, seeds, environment (torch/cuda/gpu), hook status, skipped cases.
- Unlabeled cases: error "not measured", stability still assessed; never counted as regressions or as passing on error.
- `TrajectoryRecorder.attach()` / `attached()` forward-hook helpers.
- Trajectory regression, paired and label-free: a case is flagged (`trajectory_regression`, counts as unstable) when the candidate's late movement (mean update over the last quarter of iterations) exceeds the current model's on the same case by more than `max_trajectory_regression`. `rb init` sets it to `max_regression`; off for `rb import` unless passed. On the RAFT runs it ranks the worst error regressions first and flags nothing on candidates that settled better than the baseline; on unlabeled cases it is the regression test. `late_update_change` is in every case and queue item.
- The paper's convergence statistics, per case and label-free: from every trajectory `last_update`, `late_update`, `early_update`, `late_to_early` (quarter windows, absolute magnitudes); from a run's update fields `sign_reversal_rate` (cosine between consecutive updates below zero), `mean_cos`, `displacement_mean/max/initial` (distance of intermediate estimates from the final one) and `update_energy`. The recorder now averages the per-pixel L2 norm of the update (was L1) and takes a `scale`; the raft adapter sets 8, so RAFT trajectories are in image pixels (delta_flow is at 1/8 resolution). Late share and reversals are unchanged. New optional limit `max_last_update` (`--max-last-update`), off by default; run reports end with a per-model distribution table of the statistics.
- Receipts on real runs: the report says the run evaluated both checkpoints (not "imported"), names torch, CUDA, GPU and the model-code commit, and caps the table at every flagged case plus the top of the queue (40 rows minimum) instead of printing all 200 cases.
- The raft adapter reads each checkpoint's architecture (raft or raft-small) from its keys, so one run can compare raft-things with raft-small; the receipt lists the architecture per checkpoint. `examples/raft-kitti/runpod.sh` runs the official RAFT checkpoints on KITTI-2015 on a GPU pod.
- Not yet: evidence rendering (flow images, error maps, filmstrip), `rb rerun`, `rb open`, MCP.

## 0.1.1 (2026-09-19)
README and package metadata only; no code changes. Replaces 0.1.0.

## 0.1.0 (2026-09-19)
First release: the checker and importer as a package.
- `rb import` (version-1 JSON or metrics CSV) → a run directory with `bundle.json` (version 2), `record.json`, `findings.json`, `report.md`.
- `rb findings`, `rb case`, `rb report`, `rb check save|run|list|rm`, `rb example`, `rb runs`, `rb docs`, `rb schema`, `rb version`.
- `--json` envelope on every command; exit codes 0/1/2/3; error codes with fixes.
- `TrajectoryRecorder` (the one-line hook) as a Python API.
- The arithmetic: late share, reversals, regression threshold, ranking, verdict, report. Tested number for number against the reference implementation.
Not yet: `rb run` (the runner), adapters, evidence rendering, `rb open`. Those are 0.2.
