"""rb never offers a command that would take away a miss it has just named: the repeat form (population.repeat) is offered
only for a setting that could be a counter, never for a condition nor for a setting at whose value the rule misses. What
matters is never cut where a reader reads alone: the page lists every problem of a card, and rb status keeps in view the
note its integrity line points to. And --commit @<column> reads the row's own commit, or is refused. Every name and
number is synthetic: an INT8 ResNet-50 on ImageNet, recorded by human:ana."""
import json
import sqlite3
import subprocess
from pathlib import Path

import pytest

from rabbit_brain.models import Envelope
from rbtest import as_the_user, main


@pytest.fixture
def rb(tmp_path, monkeypatch, capsys):
    as_the_user(monkeypatch)
    monkeypatch.setenv("RB_ACTOR", "human:ana")
    root = tmp_path / "int8"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    monkeypatch.chdir(root)

    def run(*argv, ok=True, text=False):
        if text:
            code = main(list(argv), typed=True)
            return code, capsys.readouterr().out
        code = main([*argv, "--json"], typed=True)
        env = Envelope.model_validate_json(capsys.readouterr().out)
        if ok:
            assert code in (0, 1) and env.ok, (argv, code, env.errors)
        return env

    run("init", "INT8 ResNet-50", "--no-agents")
    run("metric", "add", "top1", "--maximize", "--unit", "percent")
    return run


def one_model_on_eval_sets(rb, values, *, rows):
    rb("experiment", "add", "INT8 on several eval sets", "--id", "e1", "--purpose", "the eval sets", "--baseline", "int8")
    rb("spec", "set", "e1", "seed", "--per-run")
    rb("spec", "set", "e1", "population.unit", "seed", "--no-verify")
    rb("spec", "set", "e1", "valset", "--per-run")
    rb("criterion", "add", "INT8 keeps 75 on every set", "-e", "e1", "--metric", "top1", "--each", "--at-least", "75", "--id", "c1")
    for seed in (1, 2, 3):
        for v, x in zip(values, rows):
            rb("run", "add", "e1", f"top1={x + seed / 100}", "--set", f"seed={seed}", "--set", f"valset={v}", "--produced-at", "2026-05-01")


def test_no_repeat_form_is_offered_for_a_condition(rb):
    one_model_on_eval_sets(rb, ["imagenet", "imagenet-v2", "imagenet-r"], rows=[76.0, 76.5, 77.0])
    code, out = rb("show", "--long", "c1", text=True)
    assert "pooled over 3 valset values" in out
    assert "a rule with --where valset=<value> reads one" in out
    assert "population.repeat" not in out, out


def test_the_repeat_form_is_offered_for_a_counter_and_said_to_be_for_counters_only(rb):
    one_model_on_eval_sets(rb, ["0", "1", "2"], rows=[76.0, 76.5, 77.0])
    code, out = rb("show", "--long", "c1", text=True)
    note = [line for line in out.splitlines() if "pooled in one reading" in line][0]
    assert note.index("--where valset=<value> reads one") < note.index("population.repeat"), note
    assert "The repeat form is for a counter only (a seed, an epoch, a repeat's number), never a condition" in note
    assert "rb spec set e1 population.repeat valset --no-verify says so" in note


def test_no_repeat_form_is_offered_for_a_setting_at_whose_value_the_rule_misses(rb):
    # the counter-like values hold a miss at one of them: folding them would take that miss away
    one_model_on_eval_sets(rb, ["0", "1", "2"], rows=[74.0, 76.5, 77.0])
    rb("criterion", "add", "INT8 keeps 75 on average", "-e", "e1", "--metric", "top1", "--at-least", "75", "--id", "c2")
    code, out = rb("show", "--long", "c2", text=True)
    assert "valset=0" in out and "alone would not meet the rule" in out, out
    assert "population.repeat" not in out, out


def test_a_pairing_offers_the_repeat_form_for_its_counter_never_for_a_condition(rb):
    rb("experiment", "add", "INT8 against FP32", "--id", "e2", "--purpose", "the sweep", "--baseline", "fp32", "--candidate", "int8")
    rb("spec", "set", "e2", "batch", "--per-run")
    rb("spec", "set", "e2", "epoch", "--per-run")
    rb("criterion", "add", "INT8 loses at most 1 point", "-e", "e2", "--metric", "change.top1", "--at-least", "-1", "--id", "c3")
    for batch in ("small", "large"):
        for epoch in (0, 1):
            for v, x in (("fp32", 76.0), ("int8", 75.5)):
                rb("run", "add", "e2", f"{v}.top1={x + epoch / 10}", "--set", f"batch={batch}", "--set", f"epoch={epoch}", "--produced-at", "2026-05-01")
    f = rb("show", "--long", "c3").data["finding"]
    said = f["comparison"]["pairing"]["said"]
    assert "paired by batch and epoch" in said, said
    assert "where epoch only numbers repeats, rb spec set e2 population.repeat epoch --no-verify" in said, said
    assert "batch,epoch" not in said and "population.repeat batch" not in said and "<that setting>" not in said, said
    assert f["comparison"]["pairing"]["command"] == "rb spec set e2 population.repeat epoch --no-verify"


def test_a_pairing_by_conditions_alone_offers_no_repeat_form(rb):
    rb("experiment", "add", "INT8 against FP32", "--id", "e3", "--purpose", "the sweep", "--baseline", "fp32", "--candidate", "int8")
    rb("spec", "set", "e3", "valset", "--per-run")
    rb("criterion", "add", "INT8 loses at most 1 point", "-e", "e3", "--metric", "change.top1", "--at-least", "-1", "--id", "c4")
    for valset in ("imagenet", "imagenet-v2", "imagenet-r"):
        for v, x in (("fp32", 76.0), ("int8", 75.5)):
            rb("run", "add", "e3", f"{v}.top1={x}", "--set", f"valset={valset}", "--produced-at", "2026-05-01")
    pairing = rb("show", "--long", "c4").data["finding"]["comparison"]["pairing"]
    assert "paired by valset" in pairing["said"] and "population.repeat" not in pairing["said"], pairing["said"]
    assert pairing["command"] is None


def test_the_page_lists_every_problem_of_a_card(rb):
    rb("experiment", "add", "INT8 against FP32", "--id", "e4", "--purpose", "the sweep", "--baseline", "fp32", "--candidate", "int8", "--varies", "precision")
    rb("spec", "set", "e4", "int8.calib", "--unknown")
    rb("spec", "set", "e4", "fp32.calib", "512", "--no-verify")
    rb("criterion", "add", "INT8 loses at most 1 point", "-e", "e4", "--metric", "change.top1", "--at-least", "-1", "--id", "c5")
    rb("run", "add", "e4", "fp32.top1=76.0", "int8.top1=75.6", "--produced-at", "2026-05-01")
    problems = [p["text"] for p in rb("show", "--long", "c5").data["finding"]["integrity"]]
    assert len(problems) == 2, problems
    rb("page", "--out", "page.html")
    page = Path("page.html").read_text(encoding="utf-8")
    assert "(and 1 more)" not in page
    import html
    for p in problems:
        assert html.escape(p, quote=False) in page or html.escape(p) in page, p


def test_rb_status_keeps_the_note_its_integrity_line_points_to(rb):
    rb("experiment", "add", "INT8 against FP32", "--id", "e5", "--purpose", "the sweep", "--baseline", "fp32", "--candidate", "int8")
    rb("spec", "set", "e5", "seed", "--per-run")
    rb("spec", "set", "e5", "problems", "--per-run")
    rb("spec", "set", "e5", "population.unit", "seed", "--no-verify")
    rb("criterion", "add", "INT8 loses at most 1 point", "-e", "e5", "--metric", "change.top1", "--at-least", "-1", "--id", "c6")
    Path("out").mkdir()
    for seed, (v, x, n) in enumerate([("fp32", 76.0, 500), ("int8", 75.4, 250)]):
        d = Path("out") / v
        d.mkdir()
        (d / "metrics.json").write_text(json.dumps({v: {"top1": x}}), encoding="utf-8")
        (d / "config.yaml").write_text(f"seed: 1\nproblems: {n}\ndirty: {'true' if v == 'int8' else 'false'}\n", encoding="utf-8")
        rb("run", "add", "e5", "--from", str(d / "metrics.json"), "--config", str(d / "config.yaml"), "--set", "seed=1", "--set", f"problems={n}", "--produced-at", "2026-05-01")
    # a run left out, so the notes are more than rb status shows
    rb("run", "add", "e5", "fp32.top1=70", "--set", "seed=9", "--set", "problems=250", "--produced-at", "2026-05-01")
    code, out = rb("status", "--full", text=True)
    block = out[out.index("\n  c6      INT8 loses at most 1 point\n") + 1 :]
    block = "\n".join([block.splitlines()[0]] + [line for line in block.splitlines()[1:]][: next((i for i, line in enumerate(block.splitlines()[1:]) if not line.startswith("          ")), None)])
    assert "has a config that says the code had uncommitted changes when it ran, as said below" in block, block
    assert "says dirty: true: the code had uncommitted changes when it ran" in block, block


def write_ledgers(sha):
    Path("results").mkdir()
    rows = [{"run_id": "a1", "top1": 75.6, "sha": sha, "who": "ana"}]
    Path("results/ledger.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    Path("results/ledger.csv").write_text(f"run_id,top1,sha,who\na1,75.6,{sha},ana\n", encoding="utf-8")
    db = sqlite3.connect("results/runs.db")
    db.execute("create table runs (run_id text, top1 real, sha text, who text)")
    db.execute("insert into runs values ('a1', 75.6, ?, 'ana')", (sha,))
    db.commit()
    db.close()


@pytest.mark.parametrize("store", ["ledger.jsonl", "ledger.csv", "runs.db"])
def test_commit_at_a_column_reads_the_row_s_own_commit(rb, store):
    sha = "41e79187d2f794c98c869b6d5a2f7ec4811d125d"
    write_ledgers(sha)
    rb("experiment", "add", "INT8 alone", "--id", "e6", "--purpose", "the ledger", "--baseline", "int8")
    table = ["--table", "runs"] if store.endswith(".db") else []
    env = rb("run", "add", "e6", "--from", f"results/{store}", *table, "--where", "run_id=a1", "--commit", "@sha", "--produced-at", "2026-05-01")
    assert env.data["object"]["receipt"]["produced"]["commit"] == sha
    run = Path(".rb/experiments/e6/runs") / f"{env.data['object']['id']}.yaml"
    assert "@sha" not in run.read_text(encoding="utf-8")
    # a column that holds no commit is refused, and nothing is written
    env = rb("run", "add", "e6", "--from", f"results/{store}", *table, "--where", "run_id=a1", "--commit", "@who", "--again", "--why", "again", ok=False)
    assert not env.ok and env.errors[0].code == "E_OBJECT_INVALID" and "'ana' in who, which is not a commit" in env.errors[0].message


def test_commit_at_a_column_with_no_row_is_refused(rb):
    rb("experiment", "add", "INT8 alone", "--id", "e7", "--purpose", "typed", "--baseline", "int8")
    env = rb("run", "add", "e7", "top1=75.6", "--commit", "@sha", ok=False)
    assert not env.ok and env.errors[0].code == "E_OBJECT_INVALID" and "names a column of the row --from reads" in env.errors[0].message
    assert not list(Path(".rb/experiments/e7").glob("runs/*.yaml"))


def test_no_repeat_form_is_offered_where_it_would_read_away_a_miss(rb):
    """A paired reading that misses its rule, where reading each run as its own unit would meet it: no command is offered
    (review: the offer turned "criterion not met" into "estimate meets criterion" with no evidence changed)."""
    rb("experiment", "add", "INT8 against FP32", "--id", "e8", "--purpose", "the sweep", "--baseline", "fp32", "--candidate", "int8")
    rb("spec", "set", "e8", "seed", "--per-run")
    rb("criterion", "add", "INT8 loses nothing", "-e", "e8", "--metric", "change.top1", "--at-least", "0", "--id", "c8")
    for seed, (a, b) in enumerate([(100, 0), (0, 40), (0, 40)], start=1):
        rb("run", "add", "e8", f"fp32.top1={a}", "--set", f"seed={seed}", "--produced-at", "2026-05-01")
        rb("run", "add", "e8", f"int8.top1={b}", "--set", f"seed={seed}", "--produced-at", "2026-05-01")
    rb("run", "add", "e8", "int8.top1=500", "--set", "seed=4", "--produced-at", "2026-05-01")
    f = rb("show", "--long", "c8").data["finding"]
    assert f["rule_outcome"] == "misses"
    assert f["comparison"]["pairing"]["command"] is None, f["comparison"]["pairing"]
    assert "population.repeat" not in f["comparison"]["pairing"]["said"]


def test_where_a_rule_misses_no_note_offers_a_command_that_counts_runs_otherwise(rb):
    """An --each rule that misses at one unit: neither the may-be-unit note nor the pairing offers a command that would
    fold the missing unit away (review: population.unit epoch turned "not met" into "meet")."""
    rb("experiment", "add", "INT8 alone", "--id", "e9", "--purpose", "the epochs", "--baseline", "int8")
    rb("spec", "set", "e9", "seed", "--per-run")
    rb("spec", "set", "e9", "epoch", "--per-run")
    rb("criterion", "add", "INT8 keeps 70 in every run", "-e", "e9", "--metric", "top1", "--each", "--at-least", "70", "--id", "c9")
    for (seed, epoch, x) in [(1, 0, 65), (2, 0, 80), (3, 0, 71), (1, 1, 90), (2, 1, 70), (3, 1, 75)]:
        rb("run", "add", "e9", f"top1={x}", "--set", f"seed={seed}", "--set", f"epoch={epoch}", "--produced-at", "2026-05-01")
    f = rb("show", "--long", "c9").data["finding"]
    assert f["rule_outcome"] == "misses"
    texts = " ".join(c["text"] for c in f["caveats"])
    assert "population.unit" not in texts and "population.repeat" not in texts, texts
