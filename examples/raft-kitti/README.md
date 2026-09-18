# RAFT on KITTI-2015: three real release reviews

The receipts in `rb-runs/` are real runs of `rb run` on the official princeton-vl/RAFT checkpoints, 200 KITTI-2015 training pairs each, 12 refinement iterations, on a rented RTX A5000 (about two minutes per run). `runpod.sh` reproduces them on any GPU box; `rb.toml`, `run.log` and `rerun.log` are the project file and the console output as they were.

| Run | Current | Candidate | Mean EPE | What the review said |
|---|---|---|---|---|
| `20260918-1852-raft-sintel` | raft-things | raft-sintel | 5.40 → 1.52 px | Not ready: 1 error regression (000145_10, 5.48 → 5.89 px), 199 cases improved or stable |
| `20260918-1854-raft-kitti` | raft-things | raft-kitti | 5.40 → 0.61 px | Not ready: 1 case improved on error but did not settle (000079_10) |
| `20260918-1856-raft-small` | raft-things | raft-small | 5.40 → 8.46 px | Not ready: 155 error regressions |

raft-things is the checkpoint trained on synthetic data only; raft-sintel and raft-kitti are fine-tuned on real data (raft-kitti on this very training set, so its numbers are in-sample); raft-small is the smaller architecture, read from the checkpoint by the adapter. The out-of-sample review, raft-things → raft-sintel, is the honest headline: a candidate that improves the mean by 72% still regresses on one case, and the receipt names it.

Read them with the tool:

```sh
rb findings rb-runs/20260918-1852-raft-sintel --top 5
rb case rb-runs/20260918-1852-raft-sintel 000145_10
rb findings rb-runs/20260918-1856-raft-small --max-trajectory-regression 0.3 --top 10   # the paired, label-free rule added after these runs
```

The last command rederives the raft-small review with the trajectory-regression limit that `rb init` now writes by default: the 39 cases where raft-small was still moving its answer at least 0.3 px per iteration more than raft-things go to the top of the queue, and they are the worst error regressions (+3 to +22 px). On the two candidates that settled better than raft-things the rule flags nothing.

Every `report.md` ends with the per-model distribution of the trajectory statistics on this case set, so you can see where a limit sits before you trust it. `analysis.txt` is the scratch analysis from the night of the runs (per-model quantiles and correlations of each statistic with the case's error).
