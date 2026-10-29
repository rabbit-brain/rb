"""What `rb mcp` says of itself, declared: the protocol versions it speaks, its instructions to the agent, every tool
(its description, its positional arguments in order, its options and the flag each becomes, its JSON Schema), and the
resources.

Each tool is the command of the same name: a tool call is turned into that command line and run as the connecting agent.
This file is where a tool is added or its words changed. `python rust/gen/gen_mcp.py` writes it to assets/mcp.json beside
this file, which the binary carries and serves from (mcp.rs).
"""
from __future__ import annotations

PROTOCOLS = ["2025-06-18", "2025-03-26", "2024-11-05"]
INSTRUCTIONS = (
    "Rabbit Brain keeps this project's record of experiments. Call the `context` tool with your model's id as `model` before "
    "anything else (the rb://context resource is the same briefing): it is the state of the work and replaces any summary. When it "
    "opens with recording where the work stands (nothing recorded yet, in a repository with work in it), do it in that turn, "
    "from the files, without asking: rb://start is the protocol. Record when the research state materially changes: "
    "a question or hypothesis is introduced, revised or dropped; an experiment or control is proposed or run; evidence changes "
    "a conclusion; a baseline, variant or metric is established; an important uncertainty appears; the next experiment or a "
    "decision is chosen. Do not record implementation chatter (an installed package, a renamed function). Point at the "
    "repository's own files (results, reports, manifests) with sources rather than copying their prose, and keep words "
    "verbatim. Write text as plain text, since rb shows it exactly as written on every screen: Unicode symbols (≥, ×, Δ, σ, ∼) "
    "rather than LaTeX, no citation keys, and not the name of the agent or tool that did the work (rb records who made each "
    "write). When you choose the next experiment, pass the decision it gates (if_holds, if_not). After a stretch of work, "
    "call reconcile with apply: it keeps what the repository's own files say (results folders, reports, manifests, audits, "
    "checks, reproduction guides, git) in the record, and catches what was not recorded. Every result carries rb_delta: what "
    "changed in the research since this session last looked (results not recorded, a failed check, a stale decision, what the "
    "user wrote); read it, and tell the user what is theirs. Every call is recorded as yours, "
    "with this session and the model you said; nothing "
    "is refused for who you are. Calls on results and on lines of work (decide) are the user's: record one when they ask, "
    "with their words verbatim in `words`. It is proposed, not made, until the user confirms it in their own terminal (rb "
    "confirm <id>); tell them it waits, with that command. A call made before the record is `past`, with `when` as said: it "
    "waits for their confirmation too, and so does one whose `source` and `quote` cite a committed document, which rb checks and "
    "says beside it: a session cannot attest the past it reconstructs. rb "
    "checks sources and computes every finding on its axes: never compute "
    "one yourself, and report it by its axes as rb prints them (the comparison with its interval and units, the rule, the "
    "timing, every integrity problem), never collapsed into one word."
)

S, N, B = {"type": "string"}, {"type": "number"}, {"type": "boolean"}
LIST = {"type": "array", "items": {"type": "string"}}
NUMS = {"type": "object", "additionalProperties": {"type": "number"}}
TEXTS = {"type": "object", "additionalProperties": {"type": ["string", "number", "boolean"]}}
WHY = {"type": "string", "description": "the reason, recorded with the call: what it rests on (a file and line), never the user or the chat"}
AMEND = {"type": "boolean", "description": "the experiment is frozen: change it anyway, with why; the amendment is recorded"}
WORDS = {"type": "string", "description": "the user's words from the chat that this acts on, verbatim"}


def _d(schema: dict, text: str) -> dict:
    return {**schema, "description": text}


# name, description, positional arguments (in order), options (property -> flag), properties, required
TOOLS: list[dict] = [
    {"name": "context", "description": ("The whole record as one Markdown briefing. Read it first, with your model's id as model: rb keeps it "
                                        "with every later write on this connection and in this session."),
     "args": [], "opts": {"model": "--model", "provider": "--provider"},
     "props": {"model": _d(S, "your own model, as its provider names it"),
               "provider": _d(S, "who provides that model; rb fills in Claude Code's and Codex's when none is given")}},
    {"name": "status", "description": ("Rules with their findings on axes, what needs judgment, what can be done now, every record (JSON). "
                                       "The folders no experiment reads keep their first 100 (data.cut), unless all is given."),
     "args": ["experiment"], "props": {"experiment": _d(S, "only this experiment"), "all": _d(B, "every folder in full, however many")}},
    {"name": "show", "description": ("One record in full: a criterion with its finding on axes, its observations and its history, an experiment "
                                     "with its spec and what a rerun needs, a run with its receipt, an analysis with its comparisons, a decision "
                                     "with what it was made on, a resource with where it is, a setting (e1/optim.lr) with its history; "
                                     "or a path to a .yaml record file, given back as canonical JSON."),
     "args": ["id"], "props": {"id": S}, "required": ["id"]},
    {"name": "compare", "description": "An experiment's table: variants by metrics, with the change against the baseline.",
     "args": ["experiment"], "props": {"experiment": S}, "required": ["experiment"]},
    {"name": "log", "description": "Recent writes: who, through what, what changed.", "args": ["id"], "opts": {"n": "-n"},
     "props": {"id": _d(S, "only this record, or <experiment>/<setting>"), "n": _d({"type": "integer"}, "how many (default 20)")}},
    {"name": "journal", "description": "What rb observed in the repository: files that appeared or changed, git moves, wrapped commands. Observation, never the record; nothing here counts toward a finding.",
     "args": [], "opts": {"n": "-n"}, "props": {"n": _d({"type": "integer"}, "how many entries (default 20)")}},
    {"name": "journal_scan", "description": "Look once at where results land (out/, outputs/, results/, eval-out/, metrics/, and everywhere runs have named) and journal what changed.",
     "args": [], "props": {}},
    {"name": "reconcile", "description": ("Compile the repository against the record: each results folder with a report as an experiment, what its files say "
                                          "(verbatim, with file and line), its manifests, audits and checks, how to reproduce it, whether git is "
                                          "clean; and results that landed unrecorded. With apply, keep it all in the record as yours: new "
                                          "experiments, re-indexed folders, and the runs it placed without doubt. Ambiguity is listed, never guessed: a "
                                          "folder of results with no report is never made an experiment. Folders of results files alone "
                                          "that no run named are counted, three named (data.cut), and each long list keeps its first 100, "
                                          "unless all is given."),
     "args": [], "opts": {"apply": "--apply"}, "props": {"apply": _d(B, "keep it in the record"),
                                                         "all": _d(B, "every folder and item in full, however many")}},
    {"name": "page", "description": "Write the project as one self-contained, read-only HTML file and return its path. Give it to the user to read anywhere or send to anyone.",
     "args": [], "opts": {"out": "--out", "replace": "--replace"}, "props": {"out": _d(S, "where to write it (default rb-page.html beside .rb/)"),
                                                                         "replace": _d(B, "write over a file at out that rb did not write; without it, such a file is left as it is")}},
    {"name": "export", "description": ("Write the project's page, with a manifest hashing it, and return its path; with with_record, the "
                                       "record too, as a portable bundle that rb clone reopens. Readable without rb."),
     "args": [], "opts": {"out": "--out", "zip": "--zip", "with_record": "--with-record", "url": "--url", "no_repo": "--no-repo", "replace": "--replace"},
     "props": {"out": _d(S, "where to write it (default rb-export/ beside .rb/)"), "zip": _d(B, "write one zip file instead of a directory"),
               "with_record": _d(B, "carry the record too: its files and log keep who made each write, agent sessions and the user's words"),
               "url": _d(S, "with with_record, the public address it will be served from; the page then opens with the exact rb clone command"),
               "no_repo": _d(B, "leave the code repository's address out of the manifest and page"),
               "replace": _d(B, "with zip, write over a file at out that rb did not write; without it, such a file is left as it is")}},
    # journal run is deliberately not a tool: rb does not execute commands for an agent that has its own shell.
    {"name": "project_title", "description": "Change the project's title, keeping its identity and record; why and any user words are kept in the log.",
     "args": ["title"], "opts": {"why": "--why", "words": "--words"},
     "props": {"title": _d(S, "the work's theme: a paper's title, a model, a line of work"), "why": WHY, "words": WORDS},
     "required": ["title", "why"]},
    {"name": "question_add", "description": "Add a question the work is trying to answer, at the top or under a parent question.", "args": ["text"],
     "opts": {"id": "--id", "parent": "--parent", "words": "--words"}, "props": {"words": WORDS, "text": S, "id": S, "parent": _d(S, "the question it sits under")}, "required": ["text"]},
    {"name": "question_move", "description": "Put a question under another one (parent), or at the top (top); its evidence goes with it.",
     "args": ["question"], "opts": {"parent": "--parent", "top": "--top", "why": "--why", "words": "--words"},
     "props": {"question": S, "parent": S, "top": B, "why": WHY, "words": WORDS}, "required": ["question"]},
    {"name": "question_reword", "description": ("Give a question new wording, kept as a revision: the log keeps the old wording, who changed it and why; "
                                                "its id, evidence and calls stay. Record the user's own words verbatim, never a formalization of them."),
     "args": ["question", "text"], "opts": {"why": "--why", "words": "--words"},
     "props": {"question": S, "text": _d(S, "the new wording, verbatim"), "why": WHY, "words": WORDS}, "required": ["question", "text", "why"]},
    {"name": "experiment_add", "description": ("Add an experiment: what is tested, and why (hypothesis, or purpose), with its variants. It is planned "
                                               "until its first run; a direction nobody has run yet is exactly this."), "args": ["title"],
     "opts": {"id": "--id", "question": "--question", "hypothesis": "--hypothesis", "why": "--why", "purpose": "--purpose", "baseline": "--baseline",
              "candidate": "--candidate", "varies": "--varies", "like": "--like", "note": "--note", "words": "--words", "if_holds": "--if-holds", "if_not": "--if-not"},
     "props": {"if_holds": _d(S, "the decision it gates: the action taken when it holds; the sentence it was said in goes in words"), "if_not": _d(S, "what to do when it does not"), "words": WORDS, "title": S, "id": S, "question": _d(LIST, "the question ids it is filed under"), "hypothesis": _d(S, "what you expect it to show"),
               "why": _d(S, "why you expect it"), "purpose": _d(S, "what it is for, when that is not a hypothesis"),
               "baseline": _d(S, "the baseline variant's name"), "candidate": _d(LIST, "candidate variant names"),
               "varies": _d(LIST, "settings the variants differ in on purpose; any other difference is a confound"),
               "like": _d(S, "copy another experiment's shared settings (as provisional) and what it varies"), "note": _d(S, "a first note")},
     "required": ["title"]},
    {"name": "experiment_file", "description": "File an experiment under one more question (remove: take it out of one).",
     "args": ["experiment", "question"], "opts": {"remove": "--remove"}, "props": {"experiment": S, "question": S, "remove": B},
     "required": ["experiment", "question"]},
    {"name": "experiment_relate", "description": "Record how an experiment stands to another: it reproduces, extends, follows from, or compares with it. Structure: it changes no finding.",
     "args": ["experiment", "relation", "target"], "opts": {"note": "--note"},
     "props": {"experiment": S, "relation": {"type": "string", "enum": ["reproduces", "extends", "follows_from", "compares_with"]},
               "target": _d(S, "another experiment's id here, its global uid, or a published manifest's address"), "note": S},
     "required": ["experiment", "relation", "target"]},
    {"name": "experiment_gate", "description": ("Set or revise the decision an experiment gates: the action taken if it holds (if_holds) "
                                                 "and if not (if_not); the sentence it was said in goes in words. The earlier words stay in the log; a frozen experiment takes it without an amendment."),
     "args": ["experiment"], "opts": {"if_holds": "--if-holds", "if_not": "--if-not", "why": "--why", "words": "--words"},
     "props": {"experiment": S, "if_holds": _d(S, "the action taken when it holds"), "if_not": _d(S, "what to do when it does not"), "why": WHY, "words": WORDS},
     "required": ["experiment"]},
    {"name": "note_add", "description": "Add a note: an assumption, a caveat, context, on an experiment or the project. Kept verbatim; it changes no finding.",
     "args": ["text"], "opts": {"experiment": "--experiment", "words": "--words"}, "props": {"words": WORDS, "text": S, "experiment": _d(S, "the experiment it is about (default: the project)")},
     "required": ["text"]},
    {"name": "variant_add", "description": "Add a variant to an experiment.", "args": ["experiment", "name"],
     "opts": {"role": "--role", "note": "--note", "amend": "--amend", "why": "--why", "words": "--words"},
     "props": {"words": WORDS, "experiment": S, "name": S, "role": {"type": "string", "enum": ["baseline", "candidate", "control", "ablation"]}, "note": S, "amend": AMEND, "why": WHY},
     "required": ["experiment", "name", "role"]},
    {"name": "metric_add", "description": "Add a metric to the catalogue: its unit, which way is better, other names runs may use.", "args": ["name"],
     "opts": {"unit": "--unit", "direction": None, "alias": "--alias", "description": "--description"},
     "props": {"name": S, "unit": S, "direction": {"type": "string", "enum": ["minimize", "maximize"]}, "alias": LIST, "description": S}, "required": ["name"]},
    {"name": "spec_set", "description": ("Record a setting and where its value comes from; rb checks the source now when it can. Sources: file#key.path "
                                         "(YAML/JSON/TOML; the value is read when not given), database#table[column=value].column (one column of one row of a SQLite database, "
                                         "read when not given), file:LINE, a file with quote, run:PATH#/pointer, https://..., note:text. "
                                         "Or from_file with keys to record many settings from a config."),
     "args": ["experiment", "name", "value"],
     "opts": {"source": "--source", "quote": "--quote", "term": "--term", "commit": "--commit", "locator": "--locator", "variant": "--variant",
              "unknown": "--unknown", "per_run": "--per-run", "design_factor": "--design-factor", "sample_factor": "--sample-factor", "optional": "--optional", "cited": "--cited", "cited_source": "--cited-source",
              "from_file": "--from", "keys": "--keys", "note": "--note", "no_verify": "--no-verify", "amend": "--amend", "why": "--why", "words": "--words"},
     "props": {"words": WORDS, "experiment": S, "name": _d(S, "the setting; <variant>.<name> for one variant's own value"),
               "value": _d({"type": ["string", "number", "boolean", "array"]}, "the value; optional with file#key or run: sources"),
               "source": S, "quote": S, "term": S, "commit": S, "locator": S, "variant": S, "unknown": B, "per_run": B, "design_factor": B, "sample_factor": B, "optional": B,
               "cited": _d({"type": ["string", "number", "boolean"]}, "the value a cited source used"), "cited_source": S,
               "from_file": _d(S, "a YAML/JSON/TOML config to record settings from, with keys"), "keys": _d(LIST, "glob patterns of the keys that define the experiment"),
               "note": S, "no_verify": B, "amend": AMEND, "why": WHY},
     "required": ["experiment"]},
    {"name": "spec_verify", "description": "Re-read settings' sources; verified only if the source states the value.", "args": ["experiment", "*names"],
     "props": {"experiment": S, "names": _d(LIST, "settings (default: every setting with a source)")}, "required": ["experiment"]},
    {"name": "spec_vary", "description": "Declare settings the variants differ in on purpose.", "args": ["experiment", "*names"],
     "opts": {"amend": "--amend", "why": "--why", "words": "--words"}, "props": {"words": WORDS, "experiment": S, "names": LIST, "amend": AMEND, "why": WHY}, "required": ["experiment", "names"]},
    {"name": "criterion_add", "description": ("Declare a criterion on an experiment: a rule over one comparison, before the evidence that tests it. "
                                              "What it compares: metric (val_loss, int8.val_loss, change.val_loss) or subject + reference + metric (+ estimand). "
                                              "Exactly one rule: at_most, at_least, equals with tolerance (on the estimate; each or bound read every "
                                              "unit or the interval's bound), better, noninferior (a margin) or equivalent (a margin). role: primary, "
                                              "secondary, exploratory, or gate (a priority policy: a miss parks a line, it never refutes it). "
                                              "question: the questions its finding bears on, with when_met for what meeting the rule would mean there; "
                                              "a question's reading counts only the criteria linked to it (criterion_file links one already recorded)."),
     "args": ["statement"],
     "opts": {"experiment": "--experiment", "metric": "--metric", "subject": "--subject", "reference": "--reference", "estimand": "--estimand",
              "where": "--where", "subject_where": "--subject-where", "reference_where": "--reference-where",
              "at_most": "--at-most", "at_least": "--at-least", "equals": "--equals", "tolerance": "--tolerance", "better": "--better",
              "noninferior": "--noninferior", "equivalent": "--equivalent", "each": "--each", "bound": "--bound", "role": "--role",
              "min_n": "--min-n", "noise": "--noise", "question": "--question", "when_met": "--when-met",
              "source": "--source", "quote": "--quote", "locator": "--locator", "note": "--note", "id": "--id", "amend": "--amend", "why": "--why", "words": "--words"},
     "props": {"words": WORDS, "statement": S, "experiment": S, "metric": S, "subject": S, "reference": S, "estimand": S, "at_most": N, "at_least": N,
               "where": _d(LIST, "exact per-run NAME=VALUE selectors, all required"), "subject_where": _d(LIST, "selectors for subject runs"), "reference_where": _d(LIST, "selectors for reference runs"),
               "question": _d(LIST, "the question ids this criterion bears on"), "when_met": _d(S, "your verbatim interpretation for those questions when the rule is met"),
               "equals": N, "tolerance": N, "better": B, "noninferior": _d(N, "the margin"), "equivalent": _d(N, "the margin"),
               "each": _d(B, "every unit must meet the threshold"), "bound": _d(B, "the threshold reads the interval's bound"),
               "role": {"type": "string", "enum": ["primary", "secondary", "exploratory", "gate"]}, "min_n": {"type": "integer"}, "noise": N,
               "source": _d(S, "where a cited number comes from"), "quote": _d(S, "the exact text that states the number, or the ordering a threshold of 0 cites"), "locator": S, "note": S, "id": S, "amend": AMEND, "why": WHY},
     "required": ["statement", "experiment", "metric"]},
    {"name": "criterion_file", "description": ("Declare which question a criterion bears on, with an optional verbatim interpretation when its rule is met. "
                                               "Omitting when_met keeps its current words. Revisions and removal keep earlier declarations with their attribution. "
                                               "This declares relevance; rb never infers an answer from meeting or missing a rule."),
     "args": ["criterion", "question"], "opts": {"when_met": "--when-met", "remove": "--remove", "amend": "--amend", "why": "--why", "words": "--words"},
     "props": {"criterion": S, "question": S, "when_met": S, "remove": B, "amend": AMEND, "why": WHY, "words": WORDS},
     "required": ["criterion", "question", "why"]},
    {"name": "criterion_relate", "description": ("Declare that two comparisons should be checked for a reversal. Explicit orientation aligns their arms: same or opposite. "
                                                "rb reads both findings and separates an observed sign reversal from two intervals clear of zero. No new numbers or question decision are recorded."),
     "args": ["criterion", "relation", "target"], "opts": {"orientation": "--orientation", "remove": "--remove", "amend": "--amend", "why": "--why", "words": "--words"},
     "props": {"criterion": S, "relation": {"type": "string", "enum": ["reverses"]}, "target": S,
               "orientation": {"type": "string", "enum": ["same", "opposite"]}, "remove": B, "amend": AMEND, "why": WHY, "words": WORDS},
     "required": ["criterion", "relation", "target", "why"]},
    {"name": "analysis_add", "description": ("Record an analysis: a computation over saved outputs and the comparisons it produced. compare: "
                                             "[\"new/old:score=0.12[0.07,0.17]\"] (subject/reference:metric@estimand=estimate[lo,hi]); or "
                                             "from_file listing them. method, unit and units say how the intervals were made; source names the "
                                             "document that states the numbers (rb checks it). reads: the runs or analyses it read. supersedes an "
                                             "earlier analysis, with why. A reanalysis is an analysis, never a run."),
     "args": ["experiment"],
     "opts": {"compare": "--compare", "from_file": "--from", "title": "--title", "reads": "--reads", "input": "--input", "artifact": "--artifact",
              "method": "--method", "unit": "--unit", "units": "--units", "draws": "--draws", "level": "--level", "source": "--source",
              "quote": "--quote", "commit": "--commit", "locator": "--locator", "supersedes": "--supersedes", "why": "--why",
              "produced_at": "--produced-at", "command": "--command", "note": "--note", "id": "--id", "words": "--words"},
     "props": {"words": WORDS, "experiment": S, "compare": LIST, "from_file": S, "title": S, "reads": LIST, "input": LIST, "artifact": LIST, "method": S,
               "unit": S, "units": {"type": "integer"}, "draws": {"type": "integer"}, "level": N, "source": S, "quote": S, "commit": S,
               "locator": S, "supersedes": LIST, "why": WHY, "produced_at": _d(S, "when it was computed, if earlier than now, or unknown"),
               "command": S, "note": S, "id": S},
     "required": ["experiment"]},
    {"name": "run_add", "description": ("Record a run on an experiment, with a receipt. metrics: {\"int8.val_loss\": 5.64}; or from_file: a "
                                        "JSON/YAML/TOML file, or a SQLite database with table and where, which pick one row (read, never "
                                        "written; its columns that name a declared metric are the numbers, and select narrows them). "
                                        "set: per-run settings such as {\"seed\": 2}, or {\"seed\": \"@seed\"} from the row's column, or {\"seed\": \"@configs.seed\"} (or \"@configs[config_id].seed\") from the row of configs that table links. "
                                        "input/output: resource ids it read or made, optionally id:role. config: the run's resolved config, "
                                        "checked against the spec."),
     "args": ["experiment"],
     "opts": {"variant": "--variant", "from_file": "--from", "keys": "--keys", "table": "--table", "where": "--where", "select": "--select",
              "config": "--config", "set": "--set", "input": "--input",
              "output": "--output", "host": "--host", "commit": "--commit", "tree": "--tree", "artifact": "--artifact", "link": "--link", "command": "--command",
              "command_source": "--command-source", "again": "--again", "why": "--why", "note": "--note", "repeats": "--repeats", "replaces": "--replaces", "produced_at": "--produced-at",
              "words": "--words"},
     "props": {"words": WORDS, "experiment": S, "metrics": NUMS, "variant": S, "from_file": S,
               "keys": _d(LIST, "with a JSON/YAML/TOML from_file: only these numbers, glob patterns"),
               "table": _d(S, "with a SQLite from_file: the table that holds the run's row"),
               "where": _d(TEXTS, "with table: the row's columns and their values, {\"precision\": \"int8\", \"seed\": 1}; exactly one row must have them all"),
               "select": _d(LIST, "with table: only these of the row's columns that name a declared metric"),
               "config": S, "set": TEXTS,
               "input": _d(LIST, "resources the run read: id or id:role"), "output": _d(LIST, "resources the run made: id or id:role"),
               "host": _d({"type": "object", "additionalProperties": {"type": "string"}}, "where it ran: {\"provider\": \"runpod\", \"gpu\": \"A100\"}"),
               "commit": S, "tree": _d(S, "the git tree of the exact code, when it had uncommitted changes"), "artifact": LIST,
               "link": {"type": "object", "additionalProperties": {"type": "string"}}, "command": _d(S, "what produced the numbers"),
               "command_source": _d(S, "file:line of the log line that records that command's launch, for a run made before it was recorded"),
               "again": _d(B, "record numbers identical to a recorded run's as a second execution, with why"), "why": WHY, "note": S,
               "repeats": _d(S, "the run this one re-checks: it joins that run's unit and adds none"),
               "replaces": _d(S, "an invalid run of the same experiment this one replaces: it is retracted with why, and the two are linked"),
               "produced_at": _d(S, "when the output existed, when earlier than now (ISO), or unknown; with table, @column reads it from the row")},
     "required": ["experiment"]},
    {"name": "resource_add", "description": ("Record a resource: a dataset, checkpoint, code, environment or artifact. Its identity (name, version, "
                                             "hash), where its bytes are (at: provider:ref, any number), and how to get it again (source, url, "
                                             "recipe). Whether it is present, remote, reconstructible or lost is computed."),
     "args": ["id"],
     "opts": {"type": "--type", "name": "--name", "version": "--version", "subset": "--subset", "file": "--file", "sha256": "--sha256",
              "digest": "--digest", "at": "--at", "source": "--source", "url": "--url", "recipe": "--recipe", "note": "--note"},
     "props": {"id": S, "type": {"type": "string", "enum": ["dataset", "checkpoint", "code", "environment", "artifact", "other"]}, "name": S,
               "version": S, "subset": S, "file": _d(S, "a local file: hashed now, and its first location"), "sha256": S, "digest": S,
               "at": _d(LIST, "provider:ref, e.g. s3:s3://bucket/imagenet or wandb:team/proj/imagenet:v3"), "source": S, "url": S, "recipe": S, "note": S},
     "required": ["id", "type", "name"]},
    {"name": "resource_locate", "description": "Record another place a resource's bytes are, or were.", "args": ["id", "provider", "ref"],
     "opts": {"note": "--note"},
     "props": {"id": S, "provider": {"type": "string", "enum": ["local", "url", "git", "wandb", "mlflow", "s3", "gcs", "hf", "runpod", "other"]},
               "ref": S, "note": S}, "required": ["id", "provider", "ref"]},
    {"name": "resource_expose", "description": "Record that someone looked at a dataset (inspected, selected, evaluated, trained); criteria declared after it say so.",
     "args": ["id", "how"], "opts": {"at": "--at", "why": "--why", "words": "--words"},
     "props": {"words": WORDS, "id": S, "how": {"type": "string", "enum": ["inspected", "selected", "evaluated", "trained"]}, "at": _d(S, "when, if earlier than now"), "why": WHY},
     "required": ["id", "how"]},
    {"name": "retract", "description": "Take something back: it stays on record, stops counting, and rb says what rested on it.",
     "args": ["subject"], "opts": {"why": "--why", "words": "--words"}, "props": {"subject": _d(S, "an id (a note's too), or <experiment>/<setting>"), "why": WHY, "words": WORDS},
     "required": ["subject", "why"]},
    {"name": "freeze", "description": ("Lock an experiment's spec and criteria before the evidence, recorded as yours. With amend, bring a "
                                       "frozen experiment's record to it as it stands now (an amendment, with why)."),
     "args": ["experiment"], "opts": {"why": "--why", "words": "--words", "amend": "--amend"},
     "props": {"experiment": S, "why": WHY, "words": WORDS, "amend": _d(B, "adopt the frozen experiment as it is now")},
     "required": ["experiment", "why"]},
    {"name": "decide", "description": ("A call: accept, reject or investigate a criterion, experiment, question or setting; continue, park, stop, "
                                       "pivot or ship an experiment or a question. The user's: record one when they ask, with their words "
                                       "verbatim in words. It is proposed, not made, until the user confirms it in their own terminal "
                                       "(rb confirm <id>): it changes nothing before. revisit says when to look again. past records a call "
                                       "made before it was recorded, when as said; source (file[:line]) with quote gives the document that "
                                       "states it, which rb checks and says beside it. A past call waits for the same confirmation."),
     "args": ["subject", "action"], "opts": {"why": "--why", "revisit": "--revisit", "words": "--words", "past": "--past", "when": "--when",
                                             "source": "--source", "quote": "--quote"},
     "props": {"subject": S, "action": {"type": "string", "enum": ["accept", "reject", "investigate", "continue", "park", "stop", "pivot", "ship"]},
               "why": WHY, "revisit": S, "words": WORDS, "past": _d(B, "a call made before it was recorded: it reads Retrospective"),
               "when": _d(S, "with past: when the call was made, as said"),
               "source": _d(S, "with past and quote: the document that states the call, file or file:line"),
               "quote": _d(S, "with source: the document's own words that state the call")},
     "required": ["subject", "action", "why"]},
]
# the record's single writes take dry_run: every check runs and the answer says what it would write
# (data.would_write), writing nothing; reconcile previews without apply
DRY_RUN = _d(B, "check everything the write checks and say what it would write (data.would_write), writing nothing")
WRITES = ("project_title", "question_add", "question_move", "question_reword", "experiment_add", "experiment_file", "experiment_relate", "experiment_gate",
          "note_add", "variant_add", "metric_add", "spec_set", "spec_verify", "spec_vary", "criterion_add", "criterion_file",
          "criterion_relate", "analysis_add", "run_add", "resource_add", "resource_locate", "resource_expose", "retract", "freeze",
          "decide")
for _tool in TOOLS:
    if _tool["name"] in WRITES:
        _tool.setdefault("opts", {})["dry_run"] = "--dry-run"
        _tool["props"]["dry_run"] = DRY_RUN

RESOURCES = [
    {"uri": "rb://context", "name": "context", "description": "The record as one Markdown briefing", "mimeType": "text/markdown"},
    {"uri": "rb://status", "name": "status", "description": "The record as JSON: findings, open items, every record", "mimeType": "application/json"},
    {"uri": "rb://schema", "name": "schema", "description": "rb's vocabulary: every record kind, what it means, the command that writes it, and the sources rb requires", "mimeType": "application/json"},
    {"uri": "rb://format", "name": "rb-v1.schema.json", "description": "Rabbit Brain format 1 as JSON Schema: the shape of every file in .rb/", "mimeType": "application/schema+json"},
    {"uri": "rb://docs", "name": "AGENTS.md", "description": "The manual: rules, commands, and how a finding is read on its axes", "mimeType": "text/markdown"},
    {"uri": "rb://start", "name": "start", "description": "The start protocol: how to record work already done in a project under way (rb docs --start)", "mimeType": "text/markdown"},
]


def cli_words(name: str) -> list[str]:
    """The command a tool stands for, as its words: run_add is `rb run add`, status is `rb status`."""
    return name.split("_", 1) if name.endswith(("_add", "_set", "_verify", "_vary", "_relate", "_locate", "_scan", "_move", "_reword", "_file",
                                                "_expose", "_revoke", "_gate", "_title")) else [name]
