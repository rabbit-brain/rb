"""A question reads the evidence filed to it, with its author's interpretation.

These synthetic records distinguish a computed rule from what someone says a met rule
means. Sharing an experiment, descending from a question, or missing a refutation test
must never supply an answer the author did not record.
"""
import html
import json
import shlex
import shutil
from pathlib import Path

import pytest

import rabbit_brain
from rabbit_brain.models import Envelope
from rbtest import Mcp, as_the_user, cli_parser, home, main, object_card, rbyaml, screen_form


@pytest.fixture
def record(workdir, monkeypatch, capsys):
    as_the_user(monkeypatch)
    monkeypatch.setenv("RB_ACTOR", "human:author")

    def cli(*argv, error=None, exit_code=0):
        code = main([*argv, "--json"])
        env = Envelope.model_validate_json(capsys.readouterr().out)
        if error:
            assert code == 2 and not env.ok, (argv, code, env.errors)
            assert env.errors[0].code == error
        else:
            assert code == exit_code and env.ok, (argv, code, env.errors)
        return env.data

    cli.root = workdir
    cli("init", "Several inquiries into one model", "--no-agents")
    cli("question", "add", "Does accuracy hold?", "--id", "q1")
    cli("question", "add", "Is inference affordable?", "--id", "q2")
    cli("experiment", "add", "Shared measurements", "--id", "e1", "-q", "q1", "-q", "q2",
        "--hypothesis", "The new model preserves accuracy at lower cost", "--baseline", "old", "--candidate", "new")
    cli("metric", "add", "error", "--minimize")
    cli("metric", "add", "cost", "--minimize")
    return cli


def criterion(record, id="c1", metric="new.error", question="q1", when_met="Accuracy is within the declared budget.", **extra):
    args = ["criterion", "add", id, "-e", "e1", "--metric", metric, "--at-most", "0.1", "--id", id]
    if question:
        args += ["-q", question]
    if when_met is not None:
        args += ["--when-met", when_met]
    for key, value in extra.items():
        args += ["--" + key.replace("_", "-"), str(value)]
    return record(*args)["object"]


def inquiry(record, question="q1"):
    return next(q["inquiry"] for q in record("status")["questions"] if q["id"] == question)


def rows(root):
    return [json.loads(line) for line in (root / ".rb/log.jsonl").read_text(encoding="utf-8").splitlines()]


def written(root):
    folder = root / ".rb"
    return {p.relative_to(folder).as_posix(): p.read_bytes() for p in [*folder.rglob("*.yaml"), folder / "log.jsonl"]
            if "cache" not in p.relative_to(folder).parts}


def outdated(record, question="q1"):
    return any(item["code"] == "decision_outdated" and item["subject"] == question for item in record("status")["open"])


def test_shared_experiment_has_different_evidence_for_each_inquiry(record):
    criterion(record)
    criterion(record, "c2", "new.cost", "q2", "The measured cost is within budget.")
    criterion(record, "c3", question=None, when_met=None)
    record("freeze", "e1", "--why", "before the measurements")
    record("run", "add", "e1", "new.error=0.05", "new.cost=0.2")
    first, second = inquiry(record), inquiry(record, "q2")
    assert [r["criterion"] for r in first["direct"]] == ["c1"]
    assert [r["criterion"] for r in second["direct"]] == ["c2"]
    assert [r["criterion"] for r in first["unmapped"]] == ["c3"]
    assert [r["criterion"] for r in second["unmapped"]] == ["c3"]
    assert first["direct"][0]["finding"]["rule_outcome"] == "meets"
    assert second["direct"][0]["finding"]["rule_outcome"] == "misses"
    assert first["state"] == second["state"] == "open"


def test_one_criterion_can_be_filed_to_several_questions_without_duplicate_evidence(record):
    record("criterion", "add", "one measured boundary", "-e", "e1", "--metric", "new.error", "--at-most", "0.1",
           "--id", "c1", "-q", "q1", "-q", "q2", "-q", "q1", "--when-met", "The boundary holds.")
    before = written(record.root)
    record("criterion", "file", "c1", "q1", "--when-met", "The boundary holds.", "--why", "confirm its scope")
    assert written(record.root) == before
    for qid in ("q1", "q2"):
        assert [r["criterion"] for r in inquiry(record, qid)["direct"]] == ["c1"]
    links = record("show", "c1")["object"]["question_links"]
    assert sorted(link["question"] for link in links if not link.get("retracted")) == ["q1", "q2"]


def test_a_childs_negative_interpretation_does_not_answer_its_parent(record):
    record("question", "add", "Does the small case fail?", "--id", "q-child", "--parent", "q1")
    criterion(record, question="q-child", when_met="The small case fails the proposed explanation.")
    record("freeze", "e1", "--why", "before measurement")
    record("run", "add", "e1", "new.error=0.05")
    parent = inquiry(record)
    assert parent["direct"] == [] and parent["state"] == "open"
    child = next(q for q in parent["descendants"] if q["question"] == "q-child")
    assert [r["criterion"] for r in child["direct"]] == ["c1"]
    assert child["direct"][0]["interpretation_active"]
    assert record("show", "q1")["object"]["text"] == "Does accuracy hold?"


def test_a_missed_refutation_test_never_asserts_the_inverse(record):
    words = "The proposed explanation fails in this case."
    criterion(record, when_met=words)
    record("freeze", "e1", "--why", "before measurement")
    record("run", "add", "e1", "new.error=0.2")
    [row] = inquiry(record)["direct"]
    assert row["finding"]["rule_outcome"] == "misses"
    assert row["link"]["when_met"] == words and not row["interpretation_active"]
    assert "supports the proposed explanation" not in row["summary"].lower()
    assert inquiry(record)["state"] == "open"


@pytest.mark.parametrize("role", ["primary", "gate"])
def test_a_met_point_estimate_keeps_its_qualification_and_a_gate_is_no_answer(record, role):
    criterion(record, metric="change.error", role=role)
    record("freeze", "e1", "--why", "before measurement")
    record("run", "add", "e1", "old.error=1", "new.error=1.05")
    [row] = inquiry(record)["direct"]
    assert row["finding"]["rule_outcome"] == "meets"
    assert row["finding"]["result"] == "point_only"
    assert row["qualifications"] and "point" in " ".join(row["qualifications"]).lower()
    assert row["interpretation_active"] is (role != "gate")


def test_a_met_threshold_with_an_unresolved_interval_stays_qualified(record):
    criterion(record, metric="change.error")
    record("freeze", "e1", "--why", "before measurement")
    record("analysis", "add", "e1", "--compare", "new/old:error=0.02[-0.04,0.08]", "--method", "bootstrap",
           "--unit", "item", "--units", "40")
    [row] = inquiry(record)["direct"]
    assert row["finding"]["rule_outcome"] == "meets" and row["finding"]["result"] == "unresolved"
    assert row["interpretation_active"] and row["qualifications"]
    assert "unresolved" in " ".join(row["qualifications"]).lower()


def surfaces(record, capsys, question="q1"):
    """What rb show <question>, rb status and rb context print, each as text."""
    said = {}
    for name, argv in (("show", ["show", question]), ("status", ["status"]), ("context", ["context"])):
        main(argv)
        said[name] = capsys.readouterr().out
    return said


def test_an_unassigned_criterion_is_read_with_its_finding_and_the_filing_that_would_link_it(record, capsys):
    """Filed here through its experiment, with no question link: each one is listed with its finding apart from the
    evidence, with the command that links it here, and the question's counts leave it out."""
    criterion(record, "c3", question=None, when_met=None)
    record("freeze", "e1", "--why", "before measurement")
    record("run", "add", "e1", "new.error=0.05")
    q = inquiry(record)
    [row] = q["unmapped"]
    line = ("unassigned (filed here, no question link): c3: " + row["summary"] +
            ' Link it here: rb criterion file c3 q1 --why "<why it bears on this question>" --amend')
    assert line in q["summary"]
    assert q["rollup"]["rules"] == {} and q["rollup"]["results"] == {}
    for name, text in surfaces(record, capsys).items():
        assert line in text, name
    assert "Link it here: rb criterion file c3 q2 " in " ".join(inquiry(record, "q2")["summary"])


def test_rbs_own_next_steps_link_a_new_criterion_to_the_question_it_bears_on(record, capsys):
    """Every command rb offers for declaring a criterion carries -q: the one live question its experiment is filed
    under, or -q <question> when it is filed under several or none. Each parses as written, placeholders filled."""
    def offered(*argv):
        assert main([*argv, "--json"]) == 0
        env = Envelope.model_validate_json(capsys.readouterr().out)
        return list(dict.fromkeys([*env.next, *(i["do"] for i in env.data.get("open", []))]))

    def adds(commands, experiment):
        return [c for c in commands if c.startswith("rb criterion add ") and f" -e {experiment} " in c]

    [add] = adds(offered("experiment", "add", "One inquiry", "--id", "e2", "-q", "q1", "--baseline", "old", "--candidate", "new"), "e2")
    assert add == 'rb criterion add "<what it should show>" -e e2 -q q1 --metric <name> --at-most <x>'
    [add] = adds(offered("experiment", "add", "No inquiry", "--id", "e3", "--baseline", "old", "--candidate", "new"), "e3")
    assert " -e e3 -q <question> --metric " in add
    [add] = adds(offered("run", "add", "e2", "new.error=0.05"), "e2")
    assert add.startswith('rb criterion add "<what it should show>" -e e2 -q q1 --metric new.error --at-most <x>')
    [add] = adds(offered("analysis", "add", "e3", "--compare", "new/old:error=0.1[0.05,0.15]", "--method", "bootstrap"), "e3")
    assert add == 'rb criterion add "<what it should show>" -e e3 -q <question> --metric error --subject new --reference old --better'
    status = offered("status")
    assert adds(status, "e1") == ['rb criterion add "<what it should show>" -e e1 -q <question> --metric <name> --at-most <x>']   # q1 and q2
    # a number or a comparison no criterion reads is said unread, with no rule to declare (round 7)
    assert adds(status, "e2") == [] and adds(status, "e3") == []
    record("retract", "q2", "--why", "folded into q1")
    assert adds(offered("status"), "e1") == ['rb criterion add "<what it should show>" -e e1 -q q1 --metric <name> --at-most <x>']
    parser = cli_parser.build_parser()
    for command in [*status, *offered("status")]:
        if command.startswith("rb criterion add "):
            parser.parse_args(shlex.split(command.replace("<question>", "q1").replace("<name>", "error").replace("<x>", "0.1"))[1:])


def test_a_write_whose_next_step_cannot_read_its_question_is_still_done(record, capsys):
    """A question file that is not a valid record (a hand edit, a half-done merge) stops what has to read it, never a
    write that does not: experiment add, run add and analysis add record their work, exit 0 and offer -q <question>
    for the next criterion, so nothing is recorded again by a retry."""
    record("question", "add", "Does it hold on the edge device?", "--id", "q3")
    path = record.root / ".rb" / "questions" / "q3.yaml"
    doc = rbyaml.loads(path.read_text(encoding="utf-8"))
    del doc["created_by"]
    path.write_text(rbyaml.dumps(doc), encoding="utf-8")

    def done(*argv):
        code = main([*argv, "--json"])
        env = Envelope.model_validate_json(capsys.readouterr().out)
        assert code == 0 and env.ok, (argv, code, env.errors)
        return [c for c in env.next if c.startswith("rb criterion add ")]

    assert done("experiment", "add", "On the edge device", "--id", "e3", "-q", "q3", "--baseline", "old", "--candidate", "new") == [
        'rb criterion add "<what it should show>" -e e3 -q <question> --metric <name> --at-most <x>']
    [add] = done("run", "add", "e3", "new.error=0.05")
    assert add.startswith('rb criterion add "<what it should show>" -e e3 -q <question> --metric new.error ')
    [add] = done("analysis", "add", "e3", "--compare", "new/old:error=0.1[0.05,0.15]", "--method", "bootstrap")
    assert add.startswith('rb criterion add "<what it should show>" -e e3 -q <question> --metric error ')


def test_once_links_are_in_use_each_unassigned_criterion_is_given_to_the_agent_with_the_filing_that_links_it(record):
    """A record made before links existed is not asked to link every criterion it has. Once a criterion is linked (or
    related), each criterion filed under a question and linked to none is an agent item for that question, with the
    filing that links it, and amends a frozen experiment; it fails no gate, and a filing clears it."""
    def unassigned():
        return {(i["subject"], i["question"]): i for i in record("status")["open"] if i["code"] == "unassigned_criterion"}

    criterion(record, "c3", question=None, when_met=None)
    record("run", "add", "e1", "new.error=0.05")
    assert unassigned() == {}
    record("experiment", "add", "Serving cost", "--id", "e2", "-q", "q2", "--baseline", "old", "--candidate", "new")
    record("criterion", "add", "serving stays cheap", "-e", "e2", "--metric", "new.cost", "--at-most", "1", "--id", "c2", "-q", "q2")
    items = unassigned()
    assert set(items) == {("c3", "q1"), ("c3", "q2")}                    # e1 is filed under both
    assert items[("c3", "q1")]["who"] == "agent" and items[("c3", "q1")]["do"] == 'rb criterion file c3 q1 --why "<why it bears on this question>"'
    assert items[("c3", "q1")]["what"] == "c3 is filed under q1 through e1 and linked to no question, so q1's reading leaves it out: c3"
    assert not record("status")["gate"]["integrity"] and not record("status")["gate"]["undecided"]
    record("freeze", "e1", "--why", "before more runs")
    assert {i["do"] for i in unassigned().values()} == {f'rb criterion file c3 {q} --why "<why it bears on this question>" --amend' for q in ("q1", "q2")}
    record("criterion", "file", "c3", "q1", "--why", "it bears on accuracy", "--amend")
    assert unassigned() == {}


@pytest.mark.parametrize("edited", ["question", "experiment"])
def test_an_unassigned_criterion_waits_while_its_filing_would_be_refused_for_an_edit_outside_rb(record, edited):
    """The filing that links an unassigned criterion is refused while its question or its experiment reads as edited
    outside rb. rb status gives that edit's repair (edited_outside_rb) and not the filing until the edit is restored or
    adopted; the filing under a question no edit touches is still given."""
    def unassigned():
        return sorted((i["subject"], i["question"]) for i in record("status")["open"] if i["code"] == "unassigned_criterion")

    criterion(record, "c3", question=None, when_met=None)
    record("experiment", "add", "Serving cost", "--id", "e2", "-q", "q2", "--baseline", "old", "--candidate", "new")
    record("criterion", "add", "serving stays cheap", "-e", "e2", "--metric", "new.cost", "--at-most", "1", "--id", "c2", "-q", "q2")
    assert unassigned() == [("c3", "q1"), ("c3", "q2")]
    path = record.root / ".rb" / ("questions/q1.yaml" if edited == "question" else "experiments/e1/experiment.yaml")
    doc = rbyaml.loads(path.read_text(encoding="utf-8"))
    doc.update({"text": "Does top-1 accuracy hold?"} if edited == "question" else {"title": "Shared measurements, edited"})
    path.write_text(rbyaml.dumps(doc), encoding="utf-8")
    subject = "q1" if edited == "question" else "e1"
    assert [i["subject"] for i in record("status")["open"] if i["code"] == "edited_outside_rb"] == [subject]
    assert unassigned() == ([("c3", "q2")] if edited == "question" else [])
    record("criterion", "file", "c3", "q1", "--why", "it bears on accuracy", error="E_STATE_EDITED")
    record("doctor", "--restore")
    assert unassigned() == [("c3", "q1"), ("c3", "q2")]


def test_a_question_row_says_its_result_its_problems_and_its_filing_once(record, capsys):
    """The unassigned line names the filing and a row's finding line its result and problems, so the row's summary
    says none of them again; its qualifications keep each."""
    criterion(record, "c3", question=None, when_met=None)
    record("run", "add", "e1", "new.error=0.05")
    result = "point only: 1 unit: rb gives an interval from 3"
    [row] = inquiry(record)["unmapped"]
    assert f"result: {result}" in row["qualifications"] and row["summary"].count(result) == 1
    assert "filed here through its experiment" not in row["summary"]
    record("experiment", "add", "Another measurement", "--id", "e2", "-q", "q1", "--baseline", "old", "--candidate", "new")
    record("criterion", "add", "c2", "-e", "e2", "--metric", "new.error", "--at-most", "0.1", "--id", "c2", "-q", "q1")
    record("run", "add", "e2", "new.error=0.05")
    record("retract", "e2", "--why", "the setup leaked the evaluation set")
    [row] = inquiry(record)["direct"]
    assert row["summary"].count(result) == 1 and row["summary"].count("the setup leaked the evaluation set") == 1
    assert "Inquiry integrity: e2 was retracted: the setup leaked the evaluation set." in row["summary"]
    for name, text in surfaces(record, capsys).items():
        assert row["summary"] in text, name
        for line in text.splitlines():
            assert line.count(result) <= 1 and line.count("the setup leaked the evaluation set") <= 1, (name, line)


def test_a_call_on_a_linked_criterion_is_said_beside_its_interpretation_which_stays_active(record, capsys):
    """A reject or an investigate call does not deactivate the interpretation (only its rule, integrity and role
    do), and every reading says the call beside it, with its day, its reason and the words given with it, naming no
    writer."""
    words = "Accuracy is within the declared budget."
    criterion(record, when_met=words)
    record("run", "add", "e1", "new.error=0.05")
    record("decide", "c1", "reject", "--why", "the evaluation leaked test data", "--words", "reject it, the eval leaked")
    q = inquiry(record)
    [row] = q["direct"]
    assert row["interpretation_active"] and row["finding"]["review"]["action"] == "reject"
    day = row["finding"]["review"]["at"][:10]
    assert f'decided: rejected {day}: the evaluation leaked test data' in row["summary"] and 'your words: "reject it, the eval leaked"' in row["summary"]
    [said] = [line for line in q["summary"] if words in line]
    assert f"Interpretation active for c1 (estimate meets criterion; decided: rejected {day}: " in said
    assert "the evaluation leaked test data" in said and "Rule met" not in said
    for name, text in surfaces(record, capsys).items():
        assert said in text, name
    brief = next(b for b in home().briefs if b.id == "q1")
    [group] = brief.overview["interpretations"]
    assert group["text"] == words and group["active"]
    assert [c.split(": the")[0] for c in group["calls"]] == [f"Call on c1: rejected {day}"]
    page = record.root / "calls.html"
    record("page", "--out", str(page))
    assert html.escape(f"Call on c1: rejected {day}: the evaluation leaked test data") in page.read_text(encoding="utf-8")


def test_a_rule_is_said_as_its_finding_words_it_with_the_cells_that_meet_and_their_range(record):
    """Never 'Rule met' for a reading with no interval or across fixed design cells: the label is the finding's, its
    units are among the qualifications, and across cells how many meet and their range stand beside it, while the
    interpretation keeps the documented activation rule (the cells' mean meets)."""
    criterion(record, metric="change.error")
    record("run", "add", "e1", "old.error=1", "new.error=1.05")
    [said] = [line for line in inquiry(record)["summary"] if "Recorded interpretation" in line]
    assert "Interpretation active for c1 (estimate meets criterion)." in said and "Rule met" not in said
    assert "c1: units: 1 run" in said
    record("spec", "set", "e1", "width", "--design-factor")
    criterion(record, "c2", metric="change.error", question="q2", when_met="Cost stays flat at every width.")
    record("run", "add", "e1", "old.error=1", "new.error=1.2", "--set", "width=256")
    record("run", "add", "e1", "old.error=1", "new.error=0.95", "--set", "width=512")
    q = inquiry(record, "q2")
    [row] = q["direct"]
    assert row["interpretation_active"] and row["finding"]["comparison"]["design"]["meets"] == 1
    [said] = [line for line in q["summary"] if "Recorded interpretation" in line]
    assert ("Interpretation active for c2 (estimate meets criterion; 1 of 2 design cells meet the threshold; "
            "range -0.05 to +0.2).") in said
    assert any(text.startswith("units: 2 design cells · descriptive range -0.05 to +0.2;") for text in row["qualifications"])


def test_design_cells_that_all_meet_read_as_both_or_all_of_them(record):
    """Across fixed design cells that all meet the threshold the interpretation said 2 of 2 design cells meet, and the
    finding's units 2 of 2 cells, where every count rb says reads as the only one, both or all n."""
    record("spec", "set", "e1", "width", "--design-factor")
    criterion(record, "c2", metric="change.error", question="q2", when_met="Cost stays flat at every width.")
    record("run", "add", "e1", "old.error=1", "new.error=1.05", "--set", "width=256")
    record("run", "add", "e1", "old.error=1", "new.error=0.95", "--set", "width=512")
    [row] = inquiry(record, "q2")["direct"]
    assert row["finding"]["comparison"]["design"]["meets"] == 2
    [said] = [line for line in inquiry(record, "q2")["summary"] if "Recorded interpretation" in line]
    assert "; both design cells meet the threshold; " in said and "2 of 2" not in said, said
    assert " · both cells meet the threshold" in row["finding"]["says"]["units"], row["finding"]["says"]["units"]


def test_a_design_range_beside_an_interpretation_reads_as_its_finding_says_it(record):
    """Cells either side of a large threshold: the range keeps each end on its own side, as the finding's units say
    it, never 1.235e+04 to 1.235e+04 beside an estimate said as 12347.4."""
    record("spec", "set", "e1", "width", "--design-factor")
    record("criterion", "add", "c1", "-e", "e1", "--metric", "new.cost", "--at-most", "12348", "--id", "c1", "-q", "q2",
           "--when-met", "Cost stays within budget at every width.")
    record("run", "add", "e1", "new.cost=12345.6", "--set", "width=256")
    record("run", "add", "e1", "new.cost=12349.1", "--set", "width=512")
    q = inquiry(record, "q2")
    [row] = q["direct"]
    assert "estimate 12347.4 ≤ 12348" in row["finding"]["says"]["rule"]
    [said] = [line for line in q["summary"] if "Recorded interpretation" in line]
    assert ("Interpretation active for c1 (estimate meets criterion; 1 of 2 design cells meet the threshold; "
            "range 12345.6 to 12349.1).") in said and "e+04" not in said
    assert "descriptive range 12345.6 to 12349.1;" in row["finding"]["says"]["units"]


def test_conflicting_source_blocks_interpretation_without_changing_the_numeric_rule(record):
    source = record.root / "config.json"
    source.write_text('{"width": 16}', encoding="utf-8")
    record("spec", "set", "e1", "width", "--source", "config.json#width")
    criterion(record)
    record("freeze", "e1", "--why", "before measurement")
    record("run", "add", "e1", "new.error=0.05")
    assert inquiry(record)["direct"][0]["interpretation_active"]
    record("decide", "q1", "continue", "--why", "continue on the current reading")
    source.write_text('{"width": 32}', encoding="utf-8")
    [row] = inquiry(record)["direct"]
    assert row["finding"]["rule_outcome"] == "meets" and row["finding"]["integrity"]
    assert not row["interpretation_active"] and outdated(record)


def test_adding_links_keeps_the_existing_comparison_and_records_its_later_timing(record):
    criterion(record, question=None, when_met=None)
    record("freeze", "e1", "--why", "before measurement")
    record("run", "add", "e1", "new.error=0.05")
    before = record("show", "c1")["finding"]
    bytes_before = written(record.root)
    record("criterion", "file", "c1", "q1", "--when-met", "The measured error is within budget.",
           "--why", "state how this measurement bears on accuracy", error="E_FROZEN")
    assert written(record.root) == bytes_before
    record("criterion", "file", "c1", "q1", "--when-met", "The measured error is within budget.",
           "--amend", "--why", "state how this measurement bears on accuracy")
    after = record("show", "c1")["finding"]
    assert after["comparison"] == before["comparison"]
    assert after["rule_outcome"] == before["rule_outcome"]
    assert not any(p["code"] == "drift" for p in after["integrity"])
    [row] = inquiry(record)["direct"]
    assert row["link_timing"] and row["qualifications"]
    assert "after" in str(row["link_timing"]).lower()


def test_old_criteria_without_links_read_without_changing_files_freeze_or_finding(record):
    criterion(record, question=None, when_met=None)
    record("freeze", "e1", "--why", "the old rule")
    record("run", "add", "e1", "new.error=0.05")
    before = written(record.root)
    finding = record("show", "c1")["finding"]
    frozen = record("show", "e1")["object"]["frozen"]
    assert inquiry(record)["direct"] == []
    assert [r["criterion"] for r in inquiry(record)["unmapped"]] == ["c1"]
    assert record("show", "c1")["finding"] == finding
    assert record("show", "e1")["object"]["frozen"] == frozen
    assert written(record.root) == before


def test_question_decision_reads_its_scoped_findings_and_mapping(record):
    criterion(record)
    criterion(record, "c2", "new.cost", "q2", "The measured cost is within budget.")
    record("freeze", "e1", "--why", "before measurement")
    record("run", "add", "e1", "new.error=0.05", "new.cost=0.05")
    decision = record("decide", "q1", "continue", "--why", "continue on the accuracy reading")["object"]
    assert decision["inquiry_basis"] is not None and set(decision["basis"]) == {"c1"}
    assert not outdated(record)
    record("run", "add", "e1", "new.cost=0.2")
    assert not outdated(record)
    record("criterion", "file", "c1", "q1", "--when-met", "This accuracy budget holds on the measured population.",
           "--amend", "--why", "make the interpretation's scope explicit")
    assert outdated(record)


@pytest.mark.parametrize("elsewhere", [False, True], ids=["no-links-in-the-record", "a-link-elsewhere"])
def test_a_call_with_no_direct_evidence_becomes_outdated_when_the_first_link_is_added(record, elsewhere):
    """With no link anywhere the call is made on what is filed under the question, as earlier builds record it; with a
    link elsewhere it keeps the scoped snapshot, with no criterion in it. Either way the first link moves it."""
    criterion(record, question=None, when_met=None)
    if elsewhere:
        criterion(record, "c2", "new.cost", "q2", "The measured cost is within budget.")
    record("run", "add", "e1", "new.error=0.05", "new.cost=0.05")
    decision = record("decide", "q1", "continue", "--why", "continue while the inquiry is being scoped")["object"]
    if elsewhere:
        assert decision["basis"] == {} and decision["inquiry_basis"]["criteria"] == {}
    else:
        assert set(decision["basis"]) == {"c1"} and decision["inquiry_basis"] is None
        assert "inquiry_basis" not in (record.root / f".rb/decisions/{decision['id']}.yaml").read_text(encoding="utf-8")
    assert not outdated(record)
    record("criterion", "file", "c1", "q1", "--when-met", "The budget holds.", "--why", "declare the first direct evidence")
    assert outdated(record)
    again = record("decide", "q1", "continue", "--why", "continue on the linked reading")["object"]
    assert set(again["inquiry_basis"]["criteria"]) == {"c1"} and not outdated(record)


def test_with_no_links_in_the_record_a_question_call_is_recorded_as_earlier_builds_record_it(record):
    """Decided for the whole record: a question call reads every criterion filed under it or a question below it, and
    an unlinked question in a record with links keeps the scoped snapshot instead, so results linked only to another
    question never move it."""
    record("question", "add", "Does the small case hold?", "--id", "q-child", "--parent", "q1")
    record("experiment", "add", "Small case", "--id", "e2", "-q", "q-child", "--baseline", "old", "--candidate", "new")
    criterion(record, question=None, when_met=None)
    record("criterion", "add", "small case", "-e", "e2", "--metric", "new.error", "--at-most", "0.1", "--id", "c2")
    record("run", "add", "e1", "new.error=0.05")
    record("run", "add", "e2", "new.error=0.05")
    first = record("decide", "q1", "continue", "--why", "the whole line so far")["object"]
    assert set(first["basis"]) == {"c1", "c2"} and first["inquiry_basis"] is None
    record("run", "add", "e2", "new.error=0.07")
    assert outdated(record)
    record("decide", "q1", "continue", "--why", "on the readings as they stand")
    assert not outdated(record)
    criterion(record, "c3", "new.cost", "q2", "The measured cost is within budget.")
    scoped = record("decide", "q1", "park", "--why", "a link elsewhere scopes every question call")["object"]
    assert scoped["basis"] == {} and scoped["inquiry_basis"]["criteria"] == {}
    record("run", "add", "e1", "new.cost=0.2")
    assert not outdated(record)


def test_a_relation_alone_scopes_question_calls(record):
    """A declared relation is new to earlier builds, as a question link is: with one in the record and no link, a
    question call keeps the scoped snapshot, with no criterion in it."""
    criterion(record, "c1", "change.error", question=None, when_met=None)
    criterion(record, "c2", "change.error", question=None, when_met=None)
    record("criterion", "relate", "c1", "reverses", "c2", "--orientation", "same", "--why", "the same arms, read twice")
    record("run", "add", "e1", "old.error=0.1", "new.error=0.05")
    decision = record("decide", "q1", "continue", "--why", "continue while the inquiry is being scoped")["object"]
    assert decision["basis"] == {} and decision["inquiry_basis"]["criteria"] == {}


def test_a_call_on_a_question_with_nothing_under_it_moves_when_a_criterion_is_linked_to_it(record):
    record("question", "add", "Does it hold on another split?", "--id", "q3")
    decision = record("decide", "q3", "continue", "--why", "nothing to read yet")["object"]
    assert decision["basis"] == {} and decision["inquiry_basis"] is None
    assert not outdated(record, "q3")
    criterion(record, question="q3")
    assert outdated(record, "q3")


def test_a_call_made_before_anything_was_filed_under_its_question_asks_again_once_a_criterion_is_linked(record):
    """As earlier builds read it: the call reads as made on an earlier reading once a reading is filed under its
    question, and status asks for a new call once a criterion is linked to the question."""
    record("question", "add", "Does it hold on another split?", "--id", "q3")
    decision = record("decide", "q3", "continue", "--why", "nothing to read yet")["object"]
    record("experiment", "add", "Another split", "--id", "e3", "-q", "q3", "--baseline", "old", "--candidate", "new")
    record("criterion", "add", "the error holds on the other split", "-e", "e3", "--metric", "new.error", "--at-most", "0.1", "--id", "c5")
    record("run", "add", "e3", "new.error=0.05")
    assert record("show", decision["id"])["current"] is False
    assert not outdated(record, "q3") and not record("status")["gate"]["undecided"]
    record("criterion", "file", "c5", "q3", "--why", "say what c5 bears on")
    assert outdated(record, "q3")


def fixture_record(workdir, name):
    fixture = json.loads((Path(__file__).parent / "fixtures" / name).read_text(encoding="utf-8"))
    for rel, content in fixture["files"].items():
        path = workdir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
    return fixture


@pytest.fixture
def earlier(workdir, monkeypatch, capsys):
    """A record an earlier build made, before criteria could be linked to questions: q1 holds e1 with c1 and the gate g1,
    which misses, and that build's park on q1 settles the gate (tests/fixtures/question-call-before-links.json)."""
    as_the_user(monkeypatch)
    monkeypatch.setenv("RB_ACTOR", "human:ana")

    def cli(*argv, exit_code=0):
        code = main([*argv, "--json"])
        env = Envelope.model_validate_json(capsys.readouterr().out)
        assert code == exit_code and env.ok, (argv, code, env.errors)
        return env.data

    cli.root = workdir
    cli.fixture = fixture_record(workdir, "question-call-before-links.json")
    return cli


def items(record, code):
    return [item["subject"] for item in record("status")["open"] if item["code"] == code]


def test_an_earlier_builds_question_call_reads_as_it_was_made(earlier):
    assert items(earlier, "decision_outdated") == [] and items(earlier, "gate_missed") == []
    q = inquiry(earlier)
    assert q["direct"] == [] and [r["criterion"] for r in q["unmapped"]] == ["c1", "g1"]
    assert earlier("doctor")["outcome"]["passed"]
    assert written(earlier.root) == {rel.removeprefix(".rb/"): text.encode("utf-8") for rel, text in earlier.fixture["files"].items()}
    earlier("run", "add", "e1", "base.val_loss=1.0", "wide.val_loss=1.05")
    [moved] = [i for i in earlier("status")["open"] if i["code"] == "decision_outdated"]
    assert moved["subject"] == "q1" and moved["what"].endswith("moved since: c1")
    assert items(earlier, "gate_missed") == ["g1"]


def test_a_new_question_call_in_an_earlier_record_keeps_its_shape_until_a_criterion_is_linked(earlier):
    decision = earlier("decide", "q1", "continue", "--why", "resume when the kernels land")["object"]
    assert decision["inquiry_basis"] is None and set(decision["basis"]) == {"c1", "g1"}
    assert "inquiry_basis" not in (earlier.root / f".rb/decisions/{decision['id']}.yaml").read_text(encoding="utf-8")
    assert items(earlier, "decision_outdated") == [] and items(earlier, "gate_missed") == []
    earlier("criterion", "file", "c1", "q1", "--when-met", "Width lowers val_loss.", "--why", "say what c1 bears on")
    [moved] = [i for i in earlier("status")["open"] if i["code"] == "decision_outdated"]
    assert moved["subject"] == "q1" and moved["what"].endswith("moved since: c1")


def test_a_question_link_alone_is_discoverable_without_an_experiment_filing(record):
    record("question", "add", "Another angle on the same measurements", "--id", "q3")
    criterion(record, question="q3")
    status = record("status")
    assert not any(item["code"] == "open_question" and item["subject"] == "q3" for item in status["open"])
    scoped = record("status", "e1")
    assert any(q["id"] == "q3" for q in scoped["questions"])
    brief = next(b for b in home().briefs if b.id == "q3")
    assert [r["criterion"] for r in brief.inquiry["direct"]] == ["c1"]
    assert any(e.id == "e1" for e in brief.experiments)


def test_moving_an_inquiry_keeps_its_links_and_logs_the_reason_and_words(record):
    criterion(record, question="q2")
    before = record("show", "c1")["object"]
    state = rabbit_brain.open(agent="inquiry-sdk")
    words = "Put the affordability inquiry under accuracy for this outline."
    moved = state.move_question("q2", parent="q1", why="reorganize the inquiry tree", words=words)
    assert moved.id == "q2" and moved.parent == "q1"
    assert record("show", "c1")["object"] == before
    assert rows(record.root)[-1]["words"] == words
    assert rows(record.root)[-1]["detail"]["why"] == "reorganize the inquiry tree"
    parent = inquiry(record)
    assert parent["direct"] == []
    child = next(q for q in parent["descendants"] if q["question"] == "q2")
    assert [row["criterion"] for row in child["direct"]] == ["c1"]


def test_a_call_on_one_inquiry_does_not_settle_another_inquirys_missed_gate(record):
    criterion(record, question="q2", role="gate")
    record("run", "add", "e1", "new.error=0.2")
    assert any(item["code"] == "gate_missed" for item in record("status")["open"])
    record("decide", "q1", "park", "--why", "park the first inquiry only")
    assert any(item["code"] == "gate_missed" for item in record("status")["open"])
    record("decide", "q2", "park", "--why", "park the inquiry that owns this policy")
    assert not any(item["code"] == "gate_missed" for item in record("status")["open"])


def test_link_revisions_removal_and_refiling_keep_the_attributed_history(record, monkeypatch):
    criterion(record)
    monkeypatch.setenv("AI_AGENT", "inquiry-writer")
    monkeypatch.setenv("RB_SESSION", "synthetic-inquiry-session")
    words = "Say exactly which population this answers."
    revised = "Accuracy holds on the measured population."
    record("criterion", "file", "c1", "q1", "--when-met", revised, "--why", "limit the interpretation", "--words", words)
    links = record("show", "c1")["object"]["question_links"]
    assert len(links) == 2 and links[0]["retracted"] and not links[1].get("retracted")
    current = links[1]
    assert current["question_text"] == "Does accuracy hold?" and current["when_met"] == revised
    assert (current["declared_by"], current["via"], current["words"], current["session"]) == \
        ("agent:inquiry-writer", "cli", words, "synthetic-inquiry-session")
    assert rows(record.root)[-1]["words"] == words
    before = written(record.root)
    record("criterion", "file", "c1", "q1", "--why", "keep the same scope")
    assert written(record.root) == before
    record("criterion", "file", "c1", "q1", "--remove", "--why", "this question is no longer its scope")
    assert inquiry(record)["direct"] == [] and inquiry(record)["withdrawn"]
    record("criterion", "file", "c1", "q1", "--when-met", revised, "--why", "restore the explicit scope")
    links = record("show", "c1")["object"]["question_links"]
    assert len(links) == 3 and sum(not x.get("retracted") for x in links) == 1
    record("retract", "c1", "--why", "the criterion is no longer used")
    assert not any(row["interpretation_active"] for row in inquiry(record)["direct"])


def test_a_retracted_linked_criterion_leaves_the_questions_evidence_and_a_retracted_experiment_is_said_once(record, capsys):
    """A retracted criterion is no evidence on the question it is linked to: its link is context beside the withdrawn
    ones, its retraction said once. A live criterion of a retracted experiment stays, read with experiment_retracted."""
    words = "Accuracy is within the declared budget."
    criterion(record, when_met=words)
    record("experiment", "add", "Another measurement", "--id", "e2", "-q", "q1", "--baseline", "old", "--candidate", "new")
    record("criterion", "add", "c2", "-e", "e2", "--metric", "new.error", "--at-most", "0.1", "--id", "c2",
           "-q", "q1", "--when-met", words)
    record("run", "add", "e1", "new.error=0.05")
    record("run", "add", "e2", "new.error=0.05")
    record("retract", "c1", "--why", "the rule was wrong")
    record("retract", "e2", "--why", "the setup leaked the evaluation set")
    q = inquiry(record)
    assert [r["criterion"] for r in q["direct"]] == ["c2"]
    assert [p["code"] for p in q["direct"][0]["integrity"]] == ["experiment_retracted"]
    [gone] = q["withdrawn"]
    assert gone["criterion"] == "c1" and [p["code"] for p in gone["integrity"]] == ["retracted"]
    assert q["rollup"]["rules"] == {"meets": 1}
    assert all("c1" not in line for line in q["summary"])
    assert main(["show", "q1"]) == 0
    shown = capsys.readouterr().out
    assert "the criterion was retracted" not in shown and "c1:" not in shown and "; retracted" not in shown
    # the screens no longer hold the dead row for review
    brief = next(b for b in home().briefs if b.id == "q1")
    assert [f.criterion_id for f in brief.inquiry_findings] == ["c2"]
    record("decide", "q1", "park", "--why", "wait for a clean measurement")
    call = record("status")["decisions"][-1]
    assert list(call["inquiry_basis"]["criteria"]) == ["c2"]


@pytest.mark.parametrize("mutation", ["edit", "delete"])
def test_an_edited_or_missing_question_never_keeps_its_interpretation_active(record, mutation):
    criterion(record, question="q2")
    record("freeze", "e1", "--why", "before measurement")
    record("run", "add", "e1", "new.error=0.05")
    assert inquiry(record, "q2")["direct"][0]["interpretation_active"]
    path = record.root / ".rb/questions/q2.yaml"
    if mutation == "edit":
        doc = rbyaml.loads(path.read_text(encoding="utf-8"))
        doc["text"] = "A different question written outside rb"
        path.write_text(rbyaml.dumps(doc), encoding="utf-8")
        [row] = inquiry(record, "q2")["direct"]
        assert row["integrity"] and not row["interpretation_active"]
    else:
        path.unlink()
        status = record("status")
        assert any(item["code"] == "edited_outside_rb" for item in status["open"])
        assert all(not row["interpretation_active"] for q in status["questions"] for row in q["inquiry"]["direct"])
    before = written(record.root)
    record("criterion", "file", "c1", "q2", "--why", "do not silently adopt a changed question",
           error="E_STATE_EDITED")
    assert written(record.root) == before


def test_clearing_an_interpretation_keeps_its_scope_and_retracted_questions_block_it(record):
    criterion(record)
    record("run", "add", "e1", "new.error=0.05")
    record("criterion", "file", "c1", "q1", "--when-met", "", "--why", "keep relevance without this claim")
    [row] = inquiry(record)["direct"]
    assert row["link"]["when_met"] == "" and not row["interpretation_active"]
    assert inquiry(record)["withdrawn"][0]["link"]["when_met"] == "Accuracy is within the declared budget."
    record("criterion", "file", "c1", "q1", "--when-met", "The budget holds.", "--why", "declare the narrowed interpretation")
    record("retract", "q1", "--why", "this inquiry was withdrawn")
    shown = record("show", "q1")
    if "inquiry" in shown:
        assert not any(row["interpretation_active"] for row in shown["inquiry"]["direct"])
    record("criterion", "file", "c1", "q1", "--remove", "--why", "remove the withdrawn question from this rule")
    assert not any(not link.get("retracted") for link in record("show", "c1")["object"]["question_links"])


def test_unavailable_question_fails_integrity_until_its_link_is_withdrawn(record):
    criterion(record)
    record("freeze", "e1", "--why", "before measurement")
    record("run", "add", "e1", "new.error=0.05")
    before = record("show", "c1")["finding"]
    record("status", "--fail-on", "integrity")
    record("retract", "q1", "--why", "withdraw this inquiry")
    status = record("status", "--fail-on", "integrity", exit_code=1)
    assert any(item["code"] == "question_link_unavailable" and item["subject"] == "c1" for item in status["open"])
    after = record("show", "c1")["finding"]
    assert (after["comparison"], after["rule_outcome"]) == (before["comparison"], before["rule_outcome"])
    record("criterion", "file", "c1", "q1", "--remove", "--amend", "--why", "withdraw the unavailable scope")
    record("status", "--fail-on", "integrity")


def test_stale_question_link_mutes_only_the_inquiry_reading(record):
    criterion(record, metric="change.error")
    record("freeze", "e1", "--why", "before measurement")
    for i, error in enumerate((0.89, 0.90, 0.91)):
        record("run", "add", "e1", "old.error=1", f"new.error={error}", "--command", f"measure {i}")
    before = record("show", "c1")["finding"]
    first = next(b for b in home().briefs if b.id == "q1")
    assert first.inquiry_findings[0].good
    assert "meets" not in next(r for r in home().tree if r.kind == "question" and r.id == "q1").muted

    # Adoption makes the revised text an ordinary recorded edit, leaving only the stale semantic link to diagnose.
    path = record.root / ".rb/questions/q1.yaml"
    doc = rbyaml.loads(path.read_text(encoding="utf-8"))
    doc["text"] = "Does accuracy hold on a different population?"
    path.write_text(rbyaml.dumps(doc), encoding="utf-8")
    record("doctor", "--adopt", "--why", "keep the revised inquiry text")
    shown = record("show", "q1")["inquiry"]
    assert any(p["code"] == "question_link_outdated" for p in shown["direct"][0]["integrity"])
    assert not shown["direct"][0]["interpretation_active"]
    screen = home()
    brief = next(b for b in screen.briefs if b.id == "q1")
    assert not brief.inquiry_findings[0].good
    assert "meets" in next(r for r in screen.tree if r.kind == "question" and r.id == "q1").muted
    # The numerical instrument is unchanged; only the reading under the changed question loses its green signal.
    assert record("show", "c1")["finding"] == before
    assert all(f.good for b in screen.briefs for e in b.experiments for f in e.findings if f.criterion_id == "c1")
    assert all("meets" not in r.muted for r in screen.tree if r.kind == "experiment" and r.id == "e1")


def test_a_filing_never_carries_an_interpretation_to_a_reworded_question_unless_restated(record, monkeypatch, capsys):
    """Decided by content, for whoever files: after the question is reworded, a filing that omits --when-met would put
    the earlier author's words in a new declaration, so it is refused until the words are restated or cleared."""
    words = "Accuracy is within the declared budget."
    criterion(record, when_met=words)
    criterion(record, "c2", "new.cost", when_met=None)
    path = record.root / ".rb/questions/q1.yaml"
    doc = rbyaml.loads(path.read_text(encoding="utf-8"))
    doc["text"] = "Does accuracy hold on the deployed devices?"
    path.write_text(rbyaml.dumps(doc), encoding="utf-8")
    record("doctor", "--adopt", "--why", "the inquiry was reworded")
    before = written(record.root)
    for agent in (None, "inquiry-writer"):
        with monkeypatch.context() as m:
            if agent:
                m.setenv("AI_AGENT", agent)
            assert main(["criterion", "file", "c1", "q1", "--why", "refresh the scope", "--json"]) == 2
        [error] = Envelope.model_validate_json(capsys.readouterr().out).errors
        assert error.code == "E_OBJECT_INVALID" and "reworded" in error.message
        assert 'rb criterion file c1 q1 --when-met "..."' in error.fix and '--when-met ""' in error.fix
        assert written(record.root) == before
    record("criterion", "file", "c2", "q1", "--why", "the cost still bears on the reworded inquiry")
    monkeypatch.setenv("AI_AGENT", "inquiry-writer")
    record("criterion", "file", "c1", "q1", "--when-met", words, "--why", "the same reading holds for the new wording")
    link = next(l for l in record("show", "c1")["object"]["question_links"] if not l.get("retracted"))
    assert (link["when_met"], link["declared_by"], link["question_text"]) == (words, "agent:inquiry-writer", "Does accuracy hold on the deployed devices?")
    assert not [i for i in record("status")["open"] if i["code"] == "question_link_outdated"]


def test_on_a_frozen_experiment_the_restate_command_a_refusal_gives_carries_the_amendment(record, capsys):
    """The command a refused filing gives must run as given: on a frozen experiment it carries --amend."""
    words = "Accuracy is within the declared budget."
    criterion(record, when_met=words)
    record("freeze", "e1", "--why", "before the measurements")
    path = record.root / ".rb/questions/q1.yaml"
    doc = rbyaml.loads(path.read_text(encoding="utf-8"))
    doc["text"] = "Does accuracy hold on the deployed devices?"
    path.write_text(rbyaml.dumps(doc), encoding="utf-8")
    record("doctor", "--adopt", "--why", "the inquiry was reworded")
    assert main(["criterion", "file", "c1", "q1", "--why", "refresh the scope", "--amend", "--json"]) == 2
    [error] = Envelope.model_validate_json(capsys.readouterr().out).errors
    command = 'rb criterion file c1 q1 --when-met "..." --why "..." --amend'
    assert error.code == "E_OBJECT_INVALID" and command in error.fix, error.fix
    argv = shlex.split(command.replace('--when-met "..."', f'--when-met "{words}"').replace('--why "..."', '--why "the same reading holds"'))
    record(*argv[1:])
    link = next(l for l in record("show", "c1")["object"]["question_links"] if not l.get("retracted"))
    assert (link["when_met"], link["question_text"]) == (words, "Does accuracy hold on the deployed devices?")


def test_a_reworded_question_asks_for_words_only_where_its_link_has_them(record):
    """rb never asks for an interpretation nobody declared: the repair of a link with no words refiles it without
    --when-met, and that command, run as given, clears the item."""
    criterion(record, when_met=None)
    criterion(record, "c2", "new.cost", when_met="The cost is within budget.")
    path = record.root / ".rb/questions/q1.yaml"
    doc = rbyaml.loads(path.read_text(encoding="utf-8"))
    doc["text"] = "Does accuracy hold on the deployed devices?"
    path.write_text(rbyaml.dumps(doc), encoding="utf-8")
    record("doctor", "--adopt", "--why", "the inquiry was reworded")
    items = {i["subject"]: i for i in record("status")["open"] if i["code"] == "question_link_outdated"}
    assert items["c1"]["do"] == 'rb criterion file c1 q1 --why "<why this mapping fits the current question>"', items["c1"]
    assert "interpretation" not in items["c1"]["what"] and "met rule" not in items["c1"]["what"]
    assert '--when-met "<what a met rule means for this question now>"' in items["c2"]["do"]
    argv = shlex.split(items["c1"]["do"].replace('"<why this mapping fits the current question>"', '"it still bears on the inquiry"'))
    record(*argv[1:])
    assert [i["subject"] for i in record("status")["open"] if i["code"] == "question_link_outdated"] == ["c2"]
    link = next(l for l in record("show", "c1")["object"]["question_links"] if not l.get("retracted"))
    assert (link["when_met"], link["question_text"]) == ("", "Does accuracy hold on the deployed devices?")


def test_show_retracted_question_keeps_the_inquiry_diagnostic(record):
    criterion(record)
    record("run", "add", "e1", "new.error=0.05")
    record("retract", "q1", "--why", "withdraw the inquiry")
    q = record("show", "q1")["inquiry"]
    assert q["question"] == "q1" and q["direct"]
    [row] = q["direct"]
    assert row["link"]["when_met"] == "Accuracy is within the declared budget."
    assert not row["interpretation_active"]
    assert any(p["code"] == "question_retracted" for p in row["integrity"])


def test_page_advice_uses_only_criteria_linked_to_its_question(record):
    criterion(record, metric="change.error")
    criterion(record, "c2", "change.cost", "q2", "The cost boundary holds.")
    record("freeze", "e1", "--why", "before measurement")
    for i, error in enumerate((0.89, 0.90, 0.91)):
        record("run", "add", "e1", "old.error=1", f"new.error={error}", "--command", f"measure {i}")
    page = record.root / "advice.html"
    record("page", "--out", str(page))
    text = page.read_text(encoding="utf-8")
    first = text.split('id="q-q1"', 1)[1].split('id="q-q2"', 1)[0]
    second = text.split('id="q-q2"', 1)[1].split('id="runs"', 1)[0]
    assert "Next step" not in first
    assert "Next step" in second and "cost measurement on Shared measurements" in second


@pytest.mark.parametrize("args,error", [
    (["criterion", "file", "c1", "q1", "--why", " "], "E_OBJECT_INVALID"),
    (["criterion", "file", "c1", "q1", "--remove", "--when-met", "x", "--why", "x"], "E_OBJECT_INVALID"),
    (["criterion", "file", "c1", "missing-q", "--why", "missing scope"], "E_OBJECT_NOT_FOUND"),
    (["criterion", "add", "unscoped interpretation", "-e", "e1", "--metric", "new.error", "--at-most", "1", "--when-met", "x"], "E_OBJECT_INVALID"),
])
def test_invalid_link_requests_are_atomic(record, args, error):
    criterion(record)
    before = written(record.root)
    record(*args, error=error)
    assert written(record.root) == before


@pytest.mark.parametrize("mutation,error", [("criterion", "E_STATE_EDITED"), ("newer", "E_STATE_NEWER")])
def test_an_identical_link_cannot_bypass_edited_or_newer_state_guards(record, mutation, error):
    criterion(record)
    path = record.root / (".rb/experiments/e1/criteria/c1.yaml" if mutation == "criterion" else ".rb/questions/q2.yaml")
    doc = rbyaml.loads(path.read_text(encoding="utf-8"))
    if mutation == "criterion":
        doc["note"] = "a change made outside rb"
    else:
        doc["rb"] = 2
    path.write_text(rbyaml.dumps(doc), encoding="utf-8")
    before = written(record.root)
    record("criterion", "file", "c1", "q1", "--why", "confirm the existing scope", error=error)
    assert written(record.root) == before


def test_inquiry_reading_is_shared_by_status_show_context_cards_and_page(record, capsys):
    text = 'A <script>alert("inquiry")</script> & a quoted "boundary".'
    criterion(record, when_met=text)
    criterion(record, "c2", "new.cost", "q2", "Cost interpretation belonging only to q2.")
    criterion(record, "c3", question=None, when_met=None)
    record("freeze", "e1", "--why", "before measurement")
    record("run", "add", "e1", "new.error=0.05", "new.cost=0.05")
    q = inquiry(record)
    assert record("show", "q1")["inquiry"] == q
    screen = home()
    brief = next(b for b in screen.briefs if b.id == "q1")
    assert brief.inquiry == screen_form(q) and brief.summary == q["summary"]     # the record's reading, with no writer in it
    assert brief.overview["interpretations"][0]["text"] == text
    assert brief.overview["interpretations"][0]["author"] == ""            # a writer is named on no screen
    assert "No interval" in [limit["label"] for limit in brief.overview["limits"]]
    assert brief.overview["headline"] == "Evidence includes point estimates only"
    card = object_card("q1")
    assert all(line in card.sentences for line in q["summary"])
    assert all("Cost interpretation belonging only to q2." not in line for line in card.sentences)
    # Context repeats the exact shared reading, including its author's text and qualifications.
    context = rabbit_brain.open().context()
    for line in q["summary"]:
        assert line in context
    assert main(["status"]) == 0
    status_text = capsys.readouterr().out
    for line in q["summary"]:
        assert line in status_text
    page = record.root / "inquiries.html"
    record("page", "--out", str(page))
    page_text = page.read_text(encoding="utf-8")
    assert '<script>alert("inquiry")</script>' not in page_text
    assert "&lt;script&gt;" in page_text and text in html.unescape(page_text)
    # The same experiment belongs to both questions, but its record has one canonical HTML anchor.
    assert page_text.count('id="x-e1"') == 1
    # Filtering the question's presentation must not delete unassigned criteria from the global catalogue.
    all_criteria = {f.criterion_id for b in screen.briefs for e in b.experiments for f in e.findings}
    assert all_criteria == {"c1", "c2", "c3"}
    assert {r["criterion"] for r in q["direct"]} == {"c1"}
    assert {r["criterion"] for r in q["unmapped"]} == {"c3"}


def test_sdk_and_mcp_use_the_same_links_and_attribute_their_writes(record):
    state = rabbit_brain.open(agent="inquiry-sdk")
    crit = state.experiment("e1").criterion("SDK criterion", metric="new.error", at_most=0.1, id="c1",
                                             questions=["q1"], when_met="The budget holds.", why="accuracy scope",
                                             words="Use the accuracy question.")
    [link] = crit.data.question_links
    assert (link.question, link.declared_by, link.via, link.words) == \
        ("q1", "agent:inquiry-sdk", "sdk", "Use the accuracy question.")
    crit.file("q2", why="the same test also bears on this inquiry", when_met="The shared boundary holds.")
    assert {link.question for link in crit.data.question_links} == {"q1", "q2"}
    server = Mcp("inquiry-mcp")

    def rpc(method, params=None):
        return server.handle({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}})["result"]

    try:
        tools = {tool["name"]: tool for tool in rpc("tools/list")["tools"]}
        assert "criterion_file" in tools
        answer = rpc("tools/call", {"name": "criterion_file", "arguments": {
            "criterion": "c1", "question": "q2", "when_met": "The revised shared boundary holds.",
            "why": "clarify the interpretation", "words": "Use this revised wording."}})
        assert not answer["isError"], answer
        link = next(link for link in crit.data.question_links if link.question == "q2" and not link.retracted)
        assert (link.declared_by, link.via, link.words) == ("agent:inquiry-mcp", "mcp", "Use this revised wording.")
        assert rows(record.root)[-1]["via"] == "mcp"
    finally:
        assert server.close() == 0


@pytest.mark.skipif(shutil.which("git") is None, reason="rb clone starts a git repository")
def test_clone_inherits_question_links_and_their_authors_but_no_answer_or_decision(record, tmp_path_factory):
    criterion(record)
    record("freeze", "e1", "--why", "before measurement")
    record("run", "add", "e1", "new.error=0.05")
    record("decide", "q1", "accept", "--why", "the source's call")
    source = record("show", "c1")["object"]["question_links"]
    bundle = tmp_path_factory.mktemp("inquiry-bundle") / "bundle"
    record("export", "--with-record", "--out", str(bundle))
    cloned = rabbit_brain.clone(bundle, tmp_path_factory.mktemp("inquiry-clone") / "work", agent="cloner")
    assert [link.model_dump() for link in cloned.criterion("c1").data.question_links] == source
    assert cloned.records("decision") == [] and cloned.records("run") == []
    q = next(q["inquiry"] for q in cloned.status()["questions"] if q["id"] == "q1")
    assert q["state"] == "open" and not q["direct"][0]["interpretation_active"]
    assert q["direct"][0]["finding"]["rule_outcome"] == "untested"
