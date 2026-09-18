"""A synthetic iterative "model": a test double for the runner. Not a real model; every output says so.

A checkpoint is a small JSON file:  {"name": "synth-v2", "quality": 0.85, "instability": 0.3, "seed": 7}
  quality      scales the final error (lower is better; 1.0 = the reference)
  instability  share of cases whose refinement is late or drifting instead of settled
  seed         makes everything deterministic per (checkpoint, case)

`rb init --demo` writes a project with two such checkpoints so `rb run` works on any machine in seconds.
"""
from __future__ import annotations

import hashlib
import json
import math
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from .. import __version__
from ..config import Config
from ..errors import RBError
from ..models import Metric
from ..recorder import TrajectoryRecorder
from .base import Case, Prediction, read_case_selector

SHAPES = {
    "settled": [2.40, 1.52, .96, .63, .42, .29, .21, .15, .11, .08, .06, .05],
    "late": [2.30, 1.42, .90, .60, .43, .36, .31, .55, .84, .92, .78, .66],
    "drift": [2.20, 1.30, .80, .55, .45, .42, .41, .44, .50, .58, .64, .70],
}

SCENES = ["Reflective floor", "Loading bay", "Fast crossing", "Long aisle", "Tight corner", "Pallet approach", "Open aisle", "Ramp descent", "Stopped at station", "Wide turn", "Passing equipment", "Daylight aisle"]


@dataclass
class SyntheticModel:
    name: str
    quality: float
    instability: float
    seed: int


def _rng(*parts: object) -> random.Random:
    key = ":".join(str(p) for p in parts).encode("utf-8")
    return random.Random(int.from_bytes(hashlib.sha256(key).digest()[:8], "big"))


def _resample(shape: list[float], n: int) -> list[float]:
    """Stretch a 12-value shape to n iterations (linear interpolation)."""
    if n == len(shape):
        return list(shape)
    out = []
    for i in range(n):
        x = i * (len(shape) - 1) / max(n - 1, 1)
        lo, hi = int(math.floor(x)), min(int(math.floor(x)) + 1, len(shape) - 1)
        out.append(shape[lo] + (shape[hi] - shape[lo]) * (x - lo))
    return out


class SyntheticAdapter:
    task = "flow"
    metric = Metric(id="mean_endpoint_error", name="mean endpoint error", unit="px")
    synthetic = True

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.iterations = cfg.adapter.iterations
        self.count = int(getattr(cfg.dataset, "count", 24) or 24)

    def describe(self) -> dict:
        return {"id": "synthetic", "version": __version__, "iterations": self.iterations, "note": "Synthetic test double. Not a real model; numbers are illustrative."}

    def load(self, checkpoint: Path, device: str) -> SyntheticModel:
        if not checkpoint.exists():
            raise RBError("E_CHECKPOINT_NOT_FOUND", message=f"No such checkpoint: {checkpoint}")
        try:
            raw = json.loads(checkpoint.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            raise RBError("E_CHECKPOINT_NOT_FOUND", message=f"{checkpoint} is not a synthetic checkpoint (JSON): {exc}")
        return SyntheticModel(name=str(raw.get("name") or checkpoint.stem), quality=float(raw.get("quality", 1.0)), instability=float(raw.get("instability", 0.0)), seed=int(raw.get("seed", 0)))

    def cases(self) -> Iterable[Case]:
        ids, limit = read_case_selector(self.cfg)
        n = limit if limit else self.count
        for i in range(n):
            cid = f"syn-{i + 1:03d}"
            if ids is not None and cid not in ids:
                continue
            r = _rng("case", self.cfg.dataset.name, cid)
            difficulty = 1.5 + 3.5 * r.random()          # px, the reference model's error on this case
            yield Case(id=cid, name=f"{SCENES[i % len(SCENES)]} {i // len(SCENES) + 1}", inputs={"difficulty": difficulty}, gt={"difficulty": difficulty}, tags=["synthetic"])

    def infer(self, model: SyntheticModel, case: Case, rec: TrajectoryRecorder) -> Prediction:
        r = _rng("infer", model.name, model.seed, case.id)
        unstable = r.random() < model.instability
        shape = _resample(SHAPES["late" if unstable and r.random() < 0.6 else "drift" if unstable else "settled"], self.iterations)
        scale = 0.55 + case.inputs["difficulty"] / 6
        for v in shape:
            rec.step([[v * scale * (1 + 0.02 * (r.random() - 0.5))]])
        noise = math.exp(r.gauss(0.0, 0.18))
        error = case.inputs["difficulty"] * model.quality * noise * (1.15 if unstable else 1.0)
        return Prediction(output={"error": round(error, 4)})

    def metric_value(self, pred: Prediction, case: Case) -> Optional[float]:
        return None if case.gt is None else float(pred.output["error"])

    def expected_iterations(self) -> Optional[int]:
        return self.iterations


DEMO_CHECKPOINTS = {
    "synth-current.json": {"name": "synth-v1", "quality": 1.0, "instability": 0.05, "seed": 1},
    "synth-candidate.json": {"name": "synth-v2", "quality": 0.82, "instability": 0.3, "seed": 2},
}
