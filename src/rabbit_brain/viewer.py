"""The local HTML report: the same viewer the website uses, written to a file you can open.

`rb report <run> --open` writes `report.html` next to `report.md`. It is one self-contained document:
the viewer's script and stylesheet are inlined, and the comparison travels with it as a JSON block.
That is not tidiness, it is a requirement. The file is opened from disk, and on `file://` a fetch of
a sibling file is blocked, so anything the page needs must already be in it.

Evidence images are referenced by relative path by default, because `rb` already writes them beside
the report and a run with a dozen flagged cases would be tens of megabytes inlined. `--embed` turns
them into data URIs for a single file you can attach to a pull request.

Nothing here reaches the network, and no part of the run is uploaded anywhere.
"""
from __future__ import annotations

import base64
import html as html_escaping
import json
import mimetypes
from pathlib import Path
from typing import Optional

from .models import Bundle, ChecksV2, Limits

TEMPLATE = Path(__file__).resolve().parent / "assets" / "report-template.html"

# The panels `render_case` writes, in the order a reader should meet them, with what each one shows.
# A file that is not there is skipped rather than rendered as a broken image.
SHEETS: tuple[tuple[str, str, str], ...] = (
    ("inputs.png", "Inputs, and where the models disagree",
     "The two inputs, then where the candidate disagrees with the current checkpoint. Disagreement needs no ground truth, so this panel is computable on unlabelled cases."),
    ("error.png", "Error against ground truth",
     "Each model's error and the change between them. Red is where the candidate got worse."),
    ("flow.png", "Both predictions, and the labels",
     "Both model outputs and the ground truth at one scale."),
    ("filmstrip.png", "Every iteration",
     "Each refinement iteration for both models. The last quarter is framed, because that is the window the stability limits are measured over."),
    ("trajectory.png", "Update size per iteration",
     "How much each model was still moving its answer, iteration by iteration, against the limit in force."),
)


def comparison_json(bundle: Bundle) -> dict:
    """The bundle in the shape the viewer parses.

    Errors travel at full precision. Rounding them on the way in would move cases across the limit:
    a candidate at 1.300001 against a 0.3 limit is a regression in the markdown report, and would be
    called stable here if it arrived as 1.3. The viewer formats for display; it must not be handed
    numbers that have already been formatted."""
    return {
        "version": 1,
        "project": bundle.project,
        "baseline": bundle.baseline.name,
        "candidate": bundle.candidate.name,
        "dataset": bundle.dataset.name,
        "metric": bundle.metric.id,
        "unit": bundle.metric.unit,
        "source": "imported",
        "cases": [
            {
                "id": c.id,
                "name": c.name,
                "tags": list(c.tags or []),
                "baseline_error": c.baseline_error,
                "candidate_error": c.candidate_error,
                **({"baseline_trajectory": list(c.baseline_trajectory)} if c.baseline_trajectory else {}),
                **({"candidate_trajectory": list(c.candidate_trajectory)} if c.candidate_trajectory else {}),
            }
            for c in bundle.cases
            if c.baseline_error is not None and c.candidate_error is not None
        ],
    }


def limits_json(limits: Limits) -> dict:
    """The limits in the viewer's names. Every limit this engine enforces is here; a viewer given
    fewer would compute a different verdict than the report.md written from the same bundle.

    The names differ because the two surfaces were written separately, and renaming either now would
    break files already on disk. `tests/test_conformance.py` holds them to the same arithmetic."""
    out = {"threshold": limits.max_regression, "lateShare": limits.max_late_share, "reversals": limits.max_reversals}
    if limits.max_last_update is not None:
        out["lastUpdate"] = limits.max_last_update
    if limits.max_trajectory_regression is not None:
        out["trajectoryRegression"] = limits.max_trajectory_regression
    return out


def checks_json(checks: Optional[ChecksV2]) -> list[dict]:
    """Saved checks in the viewer's shape, so the report's checks tab says what report.md says.
    `project` travels with each one: that is how the viewer marks a check saved for another project
    instead of quietly passing it."""
    if checks is None:
        return []
    out = []
    for c in checks.checks:
        item = {"id": c.case_id, "name": c.name or c.case_id, "project": checks.project, "max_error": c.max_error}
        if c.max_late_share is not None:
            item["max_late_share"] = c.max_late_share
        if c.max_reversals is not None:
            item["max_reversals"] = c.max_reversals
        out.append(item)
    return out


def notice_for(limits: Limits) -> str:
    """What the run's policy carries that this version does not check. The markdown report says this;
    a report that gets forwarded has to say it too, or it reads as a clean bill the run never gave."""
    names = limits.unenforced_limits()
    if not names:
        return ""
    them = "them" if len(names) > 1 else "it"
    return (f"This run's limits also carry {', '.join(names)}, which this version records but does not enforce. "
            f"Nothing here tests {them}, and the verdict does not account for {them}.")


def _data_uri(path: Path) -> Optional[str]:
    kind, _ = mimetypes.guess_type(path.name)
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    return f"data:{kind or 'image/png'};base64," + base64.b64encode(raw).decode("ascii")


def evidence_map(run_dir: Optional[Path], embed: bool = False) -> dict:
    """Rendered sheets by case id, for whatever `rb case --render` has already written."""
    if run_dir is None:
        return {}
    root = run_dir / "evidence"
    if not root.is_dir():
        return {}
    out: dict[str, list[dict]] = {}
    for case_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        sheets = []
        for filename, label, note in SHEETS:
            f = case_dir / filename
            if not f.is_file():
                continue
            src = _data_uri(f) if embed else f"evidence/{case_dir.name}/{filename}"
            if src is None:
                continue
            sheets.append({"src": src, "label": label, "alt": f"{label} for case {case_dir.name}", "note": note})
        if sheets:
            out[case_dir.name] = sheets
    return out


def build(bundle: Bundle, run_dir: Optional[Path] = None, embed: bool = False, checks: Optional[ChecksV2] = None) -> str:
    """The finished document. Raises FileNotFoundError when the package was built without the
    viewer, which is a packaging fault rather than anything the user did."""
    if not TEMPLATE.is_file():
        raise FileNotFoundError(
            "This build of rabbit-brain does not include the report viewer. "
            "Reinstall from a released wheel, or run `rb report` without --open for the markdown receipt."
        )
    payload = {"run": comparison_json(bundle), "limits": limits_json(bundle.limits), "checks": checks_json(checks),
               "notice": notice_for(bundle.limits), "evidence": evidence_map(run_dir, embed)}
    # A literal </script> inside the block would end it early and break the document.
    data = json.dumps(payload, separators=(",", ":")).replace("</script", "<\\/script")
    title = f"{bundle.project}: {bundle.baseline.name} against {bundle.candidate.name}"
    return (TEMPLATE.read_text(encoding="utf-8")
            .replace("__RB_TITLE__", html_escaping.escape(title, quote=False))
            .replace("__RB_DATA__", data))
