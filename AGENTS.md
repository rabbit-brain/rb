# Rabbit Brain: AGENTS.md

Rabbit Brain (`rb`) reviews a candidate checkpoint of an iterative perception model against the current one: it runs both on a case set (or reads results you already have), ranks the cases that regressed on error or never settled during refinement, explains each one, and keeps checks so the next checkpoint gets the same review. It runs locally; nothing leaves this machine.

Read this whole file before running anything. **Do not compute errors, regressions, rankings, stability or verdicts yourself.** `rb` defines them, applies the same definitions on every run, and records how. That is what makes a result comparable across checkpoints and people. If you find yourself writing an evaluation or comparison script, stop and use `rb run` (or `rb import` when the per-case numbers already exist).

## Install

```sh
pip install rabbit-brain            # or: uvx --from rabbit-brain rb
pip install "rabbit-brain[raft]"    # adds torch, numpy, opencv, scipy, pillow for the RAFT adapter
rb version
rb docs                             # prints this file
```

Python 3.10 or later. The core depends only on pydantic; the model's own requirements are loaded by the adapter, and `rb doctor` says what is missing.

## When to use it, and when not

Use it when there are two checkpoints (or two models for the same task), a set of cases with or without ground truth, and a release decision to make. Iterative models (RAFT-family optical flow, RAFT-Stereo-style depth) also get stability findings from their own refinement trajectory, which needs no labels.

Do not use it for training, hyperparameter search, certifying a model, or metrics where higher is better (convert those to an error first).

## Two ways in

**Run it** (`rb run`): the adapter executes both checkpoints on the case set, records the trajectory with a forward hook, computes the per-case error, and writes the run. Built-in adapters: `raft` (princeton-vl/RAFT and forks with the same `core/` layout; KITTI-style datasets) and `synthetic` (a test double that runs anywhere in seconds; its output says so). Any other model: a custom adapter (below).

**Import results** (`rb import`): per-case errors (and optional trajectories) your evaluator already produced, as version-1 JSON or a metrics CSV. Everything downstream is identical.

## The runner workflow

```sh
rb init --project <name> --adapter raft --model-code ./raft --dataset ./data/kitti2015/training [--device cpu]
rb doctor [--checkpoint <ckpt>]                        # environment, adapter, model code, dataset, checkpoints
rb verify-hook --checkpoint <ckpt>                     # one case: the recorder must fire once per iteration
rb run --baseline <ckpt-A> --candidate <ckpt-B> --json # the review → rb-runs/<run_id>/
rb findings <run_id> --top 5                           # the ranked queue, the summary, the verdict
rb case <run_id> <case_id>                             # one case: numbers, trajectory statistics, the reasoning
rb check save <run_id> <case_id>                       # keep this case for the next checkpoint
rb check run <run_id> --checks checks.json             # next time: exit 1 if a saved check fails or a case is flagged
rb report <run_id> --print                             # the receipt a human reads
```

`rb init --demo` writes a synthetic project (two demo checkpoints under `ckpt/`) so the whole workflow can be exercised without a model or a GPU; `rb example` creates a run from built-in example results the same way. `rb runs` lists runs, `rb check list` and `rb check rm <case_id>` manage saved checks, `rb schema <name>` prints a JSON Schema; `<run_id>` may also be a run directory, a `bundle.json`, or a version-1 results file read in place.

Flags on `rb run`: `--limit N` (first N cases; use it for a pilot), `--no-trajectories` (stability then reads "not assessed", never "settled"), `--device cuda|cpu`, `--seed 0`, `--baseline-name/--candidate-name` (default: checkpoint file stems), `--fail-on none|regressions|flags|checks` (default `none`: findings are data, not errors), `--quiet`, and the limits `--max-regression 0.3 --max-late-share 0.25 --max-reversals 2 --max-trajectory-regression 0.3 --max-last-update <off>` (defaults for a project made by `rb init`; `rb import` leaves the trajectory-regression limit off unless you pass it; the late-share and reversal limits are generic heuristics, and a scorer fitted to the model is a separate, paid step). `rb findings` takes `--filter flagged|all|regressions|unstable|improved-unstable|settled-regressions|improved|stable` (default `flagged`), `--sort priority|error-change|late-share|name`, `--top N`.

## rb.toml

```toml
[project]
name = "warehouse-perception"     # saved checks follow it
task = "flow"                     # flow | stereo | depth | generic (sets the default metric and unit)

[adapter]
id = "raft"                       # raft | synthetic, or: module = "package.module:Class"
model_code = "./raft"             # RAFT checkout; its core/ is put on sys.path
iterations = 12                   # refinement iterations per case = trajectory length
device = "cuda"
small = false                     # only for random-weight checks; real checkpoints are read as raft or raft-small from their keys
mixed_precision = false

[dataset]
name = "kitti2015-train"
kind = "kitti"                    # image_2/*_10.png + *_11.png; flow_occ/*_10.png optional (no flow_occ = unlabeled cases)
path = "./data/kitti2015/training"
cases = "all"                     # "all", a file with one case id per line, or a number (first N)

[limits]
max_regression = 0.3
max_late_share = 0.25
max_reversals = 2
max_trajectory_regression = 0.3   # candidate late movement above the current model's on the same case, trajectory unit; label-free
# max_last_update = 0.3           # off unless set: a final update larger than this (trajectory unit) = not settled
```

Case ids come from the data (KITTI: the frame stem, e.g. `000012_10`) and must stay the same across checkpoints. Unlabeled cases (no ground truth) get stability findings and "error not measured"; they never count as regressions or as passing on error.

## The trajectory (recorded for you, or the one line you add)

The `raft` adapter records the trajectory without touching RAFT's code: a forward hook on `model.update_block`, whose output is `(net, up_mask, delta_flow)`; each iteration records the mean |delta_flow| per pixel. For your own model, do one of:

```python
from rabbit_brain import TrajectoryRecorder
rec = TrajectoryRecorder()
with rec.attached(model.update_block, output_index=2):     # (a) no code change: hook the update module
    model(image1, image2, iters=12, test_mode=True)
# or
for k in range(iters):                                       # (b) the one line inside the loop
    delta = update_block(...); flow = flow + delta
    rec.step(delta)
trajectory = rec.values                                      # one number per iteration
```

`rb verify-hook` proves the wiring: it runs one case and fails with `E_HOOK_NOT_REACHABLE` (never fired) or `E_HOOK_LENGTH` (fired a different number of times than `iterations`). `rb run` checks every case the same way and records the result in `record.json` under `hook`.

## Custom adapters

`[adapter] module = "package.module:Class"`. The class takes the `Config` and implements: `describe() -> dict` (id, version, settings; goes into the receipt), `load(checkpoint: Path, device: str) -> model`, `cases() -> Iterable[Case]` (`Case(id, name, inputs, gt=None, tags=[], notes=None)` from `rabbit_brain.adapters`), `infer(model, case, rec) -> Prediction` (call `rec.step(delta)` once per iteration or use `rec.attached(...)`), `metric_value(pred, case) -> float | None` (None when `case.gt` is None), `expected_iterations() -> int | None`. Set `task`, `metric` (a `Metric(id, name, unit)`) and `synthetic = False` as class attributes; set `trajectory_scale` when the update fields are at a lower resolution than the metric's pixels (the raft adapter uses 8, so trajectory values are image pixels like the error). `rb doctor` reports a missing method by name. `src/rabbit_brain/adapters/raft.py` is the reference; `synthetic.py` is the smallest complete example.

## The results file for `rb import` (version 1)

```json
{
  "version": 1,
  "project": "My perception project",
  "baseline": "model-v1", "candidate": "model-v2",
  "dataset": "Regression set", "metric": "mean_endpoint_error", "unit": "px",
  "cases": [
    { "id": "seq-001", "name": "First sequence", "baseline_error": 2.1, "candidate_error": 2.4,
      "candidate_trajectory": [2.3, 1.4, 0.9, 0.6, 0.4, 0.3, 0.2, 0.15, 0.1, 0.08, 0.06, 0.05] }
  ]
}
```

1–500 cases; ids match `^[a-zA-Z0-9_.-]{1,80}$`; errors are the same lower-is-better metric per case for both models, computed against the same ground truth and valid mask; trajectories are optional (2–64 values, mean |update| per iteration); `baseline_frames`/`candidate_frames` are optional paired per-frame series. `rb schema example` prints this file; `rb schema comparison-v1` the JSON Schema. A CSV works too (`case_id, baseline_error, candidate_error`, optional `name, tags, baseline_trajectory, candidate_trajectory, baseline_frames, candidate_frames` as semicolon-separated numbers) with `--project --baseline-name --candidate-name --dataset --metric --unit` as flags.

## Output

Every command accepts `--json` and prints exactly one JSON object on stdout; progress, logs and warnings go to stderr.

```json
{"ok": true, "rb_version": "0.2.0", "command": "run", "run_id": "20260925-1412-raft-small",
 "data": {"run_dir": "rb-runs/20260925-1412-raft-small",
          "summary": {"cases": 200, "with_gt": 200, "with_trajectories": 200, "regressions": 3, "unstable": 4, "improved_unstable": 2, "settled_regressions": 1, "flagged": 5},
          "verdict": {"status": "investigate", "ready": false, "line": "Not ready: 3 error regressions, 2 unstable cases that pass on error. Start with 000012_10.", "start": "000012_10"},
          "hook": {"status": "recorded", "verified": true, "iterations": 12, "expected": 12}},
 "errors": [],
 "next": ["rb findings 20260925-1412-raft-small --top 5", "rb case 20260925-1412-raft-small 000012_10"]}
```

`next` lists the commands that usually follow. A run directory `rb-runs/<run_id>/` holds `bundle.json` (every case's numbers, trajectories, stability statistics and flags), `record.json` (the receipt: rb version, the exact command, both checkpoints with sha256, model-code git SHA, dataset hash, seeds, environment, hook status, skipped cases), `findings.json` (the ranked queue, the summary, the verdict under those limits) and `report.md` (the human receipt). Those four are small and committable; `checks.json` belongs in the repo next to the model code. Schemas: `rb schema bundle|record|findings|checks|envelope`.

Definitions `rb` applies, and restates in every report: a case is a **regression** when the candidate's error exceeds the current model's by more than `max_regression`; **late share** is the fraction of all refinement that happened in the last third of the iterations; a **reversal** is an iteration where the update grew by more than 5% over the previous one; a case is **unstable** when late share exceeds `max_late_share` or reversals exceed `max_reversals`, when it is a trajectory regression, or, only when `max_last_update` is set, when the **last update** (the size of the final refinement step, in the trajectory's unit) exceeds it. A **trajectory regression** is paired and label-free: the candidate's **late movement** (mean update over the last quarter of iterations, in the trajectory's unit) exceeds the current model's on the same case by more than `max_trajectory_regression`. It is the same test as the error regression, applied to the refinement instead of the answer, so it also works on cases without ground truth; on labeled cases it mostly coincides with error regressions and ranks the worst of them first (flag `trajectory_regression`). Cases are ranked regression+unstable, then regression, then **improved but unstable** (they pass on error and still need a look), then the rest, by error change; unlabeled cases rank after labeled ones within a group. A **saved check** is an absolute limit for a case id in a project: candidate error ≤ `max_error`, optionally late share ≤ `max_late_share` and reversals ≤ `max_reversals`; a saved case missing from a later run fails the check.

Every trajectory also yields the absolute convergence statistics (`stability.candidate` in bundle.json and `rb case`): `last_update`, `late_update` and `early_update` (mean update over the last and first quarter of iterations), `late_to_early`. A run with an instrumented model adds, from the update fields themselves: `sign_reversal_rate` (share of consecutive updates pointing in opposite directions, cosine below zero), `mean_cos`, `displacement_mean` / `displacement_max` / `displacement_initial` (distance of the intermediate estimates from the final one) and `update_energy`. A run's report ends with the median / p90 / max of these per model, so you can see where the limits sit against the case set. Do not derive verdicts from these numbers yourself; they are there to be read, compared across checkpoints and, with consent, shared for calibration.

## Exit codes

`0` ok · `1` a saved check failed or is missing, or a case is flagged (`rb check run`, default `--fail-on any`; `rb run` only with `--fail-on`) · `2` invalid input or config · `3` environment failure (`rb doctor` when something fails; adapter or device problems). Read `errors[].fix`.

## Errors and what to do

| code | meaning | do this |
|---|---|---|
| `E_CONFIG_MISSING` | no `rb.toml` here | `rb init …` in the project root, or `rb init --demo` |
| `E_CONFIG_INVALID` | `rb.toml` has a bad field, or an unknown adapter id | fix the named field; `rb init --force` rewrites the file |
| `E_ADAPTER_IMPORT` | the adapter or the model's dependencies did not import | `rb doctor`; install the model's requirements here (`pip install "rabbit-brain[raft]"` for RAFT) |
| `E_MODEL_CODE_MISSING` | `model_code` is not a RAFT checkout | point it at the repository (must contain `core/raft.py`) |
| `E_CHECKPOINT_NOT_FOUND` | a checkpoint is missing, is not a torch state dict, or does not match RAFT's layers | check the path and that it is a RAFT checkpoint (raft-small is detected from the file); do not download weights without asking the human |
| `E_DATASET_EMPTY` | no cases found | check `[dataset] path` and `kind`, or the cases file |
| `E_HOOK_NOT_REACHABLE` | the recorder never fired | custom adapter: call `rec.step(delta)` per iteration or use `rec.attached(...)`; or run with `--no-trajectories` and say so to the human |
| `E_HOOK_LENGTH` | fired a different number of times than `iterations` | the hook must fire once per refinement iteration; align `[adapter] iterations` |
| `E_DEVICE` | CUDA requested but not available | `--device cpu` (slow) or a GPU machine |
| `E_INFERENCE_FAILED` | every case failed inference | `rb verify-hook --checkpoint <path>` shows the first error |
| `E_IMPORT_INVALID` | the results file is not a valid comparison | fix every problem listed in `errors[0].problems`; start from `rb schema example` |
| `E_IMPORT_NOT_JSON` | the file is not JSON | check for a missing comma or bracket |
| `E_IMPORT_TOO_LARGE` | over 2 MB / 500 cases | split the case set or drop per-frame series |
| `E_IMPORT_CONFIG` | a CSV import is missing names | pass `--project --baseline-name --candidate-name --dataset --metric --unit` |
| `E_CSV_INVALID` | the CSV could not be converted | required columns `case_id, baseline_error, candidate_error`; series are semicolon-separated |
| `E_FILE_NOT_FOUND` | a path does not exist | paths are relative to the current directory |
| `E_RUN_NOT_FOUND` | no such run | `rb runs`; or pass a `bundle.json` / results file path |
| `E_CASE_NOT_FOUND` | no such case id in this run | `rb findings <run> --filter all` lists every id |
| `E_CHECKS_INVALID` | the checks file is not valid | `rb schema checks` |
| `E_CHECKS_PROJECT_MISMATCH` | the named checks file is for another project | same project name, or another `--checks` file (an unnamed `checks.json` for another project is ignored with a warning) |
| `E_LIMITS_INVALID` | a limit is out of range | `--max-regression ≥ 0`, `0 ≤ --max-late-share ≤ 1`, `0 ≤ --max-reversals ≤ 64`, `--max-trajectory-regression ≥ 0`, `--max-last-update ≥ 0` |
| `E_NOT_AVAILABLE` | the command is planned, not in this version | `rb rerun`, `rb open`, `rb mcp` are not here yet; use the commands above |
| `E_WRITE_FAILED` | a file could not be written | check permissions, or `--runs-dir` |
| `E_INTERNAL` | unexpected failure | re-run with `--json` and report it |

## Worked example

Prompt from the human: *"Review candidate checkpoint ckpt/raft-small.pth against ckpt/raft-things.pth on the KITTI cases with Rabbit Brain and tell me what to look at."*

```sh
rb doctor --checkpoint ckpt/raft-things.pth --checkpoint ckpt/raft-small.pth --json    # if there is no rb.toml: rb init first
rb verify-hook --checkpoint ckpt/raft-small.pth --json
rb run --baseline ckpt/raft-things.pth --candidate ckpt/raft-small.pth --json           # add --limit 20 for a pilot
rb findings <run_id> --top 5 --json
rb case <run_id> <top case id> --json
rb report <run_id>
```

Then report to the human, in this order: the verdict line; the top cases with their `why` text; the run id; the path to `report.md`; the command that reproduces the queue (`rb findings <run_id>` with the limits used). Quote numbers only from `findings.json`. If `hook.status` is not `recorded`, say that stability was not assessed and why.

When a later checkpoint arrives: `rb check run` against a new `rb run` of it (same project, same case set) answers "are the cases we cared about still fine?" and exits 1 in CI if not; then `rb findings` for the full review.

## Boundaries

`rb` does not train, does not modify model code (the recorder is a forward hook or one line you add, and `rb verify-hook` checks it), and does not certify a model. Stability limits are generic heuristics and a starting point; a scorer fitted to the model is a separate, paid step. Nothing leaves the machine. The synthetic adapter is a test double and every output of it says so.

## For humans: verify what your agent did

Open `rb-runs/<run_id>/report.md`. The header lists the run id, the `rb` version, the exact command, both checkpoints with their sha256, the environment and the hook status; the body restates the definitions with the limits in force and lists every case. `record.json` adds the model-code git SHA, the dataset hash, seeds and skipped cases. Re-run the command from the header, or `rb findings <run_id>` with the same limits, and compare. If what the agent told you differs from the report, the report is right.
