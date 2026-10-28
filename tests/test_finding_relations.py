"""A reversal is read from two declared, aligned comparisons, with both uncertainties retained."""
import json
from pathlib import Path

import pytest

import rabbit_brain
from rabbit_brain.errors import RBError
from rabbit_brain.models import Envelope
from rbtest import Mcp, as_the_user, main


@pytest.fixture
def record(workdir, monkeypatch, capsys):
    as_the_user(monkeypatch)

    def cli(*args, error=None, exit_code=0):
        code = main([*args, "--json"])
        answer = Envelope.model_validate_json(capsys.readouterr().out)
        if error:
            assert code == 2 and answer.errors[0].code == error, (args, code, answer)
        else:
            assert code == exit_code and answer.ok, (args, code, answer)
        return answer.data

    cli.root = workdir
    cli("init", "Short and long training", "--no-agents")
    cli("question", "add", "Does the direction reverse?", "--id", "q1")
    cli("metric", "add", "loss", "--minimize", "--unit", "nats")
    for number in (1, 2):
        cli("experiment", "add", f"Training {number}", "--id", f"e{number}", "--baseline", "old", "--candidate", "new")
        cli("criterion", "add", f"Comparison {number}", "-e", f"e{number}", "--metric", "change.loss",
            "--at-most", "0", "--id", f"c{number}", *(["-q", "q1"] if number == 1 else []))
    return cli


def relate(record, *extra, orientation="same"):
    return record("criterion", "relate", "c1", "reverses", "c2", "--orientation", orientation,
                  "--why", "Compare corresponding model differences across training budgets", *extra)


def reading(record):
    return record("show", "c1")["relations"][0]


def test_reversal_needs_both_sides_and_keeps_point_uncertainty(record):
    assert relate(record)["relation"]["outcome"] == "incomplete"
    record("run", "add", "e1", "new.loss=2", "old.loss=1")
    assert reading(record)["outcome"] == "incomplete"
    record("run", "add", "e2", "new.loss=1", "old.loss=2")
    row = reading(record)
    assert row["outcome"] == "observed_reversal"
    assert row["source_certainty"] == row["target_certainty"] == "point_only"
    assert row["source_finding"]["comparison"]["estimate"] == 1
    assert row["target_finding"]["comparison"]["estimate"] == -1
    assert "uncertain" in row["summary"]


@pytest.mark.parametrize("target,expected", [("-1[-1.5,-0.5]", "resolved_reversal"), ("-0.1[-0.5,0.3]", "observed_reversal"), ("1[0.5,1.5]", "same_direction")])
def test_intervals_qualify_each_side_instead_of_voting_by_met_rules(record, target, expected):
    for exp, comparison in [("e1", "1[0.5,1.5]"), ("e2", target)]:
        record("analysis", "add", exp, "--compare", f"new/old:loss={comparison}", "--method", "synthetic interval", "--unit", "seed", "--units", "3")
    assert relate(record)["relation"]["outcome"] == expected


def test_alignment_is_explicit_and_does_not_rewrite_the_target_number(record):
    record("criterion", "relate", "c1", "reverses", "c2", "--why", "Undeclared order", error="E_OBJECT_INVALID")
    record("run", "add", "e1", "new.loss=2", "old.loss=1")
    record("run", "add", "e2", "new.loss=3", "old.loss=1")
    row = relate(record, orientation="opposite")["relation"]
    assert row["outcome"] == "observed_reversal"
    assert row["target_direction"] == "negative"
    assert row["target_finding"]["comparison"]["estimate"] == 2
    assert row["declaration"]["orientation"] == "opposite"


def test_a_question_call_tracks_related_evidence_outside_its_direct_scope(record):
    record("run", "add", "e1", "new.loss=2", "old.loss=1")
    record("run", "add", "e2", "new.loss=1", "old.loss=2")
    relate(record)
    decision = record("decide", "q1", "continue", "--why", "Continue to another seed")["object"]
    assert set(decision["basis"]) == {"c1"}
    assert decision["inquiry_basis"]["relations"][0]["target"] == "c2"
    assert not any(i["code"] == "decision_outdated" for i in record("status")["open"])
    record("run", "add", "e2", "new.loss=4", "old.loss=2")
    assert any(i["code"] == "decision_outdated" and i["subject"] == "q1" for i in record("status")["open"])


def test_either_endpoint_exposes_one_relationship(record):
    relate(record)
    record("criterion", "file", "c1", "q1", "--remove", "--why", "Use the second endpoint")
    record("criterion", "file", "c2", "q1", "--why", "Use the second endpoint")
    assert len(record("show", "c2")["relations"]) == 1
    inquiry = record("show", "q1")["inquiry"]
    assert len(inquiry["relations"]) == 1 and inquiry["relations"][0]["source"] == "c1"


def test_frozen_relation_keeps_history_and_retracting_evidence_blocks_it(record):
    record("freeze", "e1", "--why", "Lock the first comparison")
    record("criterion", "relate", "c1", "reverses", "c2", "--orientation", "same", "--why", "Later interpretation", error="E_FROZEN")
    relate(record, "--amend", "--words", "Check both sides.")
    record("retract", "c2", "--why", "The second comparison was invalid")
    assert reading(record)["outcome"] == "integrity"
    record("status", "--fail-on", "integrity", exit_code=1)
    record("criterion", "relate", "c1", "reverses", "c2", "--remove", "--amend", "--why", "Withdraw the comparison")
    relation = record("show", "c1")["object"]["relations"][0]
    assert relation["words"] == "Check both sides." and relation["retracted"]["why"] == "Withdraw the comparison"


def test_withdrawing_a_relation_answers_withdrawn_and_reads_only_a_declared_one(record, capsys):
    relate(record)
    assert main(["criterion", "relate", "c1", "reverses", "c2", "--remove", "--why", "A duplicate declaration"]) == 0
    text = capsys.readouterr().out
    assert "c1: withdrew relation to c2" in text and "cannot be read" not in text
    relate(record, orientation="opposite")
    row = record("criterion", "relate", "c1", "reverses", "c2", "--remove", "--why", "Withdraw the comparison")["relation"]
    assert (row["outcome"], row["integrity"], row["source_finding"]) == ("withdrawn", [], None)
    assert row["declaration"]["orientation"] == "opposite" and row["declaration"]["retracted"]["why"] == "Withdraw the comparison"
    assert relate(record)["relation"]["outcome"] == "incomplete"


def test_a_declaration_back_with_the_other_alignment_is_refused_from_every_surface(record, capsys):
    relate(record, orientation="opposite")
    before = {p: p.read_bytes() for p in (record.root / ".rb").rglob("*.yaml")}
    assert main(["criterion", "relate", "c2", "reverses", "c1", "--orientation", "same", "--why", "Read it from c2", "--json"]) == 2
    [error] = Envelope.model_validate_json(capsys.readouterr().out).errors
    assert error.code == "E_OBJECT_INVALID" and "c1 already declares that it reverses c2 with orientation opposite" in error.message
    assert "rb criterion relate c1 reverses c2 --remove" in error.fix
    state = rabbit_brain.open(record.root)
    with pytest.raises(RBError) as refused:
        state.relate_criterion("c2", "reverses", "c1", orientation="same", why="Read it from c2")
    assert refused.value.code == "E_OBJECT_INVALID"
    server = Mcp("relater")
    try:
        answer = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "criterion_relate", "arguments": {
            "criterion": "c2", "relation": "reverses", "target": "c1", "orientation": "same", "why": "Read it from c2"}}})["result"]
        assert answer["isError"] and answer["structuredContent"]["errors"][0]["code"] == "E_OBJECT_INVALID"
    finally:
        assert server.close() == 0
    assert {p: p.read_bytes() for p in (record.root / ".rb").rglob("*.yaml")} == before
    back = record("criterion", "relate", "c2", "reverses", "c1", "--orientation", "opposite", "--why", "The same alignment, from c2")
    assert back["relation"]["outcome"] != "integrity"


def test_a_changed_arm_resolution_is_an_integrity_problem_not_a_status_crash(record):
    relate(record)
    record("variant", "add", "e2", "another", "--role", "candidate")
    row = reading(record)
    assert row["outcome"] == "integrity"
    assert any(p["code"] == "relation_scope_invalid" for p in row["integrity"])
    assert any(i["code"] == "finding_relation_invalid" for i in record("status")["open"])


def test_same_relation_is_a_noop_and_sdk_uses_the_same_write(record):
    state = rabbit_brain.open(record.root)
    state.relate_criterion("c1", "reverses", "c2", orientation="same", why="Align corresponding comparisons")
    log = (record.root / ".rb/log.jsonl").read_bytes()
    relate(record)
    assert (record.root / ".rb/log.jsonl").read_bytes() == log
    assert reading(record)["declaration"]["via"] == "sdk"


def test_the_sdk_reads_the_relation_the_command_line_and_mcp_answer_with(record):
    """relate_criterion keeps answering with the criterion it wrote; state.relations(<criterion>), or
    criterion.relations(), reads every relation the criterion is a side of, from either side, as rb show reads them
    and as criterion relate answers on the command line and over MCP."""
    record("run", "add", "e1", "new.loss=2", "old.loss=1")
    record("run", "add", "e2", "new.loss=1", "old.loss=2")
    state = rabbit_brain.open(record.root)
    assert state.relations("c1") == [] == state.criterion("c2").relations()
    server = Mcp("relater")
    try:
        answer = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "criterion_relate", "arguments": {
            "criterion": "c1", "relation": "reverses", "target": "c2", "orientation": "same", "why": "Align corresponding comparisons"}}})
    finally:
        assert server.close() == 0
    declared = answer["result"]["structuredContent"]["data"]["relation"]
    [row] = state.relations("c1")
    assert row == declared == record("show", "c2")["relations"][0] and row["outcome"] == "observed_reversal"
    assert state.criterion("c2").relations() == [row] and row["declaration"]["via"] == "mcp"
    assert relate(record)["relation"] == row                            # the command line's answer, the same reading
    withdrawn = state.relate_criterion("c1", "reverses", "c2", why="taken back", remove=True)
    assert withdrawn.id == "c1" and withdrawn.relations[-1].retracted is not None
    assert state.relations("c1") == []
    with pytest.raises(RBError) as refused:
        state.relations("q1")
    assert refused.value.code == "E_OBJECT_NOT_FOUND"


def test_unrelated_metrics_and_levels_cannot_be_called_reversals(record):
    record("criterion", "add", "A level", "-e", "e2", "--metric", "new.loss", "--at-most", "0", "--id", "level")
    record("criterion", "relate", "c1", "reverses", "level", "--orientation", "same", "--why", "Not a difference", error="E_OBJECT_INVALID")
    record("criterion", "add", "Another metric", "-e", "e2", "--metric", "change.cost", "--at-most", "0", "--id", "cost")
    record("criterion", "relate", "c1", "reverses", "cost", "--orientation", "same", "--why", "Different metric", error="E_OBJECT_INVALID")


def test_a_pre_design_question_call_stays_current_on_the_same_evidence(workdir, monkeypatch, capsys):
    as_the_user(monkeypatch)
    fixture = json.loads((Path(__file__).parent / "fixtures/inquiry-before-design.json").read_text())
    for name, content in fixture["files"].items():
        path = workdir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    assert main(["status", "--json"]) == 0
    answer = json.loads(capsys.readouterr().out)
    assert not any(i["code"] == "decision_outdated" for i in answer["data"]["open"])
    assert main(["doctor", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["data"]["outcome"]["passed"]
    for name, content in fixture["files"].items():
        assert (workdir / name).read_text(encoding="utf-8") == content


def test_adoption_cannot_rewrite_a_declared_alignment(record):
    relate(record)
    path = record.root / ".rb/experiments/e1/criteria/c1.yaml"
    original = path.read_text()
    assert "orientation: same" in original
    path.write_text(original.replace("orientation: same", "orientation: opposite"))
    record("doctor", "--adopt", "--why", "Change the comparison order", error="E_ADOPT_AUTHORITY")


def test_a_relation_declared_over_mcp_tells_the_agent_what_changed_since_it_looked(record):
    server = Mcp("relater")

    def call(name, arguments):
        answer = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments}})["result"]
        assert not answer["isError"], answer
        return answer["structuredContent"]

    try:
        call("status", {})
        record("question", "add", "Does it hold at a longer budget?", "--id", "q10")
        answer = call("criterion_relate", {"criterion": "c1", "relation": "reverses", "target": "c2", "orientation": "same",
                                           "why": "Compare corresponding model differences across training budgets"})
        assert answer["data"]["relation"]["declaration"]["via"] == "mcp"
        assert [d["subject"] for d in answer["rb_delta"] if d["kind"] == "write"] == ["q10"]
    finally:
        assert server.close() == 0


def test_a_retraction_says_the_links_and_relations_that_rest_on_what_it_takes_back(record):
    """A question takes back the links that name it; a criterion takes back the questions it is linked to and the
    relations read through it, from either side. Each is said where the retraction says what rested on it."""
    record("question", "add", "A narrower inquiry", "--id", "q3")
    record("criterion", "file", "c2", "q3", "--why", "this comparison bears on the narrower inquiry")
    record("criterion", "add", "Comparison 3", "-e", "e2", "--metric", "change.loss", "--at-most", "0", "--id", "c3")
    relate(record)
    record("criterion", "relate", "c2", "reverses", "c3", "--orientation", "same", "--why", "the longer budget against itself")
    rested = record("retract", "q3", "--why", "the narrower inquiry is dropped")["rested_on"]
    assert rested == ["criterion c2 is linked to it"]
    rested = record("retract", "c2", "--why", "the comparison was set up wrong")["rested_on"]
    assert "it reverses criterion c3" in rested and "criterion c1 reverses it" in rested
    assert "it is linked to question q3" in rested  # the link stays on c2 after its question is retracted
    rested = record("retract", "c1", "--why", "no longer used")["rested_on"]
    assert "it is linked to question q1" in rested and "it reverses criterion c2" in rested


def test_retracting_an_experiment_says_what_rests_on_its_criteria_and_gives_the_repairs_it_leaves(record):
    """An experiment takes back its criteria's question links and the relations read through them, from either side.
    A relation to a criterion of a retracted experiment is taken back from the side still standing, and a criterion of
    a retracted experiment takes no more writes, so the one repair it is given is its own retraction."""
    record("criterion", "file", "c2", "q1", "--why", "the longer budget bears on the question too")
    record("criterion", "add", "Comparison 3", "-e", "e2", "--metric", "change.loss", "--at-most", "0", "--id", "c3")
    relate(record)
    record("criterion", "relate", "c3", "reverses", "c1", "--orientation", "same", "--why", "the third against the first")
    record("run", "add", "e1", "new.loss=2", "old.loss=1")
    record("run", "add", "e2", "new.loss=1", "old.loss=2")
    record("status", "--fail-on", "integrity")
    rested = record("retract", "e2", "--why", "the longer budget leaked its evaluation set")["rested_on"]
    assert "criterion c2 is linked to question q1" in rested
    assert "criterion c1 reverses criterion c2" in rested and "criterion c3 reverses criterion c1" in rested
    items = record("status")["open"]
    [relation] = [i for i in items if i["code"] == "finding_relation_invalid"]
    assert relation["subject"] == "c1"
    assert relation["do"] == 'rb criterion relate c1 reverses c2 --remove --why "<why the relation no longer holds>"'
    gone = {i["subject"]: i["do"] for i in items if i["code"] == "experiment_retracted"}
    assert gone == {c: f'rb retract {c} --why "<its experiment was retracted>"' for c in ("c2", "c3")}
    assert not [i for i in items if i["who"] == "agent" and i["subject"] in ("c2", "c3") and i["code"] != "experiment_retracted"]
    record("status", "--fail-on", "integrity", exit_code=1)
    record("criterion", "relate", "c1", "reverses", "c2", "--remove", "--why", "e2 was taken back")
    for c in ("c2", "c3"):
        record("retract", c, "--why", "its experiment was retracted")
    record("status", "--fail-on", "integrity")


def test_a_relation_that_cannot_be_read_gives_the_repair_its_problem_calls_for(record, capsys, monkeypatch):
    """A changed scope takes the alignment again; a side taken back takes the relation back (with --amend once frozen).
    rb show says what stops the reading, and the command given repairs it."""
    monkeypatch.setenv("RB_ACTOR", "human:ana")
    relate(record)
    record("spec", "set", "e2", "width", "--design-factor")
    [item] = [i for i in record("status")["open"] if i["code"] == "finding_relation_invalid"]
    assert "the comparison scope changed after its alignment was declared" in item["what"]
    assert item["do"] == ('rb criterion relate c1 reverses c2 --orientation "<same or opposite>" '
                          '--why "<why this alignment holds for the scopes as they are>"')
    record("criterion", "relate", "c1", "reverses", "c2", "--orientation", "same", "--why", "the scopes as they are now")
    assert not [i for i in record("status")["open"] if i["code"] == "finding_relation_invalid"]
    record("freeze", "e1", "--why", "lock the rule")
    record("retract", "c2", "--why", "the longer budget was misconfigured")
    [item] = [i for i in record("status")["open"] if i["code"] == "finding_relation_invalid"]
    assert item["what"] == "c1 reverses c2: retracted: the longer budget was misconfigured (c2)"
    assert item["do"] == 'rb criterion relate c1 reverses c2 --remove --why "<why the relation no longer holds>" --amend'
    assert main(["show", "c1"]) == 0
    assert ("c1 reverses c2: The reversal cannot be read until its evidence or declared alignment is repaired. "
            "Integrity: retracted: the longer budget was misconfigured (c2).") in capsys.readouterr().out
    record("status", "--fail-on", "integrity", exit_code=1)
    record("criterion", "relate", "c1", "reverses", "c2", "--remove", "--why", "c2 was taken back", "--amend")
    record("status", "--fail-on", "integrity")


def test_a_pair_declared_from_both_sides_reads_once_with_both_declarations(record, capsys, monkeypatch):
    """The same alignment declared back from the other criterion is one relation: one reading, listing both
    declarations and who made each in its fields, and in its line that both sides declared it, naming no writer; a
    declaration back that reads otherwise stays a reading of its own."""
    monkeypatch.setenv("RB_ACTOR", "human:ana")
    record("run", "add", "e1", "new.loss=2", "old.loss=1")
    record("run", "add", "e2", "new.loss=1", "old.loss=2")
    relate(record)
    monkeypatch.setenv("AI_AGENT", "claude-code")
    record("criterion", "relate", "c2", "reverses", "c1", "--orientation", "same", "--why", "the same pair from the longer budget")
    monkeypatch.delenv("AI_AGENT")
    inquiry = record("show", "q1")["inquiry"]
    [reading] = inquiry["relations"]
    assert (reading["source"], reading["target"], reading["outcome"]) == ("c1", "c2", "observed_reversal")
    assert [(d["criterion"], d["declared_by"]) for d in reading["declarations"]] == [("c1", "human:ana"), ("c2", "agent:claude-code")]
    assert reading["declaration"]["declared_by"] == "human:ana"
    line = "c1 reverses c2, and c2 reverses c1 (declared from both sides): The measured direction reverses; repeatability remains uncertain."
    assert line in inquiry["summary"]
    for subject in ("q1", "c2"):
        assert main(["show", subject]) == 0
        assert capsys.readouterr().out.count("reverses c") == 2  # one line, naming the pair both ways
    page = record.root / "pair.html"
    record("page", "--out", str(page))
    assert "1 declared comparison" in page.read_text(encoding="utf-8")
    record("decide", "q1", "investigate", "--why", "check the reversal again")
    [stored] = record("status")["decisions"][-1]["inquiry_basis"]["relations"]
    assert "declarations" not in stored and stored["declaration"]["declared_by"] == "human:ana"
    assert not any(i["code"] == "decision_outdated" for i in record("status")["open"])
    # a declaration back whose scope changed reads apart, so its problem is not hidden by the other's reading
    record("spec", "set", "e1", "width", "--design-factor")
    record("criterion", "relate", "c1", "reverses", "c2", "--orientation", "same", "--why", "the scopes as they are now")
    readings = record("show", "q1")["inquiry"]["relations"]
    assert len(readings) == 2 and [len(r["declarations"]) for r in readings] == [1, 1]
