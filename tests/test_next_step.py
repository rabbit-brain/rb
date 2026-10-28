"""A question's Next step and a resource's role and acquisition (BP 1c), as the shell's document holds them
(questions[].next_step, resources{}.role, resources{}.acquisition), computed on read from what the record holds.

The record is test_reproduce.py's INT8 ResNet-50 on ImageNet record, written with rb's own commands as human:ana, and
read back through the document rb page (the Owner tier) and rb open (the Live tier) build under RB_TEST_SHELL. next_step.rs
and acquisition.rs compute the fields; shell/model.rs puts them in the document. Until the merge point that carries both,
tests/fixtures/shell/merged-phases.txt does not list P2 and these skip with "pending P2" (RB_SHELL_CONTRACT_MERGED=P2 runs
them before then); the unit tests (cargo test reproduce:: next_step:: acquisition::) hold the same rules meanwhile."""
import json

from test_reproduce import int8, needs  # noqa: F401  (int8 is the record's fixture)


def question(doc: dict, qid: str) -> dict:
    return next(q for q in doc["questions"] if q["id"] == qid)


def test_next_step_is_a_request_in_words(int8):
    """Two seeds of e1 against a rule that needs three: q1's Next step asks for one more top1 measurement on e1, named by
    its short title, of the arms rb's suggested run names, in words, never as the command rb status gives."""
    needs("P2")
    int8.ok("criterion", "add", "INT8 loses at most a point of top-1 over three seeds", "-e", "e1", "-q", "q1", "--metric", "change.top1",
            "--at-least", "-0.01", "--min-n", "3", "--id", "c3")
    for tier in ("owner", "live"):
        said = question(int8.document(tier), "q1")["next_step"]
        assert said == "run one more top1 measurement on INT8 against FP32 and record it in rb", (tier, said)
        assert not any(w in said for w in ("rb run add", "--", "<value>")), said


def test_a_question_set_aside_has_no_next_step(int8):
    """A question parked says no Next step, though rb status still lists the rule's missing seed."""
    needs("P2")
    int8.ok("criterion", "add", "INT8 loses at most a point of top-1 over three seeds", "-e", "e1", "-q", "q1", "--metric", "change.top1",
            "--at-least", "-0.01", "--min-n", "3", "--id", "c3")
    int8.ok("decide", "q1", "park", "--why", "the GPUs are taken this week")
    assert question(int8.document("owner"), "q1")["next_step"] == ""


def test_a_resource_says_what_it_was_read_as_and_how_to_get_it_again(int8):
    """Each resource's role is what the runs that read it read it as, in plain words, and its acquisition its recorded
    recipe handed apart into the command and the file and line it cites; one with no recipe has none. The same on both
    tiers, and nothing of it is stored in the record."""
    needs("P2")
    before = int8.record_files()
    for tier in ("owner", "live"):
        res = int8.document(tier)["resources"]
        got = {k: (v["role"], v["acquisition"]) for k, v in res.items()}
        assert got == {
            "imagenet-val": ("evaluation data", {"command": "python get_data.py --split val", "from": "scripts/README.md:5"}),
            "imagenet-calib": ("training data", {"command": "python get_data.py --calib 512", "from": "scripts/README.md:6"}),
            "resnet50-fp32": ("weights", None),
            "ckpt-int8": ("model", {"command": "python quantize.py --seed 1 --calib 512", "from": "scripts/README.md:8"}),
        }, (tier, json.dumps(got, indent=1))
    assert int8.record_files() == before
