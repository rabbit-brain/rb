"""`rb onboard`: turn a described setup into a working project, or into the honest shape of one.

Most teams are not RAFT. The point of this command is not to pretend otherwise: it takes a brief (task,
architecture, framework, checkpoints, data, labels, what they want to compare), writes the configuration,
and then either points at a built-in adapter or writes a scaffold whose unwritten parts are named, one by
one, with what each must return. `INTEGRATION.md` carries the ladder that turns either into a trusted run:
doctor, verify-hook, verify-adapter, a five-case run, the real run. Nothing here is sent anywhere, and the
files it writes are ordinary files in the user's own directory.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from .config import AdapterSection, Config, DatasetSection, Limits, ProjectSection, render_config
from .errors import RBError
from .models import SCHEMAS, Metric

BRIEF_NAME = "brief.json"
INTEGRATION_NAME = "INTEGRATION.md"
ADAPTER_NAME = "rb_adapter.py"

# Architectures the built-in raft adapter covers: princeton-vl/RAFT and forks with the same core/ layout.
RAFT_FAMILY = ("raft", "raft-small", "sea-raft", "searaft", "gmflow-raft", "raft-things", "raft-kitti", "raft-sintel")


class Checkpoints(BaseModel):
    model_config = ConfigDict(extra="forbid")
    current: str = Field(min_length=1, description="path to the checkpoint you ship today")
    candidate: Optional[str] = Field(default=None, description="path to the one you are considering; may be filled in later")


class DataBrief(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1, description="directory holding the cases")
    kind: str = Field(default="custom", description="kitti | npz | custom; kitti means image_2/*_10.png with flow_occ/")
    layout: Optional[str] = Field(default=None, description="one sentence: how one case is stored on disk")
    labels: Literal["all", "some", "none"] = "all"


class BriefV1(BaseModel):
    """What a team can say about their setup without sending us anything. `rb schema brief` prints this."""
    model_config = ConfigDict(extra="ignore")
    version: Literal[1] = 1
    project: str = Field(min_length=1, max_length=100)
    task: Literal["flow", "stereo", "depth", "generic"] = "flow"
    architecture: str = Field(min_length=1, max_length=120, description="e.g. RAFT, IGEV-Stereo, our own encoder plus GRU")
    framework: str = Field(default="pytorch", max_length=60)
    model_code: Optional[str] = Field(default=None, description="path to the repository holding the model")
    evaluator: Optional[str] = Field(default=None, description="path to your own evaluation script, if you have one; it becomes the reference path")
    iterations: Optional[int] = Field(default=None, ge=2, le=64, description="refinement iterations per case; leave null if you do not know yet")
    checkpoints: Checkpoints
    data: DataBrief
    metric: Optional[Metric] = Field(default=None, description="override the task default; any lower-is-better error with a unit")
    compare: Optional[str] = Field(default=None, max_length=400, description="one sentence: what decision this review has to support")
    device: str = "cuda"


EXAMPLE_BRIEF = BriefV1(
    project="warehouse-stereo",
    task="stereo",
    architecture="IGEV-Stereo",
    framework="pytorch",
    model_code="./igev",
    evaluator="./igev/evaluate_stereo.py",
    iterations=16,
    checkpoints=Checkpoints(current="./ckpt/igev-sceneflow.pth", candidate="./ckpt/igev-finetuned.pth"),
    data=DataBrief(path="./data/yard-val", kind="custom", layout="one folder per case: left.png, right.png, disp.npy", labels="some"),
    compare="whether the finetuned checkpoint is safe to ship on the yard set, including the cases with no disparity ground truth",
)


def is_supported(brief: BriefV1) -> Optional[str]:
    """The built-in adapter id that fits this brief, or None: most briefs get None and that is the normal case."""
    arch = brief.architecture.strip().lower().replace("_", "-").replace(" ", "-")
    if brief.task == "flow" and any(arch == f or arch.startswith(f + "-") or f in arch for f in RAFT_FAMILY):
        return "raft"
    return None


def config_from(brief: BriefV1, adapter_id: Optional[str]) -> Config:
    from .adapters.base import TASK_METRICS   # local: adapters sits above config, which sits above models
    metric = brief.metric or TASK_METRICS.get(brief.task, TASK_METRICS["generic"])
    cfg = Config(
        project=ProjectSection(name=brief.project, task=brief.task),
        adapter=AdapterSection(
            id=adapter_id, module=None if adapter_id else f"rb_adapter:{class_name(brief)}",
            model_code=brief.model_code, iterations=brief.iterations or 12, device=brief.device,
            reference_cases=5 if (adapter_id or brief.evaluator) else 0,
        ),
        dataset=DatasetSection(name=Path(brief.data.path).name or "cases", kind=brief.data.kind, path=brief.data.path, cases="all"),
        metric=None if brief.metric is None else brief.metric,
    )
    cfg.limits = Limits(max_trajectory_regression=cfg.limits.max_regression)
    if not adapter_id:
        for key in ("small", "mixed_precision", "alternate_corr"):
            cfg.adapter.__dict__.pop(key, None)
    return cfg, metric


def class_name(brief: BriefV1) -> str:
    stem = "".join(ch for ch in brief.architecture.title() if ch.isalnum()) or "My"
    return f"{stem}Adapter"


def adapter_scaffold(brief: BriefV1, metric: Metric) -> str:
    """A file that imports, that `rb doctor` can load, and whose unwritten parts each say what they must return.

    Deliberately not a working adapter. Nothing here can guess a team's loader, their valid mask or their
    metric formula, and a scaffold that quietly returned plausible numbers would be the exact failure the
    adapter-agreement check exists to catch.
    """
    cls = class_name(brief)
    iters = brief.iterations or 12
    has_eval = bool(brief.evaluator)
    gt_note = {"all": "every case has ground truth", "some": "some cases have ground truth; yield gt=None for the rest and rb assesses them on the trajectory alone",
               "none": "no ground truth; yield gt=None everywhere and rb reviews on the trajectory alone"}[brief.data.labels]
    reference = f'''
    # --- the reference path: what makes the numbers trustworthy ------------------------------------
    def reference_description(self):
        return "{Path(brief.evaluator).name if has_eval else 'your own evaluation script'}: its own loader, forward call and metric formula, {iters} iterations"

    def reference_value(self, model, case):
        """TODO (5 of 5). The SAME per-case error, computed through YOUR evaluator's own code path.

        Share no code with infer() or metric_value() above: import {brief.evaluator or './your_eval.py'} and call it.
        rb compares the two on a few cases before it reports anything, and refuses on a disagreement beyond
        0.001 relative. This is the check that catches an adapter that loads or scores the model slightly
        differently from the way your team actually evaluates it, which produces numbers that look fine and
        are not about your model. Return a float.
        """
        raise NotImplementedError("rb_adapter.reference_value: see INTEGRATION.md step 5")
'''
    return f'''"""Rabbit Brain adapter for {brief.architecture} ({brief.task}).

Generated by `rb onboard` from {BRIEF_NAME}. Five TODOs; nothing else needs changing.
Run `rb doctor` after each one and it will tell you which step you are on.

What rb needs from you and nothing more: load a checkpoint, list the cases, run the model once per case
while a recorder watches the refinement, and return the per-case error. rb does the comparison, the ranking,
the wording and the receipt, and it never edits your model code: the recorder is a forward hook.
"""
from pathlib import Path

from rabbit_brain.adapters import Case, Metric, Prediction, RBError, read_case_selector


class {cls}:
    task = "{brief.task}"
    metric = Metric(id="{metric.id}", name="{metric.name}", unit="{metric.unit}")
    synthetic = False
    trajectory_scale = 1.0   # multiply recorded updates by this to put them in the metric's unit (RAFT uses 8: its updates are at 1/8 resolution)

    def __init__(self, cfg):
        self.cfg = cfg
        self.iterations = cfg.adapter.iterations
        self.root = Path(cfg.dataset.path)
        self.architectures = {{}}   # checkpoint path -> a pure architecture label; the receipt carries it and rb warns when the two differ

    def describe(self):
        return {{"id": "{brief.project}", "iterations": self.iterations, "hook": "forward hook on the update module", "architectures": dict(self.architectures)}}

    def load(self, checkpoint: Path, device: str):
        """TODO (1 of 5). Return your model, on `device`, in eval mode, with the weights at `checkpoint` loaded.

        Your code lives at {brief.model_code or '[adapter] model_code in rb.toml'}, which rb puts on sys.path.
        Record a pure architecture label in self.architectures[str(checkpoint)] (never a learned value).
        """
        if not checkpoint.exists():
            raise RBError("E_CHECKPOINT_NOT_FOUND", message=f"No such checkpoint: {{checkpoint}}")
        raise NotImplementedError("rb_adapter.load: see INTEGRATION.md step 3")

    def cases(self):
        """TODO (2 of 5). Yield one Case per sample, from {brief.data.path}.

        Layout you described: {brief.data.layout or 'not stated in the brief'}.
        Labels: {gt_note}.
        `id` must be stable across checkpoints; saved checks follow it. Honour the case selector so
        `--limit` and a cases file work.
        """
        ids, limit = read_case_selector(self.cfg)
        for i, path in enumerate(sorted(self.root.glob("*"))):
            if ids is not None and path.stem not in ids:
                continue
            if limit is not None and i >= limit:
                break
            raise NotImplementedError("rb_adapter.cases: see INTEGRATION.md step 3")
            yield Case(id=path.stem, name=path.stem, inputs=str(path), gt=str(path))

    def infer(self, model, case, rec):
        """TODO (3 of 5). Run the model once on `case` and return its output.

        The one line that matters: wrap the forward call in `rec.attached(<the module applied once per
        refinement iteration>)`, or call `rec.step(delta)` inside your own loop. That is the whole
        instrumentation, and `rb verify-hook` proves it fired exactly {iters} times.

            with torch.no_grad(), rec.attached(model.update_block, output_index=2):
                out = model(...)
            return Prediction(output=out)
        """
        raise NotImplementedError("rb_adapter.infer: see INTEGRATION.md step 4")

    def metric_value(self, pred, case):
        """TODO (4 of 5). The per-case error, lower is better, in {metric.unit}. Return None when case.gt is None.

        Use the same formula and the same valid mask your team already uses, so this number means what your
        team means by it. {metric.name} is the default for a {brief.task} task; the brief did not override it.
        """
        if case.gt is None:
            return None
        raise NotImplementedError("rb_adapter.metric_value: see INTEGRATION.md step 4")

    def expected_iterations(self):
        return self.iterations
{reference if has_eval else ""}
    # --- optional: evidence sheets. Without these rb still renders the disagreement map, the filmstrips
    # and the trajectory plot; with them it also renders the fields, the error maps and the change in error.
    # def read_images(self, case): return [<HxWx3 uint8>, ...]
    # def read_gt(self, case): return <field in the prediction's layout>, <HxW bool valid mask>
'''


def integration_md(brief: BriefV1, adapter_id: Optional[str], metric: Metric, cfg_path: str) -> str:
    cls = class_name(brief)
    iters = brief.iterations or 12
    built_in = adapter_id is not None
    cand = brief.checkpoints.candidate or "<candidate checkpoint>"
    cur = brief.checkpoints.current
    head = (
        f"# {brief.project}: integrating Rabbit Brain\n\n"
        f"Generated by `rb onboard` from `{BRIEF_NAME}`. Nothing was sent anywhere; these are ordinary files in this directory.\n\n"
        f"**What you told us.** A {brief.task} model, {brief.architecture}, on {brief.framework}. "
        f"Cases in `{brief.data.path}` ({brief.data.kind} layout, ground truth: {brief.data.labels}). "
        f"{iters} refinement iterations per case. Error measured as {metric.name} in {metric.unit}."
        + (f" The decision this has to support: {brief.compare}\n\n" if brief.compare else "\n\n")
    )
    if built_in:
        body = (
            f"**Good news: `{adapter_id}` covers {brief.architecture}.** There is nothing to write. `{cfg_path}` points at it, "
            f"and the ladder below is the whole integration.\n\n"
        )
        steps = [
            ("1", "Install", f"`pip install \"rabbit-brain[{adapter_id}]\"`", "Brings the adapter's own requirements."),
            ("2", "Check the environment", "`rb doctor --checkpoint " + cur + "`", "Config, device, model code, dataset, and each checkpoint's sha256, architecture and parameter count. Exit 3 and a named fix if something is wrong."),
            ("3", "Prove the instrumentation", f"`rb verify-hook --checkpoint {cur}`", f"Runs one case and asserts the recorder fired exactly {iters} times. `E_HOOK_NOT_REACHABLE` or `E_HOOK_LENGTH` if not."),
            ("4", "Prove the numbers", f"`rb verify-adapter --checkpoint {cur}`", "Compares the adapter's per-case error with the model repository's own evaluation path on a few cases. This is the step that makes the rest trustworthy; see below."),
            ("5", "A small run first", f"`rb run --baseline {cur} --candidate {cand} --limit 5`", "Five cases end to end. Read `rb-runs/<run>/report.md`."),
            ("6", "The real review", f"`rb run --baseline {cur} --candidate {cand}`", "Then `rb findings <run> --top 5`, and `rb case <run> <id> --render` for the case it names."),
        ]
    else:
        body = (
            f"**We do not have a built-in adapter for {brief.architecture}, and we are not going to pretend we do.** "
            f"`{ADAPTER_NAME}` is the shape of one: the parts only you can write are marked TODO, five of them, and each says what it must return. "
            f"Everything else (the comparison, the ranking, the wording, the evidence, the receipt) is already written.\n\n"
            f"Why it is not a working adapter out of the box: nothing can guess your loader, your valid mask or your metric formula, "
            f"and a scaffold that quietly returned plausible numbers would be exactly the failure step 5 exists to catch.\n\n"
            f"Expect an hour if your model already exposes its refinement loop, longer if you have to find where the per-iteration update lives.\n\n"
        )
        steps = [
            ("1", "Install", "`pip install rabbit-brain` (add `[evidence]` for the sheets: numpy and pillow)", "The core depends on pydantic only; your model's own requirements stay yours."),
            ("2", "Read the file", f"`{ADAPTER_NAME}`", f"Class `{cls}`, five TODOs, in the order below. `rb docs` prints the full manual including a complete worked adapter."),
            ("3", "Loading and listing", "`rb doctor`", "Fill TODO 1 (`load`) and TODO 2 (`cases`), then run this. It names what is still missing. Repeat until it passes."),
            ("4", "One case, instrumented", f"`rb verify-hook --checkpoint {cur}`", f"Fill TODO 3 (`infer`) and TODO 4 (`metric_value`). This runs one case and asserts the recorder fired exactly {iters} times. The recorder is a forward hook; your model code is not edited."),
            ("5", "Prove the numbers", f"`rb verify-adapter --checkpoint {cur}`", "Fill TODO 5 (`reference_value`) if you have an evaluation script. See below; this is the most valuable hour in the integration."),
            ("6", "A small run first", f"`rb run --baseline {cur} --candidate {cand} --limit 5`", "Five cases end to end. Read `rb-runs/<run>/report.md`."),
            ("7", "The real review", f"`rb run --baseline {cur} --candidate {cand}`", "Then `rb findings <run> --top 5`, and `rb case <run> <id> --render` for the case it names."),
        ]
    ladder = "\n".join(f"**{n}. {title}.** {cmd}\n\n{why}\n" for n, title, cmd, why in steps)
    reference_section = (
        "## Why step " + ("4" if built_in else "5") + " matters more than it looks\n\n"
        "An adapter that loads, preprocesses or scores the model even slightly differently from the way your team evaluates it "
        "produces numbers that are the right shape, the right range and entirely plausible, and are not about your model. "
        "Every number after that is wrong in a way no review catches. So an adapter states a reference path: the same per-case "
        "error computed through your own evaluation code, sharing nothing with the adapter, and `rb` compares the two before it "
        "reports anything. A disagreement beyond 0.001 relative stops the run and writes nothing. Every receipt records the result.\n\n"
        + ("The brief named `" + brief.evaluator + "` as your evaluator, and TODO 5 is wired to it.\n\n" if brief.evaluator else
           "The brief did not name an evaluation script. If your team has one, point TODO 5 at it; if not, `[adapter] reference_cases = 0` in the config turns the check off and every receipt then says the agreement was not established, which is the honest state and worth fixing later.\n\n")
    )
    agent = (
        "## If a coding agent is doing this\n\n"
        "Give it this directory and one line:\n\n"
        "> Review candidate checkpoint B against A on this case set with Rabbit Brain. Read `INTEGRATION.md` and `rb docs` first.\n\n"
        "`rb docs` prints the full manual, every command takes `--json`, every error carries a code and a fix, and exit codes are "
        "0 ok, 1 a check failed, 2 bad input, 3 an environment or adapter problem. Tell it not to compute errors, regressions, "
        "rankings or verdicts itself: `rb` defines those and records how, which is what makes two runs comparable.\n\n"
    )
    cannot = (
        "## What a form could not tell us\n\n"
        "Three things decide whether these numbers mean what your team means, and none of them can be inferred from a brief: "
        "the exact metric formula and valid mask your team already uses, which module in your model applies one refinement "
        "iteration, and whether your case ids stay stable across checkpoints (saved checks follow them). The TODOs ask for the "
        "first two and step " + ("4" if built_in else "5") + " verifies them. The third is worth five minutes of thought now rather than at the next release.\n\n"
    )
    back = (
        "## When you are through, or when you are stuck\n\n"
        "Nothing in this tool phones home and nothing here will. If it worked and you are willing, `rb share <run>` writes "
        "`share.json`, the run's anonymised statistics with no case ids, names, paths, dataset or checkpoint names; read it, and "
        "send it if you want to, as an attachment on an issue at https://github.com/rabbit-brain/rb/issues. That file is what "
        "calibrates the generic limits per model family, and right now the limits are heuristics because that corpus is empty.\n\n"
        "If it stopped, the most useful thing you can send is the command you ran and the error, which carries a code and a fix. "
        "An adapter for a model we do not support yet is a thing we will help write.\n\n"
        "One honest caveat to carry into the first review: the stability limits that ship with the tool are generic starting "
        "points, not properties of your model. The report shows each limit against your data's own distribution, and marks as "
        "**borderline** any finding that turns on a margin thin enough that the same checkpoints on another machine might sort it the other way.\n"
    )
    return head + body + "## The ladder\n\n" + ladder + "\n" + reference_section + agent + cannot + back


def write_package(brief: BriefV1, root: Path, force: bool = False, source: Optional[Path] = None) -> dict:
    """`source` is the brief the user passed in: it is already on disk by definition, so it is never a conflict,
    and it is only copied when it lives somewhere else."""
    from .config import CONFIG_NAME
    adapter_id = is_supported(brief)
    cfg, metric = config_from(brief, adapter_id)
    brief_target = root / BRIEF_NAME
    same_brief = source is not None and source.resolve() == brief_target.resolve()
    targets = [root / CONFIG_NAME, root / INTEGRATION_NAME] + ([] if same_brief else [brief_target]) + ([] if adapter_id else [root / ADAPTER_NAME])
    existing = [str(p) for p in targets if p.exists()]
    if existing and not force:
        raise RBError("E_CONFIG_INVALID", message=f"These would be overwritten: {', '.join(existing)}. Pass --force if that is what you want.")
    root.mkdir(parents=True, exist_ok=True)
    written = []
    (root / CONFIG_NAME).write_text(render_config(cfg), encoding="utf-8"); written.append(CONFIG_NAME)
    if not adapter_id:
        (root / ADAPTER_NAME).write_text(adapter_scaffold(brief, metric), encoding="utf-8"); written.append(ADAPTER_NAME)
    (root / INTEGRATION_NAME).write_text(integration_md(brief, adapter_id, metric, CONFIG_NAME), encoding="utf-8"); written.append(INTEGRATION_NAME)
    if not same_brief:
        brief_target.write_text(json.dumps(brief.model_dump(exclude_none=True), indent=1) + "\n", encoding="utf-8"); written.append(BRIEF_NAME)
    return {"dir": str(root), "files": written, "adapter": adapter_id or f"rb_adapter:{class_name(brief)}",
            "built_in": adapter_id is not None, "todos": 0 if adapter_id else (5 if brief.evaluator else 4),
            "metric": metric.model_dump(), "task": brief.task}


SCHEMAS["brief"] = BriefV1   # `rb schema brief`; registered here because models cannot import this module
