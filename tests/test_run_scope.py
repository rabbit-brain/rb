"""Explicit slices and fixed design cells must not acquire invented replications."""
import json
import shlex
import shutil
import subprocess

import pytest

import rabbit_brain
from rabbit_brain.errors import RBError
from rabbit_brain.models import Envelope
from rbtest import Mcp, as_the_user, main, rbyaml


@pytest.fixture
def record(workdir, monkeypatch, capsys):
    as_the_user(monkeypatch)
    monkeypatch.setenv("RB_ACTOR", "human:designer")

    def cli(*args, error=None):
        code = main([*args, "--json"])
        answer = Envelope.model_validate_json(capsys.readouterr().out)
        if error:
            assert code == 2 and not answer.ok, (args, code, answer)
            assert answer.errors[0].code == error, answer.errors
        else:
            assert code == 0 and answer.ok, (args, code, answer.errors)
        return answer.data

    cli.root = workdir
    cli("init", "Synthetic design and repeated samples", "--no-agents")
    cli("experiment", "add", "A fixed grid", "--id", "e", "--baseline", "old", "--candidate", "new")
    cli("metric", "add", "error", "--minimize")
    for name in ("width", "loops", "seed"):
        cli("spec", "set", "e", name, "--per-run")
    return cli


def criterion(record, id="c", *flags):
    return record("criterion", "add", id, "-e", "e", "--metric", "error", "--subject", "new",
                  "--reference", "old", "--at-most", "0", "--id", id, *flags)["object"]


def run(record, width, seed, new, old=10, loops=6):
    return record("run", "add", "e", f"new.error={new}", f"old.error={old}",
                  "--set", f"width={width}", "--set", f"loops={loops}", "--set", f"seed={seed}")


def finding(record, id="c"):
    return record("show", id)["finding"]


def bytes_written(root):
    rb = root / ".rb"
    return {p.relative_to(rb).as_posix(): p.read_bytes() for p in [*rb.rglob("*.yaml"), rb / "log.jsonl"]
            if "cache" not in p.relative_to(rb).parts}


def test_shared_selector_reads_only_its_exact_slice_and_leaves_all_recorded_runs(record):
    criterion(record, "c", "--where", "width=512")
    criterion(record, "empty", "--where", "width=1024")
    for seed, score in enumerate((8, 9, 7), 1):
        run(record, 512, seed, score)
        run(record, 768, seed, 30 + seed)
    before = bytes_written(record.root)
    f = finding(record)
    assert f["comparison"]["estimate"] == -2
    assert f["comparison"]["n"] == 3 and f["comparison"]["lo"] is not None
    assert len(f["observations"]) == 3
    assert all(o["per_run"]["width"] == 512 for o in f["observations"])
    assert "where width=512" in f["rule"]
    assert finding(record, "empty")["rule_outcome"] == "untested"
    assert bytes_written(record.root) == before


def test_same_variant_can_compare_disjoint_setting_values_without_pairing_them_with_themselves(record):
    record("criterion", "add", "loop comparison", "-e", "e", "--metric", "error", "--subject", "new",
           "--reference", "new", "--where", "width=512", "--subject-where", "loops=8",
           "--reference-where", "loops=6", "--at-most", "0", "--id", "c")
    for seed, (a, b) in enumerate(((6, 10), (8, 11), (7, 12)), 1):
        run(record, 512, seed, a, loops=8)
        run(record, 512, seed, b, loops=6)
        run(record, 768, seed, 100, loops=8)
    f = finding(record)
    assert f["comparison"]["estimate"] == -4
    assert f["comparison"]["n"] == 3
    assert f["comparison"]["hi"] < 0
    assert len(f["observations"]) == 6
    assert all("loops=" not in o["unit"] for o in f["observations"])


def test_the_setting_that_separates_the_arms_is_never_offered_or_taken_as_the_unit(record, capsys):
    """Each arm would be one unit, with nothing to pair or replicate: the unit hint never suggests a setting the
    criterion selects by, and spec set refuses it as the unit, as criterion add refuses the same combination."""
    record("criterion", "add", "loop comparison", "-e", "e", "--metric", "error", "--subject", "new",
           "--reference", "new", "--where", "width=512", "--subject-where", "loops=8",
           "--reference-where", "loops=6", "--at-most", "0", "--id", "c")
    for seed, (a, b) in enumerate(((6, 10), (8, 11), (7, 12)), 1):
        run(record, 512, seed, a, loops=8)
        run(record, 512, seed, b, loops=6)
    assert not any(c["code"] == "unit_hint" for c in finding(record)["caveats"])
    before = bytes_written(record.root)
    record("spec", "set", "e", "population.unit", "loops", "--no-verify", error="E_OBJECT_INVALID")
    with pytest.raises(RBError) as refused:
        rabbit_brain.open(record.root).experiment("e").spec.set("population.unit", "loops", verify=False)
    assert refused.value.code == "E_OBJECT_INVALID" and "criterion c reads loops=8 against loops=6" in str(refused.value)
    assert bytes_written(record.root) == before
    record("freeze", "e", "--why", "before more runs")
    assert main(["spec", "set", "e", "population.unit", "loops", "--no-verify", "--amend", "--why", "try", "--json"]) == 2
    [error] = Envelope.model_validate_json(capsys.readouterr().out).errors
    assert error.fix.endswith('rb spec set e population.unit <setting> --amend --why "...".')
    record("spec", "set", "e", "population.unit", "seed", "--no-verify", "--amend", "--why", "a seed is a unit within each arm")
    assert settings(record.root)["population.unit"]["value"] == "seed"


@pytest.mark.skipif(shutil.which("git") is None, reason="the two halves meet in a git merge")
@pytest.mark.parametrize("rule", [[], ["--min-n", "3"], ["--bound"]], ids=["estimate", "min-n", "bound"])
def test_a_merge_that_brings_a_unit_to_the_arms_it_separates_reads_as_an_integrity_problem(record, capsys, monkeypatch, tmp_path_factory, rule):
    """Writes refuse a unit that separates a criterion's arms, but two branches can each make half of it: one declares
    the criterion and records its runs, the other the unit. The merge reads as rb's own, and the finding names the
    problem, with the agent given the repair, which clears it. No run can add a unit while each arm is one, so rb status
    asks for none, whatever the rule needs (more units, or an interval)."""
    config = tmp_path_factory.mktemp("gitconfig") / "config"
    config.write_text("[user]\n\temail = t@example.com\n\tname = t\n[init]\n\tdefaultBranch = main\n", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")

    def git(*args):
        subprocess.run(["git", *args], cwd=record.root, check=True, capture_output=True)

    git("init", "-q")
    git("add", "-A")
    git("commit", "-qm", "the experiment")
    git("switch", "-qc", "loops")
    record("criterion", "add", "loop comparison", "-e", "e", "--metric", "error", "--subject", "new",
           "--reference", "new", "--where", "width=512", "--subject-where", "loops=8",
           "--reference-where", "loops=6", "--at-most", "0", "--id", "c", *rule)
    for seed, (a, b) in enumerate(((6, 10), (8, 11), (7, 12)), 1):
        run(record, 512, seed, a, loops=8)
        run(record, 512, seed, b, loops=6)
    git("add", "-A")
    git("commit", "-qm", "a criterion that separates its arms by loops, and its runs")
    git("switch", "-q", "main")
    record("spec", "set", "e", "population.unit", "loops", "--no-verify")
    git("add", "-A")
    git("commit", "-qm", "loops as the unit")
    git("merge", "-q", "--no-edit", "loops")
    assert record("doctor")["outcome"]["passed"]
    [problem] = [p for p in finding(record)["integrity"] if p["code"] == "unit_separates_arms"]
    assert problem["text"] == "population.unit is loops, the setting that separates its two arms (loops=8 against loops=6), so each arm is one unit"
    assert finding(record)["rule_outcome"] == ("meets" if not rule else "undetermined")
    status = record("status")
    [item] = [i for i in status["open"] if i["subject"] == "c"]
    assert (item["code"], item["who"]) == ("unit_separates_arms", "agent") and item["do"].startswith("rb spec set e population.unit <setting>")
    assert status["gate"]["integrity"] and not [i for i in status["open"] if i["code"] in ("edited_outside_rb", "merged_outside_rb")]
    assert main(["status", "--fail-on", "integrity", "--json"]) == 1
    capsys.readouterr()
    record("spec", "set", "e", "population.unit", "seed", "--no-verify")
    assert not [p for p in finding(record)["integrity"] if p["code"] == "unit_separates_arms"]
    assert main(["status", "--fail-on", "integrity", "--json"]) == 0
    capsys.readouterr()


def test_fixed_cells_have_descriptive_coverage_not_a_sampling_interval(record):
    record("spec", "set", "e", "width", "--design-factor")
    criterion(record, "c", "--each")
    criterion(record, "minimum", "--min-n", "3")
    for width, score in ((256, 8), (512, 9), (768, 11)):
        run(record, width, 1, score)
    f = finding(record)
    r = f["comparison"]
    assert r["n"] == 3 and r["unit"] == "design cell"
    assert r["lo"] is None and r["hi"] is None
    assert r["design"]["min"] == -2 and r["design"]["max"] == 1
    assert r["design"]["meets"] == 2
    assert f["missed_units"] == ["loops=6 · seed=1 · width=768"]  # the unit within its cell, as its observation names it
    assert f["result"] == "point_only" and f["rule_outcome"] == "misses"
    assert "2 of 3 cells" in f["says"]["units"]
    assert finding(record, "minimum")["rule_outcome"] == "undetermined"
    assert not any(c["code"] == "unit_hint" and "width" in c["text"] for c in f["caveats"])


def test_fixed_slice_retains_seed_interval_and_unbalanced_replicates_do_not_reweight_cells(record):
    record("spec", "set", "e", "width", "--design-factor")
    criterion(record)
    criterion(record, "slice", "--where", "width=512", "--min-n", "3")
    for seed, score in enumerate((8, 9, 7), 1):
        run(record, 512, seed, score)
    run(record, 768, 1, 14)
    aggregate = finding(record)["comparison"]
    assert aggregate["estimate"] == 1  # equal weight for means -2 and +4, not four samples
    assert aggregate["n"] == 2 and aggregate["lo"] is None
    within = finding(record, "slice")["comparison"]
    assert within["n"] == 3 and within["estimate"] == -2 and within["lo"] is not None
    assert within["design"] == {}
    assert within["unit"] != "fixed design cell"


def test_declaring_design_after_evidence_requires_amendment_without_historicizing_raw_runs(record):
    criterion(record)
    record("freeze", "e", "--why", "before observing")
    for width, score in ((256, 8), (512, 9), (768, 7)):
        run(record, width, 1, score)
    old = finding(record)
    assert old["comparison"]["lo"] is not None
    before = bytes_written(record.root)
    record("spec", "set", "e", "width", "--design-factor", error="E_FROZEN")
    assert bytes_written(record.root) == before
    record("spec", "set", "e", "width", "--design-factor", "--amend", "--why", "widths are fixed design cells")
    new = finding(record)
    assert new["comparison"]["lo"] is None and new["comparison"]["design"]
    assert new["units"]["runs"] == old["units"]["runs"] == 3
    assert all(o["counted"] and not o["reason"] for o in new["observations"])
    [declared] = [c["text"] for c in new["caveats"] if c["code"] == "design_factor"]
    assert declared.startswith("width declared a fixed design factor at ") and "human:designer" not in declared


@pytest.mark.parametrize("flags", [
    ("--where", "undeclared=1"),
    ("--where", "width=null"),
    ("--where", "width=[512]"),
    ("--where", "width=512", "--subject-where", "width=768"),
    ("--where", "width=512", "--estimand", "pooled"),
    ("--where", "width=true", "--subject-where", "width=1"),
    ("--where", "width=1", "--subject-where", "width=1.0"),
])
def test_invalid_selector_is_atomic(record, flags):
    before = bytes_written(record.root)
    record("criterion", "add", "invalid", "-e", "e", "--metric", "new.error", "--at-most", "0", *flags,
           error="E_OBJECT_INVALID")
    assert bytes_written(record.root) == before


def test_same_variant_overlapping_selectors_are_refused(record):
    before = bytes_written(record.root)
    record("criterion", "add", "overlap", "-e", "e", "--metric", "error", "--subject", "new", "--reference", "new",
           "--subject-where", "width=512", "--reference-where", "loops=6", "--at-most", "0", error="E_OBJECT_INVALID")
    assert bytes_written(record.root) == before


def test_scoped_or_design_reading_cannot_be_overridden_by_unsliced_analysis(record):
    criterion(record, "slice", "--where", "width=512")
    criterion(record)
    for width, score in ((256, 8), (512, 9), (768, 7)):
        run(record, width, 1, score)
    record("analysis", "add", "e", "--compare", "new/old:error=99[98,100]", "--method", "reported interval",
           "--unit", "seed", "--units", "30", "--id", "a")
    assert finding(record, "slice")["comparison"]["estimate"] == -1
    assert finding(record)["comparison"]["source"] == "a"
    record("spec", "set", "e", "width", "--design-factor")
    f = finding(record)
    assert f["comparison"]["source"] == "runs" and f["comparison"]["lo"] is None
    assert any(c["code"] == "analysis_scope" for c in f["caveats"])


def test_sdk_and_mcp_keep_selector_types_and_design_declaration(record):
    state = rabbit_brain.open(record.root)
    exp = state.experiment("e")
    setting = exp.spec.set("width", design_factor=True)
    assert setting.design_factor and setting.per_run
    c = exp.criterion("SDK slice", metric="new.error", at_most=10, where={"width": 512}, id="sdk")
    assert c.data.where == {"width": 512}
    client = Mcp()
    try:
        response = client.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "criterion_add", "arguments": {
            "statement": "MCP slice", "experiment": "e", "metric": "new.error", "at_most": 10, "where": ["width=768"], "id": "mcp"}}})
        assert not response["result"].get("isError"), response
    finally:
        assert client.close() == 0
    assert record("show", "mcp")["object"]["where"] == {"width": 768}
    assert record("show", "mcp")["object"]["via"] == "mcp"


def test_incomplete_design_cell_does_not_turn_complete_cells_into_an_all_cells_claim(record):
    record("spec", "set", "e", "width", "--design-factor")
    criterion(record, "c", "--each")
    run(record, 512, 1, 8)
    record("run", "add", "e", "new.error=9", "--set", "width=768", "--set", "loops=6", "--set", "seed=1")
    f = finding(record)
    assert f["comparison"]["design"]["count"] == 1
    assert f["comparison"]["design"]["incomplete"] == ["width=768"]
    assert f["rule_outcome"] == "undetermined"


def test_repeat_does_not_add_a_replication_or_reweight_a_design_cell(record):
    record("spec", "set", "e", "width", "--design-factor")
    criterion(record)
    run(record, 512, 1, 8)
    first = finding(record)["observations"][0]["run"]
    run(record, 768, 1, 14)
    before = finding(record)["comparison"]
    record("run", "add", "e", "new.error=8", "old.error=10", "--set", "width=512", "--set", "loops=6", "--set", "seed=1",
           "--repeats", first)
    after = finding(record)
    assert after["comparison"]["estimate"] == before["estimate"] == 1
    assert after["comparison"]["n"] == 2 and after["units"]["repeats"] == 1
    assert after["comparison"]["design"]["cells"][0]["n"] == 1


def arm(record, width, seed, **values):
    """One run of one arm (or of the difference, change=...), every per-run setting given."""
    numbers = [f"{'change.new' if k == 'change' else k}.error={v}" for k, v in values.items()]
    return record("run", "add", "e", *numbers, "--set", f"width={width}", "--set", "loops=6", "--set", f"seed={seed}")["object"]["id"]


def test_a_declared_unit_the_design_cells_share_is_counted_within_each_cell(record):
    """seed=2 at width=256 is not seed=2 at width=512: a run is left out, kept, counted and dated by the units of its
    own cell, and what rests on it says so."""
    record("spec", "set", "e", "population.unit", "seed", "--no-verify")
    record("spec", "set", "e", "width", "--design-factor")
    arm(record, 256, 1, new=8, old=10)
    lone = arm(record, 256, 2, new=9)
    arm(record, 512, 2, new=9, old=10)
    arm(record, 512, 3, new=7, old=10)
    criterion(record)
    f = finding(record)
    seen = {o["run"]: o for o in f["observations"]}
    assert seen[lone]["unit"] == "width=256 · seed=2" and not seen[lone]["counted"]
    assert "has no old run" in seen[lone]["reason"]
    assert f["units"]["runs"] == 3 and f["comparison"]["n"] == 2
    assert "all 3 units in 2 design cells were produced before it was declared" in f["says"]["timing"]
    # rb compare counts the same units, a row per width: seed=2 once in each
    table = {(row["variant"], row["where"]["width"]): row["metrics"]["error"] for row in record("compare", "e")["rows"]}
    assert table[("new", 256)]["n"] == 2 and table[("new", 256)]["mean"] == 8.5 and table[("new", 512)]["n"] == 2
    assert table[("old", 256)]["n"] == 1 and table[("old", 512)]["n"] == 2
    assert record("retract", lone, "--why", "an arm with no reference")["rested_on"] == ["criterion c reads it in its timing"]


def test_a_declared_unit_that_is_one_of_the_design_factors_is_the_cell_it_is_in(record):
    """width=256 at loops=6 and width=256 at loops=8 are two cells, so two units, when width is the declared unit."""
    record("spec", "set", "e", "population.unit", "width", "--no-verify")
    for name in ("width", "loops"):
        record("spec", "set", "e", name, "--design-factor")
    for width in (256, 512):
        for loops in (6, 8):
            run(record, width, 1, 9 if loops == 6 else 11, loops=loops)
    criterion(record, "c", "--each")
    f = finding(record)
    assert {o["unit"] for o in f["observations"]} == {f"width={w} · loops={l}" for w in (256, 512) for l in (6, 8)}
    assert "all 4 units in 4 design cells were produced before it was declared" in f["says"]["timing"]
    assert "2 of 4 units, in 4 design cells, are above 0" in f["says"]["rule"]
    # the unit is no condition: a row per loops, over its two width cells
    table = {(row["variant"], row["where"]["loops"]): row["metrics"]["error"] for row in record("compare", "e")["rows"]}
    assert sorted(table) == [("new", 6), ("new", 8), ("old", 6), ("old", 8)] and all(c["n"] == 2 for c in table.values())


def test_two_arms_of_one_cell_are_not_left_out_for_a_difference_recorded_in_another_cell(record):
    record("spec", "set", "e", "population.unit", "seed", "--no-verify")
    record("spec", "set", "e", "width", "--design-factor")
    criterion(record)
    new = arm(record, 256, 1, new=8)
    arm(record, 256, 1, old=10)
    arm(record, 512, 1, change=-1)
    f = finding(record)
    assert all(o["counted"] for o in f["observations"])
    assert [c["estimate"] for c in f["comparison"]["design"]["cells"]] == [-2, -1]
    assert f["units"]["runs"] == 3
    assert "criterion c counts it" in record("retract", new, "--why", "the run was misconfigured")["rested_on"]


def test_a_design_reading_says_its_units_and_its_range_as_rb_says_numbers(record):
    record("spec", "set", "e", "width", "--design-factor")
    for width in (256, 512, 768):
        for seed in (1, 2, 3):
            run(record, width, seed, 10 - 1 / (seed + width / 256), old=10)
    criterion(record)
    f = finding(record)
    assert "all 9 units in 3 design cells were produced before it was declared" in f["says"]["timing"]
    lo, hi = f["comparison"]["design"]["min"], f["comparison"]["design"]["max"]
    assert f"descriptive range {lo:+.6g} to {hi:+.6g};" in f["says"]["units"]  # as the cells' table says their estimates
    assert repr(lo) not in f["says"]["units"]


def test_the_unit_hint_counts_a_settings_values_within_each_design_cell(record):
    """A declared unit is counted within each cell, so the hint is too: three seeds in each of three cells name nine
    units either way, and declaring seed the unit would change nothing; two seeds across four units in a cell would."""
    record("spec", "set", "e", "width", "--design-factor")
    for width in (256, 512, 768):
        for seed in (1, 2, 3):
            run(record, width, seed, 9, old=10)
    criterion(record)
    assert not [c for c in finding(record)["caveats"] if c["code"] == "unit_hint"]
    for width in (256, 512):
        for seed in (4, 5):
            run(record, width, seed, 9, old=10, loops=8)
    [hint] = [c["text"] for c in finding(record)["caveats"] if c["code"] == "unit_hint"]
    assert hint.startswith("loops takes 2 values across the 5 units in design cells width=256 and 1 more: ")
    assert "declare it the unit (rb spec set e population.unit loops)" in hint


@pytest.mark.parametrize("cells,rule,said", [
    ((12345.6, 12349.1), "12347", "12345.6 to 12349.1"),     # either side of a large threshold, never 1.235e+04
    ((3846.55, 3846.64), "3846.60", "3846.55 to 3846.64"),   # either side of a threshold the estimate needs 7 digits beside
    ((12345.61, 12345.64), "0", "12345.61 to 12345.64"),     # two ends that differ never read alike
])
def test_a_design_range_keeps_each_end_on_its_side_of_the_rule_and_never_reads_coarser_than_the_estimate(record, cells, rule, said):
    record("spec", "set", "e", "width", "--design-factor")
    record("criterion", "add", "c", "-e", "e", "--metric", "new.error", "--at-most", rule, "--id", "c")
    for width, value in zip((256, 512), cells):
        record("run", "add", "e", f"new.error={value}", "--set", f"width={width}", "--set", "loops=6", "--set", "seed=1")
    f = finding(record)
    assert f"descriptive range {said};" in f["says"]["units"]
    # never coarser than the estimate is said: each end is as close to its cell as the estimate's digits would put it
    estimate = f["says"]["comparison"].split(": ", 2)[-1].split(" ")[0]
    digits = len(estimate.lstrip("+-").split("e")[0].replace(".", "").lstrip("0"))
    for end, value in zip(said.split(" to "), cells):
        assert abs(float(end) - value) <= abs(float(f"{value:.{digits}g}") - value)


def test_an_each_rule_across_design_cells_judges_every_unit_within_its_cell(record):
    """Every cell's mean meets the threshold while one unit in each cell misses it: the rule is not met, and no cell
    meets it, before and after the cells share a declared unit."""
    record("spec", "set", "e", "width", "--design-factor")
    for width in (256, 512):
        for seed, new in ((1, 9), (2, 10), (3, 11)):
            run(record, width, seed, new, old=10)
    criterion(record, "c", "--each")
    record("criterion", "add", "met", "-e", "e", "--metric", "error", "--subject", "new", "--reference", "old",
           "--at-most", "1", "--each", "--id", "met")
    f = finding(record)
    assert f["rule_outcome"] == "misses"
    assert f["missed_units"] == ["loops=6 · seed=3 · width=256", "loops=6 · seed=3 · width=512"]
    assert "criterion not met: 2 of 6 units, in 2 design cells, are above 0 (loops=6, seed=3, width=256; " in f["says"]["rule"]
    assert f["comparison"]["design"]["meets"] == 0 and "0 of 2 cells meet the threshold" in f["says"]["units"]
    met = finding(record, "met")
    assert met["rule_outcome"] == "meets" and "all 6 units in 2 design cells ≤ 1" in met["says"]["rule"]
    assert "both cells meet the threshold" in met["says"]["units"] and "2 of 2" not in met["says"]["units"], met["says"]["units"]
    assert met["comparison"]["design"]["meets"] == 2
    record("spec", "set", "e", "population.unit", "seed", "--no-verify")
    f = finding(record)
    assert f["rule_outcome"] == "misses" and f["missed_units"] == ["width=256 · seed=3", "width=512 · seed=3"]
    assert "2 of 6 units, in 2 design cells, are above 0" in f["says"]["rule"]


def test_design_interpretation_changes_decision_basis_even_when_point_numbers_do_not_change(record):
    criterion(record)
    run(record, 512, 1, 8)
    run(record, 768, 1, 8)
    record("decide", "c", "accept", "--why", "this point comparison is sufficient for now")
    before = finding(record)
    assert before["review"]["current"]
    record("spec", "set", "e", "width", "--design-factor")
    after = finding(record)
    for field in ("estimate", "lo", "hi", "n"):
        assert before["comparison"][field] == after["comparison"][field]
    assert not after["review"]["current"]


def settings(root):
    return {x["name"]: x for x in rbyaml.loads((root / ".rb/experiments/e/experiment.yaml").read_text(encoding="utf-8"))["settings"]}


def last_change(root):
    return json.loads((root / ".rb/log.jsonl").read_text(encoding="utf-8").splitlines()[-1])["detail"]["change"]


def test_a_sample_factor_clears_a_designation_and_declares_nothing_else(record):
    record("freeze", "e", "--why", "before observing")
    record("spec", "set", "e", "seed", "--sample-factor")
    assert "design_factor" not in settings(record.root)["seed"] and settings(record.root)["seed"]["per_run"]
    assert last_change(record.root) == "setting seed: unchanged"
    record("spec", "set", "e", "width", "--design-factor", "--amend", "--why", "widths are fixed design cells")
    assert settings(record.root)["width"]["design_factor"] is True and last_change(record.root) == "setting width: now a fixed design factor"
    record("spec", "set", "e", "width", "--sample-factor", "--amend", "--why", "widths are sampled after all")
    assert "design_factor" not in settings(record.root)["width"]
    assert last_change(record.root) == "setting width: no longer a fixed design factor"
    record("spec", "set", "e", "width", "--per-run")
    assert "design_factor" not in settings(record.root)["width"] and last_change(record.root) == "setting width: unchanged"


def test_a_stray_false_designation_reads_as_none_adopts_either_way_and_goes_at_the_next_write(record):
    path = record.root / ".rb/experiments/e/experiment.yaml"

    def mark(value):
        doc = rbyaml.loads(path.read_text(encoding="utf-8"))
        seed = next(s for s in doc["settings"] if s["name"] == "seed")
        if value is None:
            seed.pop("design_factor")
        else:
            seed["design_factor"] = value
        path.write_text(rbyaml.dumps(doc), encoding="utf-8")

    mark(False)
    record("doctor", "--adopt", "--why", "an earlier build wrote this for --sample-factor")
    assert settings(record.root)["seed"]["design_factor"] is False
    assert record("show", "e/seed")["object"].get("design_factor") is None
    mark(None)
    record("doctor", "--adopt", "--why", "the line declared nothing")
    mark(False)
    record("doctor", "--adopt", "--why", "an earlier build wrote this for --sample-factor")
    record("spec", "set", "e", "lr", "0.1")
    assert "design_factor" not in settings(record.root)["seed"]


def test_sdk_and_mcp_clear_a_designation_the_way_the_cli_does(record):
    exp = rabbit_brain.open(record.root).experiment("e")
    assert exp.spec.set("seed", per_run=True, design_factor=False).design_factor is None
    assert "design_factor" not in settings(record.root)["seed"] and last_change(record.root) == "setting seed: unchanged"
    assert exp.spec.set("width", design_factor=True).design_factor is True
    assert exp.spec.set("width", design_factor=False).design_factor is None
    assert "design_factor" not in settings(record.root)["width"]
    client = Mcp()
    try:
        for flag, change in (("design_factor", "now a fixed design factor"), ("sample_factor", "no longer a fixed design factor")):
            response = client.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "spec_set", "arguments": {
                "experiment": "e", "name": "loops", flag: True}}})
            assert not response["result"].get("isError"), response
            assert last_change(record.root) == f"setting loops: {change}"
    finally:
        assert client.close() == 0
    assert "design_factor" not in settings(record.root)["loops"]


def test_a_design_factor_never_turns_a_setting_with_one_value_into_a_per_run_one(record, capsys):
    record("spec", "set", "e", "lr", "0.1")
    record("spec", "set", "e", "depth", "--unknown")
    record("criterion", "add", "c", "-e", "e", "--metric", "error", "--subject", "new", "--reference", "old", "--at-most", "0", "--id", "c")
    for seed in (1, 2, 3):
        run(record, 512, seed, 9)
    before, reading = bytes_written(record.root), finding(record)
    for name, has in (("lr", "(0.1)"), ("depth", "(not yet known)")):
        assert main(["spec", "set", "e", name, "--design-factor", "--why", "a fixed choice", "--json"]) == 2
        [error] = Envelope.model_validate_json(capsys.readouterr().out).errors
        assert error.code == "E_OBJECT_INVALID" and f"{name} has one value for every run {has}" in error.message
        assert f"rb spec set e {name} --per-run, then rb spec set e {name} --design-factor" in error.fix
    exp = rabbit_brain.open(record.root).experiment("e")
    with pytest.raises(RBError) as refused:
        exp.spec.set("lr", design_factor=True)
    assert refused.value.code == "E_OBJECT_INVALID"
    client = Mcp()
    try:
        response = client.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "spec_set", "arguments": {
            "experiment": "e", "name": "lr", "design_factor": True}}})
        assert response["result"]["isError"] and response["result"]["structuredContent"]["errors"][0]["code"] == "E_OBJECT_INVALID"
    finally:
        assert client.close() == 0
    assert bytes_written(record.root) == before and finding(record) == reading
    assert exp.spec.set("batch", design_factor=True).per_run
    record("spec", "set", "e", "lr", "--per-run", "--design-factor", "--why", "each run now gives its own")
    assert settings(record.root)["lr"]["design_factor"] is True and "value" not in settings(record.root)["lr"]


def test_on_a_frozen_experiment_a_refusal_gives_commands_that_carry_the_amendment(record, capsys):
    """A command a refusal gives must run as given: on a frozen experiment, one that changes the spec carries --amend
    and its reason."""
    record("spec", "set", "e", "lr", "0.1")
    criterion(record, "c")
    record("freeze", "e", "--why", "before the runs")

    def refused(*argv):
        assert main([*argv, "--json"]) == 2
        [error] = Envelope.model_validate_json(capsys.readouterr().out).errors
        assert error.code == "E_OBJECT_INVALID", error
        return error

    def run_as_given(command):
        argv = shlex.split(command.replace('"..."', '"the reason it changes"'))
        assert argv[0] == "rb"
        record(*argv[1:])

    error = refused("spec", "set", "e", "lr", "--design-factor", "--amend", "--why", "a fixed choice")
    for command in ('rb spec set e lr --per-run --amend --why "..."', 'rb spec set e lr --design-factor --amend --why "..."'):
        assert command in error.fix, error.fix
        run_as_given(command)
    assert settings(record.root)["lr"]["design_factor"] is True
    record("spec", "set", "e", "depth", "12", "--amend", "--why", "the released depth")
    error = refused("criterion", "add", "d", "-e", "e", "--metric", "error", "--subject", "new", "--reference", "old",
                    "--at-most", "0", "--id", "d", "--where", "depth=12", "--amend", "--why", "one slice")
    assert 'rb spec set e depth --per-run --amend --why "..."' in error.message, error.message
    run_as_given('rb spec set e depth --per-run --amend --why "..."')
    record("spec", "set", "e", "batch", "64", "--amend", "--why", "the released batch")
    error = refused("run", "add", "e", "new.error=8", "old.error=10", "--set", "width=512", "--set", "loops=6", "--set", "seed=1",
                    "--set", "depth=12", "--set", "lr=0.1", "--set", "batch=64")
    assert 'rb spec set e batch --per-run --amend --why "..."' in error.message, error.message
    run_as_given('rb spec set e batch --per-run --amend --why "..."')


def test_a_settings_reason_is_kept_on_its_log_line_from_cli_sdk_and_mcp(record):
    def last():
        return json.loads((record.root / ".rb/log.jsonl").read_text(encoding="utf-8").splitlines()[-1])

    record("spec", "set", "e", "batch", "--design-factor", "--why", "batches here are a fixed grid", "--words", "treat batches as fixed")
    assert (last()["detail"]["why"], last()["words"]) == ("batches here are a fixed grid", "treat batches as fixed")
    rabbit_brain.open(record.root).experiment("e").spec.set("lr", 0.1, why="the value the paper used")
    assert last()["detail"]["why"] == "the value the paper used" and last()["via"] == "sdk"
    client = Mcp()
    try:
        response = client.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "spec_set", "arguments": {
            "experiment": "e", "name": "depth", "value": 12, "why": "the depth of the released model"}}})
        assert not response["result"].get("isError"), response
    finally:
        assert client.close() == 0
    assert last()["detail"]["why"] == "the depth of the released model" and last()["via"] == "mcp"
    record("spec", "set", "e", "lr", "0.2")
    assert "why" not in last()["detail"]


@pytest.mark.parametrize("via", ["cli", "sdk", "mcp"])
def test_the_reason_given_to_spec_vary_variant_add_and_run_add_is_kept_on_its_log_line(record, via):
    def last():
        return json.loads((record.root / ".rb/log.jsonl").read_text(encoding="utf-8").splitlines()[-1])

    def sets(values):
        return [token for k, v in values.items() for token in ("--set", f"{k}={v}")]

    per_run = {"width": 512, "loops": 6, "seed": 1}
    if via == "cli":
        record("spec", "vary", "e", "lr", "--why", "lr differs on purpose")
        assert (last()["op"], last()["detail"]["why"]) == ("spec_vary", "lr differs on purpose")
        record("variant", "add", "e", "mid", "--role", "candidate", "--why", "a third arm")
        assert (last()["op"], last()["detail"]["why"]) == ("add_variant", "a third arm")
        record("run", "add", "e", "new.error=8", "old.error=10", *sets(per_run), "--why", "the first seed")
        assert (last()["op"], last()["detail"]["why"]) == ("record", "the first seed")
        record("spec", "vary", "e", "depth")
        assert "why" not in last()["detail"]
        record("variant", "add", "e", "low", "--role", "ablation")
        assert "why" not in last()["detail"]
        record("run", "add", "e", "new.error=7", "old.error=10", *sets({**per_run, "seed": 2}))
        assert "why" not in last()["detail"]
    elif via == "sdk":
        exp = rabbit_brain.open(record.root).experiment("e")
        exp.spec.vary("lr", why="lr differs on purpose")
        assert (last()["op"], last()["detail"]["why"], last()["via"]) == ("spec_vary", "lr differs on purpose", "sdk")
        exp.add_variant("mid", role="candidate", why="a third arm")
        assert (last()["op"], last()["detail"]["why"]) == ("add_variant", "a third arm")
        exp.record({"new.error": 8, "old.error": 10}, per_run=per_run, why="the first seed")
        assert (last()["op"], last()["detail"]["why"]) == ("record", "the first seed")
        exp.spec.vary("depth")
        assert "why" not in last()["detail"]
    else:
        client = Mcp()
        try:
            for n, (name, arguments) in enumerate([
                ("spec_vary", {"experiment": "e", "names": ["lr"], "why": "lr differs on purpose"}),
                ("variant_add", {"experiment": "e", "name": "mid", "role": "candidate", "why": "a third arm"}),
                ("run_add", {"experiment": "e", "metrics": {"new.error": 8, "old.error": 10}, "set": per_run, "why": "the first seed"}),
            ]):
                response = client.handle({"jsonrpc": "2.0", "id": n, "method": "tools/call", "params": {"name": name, "arguments": arguments}})
                assert not response["result"].get("isError"), response
                assert (last()["detail"]["why"], last()["via"]) == (arguments["why"], "mcp"), name
        finally:
            assert client.close() == 0


def test_adoption_cannot_backdate_design_interpretation_to_the_original_setting_author(record):
    path = record.root / ".rb/experiments/e/experiment.yaml"
    doc = rbyaml.loads(path.read_text(encoding="utf-8"))
    next(s for s in doc["settings"] if s["name"] == "width")["design_factor"] = True
    path.write_text(rbyaml.dumps(doc), encoding="utf-8")
    before = bytes_written(record.root)
    record("doctor", "--adopt", "--why", "pretend the earlier author declared design cells", error="E_ADOPT_AUTHORITY")
    assert bytes_written(record.root) == before


def test_edited_run_cannot_hide_from_a_selector_by_changing_its_per_run_value(record):
    criterion(record, "c", "--where", "width=512")
    run(record, 512, 1, 8)
    id = finding(record)["observations"][0]["run"]
    path = record.root / f".rb/experiments/e/runs/{id}.yaml"
    doc = rbyaml.loads(path.read_text(encoding="utf-8"))
    doc["per_run"]["width"] = 768
    path.write_text(rbyaml.dumps(doc), encoding="utf-8")
    f = finding(record)
    assert any(c["code"] == "edited_outside_rb" and c["subject"] == id for c in f["integrity"])


def test_adoption_cannot_backdate_a_changed_typed_selector(record):
    criterion(record, "c", "--where", "width=true")
    path = record.root / ".rb/experiments/e/criteria/c.yaml"
    doc = rbyaml.loads(path.read_text(encoding="utf-8"))
    doc["where"]["width"] = 1
    path.write_text(rbyaml.dumps(doc), encoding="utf-8")
    before = bytes_written(record.root)
    record("doctor", "--adopt", "--why", "pretend this earlier criterion selected a number", error="E_ADOPT_AUTHORITY")
    assert bytes_written(record.root) == before


@pytest.mark.parametrize("name", ["where", "question_links", "design_factor"])
def test_a_per_run_value_named_like_a_criterion_or_setting_field_adopts_as_any_value_does(record, name):
    """Only a criterion's selectors and links, and a setting's designation, are protected whole; a run's per-run value of
    the same name is an ordinary value, adopted as seed's is."""
    record("spec", "set", "e", name, "--per-run")
    id = record("run", "add", "e", "new.error=8", "old.error=10", "--set", f"{name}=lab1", "--set", "width=512",
                "--set", "loops=6", "--set", "seed=1")["object"]["id"]
    path = record.root / f".rb/experiments/e/runs/{id}.yaml"
    for key, value in ((name, "lab2"), ("seed", 2)):
        doc = rbyaml.loads(path.read_text(encoding="utf-8"))
        doc["per_run"][key] = value
        path.write_text(rbyaml.dumps(doc), encoding="utf-8")
        record("doctor", "--adopt", "--why", f"the run's {key} was recorded wrong")
        assert rbyaml.loads(path.read_text(encoding="utf-8"))["per_run"][key] == value


def test_one_fixed_design_slice_is_not_presented_as_an_independent_sample(record):
    record("experiment", "add", "Single-seed screen", "--id", "screen", "--candidate", "p6")
    for name in ("width", "loops"):
        record("spec", "set", "screen", name, "--design-factor")
    record("criterion", "add", "Loop comparison", "-e", "screen", "--metric", "error", "--subject", "p6",
           "--reference", "p6", "--where", "width=512", "--subject-where", "loops=8", "--reference-where", "loops=6",
           "--at-most", "0", "--id", "onecell")
    record("run", "add", "screen", "p6.error=8", "--set", "width=512", "--set", "loops=8")
    record("run", "add", "screen", "p6.error=10", "--set", "width=512", "--set", "loops=6")
    f = finding(record, "onecell")
    r = f["comparison"]
    assert r["estimate"] == -2 and r["n"] == 1 and r["lo"] is None and r["hi"] is None
    assert r["unit"] == "fixed design cell"
    assert "no independent replications" in r["why_no_interval"]
    assert "interval from 3" not in r["why_no_interval"]
    assert "1 fixed design cell" in f["says"]["units"]


def test_an_ordering_in_every_design_cell_is_a_sign_rule_rb_computes_and_points_to(record, capsys):
    """Review of round 6's build: an ordering claimed in every fixed design cell, declared as --better, read
    undetermined on every rule, and rb's own repair asked for more runs, which never give fixed cells an interval. rb
    points to the rule it computes, a threshold of 0 on each unit; that rule names the cell that misses; and a reason
    given as a line alone, which states no 0, is told to say the ordering in words."""
    record("spec", "set", "e", "width", "--design-factor")
    for width, new in ((256, 9), (512, 11), (1024, 8)):
        run(record, width, 1, new)
    criterion_cmd = ("criterion", "add", "new beats old at every width", "-e", "e", "--metric", "error", "--subject", "new", "--reference", "old")
    record(*criterion_cmd, "--better", "--id", "c-better")
    [item] = [i for i in record("status")["open"] if i["subject"] == "c-better" and i["code"] == "no_interval"]
    assert "--at-most 0 --each" in item["do"] and "interval from 3" not in item["do"], item
    (record.root / "paper.md").write_text("# Paper\n\nThe new model beats the old one at every width.\n", encoding="utf-8")
    code = main([*criterion_cmd, "--at-most", "0", "--each", "--id", "c-each", "--why", "paper.md:3", "--json"])
    err = Envelope.model_validate_json(capsys.readouterr().out).errors[0]
    assert code == 2 and "An ordering a line states" in err.fix, err
    record(*criterion_cmd, "--at-most", "0", "--each", "--id", "c-each", "--why", "paper.md:3 says the new model beats the old one at every width")
    f = finding(record, "c-each")
    assert f["rule_outcome"] == "misses" and "width=512)" in f["says"]["rule"], f["says"]
