"""The illustrative example: a warehouse robot's optical-flow model, v2.3 -> v2.4.
Synthetic numbers shaped to show the workflow. Not measured results. Identical to the workspace's example data.
"""
from __future__ import annotations

from .fmt import to_fixed
from .models import CaseV1, ComparisonV1

# id, name, tags, baseline_error, candidate_error, baseline shape, candidate shape, notes
_SPECS = [
    ("aisle-042", "Reflective floor", ["warehouse", "reflection"], 3.4, 5.2, "settled", "late", "The candidate's error rises mid-sequence and its refinement never settles: most of its revisions arrive late. Investigate before release."),
    ("loading-018", "Loading-bay transition", ["low light", "exposure"], 2.6, 4.0, "settled", "settled", "Error regressed, but the candidate converged cleanly. It is confidently worse here: a data or training gap, not instability."),
    ("crossing-007", "Fast crossing motion", ["occlusion", "motion"], 3.8, 4.7, "late", "late", "Both models keep revising this case late. The candidate adds error on top. Consider it a known hard case and keep a check on it."),
    ("aisle-011", "Long aisle", ["warehouse", "texture"], 2.9, 1.9, "settled", "drift", "Error improved, yet the candidate's updates grow again through its final iterations. The better number is not a settled answer."),
    ("turn-026", "Tight corner", ["rotation"], 3.2, 2.1, "settled", "late", "Error improved, but the candidate re-opened its estimate late in the loop. Treat as unreliable until it holds on more sequences."),
    ("pallet-003", "Pallet approach", ["close range"], 4.3, 2.9, "settled", "settled", None),
    ("aisle-024", "Open aisle", ["warehouse"], 2.7, 1.7, "settled", "settled", None),
    ("loading-021", "Ramp descent", ["slope"], 3.6, 2.3, "settled", "settled", None),
    ("static-006", "Stopped at station", ["stationary"], 1.7, 1.1, "settled", "settled", None),
    ("turn-012", "Wide turn", ["rotation"], 2.8, 1.9, "settled", "settled", None),
    ("crossing-019", "Passing equipment", ["occlusion"], 4.2, 2.9, "settled", "settled", None),
    ("aisle-031", "Daylight aisle", ["daylight"], 2.3, 1.6, "settled", "settled", None),
]

_FRAME_REGRESSION = [.58, .63, .67, .70, .82, 1.06, 1.46, 1.75, 1.68, 1.57, 1.32, 1.04, .89, .80, .69, .64]
_FRAME_FLAT = [.86, .90, .95, 1.06, 1.08, .98, 1.14, 1.16, 1.03, .94, 1.06, 1.01, .94, 1.04, .96, .89]
_SHAPES = {
    "settled": [2.40, 1.52, .96, .63, .42, .29, .21, .15, .11, .08, .06, .05],
    "late": [2.30, 1.42, .90, .60, .43, .36, .31, .55, .84, .92, .78, .66],
    "drift": [2.20, 1.30, .80, .55, .45, .42, .41, .44, .50, .58, .64, .70],
}


def _r3(v: float) -> float:
    return float(to_fixed(v, 3))


def _frame_series(mean: float, regression: bool) -> list[float]:
    shape = _FRAME_REGRESSION if regression else _FRAME_FLAT
    scale = mean / (sum(shape) / len(shape))
    return [_r3(v * scale) for v in shape]


def _trajectory(kind: str, error: float) -> list[float]:
    scale = 0.55 + error / 6
    return [_r3(v * scale) for v in _SHAPES[kind]]


def example_comparison() -> ComparisonV1:
    cases = []
    for i, (cid, name, tags, b, c, base_shape, cand_shape, notes) in enumerate(_SPECS):
        cases.append(CaseV1(
            id=cid, name=name, tags=list(tags), baseline_error=b, candidate_error=c,
            baseline_frames=_frame_series(b, False), candidate_frames=_frame_series(c, i < 3),
            baseline_trajectory=_trajectory(base_shape, b), candidate_trajectory=_trajectory(cand_shape, c),
            notes=notes,
        ))
    return ComparisonV1(
        version=1, project="Warehouse perception", baseline="flow-v2.3", candidate="flow-v2.4",
        dataset="Warehouse regression set", metric="mean_endpoint_error", unit="px", source="example", cases=cases,
    )


MINIMAL_EXAMPLE = """{
  "version": 1,
  "project": "My perception project",
  "baseline": "model-v1", "candidate": "model-v2",
  "dataset": "Regression set", "metric": "mean_endpoint_error", "unit": "px",
  "cases": [
    { "id": "seq-001", "name": "First sequence", "baseline_error": 2.1, "candidate_error": 2.4,
      "candidate_trajectory": [2.3, 1.4, 0.9, 0.6, 0.4, 0.3, 0.2, 0.15, 0.1, 0.08, 0.06, 0.05] }
  ]
}"""
