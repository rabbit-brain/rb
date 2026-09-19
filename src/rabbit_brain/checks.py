"""Saved checks: absolute limits per case id for one project, kept in the repo (`checks.json`) and run against the next candidate."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Optional, Union

from pydantic import ValidationError

from .errors import RBError
from .fmt import pct, to_fixed
from .models import Bundle, CheckResult, ChecksV1, ChecksV2, CheckV1, CheckV2, ComparisonV1
from .stability import EPS, trajectory_stats

DEFAULT_CHECKS_PATH = Path("checks.json")


def load_checks(path: Path, project: Optional[str] = None, strict: bool = True) -> ChecksV2:
    """Read a version-1 (workspace / kit) or version-2 checks file. Returns version 2, filtered to `project` when given.

    A file for another project raises E_CHECKS_PROJECT_MISMATCH when `strict`, otherwise returns no checks (the caller may warn)."""
    if not path.exists():
        return ChecksV2(version=2, project=project or "", checks=[])
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError:
        raise RBError("E_CHECKS_INVALID", message=f"{path} is not valid JSON.")
    if not isinstance(raw, dict):
        raise RBError("E_CHECKS_INVALID")
    try:
        if raw.get("version") == 2:
            v2 = ChecksV2.model_validate(raw)
            if project and v2.checks and v2.project != project:
                if strict:
                    raise RBError("E_CHECKS_PROJECT_MISMATCH", message=f"{path} holds checks for '{v2.project}', not '{project}'.")
                return ChecksV2(version=2, project=project, checks=[])
            return v2
        if raw.get("version") == 1:
            v1 = ChecksV1.model_validate(raw)
            checks = [c for c in v1.checks if project is None or c.project == project]
            if project and v1.checks and not checks:
                if strict:
                    raise RBError("E_CHECKS_PROJECT_MISMATCH", message=f"No saved checks in {path} match project '{project}'.")
                return ChecksV2(version=2, project=project, checks=[])
            proj = project or (checks[0].project if checks else "")
            return ChecksV2(version=2, project=proj, checks=[CheckV2(case_id=c.id, name=c.name, max_error=c.max_error, max_late_share=c.max_late_share) for c in checks])
    except ValidationError as exc:
        raise RBError("E_CHECKS_INVALID", message=f"{path}: {exc.errors()[0].get('msg', 'invalid')}")
    raise RBError("E_CHECKS_INVALID", message=f"{path}: expected version 1 or 2.")


def save_checks(path: Path, checks: ChecksV2) -> None:
    try:
        path.write_text(json.dumps(checks.model_dump(exclude_none=True), indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        raise RBError("E_WRITE_FAILED", message=f"Could not write {path}: {exc}")


def evaluate_check(check: CheckV2, comparison: Union[ComparisonV1, Bundle], unit: str) -> CheckResult:
    """Same semantics as the workspace's checkResult() and the kit's check.py."""
    found = next((c for c in comparison.cases if c.id == check.case_id), None)
    base = dict(case_id=check.case_id, max_error=check.max_error, max_late_share=check.max_late_share, max_reversals=check.max_reversals)
    if found is None:
        return CheckResult(status="missing", reason="Case not present in this comparison.", **base)
    base["candidate_error"] = found.candidate_error
    if found.candidate_error is None:
        return CheckResult(status="failing", reason="Candidate error was not measured (no ground truth for this case).", **base)
    if found.candidate_error > check.max_error + EPS:
        return CheckResult(status="failing", reason=f"Candidate error {to_fixed(found.candidate_error)} {unit} exceeds the {to_fixed(check.max_error)} {unit} limit.", **base)
    if check.max_late_share is not None or check.max_reversals is not None:
        if not found.candidate_trajectory:
            return CheckResult(status="failing", reason="This check requires a settled trajectory, but none was exported for the candidate.", **base)
        stats = trajectory_stats(found.candidate_trajectory)
        if check.max_late_share is not None and stats.late_share > check.max_late_share + EPS:
            return CheckResult(status="failing", reason=f"Late revision {pct(stats.late_share)} exceeds the {pct(check.max_late_share)} limit.", **base)
        if check.max_reversals is not None and stats.reversals > check.max_reversals:
            return CheckResult(status="failing", reason=f"Reversals {stats.reversals} exceed the {check.max_reversals} limit.", **base)
    return CheckResult(status="passing", reason="Within limits.", **base)


def evaluate_all(checks: ChecksV2, comparison: Union[ComparisonV1, Bundle], unit: str, case_id: Optional[str] = None) -> list[CheckResult]:
    selected = [c for c in checks.checks if case_id is None or c.case_id == case_id]
    return [evaluate_check(c, comparison, unit) for c in selected]


def upsert_check(checks: ChecksV2, new: CheckV2) -> ChecksV2:
    others = [c for c in checks.checks if c.case_id != new.case_id]
    return ChecksV2(version=2, project=checks.project, checks=[*others, new])


def default_check(bundle: Bundle, case_id: str, *, max_error: Optional[float], max_late_share: Optional[float], max_reversals: Optional[int], require_settled: bool, note: str) -> CheckV2:
    case = next((c for c in bundle.cases if c.id == case_id), None)
    if case is None:
        raise RBError("E_CASE_NOT_FOUND", message=f"Case '{case_id}' is not in run {bundle.run_id}.")
    if max_error is None:
        # the best level either model reached on this case, plus the regression tolerance: a case that improved keeps its
        # improvement, a case that regressed must come back to the current model's level
        best = min(v for v in (case.baseline_error, case.candidate_error) if v is not None)
        max_error = float(to_fixed(best + bundle.limits.max_regression, 2))
    if max_late_share is None and require_settled and case.candidate_trajectory:
        max_late_share = bundle.limits.max_late_share
    return CheckV2(case_id=case.id, name=case.name, max_error=max_error, max_late_share=max_late_share, max_reversals=max_reversals, from_run=bundle.run_id,
                   created=date.today().isoformat(), unit=bundle.metric.unit, note=note or "")


def describe_check(check: CheckV2, unit: str) -> str:
    parts = [f"candidate error ≤ {to_fixed(check.max_error)} {unit}"]
    if check.max_late_share is not None:
        parts.append(f"late revision ≤ {pct(check.max_late_share)}")
    if check.max_reversals is not None:
        parts.append(f"reversals ≤ {check.max_reversals}")
    return ", ".join(parts)
