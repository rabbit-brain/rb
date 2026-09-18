"""The adapter contract. A task owns the metric and its unit; an adapter owns loading and inference for one codebase.

Write your own as `package.module:Class` in rb.toml (`[adapter] module = ...`). It needs the four methods below;
everything else is optional. Keep it label-free where you can: `gt` may be None and `metric_value` then returns None.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional, Protocol, runtime_checkable

from ..config import Config
from ..models import Metric
from ..recorder import TrajectoryRecorder


@dataclass
class Case:
    id: str                       # stable across checkpoints; ^[a-zA-Z0-9_.-]{1,80}$
    name: str
    inputs: Any                   # whatever `infer` needs (paths, arrays, tensors)
    gt: Any = None                # ground truth, or None when unlabeled
    tags: list[str] = field(default_factory=list)
    notes: Optional[str] = None


@dataclass
class Prediction:
    output: Any
    per_frame: Optional[list[float]] = None   # optional per-frame error series for sequence cases


@runtime_checkable
class Adapter(Protocol):
    task: str
    metric: Metric
    synthetic: bool               # True only for test doubles; the report says so

    def describe(self) -> dict: ...                                   # id, version, settings → record.json
    def load(self, checkpoint: Path, device: str) -> Any: ...          # a model ready for inference
    def cases(self) -> Iterable[Case]: ...                             # the case set from the config
    def infer(self, model: Any, case: Case, rec: TrajectoryRecorder) -> Prediction: ...   # records one value per iteration
    def metric_value(self, pred: Prediction, case: Case) -> Optional[float]: ...          # None when case.gt is None
    def expected_iterations(self) -> Optional[int]: ...               # for rb verify-hook; None if unknown


TASK_METRICS: dict[str, Metric] = {
    "flow": Metric(id="mean_endpoint_error", name="mean endpoint error", unit="px"),
    "stereo": Metric(id="mean_disparity_error", name="mean disparity error", unit="px"),
    "depth": Metric(id="mean_abs_depth_error", name="mean absolute depth error", unit="cm"),
    "generic": Metric(id="error", name="error", unit="units"),
}


def task_metric(cfg: Config) -> Metric:
    if cfg.metric is not None:
        return cfg.metric
    return TASK_METRICS.get(cfg.project.task, TASK_METRICS["generic"])


def read_case_selector(cfg: Config) -> tuple[Optional[set[str]], Optional[int]]:
    """`dataset.cases`: "all" → (None, None); a file → (ids, None); a number → (None, N)."""
    spec = (cfg.dataset.cases or "all").strip()
    if spec == "all":
        return None, None
    if spec.isdigit():
        return None, int(spec)
    p = Path(spec)
    if p.exists():
        ids = {line.strip() for line in p.read_text(encoding="utf-8").splitlines() if line.strip() and not line.startswith("#")}
        return ids, None
    return None, None
