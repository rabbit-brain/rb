"""rb's command line, declared: every command, argument and help text, as the argparse tree it is.

This is where the command line is changed. `python rust/gen/gen_cli_spec.py` writes the tree to cli_spec.json beside
this file, which the binary carries and parses by (argparse.rs reads a command line as Python's argparse does: the
same namespace, the same usage errors, the same --help). A `func` is the name of the command's function in the binary
(commands.rs). The tests read the same tree, to hold AGENTS.md and SKILL.md to the commands that exist, and they fail
when cli_spec.json is not what this file gives.
"""
from __future__ import annotations

import argparse
import re
from typing import Any

VERSION = "{version}"       # what the spec says where the version goes; the binary says its own
DESCRIPTION = ("The record of the experiments your coding agents run, as Rabbit Brain YAML files in your repo: questions, experiments with "
               "their hypothesis, variants and criteria, runs with receipts, and the resources they used. Agents record; rb checks "
               "and computes findings; you decide. Documentation: rb docs. New here? Run rb tour.")
RESEARCH_DOCTOR_HELP = 'check the record: who you are recorded as, edits made outside rb, merge leftovers, sources outside the repository'
GATES = ["unmet", "unresolved", "untested", "integrity", "undecided", "stale", "unknown", "late", "checks",
         "unestablished"]
PROVIDERS = ["local", "url", "git", "wandb", "mlflow", "s3", "gcs", "hf", "runpod", "other"]
RELATIONS = ["reproduces", "extends", "follows_from", "compares_with"]
RESOURCE_TYPES = ["dataset", "checkpoint", "code", "environment", "artifact", "other"]
# the records `rb schema <name>` prints, beside `all` (the whole format) and `ir` (the vocabulary)
SCHEMA_NAMES = ["project", "question", "experiment", "metric", "criterion", "run", "analysis", "resource", "decision",
                "finding", "envelope"]
# every command on the record, as its words; then the rest of the command line
LEDGER_COMMANDS = ["init", "project title", "question add", "question move", "question reword", "experiment add", "experiment file", "experiment relate",
                   "experiment gate", "variant add", "note add", "metric add", "spec set", "spec verify", "spec vary",
                   "criterion add", "criterion file", "criterion relate", "run add", "analysis add", "resource add", "resource locate", "resource expose",
                   "freeze", "decide", "confirm", "retract", "status", "show", "compare", "log", "context", "fmt", "journal",
                   "journal scan", "journal run", "reconcile", "compile", "page", "export", "clone", "sync", "login",
                   "logout", "env check"]
COMMANDS = [*LEDGER_COMMANDS, "doctor", "remove", "ui", "open", "mcp", "skill", "tour", "docs", "schema", "version", "feedback", "config auto-update",
            "telemetry status", "telemetry enable", "telemetry disable", "telemetry preview", "telemetry flush"]


class UsageError(Exception):
    """argparse could not read the command line: rb reports it as E_USAGE (an envelope under --json)."""

    def __init__(self, message: str, usage: str) -> None:
        super().__init__(message)
        self.message, self.usage = message, usage


class RBArgumentParser(argparse.ArgumentParser):
    """argparse, except that a usage error is raised for rb to report (an agent with --json gets an envelope, not a
    bare usage line on stderr), and a negative number in scientific notation (--at-most -1e-3) is a value, not an option."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._negative_number_matcher = re.compile(r"^-(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?$")

    def error(self, message: str) -> None:  # type: ignore[override]
        raise UsageError(message, self.format_usage())


def add_parsers(sub: Any, common: argparse.ArgumentParser) -> None:
    """`common` carries --json; the record's single writes take --dry-run too (a bulk write previews in its plain form)."""
    rc = [common]
    dry = argparse.ArgumentParser(add_help=False)
    dry.add_argument("--dry-run", action="store_true",
                     help="run every check the write makes and print what it would write, writing nothing; it exits as the write would")
    wc = [common, dry]

    def group(name: str, verbs: str, help: str, hidden: bool = False) -> Any:
        g = sub.add_parser(name, help=argparse.SUPPRESS if hidden else f"{verbs}: {help}")
        return g.add_subparsers(dest="sub_command", metavar=f"<{verbs}>", required=True)

    def src(s: argparse.ArgumentParser, what: str, quote_help: str, database: bool = False) -> None:
        row = "database#table[column=value].column (SQLite), " if database else ""
        s.add_argument("--source", default=None, help=f"where {what} comes from: file#key (YAML/JSON/TOML), {row}file:LINE, run:PATH#/pointer, https://..., or note:text")
        s.add_argument("--quote", default=None, help=quote_help)
        s.add_argument("--term", default=None, help="the word the source uses for it on a prose line, e.g. \"learning rate\"")
        s.add_argument("--commit", default=None, help="read the file at this git commit instead of the working tree")
        s.add_argument("--locator", default=None, help="how a reader finds it, e.g. \"§4.2\" or \"Table 3\"")

    def why(s: argparse.ArgumentParser, required: bool = False, help: str = "the reason, recorded with the call") -> None:
        s.add_argument("-m", "--why", required=required, default=None, help=help)
        said(s)

    def said(s: argparse.ArgumentParser) -> None:
        s.add_argument("--words", default=None, metavar="TEXT",
                       help="the user's words from the chat that this acts on, verbatim (an agent quoting them); kept beside the reason")

    def criterion_args(s: argparse.ArgumentParser) -> None:
        s.add_argument("statement")
        s.add_argument("-e", "--experiment", required=True, help="the experiment whose runs test it")
        s.add_argument("-q", "--question", action="append", default=None, help="the question this criterion bears on (repeatable)")
        s.add_argument("--when-met", default=None, help="your interpretation for these questions when the rule is met, verbatim")
        s.add_argument("--metric", required=True, help="what is measured: val_loss, int8.val_loss, candidate.val_loss, or change.val_loss (the candidate against the baseline); with --subject/--reference, the bare metric")
        s.add_argument("--subject", default=None, help="the arm it is about: a variant, a role, or an arm an analysis names (default: the only candidate)")
        s.add_argument("--reference", default=None, help="the arm it is compared against; without one it reads a level")
        s.add_argument("--estimand", default=None, help="what is estimated, when an analysis reports it that way: pooled, per_class (default: rb's reading of the runs, a mean over units)")
        for selector in ("where", "subject-where", "reference-where"):
            s.add_argument("--" + selector, action="append", default=None, metavar="NAME=VALUE", help="select exact per-run setting values; repeat to require all (" + selector + ")")
        s.add_argument("--at-most", type=float, default=None)
        s.add_argument("--at-least", type=float, default=None)
        s.add_argument("--equals", "--within", dest="equals", type=float, default=None, metavar="TARGET", help="with --tolerance: holds when |observed - TARGET| <= tolerance")
        s.add_argument("--tolerance", type=float, default=None)
        s.add_argument("--better", action="store_true", help="the subject is better than the reference: the interval clear of 0 on the better side")
        s.add_argument("--noninferior", type=float, default=None, metavar="MARGIN", help="the subject is no worse than the reference by more than MARGIN, read on the interval")
        s.add_argument("--equivalent", type=float, default=None, metavar="MARGIN", help="the difference lies inside ±MARGIN, read on the interval")
        s.add_argument("--over", choices=["mean", "each", "bound"], default=None, help=argparse.SUPPRESS)
        s.add_argument("--each", action="store_true", help="a threshold every independent unit must meet, not only the estimate")
        s.add_argument("--bound", action="store_true", help="a threshold on the interval's bound (the upper for --at-most, the lower for --at-least)")
        s.add_argument("--role", choices=["primary", "secondary", "exploratory", "gate"], default="primary",
                       help="primary (what the work claims), secondary, exploratory, or gate (a priority policy: a miss parks a line, it never refutes it)")
        s.add_argument("--min-n", type=int, default=1, help="independent units it takes (default 1)")
        s.add_argument("--noise", type=float, default=None, help="run-to-run spread: an estimate this close to the rule is said to be within the noise")
        src(s, "the cited number (a paper's table)", "the exact text that states the number, or the ordering a threshold of 0 cites; a file source must state it or the criterion is refused")
        s.add_argument("--note", default="")
        s.add_argument("--amend", action="store_true", help="the experiment is frozen: add it anyway (with --why); the amendment is recorded")
        why(s)
        s.add_argument("--id", default=None)
        s.set_defaults(func="cmd_criterion_add")

    def run_args(s: argparse.ArgumentParser) -> None:
        s.add_argument("experiment")
        s.add_argument("pairs", nargs="*", metavar="NAME=VALUE", help="results, e.g. int8.val_loss=5.64 latency_ms=25.1")
        s.add_argument("--variant", default=None, help="the variant these numbers are for: val_loss=5.64 becomes <variant>.val_loss")
        s.add_argument("--from", dest="from_file", default=None, metavar="FILE",
                       help="a JSON/YAML/TOML object of metric names to numbers (nested keys join with dots), or a SQLite database with --table")
        s.add_argument("--keys", action="append", default=None, help="with --from: only these numbers, glob patterns, e.g. \"summary.*\" (the file is still hashed whole)")
        s.add_argument("--table", default=None, help="with --from a SQLite database: the table that holds the run's row (the database is read, never written)")
        s.add_argument("--where", action="append", default=None, metavar="COLUMN=VALUE",
                       help="with --table: the row's columns and their values, e.g. precision=int8,seed=1; exactly one row must have them all")
        s.add_argument("--select", action="append", default=None, metavar="COLUMN",
                       help="with --table: only these of the row's columns that name a declared metric (default: all of them)")
        s.add_argument("--config", default=None, help="the run's own resolved config (Hydra, W&B config.yaml, hparams.yaml): checks it ran with the spec")
        s.add_argument("--set", action="append", default=None, metavar="NAME=VALUE",
                       help="a per-run setting this run used, e.g. seed=2; with --table, seed=@seed takes the row's seed column, and seed=@configs.seed (or @configs[config_id].seed) the seed of the row of configs that table links")
        s.add_argument("--input", action="append", default=None, metavar="RESOURCE[:ROLE]", help="a resource the run read, e.g. imagenet-val:eval (repeatable)")
        s.add_argument("--output", action="append", default=None, metavar="RESOURCE[:ROLE]", help="a resource the run made, e.g. int8-ckpt:checkpoint (repeatable)")
        s.add_argument("--host", action="append", default=None, metavar="NAME=VALUE", help="where it ran, as you say it: provider=runpod gpu=A100 (repeatable)")
        s.add_argument("--commit", default=None, help="the commit that produced the numbers")
        s.add_argument("--tree", default=None, metavar="TREE",
                       help="the git tree of the exact code that produced them, when it had uncommitted changes (rb journal run keeps it; rb reconcile passes it)")
        s.add_argument("--artifact", "--file", dest="artifact", action="append", default=None, help="an output whose hash goes into the run (repeatable)")
        s.add_argument("--link", action="append", default=None, metavar="NAME=URL", help="where else it lives, e.g. wandb=https://... (repeatable)")
        s.add_argument("--command", dest="command_text", default=None, help="the command that produced the numbers (recorded as given; rb does not run it)")
        s.add_argument("--command-source", default=None, metavar="FILE[:LINE]",
                       help="the log line that records that command's launch, for a run made before it was recorded; rb checks the line holds it")
        s.add_argument("--again", action="store_true", help="record numbers identical to a recorded run's as a second execution (with --why)")
        s.add_argument("--replaces", default=None, metavar="RUN",
                       help="this run replaces RUN, an invalid run of the same experiment: RUN is retracted with --why and the two are linked")
        s.add_argument("--repeats", default=None, metavar="RUN", help="this run re-checks RUN: it joins that run's unit and adds none")
        s.add_argument("--produced-at", default=None, metavar="WHEN",
                       help="when the output existed, when that is earlier than now (ISO date or time), or unknown; with --table, @COLUMN reads it from the row")
        why(s)
        s.add_argument("--note", default="")
        s.set_defaults(func="cmd_run_add")

    g = group("project", "title", "the project's title")
    s = g.add_parser("title", parents=wc, help="change the project's title, keeping its identity and record")
    s.add_argument("title", help="the work's theme: a paper's title, a model, a line of work")
    why(s, required=True, help="why the title is changing, kept in the log")
    s.set_defaults(func="cmd_project_title")

    g = group("question", "add|move|reword", "what the work is trying to find out, as a tree experiments are filed under")
    s = g.add_parser("add", parents=wc, help="add a question, at the top or under another")
    s.add_argument("text")
    s.add_argument("--id", default=None)
    s.add_argument("--parent", default=None, help="the question this one sits under")
    said(s)
    s.set_defaults(func="cmd_question_add")
    s = g.add_parser("move", parents=wc, help="put a question under another one, or at the top; its evidence goes with it")
    s.add_argument("question")
    s.add_argument("--parent", default=None)
    s.add_argument("--top", action="store_true")
    why(s)
    s.set_defaults(func="cmd_question_move")
    s = g.add_parser("reword", parents=wc, help="give a question new wording, kept as a revision: the log keeps the old wording, who changed it and why; its id, evidence and calls stay")
    s.add_argument("question")
    s.add_argument("text", help="the new wording, verbatim: the user's own words, never a formalization of them")
    why(s, required=True, help="why it is reworded, kept in the log")
    s.set_defaults(func="cmd_question_reword")

    g = group("experiment", "add|file|relate|gate", "what is tested: its purpose, hypothesis, variants and settings; where it is filed; how it stands to others; the decision it gates")
    s = g.add_parser("add", parents=wc, help="add an experiment: planned until its first run")
    s.add_argument("title")
    s.add_argument("--id", default=None, help="a short id you choose (default: e and six random characters)")
    s.add_argument("-q", "--question", action="append", default=None, help="a question it is filed under (repeatable: an experiment can answer several)")
    s.add_argument("--hypothesis", default=None, help="what you expect it to show")
    s.add_argument("-m", "--why", default=None, help="why you expect it")
    s.add_argument("--purpose", default=None, help="what it is for, when that is not a hypothesis (a benchmark, a sweep)")
    s.add_argument("--baseline", default=None, help="name of the baseline variant, e.g. fp32")
    s.add_argument("--candidate", action="append", default=None, help="name of a candidate variant, e.g. int8 (repeatable)")
    s.add_argument("--varies", action="append", default=None, help="the settings the variants differ in on purpose (repeatable or comma-separated); any other difference is a confound")
    s.add_argument("--like", default=None, help="copy another experiment's shared settings (as provisional) and what it varies")
    s.add_argument("--note", default="", help="a first note: context, an assumption, a caveat")
    s.add_argument("--if-holds", default=None, metavar="TEXT", help="the decision it gates: the action taken when it holds; the sentence it was said in goes in --words")
    s.add_argument("--if-not", default=None, metavar="TEXT", help="and what to do when it does not")
    said(s)
    s.set_defaults(func="cmd_experiment_add")
    s = g.add_parser("file", parents=wc, help="file an experiment under one more question (or --remove it from one)")
    s.add_argument("experiment")
    s.add_argument("question")
    s.add_argument("--remove", action="store_true")
    s.set_defaults(func="cmd_experiment_file")
    s = g.add_parser("relate", parents=wc, help="how an experiment stands to another: reproduces, extends, follows_from, compares_with")
    s.add_argument("experiment")
    s.add_argument("relation", choices=RELATIONS)
    s.add_argument("target", help="another experiment's id here, its global uid, or a published manifest's address")
    s.add_argument("--note", default=None)
    s.set_defaults(func="cmd_experiment_relate")
    s = g.add_parser("gate", parents=wc, help="the decision an experiment gates, set or revised: the action taken if it holds, and if not; the sentence it was said in goes in --words")
    s.add_argument("experiment")
    s.add_argument("--if-holds", default=None, metavar="TEXT", help="the action taken when it holds")
    s.add_argument("--if-not", default=None, metavar="TEXT", help="what to do when it does not")
    why(s)
    s.set_defaults(func="cmd_experiment_gate")

    g = group("note", "add", "words about an experiment or the project: an assumption, a caveat, context; a note changes no finding")
    s = g.add_parser("add", parents=wc, help="add a note, kept verbatim with who wrote it")
    s.add_argument("text")
    s.add_argument("-e", "--experiment", default=None, help="the experiment it is about (default: the whole project)")
    said(s)
    s.set_defaults(func="cmd_note_add")

    g = group("variant", "add", "an arm of an experiment: baseline, candidate, control, ablation")
    s = g.add_parser("add", parents=wc, help="add a variant to an experiment")
    s.add_argument("experiment")
    s.add_argument("name")
    s.add_argument("--role", required=True, choices=["baseline", "candidate", "control", "ablation"])
    s.add_argument("--note", default="")
    s.add_argument("--amend", action="store_true", help="the experiment is frozen: add it anyway (with --why); the amendment is recorded")
    why(s)
    s.set_defaults(func="cmd_variant_add")

    g = group("metric", "add", "the catalogue: a metric's unit, which way is better, its other names")
    s = g.add_parser("add", parents=wc, help="declare a metric")
    s.add_argument("name")
    s.add_argument("--unit", default=None)
    d = s.add_mutually_exclusive_group()
    d.add_argument("--minimize", action="store_true", help="lower is better (an error, a loss, a latency)")
    d.add_argument("--maximize", action="store_true", help="higher is better (an accuracy, a score)")
    s.add_argument("--alias", action="append", default=None, help="another name runs may use for it (repeatable), e.g. cross_entropy")
    s.add_argument("--description", default=None)
    s.set_defaults(func="cmd_metric_add")

    g = group("spec", "set|verify|vary", "an experiment's settings, where each value comes from, and whether rb has checked it")
    s = g.add_parser("set", parents=wc, help="give a setting's value and source (checked when it can be), record it as unknown, or read many from a config with --from")
    s.add_argument("experiment")
    s.add_argument("name", nargs="?", default=None, help="the setting; <variant>.<name> for one variant's own value")
    s.add_argument("value", nargs="?", default=None, help="a number, true/false, text, or a JSON list; optional with file#key or run: sources")
    s.add_argument("--unknown", action="store_true", help="nobody has found this value yet")
    s.add_argument("--per-run", action="store_true", help="each run gives its own value (a seed): --set seed=2 on rb run add")
    design = s.add_mutually_exclusive_group()
    design.add_argument("--design-factor", action="store_true", help="a fixed design choice, not an independent sample; a new setting becomes per-run, and an existing one must already be per-run")
    design.add_argument("--sample-factor", action="store_true", help="clear this setting's design-factor declaration")
    src(s, "the value", "the exact text in a file or url source that states it", database=True)
    s.add_argument("--variant", default=None, help="set it for this variant only (same as <variant>.<name>)")
    req = s.add_mutually_exclusive_group()
    req.add_argument("--optional", action="store_true", help="an unknown value is a note, not an integrity problem of the findings")
    req.add_argument("--required", action="store_true", help="it does (the default for a new setting)")
    s.add_argument("--cited", default=None, metavar="VALUE", help="the value the cited source (a paper, its evaluation code) used; a difference makes cited criteria not comparable")
    s.add_argument("--cited-source", default=None, help="where the cited value comes from")
    s.add_argument("--from", dest="from_file", default=None, metavar="FILE", help="record settings from a YAML/JSON/TOML config, each by key path, with --keys")
    s.add_argument("--keys", action="append", default=None, help="with --from: the keys that define the experiment, glob patterns, e.g. \"optim.*,model.depth\"")
    s.add_argument("--note", default=None)
    s.add_argument("--no-verify", action="store_true", help="record it without checking the source now")
    s.add_argument("--amend", action="store_true", help="the experiment is frozen: change it anyway (with --why); the amendment is recorded")
    why(s)
    s.set_defaults(func="cmd_spec_set")
    s = g.add_parser("verify", parents=wc, help="check each setting's source again and record what it states; verified only if it states the value")
    s.add_argument("experiment")
    s.add_argument("names", nargs="*", help="settings (default: every setting with a source)")
    s.set_defaults(func="cmd_spec_verify")
    s = g.add_parser("vary", parents=wc, help="declare settings the variants differ in on purpose; any other difference is a confound")
    s.add_argument("experiment")
    s.add_argument("names", nargs="+", help="setting names (or comma-separated)")
    s.add_argument("--amend", action="store_true", help="the experiment is frozen: change it anyway (with --why); the amendment is recorded")
    why(s)
    s.set_defaults(func="cmd_spec_vary")

    g = group("criterion", "add|file|relate", "a rule over one comparison, declared before the evidence it reads")
    s = g.add_parser("add", parents=wc, help="declare a criterion; declare it before the evidence that tests it")
    criterion_args(s)
    s = g.add_parser("file", parents=wc, help="declare which question a criterion bears on and what meeting it would mean")
    s.add_argument("criterion")
    s.add_argument("question")
    s.add_argument("--when-met", default=None, help="your interpretation when the rule is met, verbatim; omitted keeps the current words")
    s.add_argument("--remove", action="store_true", help="retract this question declaration, keeping its history")
    s.add_argument("--amend", action="store_true", help="change the frozen declaration with the reason recorded")
    why(s, required=True)
    s.set_defaults(func="cmd_criterion_file")
    s = g.add_parser("relate", parents=wc, help="declare, with its reason, that a comparison reverses another and how their arms align; rb reads whether it does")
    s.add_argument("criterion")
    s.add_argument("relation", choices=["reverses"])
    s.add_argument("target")
    s.add_argument("--orientation", choices=["same", "opposite"], default=None, help="same: corresponding subject/reference order; opposite: swap the target's order")
    s.add_argument("--remove", action="store_true", help="withdraw the relation, keeping its attribution")
    s.add_argument("--amend", action="store_true", help="change a frozen relation with the reason recorded")
    why(s, required=True)
    s.set_defaults(func="cmd_criterion_relate")

    g = group("analysis", "add", "a computation over saved outputs, and the comparisons it produced with their intervals")
    s = g.add_parser("add", parents=wc, help="record an analysis: its comparisons, what it read, its method, and where its numbers are stated")
    s.add_argument("experiment")
    s.add_argument("--compare", action="append", default=None, metavar="SUBJECT/REFERENCE:METRIC[@ESTIMAND]=EST[LO,HI]",
                   help="one comparison it produced (repeatable), e.g. new/old:score=0.12[0.07,0.17]")
    s.add_argument("--from", dest="from_file", default=None, metavar="FILE", help="its output file listing the comparisons (JSON/YAML/TOML); hashed, and read as computed")
    s.add_argument("--title", default=None)
    s.add_argument("--reads", action="append", default=None, metavar="RUN|ANALYSIS", help="runs or analyses it read (repeatable or comma-separated)")
    s.add_argument("--input", action="append", default=None, metavar="RESOURCE[:ROLE]", help="a resource it read, e.g. predictions:eval (repeatable)")
    s.add_argument("--artifact", "--file", dest="artifact", action="append", default=None, help="an output whose hash goes into the analysis (repeatable)")
    s.add_argument("--method", default=None, help="how the intervals were made, e.g. \"paired bootstrap over scenes\"")
    s.add_argument("--unit", default=None, help="the independent unit it resampled, e.g. \"scene\"")
    s.add_argument("--units", type=int, default=None, help="how many of them")
    s.add_argument("--draws", type=int, default=None, help="bootstrap draws, when it resampled")
    s.add_argument("--level", type=float, default=None, help="the intervals' level (default 0.95)")
    s.add_argument("--source", default=None, help="the document that states the numbers (a README, a report saved as text): rb finds the line that states each")
    s.add_argument("--quote", default=None, help="the exact row that states them")
    s.add_argument("--commit", default=None, help="read the document at this git commit")
    s.add_argument("--locator", default=None, help="how a reader finds it, e.g. \"Table 2\"")
    s.add_argument("--supersedes", action="append", default=None, metavar="ANALYSIS", help="an earlier analysis this one replaces (with --why)")
    s.add_argument("--produced-at", default=None, metavar="WHEN", help="when it was computed, when earlier than now, or unknown")
    s.add_argument("--command", dest="command_text", default=None, help="the command that computed it (recorded as given; rb does not run it)")
    why(s, help="why it supersedes the earlier analysis")
    s.add_argument("--note", default=None)
    s.add_argument("--id", default=None)
    s.set_defaults(func="cmd_analysis_add")

    g = group("run", "add", "what ran: its results, per-run settings, inputs and outputs, with a receipt")
    s = g.add_parser("add", parents=wc, help="record a run: numbers from any evaluator, or a file")
    run_args(s)

    g = group("resource", "add|locate|expose", "a dataset, checkpoint, code or artifact: what it is, where its bytes are, how to get it again, who has looked at it")
    s = g.add_parser("add", parents=wc, help="record a resource: its identity, its locations, and how to get it again")
    s.add_argument("id", help="a short id you choose, e.g. imagenet-val")
    s.add_argument("--type", required=True, choices=RESOURCE_TYPES)
    s.add_argument("--name", required=True, help="what it is, e.g. \"ImageNet-1k validation\"")
    s.add_argument("--version", default=None)
    s.add_argument("--subset", default=None, help="which part, e.g. \"training split, images 0..199\"")
    s.add_argument("--file", default=None, help="a local file: hashed now, and recorded as its first location")
    s.add_argument("--sha256", default=None, help="the sha256 of the file, when it is one file")
    s.add_argument("--digest", default=None, help="any other fingerprint: a provider's digest, a dataset hash")
    s.add_argument("--at", action="append", default=None, metavar="PROVIDER:REF", help=f"where its bytes are (repeatable); providers: {', '.join(PROVIDERS)}")
    s.add_argument("--source", default=None, help="where it comes from, in words")
    s.add_argument("--url", default=None, help="where to get it again")
    s.add_argument("--recipe", default=None, help="the command or steps that rebuild it")
    s.add_argument("--note", default=None)
    s.set_defaults(func="cmd_resource_add")
    s = g.add_parser("locate", parents=wc, help="record another place a resource's bytes are (or were)")
    s.add_argument("id")
    s.add_argument("provider", choices=PROVIDERS)
    s.add_argument("ref", help="a path, a uri, an artifact name, a pod id")
    s.add_argument("--note", default=None)
    s.set_defaults(func="cmd_resource_locate")
    s = g.add_parser("expose", parents=wc, help="record that someone looked at a dataset: inspected it, selected on it, evaluated on it, trained on it")
    s.add_argument("id")
    s.add_argument("how", choices=["inspected", "selected", "evaluated", "trained"])
    s.add_argument("--at", default=None, help="when it happened, when earlier than now (ISO date or time), or unknown; kept as reported")
    why(s, help="what was looked at, and why")
    s.set_defaults(func="cmd_resource_expose")

    s = sub.add_parser("retract", parents=wc, help="take anything back: it stays on record, stops counting, and rb says what rested on it")
    s.add_argument("subject", help="an id (a note's too), or <experiment>/<setting>")
    s.add_argument("--scope", action="append", default=None, metavar="MEASUREMENT",
                   help="runs only: supersede these measurements (e.g. --scope latency_ms) and keep the rest counting")
    why(s, required=True)
    s.set_defaults(func="cmd_retract")

    s = sub.add_parser("freeze", parents=wc, help="lock an experiment's spec and criteria before the evidence, recorded with who locked it")
    s.add_argument("experiment")
    s.add_argument("--amend", action="store_true", help="the experiment is frozen: adopt it as it is now (with --why)")
    why(s)
    s.set_defaults(func="cmd_freeze")

    s = sub.add_parser("decide", parents=wc, help="a call on a criterion, an experiment, a question or a setting: accept, reject, investigate; continue, park, stop, pivot, ship. Recorded with who made it and why; in an agent session it is proposed until the user confirms it")
    s.add_argument("subject")
    s.add_argument("action", choices=["accept", "reject", "investigate", "continue", "park", "stop", "pivot", "ship"])
    why(s, required=True)
    s.add_argument("--revisit", default=None, help="when to look again, e.g. \"when the new images are in\"")
    s.add_argument("--past", action="store_true", help="a call made before it was recorded: it reads Retrospective, dated as --when says")
    s.add_argument("--when", default=None, metavar="TEXT", help="with --past: when the call was made, as said (a date, or words); unknown without it")
    s.add_argument("--source", default=None, metavar="FILE[:LINE]", help="with --past and --quote: the document that states the call; rb says what git shows of it, and a past call from an agent session still waits for rb confirm")
    s.add_argument("--quote", default=None, metavar="TEXT", help="with --source: the document's own words that state the call, naming what it is on; rb checks the file holds them")
    s.set_defaults(func="cmd_decide")

    s = sub.add_parser("confirm", parents=wc, help="make a call proposed, or a past call mentioned, in an agent session yours, or an experiment's design one wrote: run in your own terminal; with no id, list what waits")
    s.add_argument("ids", nargs="*", metavar="ID", help="the calls to confirm, as rb status lists them, or <experiment>@<hash> for an experiment's design with its gate, as rb confirm lists it; an experiment's id alone lists its design")
    s.add_argument("--all", action="store_true", help="confirm every call, gate and design that waits as it reads now, after saying each (the command rb confirm ends with confirms them as listed)")
    s.set_defaults(func="cmd_confirm")

    s = sub.add_parser("status", parents=rc, help="each criterion on its axes, and what is open, with who usually acts")
    s.add_argument("experiment", nargs="?", default=None, help="only this experiment")
    s.add_argument("--fail-on", action="append", default=None, metavar="GATE", help=f"exit 1 when any of: {', '.join(GATES)} (repeatable or comma-separated); an edit made outside rb that a finding depends on fails any of them")
    s.set_defaults(func="cmd_status")

    s = sub.add_parser("show", parents=rc, help="one record: a criterion with its finding, an experiment with its spec, a run with its receipt, e1/lr with its history; or a .yaml record file as canonical JSON")
    s.add_argument("id", help="an id, <experiment>/<setting>, or a path to an Rabbit Brain YAML file")
    s.set_defaults(func="cmd_show")

    s = sub.add_parser("compare", parents=rc, help="an experiment's own table: variants by metrics, with changes against the baseline")
    s.add_argument("experiment", nargs="?", default=None)
    s.set_defaults(func="cmd_compare")

    s = sub.add_parser("log", parents=rc, help="every write, newest last: who, how, what")
    s.add_argument("id", nargs="?", default=None, help="only this record, or <experiment>/<setting>")
    s.add_argument("-n", type=int, default=20, help="how many (default 20)")
    s.set_defaults(func="cmd_log")

    s = sub.add_parser("context", parents=rc, help="the record as one Markdown briefing a fresh agent session reads first")
    s.add_argument("--model", default=None, metavar="ID",
                   help="an agent's own model, as its provider names it: kept with every later write in this agent session (RB_MODEL gives it for one command)")
    s.add_argument("--provider", default=None, metavar="NAME",
                   help="who provides that model, kept the same way (RB_PROVIDER for one command); rb fills in Claude Code's and Codex's when none is given")
    s.set_defaults(func="cmd_context")

    s = sub.add_parser("fmt", parents=rc, help="write Rabbit Brain YAML files in their canonical form, comments kept (default: every record file under .rb/)")
    s.add_argument("paths", nargs="*", help="files or directories (default: the record)")
    s.add_argument("--check", action="store_true", help="change nothing; exit 1 if any file is not canonical")
    s.set_defaults(func="cmd_fmt")

    j = sub.add_parser("journal", parents=rc, help="what rb observed in the repository: files that appeared, git moves, wrapped commands")
    j.add_argument("-n", type=int, default=20, help="how many entries (default 20)")
    j.set_defaults(func="cmd_journal")
    jsub = j.add_subparsers(dest="sub_command", required=False, metavar="<scan|run>")
    s = jsub.add_parser("scan", parents=rc, help="look at the watched places once and journal what changed")
    s.add_argument("--hook", metavar="<agent>",
                   help="run from that agent's session hook (rb init installs it): say nothing, exit 0, and keep the look as that agent's")
    s.set_defaults(func="cmd_journal_scan")
    s = jsub.add_parser("run", parents=rc, help="run a command and journal what it did: rb journal run -- python eval.py ...")
    s.add_argument("cmd", nargs=argparse.REMAINDER, help="the command, after --")
    s.set_defaults(func="cmd_journal_run")

    s = sub.add_parser("reconcile", parents=rc, help="compile the repository against the record: each results folder with a report as an experiment, what its files say (verbatim), its checks, how to reproduce it; --apply keeps it")
    s.add_argument("--apply", action="store_true", help="keep it in the record: add and re-index the experiments, record the runs found, attributed to you")
    s.add_argument("--no-agents", action="store_true",
                   help="with --apply where there is no record yet: leave the agents' instruction files alone (default: install rb's, as rb init does)")
    s.add_argument("--yes", action="store_true",
                   help="with --apply where there is no record yet: add rb's part to an AGENTS.md or .mcp.json that is here without asking (default: asked once in your terminal; elsewhere left as they are)")
    s.set_defaults(func="cmd_reconcile")

    s = sub.add_parser("compile", parents=rc, help="list what existing reports and results files the record can place, and what it cannot; writes nothing")
    s.add_argument("paths", nargs="*", help="files or directories to compile from")
    s.set_defaults(func="cmd_compile")

    s = sub.add_parser("page", parents=rc, help="write the project as one self-contained, read-only HTML file you can send anywhere")
    s.add_argument("--out", default=None, help="where to write it (default rb-page.html beside .rb/)")
    s.add_argument("--replace", action="store_true", help="write over a file at --out that rb did not write (default: refused, the file left as it is)")
    s.set_defaults(func="cmd_page")

    s = sub.add_parser("export", parents=rc, help="write the project's page with a hashed manifest, readable without rb; with --with-record, the record too, as a portable bundle")
    s.add_argument("--out", default=None, help="where to write it (default rb-export/ beside .rb/)")
    s.add_argument("--zip", action="store_true", help="write one zip file instead of a directory (default rb-export.zip)")
    s.add_argument("--with-record", action="store_true", help="carry the record too, for rb clone: its files and log keep who made each write, agent sessions and the user's words")
    s.add_argument("--url", default=None, help="with --with-record, the public address this will be served from: the page then opens with the exact rb clone command, and a directory export also contains bundle.zip")
    s.add_argument("--no-repo", action="store_true", help="leave the code repository's address out of the manifest and page")
    s.add_argument("--replace", action="store_true", help="with --zip, write over a file at --out that rb did not write (default: refused, the file left as it is)")
    s.set_defaults(func="cmd_export")

    s = sub.add_parser("clone", parents=rc, help="start your own line of work from an exported bundle: the structure arrives inherited, and nothing counts until reproduced here")
    s.add_argument("source", help="a bundle rb export --with-record wrote: a directory, a .zip, or its address")
    s.add_argument("dest", nargs="?", default=None, help="where to create it (default ./<project id>)")
    s.add_argument("--code", default=None, metavar="ADDRESS", help="where to get the code the runs need, in place of the repository the bundle names (a copy you can reach, or one the bundle does not name): the record lands in a checkout of it at the bundle's commit")
    s.set_defaults(func="cmd_clone")

    s = sub.add_parser("env", parents=rc, help="the environment: check this machine against the record's runs and any env.* settings")
    esub = s.add_subparsers(dest="env_command", metavar="<command>", required=True)
    e = esub.add_parser("check", parents=rc, help="compare; differences are said, never fatal (exit 0)")
    e.set_defaults(func="cmd_env_check")

    s = sub.add_parser("login", parents=rc, help="rabbitbrain.ai, optional: sign this machine in with a short code, approved on any device where you are signed in")
    s.add_argument("--hub", default=None, help="the hub's address (default RB_HUB_URL, else rabbitbrain.ai)")
    s.set_defaults(func="cmd_login")

    s = sub.add_parser("logout", parents=rc, help="revoke this machine's hub credential and remove it from the credentials file")
    s.add_argument("--hub", default=None, help="the hub's address (default RB_HUB_URL, else rabbitbrain.ai)")
    s.set_defaults(func="cmd_logout")

    s = sub.add_parser("sync", parents=rc, help="rabbitbrain.ai, optional: a private copy of this record's page in your account, for your own eyes from anywhere")
    s.add_argument("--hub", default=None, help="the hub's address (default RB_HUB_URL, else rabbitbrain.ai)")
    s.add_argument("--with-record", action="store_true", help="send the record too: its files and log keep who made each write, agent sessions and the user's words")
    s.add_argument("--watch", action="store_true", help="keep the hosted copy current: look every 30 s and sync again when the record changed, at most once a minute, until Ctrl-C")
    s.set_defaults(func="cmd_sync")


def build_parser(version: str = VERSION) -> argparse.ArgumentParser:
    """rb's whole command line. `version` is what --version says after "rabbit-brain": the spec carries a
    placeholder, and the binary puts its own version there."""
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", help="print one JSON object (the envelope) instead of text")

    p = RBArgumentParser(prog="rb", description=DESCRIPTION)
    p.add_argument("--version", action="version", version=f"rabbit-brain {version}")
    sub = p.add_subparsers(dest="command", metavar="<command>")

    s = sub.add_parser("init", parents=[common], help="start the record (.rb/) here")
    s.add_argument("title", nargs="?", default=None, help="the work's theme, which its questions share: a paper's title, a model, a line of work (default: this directory's name)")
    s.add_argument("--id", default=None, help="a short id for the project (default: from the title)")
    s.add_argument("--no-agents", action="store_true",
                   help="leave the agents' instruction files alone (default: install rb's instructions for the agents found here, as rb skill add does)")
    s.add_argument("--new-project", action="store_true",
                   help="start a separate project in a worktree whose repository already holds the project's record in another worktree (default: refused, with how to bring that record here)")
    s.add_argument("--yes", action="store_true",
                   help="add rb's section to an AGENTS.md and rb's server to a .mcp.json that are here without asking (default: asked once in your terminal; elsewhere left as they are, with what to add)")
    s.add_argument("--dry-run", action="store_true",
                   help="list every file and integration rb init would write, every file it would change and how, and what later commands add, writing nothing")
    s.set_defaults(func="cmd_init")
    s = sub.add_parser("doctor", parents=[common], help=RESEARCH_DOCTOR_HELP)
    s.add_argument("--restore", action="store_true", help="put back what rb last wrote for every record changed outside rb")
    s.add_argument("--adopt", action="store_true", help="take every change made outside rb as it stands (with --why); edits to who wrote or decided what are refused")
    s.add_argument("-m", "--why", default=None, help="the reason, recorded with --adopt")
    s.add_argument("--words", default=None, metavar="TEXT",
                   help="the user's words from the chat that this acts on, verbatim (an agent quoting them); kept beside the reason")
    s.add_argument("--seen", action="append", default=None, metavar="PATH@SHA256",
                   help="with --adopt: the reviewed files, each pinned to the sha it had (or 'deleted'); any drift since is refused")
    s.set_defaults(func="cmd_doctor")
    s = sub.add_parser("remove", parents=[common], help="take rb out of this checkout: only what checks out as rb's (the record, its instructions for agents, its pages and exports, its refs and folders in git), after showing the plan")
    s.add_argument("--dry-run", action="store_true", help="show the plan (what rb would remove, what it would keep and why), removing nothing")
    s.add_argument("--yes", action="store_true", help="remove without asking (default: asked once in your terminal; refused where there is none)")
    s.set_defaults(func="cmd_remove")
    add_parsers(sub, common)

    s = sub.add_parser("ui", parents=[common], help="open this project in your terminal: your call, the cards, the tree (bare rb does this too)")
    s.set_defaults(func="cmd_ui")

    s = sub.add_parser("open", parents=[common], help="the catalogue in your browser, live and read-only, on 127.0.0.1 only")
    s.add_argument("--port", type=int, default=8787, help="port on 127.0.0.1 (default 8787)")
    s.set_defaults(func="cmd_open")

    s = sub.add_parser("mcp", help="serve the record to an agent over MCP (stdio); every call is recorded as the agent's")
    s.add_argument("--agent", default=None, help="the agent's name in the record (default: the MCP client's name)")
    s = sub.add_parser("docs", parents=[common], help="print AGENTS.md (--start: how to start the record in a project already under way; --errors: the error table)")
    s.add_argument("--errors", action="store_true")
    s.add_argument("--start", action="store_true", help="print the start protocol: how an agent records work already done")
    s.set_defaults(func="cmd_docs")

    s = sub.add_parser("tour", parents=[common], help="a one-minute walkthrough: builds a small real project in ./rb-tour and runs the whole loop")
    s.add_argument("dir", nargs="?", default=None, help="where the tour lands (default ./rb-tour; must be new or empty)")
    s.set_defaults(func="cmd_tour")

    s = sub.add_parser("skill", parents=[common], help="print the agent skill; `rb skill add` installs it for your coding agent")
    s.add_argument("action", nargs="?", choices=["add"], help="add: write it where the agent looks")
    s.add_argument("--claude", action="store_true", help="~/.claude/skills/rabbit-brain/ (the default target)")
    s.add_argument("--project", action="store_true", help="with --claude: this project's .claude/skills/ instead of the home directory")
    s.add_argument("--cursor", action="store_true", help=".cursor/rules/rabbit-brain.mdc in this project")
    s.add_argument("--agents-md", "--codex", dest="agents_md", action="store_true",
                   help="a marked section in this project's AGENTS.md, for Codex and anything else that reads it")
    s.add_argument("--yes", action="store_true",
                   help="with --agents-md: add rb's section to an AGENTS.md that is here without asking (default: asked once in your terminal; elsewhere left as it is, with what to add)")
    s.set_defaults(func="cmd_skill")

    s = sub.add_parser("schema", parents=[common], help="print a JSON Schema: `rb schema all` is Rabbit Brain format 1 (rb-v1.schema.json), `rb schema <kind>` one record's shape")
    s.add_argument("name", choices=["all", *SCHEMA_NAMES, "ir"])
    s.set_defaults(func="cmd_schema")

    s = sub.add_parser("version", parents=[common], help="print the version")
    s.set_defaults(func="cmd_version")

    s = sub.add_parser("feedback", parents=[common], help="a link to send feedback on rb: the page shows what it would send before you send it; rb sends nothing",
                       description="Prints a link to send feedback on rb: the page shows what it would send before you send it; rb sends nothing. In your own terminal it opens the page in your browser; anywhere else it only prints the link.")
    s.add_argument("--about", nargs="+", default=None, metavar="WORD", help="the rb command it is about, by its words (--about run add); its arguments are never sent")
    s.set_defaults(func="cmd_feedback")

    s = sub.add_parser("config", parents=[common], help="rb's own settings on this machine: whether rb keeps itself up to date")
    csub = s.add_subparsers(dest="sub_command", metavar="<auto-update>", required=True)
    a = csub.add_parser("auto-update", parents=[common], help="whether rb checks for a newer rb once a day, and updates itself when it was installed as a tool (uv tool install, pipx); says the setting with no value",
                        description="Whether rb checks PyPI for a newer rb at most once a day: installed as a tool (uv tool install, pipx) it updates itself in the background, and installed with pip it says one line and changes nothing. Never in CI, in an agent session or under DO_NOT_TRACK. With no value it says the setting.")
    a.add_argument("value", nargs="?", choices=["on", "off"], default=None, help="on or off")
    a.set_defaults(func="cmd_config_auto_update")

    t = sub.add_parser("telemetry", help="optional usage analytics: off until you enable it; never sends research content")
    actions = t.add_subparsers(dest="sub_command", metavar="<status|enable|disable|preview|flush>", required=True)
    for name, help in [
        ("status", "show the local analytics setting and destination without sending anything"),
        ("enable", "opt in to limited usage analytics on this installation"),
        ("disable", "stop analytics and delete the local installation ID and pending events"),
        ("preview", "show the queued payload without sending it"),
        ("flush", "try sending the pending usage events now"),
    ]:
        s = actions.add_parser(name, parents=[common], help=help)
        if name == "enable":
            s.add_argument("--project-key", default=None, help="PostHog public project capture key; never a personal API key")
            s.add_argument("--host", default=None, help="PostHog HTTPS ingestion host (must match the project's region)")
        s.set_defaults(func="cmd_telemetry")
    return p
