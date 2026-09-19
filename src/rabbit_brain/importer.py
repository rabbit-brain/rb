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
    """A series cell: numbers separated by ';' (or by spaces, or by ',' inside a quoted cell)."""
    if text is None or not text.strip():
        return None
    sep = ";" if ";" in text else ("," if "," in text else None)
    parts = [v for v in (text.split(sep) if sep else text.split()) if v.strip()]
    try:
        values = [float(v) for v in parts]
    except ValueError:
        raise ValueError(f"{name}: '{text[:40]}{'...' if len(text) > 40 else ''}' is not a series of numbers (separate values with ';')")
    if not low <= len(values) <= high or any(not math.isfinite(n) or n < 0 for n in values):
        raise ValueError(f"{name} must hold {low}-{high} finite nonnegative numbers (found {len(values)})")
    return values


CSV_COLUMNS = ("case_id", "baseline_error", "candidate_error", "name", "tags", "baseline_trajectory", "candidate_trajectory", "baseline_frames", "candidate_frames", "notes")

CSV_EXAMPLE = """case_id,name,baseline_error,candidate_error,baseline_trajectory,candidate_trajectory,tags
000012_10,000012_10,2.10,2.55,4.1;2.0;1.1;0.6;0.4;0.3;0.2;0.15;0.1;0.08;0.06;0.05,4.3;2.2;1.3;0.9;0.7;0.5;0.4;0.35;0.3;0.28;0.25;0.24,kitti;night
000013_10,000013_10,1.40,1.05,3.9;1.8;0.9;0.5;0.3;0.2;0.15;0.1;0.08;0.06;0.05;0.04,3.6;1.7;0.8;0.4;0.25;0.15;0.1;0.07;0.05;0.04;0.03;0.03,kitti
"""


def parse_column_map(spec: Optional[str]) -> dict[str, str]:
    """`--columns case_id=frame,baseline_error=epe_a,...`: rb column -> the file's column."""
    mapping: dict[str, str] = {}
    for item in (spec or "").split(","):
        item = item.strip()
        if not item:
            continue
        if "=" not in item:
            raise RBError("E_CSV_INVALID", message=f"--columns entries look like rb_column=file_column; got '{item}'.")
        ours, theirs = (x.strip() for x in item.split("=", 1))
        if ours not in CSV_COLUMNS:
            raise RBError("E_CSV_INVALID", message=f"--columns: '{ours}' is not an rb column. Columns: {', '.join(CSV_COLUMNS)}.")
        mapping[ours] = theirs
    return mapping


def csv_to_comparison(path: Path, *, project: str, baseline: str, candidate: str, dataset: str, metric: str, unit: str, columns: Optional[dict[str, str]] = None) -> ComparisonV1:
    if not path.exists():
        raise RBError("E_FILE_NOT_FOUND", message=f"No such file: {path}")
    missing = [k for k, v in {"--project": project, "--baseline-name": baseline, "--candidate-name": candidate, "--dataset": dataset, "--metric": metric, "--unit": unit}.items() if not (v or "").strip()]
    if missing:
        raise RBError("E_IMPORT_CONFIG", message=f"CSV import needs {', '.join(missing)}.")
    columns = columns or {}
    col = lambda name: columns.get(name, name)  # noqa: E731
    cases: list[dict[str, Any]] = []
    row_no = 1
    try:
        with io.open(path, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            found = list(reader.fieldnames or [])
            required = ["case_id", "baseline_error", "candidate_error"]
            absent = [f"{r} (as '{col(r)}')" if col(r) != r else r for r in required if col(r) not in found]
            if absent:
                raise ValueError(f"required columns not found: {', '.join(absent)}; the file has: {', '.join(found) or 'no header'}. Map your names with --columns, e.g. --columns case_id=frame,baseline_error=epe_current,candidate_error=epe_candidate")
            for row in reader:
                row_no += 1
                cid = row[col("case_id")]
                try:
                    b, c = float(row[col("baseline_error")]), float(row[col("candidate_error")])
                except ValueError:
                    raise ValueError(f"row {row_no} ({cid}): baseline_error and candidate_error must be numbers")
                if any(not math.isfinite(n) or n < 0 for n in (b, c)):
                    raise ValueError(f"row {row_no} ({cid}): errors must be finite and nonnegative")
                case: dict[str, Any] = {
                    "id": cid, "name": row.get(col("name")) or cid,
                    "tags": [t.strip() for t in (row.get(col("tags")) or "").split(";") if t.strip()],
                    "baseline_error": b, "candidate_error": c,
                }
                for key, low, high in CSV_SERIES:
                    try:
                        values = _numbers(row.get(col(key)), key, low, high)
                    except ValueError as exc:
                        raise ValueError(f"row {row_no} ({cid}), column {col(key)}: {exc}")
                    if values is not None:
                        case[key] = values
                if ("baseline_frames" in case) != ("candidate_frames" in case) or ("baseline_frames" in case and len(case["baseline_frames"]) != len(case["candidate_frames"])):
                    raise ValueError(f"row {row_no} ({cid}): frame series must be paired with equal lengths")
                if row.get(col("notes")):
                    case["notes"] = row[col("notes")]
                cases.append(case)
    except (ValueError, KeyError) as exc:
        raise RBError("E_CSV_INVALID", message=f"The CSV could not be converted: {exc}.")
    if not cases:
        raise RBError("E_CSV_INVALID", message="The CSV contains no cases.")
    value = {"version": 1, "project": project, "baseline": baseline, "candidate": candidate, "dataset": dataset, "metric": metric, "unit": unit, "source": "imported", "cases": cases}
    return parse_comparison_value(value)
