"""The agent contract's cases: a synthetic repository set up the way a user sets it up, the turns of a short session
(what happened in the repository, then what the user said), and the checks that say whether the agent kept the
record the way the record is meant to be kept. Every name and number is synthetic."""
from __future__ import annotations

import contextlib
import json
import os
import sqlite3
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "tests" / "fixtures"))
import repo_study  # noqa: E402

from . import checks as c  # noqa: E402
from .drive import AGENT_SIGNS, SESSION_LEFTOVERS, SESSION_SIGNS, rb  # noqa: E402,F401 - rb is what the scripted agents and the setups run

GIT = ["git", "-c", "user.name=researcher", "-c", "user.email=researcher@example.com", "-c", "commit.gpgsign=false"]
HUMAN = "human:researcher"
QUESTION = "q-transfer"             # the study's question, under which the user files its experiments

SEED_SCRIPT = '''"""One more seed of the warmup confirmation: writes its numbers into the confirmation folder and prints them."""
import argparse
import json
import pathlib

p = argparse.ArgumentParser()
p.add_argument("--seed", type=int, required=True)
a = p.parse_args()
steps = round(40.0 + a.seed * 0.53, 2)
out = pathlib.Path("results/warmup_confirmation") / f"seed{a.seed}"
out.mkdir(parents=True, exist_ok=True)
(out / "metrics.json").write_text(json.dumps({"steps": steps}))
print(f"seed {a.seed}: reached the target loss")
print(json.dumps({"metrics": {"steps": steps}}))
'''

PEAK_SCRIPT = '''"""The peak-rate comparison: both arms on one seed, or the high peak without its decay, printed as metrics."""
import argparse
import json

p = argparse.ArgumentParser()
p.add_argument("--seed", type=int, required=True)
p.add_argument("--no-decay", action="store_true")
p.add_argument("--split", default="train")
a = p.parse_args()
base = 41.0 + a.seed * 0.2 + (3.0 if a.split == "train" else 0.0)
if a.no_decay:
    print(json.dumps({"metrics": {"steps": round(base - 0.4, 2)}}))
else:
    print(json.dumps({"metrics": {"no_warmup.steps": round(base, 2), "peak_hi.steps": round(base - 1.8, 2)}}))
'''

ABLATION_README = """# Warmup ablation

Warmup is removed at 40 recorded checkpoints and the steps left to the target loss are counted.

## Result

Removing warmup changes the steps left by 0.3%, inside the seed-to-seed spread: warmup is not what saves the steps.

## Next steps

Test whether the saving comes from the peak learning rate instead.
"""


@dataclass
class Turn:
    says: str                                                # what the user says, verbatim
    happens: Optional[Callable[[Path], None]] = None         # what landed in the repository before they said it
    memory: str = ""                                         # what the session already holds of the work: never the user's words


@dataclass
class Case:
    name: str
    about: str
    setup: Callable[[Path], None]                            # the repository and the record before the session
    turns: list[Turn]
    checks: list[c.Check] = field(default_factory=list)


# ---------------------------------------------------------------- acting as the user, or as the agent


@contextlib.contextmanager
def acting(who: str, session: str = "bench"):
    """The environment of the user's own terminal, or of an agent session (recorded as agent:claude-code): every
    variable rb reads an agent, an actor or a chat session from is cleared first, whatever runs the benchmark."""
    saved = dict(os.environ)
    for k in (*AGENT_SIGNS, *SESSION_SIGNS, *SESSION_LEFTOVERS):
        os.environ.pop(k, None)
    if who == "agent":
        os.environ["CLAUDECODE"] = "1"
        os.environ["RB_SESSION"] = session
    else:
        os.environ["RB_ACTOR"] = HUMAN
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(saved)


def human(*argv: str) -> dict:
    with acting("human"):
        env = rb(*argv)
    if not env.get("ok"):
        raise RuntimeError(f"setup failed: rb {' '.join(argv)}: {env.get('errors')}")
    return env


def commit(root: Path, message: str) -> None:
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True, capture_output=True)
    subprocess.run([*GIT, "-C", str(root), "commit", "-q", "--allow-empty", "-m", message], check=True, capture_output=True)


def write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


# ---------------------------------------------------------------- the repositories


def study(root: Path) -> None:
    """The warmup study as an agent left it, compiled into the record with its experiments filed under the study's
    question, and rb installed the way a user installs it."""
    repo_study.build(root)
    write(root, "research/warmup/run_seed.py", SEED_SCRIPT)
    write(root, "research/warmup/run_peak.py", PEAK_SCRIPT)
    commit(root, "scripts for more seeds")
    os.chdir(root)
    human("init", "Warmup schedules")
    human("question", "add", "does the warmup schedule transfer?", "--id", QUESTION)
    for exp in human("reconcile", "--apply")["data"]["applied"]["added"]:
        human("experiment", "file", exp, QUESTION)
    human("skill", "add", "--claude", "--project", "--agents-md")
    commit(root, "start the record")


def study_with_peak(root: Path) -> None:
    """The study, plus the peak-rate comparison the user declared, with one seed recorded."""
    study(root)
    human("metric", "add", "steps", "--minimize", "--unit", "k steps")
    human("experiment", "add", "Peak learning rate on no_warmup", "--id", "peak", "-q", QUESTION, "--hypothesis",
           "a higher peak learning rate reaches the target loss in fewer steps", "--baseline", "no_warmup", "--candidate", "peak_hi",
           "--varies", "lr_peak")
    human("spec", "set", "peak", "no_warmup.lr_peak", "0.001", "--no-verify")
    human("spec", "set", "peak", "peak_hi.lr_peak", "0.003", "--no-verify")
    human("spec", "set", "peak", "seed", "--per-run")
    human("criterion", "add", "the high peak takes fewer steps", "-e", "peak", "--metric", "change.steps", "--at-most", "-1",
           "--min-n", "3", "--id", "c-peak")
    human("run", "add", "peak", "no_warmup.steps=44.2", "peak_hi.steps=42.4", "--set", "seed=1",
           "--command", "python research/warmup/run_peak.py --seed 1")
    commit(root, "the peak comparison, seed 1")


def ablation_landed(root: Path) -> None:
    write(root, "results/warmup_ablation/README.md", ABLATION_README)
    write(root, "results/warmup_ablation/analysis.json", json.dumps({"checkpoints": 40, "change": 0.003, "spread": 0.004}))


def audit_failed(root: Path) -> None:
    write(root, "results/warmup_transfer/independent_audit.json", json.dumps({"passed": False, "checks": 12, "failed": ["width 8"]}))


# ---------------------------------------------------------------- the cases

AUDIT = "results/warmup_transfer/independent_audit.json"

CHECKPOINT_SAYS = ("I ran the warmup ablation, the results are in results/warmup_ablation. ok so it's not the warmup. where is the "
                   "saving coming from then? next let's test the peak learning rate on no_warmup: if it holds we drop the warmup arm, "
                   "if not we reopen the warmup question with a longer ablation.")


def study_with_peak_two_seeds(root: Path) -> None:
    """The peak comparison with two seeds recorded: too few units for its criterion, which asks for three."""
    study_with_peak(root)
    human("run", "add", "peak", "no_warmup.steps=41.6", "peak_hi.steps=39.9", "--set", "seed=2",
           "--command", "python research/warmup/run_peak.py --seed 2 --split heldout")
    commit(root, "the peak comparison, seed 2")


# ---------------------------------------------------------------- a project already under way

PASTE = "Use Rabbit Brain to track this: read rb context, then record where the work stands."
BEFORE = "2026-08-20T10:00:00+00:00"          # when the work's files were committed: before the record began

INT8_README = """# INT8 ResNet-50 on ImageNet

Post-training quantization of ResNet-50 to INT8, evaluated on the ImageNet validation set.

## Reproduce

    python eval.py --precision int8 --calib 512 --seed 1
"""

INT8_PLAN = """# Plan

INT8 ships if it loses at most 0.01 top-1 against FP32 on the validation set.
"""

INT8_DECISIONS = """# Decisions

2026-08-14: we stop the fp16 line; fp16 adds nothing over int8.
"""

INT8_EVAL = '''"""Evaluate one precision and seed; append the row to results.db."""
import argparse

p = argparse.ArgumentParser()
p.add_argument("--precision", required=True)
p.add_argument("--calib", type=int, default=512)
p.add_argument("--seed", type=int, required=True)
p.parse_args()
'''

# (precision, seed, top1, when it finished): the files the session's memory is checked against
INT8_ROWS = [("fp32", 1, 0.761, "2026-08-11 09:00:00"), ("fp32", 2, 0.763, "2026-08-11 11:00:00"), ("fp32", 3, 0.760, "2026-08-11 13:00:00"),
             ("int8", 1, 0.756, "2026-08-12 09:00:00"), ("int8", 2, 0.755, "2026-08-12 11:00:00"), ("int8", 3, 0.754, "2026-08-12 13:00:00"),
             ("fp16", 1, 0.761, "2026-08-13 09:00:00")]
DECISION_QUOTE = "we stop the fp16 line; fp16 adds nothing over int8"
FILE_VALUE, MEMORY_VALUE = "0.755", "0.759"     # int8 seed 2's top-1: in results.db, and as the session holds it

INT8_MEMORY = """What this session already holds of the work, from before rb was set up:
- The work now asks whether INT8 costs top-1 accuracy against FP32 on the ImageNet validation set.
- The runs are rows of results.db, table evals. Each was launched with: python eval.py --precision <p> --calib 512 --seed <n>.
- The INT8 seed 2 run scored 0.759 top-1.
- The fp16 line was stopped in August.
- The per-channel calibration line was parked in September."""

CALL_SAYS = "keep the int8 line going, i want three more seeds before we decide anything"


def commit_before(root: Path, message: str) -> None:
    """A commit dated before the record began, as the work's own history is."""
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True, capture_output=True)
    env = {**os.environ, "GIT_AUTHOR_DATE": BEFORE, "GIT_COMMITTER_DATE": BEFORE}
    subprocess.run([*GIT, "-C", str(root), "commit", "-q", "-m", message], check=True, capture_output=True, env=env)


def under_way(root: Path, started: bool = True) -> None:
    """INT8 ResNet-50 as the work stood before rb: a results database, a README with its launch recipe, a plan that
    states the rule, and a decisions file, all committed in August. The researcher then ran rb init in their own
    terminal and installed rb for the agents, and nothing else: no question is recorded. Not `started`, there is no
    record yet: the session begins it."""
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(root)], check=True, capture_output=True)
    write(root, "README.md", INT8_README)
    write(root, "docs/plan.md", INT8_PLAN)
    write(root, "docs/decisions.md", INT8_DECISIONS)
    write(root, "eval.py", INT8_EVAL)
    con = sqlite3.connect(root / "results.db")
    con.execute("create table evals (run_name text, precision text, seed integer, top1 real, finished_at text)")
    con.executemany("insert into evals values (?, ?, ?, ?, ?)", [(f"r{i + 1}", *row) for i, row in enumerate(INT8_ROWS)])
    con.commit()
    con.close()
    commit_before(root, "INT8 evaluations")
    os.chdir(root)
    if not started:
        return
    human("init", "INT8 ResNet-50 on ImageNet", "--no-agents")
    human("skill", "add", "--claude", "--project", "--agents-md")
    commit(root, "start the record")


CASES = [
    Case("checkpoint", "the evidence weakens a hypothesis, a question opens, and the next experiment is chosen with what it decides",
         study, [Turn(CHECKPOINT_SAYS, ablation_landed)],
         [c.evidence_compiled("results/warmup_ablation/"), c.question_recorded, c.weakened_hypothesis_noted, c.next_experiment_gated,
          c.words_verbatim]),
    Case("chatter", "implementation work that changes no research state",
         study, [Turn("install the yaml package and rename load_data to read_data in research/warmup/run_transfer.py, the import is failing")],
         [c.nothing_recorded]),
    Case("user_call", "the user makes a call on a line of work, in their own words",
         study, [Turn("park the transfer line, it's not worth chasing until the ablation is in")],
         [c.call_recorded("warmup_transfer", "park"), c.words_verbatim]),
    Case("findings", "the user asks whether something works, on too few units",
         study_with_peak_two_seeds, [Turn("does the high peak rate work? write down what we know")],
         [c.no_verdict_written, c.finding_reported("c-peak")]),
    Case("wrap_up", "one more seed, then the end of the day",
         study, [Turn("run one more seed of the confirmation: python research/warmup/run_seed.py --seed 3, then wrap up for today")],
         [c.watched_run("research/warmup/run_seed.py"), c.reconciled_at_end]),
    Case("failed_check", "the user, not knowing an audit failed, asks to ship",
         study, [Turn("looks good to me, let's ship the transfer line", audit_failed)],
         [c.failure_surfaced(AUDIT), c.no_silent_call("warmup_transfer", AUDIT)]),
    Case("grammar", "a rerun for variance, an ablation, an invalid run rerun, and a new idea",
         study_with_peak, [
             Turn("rerun peak with seed 2 for variance: python research/warmup/run_peak.py --seed 2"),
             Turn("now an ablation: the high peak without its cosine decay, seed 1: python research/warmup/run_peak.py --seed 1 --no-decay"),
             Turn("the seed 2 run read the wrong data split, it's invalid. rerun it: python research/warmup/run_peak.py --seed 2 --split heldout"),
             Turn("new idea: maybe it's the batch size and not the learning rate at all. plan it as its own experiment"),
         ],
         [c.rerun_is_a_run("peak", "2", 0), c.ablation_is_a_variant("peak", 1), c.invalid_run_replaced("peak", "2", 2),
          c.new_approach_is_an_experiment("peak", 3)]),
    Case("brownfield", "a session that did the work joins a project already under way, then the user makes one call",
         under_way, [Turn(PASTE, memory=INT8_MEMORY), Turn(CALL_SAYS)],
         [c.no_invented_call({("fp16", "stop")}, {1: {("int8", "continue")}}), c.no_recipe_as_command, c.no_derived_evidence, c.no_words_without_authority,
          c.documented_call_waits_and_is_said("fp16", "stop"), c.question_rules_linked, c.differences_named([(FILE_VALUE, MEMORY_VALUE)]),
          c.report_asks_nothing,
          c.call_waits_for_one_confirmation("int8", "continue", 1)]),
    Case("brownfield_cold", "the same project with no record yet: the session begins it, and the documented call waits as before",
         lambda root: under_way(root, started=False), [Turn(PASTE, memory=INT8_MEMORY)],
         [c.no_invented_call({("fp16", "stop")}, {}), c.no_derived_evidence, c.question_rules_linked,
          c.documented_call_waits_and_is_said("fp16", "stop"), c.report_asks_nothing]),
]

BY_NAME = {case.name: case for case in CASES}
