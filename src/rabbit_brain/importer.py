"""Read a version-1 results file (JSON) or a metrics CSV and validate it, reporting every problem in plain language."""
from __future__ import annotations

import csv
import io
import json
import math
from pathlib import Path
from typing import Any, Optional

from pydantic import ValidationError

from .errors import RBError
from .models import ComparisonV1

MAX_BYTES = 2_000_000

REQUIREMENT: dict[str, str] = {
    "version": "the number 1",
    "project": "a short project name",
    "baseline": "the current model's name",
    "candidate": "the candidate model's name",
    "dataset": "the evaluation set's name",
    "metric": "an error metric name such as mean_endpoint_error",
    "unit": "a short unit such as px or cm",
    "baseline_error": "a nonnegative number",
    "candidate_error": "a nonnegative number",
    "id": "1–80 characters of letters, digits, _ . or -",
    "name": "a short name",
    "baseline_frames": "2–512 nonnegative numbers",
    "candidate_frames": "2–512 nonnegative numbers",
    "baseline_trajectory": "2–64 nonnegative numbers, one per refinement iteration",
    "candidate_trajectory": "2–64 nonnegative numbers, one per refinement iteration",
    "cases": "a list of 1–500 cases",
    "tags": "up to 8 short tags",
    "notes": "at most 1000 characters",
    "source": "\"example\" or \"imported\"",
}

NOT_JSON = "This is not valid JSON. Check for a missing comma or bracket, or start from the minimal example (rb schema example)."


def _describe(err: dict[str, Any], value: Any) -> str:
    loc = list(err.get("loc", ()))
    etype = err.get("type", "")
    msg = err.get("msg", "")
    label = ""
    if loc and loc[0] == "cases" and len(loc) >= 2 and isinstance(loc[1], int):
        cases = value.get("cases") if isinstance(value, dict) else None
        item = cases[loc[1]] if isinstance(cases, list) and loc[1] < len(cases) else None
        cid = item.get("id") if isinstance(item, dict) else None
        label = f"Case {loc[1] + 1} ({cid}): " if isinstance(cid, str) else f"Case {loc[1] + 1}: "
        if len(loc) == 2 and etype != "value_error":
            return f"{label}must be an object with id, name, baseline_error and candidate_error."
    if etype == "value_error":
        text = msg[len("Value error, "):] if msg.startswith("Value error, ") else msg
        return f"{label}{text}"
    field = str(loc[-1]) if loc else ""
    key = str(loc[-2]) if loc and isinstance(loc[-1], int) and len(loc) >= 2 else field
    if not key:
        return "The file must be a JSON object."
    if key == "version":
        return f"{label}version must be the number 1."
    requirement = REQUIREMENT.get(key, msg.lower())
    if etype == "missing":
        return f"{label}{key} is missing: add {requirement}."
    return f"{label}{key} must be {requirement}."


def problems_from(exc: ValidationError, value: Any, cap: int = 10) -> list[str]:
    seen: set[str] = set()
    problems: list[str] = []
    for err in exc.errors():
        text = _describe(err, value)
        if text not in seen:
            seen.add(text)
            problems.append(text)
    shown = problems[:cap]
    if len(problems) > cap:
        shown.append(f"…and {len(problems) - cap} more.")
    return shown


def parse_comparison_text(raw: str) -> ComparisonV1:
    if len(raw.encode("utf-8")) > MAX_BYTES:
        raise RBError("E_IMPORT_TOO_LARGE")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        raise RBError("E_IMPORT_NOT_JSON", problems=[NOT_JSON])
    return parse_comparison_value(value)


def parse_comparison_value(value: Any) -> ComparisonV1:
    if not isinstance(value, dict):
        raise RBError("E_IMPORT_INVALID", problems=["The file must be a JSON object."])
    try:
        parsed = ComparisonV1.model_validate(value)
    except ValidationError as exc:
        raise RBError("E_IMPORT_INVALID", problems=problems_from(exc, value))
    if parsed.source != "example":
        parsed.source = "imported"
    return parsed


def load_comparison(path: Path) -> ComparisonV1:
    if not path.exists():
        raise RBError("E_FILE_NOT_FOUND", message=f"No such file: {path}")
    return parse_comparison_text(path.read_text(encoding="utf-8-sig"))


# ---------------------------------------------------------------- CSV (the v5 kit's convert_csv.py, with CONFIG from flags)

CSV_SERIES = (("baseline_trajectory", 2, 64), ("candidate_trajectory", 2, 64), ("baseline_frames", 2, 512), ("candidate_frames", 2, 512))


def _numbers(text: Optional[str], name: str, low: int, high: int) -> Optional[list[float]]:
    if text is None or not text.strip():
        return None
    values = [float(v) for v in text.split(";") if v.strip()]
    if not low <= len(values) <= high or any(not math.isfinite(n) or n < 0 for n in values):
        raise ValueError(f"{name} must hold {low}-{high} finite nonnegative numbers")
    return values


def csv_to_comparison(path: Path, *, project: str, baseline: str, candidate: str, dataset: str, metric: str, unit: str) -> ComparisonV1:
    if not path.exists():
        raise RBError("E_FILE_NOT_FOUND", message=f"No such file: {path}")
    missing = [k for k, v in {"--project": project, "--baseline-name": baseline, "--candidate-name": candidate, "--dataset": dataset, "--metric": metric, "--unit": unit}.items() if not (v or "").strip()]
    if missing:
        raise RBError("E_IMPORT_CONFIG", message=f"CSV import needs {', '.join(missing)}.")
    cases: list[dict[str, Any]] = []
    try:
        with io.open(path, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            required = {"case_id", "baseline_error", "candidate_error"}
            if not reader.fieldnames or not required.issubset(set(reader.fieldnames)):
                raise ValueError("required columns: case_id, baseline_error, candidate_error")
            for row in reader:
                b, c = float(row["baseline_error"]), float(row["candidate_error"])
                if any(not math.isfinite(n) or n < 0 for n in (b, c)):
                    raise ValueError("errors must be finite and nonnegative")
                case: dict[str, Any] = {
                    "id": row["case_id"], "name": row.get("name") or row["case_id"],
                    "tags": [t.strip() for t in (row.get("tags") or "").split(";") if t.strip()],
                    "baseline_error": b, "candidate_error": c,
                }
                for key, low, high in CSV_SERIES:
                    values = _numbers(row.get(key), key, low, high)
                    if values is not None:
                        case[key] = values
                if ("baseline_frames" in case) != ("candidate_frames" in case) or ("baseline_frames" in case and len(case["baseline_frames"]) != len(case["candidate_frames"])):
                    raise ValueError(f"frame series must be paired with equal lengths for {row['case_id']}")
                if row.get("notes"):
                    case["notes"] = row["notes"]
                cases.append(case)
    except (ValueError, KeyError) as exc:
        raise RBError("E_CSV_INVALID", message=f"The CSV could not be converted: {exc}.")
    if not cases:
        raise RBError("E_CSV_INVALID", message="The CSV contains no cases.")
    value = {"version": 1, "project": project, "baseline": baseline, "candidate": candidate, "dataset": dataset, "metric": metric, "unit": unit, "source": "imported", "cases": cases}
    return parse_comparison_value(value)
