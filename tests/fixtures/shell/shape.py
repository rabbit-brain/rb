"""The contract's shape from the example: one row per field path, its JSON type, the phase that builds it, and its tier.

Usage: python3 -I shape.py int8-demo.live.view.json int8-demo.away.view.json int8-demo.past.view.json > view-1.shape.tsv
The Live example first, then rb open's other places (place_of.py): a field's types are those it has in any of them.
{id} marks a map keyed by an object id; {key} a map keyed by a name from the record (a setting, an arm, a metric, a
citation's path:line).
The phase column follows SPRINT-OCT24.md section 1.3; the longest matching prefix wins.
"""
import json
import sys

PHASE = [  # (path prefix, phase, where today's code computes it, or what builds it)
    ("contract", "c", "new: shell/model.rs (M0)"), ("surface", "c", "new: shell/model.rs (M0)"), ("tier", "c", "new: M0 from view::Reader; shell/tier.rs holds the table in 1b"),
    ("rb_version", "today", "webui state payload"), ("at", "today", "webui state payload"), ("digest", "today", "Ledger::state_digest; 4a adds the journal key"),
    ("escaped", "today", "webui drawn()"), ("project", "today", "Home.title, Home.project_id"),
    ("home", "today", "view::home (Home)"), ("home.counts", "1b", "status counts (ledger/views.rs), not on Home today"),
    ("home.last_write", "1b", "place.last_write (places.rs)"), ("home.latest", "5H", "new: history.rs"),
    ("home.made", "1b", "new: counted from findings' timing and receipts"), ("home.quiet", "1a", "Home.quiet, reworded"),
    ("home.found_total", "today", "len(Home.found)"), ("home.waiting_line", "today", "view.rs waiting_said"),
    ("home.notes", "today", "Home.notes (split into id, at, text in M0)"),
    ("your_call", "1a", "new: Home.calls count, one source"), ("calls", "1a", "Home.calls today: subject, label, title, text, command, at"),
    ("made_calls", "1a", "call_line and why_said (ledger/mod.rs)"), ("pending", "today", "Home.pending, confirm_all_line, designs_line"),
    ("open_items", "1a", "status open[] (who != human); counts only today (agent_line)"),
    ("questions", "today", "QuestionBrief"), ("questions[].lead", "1b", "new: overview.lead"), ("questions[].more", "1b", "new"), ("questions[].no_rule", "1b", "new: FIX2, a question no rule is linked to in one reading, its card's line too; \"\" where a rule is linked"),
    ("questions[].overview.interpretations[].holds", "1b", "new"), ("questions[].overview.interpretations[].confirmed", "today", "view.rs interpretations"),
    ("questions[].calls", "1a", "overview.calls today, each once in 1a"), ("questions[].made_calls", "1a", "call_line"),
    ("questions[].next_step", "1c", "new: QuestionBrief.next_step from next[]"),
    ("questions[].overview.relations", "today", "view.rs relations; a lead of kind relation indexes this list"),
    ("questions[].overview.limits[].relations", "1b", "new: indexes into overview.relations of the relations that raise the limit"),
    ("unfiled", "1a", "new: unfiled group (calls at ':unfiled' today)"),
    ("findings", "today", "FindingLine"), ("findings.{id}.experiment", "today", "status findings[]"),
    ("findings.{id}.reading", "1b", "reading text parts (assess.rs); numbers in finding.comparison today"),
    ("findings.{id}.check_band", "1b", "the criterion's rule (status), for the figure (2b)"),
    ("findings.{id}.units_line", "1b", "new"), ("findings.{id}.rule_detail", "1b", "why_rule (ledger/findings.rs)"),
    ("findings.{id}.unit_amended", "1b", "new: amendments after the lock"),
    ("findings.{id}.observations", "1a", "status findings[].observations, dropped by FindingLine today"),
    ("findings.{id}.source", "1a", "comparison.source"), ("findings.{id}.design_cells", "1a", "comparison.design.cells"),
    ("findings.{id}.rests_on", "1a", "graph.rs (SS calls it FindingLine.rests_on: what rests on this)"),
    ("findings.{id}.receipt_rows", "1b", "finding.declared and the Criterion (SS 5.1 receipts), as rows"),
    ("findings.{id}.cited", "1b", "the criterion's source: path, line, the quote as the file reads it, commit, sha256"),
    ("experiments", "today", "ExperimentBrief"), ("experiments[].short_title", "1b", "new"),
    ("experiments[].who_line", "today", "Card.who_line (object_card)"), ("experiments[].run_ids", "today", "status experiments[].run_ids"),
    ("experiments[].reproduce", "1c", "new: reproduce.rs; rerunnability today"),
    ("experiments[].amendments", "1b", "the experiment's amendments[] after frozen.at (unit_amended links here)"),
    ("runs", "today", "RunRow (by and session never enter)"), ("runs.{id}.label", "1b", "new"), ("runs.{id}.named", "M8", "contract 1.9 (FIX8): model.rs run_names, the run as every line names it: run <label>, told apart from a run whose label reads the same by when it was recorded"), ("runs.{id}.at", "1b", "receipt.at"),
    ("runs.{id}.metrics_text", "1b", "new: rb's %.6g"), ("runs.{id}.per_run_text", "today", "run values as k=v (view.rs)"),
    ("runs.{id}.receipt_rows", "1b", "receipt_card and run_facts today, as prose"), ("runs.{id}.command", "1b", "command kinds (support.rs)"),
    ("runs.{id}.counts_toward", "1a", "graph.rs"), ("runs.{id}.same_bytes", "1a", "graph.rs"), ("runs.{id}.made", "1a", "run outputs"),
    ("runs.{id}.retracted", "today", "retraction_words"), ("runs.{id}.recorded_in", "4b", "places (webui)"),
    ("runs.{id}.reproduce", "1c", "new: reproduce.rs, the recipe narrowed to the run (Reproduce this run)"),
    ("resources", "today", "ResourceRow"), ("resources.{id}.role", "1c", "new: input role words"),
    ("resources.{id}.acquisition", "1c", "computed on read from Acquisition.recipe"),
    ("resources.{id}.read_by", "1a", "cmds/reading.rs read/made split"), ("resources.{id}.made_by", "1a", "run outputs"),
    ("resources.{id}.rests_on", "1a", "graph.rs"),
    ("analyses", "today", "status analyses (no card today: /card.json 404)"), ("analyses.{id}.used_by", "1a", "graph.rs"),
    ("history", "5H", "new: history.rs over delta::wrote"), ("history.sittings[].lines[].note", "5H", "history.rs: a session's reason apart from why (contract 1.2)"), ("history.log", "today", "Ledger::log_entries, screen form with no actor (M0)"),
    ("places", "today", "webui place, places, versions, elsewhere"), ("places.scope_bar", "4b", "new"), ("places.place.line", "today", "places.rs place line"),
    ("reproduce_url", "today", "rb export --url"),
    # contract 1.4: R7's fields, filled by M5 (M's requests.md lines 28 to 54)
    ("questions[].overview.interpretations[].calls", "M5", "view.rs interpretations: the latest call on each of its criteria, as its line says it"),
    ("project.parent", "M5", "project.yaml parent where http::is_address, as http::shareable says it; \"\" otherwise"),
    ("experiments[].relation_lines", "M5", "view::relation_lines, the card's line for each relation"),
    ("experiments[].settings_line", "M5", "view::settings_of, the card's settings line"),
    ("experiments[].tally", "M5", "view::experiment_tally, the experiment's rule counts as home.tally"),
    ("experiments[].muted", "M5", "view::experiment_tally, the met rules' words drawn muted, as home.muted"),
    ("experiments[].table.rows[].metrics.{key}.delta", "M5", "rb compare's change against the baseline"),
    ("experiments[].table.rows[].metrics.{key}.better", "M5", "rb compare's better"),
    ("experiments[].table.rows[].metrics.{key}.paired", "M5", "rb compare's paired"),
    ("experiments[].table.rows[].metrics.{key}.primary", "M5", "rb compare's primary"),
    ("experiments[].table.rows[].metrics.{key}.said", "M5", "rb compare's said"),
    ("experiments[].table.rows[].metrics.{key}.basis", "M5", "rb compare's basis"),
    ("experiments[].table.rows[].metrics.{key}.differs_in", "M5", "rb compare's differs_in"),
    ("experiments[].table.rows[].metrics.{key}.other_code", "M5", "rb compare's other_code"),
    ("findings.{id}.design_cells[].interval_text", "M5", "the cell's interval as rb says it"),
    ("findings.{id}.design_cells[].n", "M5", "the cell's units"),
    ("findings.{id}.design_cells[].unit", "M5", "what a unit is, as n says"),
    ("unfiled.experiments", "M5", "experiments filed under no question (rb status's \"Not filed under a question\")"),
    ("calls[].do", "M5", "the open item's own command, as rb status gives it"),
    # contract 1.5, R7's request: each declaration a computed comparison rests on, as declared, never who declared it
    ("questions[].overview.relations[].declarations", "R7", "new: relations::declared, as declared, with no writer"),
    # contract 1.6, Overview v2 (OVERVIEW-V2.md): the record projected onto the user's decisions, filled by M6
    ("home.standing", "M6", "model.rs standing_of: per top-level question, rb's state word, the line on why with its reasons, the tallies"),
    ("home.integrity", "M6", "model.rs integrity_of: what weakens the whole body of evidence, each part with its numbers and ids"),
    ("home.queue", "M6", "model.rs queue_of: Needs your judgment, in the fixed order, no command, hash or path"),
    ("questions[].state", "M6", "rb's state of the question"), ("questions[].tallies", "M6", "model.rs tallies_of, the criteria linked to it"),
    ("questions[].compact", "M6", "the compact row: state and tallies"),
    ("findings.{id}.state", "M6", "assess::rule_word"), ("findings.{id}.state_label", "M6", "the rule word after a count"),
    ("findings.{id}.subject_label", "M6", "model.rs arm_label, a baseline labelled as one"),
    ("findings.{id}.reference_label", "M6", "model.rs arm_label, or the cited number"),
    ("findings.{id}.reading_short", "M6", "the compact row's reading, arms labelled"), ("findings.{id}.verdict", "P4B", "model.rs verdict: the rule's one sentence (status word, reading, result class in words, distance from its number)"),
    ("findings.{id}.reading.unit_said", "P4B", "model.rs: the unit as a sentence says it (points for a difference of percentages)"), ("findings.{id}.tags", "M6", "at most two ontology tags"),
    ("findings.{id}.reads_baseline", "M6", "it reads the baseline or control arm alone"),
    ("experiments[].compares", "M6", "model.rs compares, arms labelled"),
    # CITE (CITE.md items 1 and 4): every file:line the text cites, resolved (ledger/cite.rs, M's CITE-FIELDS.md)
    # PIVOT (the experiment's comparison as a grid; a reader's queue rows say what stands): rb compare's cells, additive
    ("home.queue[].reader_title", "PV", "model.rs queue_of: what stands, for a reader, with no verb to act on first"),
    ("experiments[].table.unit", "PV", "rb compare's cell_unit: what a grid's cell counts, within its column"),
    ("experiments[].table.rows[].unvaried", "PV", "rb compare: settings the variants hold apart that the experiment does not declare varied"),
    ("experiments[].table.rows[].metrics.{key}.runs", "PV", "rb compare: how many runs the cell's mean is over"),
    ("experiments[].table.rows[].metrics.{key}.run_ids", "PV", "rb compare: the runs the cell's mean is over"),
    ("experiments[].table.rows[].metrics.{key}.count_said", "PV", "rb compare: what the cell rests on, as a grid says it (3 seeds, 1 run)"),
    ("experiments[].table.rows[].metrics.{key}.lo", "PV", "rb compare: the change's interval within its column, where the units pair"),
    ("experiments[].table.rows[].metrics.{key}.hi", "PV", "rb compare: the change's interval within its column, where the units pair"),
    ("experiments[].table.rows[].metrics.{key}.level", "PV", "rb compare: the change's interval within its column, where the units pair"),
    ("experiments[].table.rows[].metrics.{key}.no_interval", "PV", "rb compare: why the change has no interval, in plain words"),
    ("experiments[].table.rows[].metrics.{key}.criteria", "PV", "rb compare: the rules that read the cell's change"),
    ("experiments[].table.rows[].metrics.{key}.in_column", "PV", "rb compare: false where the baseline holds a setting the experiment does not declare varied otherwise"),
    ("citations", "CITE", "ledger/cite.rs of_document: the cited lines, commit and sha256; a shared page quotes only what git tracks, no log or transcript"),
]
LIVE_ONLY = ("places", "findings.{id}.your_words", "findings.{id}.observed", "findings.{id}.observed_short", "findings.{id}.observed_places",
             "runs.{id}.observed_in", "runs.{id}.recorded_in", "resources.{id}.where_short", "digest",
             "questions[].overview.relations[].declarations[].words", "citations.{key}.open_url")
ELEMENTS = [  # element shapes of lists every example leaves empty, as rb computes them (requests applied at MP-1020-0828)
    ("home.unplaced[]", "string"), ("home.muted[]", "string"), ("home.tree[].muted[]", "string"),
    ("questions[].changed[]", "string"), ("questions[].ruled[]", "string"), ("questions[].summary[]", "string"),
    ("questions[].overview.relations[]", "object"), ("questions[].overview.relations[].summary", "string"),
    ("questions[].overview.relations[].outcome", "string"), ("questions[].overview.relations[].comparisons", "list"),
    ("questions[].overview.relations[].comparisons[]", "object"), ("questions[].overview.relations[].comparisons[].source", "string"),
    ("questions[].overview.relations[].comparisons[].target", "string"), ("questions[].overview.limits[].relations[]", "number"),
    ("experiments[].notes[]", "string"), ("experiments[].note_ids[]", "string"), ("experiments[].relations[]", "string"),
    ("findings.{id}.rests_on.relations_out[]", "object"), ("findings.{id}.rests_on.relations_out[].kind", "string"),
    ("findings.{id}.rests_on.relations_out[].criterion", "string"), ("findings.{id}.rests_on.relations_in[]", "object"),
    ("findings.{id}.rests_on.relations_in[].kind", "string"), ("findings.{id}.rests_on.relations_in[].criterion", "string"),
    ("pending.confirm_all", "object"), ("pending.confirm_all.line", "string"), ("pending.confirm_all.confirm", "string"),
    # contract 1.4 (M's requests.md lines 28 to 54)
    ("experiments[].relation_lines[]", "string"), ("experiments[].muted[]", "string"), ("experiments[].table.rows[].metrics.{key}.differs_in[]", "string"),
    ("unfiled.experiments[]", "string"),
    # contract 1.5 (R7's request; the words given with a declaration are the user's, so Live only, as the tier table says)
    ("questions[].overview.relations[].declarations", "list"), ("questions[].overview.relations[].declarations[]", "object"),
    ("questions[].overview.relations[].declarations[].criterion", "string"),
    ("questions[].overview.relations[].declarations[].kind", "string"),
    ("questions[].overview.relations[].declarations[].target", "string"),
    ("questions[].overview.relations[].declarations[].orientation", "string"),
    ("questions[].overview.relations[].declarations[].declared_at", "string"),
    ("questions[].overview.relations[].declarations[].why", "string"),
    ("questions[].overview.relations[].declarations[].words", "string"),
    # PIVOT: a setting the variants hold apart undeclared; the rules a cell's change is read by
    ("experiments[].table.rows[].unvaried.{key}", "string"), ("experiments[].table.rows[].metrics.{key}.run_ids[]", "string"),
    ("experiments[].table.rows[].metrics.{key}.criteria[]", "string"),
]
TOP = {"findings", "runs", "resources", "analyses"}
DYN = (".says", ".observations[].per_run", ".by_arm", ".by_question", ".table.rows[].varies", ".table.rows[].where", ".table.rows[].unvaried", ".table.rows[].metrics")


def kind(v):
    return {dict: "object", list: "list", str: "string", int: "number", float: "number", bool: "bool", type(None): "null"}[type(v)]


def child(p, k):
    """The path of key k in the object at path p: {id} in a map of objects, {key} in a map of the record's names."""
    if not p:
        return k
    if p in TOP:
        return p + ".{id}"
    if p == "citations":
        return p + ".{key}"
    if p.endswith(DYN) or (p.endswith(".per_run.by_arm.{key}[]") and k != "run"):
        return p + ".{key}"
    return p + "." + k


def nodes(v, p=""):
    """Every value of a document with its path, the document itself first (path "")."""
    yield p, v
    if isinstance(v, dict):
        for k, x in v.items():
            yield from nodes(x, child(p, k))
    elif isinstance(v, list):
        for x in v:
            yield from nodes(x, p + "[]")


def phase(p):
    best = max((r for r in PHASE if p == r[0] or p.startswith(r[0] + ".") or p.startswith(r[0] + "[")), key=lambda r: len(r[0]), default=("", "?", "?"))
    return best[1], best[2]


def tier(p):
    return "live" if p.startswith(LIVE_ONLY) else ("owner" if p == "reproduce_url" else "both")


def rows_of(docs):
    rows = {}
    for doc in docs:
        for p, v in nodes(doc):
            if p:
                rows[p] = "|".join(sorted(set(rows.get(p, kind(v)).split("|")) | {kind(v)}))
    rows.setdefault("reproduce_url", "string")
    for p, k in ELEMENTS:
        if p in rows:
            rows[p] = "|".join(sorted(set(rows[p].split("|")) | {k}))
            continue
        parent = p[:-2] if p.endswith("[]") else p.rsplit(".", 1)[0]
        keys = list(rows)
        at = max(i for i, q in enumerate(keys) if q == parent or q.startswith(parent + ".") or q.startswith(parent + "["))
        rows = dict([*[(q, rows[q]) for q in keys[:at + 1]], (p, k), *[(q, rows[q]) for q in keys[at + 1:]]])
    return rows


if __name__ == "__main__":
    rows = rows_of(json.load(open(name, encoding="utf-8")) for name in sys.argv[1:])
    print("path\ttype\tphase\ttoday\ttier")
    for p, k in rows.items():
        ph, today = phase(p)
        print(f"{p}\t{k}\t{ph}\t{today}\t{tier(p)}")
