"""A question call stays current while what it was made on stays the same.

A call on a question keeps a snapshot of the question's reading. Whether it still holds compares the numbers, rule
outcomes, links, interpretations and problem codes in that snapshot, through one projection of the stored snapshot
and of the snapshot now. rb's notes and sentences, the problems' texts and the question's place in the tree are not
compared: a run the criterion does not read, a verified setting, a comment in a config, a repeated declaration, a
move of the question or a reworded sentence leave the call current. A snapshot or a relation scope a newer rb wrote
reads as needing that rb (state_newer), never as a call made on an earlier reading. State a newer rb wrote elsewhere
fails every gate, and the call is still judged on its own reading.

Every name and number here is synthetic.
"""
import json

import pytest

from rabbit_brain.models import Envelope
from rbtest import as_the_user, main, rbyaml


@pytest.fixture
def record(workdir, monkeypatch, capsys):
    as_the_user(monkeypatch)
    monkeypatch.setenv("RB_ACTOR", "human:ana")

    def cli(*argv, error=None, exit_code=0):
        code = main([*argv, "--json"])
        env = Envelope.model_validate_json(capsys.readouterr().out)
        if error:
            assert code == 2 and not env.ok and env.errors[0].code == error, (argv, code, env.errors)
        else:
            assert code == exit_code and env.ok, (argv, code, env.errors)
        return env.data

    cli.root = workdir
    cli("init", "Width study", "--no-agents")
    cli("question", "add", "Does the wider model lower val_loss?", "--id", "q1")
    cli("experiment", "add", "Width screen", "--id", "e1", "-q", "q1", "--baseline", "base", "--candidate", "wide")
    cli("metric", "add", "val_loss", "--minimize", "--unit", "nats")
    cli("spec", "set", "e1", "seed", "--per-run")
    return cli


def linked(record, *extra, id="c1"):
    record("criterion", "add", "wide lowers val_loss", "-e", "e1", "--metric", "change.val_loss", "--at-most", "0", "--id", id,
           "-q", "q1", "--when-met", "Width lowers val_loss on this screen.", *extra)


def seeds(record, *metrics, flags=(), wide=(0.9, 0.88, 0.91)):
    return [record("run", "add", "e1", "base.val_loss=1.0", f"wide.val_loss={value}", *metrics, "--set", f"seed={seed}", *flags)["object"]["id"]
            for seed, value in enumerate(wide, 1)]


def call(record, action="accept"):
    return record("decide", "q1", action, "--why", "width lowers val_loss on this screen")["object"]


def outdated(record):
    """The calls status reads as made on an earlier reading; a snapshot this rb made never reads as a newer rb's."""
    status = record("status")
    assert not [item for item in status["open"] if item["code"] == "state_newer"]
    return [item["what"] for item in status["open"] if item["code"] == "decision_outdated"]


def codes(record, code):
    return [item["subject"] for item in record("status")["open"] if item["code"] == code]


def notes(record, criterion="c1"):
    return [(c["code"], c["text"]) for c in record("show", criterion)["finding"]["caveats"]]


def written(record):
    folder = record.root / ".rb"
    return {p.relative_to(folder).as_posix(): p.read_bytes()
            for p in [*folder.rglob("*.yaml"), folder / "log.jsonl"] if "cache" not in p.relative_to(folder).parts}


def rewrite(record, rel, kind, id, change):
    """A record file as another rb wrote it: changed, and logged as that rb logs a write."""
    path = record.root / rel
    doc = rbyaml.loads(path.read_text(encoding="utf-8"))
    change(doc)
    path.write_text(rbyaml.dumps(doc), encoding="utf-8")
    entry = {"at": "2099-01-01T00:00:00+00:00", "actor": "human:ana", "actor_via": "RB_ACTOR", "via": "cli", "op": "revise",
             "kind": kind, "id": id, "path": rel, "sha256": rbyaml.content_sha({k: v for k, v in doc.items() if k not in ("rb", "kind")})}
    with (record.root / ".rb/log.jsonl").open("a", encoding="utf-8") as log:
        log.write(json.dumps(entry) + "\n")


def test_a_run_outside_the_criterions_selectors_leaves_the_call_current(record):
    """(a) The only change is the note that one run is outside c1's selectors. The same call made again repeats the
    first, so rb refuses it, until a run c1 reads moves the reading."""
    record("spec", "set", "e1", "width", "--per-run")
    linked(record, "--where", "width=512")
    seeds(record, flags=("--set", "width=512"))
    call(record)
    before = notes(record)
    record("run", "add", "e1", "base.val_loss=1.0", "wide.val_loss=0.95", "--set", "seed=1", "--set", "width=256")
    assert notes(record) != before and outdated(record) == []
    unchanged = written(record)
    record("decide", "q1", "accept", "--why", "width lowers val_loss on this screen", error="E_OBJECT_INVALID")
    assert written(record) == unchanged
    record("run", "add", "e1", "base.val_loss=1.0", "wide.val_loss=0.95", "--set", "seed=4", "--set", "width=512")
    assert [what.rsplit(": ", 1)[1] for what in outdated(record)] == ["c1"]
    call(record)
    assert outdated(record) == []


def test_a_measurement_superseded_for_another_metric_leaves_the_call_current(record):
    """(b) Retracting a run's wall_s while c1 reads val_loss only adds a note."""
    linked(record)
    runs = seeds(record, "base.wall_s=10", "wide.wall_s=12")
    call(record)
    before = notes(record)
    record("retract", runs[0], "--scope", "wall_s", "--why", "the timer was off")
    assert notes(record) != before and outdated(record) == []
    record("retract", runs[1], "--why", "the seed ran on the wrong split")
    assert outdated(record)


def test_verifying_a_setting_leaves_the_call_current(record):
    """(c) Verifying a setting changes nothing in the spec, and so nothing a call was made on."""
    (record.root / "configs").mkdir()
    (record.root / "configs/train.yaml").write_text("optim:\n  lr: 0.001\n", encoding="utf-8")
    record("spec", "set", "e1", "lr", "0.001", "--source", "configs/train.yaml#optim.lr", "--no-verify")
    linked(record)
    seeds(record)
    call(record)
    before = notes(record)
    record("spec", "verify", "e1")
    assert notes(record) != before and outdated(record) == []


def test_a_comment_in_a_setting_source_leaves_the_call_current(record):
    """(d) The value still reads the same; only the line it is on moved."""
    config = record.root / "configs/train.yaml"
    config.parent.mkdir()
    config.write_text("optim:\n  lr: 0.001\n", encoding="utf-8")
    record("spec", "set", "e1", "lr", "--source", "configs/train.yaml#optim.lr")
    linked(record)
    seeds(record)
    call(record)
    before = notes(record)
    config.write_text("# tuned by hand\noptim:\n  lr: 0.001\n", encoding="utf-8")
    assert notes(record) != before and outdated(record) == []
    config.write_text("optim:\n  lr: 0.002\n", encoding="utf-8")
    assert outdated(record)


def test_declaring_a_design_factor_again_leaves_the_call_current(record):
    """(e) The same declaration again rewrites when it was set, which only the design note quotes."""
    record("spec", "set", "e1", "width", "--per-run")
    record("spec", "set", "e1", "width", "--design-factor", "--why", "a fixed grid of widths")
    linked(record)
    for width in (256, 512):
        for seed in (1, 2):
            record("run", "add", "e1", "base.val_loss=1.0", f"wide.val_loss=0.9{seed}", "--set", f"seed={seed}", "--set", f"width={width}")
    call(record)
    before = notes(record)
    record("spec", "set", "e1", "width", "--design-factor", "--why", "a fixed grid of widths")
    assert notes(record) != before and outdated(record) == []


def test_moving_the_question_leaves_the_call_and_the_gate_it_settled_current(record):
    """(f) A reframing is not a fresh start: the call stays current, and so does the gate it settled."""
    record("question", "add", "Is the wider model worth it?", "--id", "q0")
    linked(record)
    record("criterion", "add", "wide saves at least 20% of the steps", "-e", "e1", "--metric", "saving_pct", "--at-least", "20",
           "--role", "gate", "--id", "g1", "-q", "q1")
    seeds(record, "saving_pct=18")
    call(record, "park")
    assert outdated(record) == [] and codes(record, "gate_missed") == []
    record("question", "move", "q1", "--parent", "q0", "--why", "frame it under the cost question")
    assert outdated(record) == [] and codes(record, "gate_missed") == []


def test_a_snapshot_worded_by_another_build_reads_the_same(record):
    """(g) Another build's notes, method, rule sentence and problem texts, and a snapshot kept before the rule key, are
    the same reading; a stored number that differs is not."""
    linked(record)
    seeds(record)
    decision = call(record)
    rel = f".rb/decisions/{decision['id']}.yaml"

    def reworded(doc):
        row = doc["inquiry_basis"]["criteria"]["c1"]
        del row["rule_key"]
        row["statement"] = "the wider model lowers val_loss"
        finding = row["finding"]
        finding["rule"] = "change.val_loss at most 0"
        finding["comparison"]["method"] = "paired differences, t interval"
        finding["comparison"]["why_no_interval"] = "said another way"
        for note in finding["caveats"]:
            note["text"] = "said another way: " + note["text"]

    rewrite(record, rel, "decision", decision["id"], reworded)
    assert outdated(record) == []
    rewrite(record, rel, "decision", decision["id"], lambda doc: doc["inquiry_basis"]["criteria"]["c1"]["finding"]["comparison"].update(estimate=-0.5))
    assert [what.rsplit(": ", 1)[1] for what in outdated(record)] == ["c1"]


@pytest.mark.parametrize("newer", ["version", "key"])
def test_a_snapshot_from_a_newer_rb_reads_as_newer_never_as_outdated(record, newer):
    """(h) This rb names the record as needing a newer rb and fails every gate. It does not advise a new call, and
    refuses one that would write over the newer state."""
    linked(record)
    record("question", "add", "Is it cheap enough?", "--id", "q2")
    record("criterion", "add", "cost", "-e", "e1", "--metric", "wide.cost", "--at-most", "5", "--id", "c2", "-q", "q2")
    seeds(record, "wide.cost=4")
    decision = call(record)
    record("decide", "q2", "accept", "--why", "cheap enough")

    def future(doc):
        if newer == "version":
            doc["inquiry_basis"]["version"] = 2
        else:
            doc["inquiry_basis"]["criteria"]["c1"]["finding"]["population"] = {"unit": "seed"}

    rewrite(record, f".rb/decisions/{decision['id']}.yaml", "decision", decision["id"], future)
    status = record("status")
    [item] = [i for i in status["open"] if i["code"] == "state_newer"]
    assert item["what"].startswith(f"1 record here has a part only a newer rb reads: .rb/decisions/{decision['id']}.yaml: ")
    assert item["what"].endswith("which this rb does not know") and "written by a newer rb" not in item["what"]
    assert not [i for i in status["open"] if i["code"] == "decision_outdated"]
    assert all(status["gate"].values())
    before = written(record)
    record("decide", "q1", "accept", "--why", "make it again", error="E_STATE_NEWER")
    assert written(record) == before


def test_a_relation_scope_from_a_newer_rb_reads_as_newer_never_as_outdated(record):
    """(i) Only the keys this rb knows are compared; the record reads as needing the rb that wrote the others."""
    linked(record)
    record("criterion", "add", "wide lowers val_loss at a small scale", "-e", "e1", "--metric", "change.val_loss", "--at-most", "0", "--id", "c2")
    record("criterion", "relate", "c1", "reverses", "c2", "--orientation", "same", "--why", "the same arms on both scales")
    seeds(record)
    call(record)
    rewrite(record, ".rb/experiments/e1/criteria/c1.yaml", "criterion", "c1",
            lambda doc: doc["relations"][0]["source_scope"].update(population="seed"))
    status = record("status")
    [item] = [i for i in status["open"] if i["code"] == "state_newer"]
    assert item["what"] == ("1 record here has a part only a newer rb reads: .rb/experiments/e1/criteria/c1.yaml: criterion c1 declares a "
                            "relation scope with source_scope.population, which this rb does not know")
    assert not [i for i in status["open"] if i["code"] in ("decision_outdated", "finding_relation_invalid")]
    assert not [p for p in record("show", "c1")["relations"][0]["integrity"] if p["code"] == "relation_outdated"]


def newer_line(record):
    """A write of a kind only a newer rb knows, logged as that rb logs it."""
    entry = {"at": "2099-01-01T00:00:00+00:00", "actor": "human:ana", "actor_via": "RB_ACTOR", "via": "cli", "op": "add",
             "kind": "benchmark", "id": "b1", "path": ".rb/benchmarks/b1.yaml", "sha256": "0" * 64}
    with (record.root / ".rb/log.jsonl").open("a", encoding="utf-8") as log:
        log.write(json.dumps(entry) + "\n")


@pytest.mark.parametrize("moved", [False, True], ids=["newer-state-alone", "with-a-moved-reading"])
def test_state_a_newer_rb_wrote_elsewhere_leaves_the_call_judged_on_its_own_reading(record, moved):
    """A log line only a newer rb reads fails every gate under its own item. Alone it moves no call; a linked reading
    that moved still reads as moved, on the question as on the experiment."""
    linked(record)
    record("criterion", "add", "wide lowers val_loss at a small scale", "-e", "e1", "--metric", "change.val_loss", "--at-most", "0", "--id", "c2")
    record("criterion", "relate", "c1", "reverses", "c2", "--orientation", "same", "--why", "the same arms on both scales")
    seeds(record)
    call(record)
    record("decide", "e1", "continue", "--why", "keep the screen going")
    if moved:
        seeds(record, wide=(1.2, 1.25, 1.3))
    newer_line(record)
    status = record("status")
    assert [i for i in status["open"] if i["code"] == "state_newer"] and all(status["gate"].values())
    assert sorted(i["subject"] for i in status["open"] if i["code"] == "decision_outdated") == (["e1", "q1"] if moved else [])
    assert [d["current"] for d in status["decisions"] if d["subject"] == "q1"] == [not moved]
    assert not [i for i in status["open"] if i["code"] == "finding_relation_invalid"]


def test_a_linked_criterion_only_a_newer_rb_reads_is_not_read_as_moved(record):
    """A newer rb rewrote c2 in its own format. This rb names the record as needing that rb and fails every gate; it
    does not read c2 as gone from the reading the call was made on."""
    linked(record)
    record("criterion", "add", "wide stays cheap", "-e", "e1", "--metric", "wide.cost", "--at-most", "5", "--id", "c2", "-q", "q1")
    seeds(record, "wide.cost=4")
    call(record)
    rewrite(record, ".rb/experiments/e1/criteria/c2.yaml", "criterion", "c2", lambda doc: doc.update(rb=2))
    status = record("status")
    assert [i for i in status["open"] if i["code"] == "state_newer"] and all(status["gate"].values())
    assert not [i for i in status["open"] if i["code"] == "decision_outdated"]
