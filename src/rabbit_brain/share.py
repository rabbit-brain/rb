"""`rb review share`: the anonymised statistics of a run, as a file the human can send to the calibration corpus.

The command sends nothing. `rb review share <run>` writes `rb-runs/<run>/share.json`, prints exactly what is in it and what is
not, and says how to send it. The corpus is what calibrates the generic limits per model family ("on RAFT-family
models, late movement above X predicts a real regression with precision Y"); every contribution is one run's numbers.

What goes in: the task and metric, the adapter id and architecture labels, iteration count, environment versions, the
limits in force, per case the errors, the trajectories, the stability and convergence statistics, the flags and the
outcome (cases are numbered, not named), and the run's summary. What stays out: case ids and names, tags and notes,
file paths, dataset names and hashes, checkpoint paths, names and hashes, the command line, notes, evidence, the
verdict text (it names a case), the project name.
"""
from __future__ import annotations

import json
import secrets
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from . import __version__
from .models import Bundle, Findings, Limits, Record, TrajectoryStats

INCLUDED = ("task and metric (id, unit)", "adapter id and per-checkpoint architecture labels", "iteration count and trajectory scale",
            "python, torch and CUDA versions, the GPU name and the OS family", "the limits in force", "per case: both errors, both trajectories, the stability and convergence statistics, the flags and the outcome (cases numbered, not named)",
            "the run's summary counts")
EXCLUDED = ("case ids and names, tags, notes", "file paths, dataset name and hashes", "checkpoint paths, names and hashes", "the project name and the command line",
            "evidence images", "the verdict text (it names a case)")


class ShareCase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    index: int
    has_gt: bool
    baseline_error: Optional[float] = None
    candidate_error: Optional[float] = None
    error_change: Optional[float] = None
    late_update_change: Optional[float] = None
    baseline_trajectory: Optional[list[float]] = None
    candidate_trajectory: Optional[list[float]] = None
    baseline_stability: Optional[TrajectoryStats] = None
    candidate_stability: Optional[TrajectoryStats] = None
    error_outcome: str
    stability_outcome: str
    flags: list[str] = Field(default_factory=list)


class ShareV1(BaseModel):
    """`share.json`: one run's anonymised statistics for the calibration corpus. `rb schema share` prints this schema."""
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["rb-share-1"] = "rb-share-1"
    share_id: str                      # random per file; links nothing
    created: str
    rb_version: str
    source: str                        # run | imported | example
    task: str
    metric: dict                       # id, unit
    adapter: dict                      # id, architectures (labels), iterations, trajectory_scale, hook status
    environment: dict                  # python, torch, cuda, gpu, os family
    limits: Limits
    dataset: dict                      # count, with_gt, with_trajectories
    summary: dict                      # the run's summary counts and mean errors
    cases: list[ShareCase]


def build_share(bundle: Bundle, record: Optional[Record], findings: Findings) -> ShareV1:
    adapter = dict(bundle.adapter or {})
    archs = {}
    for role in ("baseline", "candidate"):
        ref = (record.checkpoints.get(role) if record else None) or getattr(bundle, role)
        if getattr(ref, "architecture", None):
            archs[role] = ref.architecture
    env = dict(record.environment) if record and record.environment else {}
    cases = []
    for i, c in enumerate(bundle.cases):
        cases.append(ShareCase(
            index=i, has_gt=c.has_gt, baseline_error=c.baseline_error, candidate_error=c.candidate_error, error_change=c.error_change,
            late_update_change=c.late_update_change, baseline_trajectory=c.baseline_trajectory, candidate_trajectory=c.candidate_trajectory,
            baseline_stability=c.stability.baseline, candidate_stability=c.stability.candidate,
            error_outcome=c.error_outcome, stability_outcome=c.stability_outcome, flags=list(c.flags),
        ))
    s = findings.summary
    return ShareV1(
        share_id=secrets.token_hex(8), created=datetime.now().astimezone().isoformat(timespec="seconds"), rb_version=__version__,
        source=bundle.source, task=bundle.task, metric={"id": bundle.metric.id, "unit": bundle.metric.unit},
        adapter={"id": adapter.get("id") or ("import" if bundle.source != "run" else "custom"), "architectures": archs,
                 "iterations": adapter.get("iterations"), "trajectory_scale": adapter.get("trajectory_scale"),
                 "hook": (record.hook or {}).get("status") if record else None},
        environment={**{k: env.get(k) for k in ("python", "torch", "cuda", "gpu") if env.get(k) is not None},
                     **({"os": str(env["platform"]).split("-")[0]} if env.get("platform") else {})},   # the OS family only, not the kernel string
        limits=findings.limits,
        dataset={"count": s.cases, "with_gt": s.with_gt, "with_trajectories": s.with_trajectories},
        summary={"mean_error": s.mean_error.model_dump(), "regressions": s.regressions, "improved": s.improved, "stable": s.stable,
                 "unstable": s.unstable, "improved_unstable": s.improved_unstable, "settled_regressions": s.settled_regressions, "flagged": s.flagged},
        cases=cases,
    )


def write_share(run_dir: Path, share: ShareV1) -> Path:
    path = run_dir / "share.json"
    path.write_text(json.dumps(share.model_dump(), indent=1) + "\n", encoding="utf-8")
    return path


def consent_text(share: ShareV1, path: Path) -> list[str]:
    return [
        f"Wrote {path}: {len(share.cases)} cases, {share.adapter.get('id')} adapter, {share.task} / {share.metric['id']} ({share.metric['unit']}).",
        "It contains: " + "; ".join(INCLUDED) + ".",
        "It does not contain: " + "; ".join(EXCLUDED) + ".",
        "Nothing has left this machine. Read the file; if you are willing to contribute it to the calibration corpus (the per-family limits that ship with rb),",
        "send it as an attachment to https://github.com/rabbit-brain/rb/issues/new?title=share (or by email to the maintainer). Delete it to withdraw before sending.",
    ]
