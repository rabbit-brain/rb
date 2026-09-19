"""Data contracts. Every command's inputs and outputs are pydantic models; `rb schema` prints them as JSON Schema.

Version 1 (the workspace / kit format, unchanged) is the input contract for `rb import`.
Version 2 (bundle) is what a run directory holds: the same per-case fields plus derived stability,
flags, a generalised metric object and an evaluation record.
"""
from __future__ import annotations

import math
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ID_PATTERN = r"^[a-zA-Z0-9_.-]{1,80}$"
SCORE_MAX = 1_000_000.0


def _finite_nonneg(values: list[float], name: str) -> list[float]:
    for v in values:
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0 or v > SCORE_MAX:
            raise ValueError(f"{name} must hold finite nonnegative numbers")
    return [float(v) for v in values]


# ---------------------------------------------------------------- limits & metric

class Limits(BaseModel):
    """What gets flagged. `max_regression` is in the metric's unit; the stability limits read the candidate's trajectory.
    `max_last_update` (same unit as the trajectory values) is off unless set: a case is not settled when the model was
    still moving its answer by more than this per iteration at the end."""
    model_config = ConfigDict(extra="forbid")
    max_regression: float = Field(0.3, ge=0, le=SCORE_MAX)
    max_late_share: float = Field(0.25, ge=0, le=1)
    max_reversals: int = Field(2, ge=0, le=64)
    max_last_update: Optional[float] = Field(default=None, ge=0, le=SCORE_MAX)
    max_trajectory_regression: Optional[float] = Field(default=None, ge=0, le=SCORE_MAX)  # paired: candidate late movement minus the current model's, same case


class Metric(BaseModel):
    """Any lower-is-better error with a unit (px for flow, cm or m for depth, ...)."""
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=60)
    name: str = Field(min_length=1, max_length=80)
    unit: str = Field(min_length=1, max_length=16)
    lower_is_better: Literal[True] = True

    @classmethod
    def from_v1(cls, metric: str, unit: Optional[str]) -> "Metric":
        slug = metric.strip()
        u = unit or ("px" if slug.endswith("_px") else "units")
        name = slug[:-3] if slug.endswith("_px") else slug
        name = name.replace("_", " ")
        return cls(id=slug, name=name, unit=u)


# ---------------------------------------------------------------- version 1 (input contract)

class CaseV1(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(pattern=ID_PATTERN)
    name: str = Field(min_length=1, max_length=120)
    tags: list[str] = Field(default_factory=list, max_length=8)
    baseline_error: float = Field(ge=0, le=SCORE_MAX)
    candidate_error: float = Field(ge=0, le=SCORE_MAX)
    baseline_frames: Optional[list[float]] = Field(default=None, min_length=2, max_length=512)
    candidate_frames: Optional[list[float]] = Field(default=None, min_length=2, max_length=512)
    baseline_trajectory: Optional[list[float]] = Field(default=None, min_length=2, max_length=64)
    candidate_trajectory: Optional[list[float]] = Field(default=None, min_length=2, max_length=64)
    notes: Optional[str] = Field(default=None, max_length=1000)

    @field_validator("name", mode="before")
    @classmethod
    def _strip_name(cls, v):
        return v.strip() if isinstance(v, str) else v

    @field_validator("tags")
    @classmethod
    def _tags(cls, v: list[str]) -> list[str]:
        for t in v:
            if len(t) > 40:
                raise ValueError("tags must be at most 40 characters each")
        return v

    @field_validator("baseline_error", "candidate_error")
    @classmethod
    def _finite(cls, v):
        if v is None:
            return v
        if not math.isfinite(v):
            raise ValueError("must be a finite nonnegative number")
        return float(v)

    @field_validator("baseline_frames", "candidate_frames", "baseline_trajectory", "candidate_trajectory")
    @classmethod
    def _series(cls, v, info):
        if v is None:
            return v
        return _finite_nonneg(v, info.field_name)

    @model_validator(mode="after")
    def _paired_frames(self):
        if (self.baseline_frames is None) != (self.candidate_frames is None) or (
            self.baseline_frames is not None and self.candidate_frames is not None and len(self.baseline_frames) != len(self.candidate_frames)
        ):
            raise ValueError("Frame series must be paired and have equal lengths.")
        return self


class ComparisonV1(BaseModel):
    """The version-1 results file: everything computed by the customer's own evaluator."""
    model_config = ConfigDict(extra="ignore")
    version: Literal[1]
    project: str = Field(min_length=1, max_length=100)
    baseline: str = Field(min_length=1, max_length=100)
    candidate: str = Field(min_length=1, max_length=100)
    dataset: str = Field(min_length=1, max_length=120)
    metric: str = Field(min_length=1, max_length=60)
    unit: Optional[str] = Field(default=None, min_length=1, max_length=16)
    source: Optional[Literal["example", "imported"]] = None
    cases: list[CaseV1] = Field(min_length=1, max_length=500)

    @field_validator("project", "baseline", "candidate", "dataset", "metric", "unit", mode="before")
    @classmethod
    def _strip(cls, v):
        return v.strip() if isinstance(v, str) else v

    @model_validator(mode="after")
    def _unique_ids(self):
        if len({c.id for c in self.cases}) != len(self.cases):
            raise ValueError("Case IDs must be unique.")
        return self

    def metric_obj(self) -> Metric:
        return Metric.from_v1(self.metric, self.unit)


# ---------------------------------------------------------------- version 2 (bundle)

class ModelRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    checkpoint: Optional[str] = None
    sha256: Optional[str] = None
    architecture: Optional[str] = None   # as the adapter read it from the checkpoint (raft, raft-small), when it can


class DatasetRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    count: int
    case_list_hash: Optional[str] = None   # sha256 over the case ids: the same set of cases
    content_hash: Optional[str] = None     # sha256 over the input and ground-truth files themselves: the same data (runs only)


class Convergence(BaseModel):
    """Per-case convergence history from the update fields themselves (the paper's readout families, averaged over pixels).
    Only a run with an instrumented model produces these; imported scalar trajectories cannot."""
    model_config = ConfigDict(extra="forbid")
    sign_reversal_rate: float = Field(ge=0, le=1)   # share of adjacent update pairs pointing in opposite directions (cosine < 0)
    mean_cos: float = Field(ge=-1, le=1)             # mean cosine between consecutive updates
    displacement_mean: float = Field(ge=0)           # mean distance of the intermediate estimates from the final one
    displacement_max: float = Field(ge=0)            # the largest of those
    displacement_initial: float = Field(ge=0)        # distance from the first estimate to the final one
    update_energy: float = Field(ge=0)               # sum over iterations of the squared update magnitude


class TrajectoryStats(BaseModel):
    """`late_share`, `reversals`, `peak_iteration`, `total`: the version-1 statistics, unchanged. The rest are the paper's
    convergence statistics computed from the same values (quarter windows, absolute magnitudes) plus, for runs, the
    direction and displacement statistics from the update fields."""
    model_config = ConfigDict(extra="forbid")
    iterations: int
    late_share: float
    reversals: int
    peak_iteration: int
    total: float
    last_update: Optional[float] = None       # magnitude of the final update
    late_update: Optional[float] = None       # mean update over the last quarter of iterations
    early_update: Optional[float] = None      # mean update over the first quarter
    late_to_early: Optional[float] = None     # late_update / early_update
    sign_reversal_rate: Optional[float] = None
    mean_cos: Optional[float] = None
    displacement_mean: Optional[float] = None
    displacement_max: Optional[float] = None
    displacement_initial: Optional[float] = None
    update_energy: Optional[float] = None


class CaseStability(BaseModel):
    model_config = ConfigDict(extra="forbid")
    baseline: Optional[TrajectoryStats] = None
    candidate: Optional[TrajectoryStats] = None


class Evidence(BaseModel):
    model_config = ConfigDict(extra="allow")
    dir: str                                   # relative to the run directory
    files: list[str] = Field(default_factory=list)


ErrorOutcome = Literal["regression", "improved", "stable", "not_measured"]
StabilityOutcome = Literal["unstable", "settled", "not_assessed"]


class CaseV2(CaseV1):
    """A version-1 case plus derived fields. Derived fields are recomputed by `rb findings` when limits change.
    Errors may be null for unlabeled cases (`has_gt` false): stability is still assessed, error is not."""
    model_config = ConfigDict(extra="ignore")
    baseline_error: Optional[float] = Field(default=None, ge=0, le=SCORE_MAX)  # type: ignore[assignment]
    candidate_error: Optional[float] = Field(default=None, ge=0, le=SCORE_MAX)  # type: ignore[assignment]
    has_gt: bool = True
    error_change: Optional[float] = None
    late_update_change: Optional[float] = None  # candidate late movement minus the current model's on this case (trajectory unit)
    baseline_convergence: Optional[Convergence] = None
    candidate_convergence: Optional[Convergence] = None
    stability: CaseStability = Field(default_factory=CaseStability)
    error_outcome: ErrorOutcome = "stable"
    stability_outcome: StabilityOutcome = "not_assessed"
    flags: list[str] = Field(default_factory=list)
    borderline: list[str] = Field(default_factory=list)   # limits this case turns on by less than a tenth of the limit
    evidence: Optional[Evidence] = None


class Bundle(BaseModel):
    """`rb-runs/<run_id>/bundle.json`."""
    model_config = ConfigDict(extra="ignore")
    version: Literal[2]
    run_id: str
    project: str
    task: str = "generic"
    source: Literal["run", "imported", "example"]
    metric: Metric
    baseline: ModelRef
    candidate: ModelRef
    dataset: DatasetRef
    limits: Limits
    record: str = "record.json"
    adapter: Optional[dict] = None
    cases: list[CaseV2]

    def to_v1(self) -> ComparisonV1:
        """The workspace-compatible view of this bundle (drops derived fields)."""
        return ComparisonV1(
            version=1, project=self.project, baseline=self.baseline.name, candidate=self.candidate.name,
            dataset=self.dataset.name, metric=self.metric.id, unit=self.metric.unit,
            source="example" if self.source == "example" else "imported",
            cases=[CaseV1(**c.model_dump(include=set(CaseV1.model_fields))) for c in self.cases if c.baseline_error is not None and c.candidate_error is not None],
        )


class Record(BaseModel):
    """`record.json`: the receipt. Everything a second engineer needs to reproduce the run."""
    model_config = ConfigDict(extra="allow")
    rb_version: str
    run_id: str
    command: str
    started: str
    finished: str
    wall_seconds: float
    source: Literal["run", "imported", "example"]
    checkpoints: dict[str, ModelRef]
    dataset: DatasetRef
    environment: dict
    hook: dict
    limits: Limits
    input: Optional[dict] = None
    adapter: Optional[dict] = None
    adapter_agreement: Optional[dict] = None   # per checkpoint: the adapter's errors against the model repository's own evaluation on a few cases
    model_code: Optional[dict] = None
    seeds: Optional[dict] = None
    deterministic_algorithms: Optional[bool] = None
    notes: str = ""


# ---------------------------------------------------------------- findings

class MeanError(BaseModel):
    model_config = ConfigDict(extra="forbid")
    baseline: float
    candidate: float
    change_pct: Optional[float]


class CheckCounts(BaseModel):
    model_config = ConfigDict(extra="forbid")
    saved: int = 0
    passing: int = 0
    failing: int = 0
    missing: int = 0
    other_project: int = 0


class Summary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cases: int
    with_gt: int
    with_trajectories: int
    mean_error: MeanError
    regressions: int
    improved: int
    stable: int
    unstable: int
    improved_unstable: int
    settled_regressions: int
    flagged: int
    borderline: int = 0            # cases whose flag (or lack of one) turns on a margin of less than a tenth of a limit
    borderline_flagged: int = 0    # of those, the ones that are flagged
    checks: CheckCounts = Field(default_factory=CheckCounts)


class Verdict(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["clear", "investigate", "checks_failing"]
    ready: bool
    line: str
    start: Optional[str] = None
    start_name: Optional[str] = None


class QueueItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rank: int
    id: str
    name: str
    tags: list[str] = Field(default_factory=list)
    flags: list[str]
    error_outcome: ErrorOutcome
    stability_outcome: StabilityOutcome
    baseline_error: Optional[float] = None
    candidate_error: Optional[float] = None
    error_change: Optional[float] = None
    baseline_late_share: Optional[float] = None
    baseline_reversals: Optional[int] = None
    candidate_late_share: Optional[float] = None
    candidate_reversals: Optional[int] = None
    baseline_late_update: Optional[float] = None
    candidate_late_update: Optional[float] = None
    late_update_change: Optional[float] = None
    borderline: list[str] = Field(default_factory=list)   # limits this case turns on by less than a tenth of the limit
    why: str
    notes: Optional[str] = None
    evidence: Optional[str] = None
    rerun: Optional[str] = None


class Findings(BaseModel):
    """`findings.json`: the ranked queue, the summary and the verdict, under the limits given."""
    model_config = ConfigDict(extra="forbid")
    run_id: str
    project: str
    metric: Metric
    limits: Limits
    summary: Summary
    verdict: Verdict
    queue: list[QueueItem]


# ---------------------------------------------------------------- checks

class CheckV1(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(pattern=ID_PATTERN)
    name: str = Field(default="", max_length=120)
    project: str = Field(max_length=100)
    max_error: float = Field(ge=0, le=SCORE_MAX)
    max_late_share: Optional[float] = Field(default=None, ge=0, le=1)


class ChecksV1(BaseModel):
    model_config = ConfigDict(extra="ignore")
    version: Literal[1]
    checks: list[CheckV1]


class CheckV2(BaseModel):
    model_config = ConfigDict(extra="ignore")
    case_id: str = Field(pattern=ID_PATTERN)
    name: str = Field(default="", max_length=120)
    max_error: float = Field(ge=0, le=SCORE_MAX)
    max_late_share: Optional[float] = Field(default=None, ge=0, le=1)
    max_reversals: Optional[int] = Field(default=None, ge=0, le=64)
    from_run: Optional[str] = None
    created: Optional[str] = None
    unit: Optional[str] = None          # the metric's unit when the check was saved, so `rb check list` can say it
    note: str = Field(default="", max_length=400)


class ChecksV2(BaseModel):
    """`checks.json`: saved limits per case id for one project. Commit it next to the model code."""
    model_config = ConfigDict(extra="ignore")
    version: Literal[2]
    project: str = Field(min_length=1, max_length=100)
    checks: list[CheckV2] = Field(default_factory=list)


CheckStatus = Literal["passing", "failing", "missing", "other-project"]


class CheckResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case_id: str
    status: CheckStatus
    reason: str
    max_error: float
    max_late_share: Optional[float] = None
    max_reversals: Optional[int] = None
    candidate_error: Optional[float] = None


# ---------------------------------------------------------------- envelope

class ErrorItem(BaseModel):
    model_config = ConfigDict(extra="allow")
    code: str
    message: str
    fix: Optional[str] = None


class Envelope(BaseModel):
    """What every command prints with --json: exactly one object on stdout."""
    model_config = ConfigDict(extra="forbid")
    ok: bool
    rb_version: str
    command: str
    run_id: Optional[str] = None
    data: dict = Field(default_factory=dict)
    errors: list[ErrorItem] = Field(default_factory=list)
    next: list[str] = Field(default_factory=list)


SCHEMAS: dict[str, type[BaseModel]] = {
    "bundle": Bundle,
    "share": None,  # type: ignore[dict-item]  (filled below: share.py imports this module)
    "brief": None,  # type: ignore[dict-item]  (filled by onboard.py when it is imported; it sits above config, which sits above this module)
    "record": Record,
    "findings": Findings,
    "checks": ChecksV2,
    "checks-v1": ChecksV1,
    "comparison-v1": ComparisonV1,
    "envelope": Envelope,
    "limits": Limits,
}


def _register_share() -> None:
    from .share import ShareV1
    SCHEMAS["share"] = ShareV1


_register_share()
