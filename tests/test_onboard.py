"""`rb onboard`: the step that turns a described setup into a configured project, or into the honest shape of one.

Most briefs will not match a built-in adapter. These tests hold that case to the same standard as the supported
one: the generated file must import, name every method rb will call, carry the right task metric, and say what is
still missing. A scaffold that silently returned plausible numbers would be the exact failure adapter agreement exists to catch.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from rabbit_brain.config import load_config
from rabbit_brain.models import SCHEMAS, Metric
from rabbit_brain.onboard import ADAPTER_NAME, BRIEF_NAME, INTEGRATION_NAME, BriefV1, EXAMPLE_BRIEF, is_supported, write_package

RAFT_BRIEF = {
    "version": 1, "project": "flow-release", "task": "flow", "architecture": "RAFT", "framework": "pytorch",
    "model_code": "./raft", "iterations": 12, "device": "cpu",
    "checkpoints": {"current": "./ckpt/raft-things.pth", "candidate": "./ckpt/raft-small.pth"},
    "data": {"path": "./data/kitti2015/training", "kind": "kitti", "labels": "all"},
}


def test_the_brief_is_a_published_schema():
    assert SCHEMAS["brief"] is BriefV1
    assert BriefV1.model_validate(RAFT_BRIEF).architecture == "RAFT"
    assert BriefV1.model_validate(json.loads(json.dumps(EXAMPLE_BRIEF.model_dump(exclude_none=True)))) == EXAMPLE_BRIEF


def test_a_raft_family_brief_gets_the_built_in_adapter_and_no_scaffold(workdir):
    brief = BriefV1.model_validate(RAFT_BRIEF)
    assert is_supported(brief) == "raft"
    result = write_package(brief, workdir)
    assert result["built_in"] is True and result["todos"] == 0
    assert not (workdir / ADAPTER_NAME).exists(), "nothing to write when an adapter already covers it"
    cfg = load_config(workdir / "rb.toml")
    assert cfg.adapter.id == "raft" and cfg.adapter.module is None and cfg.adapter.iterations == 12
    md = (workdir / INTEGRATION_NAME).read_text(encoding="utf-8")
    assert "rb verify-adapter" in md and "rb verify-hook" in md and "rb doctor" in md


def test_an_unsupported_brief_gets_a_scaffold_that_imports_and_names_everything(workdir):
    brief = EXAMPLE_BRIEF   # IGEV-Stereo: no built-in adapter, an evaluator named, some cases unlabeled
    assert is_supported(brief) is None
    result = write_package(brief, workdir)
    assert result["built_in"] is False and result["todos"] == 5
    cfg = load_config(workdir / "rb.toml")
    assert cfg.adapter.module == "rb_adapter:IgevStereoAdapter" and cfg.adapter.id is None
    assert cfg.project.task == "stereo" and cfg.adapter.iterations == 16
    assert cfg.adapter.reference_cases == 5, "the brief named an evaluator, so the agreement check is on"

    sys.path.insert(0, str(workdir))
    try:
        for mod in [m for m in list(sys.modules) if m == "rb_adapter"]:
            del sys.modules[mod]
        import rb_adapter
        cls = rb_adapter.IgevStereoAdapter
    finally:
        sys.path.remove(str(workdir))
    for method in ("describe", "load", "cases", "infer", "metric_value", "expected_iterations", "reference_value", "reference_description"):
        assert callable(getattr(cls, method)), f"the scaffold must name {method}: rb calls it"
    assert cls.task == "stereo" and cls.metric.unit == "px" and cls.metric.id == "mean_disparity_error"
    assert cls.synthetic is False, "a scaffold for a real model must never claim to be a test double"

    body = (workdir / ADAPTER_NAME).read_text(encoding="utf-8")
    assert body.count("TODO") >= 5 and "NotImplementedError" in body
    assert "rec.attached" in body, "the one line of instrumentation has to be shown, not described"
    assert brief.data.layout in body, "what they told us about their layout belongs next to the TODO that needs it"
    assert "some cases have ground truth" in body, "labels: some must reach the cases() docstring"


def test_the_scaffold_and_readme_do_not_claim_support_we_do_not_have(workdir):
    write_package(EXAMPLE_BRIEF, workdir)
    md = (workdir / INTEGRATION_NAME).read_text(encoding="utf-8")
    assert "We do not have a built-in adapter for IGEV-Stereo" in md
    assert "not going to pretend" in md
    assert "0.001 relative" in md, "the agreement tolerance is the claim that makes the rest trustworthy"
    assert "generic starting" in md and "borderline" in md, "the limits caveat travels with the package"
    assert "share.json" in md and "no case ids" in md, "the way back is opt-in and says what it contains"
    assert "neurips" not in md.lower() and "endpoint sufficiency" not in md.lower()


def test_a_custom_metric_survives_the_round_trip(workdir):
    """Config accepted a metric that rb.toml never wrote back, so a team's own unit was silently replaced
    by the task default. The brief is the first thing that can carry one, so this pins it."""
    brief = BriefV1.model_validate({**RAFT_BRIEF, "architecture": "our own UNet plus GRU", "task": "depth",
                                    "metric": {"id": "rmse_cm", "name": "depth RMSE", "unit": "cm"}})
    write_package(brief, workdir)
    cfg = load_config(workdir / "rb.toml")
    assert cfg.metric == Metric(id="rmse_cm", name="depth RMSE", unit="cm")
    assert 'unit = "cm"' in (workdir / "rb.toml").read_text(encoding="utf-8")


def test_the_brief_you_passed_is_not_a_conflict_with_itself(workdir):
    (workdir / BRIEF_NAME).write_text(json.dumps(RAFT_BRIEF), encoding="utf-8")
    result = write_package(BriefV1.model_validate(RAFT_BRIEF), workdir, source=workdir / BRIEF_NAME)
    assert BRIEF_NAME not in result["files"], "it is already there; rewriting it is not a service"
    assert (workdir / "rb.toml").exists()


def test_existing_files_are_not_overwritten_without_force(workdir):
    write_package(EXAMPLE_BRIEF, workdir)
    (workdir / ADAPTER_NAME).write_text("# an hour of someone's work\n", encoding="utf-8")
    with pytest.raises(Exception) as exc:
        write_package(EXAMPLE_BRIEF, workdir)
    assert "overwritten" in str(exc.value)
    assert (workdir / ADAPTER_NAME).read_text(encoding="utf-8").startswith("# an hour")
    write_package(EXAMPLE_BRIEF, workdir, force=True)
    assert "TODO" in (workdir / ADAPTER_NAME).read_text(encoding="utf-8")


def test_doctor_reads_the_generated_project_and_names_what_is_left(workdir):
    """The real acceptance test: a stranger runs the two commands in INTEGRATION.md and gets a checklist,
    not a traceback. The adapter must load even though every TODO is unwritten."""
    brief = BriefV1.model_validate({**RAFT_BRIEF, "architecture": "IGEV-Stereo", "task": "stereo", "model_code": "./igev", "device": "cpu"})
    write_package(brief, workdir)
    (workdir / "igev").mkdir()
    env = {**__import__("os").environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")}
    r = subprocess.run([sys.executable, "-m", "rabbit_brain.cli", "doctor", "--json"], cwd=workdir, capture_output=True, text=True, env=env)
    data = json.loads(r.stdout)["data"]
    checks = {c["check"]: c for c in data["checks"]}
    assert checks["config"]["status"] == "ok"
    assert checks["adapter"]["status"] == "ok", "an unwritten adapter must still import: that is what makes the checklist usable"
    assert checks["dataset"]["status"] == "fail", "and the things genuinely missing are named"
    assert any("verify-hook" in (c.get("fix") or "") for c in data["checks"])
