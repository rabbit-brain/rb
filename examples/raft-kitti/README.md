# RAFT on KITTI-2015: three real release reviews

The receipts in `rb-runs/` are real runs of `rb run` on the official princeton-vl/RAFT checkpoints, 200 KITTI-2015 training pairs each, 12 refinement iterations, on a rented RTX A5000 (about two minutes per run). `runpod.sh` reproduces them on any GPU box; `rb.toml`, `run.log` and `rerun.log` are the project file and the console output as they were.

| Run | Current | Candidate | Mean EPE | What the review said |
|---|---|---|---|---|
| `20260918-1852-raft-sintel` | raft-things | raft-sintel | 5.40 → 1.52 px | Not ready: 1 error regression (000145_10, 5.48 → 5.89 px), 199 cases improved or stable |
| `20260918-1854-raft-kitti` | raft-things | raft-kitti | 5.40 → 0.61 px | Not ready: 1 case improved on error but did not settle (000079_10) |
| `20260918-1856-raft-small` | raft-things | raft-small | 5.40 → 8.46 px | Not ready: 155 error regressions |
| `20260918-2333-raft-small` | raft-things | raft-small | 5.40 → 8.46 px | The same review rerun the next night with the trajectory-regression limit on (`--max-trajectory-regression 0.3`) and `--evidence standard`: Not ready: 155 error regressions, 39 unstable cases; 20 evidence sheets, all reproducing the run's numbers (`record.json` → `evidence.checks`) |

raft-things is the checkpoint trained on synthetic data only; raft-sintel and raft-kitti are fine-tuned on real data (raft-kitti on this very training set, so its numbers are in-sample); raft-small is the smaller architecture, read from the checkpoint by the adapter. The out-of-sample review, raft-things → raft-sintel, is the honest headline: a candidate that improves the mean by 72% still regresses on one case, and the receipt names it.

Read them with the tool:

```sh
rb findings rb-runs/20260918-1852-raft-sintel --top 5
rb case rb-runs/20260918-1852-raft-sintel 000145_10
rb findings rb-runs/20260918-1856-raft-small --max-trajectory-regression 0.3 --top 10   # the paired, label-free rule added after these runs
```

The last command rederives the raft-small review with the trajectory-regression limit that `rb init` now writes by default: the 39 cases where raft-small was still moving its answer at least 0.3 px per iteration more than raft-things go to the top of the queue, and they are the worst error regressions (+3 to +22 px). On the two candidates that settled better than raft-things the rule flags nothing.

Every `report.md` ends with the per-model distribution of the trajectory statistics on this case set, so you can see where a limit sits before you trust it. `analysis.txt` is the scratch analysis from the night of the runs (per-model quantiles and correlations of each statistic with the case's error).

## The flagged cases, as pictures

`rb case <run> <id> --render` re-ran both checkpoints on the flagged case of each review and wrote the evidence sheets; `case-1400.jpg` in each `evidence/<case>/` directory is the 1400 px copy kept in the repository (the full-size PNGs are gitignored; the `20260918-2333-raft-small` run keeps one of its twenty sheets here). Every re-run reproduced the run's numbers exactly.

- `rb-runs/20260918-2333-raft-small/evidence/000095_10/case-1400.jpg`: the top of the raft-small queue (2.97 → 25.18 px, error regression and trajectory regression). The error-change tile is red on one object, the car nearest the camera; the disagreement tile is the same car. raft-small is still moving that car by 1.2 to 1.6 px per iteration in its last three tiles while raft-things has settled at 0.4, which is the paired rule firing (+0.96 px over the current model, limit +0.3).

- `rb-runs/20260918-1852-raft-sintel/evidence/000145_10/case-1400.jpg`: the +0.41 px regression is one object, the pole nearest the camera on the right. The error-change tile is red there and nowhere else of note, and the filmstrips show both checkpoints still moving that pole through all twelve iterations while the rest of the frame has settled. The candidate's late movement is lower than raft-things' (0.63 vs 1.18 px per iteration), which is why the receipt calls it a settled regression: it converged, to a worse answer.
- `rb-runs/20260918-1854-raft-kitti/evidence/000079_10/case-1400.jpg`: raft-things gets the cars wrong (yellow and red on every car in its error map) and raft-kitti fixes them (blue on the same cars in the error-change tile). The "unstable" flag is visible in the candidate's last three tiles: its update grows again in iterations 10 to 12 (0.47, 0.55, 0.66 px), at the left road edge and the far right, while raft-things keeps shrinking.

The scales on those sheets were set on these images: a single near pole had been dictating the flow, error and filmstrip scales, so flow colour now saturates at the 95th percentile with square-root saturation, heat maps run to the 99th percentile capped at four times the mean, and every tile label says its scale.
