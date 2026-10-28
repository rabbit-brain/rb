"""A future record shape must be discovered before an unrelated write, not while printing its result."""
import json

import pytest

import rabbit_brain
from rabbit_brain.errors import RBError
from rabbit_brain.models import Envelope
from rbtest import as_the_user, main, rbyaml


@pytest.fixture
def record(workdir, monkeypatch, capsys):
    as_the_user(monkeypatch)
    monkeypatch.setenv("RB_ACTOR", "human:fixture")

    def cli(*args, error=None):
        code = main([*args, "--json"])
        result = Envelope.model_validate_json(capsys.readouterr().out)
        if error:
            assert code == 2 and not result.ok and result.errors[0].code == error, result
        else:
            assert code == 0 and result.ok, result
        return result.data

    cli.root = workdir
    cli("init", "Future shape fixture", "--no-agents")
    cli("question", "add", "Does the budget hold?", "--id", "q1")
    cli("experiment", "add", "Measured budget", "--id", "e1", "-q", "q1")
    cli("criterion", "add", "Budget", "-e", "e1", "--metric", "score", "--at-least", "0.7", "--id", "c1",
        "-q", "q1", "--when-met", "The declared budget holds.")
    cli("freeze", "e1", "--why", "before measurement")
    cli("run", "add", "e1", "score=0.8")
    cli.decision = cli("decide", "q1", "continue", "--why", "record the current reading")["object"]["id"]
    return cli


def written(record):
    folder = record.root / ".rb"
    return {p.relative_to(folder).as_posix(): p.read_bytes()
            for p in [*folder.rglob("*.yaml"), folder / "log.jsonl"] if "cache" not in p.relative_to(folder).parts}


def future_shape(record, shape, *, logged=True):
    """A synthetic newer writer's field, including a nested declaration and a future decision basis."""
    kind, id = ("decision", record.decision) if shape == "decision" else ("criterion", "c1")
    rel = f".rb/decisions/{id}.yaml" if kind == "decision" else ".rb/experiments/e1/criteria/c1.yaml"
    path = record.root / rel
    doc = rbyaml.loads(path.read_text(encoding="utf-8"))
    if shape == "question_link":
        doc["question_links"][0]["future_interpretation"] = {"version": 2}
    elif shape == "decision":
        doc["future_inquiry_basis"] = {"version": 2, "question": "q1"}
    else:
        doc["future_question_links"] = [{"question": "q1"}]
    path.write_text(rbyaml.dumps(doc), encoding="utf-8")
    if logged:
        entry = {"at": "2099-01-01T00:00:00+00:00", "actor": "human:future", "actor_via": "git-email",
                 "via": "cli", "op": "revise", "kind": kind, "id": id, "path": rel,
                 "sha256": rbyaml.content_sha({k: v for k, v in doc.items() if k not in ("rb", "kind")})}
        with (record.root / ".rb/log.jsonl").open("a", encoding="utf-8") as log:
            log.write(json.dumps(entry) + "\n")
    return path


@pytest.mark.parametrize("shape,command", [
    ("criterion", ("run", "add", "e1", "score=0.9")),
    ("question_link", ("run", "add", "e1", "score=0.9")),
    ("decision", ("run", "add", "e1", "score=0.9")),
    ("decision", ("question", "add", "An unrelated inquiry", "--id", "q2")),
    ("question_link", ("criterion", "add", "Another rule", "-e", "e1", "--metric", "cost", "--at-most", "1",
                       "--id", "c2", "--amend", "--why", "extend the frozen instrument")),
    ("decision", ("project", "title", "Future shape fixture", "--why", "keep the current title")),
])
def test_future_shape_refuses_before_any_record_or_log_mutation(record, shape, command):
    future_shape(record, shape)
    before = written(record)
    record(*command, error="E_STATE_NEWER")
    assert written(record) == before


def test_repeated_sdk_calls_recheck_a_changed_file_before_writing(record):
    state = rabbit_brain.open(agent="future-shape-test")
    state.add_question("Prime the bridge's file cache", id="q2")
    future_shape(record, "question_link")
    before = written(record)
    for text in ("First refused note", "Second refused note"):
        with pytest.raises(RBError) as caught:
            state.add_note(text)
        assert caught.value.code == "E_STATE_NEWER"
        assert written(record) == before


def test_corruption_and_restore_keep_their_existing_behavior(record):
    before = written(record)
    path = future_shape(record, "question_link", logged=False)
    doc = rbyaml.loads(path.read_text(encoding="utf-8"))
    del doc["created_by"]
    path.write_text(rbyaml.dumps(doc), encoding="utf-8")
    record("show", "c1", error="E_STATE_CORRUPT")
    record("doctor", "--restore")
    assert path.read_bytes() == before[path.relative_to(record.root / ".rb").as_posix()]
    record("run", "add", "e1", "score=0.9")
