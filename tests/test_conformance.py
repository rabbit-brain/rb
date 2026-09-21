"""The website's arithmetic and this package's, held to the same answers.

`rb report --open` writes an HTML report that computes its own verdict from the numbers rb inlines
into it, using lib/comparison.ts from the site repository. The markdown report beside it comes from
stability.py here. Two implementations, one bundle, and a reader who will believe whichever they
opened. This test runs both over the same fixtures and fails on any difference: a verdict, a count,
a per-case label, the order of the queue, or a saved check's status or wording.

The TypeScript side is `tests/conformance/comparison.ts`, a byte-for-byte copy of the website's
`lib/comparison.ts`, tied to the viewer this package ships by a hash that `tests/test_viewer.py`
checks. Set RB_WEB to a checkout of the site repository to run against its working copy instead.

All it needs beyond that is node and `zod` resolvable from the repository; CI installs zod. Without
either the test skips and says so, which is the one state worth noticing: a machine that cannot run
this is a machine where the two implementations are not being compared.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rabbit_brain import viewer
from rabbit_brain.checks import evaluate_all
from rabbit_brain.models import Bundle, ChecksV2, Limits
from rabbit_brain.runs import compute_findings
from rabbit_brain.stability import (SummaryNumbers, error_outcome, is_flagged, priority, rank,
                                    stability_outcome, verdict_text)
from tests.conformance.corpus import fixtures

HERE = Path(__file__).resolve().parent
TOLERANCE = 1e-9

# rb calls a case with no candidate trajectory "not_assessed"; the viewer calls it "unknown".
# Same state, two vocabularies, and renaming either would break files already written.
STABILITY = {"not_assessed": "unknown", "settled": "settled", "unstable": "unstable"}


VENDORED = HERE / "conformance" / "comparison.ts"


def comparison_ts() -> Path | None:
    """The vendored copy, which is the code this package ships, unless RB_WEB names a site checkout.

    It used to search for a site checkout beside the repository and prefer it. That made the test
    depend on what happened to sit next to it, and on a machine with a site checkout it compared rb
    against a working copy rather than against the viewer in the wheel. RB_WEB is the way to ask for
    that, and it is deliberate when it happens."""
    env = os.environ.get("RB_WEB")
    if env:
        live = Path(env) / "lib" / "comparison.ts"
        if live.is_file():
            return live
    return VENDORED if VENDORED.is_file() else None


def node_command(node: str, lib: Path, script: Path, payloads: Path) -> list[list[str]]:
    """The ways to ask node to run TypeScript, best first.

    Node strips types from a `.ts` file on its own from 22.18 and 23.6; before that the same thing is
    behind --experimental-strip-types, and before 22.6 it cannot do it at all. Trying in order costs
    one failed process on old versions and keeps the test from depending on which node is installed."""
    base = [str(script), str(lib), str(payloads)]
    return [[node, *base], [node, "--experimental-strip-types", *base]]


def python_answer(name: str, bundle: Bundle, checks: ChecksV2 | None) -> dict:
    limits: Limits = bundle.limits
    cases = bundle.cases
    s = SummaryNumbers(cases, limits)
    v = verdict_text(cases, limits)
    results = evaluate_all(checks, bundle, bundle.metric.unit) if checks else []
    # The assembled line, checks clause and all, straight from the code that writes report.md.
    found = compute_findings(bundle, limits, checks)
    return {
        "name": name,
        "verdict": {"ready": v["ready"], "status": found.verdict.status,
                    "line": found.verdict.line, "start": v["start"]},
        "summary": {
            "baseline": s.baseline, "candidate": s.candidate, "change": s.change,
            "regressions": s.regressions, "improved": s.improved, "unstable": s.unstable,
            "unstablePassing": s.unstable_passing, "withTrajectories": s.with_trajectories, "flagged": s.flagged,
        },
        "cases": {c.id: {
            "error": error_outcome(c, limits.max_regression),
            "stability": STABILITY[stability_outcome(c, limits)],
            "flagged": is_flagged(c, limits),
            "priority": priority(c, limits),
        } for c in cases},
        "order": [c.id for c in rank(cases, limits)],
        "checks": [{"id": r.case_id, "status": r.status, "reason": r.reason} for r in results],
    }


def node_answers(lib: Path, payloads: list[dict], tmp_path: Path) -> list[dict]:
    path = tmp_path / "payloads.json"
    path.write_text(json.dumps(payloads), encoding="utf-8")
    node = shutil.which("node") or "node"
    script = HERE / "conformance" / "answer.mjs"
    proc = None
    for command in node_command(node, lib, script, path):
        proc = subprocess.run(command, capture_output=True, text=True, timeout=180)
        if proc.returncode == 0:
            return json.loads(proc.stdout)
        # A missing dependency or a real disagreement is the same on every node; only the TypeScript
        # loading is worth another attempt.
        if "ERR_UNKNOWN_FILE_EXTENSION" not in proc.stderr:
            break
    # Two ways this machine simply cannot run the TypeScript side. Both are reported as skips, with
    # the fix, rather than as a failure of the thing under test. CI installs both and fails on a skip,
    # so the guard still runs somewhere on every push.
    version = subprocess.run([node, "--version"], capture_output=True, text=True).stdout.strip()
    if "Cannot find package 'zod'" in proc.stderr:
        pytest.skip("zod is not installed, so the TypeScript side cannot run; `npm install zod@3.25.76` in this repository")
    if "ERR_UNKNOWN_FILE_EXTENSION" in proc.stderr:
        pytest.skip(f"node {version} cannot run TypeScript; 22.18 or later, or 23.6 or later, can")
    pytest.fail(f"the TypeScript side failed to run under node {version}:\n{proc.stderr.strip()}")


def differences(py: dict, ts: dict, path: str = "") -> list[str]:
    """Every place the two answers disagree, named well enough to fix without rerunning."""
    out: list[str] = []
    if isinstance(py, dict) and isinstance(ts, dict):
        for key in sorted(set(py) | set(ts)):
            if key not in py:
                out.append(f"{path}.{key}: only the viewer has it ({ts[key]!r})")
            elif key not in ts:
                out.append(f"{path}.{key}: only rb has it ({py[key]!r})")
            else:
                out += differences(py[key], ts[key], f"{path}.{key}")
        return out
    if isinstance(py, list) and isinstance(ts, list):
        if len(py) != len(ts):
            return [f"{path}: rb has {len(py)}, the viewer has {len(ts)}"]
        for i, (a, b) in enumerate(zip(py, ts)):
            out += differences(a, b, f"{path}[{i}]")
        return out
    if isinstance(py, bool) or isinstance(ts, bool):
        return [] if py is ts else [f"{path}: rb says {py!r}, the viewer says {ts!r}"]
    if isinstance(py, (int, float)) and isinstance(ts, (int, float)):
        if abs(py - ts) > TOLERANCE:
            return [f"{path}: rb says {py!r}, the viewer says {ts!r}"]
        return []
    return [] if py == ts else [f"{path}: rb says {py!r}, the viewer says {ts!r}"]


@pytest.fixture(scope="module")
def answers(tmp_path_factory):
    lib = comparison_ts()
    if lib is None:
        pytest.skip("no comparison.ts to test against; set RB_WEB to a checkout of the site repository")
    if shutil.which("node") is None:
        pytest.skip("node is not installed; it runs the TypeScript side")
    corpus = fixtures()
    payloads = [{"name": f["name"], "payload": {
        "run": viewer.comparison_json(f["bundle"]),
        "limits": viewer.limits_json(f["bundle"].limits),
        "checks": viewer.checks_json(f["checks"]),
    }} for f in corpus]
    ts = {a["name"]: a for a in node_answers(lib, payloads, tmp_path_factory.mktemp("conformance"))}
    py = {f["name"]: python_answer(f["name"], f["bundle"], f["checks"]) for f in corpus}
    return py, ts


@pytest.mark.parametrize("name", [f["name"] for f in fixtures()])
def test_the_two_implementations_agree(answers, name):
    py, ts = answers
    assert name in ts, f"the viewer produced no answer for {name}"
    found = differences(py[name], ts[name], name)
    assert not found, "rb and the report viewer disagree:\n  " + "\n  ".join(found)


def test_the_corpus_covers_what_it_claims_to():
    """A conformance test that only ever sees settled, passing cases proves nothing."""
    seen = {"regression": 0, "improved": 0, "stable": 0, "unstable": 0, "settled": 0, "not_assessed": 0, "flagged": 0}
    statuses = set()
    for f in fixtures():
        limits = f["bundle"].limits
        for c in f["bundle"].cases:
            seen[error_outcome(c, limits.max_regression)] = seen.get(error_outcome(c, limits.max_regression), 0) + 1
            seen[stability_outcome(c, limits)] = seen.get(stability_outcome(c, limits), 0) + 1
            seen["flagged"] += int(is_flagged(c, limits))
        if f["checks"]:
            statuses |= {r.status for r in evaluate_all(f["checks"], f["bundle"], f["bundle"].metric.unit)}
    missing = [k for k, v in seen.items() if v == 0]
    assert not missing, f"no fixture produces: {', '.join(missing)}"
    assert {"passing", "failing", "missing"} <= statuses, f"saved-check statuses covered: {sorted(statuses)}"


def test_checks_reach_the_viewer_with_the_project_they_were_saved_for():
    """rb scopes a checks file to the run's project before it gets this far, so the viewer never has
    to judge a foreign check in a generated report. It judges per check anyway, because the hosted
    workspace holds checks for several projects at once, and that decision needs the project name."""
    from rabbit_brain.models import CheckV2

    checks = ChecksV2(version=2, project="alpha", checks=[CheckV2(case_id="one", name="One", max_error=1.0)])
    assert [c["project"] for c in viewer.checks_json(checks)] == ["alpha"]
    assert viewer.checks_json(None) == []
