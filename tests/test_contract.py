"""The stored contract is wider than the engine, and says so.

Rabbit Brain's data model was shaped by one task (optical flow) and one diagnostic (the trajectory
statistics). These tests pin the seams that let a stored run carry depth metrics, latency, named
intermediate series and limits from diagnostics this version does not implement, so a team's runs do
not need migrating when the engine catches up.

The point of every test here is the same: widening the format must never make the engine look like
it checked something it did not.
"""
import pytest
from pydantic import ValidationError

from rabbit_brain.example import example_comparison
from rabbit_brain.models import Bundle, CaseV1, CaseV2, DatasetRef, Limits, Metric, ModelRef
from rabbit_brain.report import report_core


def bundle(**kw):
    base = dict(
        version=2, run_id="r1", project="p", source="imported",
        metric=Metric(id="mean_endpoint_error", name="mean endpoint error", unit="px"),
        baseline=ModelRef(name="a"), candidate=ModelRef(name="b"),
        dataset=DatasetRef(name="d", count=1), limits=Limits(),
        cases=[CaseV2(id="c1", name="c1", baseline_error=1.0, candidate_error=1.2)],
    )
    return Bundle(**{**base, **kw})


# ---- direction ---------------------------------------------------------------------------------

def test_metric_can_express_higher_is_better():
    """Depth delta<1.25, task success and PSNR are higher-is-better. The format must be able to say so."""
    m = Metric(id="delta_1_25", name="delta < 1.25", unit="ratio", lower_is_better=False)
    assert m.lower_is_better is False
    assert m.model_dump()["lower_is_better"] is False


def test_engine_refuses_a_higher_is_better_primary_metric():
    """error_outcome() calls candidate-minus-baseline a regression. Running a higher-is-better metric
    through it would report every improvement as a regression, so the boundary rejects it."""
    m = Metric(id="delta_1_25", name="delta < 1.25", unit="ratio", lower_is_better=False)
    with pytest.raises(ValidationError, match="higher-is-better"):
        bundle(metric=m)


def test_lower_is_better_metric_still_builds():
    assert bundle().metric.lower_is_better is True


# ---- secondary metrics -------------------------------------------------------------------------

def test_case_carries_secondary_metrics_on_both_sides():
    c = CaseV1(id="c1", name="c1", baseline_error=5.4, candidate_error=5.7,
               baseline_metrics={"latency_ms": 28.9}, candidate_metrics={"latency_ms": 25.1})
    assert c.baseline_metrics["latency_ms"] == 28.9
    assert c.candidate_metrics["latency_ms"] == 25.1


def test_secondary_metrics_may_be_negative():
    """A secondary measurement can legitimately be signed (a bias, a delta), unlike the primary error."""
    c = CaseV1(id="c1", name="c1", baseline_error=1.0, candidate_error=1.0,
               candidate_metrics={"depth_bias_cm": -0.4})
    assert c.candidate_metrics["depth_bias_cm"] == -0.4


def test_secondary_metric_keys_are_identifiers():
    with pytest.raises(ValidationError, match="lowercase identifiers"):
        CaseV1(id="c1", name="c1", baseline_error=1.0, candidate_error=1.0,
               candidate_metrics={"Latency (ms)": 1.0})


def test_secondary_metric_rejects_non_finite():
    with pytest.raises(ValidationError, match="finite"):
        CaseV1(id="c1", name="c1", baseline_error=1.0, candidate_error=1.0,
               candidate_metrics={"latency_ms": float("inf")})


def test_bundle_records_secondary_metric_definitions():
    b = bundle(metrics=[Metric(id="latency_ms", name="latency", unit="ms")])
    assert [m.id for m in b.metrics] == ["latency_ms"]


# ---- named series ------------------------------------------------------------------------------

def test_case_carries_named_series_beyond_the_scalar_trajectory():
    c = CaseV1(id="c1", name="c1", baseline_error=1.0, candidate_error=1.0,
               candidate_trajectory=[1.0, 0.5, 0.2],
               candidate_series={"confidence": [0.2, 0.5, 0.9]})
    assert c.candidate_trajectory == [1.0, 0.5, 0.2]      # still the one stability reads
    assert c.candidate_series["confidence"] == [0.2, 0.5, 0.9]


def test_named_series_needs_at_least_two_values():
    with pytest.raises(ValidationError, match="between 2 and 512"):
        CaseV1(id="c1", name="c1", baseline_error=1.0, candidate_error=1.0,
               candidate_series={"confidence": [0.5]})


# ---- unenforced limits -------------------------------------------------------------------------

def test_limits_carry_names_this_version_does_not_enforce():
    lim = Limits(extra={"max_depth_rmse": 0.05, "min_task_success": 0.92})
    assert lim.unenforced_limits() == ["max_depth_rmse", "min_task_success"]


def test_limits_with_nothing_extra_report_nothing_unenforced():
    assert Limits().unenforced_limits() == []


def test_extra_limit_names_are_identifiers():
    with pytest.raises(ValidationError, match="lowercase identifiers"):
        Limits(extra={"Max Depth RMSE": 0.05})


def test_report_says_which_limits_were_not_checked():
    """The whole point of storing an unenforced limit is that nobody mistakes it for a passing one."""
    run = example_comparison()
    lim = Limits(extra={"max_depth_rmse": 0.05})
    md = report_core(run, lim)
    assert "Limits not checked" in md
    assert "max_depth_rmse" in md
    assert "does not account for it" in md


def test_report_stays_quiet_when_every_limit_is_enforced():
    md = report_core(example_comparison(), Limits())
    assert "Limits not checked" not in md


# ---- backward compatibility ---------------------------------------------------------------------

def test_existing_cases_load_with_no_new_fields():
    c = CaseV1(id="c1", name="c1", baseline_error=1.0, candidate_error=2.0)
    assert c.baseline_metrics == {} and c.candidate_metrics == {}
    assert c.baseline_series == {} and c.candidate_series == {}


def test_existing_limits_load_with_no_extra():
    assert Limits(max_regression=0.3).extra == {}


def test_bundle_to_v1_is_unaffected_by_the_new_fields():
    b = bundle(cases=[CaseV2(id="c1", name="c1", baseline_error=1.0, candidate_error=1.2,
                             candidate_metrics={"latency_ms": 9.0})])
    v1 = b.to_v1()
    assert [c.id for c in v1.cases] == ["c1"]
    assert v1.metric == "mean_endpoint_error"
