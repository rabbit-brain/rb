# RAFT on KITTI-2015: three real release reviews

The receipts in `rb-runs/` are real runs of `rb run` on the official princeton-vl/RAFT checkpoints, 200 KITTI-2015 training pairs each, 12 refinement iterations, on a rented RTX A5000 (about two minutes per run). `runpod.sh` reproduces them on any GPU box; `rb.toml`, `run.log` and `rerun.log` are the project file and the console output as they were.

| Run | Current | Candidate | Mean EPE | What the review said |
|---|---|---|---|---|
| `20260918-1852-raft-sintel` | raft-things | raft-sintel | 5.40 → 1.52 px | Not ready: 1 error regression (000145_10, 5.48 → 5.89 px), 199 cases improved or stable |
| `20260918-1854-raft-kitti` | raft-things | raft-kitti | 5.40 → 0.61 px | Not ready: 1 case improved on error but did not settle (000079_10) |
| `20260918-1856-raft-small` | raft-things | raft-small | 5.40 → 8.46 px | Not ready: 155 error regressions |
| `20260918-2333-raft-small` | raft-things | raft-small | 5.40 → 8.46 px | The same review rerun the next night with the trajectory-regression limit on (`--max-trajectory-regression 0.3`) and `--evidence standard`: Not ready: 155 error regressions, 39 unstable cases; 20 evidence sheets, all reproducing the run's numbers (`record.json` → `evidence.checks`) |

## Run on a second machine

All three reviews were run again on a different box (RTX 4090, torch 2.8 / CUDA 12.8, against the A5000 and torch 2.4 of the receipts above), from the same `runpod.sh`, same checkpoints, same 200 pairs.

What reproduced: the adapter agreed with RAFT's own `evaluate.py` path to the last digit on all four checkpoints, both times. The error side of every review came out the same case for case: 1 regression for raft-sintel (000145_10), 0 for raft-kitti, 155 for raft-small, the same top of the queue, mean EPE within 0.003 px.

What did not: per-case numbers are not bit-identical across hardware. 53 of the 200 cases differed by more than 0.01 px, 8 by more than 0.1 px, the largest 61.30 → 62.47 px on the hardest case in the set. That is enough to move a case across a limit, and six did. On raft-small, three cases became unstable and two settled (their late movement sits at 0.279 to 0.305 against a 0.3 px limit), so the count went 39 → 40; on raft-kitti, 000079_10 had 3 reversals on the A5000 and 2 on the 4090, so the one case that review named disappeared and the verdict went from "1 case improved but did not settle" to "nothing flagged".

This is what the borderline marker is for, and these runs are the evidence for it: all six cases that moved are marked borderline by the A5000 numbers alone, without knowing the 4090's.

`20260918-2333-raft-small/report.md` marks all five raft-small cases, and `20260918-1854-raft-kitti/report.md` marks 000079_10, on the line under the stability limits and in the table's stability column.

```sh
rb case rb-runs/20260918-1854-raft-kitti 000079_10        # "One reversal either way on the reversal limit changes this"
```

The four `report.md` files were re-rendered by 0.2.1 so the marks are readable here without installing anything, and each says so in its header; `bundle.json`, `record.json` and `findings.json` are untouched, so `rb report <run>` on them reproduces exactly the committed report. Read together: the review's verdict travels between machines, and the individual cases sitting on a threshold do not, which the tool now says on the case rather than leaving a colleague to discover it.

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

## The same reviews as research state (`.rb/`)

`.rb/` in this directory holds the question these runs were for, as Rabbit Brain 0.5 keeps it. There is one experiment, `sintel`: raft-things against raft-sintel, varying the checkpoint. Six of its settings were verified against `rb.toml` and the run's own `record.json`: iterations, the regression limit, the 200 cases, the RAFT commit, and both checkpoint hashes. The raft-sintel review is attached as evidence, and there are two claims, "lowers mean EPE by at least 3 px" and "regresses on no case".

An agent built it, after the runs, so `rb status` says both claims are **untested**. The review was attached before the claims were written, which makes it exploratory: it meets the first claim and misses the second (one regression, 000145_10), and neither counts. The criteria were written by an agent, so they wait on a person's freeze. What would establish them is a person running `rb freeze sintel` and a new run attached after it.

```sh
cd examples/raft-kitti
rb status              # what is established (nothing), what needs a person (the freeze), what an agent can do
rb compare sintel      # things 5.403 px vs sintel 1.524 px, -3.88, better
rb show no-regressions # the exploratory observation that misses, and why it does not count
```
