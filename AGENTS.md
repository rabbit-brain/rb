# Rabbit Brain: AGENTS.md

Rabbit Brain (`rb`) reviews a candidate checkpoint of an iterative perception model against the current one: it runs both on a case set (or reads results you already have), ranks the cases that regressed on error or never settled during refinement, explains each one, and keeps checks so the next checkpoint gets the same review. It runs locally; nothing leaves this machine.

Read this whole file before running anything. **Do not compute errors, regressions, rankings, stability or verdicts yourself.** `rb` defines them, applies the same definitions on every run, and records how. That is what makes a result comparable across checkpoints and people. If you find yourself writing an evaluation or comparison script, stop and use `rb run` (or `rb import` when the per-case numbers already exist).

## Install

```sh
pip install rabbit-brain            # or: uvx --from rabbit-brain rb
pip install "rabbit-brain[raft]"    # adds torch, torchvision, numpy, opencv, scipy, pillow for the RAFT adapter
pip install "rabbit-brain[evidence]" # numpy and pillow only: evidence sheets for a custom adapter (your model's own requirements are yours)
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
rb onboard                                             # writes brief.json to fill in: task, architecture, checkpoints, data, labels
rb onboard --brief brief.json                          # → rb.toml, an adapter or a scaffold with named TODOs, INTEGRATION.md
rb init --project <name> --adapter raft --model-code ./raft --dataset ./data/kitti2015/training [--device cpu]
rb doctor [--checkpoint <ckpt>]                        # environment, adapter, model code, dataset, checkpoints
rb verify-hook --checkpoint <ckpt>                     # one case: the recorder must fire once per iteration
rb verify-adapter --checkpoint <ckpt>                  # a few cases: the adapter must reproduce the model repository's own evaluation
rb run --baseline <ckpt-A> --candidate <ckpt-B> --json # the review → rb-runs/<run_id>/ (runs the adapter check first)
rb findings <run_id> --top 5                           # the ranked queue, the summary, the verdict
rb case <run_id> <case_id> [--render]                  # one case: numbers, trajectory statistics, the reasoning; --render writes its evidence PNGs
rb check save <run_id> <case_id>                       # keep this case for the next checkpoint
rb check run <run_id> --checks checks.json             # next time: exit 1 if a saved check fails or a case is flagged
rb report <run_id> --print                             # the receipt a human reads
rb report <run_id> --open                              # the same review as report.html, for a human to read in a browser; --embed to make it a single attachable file
rb share <run_id>                                      # anonymised statistics of the run as share.json, for the human to send if they choose; nothing is sent
```

`rb init --demo` writes a synthetic project (two demo checkpoints under `ckpt/`) so the whole workflow can be exercised without a model or a GPU; `rb example` creates a run from built-in example results the same way. `rb runs` lists runs, `rb check list` and `rb check rm <case_id>` manage saved checks, `rb schema <name>` prints a JSON Schema; `<run_id>` may also be a run directory, a `bundle.json`, or a version-1 results file read in place.

Flags on `rb run`: `--limit N` (first N cases; use it for a pilot), `--no-trajectories` (stability then reads "not assessed", never "settled"), `--skip-reference` (do not check the adapter against the reference evaluation; the receipt says so), `--device cuda|cpu`, `--seed 0`, `--baseline-name/--candidate-name` (default: checkpoint file stems), `--fail-on none|regressions|flags|checks` (default `none`: findings are data, not errors), `--quiet`, and the limits `--max-regression 0.3 --max-late-share 0.25 --max-reversals 2 --max-trajectory-regression 0.3 --max-last-update <off>` (defaults for a project made by `rb init`; `rb import` leaves the trajectory-regression limit off unless you pass it; the late-share and reversal limits are generic heuristics, and a scorer fitted to the model is a separate, paid step). `rb findings` takes `--filter flagged|all|regressions|unstable|improved-unstable|settled-regressions|improved|stable` (default `flagged`), `--sort priority|error-change|candidate-error|current-error|late-share|name` (`candidate-error` answers "where is the candidate worst in absolute terms", which the priority queue does not), `--top N`. Vocabulary: `stable` and `improved` are error outcomes (the change stayed within `max_regression`, or fell below it); `unstable` and `settled` are trajectory words. The findings table prints `late` (the candidate's share of refinement in the last third), `move` (its late movement minus the current model's, in the metric's unit per iteration) and `rev` (its reversals). Every command takes `--runs-dir` (repeated in the `next` hints when set) and `--verbose` (warnings from the model code are otherwise counted on stderr and hidden).

## Starting from a description of the setup: `rb onboard`

`rb init` assumes someone already knows which adapter fits and what to put in `rb.toml`. `rb onboard` is for the case before that: a team describes their setup once and gets a configured project back. `rb onboard` with no arguments writes `brief.json`, a filled example to replace with their own: project, task (flow | stereo | depth | generic), architecture, framework, where the model code and checkpoints are, how one case is stored, whether there is ground truth (`all` | `some` | `none`), refinement iterations, an optional evaluation script, an optional metric override, and one sentence on the decision the review has to support. `rb schema brief` prints the schema. Nothing is sent anywhere; this is a local file that generates local files.

`rb onboard --brief brief.json` then writes `rb.toml`, `INTEGRATION.md`, and one of two things. If the architecture is covered by a built-in adapter (RAFT-family flow today), that is all: the config points at it and there is nothing to write. Otherwise it writes `rb_adapter.py`, a scaffold that imports, that `rb doctor` can load, and whose unwritten parts each carry a TODO saying what that method must return, with the brief's own details (the case layout, the label situation, the metric and its unit, the evaluator's path) written into the docstrings that need them. It is deliberately not a working adapter: nothing can guess a team's loader, their valid mask or their metric formula, and a scaffold that quietly returned plausible numbers would be the exact failure adapter agreement exists to catch.

`INTEGRATION.md` carries the ladder that turns either into a trusted run, each step saying what it proves: `rb doctor`, `rb verify-hook`, `rb verify-adapter`, `rb run --limit 5`, then the real review. An agent handed the directory should read it and `rb docs` before touching anything.

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
alternate_corr = false            # RAFT's memory-saving correlation (needs its CUDA extension); false is the default
reference_cases = 5               # rb run first compares the adapter with the model repository's own evaluation on this many cases (0 = off)

[dataset]
name = "kitti2015-train"          # rb init takes it from --dataset-name, else the directory's last component; it names the dataset in every receipt
kind = "kitti"                    # image_2/*_10.png + *_11.png; flow_occ/*_10.png optional (no flow_occ = unlabeled cases)
path = "./data/kitti2015/training"
cases = "all"                     # "all", a file with one case id per line, or a number (first N)

[limits]
max_regression = 0.3
max_late_share = 0.25
max_reversals = 2
max_trajectory_regression = 0.3   # candidate late movement above the current model's on the same case, trajectory unit; label-free
# max_last_update = 0.3           # off unless set: a final update larger than this (trajectory unit) = not settled

[evidence]
level = "standard"                # none | standard (the top flagged cases) | full (every case): PNGs under rb-runs/<run>/evidence/
top = 10                          # how many flagged cases get evidence at level standard
```

Case ids come from the data (KITTI: the frame stem, e.g. `000012_10`) and must stay the same across checkpoints. Unlabeled cases (no ground truth) get stability findings and "error not measured"; they never count as regressions or as passing on error. `iterations` is the refinement count both checkpoints run with; keep it fixed across the runs you compare and across the checkpoints a `checks.json` follows (RAFT's own KITTI evaluation uses 24; the examples use 12 because the trajectory statistics are computed per iteration and 12 is what the checkpoints were tuned at). `rb init` also appends `rb-runs/*/evidence/` to `.gitignore` (evidence PNGs are large; the run's JSON files and report are meant to be committed) and says so.

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

## Adapter agreement: the adapter must reproduce the model's own evaluation

An adapter that loads, preprocesses or scores the model differently from the model repository's own evaluation code produces numbers that look right (shapes, ranges, plausible errors) and are not about the model. So an adapter states a **reference path**, the per-case error computed through the repository's own loader, forward call and metric formula (`reference_value(model, case)`, sharing no code with `infer` / `metric_value`), and `rb` compares the two: `rb verify-adapter --checkpoint <ckpt>` on a few cases, and `rb run` on `reference_cases` cases per checkpoint before it evaluates anything. Disagreement beyond 0.001 relative is `E_ADAPTER_DISAGREES` (exit 3) and the run writes nothing; the receipt of every run records the agreement (`record.json` → `adapter_agreement`, and the report's "Adapter agreement" line: agree on N cases with the largest difference, not established when the adapter has no reference path, skipped with `--skip-reference`). The built-in `raft` adapter's reference path is RAFT's `core/datasets.py` KITTI loader, `InputPadder`, `RAFT.forward(test_mode=True)` and the per-image EPE from `evaluate.py`. Agreement on five cases is evidence about those five cases with that checkpoint on that machine; the receipt keeps that scope. Never report findings from a run whose receipt says the adapter disagreed or was skipped without saying so to the human.

## Custom adapters

For a model that is not RAFT, write one file next to `rb.toml` (say `rb_adapter.py`) and point the config at it: `rb init --adapter rb_adapter:MyAdapter --model-code ./mymodel --dataset ./data/val --kind npz --iterations 8`. The project directory and `model_code` are on `sys.path` when `rb` imports the adapter, so no `PYTHONPATH` is needed; the class takes the `Config` and implements six methods. Everything it needs is importable from `rabbit_brain.adapters`: `Case`, `Prediction`, `Metric`, `RBError`, `read_case_selector`. A complete adapter for a model whose `forward(x, iters)` applies `model.update` once per iteration:

```python
from pathlib import Path
import numpy as np, torch
from rabbit_brain.adapters import Case, Metric, Prediction, RBError, read_case_selector
from mymodel.model import MyModel                      # your code, under [adapter] model_code

class MyAdapter:
    task = "flow"                                       # flow | stereo | depth | generic: sets the default metric and unit
    metric = Metric(id="mean_endpoint_error", name="mean endpoint error", unit="px")
    synthetic = False
    trajectory_scale = 1.0                              # multiply recorded updates by this to be in the metric's unit (RAFT: 8, its updates are at 1/8 resolution)

    def __init__(self, cfg):
        self.cfg = cfg
        self.iterations = cfg.adapter.iterations
        self.root = Path(cfg.dataset.path)
        self.architectures = {}                         # checkpoint path -> a pure architecture label; goes into the receipt, and rb warns when the two differ

    def describe(self):                                 # settings, into the receipt
        return {"id": "mymodel", "iterations": self.iterations, "hook": "forward hook on model.update", "architectures": dict(self.architectures)}

    def load(self, checkpoint: Path, device: str):
        if not checkpoint.exists():
            raise RBError("E_CHECKPOINT_NOT_FOUND", message=f"No such checkpoint: {checkpoint}")
        model = MyModel()
        model.load_state_dict(torch.load(checkpoint, map_location="cpu", weights_only=True))
        self.architectures[str(checkpoint)] = "MyModel(hidden=16)"   # architecture only, never a learned value
        return model.to(device).eval()

    def cases(self):                                    # one Case per sample; ids stable across checkpoints; gt=None when unlabeled
        ids, limit = read_case_selector(self.cfg)       # honours [dataset] cases = "all" | a file of ids | N
        for i, path in enumerate(sorted(self.root.glob("*.npz"))):
            if ids is not None and path.stem not in ids:
                continue
            if limit is not None and i >= limit:
                break
            yield Case(id=path.stem, name=path.stem, inputs=str(path), gt=str(path))

    def infer(self, model, case, rec):                  # rec records one value per refinement iteration
        d = np.load(case.inputs)
        x = torch.from_numpy(np.concatenate([d["frame1"], d["frame2"]], 0)).float()[None] / 255.0
        with torch.no_grad(), rec.attached(model.update):    # the hook records model.update's output; output_index=k if it returns a tuple
            out, _ = model(x.to(next(model.parameters()).device), iters=self.iterations)
        return Prediction(output=out[0].cpu())         # (C, H, W) or (H, W, C); evidence renders 2-channel fields, read_gt must match its shape

    def metric_value(self, pred, case):                 # the error, lower is better; None when case.gt is None
        if case.gt is None:
            return None
        gt = torch.from_numpy(np.load(case.gt)["field"]).float()
        return float(torch.sqrt(((pred.output - gt) ** 2).sum(0)).mean())

    def expected_iterations(self):
        return self.iterations

    # optional: adapter agreement (strongly recommended) and evidence
    def reference_description(self):
        return "mymodel/eval.py: its own sample() loader and epe() formula, iters=8"
    def reference_value(self, model, case):            # the same number through YOUR evaluator's code path, sharing nothing with infer/metric_value
        import importlib.util
        spec = importlib.util.spec_from_file_location("my_eval", Path(self.cfg.adapter.model_code) / "eval.py"); ev = importlib.util.module_from_spec(spec); spec.loader.exec_module(ev)
        x, gt = ev.sample(case.inputs)
        with torch.no_grad():
            out, _ = model(x, iters=8)
        return ev.epe(out[0], gt)
    def read_images(self, case):                        # list of HxWx3 uint8 arrays
        d = np.load(case.inputs); return [np.transpose(d["frame1"], (1, 2, 0)), np.transpose(d["frame2"], (1, 2, 0))]
    def read_gt(self, case):                            # (field, valid) in the prediction's layout; valid HxW bool
        d = np.load(case.gt); return d["field"], np.ones(d["field"].shape[1:], bool)
```

What the recorder stores: for each call of the hooked module (or each `rec.step(delta)`), the mean over pixels of the L2 norm of the update vector, times `trajectory_scale`; the fields themselves are kept for the run's convergence statistics and the filmstrips. Late share uses the last third of the iterations (the last `n - 2n//3`; 3 of 8), late movement the last quarter (`n//4`, at least 1; 2 of 8). The reference path's iteration count is your evaluator's, so a run with a different `[adapter] iterations` will disagree with it by design; either keep them equal or run with `--skip-reference` and say so. `rb doctor` reports a missing method by name; `rb verify-hook` proves the hook fires `iterations` times; `rb verify-adapter` proves the numbers. Errors an adapter raises with `RBError(code, message=...)` reach the human with the table's fix text. `rb init` for a custom adapter writes only the generic keys; the built-in `src/rabbit_brain/adapters/raft.py` is the full reference and `synthetic.py` the smallest one.

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

1–500 cases; ids match `^[a-zA-Z0-9_.-]{1,80}$`; errors are the same lower-is-better metric per case for both models, computed against the same ground truth and valid mask; trajectories are optional (2–64 values, mean |update| per iteration); `baseline_frames`/`candidate_frames` are optional paired per-frame series. `rb schema example` prints this file; `rb schema comparison-v1` the JSON Schema.

**CSV.** `rb schema csv` prints a sample. Columns `case_id, baseline_error, candidate_error` are required; `name, tags, baseline_trajectory, candidate_trajectory, baseline_frames, candidate_frames, notes` optional; series cells hold numbers separated by `;` (spaces work too). Your evaluator's own column names need no conversion script: map them with `--columns case_id=frame,baseline_error=epe_current,candidate_error=epe_candidate,candidate_trajectory=updates`. The names come from flags, and `--note` puts a sentence into the receipt (where the numbers came from, which file, what was converted). Worked example, an evaluator's CSV with two RAFT checkpoints on 200 KITTI pairs:

```sh
rb import kitti_eval.csv --project raft-kitti --baseline-name raft-things --candidate-name raft-sintel \
   --dataset "KITTI-2015 training, 200 pairs" --metric endpoint_error --unit px \
   --columns case_id=frame,baseline_error=epe_current,candidate_error=epe_candidate,baseline_trajectory=current_update_norms,candidate_trajectory=candidate_update_norms \
   --note "from our evaluator's kitti_eval.csv, 12 iterations" --json
rb findings <run_id> --top 5 --max-trajectory-regression 0.3   # the paired label-free test is off for imports until you pass it: do so when both trajectories are in the metric's unit
rb case <run_id> <case_id>
```

An imported run has numbers only, so `rb case --render` is not available for it (evidence needs the model and the data, which is `rb run`). A CSV error names the row and column: "row 14 (000012_10), column updates: ... is not a series of numbers (separate values with ';')".

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

`next` lists the commands that usually follow. A run directory `rb-runs/<run_id>/` holds `bundle.json` (every case's numbers, trajectories, stability statistics, flags and borderline limits), `record.json` (the receipt: rb version, the exact command, both checkpoints with sha256, model-code git SHA, dataset hash, seeds, environment, hook status, adapter agreement, skipped cases), `findings.json` (the ranked queue, the summary, the verdict under those limits) and `report.md` (the human receipt). Those four are small and committable; `checks.json` belongs in the repo next to the model code. The files and the `--json` output of the corresponding commands are the same documents (nulls written explicitly). `record.json` → `dataset.case_list_hash` is a hash of the case ids (the same set of cases); `dataset.content_hash` is a hash of the input and ground-truth files themselves (the same data), written by `rb run` when the adapter's cases name files. `rb runs` lists every run with its models and verdict. Schemas: `rb schema bundle|record|findings|checks|envelope`.

Definitions `rb` applies, and restates in every report: a case is a **regression** when the candidate's error exceeds the current model's by more than `max_regression`; **late share** is the fraction of all refinement that happened in the last third of the iterations; a **reversal** is an iteration where the update grew by more than 5% over the previous one, a size and not a direction (the direction measure is the separate `sign_reversal_rate`); a case is **unstable** when late share exceeds `max_late_share` or reversals exceed `max_reversals`, when it is a trajectory regression, or, only when `max_last_update` is set, when the **last update** (the size of the final refinement step, in the trajectory's unit) exceeds it. A **trajectory regression** is paired and label-free: the candidate's **late movement** (mean update over the last quarter of iterations, in the trajectory's unit) exceeds the current model's on the same case by more than `max_trajectory_regression`. It is the same test as the error regression, applied to the refinement instead of the answer, so it also works on cases without ground truth; on labeled cases it mostly coincides with error regressions and ranks the worst of them first (flag `trajectory_regression`). A case is **borderline** when moving one limit by a tenth (one reversal, for the reversal limit) would change what the case is called: its `borderline` field names those limits, the findings table and the report mark it, and the summary counts it. The margin it marks is about what the same checkpoints, data and code produce from one machine to another, so a borderline outcome is the one a re-run elsewhere may not reproduce; it changes no verdict, and a review is not "clean" because its flags are borderline. Cases are ranked regression+unstable, then regression, then **improved but unstable** (they pass on error and still need a look), then the rest, by error change; unlabeled cases rank after labeled ones within a group. A **saved check** is an absolute limit for a case id in a project: candidate error ≤ `max_error`, optionally late share ≤ `max_late_share` and reversals ≤ `max_reversals`; a saved case missing from a later run fails the check. `rb check save` defaults `max_error` to the better of the two models on that case plus `max_regression` (a case that improved keeps its improvement; a case that regressed must come back to the current model's level) and requires a settled trajectory unless `--no-settled`; a check saved from a run can therefore fail on that same run, which is the point: it fails until the case is fixed. `rb check run` prints the flagged cases and the check results (`--all` prints every case) and exits 1 when anything fails; its `--json` has `ok: true` with `data.failed: true`, because findings are data. Checks are meant for runs of the same project, case set, `iterations` and limits; a run made with different settings is a different question.

**Evidence.** `rb run` renders the top flagged cases (`[evidence] level = "standard"`, `top`; `--evidence none|standard|full` overrides) and `rb case <run> <id> --render` renders any case: it re-runs both checkpoints on that case and writes `rb-runs/<run>/evidence/<case>/` with `inputs.png` (the inputs, and where the two models disagree: |candidate - current| per pixel, which needs no ground truth), `flow.png` (current | candidate | ground truth on one colour scale), `error.png` (per-pixel error of each model over the dimmed input, and the change: red where the candidate is worse, blue where it is better; only when ground truth exists), `filmstrip.png` (one tile per refinement iteration, the update size per pixel, candidate then current, one colour scale, the last quarter framed), `trajectory.png` (both trajectories on a log axis, the last quarter shaded) and `case.png` (all of it stacked, 1920 px wide, with the finding as caption), plus a README that states the scales. Scales show structure, not extremes: flow colour saturates at the 95th percentile with square-root saturation; heat maps run to the 99th percentile capped at four times the mean, so a region above that saturates, and saturation is itself the finding (a near object the model keeps moving, say). Every tile label says its scale. The caption and the JSON `evidence.check` say whether the re-run reproduced the run's error and trajectory for that case. Evidence directories are gitignored (large); `bundle.json` and the report list which cases have them. Rendering needs numpy and pillow (`E_EVIDENCE_DEPS` otherwise); a run never fails because a rendering did. Custom adapters opt in with `read_images(case)` and `read_gt(case)`; without them only the disagreement map, the filmstrips and the trajectory plot are drawn. Show the human `case.png` for the case the verdict names; do not describe images you have not opened.

Every trajectory also yields the absolute convergence statistics (`stability.candidate` in bundle.json and `rb case`): `last_update`, `late_update` and `early_update` (mean update over the last and first quarter of iterations), `late_to_early`. A run with an instrumented model adds, from the update fields themselves: `sign_reversal_rate` (share of consecutive updates pointing in opposite directions, cosine below zero), `mean_cos`, `displacement_mean` / `displacement_max` / `displacement_initial` (distance of the intermediate estimates from the final one) and `update_energy`. A run's report ends with the median / p90 / max of these per model, so you can see where the limits sit against the case set. Do not derive verdicts from these numbers yourself; they are there to be read, compared across checkpoints and, with consent, shared for calibration.

## Exit codes

`0` ok · `1` a saved check failed or is missing, or a case is flagged (`rb check run`, default `--fail-on any`; `rb run` only with `--fail-on`) · `2` invalid input or config · `3` environment failure (`rb doctor` when something fails; adapter, hook, device or adapter-agreement problems). Read `errors[].fix`.

## Errors and what to do

| code | meaning | do this |
|---|---|---|
| `E_CONFIG_MISSING` | no `rb.toml` here | `rb init …` in the project root, or `rb init --demo` |
| `E_CONFIG_INVALID` | `rb.toml` has a bad field, or an unknown adapter id | fix the named field; `rb init --force` rewrites the file |
| `E_ADAPTER_IMPORT` | the adapter or the model's dependencies did not import | `rb doctor` names the module: a custom adapter file goes next to rb.toml or under `model_code` (both are on `sys.path`); a missing dependency is installed here (`pip install "rabbit-brain[raft]"` for RAFT) |
| `E_DOCTOR` | `rb doctor` found a failing check | read `data.checks`: every failed check has a detail and a fix |
| `E_MODEL_CODE_MISSING` | `model_code` is not a RAFT checkout | point it at the repository (must contain `core/raft.py`) |
| `E_CHECKPOINT_NOT_FOUND` | a checkpoint is missing, is not a torch state dict, or does not match RAFT's layers | check the path and that it is a RAFT checkpoint (raft-small is detected from the file); do not download weights without asking the human |
| `E_DATASET_EMPTY` | no cases found | check `[dataset] path` and `kind`, or the cases file |
| `E_ADAPTER_DISAGREES` | the adapter's per-case error differs from the model repository's own evaluation on the same cases | `rb verify-adapter --checkpoint <path>` shows both columns; fix the adapter's loading, preprocessing (input range, padding, colour order), forward call or metric formula; `rb run --skip-reference` runs anyway and the receipt says so |
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
| `E_EVIDENCE_DEPS` | evidence rendering needs numpy and pillow | `pip install numpy pillow` (included in `rabbit-brain[raft]`) |
| `E_INTERNAL` | unexpected failure | re-run with `--json` and report it |
| `E_WORKSPACE_NO_TOKEN` | no `RB_WORKSPACE_TOKEN` | the workspace is the paid feature; the tool needs no account. `rb plans` needs no token |
| `E_WORKSPACE_AUTH` | the workspace rejected the token | it may be revoked or for another deployment; create a new one |
| `E_WORKSPACE_PAYMENT_REQUIRED` | no active subscription on that workspace | `rb workspace checkout` returns a link for a person to approve |
| `E_WORKSPACE_NOT_SELLABLE` | that plan is published but unfinished | `rb plans` shows which plans can be bought today |
| `E_WORKSPACE_UNREACHABLE` | the workspace host could not be reached | exit 3. Nothing local was affected; the run is still on disk |
| `E_WORKSPACE_REJECTED` | the workspace refused the request | the message says why; push the `bundle.json` unmodified |

## Worked example

Prompt from the human: *"Review candidate checkpoint ckpt/raft-small.pth against ckpt/raft-things.pth on the KITTI cases with Rabbit Brain and tell me what to look at."*

```sh
rb doctor --checkpoint ckpt/raft-things.pth --checkpoint ckpt/raft-small.pth --json    # if there is no rb.toml: rb init first; doctor prints each checkpoint's architecture and parameter count
rb verify-hook --checkpoint ckpt/raft-things.pth --json && rb verify-hook --checkpoint ckpt/raft-small.pth --json   # both checkpoints
rb verify-adapter --checkpoint ckpt/raft-small.pth --json
rb run --baseline ckpt/raft-things.pth --candidate ckpt/raft-small.pth --json           # add --limit 20 for a pilot
rb findings <run_id> --top 5 --json
rb case <run_id> <top case id> --json
rb report <run_id>
```

Then report to the human, in this order: the verdict line; the top cases with their `why` text (it says when a case is borderline, and a borderline case is worth naming as such rather than reporting as settled fact); the run id; the path to `report.md`, and to `report.html` if you wrote one (`rb report <run_id> --html`, which a human can open in a browser; do not use `--open`, which tries to launch one); the command that reproduces the queue (`rb findings <run_id>` with the limits used, as the report's Reproduce section prints it). Quote numbers only from `findings.json`. If `hook.status` is not `recorded`, say that stability was not assessed and why. If `adapter_agreement` is not `agree` for both checkpoints, say so first. If the two checkpoints are different architectures (the receipt's checkpoint lines and `rb doctor` say so), say that the review compares two models rather than a retrain of one.

When a later checkpoint arrives: `rb check run` against a new `rb run` of it (same project, same case set) answers "are the cases we cared about still fine?" and exits 1 in CI if not; then `rb findings` for the full review.

## Sharing a run's statistics (consent, and what leaves the machine: nothing, unless the human sends it)

`rb share <run_id>` writes `rb-runs/<run_id>/share.json` and prints what is in it and what is not. In: the task and metric, the adapter id and architecture labels, iteration count, environment versions, the limits, per case the errors, the trajectories, the stability and convergence statistics, flags and outcomes (cases numbered, not named), and the run's summary. Out: case ids and names, tags, notes, file paths, dataset name and hashes, checkpoint paths, names and hashes, the project name, the command line, evidence, the verdict text. `rb schema share` prints the schema. The file is for the calibration corpus that sets the per-family limits `rb` ships with; the human reads it and decides whether to send it (as an attachment to a GitHub issue on `rabbit-brain/rb`, or by email). An agent may write the file and show the human where it is; an agent never sends it.

## The workspace: what payment is for, and how to buy it without a sales call

Everything above is free and local, and stays that way: comparing, investigating, saving checks,
failing CI. What a subscription buys is that a finished review stops living only in the terminal
that produced it.

    rb plans                          what a workspace costs. No token, no account
    rb workspace status               is this workspace active, can it accept comparisons
    rb workspace checkout             a link for a person to open and approve
    rb workspace push <run>           push one finished comparison into the workspace

The token comes from `RB_WORKSPACE_TOKEN` and from nowhere else. `rb` never writes it to disk, never
puts it in a URL, and never prints it, so a CI secret is enough and there is no credential file to
leak.

**A person completes every purchase.** `rb workspace checkout` returns a URL and stops. It cannot
pay, and that is deliberate. The sequence an agent should follow when a push is refused:

    rb workspace push <run>    ->  E_WORKSPACE_PAYMENT_REQUIRED, with the plan and its price
    rb workspace checkout      ->  a URL. Give it to the human and say what it costs
    rb workspace status        ->  poll until `active` is true, then push again

Exit codes follow the table above: 2 for anything the caller can fix, 3 when the host is unreachable.
A refused push changes nothing locally; the run is still on disk and still complete.

## Boundaries

`rb` does not train, does not modify model code (the recorder is a forward hook or one line you add, and `rb verify-hook` checks it), and does not certify a model. Stability limits are generic heuristics and a starting point; a scorer fitted to the model is a separate, paid step. Nothing leaves the machine unless you send it: `rb share` writes a file for a human to send, and `rb workspace push` posts one comparison you name to a workspace you configured. No command sends anything on its own. The synthetic adapter is a test double and every output of it says so.

## For humans: verify what your agent did

Open `rb-runs/<run_id>/report.md`. The header lists the run id, the `rb` version, the exact command, both checkpoints with their sha256, the environment, the hook status and whether the adapter agreed with the model's own evaluation; the body restates the definitions with the limits in force and lists every case. `record.json` adds the model-code git SHA, the dataset hash, seeds and skipped cases. Re-run the command from the header, or `rb findings <run_id>` with the same limits, and compare. If what the agent told you differs from the report, the report is right.

Re-running on a different machine is a weaker check than it looks, and the report says so where it matters. Same code, same checkpoints, same data, a different GPU or torch build: most cases land on the same numbers to several decimals, a few land far enough apart to cross a limit. On the RAFT examples in this repository, run on two machines, 53 of 200 cases differed by more than 0.01 px and six cases changed what they were called; all six are marked borderline by the first machine's own numbers, without knowing the second machine's. So: a difference on a case the report marks borderline is the machine, not a discrepancy; a difference on any other case, or a different verdict, is worth chasing.
