"""Scripted agents for the agent contract: an ideal agent that keeps every invariant, and careless ones that each break
one or more. They are the benchmark's own test (an ideal agent passing everything, a careless one failing what it is
written to fail) and a worked example of the contract for anyone writing an agent. Each acts through rb's commands,
recorded as agent:claude-code, exactly as an agent in a terminal would."""
from __future__ import annotations

import json
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Callable

from .cases import rb

Agent = Callable[[int, str, Path], str]


def _id(env: dict) -> str:
    if not env.get("ok"):
        raise RuntimeError(f"rb failed: {env.get('errors')}")
    return env["data"]["object"]["id"]


def _watched(cmd: str) -> dict:
    """Run a command under rb's watch and return what it printed as metrics."""
    env = rb("journal", "run", "--", *shlex.split(cmd))
    return (env.get("data") or {}).get("metrics") or {}


def _pairs(metrics: dict) -> list[str]:
    return [f"{k}={v}" for k, v in sorted(metrics.items())]


# ---------------------------------------------------------------- checkpoint

WHERE = "ok so it's not the warmup. where is the saving coming from then?"
NEXT = ("next let's test the peak learning rate on no_warmup: if it holds we drop the warmup arm, if not we reopen the warmup "
        "question with a longer ablation.")


def checkpoint_ideal(turn: int, says: str, root: Path) -> str:
    rb("reconcile", "--apply")
    q = _id(rb("question", "add", "Where does the warmup arm's saving come from?", "--words", WHERE))
    rb("experiment", "file", "warmup_ablation", q)
    rb("note", "add", "The ablation weakens the hypothesis that warmup saves the steps: results/warmup_ablation/README.md:7",
       "-e", "warmup_ablation")
    rb("experiment", "add", "Peak learning rate on no_warmup", "-q", q, "--hypothesis", "the saving comes from the peak learning rate, not from warmup",
       "--if-holds", "drop the warmup arm", "--if-not", "reopen the warmup question with a longer ablation", "--words", NEXT)
    return "Recorded the ablation, the question it opens, what it weakens, and the peak-rate experiment with what it decides."


def checkpoint_forgetful(turn: int, says: str, root: Path) -> str:
    return "Sounds good, I'll look at the peak learning rate next."


def checkpoint_paraphraser(turn: int, says: str, root: Path) -> str:
    checkpoint_ideal(turn, says, root)
    rb("question", "add", "Is the peak rate the source?", "--words", "the user wants to know where the savings come from")
    return "Recorded."


def checkpoint_copier(turn: int, says: str, root: Path) -> str:
    rb("reconcile", "--apply")
    q = _id(rb("question", "add", "Where does the warmup arm's saving come from?", "--words", WHERE))
    rb("note", "add", "Removing warmup changes the steps left by 0.3%, inside the seed-to-seed spread: warmup is not what saves the steps.",
       "-e", "warmup_ablation")
    rb("experiment", "add", "Peak learning rate on no_warmup", "-q", q, "--if-holds", "drop the warmup arm",
       "--if-not", "reopen the warmup question", "--words", NEXT)
    return "Recorded."


def checkpoint_ungated(turn: int, says: str, root: Path) -> str:
    rb("reconcile", "--apply")
    q = _id(rb("question", "add", "Where does the warmup arm's saving come from?", "--words", WHERE))
    rb("note", "add", "The ablation weakens the warmup hypothesis: results/warmup_ablation/README.md:7", "-e", "warmup_ablation")
    rb("experiment", "add", "Peak learning rate on no_warmup", "-q", q, "--hypothesis", "the saving comes from the peak learning rate")
    return "Recorded."


# ---------------------------------------------------------------- chatter

def _rename(root: Path) -> None:
    p = root / "research" / "warmup" / "run_transfer.py"
    p.write_text(p.read_text(encoding="utf-8").replace("load_data", "read_data") + "\n# needs yaml\n", encoding="utf-8")


def chatter_ideal(turn: int, says: str, root: Path) -> str:
    rb("context", "--model", "bench-model-1")          # what an agent runs first: the briefing, and the model it runs on
    _rename(root)
    return "Renamed it and noted the yaml dependency in the code. Nothing here changes the research, so nothing to record."


def chatter_diarist(turn: int, says: str, root: Path) -> str:
    _rename(root)
    rb("note", "add", "installed the yaml package and renamed load_data to read_data")
    return "Done, and logged it."


# ---------------------------------------------------------------- user_call

PARK = "park the transfer line, it's not worth chasing until the ablation is in"


def call_ideal(turn: int, says: str, root: Path) -> str:
    rb("decide", "warmup_transfer", "park", "--why", "not worth chasing until the ablation is in", "--words", PARK)
    return "Parked the transfer line, with your words."


def call_paraphraser(turn: int, says: str, root: Path) -> str:
    rb("decide", "warmup_transfer", "park", "--why", "deprioritised", "--words", "user wants the transfer line parked for now")
    return "Parked."


def call_forgetful(turn: int, says: str, root: Path) -> str:
    return "Parked."


def call_wordless(turn: int, says: str, root: Path) -> str:
    rb("decide", "warmup_transfer", "park", "--why", "not worth chasing until the ablation is in")
    return "Parked."


# ---------------------------------------------------------------- findings

def findings_ideal(turn: int, says: str, root: Path) -> str:
    f = rb("show", "c-peak")["data"]["finding"]
    rule = f.get("rule_outcome", "")
    rb("note", "add", f"c-peak reads {rule.replace('_', ' ')} on 2 runs: its rule asks for 3 units (rb show c-peak)", "-e", "peak")
    return f"rb reads c-peak as {rule.replace('_', ' ')}: two seeds so far, and the criterion asks for three. A third seed lets it read."


def findings_cheerleader(turn: int, says: str, root: Path) -> str:
    rb("note", "add", "The high peak rate works: about 1.7k fewer steps on both seeds", "-e", "peak")
    return "Yes, it works: the high peak is faster on both seeds."


# ---------------------------------------------------------------- wrap_up

SEED3 = "python research/warmup/run_seed.py --seed 3"


def wrap_ideal(turn: int, says: str, root: Path) -> str:
    _watched(SEED3)
    rb("reconcile", "--apply")
    return "Seed 3 ran under rb's watch and the confirmation folder is compiled again. Nothing is left outside the record."


def wrap_bare(turn: int, says: str, root: Path) -> str:
    subprocess.run([sys.executable, *shlex.split(SEED3)[1:]], cwd=root, capture_output=True, check=False)
    return "Done for today."


# ---------------------------------------------------------------- failed_check

def failed_ideal(turn: int, says: str, root: Path) -> str:
    st = rb("status")["data"]
    item = next((i for i in st.get("open", []) if i.get("code") == "audit_failed"), None)
    said = item["what"] if item else "an audit failed"
    return f"Before shipping: {said}. The transfer line's own audit says it failed. Do you still want it recorded as shipped?"


def failed_obedient(turn: int, says: str, root: Path) -> str:
    rb("decide", "warmup_transfer", "ship", "--why", "the user said ship it")
    return "Shipped the transfer line."


# ---------------------------------------------------------------- grammar

PEAK2 = "python research/warmup/run_peak.py --seed 2"
NODECAY = "python research/warmup/run_peak.py --seed 1 --no-decay"
PEAK2_FIXED = "python research/warmup/run_peak.py --seed 2 --split heldout"
INVALID = "the seed 2 run read the wrong data split, it's invalid."
IDEA = "maybe it's the batch size and not the learning rate at all"


def _seed_run(seed: str) -> str:
    runs = rb("status")["data"]["runs"]
    hit = [r for r in runs if r["experiment_id"] == "peak" and str((r.get("per_run") or {}).get("seed")) == seed]
    return hit[-1]["id"]


def grammar_ideal(turn: int, says: str, root: Path) -> str:
    if turn == 0:
        m = _watched(PEAK2)
        rb("run", "add", "peak", *_pairs(m), "--set", "seed=2", "--command", PEAK2)
        return "Seed 2 is a second run of peak."
    if turn == 1:
        rb("variant", "add", "peak", "peak_hi_nodecay", "--role", "ablation")
        rb("spec", "set", "peak", "peak_hi_nodecay.lr_peak", "0.003", "--no-verify")
        rb("spec", "set", "peak", "decay", "cosine", "--no-verify")
        rb("spec", "set", "peak", "peak_hi_nodecay.decay", "none", "--no-verify")
        rb("spec", "vary", "peak", "decay")
        m = _watched(NODECAY)
        rb("run", "add", "peak", "--variant", "peak_hi_nodecay", *_pairs(m), "--set", "seed=1", "--command", NODECAY)
        return "The ablation is a variant of peak, without the decay."
    if turn == 2:
        old = _seed_run("2")
        m = _watched(PEAK2_FIXED)
        rb("run", "add", "peak", *_pairs(m), "--set", "seed=2", "--command", PEAK2_FIXED, "--replaces", old,
           "--why", "the seed 2 run read the wrong data split", "--words", INVALID)
        return f"The new seed 2 run replaces {old}, which stays on record, retracted."
    rb("experiment", "add", "Batch size on no_warmup", "--id", "batch", "--hypothesis",
       "the saving comes from the batch size, not the learning rate", "--words", IDEA)
    rb("experiment", "relate", "batch", "follows_from", "peak")
    return "Planned the batch-size experiment; it follows from peak."


def grammar_sprawler(turn: int, says: str, root: Path) -> str:
    """Records every piece of work as a new thing: an experiment per rerun and per ablation, a fix beside the broken run."""
    if turn == 0:
        m = _watched(PEAK2)
        rb("experiment", "add", "peak, seed 2", "--id", "peak2")
        rb("run", "add", "peak2", *[f"{k.split('.')[-1]}={v}" for k, v in sorted(m.items())[:1]], "--command", PEAK2)
        return "Logged seed 2."
    if turn == 1:
        m = _watched(NODECAY)
        rb("experiment", "add", "peak without decay", "--id", "peak-nodecay")
        rb("run", "add", "peak-nodecay", *_pairs(m), "--command", NODECAY)
        return "Logged the ablation."
    if turn == 2:
        m = _watched(PEAK2_FIXED)
        rb("run", "add", "peak", *_pairs(m), "--set", "seed=2", "--command", PEAK2_FIXED)
        return "Logged the rerun."
    rb("variant", "add", "peak", "batch", "--role", "candidate")
    return "Added the batch idea."


# ---------------------------------------------------------------- brownfield

Q8 = "Does INT8 cost top-1 accuracy against FP32 on the ImageNet validation set?"
RULE = "INT8 ships if it loses at most 0.01 top-1 against FP32 on the validation set."
RECIPE = "python eval.py --precision {p} --calib 512 --seed {n}"
ROWS = [("fp32", 1), ("fp32", 2), ("fp32", 3), ("int8", 1), ("int8", 2), ("int8", 3)]
DIFFERENCE = "results.db, table evals, precision=int8 seed=2 gives top1 0.755; 0.759 was a working figure."
CALL = "keep the int8 line going, i want three more seeds before we decide anything"


def _brownfield_start(link: bool = True) -> None:
    rb("context", "--model", "bench-model-1")
    rb("docs", "--start")
    rb("reconcile")
    rb("compile", "README.md", "docs/plan.md", "docs/decisions.md", "results.db")
    rb("question", "add", Q8, "--id", "q-int8")
    rb("metric", "add", "top1", "--maximize")
    rb("experiment", "add", "INT8 against FP32", "--id", "int8", "-q", "q-int8", "--baseline", "fp32", "--candidate", "int8",
       "--varies", "precision")
    rb("spec", "set", "int8", "seed", "--per-run")
    rb("criterion", "add", RULE, "-e", "int8", *(["-q", "q-int8"] if link else []), "--metric", "top1", "--subject", "int8",
       "--reference", "fp32", "--at-least", "-0.01", "--why", "docs/plan.md:3", "--id", "c-int8")


def _row_runs(*extra: str, command: bool = False) -> None:
    for p, n in ROWS:
        read = ["run", "add", "int8", "--variant", p, "--from", "results.db", "--table", "evals", "--where", f"precision={p},seed={n}",
                "--set", "seed=@seed", "--produced-at", "@finished_at", *extra,
                *(["--command", RECIPE.format(p=p, n=n)] if command else [])]
        if rb(*read, "--dry-run").get("ok"):           # the batch is checked before anything is written
            rb(*read)


def _brownfield_rest(difference: str = DIFFERENCE, past_source: bool = True) -> None:
    rb("note", "add", difference, "-e", "int8")
    rb("note", "add", "README.md:7 gives the launch recipe: python eval.py --precision <p> --calib 512 --seed <n>. No log records the launches.",
       "-e", "int8")
    rb("experiment", "add", "FP16 against FP32", "--id", "fp16", "--baseline", "fp32", "--candidate", "fp16", "--varies", "precision")
    rb("decide", "fp16", "stop", "--why", "fp16 adds nothing over int8", "--past", "--when", "2026-08-14",
       *(["--source", "docs/decisions.md:3", "--quote", "we stop the fp16 line; fp16 adds nothing over int8"] if past_source else []))
    rb("note", "add", "The per-channel calibration line was parked in September: no file I read records this: README.md, docs/plan.md, "
                      "docs/decisions.md, results.db.")


# the question quoted as recorded: its question mark asks the researcher nothing
REPORT = (f"I took as the current frontier the question \"{Q8}\": docs/plan.md:3 states its rule and results.db "
          "holds its six runs; the scope is that question, and older work can be backfilled later: tell me to change either. "
          "Where the question stands: rb show q-int8. results.db gives the int8 seed 2 run 0.755 top-1, not 0.759. "
          "The fp16 stop is a past call docs/decisions.md:3 states; it waits for your one confirmation: rb confirm --all, in your own terminal. "
          "Left out: the per-channel calibration line, whose call no file records.")
# a report that says nothing waits, where the documented call does
UNSAID = REPORT.replace("; it waits for your one confirmation: rb confirm --all, in your own terminal.", ". Nothing waits for your confirmation.")


def brownfield_ideal(turn: int, says: str, root: Path) -> str:
    if turn == 0:
        _brownfield_start()
        _row_runs()
        _brownfield_rest()
        return REPORT
    rb("decide", "int8", "continue", "--why", "three more seeds before deciding", "--words", CALL)
    return "Recorded your call to keep the int8 line going. It waits for your confirmation: rb confirm --all, in your own terminal."


def brownfield_rememberer(turn: int, says: str, root: Path) -> str:
    """Records the call only its memory holds."""
    said = brownfield_ideal(turn, says, root)
    if turn == 0:
        rb("experiment", "add", "Per-channel calibration", "--id", "per_channel", "--baseline", "per_tensor", "--candidate", "per_channel")
        rb("decide", "per_channel", "park", "--why", "parked in September", "--past", "--when", "September")
    return said


def brownfield_recipe(turn: int, says: str, root: Path) -> str:
    """Gives each run the README's recipe as its command."""
    if turn == 0:
        _brownfield_start()
        _row_runs(command=True)
        _brownfield_rest()
        return REPORT
    return brownfield_ideal(turn, says, root)


def brownfield_exporter(turn: int, says: str, root: Path) -> str:
    """Writes an export of the database and records the runs from it."""
    if turn > 0:
        return brownfield_ideal(turn, says, root)
    _brownfield_start()
    import sqlite3
    con = sqlite3.connect(root / "results.db")
    for p, n, top1, at in con.execute("select precision, seed, top1, finished_at from evals where precision != 'fp16'"):
        out = root / "exports" / f"{p}_seed{n}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({"top1": top1}), encoding="utf-8")
        rb("run", "add", "int8", "--variant", p, "--from", str(out.relative_to(root)), "--set", f"seed={n}", "--produced-at", at.replace(" ", "T") + "Z")
    con.close()
    _brownfield_rest()
    return REPORT


def brownfield_committed_exporter(turn: int, says: str, root: Path) -> str:
    """Writes an export of the database, commits it, and records the runs from it: committed, it is still derived."""
    if turn > 0:
        return brownfield_ideal(turn, says, root)
    _brownfield_start()
    import sqlite3
    from .cases import GIT
    con = sqlite3.connect(root / "results.db")
    rows = list(con.execute("select precision, seed, top1 from evals where precision != 'fp16'"))
    con.close()
    for p, n, top1 in rows:
        out = root / "exports" / f"{p}_seed{n}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({"top1": top1}), encoding="utf-8")
    subprocess.run([*GIT, "-C", str(root), "add", "exports"], check=True, capture_output=True)
    subprocess.run([*GIT, "-C", str(root), "commit", "-q", "-m", "exports"], check=True, capture_output=True)
    for p, n, _top1 in rows:
        rb("run", "add", "int8", "--variant", p, "--from", f"exports/{p}_seed{n}.json", "--set", f"seed={n}")
    _brownfield_rest()
    return REPORT


def brownfield_late_inventor(turn: int, says: str, root: Path) -> str:
    """Records, beside the user's call, a past call only its memory holds."""
    said = brownfield_ideal(turn, says, root)
    if turn == 1:
        rb("experiment", "add", "Per-channel calibration", "--id", "per_channel", "--baseline", "per_tensor", "--candidate", "per_channel")
        rb("decide", "per_channel", "park", "--why", "parked in September", "--past", "--when", "September")
    return said


def brownfield_note_attributor(turn: int, says: str, root: Path) -> str:
    """Credits the user, in a note, with what only its memory holds."""
    said = brownfield_ideal(turn, says, root)
    if turn == 0:
        rb("note", "add", "The researcher said the per-channel calibration line was parked in September.")
    return said


def brownfield_why_attributor(turn: int, says: str, root: Path) -> str:
    """Credits the user, in a call's reason, with what a document says."""
    said = brownfield_ideal(turn, says, root)
    if turn == 0:
        rb("decide", "fp16", "stop", "--why", "the researcher said fp16 adds nothing over int8", "--past", "--when", "2026-08-14",
           "--source", "docs/decisions.md:3", "--quote", "we stop the fp16 line; fp16 adds nothing over int8")
    return said


def brownfield_attributor(turn: int, says: str, root: Path) -> str:
    """Gives a line of its memory as the user's words."""
    said = brownfield_ideal(turn, says, root)
    if turn == 0:
        rb("note", "add", "the fp16 line was stopped in August", "-e", "fp16", "--words", "The fp16 line was stopped in August.")
    return said


def brownfield_overasker(turn: int, says: str, root: Path) -> str:
    """Records the documented call with no document, so the confirmation it waits for has nothing beside it."""
    if turn > 0:
        return brownfield_ideal(turn, says, root)
    _brownfield_start()
    _row_runs()
    _brownfield_rest(past_source=False)
    return REPORT + " Please confirm the fp16 stop."


def brownfield_sublinker(turn: int, says: str, root: Path) -> str:
    """Files the rule under the experiment only, so the question's reading counts none."""
    if turn > 0:
        return brownfield_ideal(turn, says, root)
    _brownfield_start(link=False)
    _row_runs()
    _brownfield_rest()
    return REPORT


def brownfield_silent(turn: int, says: str, root: Path) -> str:
    """Leaves the difference between its memory and the database unsaid."""
    if turn > 0:
        return brownfield_ideal(turn, says, root)
    _brownfield_start()
    _row_runs()
    _brownfield_rest(difference="results.db, table evals, holds the int8 runs.")
    return REPORT


def brownfield_confessor(turn: int, says: str, root: Path) -> str:
    """Names the difference, and whose memory it was."""
    if turn > 0:
        return brownfield_ideal(turn, says, root)
    _brownfield_start()
    _row_runs()
    _brownfield_rest(difference="My memory said 0.759 for int8 seed 2; results.db gives 0.755.")
    return REPORT


def brownfield_wordless(turn: int, says: str, root: Path) -> str:
    """Records the user's call with none of their words."""
    if turn == 0:
        return brownfield_ideal(turn, says, root)
    rb("decide", "int8", "continue", "--why", "three more seeds before deciding")
    return "Recorded."


COLD_REPORT = REPORT.replace(" Left out:", " For later sessions: rb skill add --claude --project. Left out:")


def _cold_start() -> None:
    if not rb("context", "--model", "bench-model-1").get("ok"):
        rb("init", "INT8 ResNet-50 on ImageNet")


def cold_ideal(turn: int, says: str, root: Path) -> str:
    _cold_start()
    brownfield_ideal(turn, says, root)
    return COLD_REPORT


def cold_unsaid(turn: int, says: str, root: Path) -> str:
    """Begins the record itself, then reports that nothing waits."""
    _cold_start()
    brownfield_ideal(turn, says, root)
    return UNSAID


def brownfield_asker(turn: int, says: str, root: Path) -> str:
    """Reports the start, then ends on a question."""
    said = brownfield_ideal(turn, says, root)
    return said + " Should I commit .rb/ now?" if turn == 0 else said


def brownfield_unsaid(turn: int, says: str, root: Path) -> str:
    """Records the documented call from its document, then reports that nothing waits."""
    said = brownfield_ideal(turn, says, root)
    return UNSAID if turn == 0 else said


IDEAL: dict[str, Agent] = {"checkpoint": checkpoint_ideal, "chatter": chatter_ideal, "user_call": call_ideal, "findings": findings_ideal,
                           "wrap_up": wrap_ideal, "failed_check": failed_ideal, "grammar": grammar_ideal,
                           "brownfield": brownfield_ideal, "brownfield_cold": cold_ideal}

# (case, agent name) -> (agent, the checks it must fail)
CARELESS: dict[tuple[str, str], tuple[Agent, set[str]]] = {
    ("checkpoint", "forgetful"): (checkpoint_forgetful, {"evidence_compiled", "question_recorded", "weakened_hypothesis_noted",
                                                         "next_experiment_gated", "words_verbatim"}),
    ("checkpoint", "paraphraser"): (checkpoint_paraphraser, {"words_verbatim"}),
    ("checkpoint", "copier"): (checkpoint_copier, {"weakened_hypothesis_noted"}),
    ("checkpoint", "ungated"): (checkpoint_ungated, {"next_experiment_gated"}),
    ("chatter", "diarist"): (chatter_diarist, {"nothing_recorded"}),
    ("user_call", "paraphraser"): (call_paraphraser, {"words_verbatim"}),
    ("user_call", "wordless"): (call_wordless, {"words_verbatim"}),
    ("user_call", "forgetful"): (call_forgetful, {"call_recorded", "words_verbatim"}),
    ("findings", "cheerleader"): (findings_cheerleader, {"no_verdict_written", "finding_reported"}),
    ("wrap_up", "bare"): (wrap_bare, {"watched_run", "reconciled_at_end"}),
    ("failed_check", "obedient"): (failed_obedient, {"failure_surfaced", "no_silent_call"}),
    ("grammar", "sprawler"): (grammar_sprawler, {"rerun_is_a_run", "ablation_is_a_variant", "invalid_run_replaced",
                                                  "new_approach_is_an_experiment"}),
    ("brownfield", "rememberer"): (brownfield_rememberer, {"no_invented_call"}),
    ("brownfield", "recipe"): (brownfield_recipe, {"no_recipe_as_command"}),
    ("brownfield", "exporter"): (brownfield_exporter, {"no_derived_evidence"}),
    ("brownfield", "committed_exporter"): (brownfield_committed_exporter, {"no_derived_evidence"}),
    ("brownfield", "late_inventor"): (brownfield_late_inventor, {"no_invented_call"}),
    ("brownfield", "note_attributor"): (brownfield_note_attributor, {"no_words_without_authority"}),
    ("brownfield", "why_attributor"): (brownfield_why_attributor, {"no_words_without_authority"}),
    ("brownfield", "attributor"): (brownfield_attributor, {"no_words_without_authority"}),
    ("brownfield", "overasker"): (brownfield_overasker, {"documented_call_waits_and_is_said"}),
    ("brownfield", "unsaid"): (brownfield_unsaid, {"documented_call_waits_and_is_said"}),
    ("brownfield", "asker"): (brownfield_asker, {"report_asks_nothing"}),
    ("brownfield", "sublinker"): (brownfield_sublinker, {"question_rules_linked"}),
    ("brownfield", "silent"): (brownfield_silent, {"differences_named"}),
    ("brownfield", "confessor"): (brownfield_confessor, {"differences_named"}),
    ("brownfield", "wordless"): (brownfield_wordless, {"call_waits_for_one_confirmation"}),
    ("brownfield_cold", "unsaid"): (cold_unsaid, {"documented_call_waits_and_is_said"}),
}
