"""rb's vocabulary, in one table, for every audience.

This is the vocabulary rb ships, never an interpretation of anyone's project. One vocabulary: the names below are the
same words in the Rabbit Brain YAML on disk, the JSON Schema, the MCP tools, the docs, the CLI and every human surface. Stability
is the point: these names do not get renamed, rebranded, or given display synonyms, and a change to this table is a
versioned decision with a reason, never a redesign side-effect. Project-local richness (metric names, variant names,
aliases, population declarations) grows as instances of these kinds, never as new kinds.

The drawers are few on purpose: questions form a tree experiments are filed under; an experiment carries its purpose,
hypothesis, variants, settings, criteria, notes and relations; runs carry results; analyses carry comparisons;
resources are what runs read and make; findings are computed on axes; decisions are calls, each attributed to whoever
made it.

`rb schema ir` prints this table; the rb://schema MCP resource serves it; the compile contract cites it; the surfaces
render from it. `rb schema all` prints the whole file format (rb-v1.schema.json) and `rb schema <kind>` each record's
shape; rb checks the provenance column on every write.

This file is where the table is changed. The binary carries it in schemas.json (what `rb schema ir` prints) and in
assets/mcp.json (the rb://schema resource), both beside this file and both written from it:
python rust/gen/gen_schemas.py && python rust/gen/gen_mcp.py
"""
from __future__ import annotations

# kind: (gloss, meaning, write, provenance)
IR: dict[str, dict[str, str]] = {
    "question": {
        "thing": "what the work is trying to find out",
        "is": "a tree: a sub-question names its parent, and an experiment may be filed under several; where it stands is read from what is below it, never a child's negative as its own",
        "write": 'rb question add "<what are you trying to find out?>" --parent <q>',
        "provenance": "wording as asked"},
    "experiment": {
        "thing": "what is tested",
        "is": "its purpose or hypothesis, one baseline and variants with roles, varies naming the intended differences; planned until its first run; an undeclared difference between variants is a confound",
        "write": 'rb experiment add "<title>" --hypothesis "<h>" --baseline <b> --candidate <c> --varies <s>',
        "provenance": "declared, then checked against its settings"},
    "variant": {
        "thing": "one arm of an experiment",
        "is": "baseline, candidate, control or ablation; its own settings override the shared ones",
        "write": "rb variant add <exp> <name> --role <r>",
        "provenance": "role declared"},
    "setting": {
        "thing": "what the experiment changes or depends on",
        "is": "a value with where it comes from; population.* declares the counting structure and never blocks",
        "write": "rb spec set <exp> <name> <value> --source file#key",
        "provenance": "REQUIRED: a source rb can read, or --unknown, or a vouch (rb decide <exp>/<name> accept)"},
    "criterion": {
        "thing": "what would count on one comparison",
        "is": "a rule over one comparison (subject, reference, metric, estimand), with a role (primary, secondary, exploratory, gate), declared by someone at a time before the evidence it reads; explicit question links may keep the author's verbatim interpretation when met, never an answer inferred by rb",
        "write": 'rb criterion add "<c>" -e <exp> -q <question> --metric <m> --subject <a> --reference <b> --better',
        "provenance": "declared by whoever wrote it, with the user's words when an agent gives them; question links snapshot the question and retain author, time, reason, session and revisions; REQUIRED for cited numbers: --source --quote --locator, checked against the file"},
    "run": {
        "thing": "one execution, and what it produced",
        "is": "results with a receipt: actor, chat session, the agent (its name, version, provider and model), time, repository state, command, environment, the resources read and made, when its output existed; the same output recorded twice is one run; a repeat checks a run and adds no unit; a wrong run is retracted (whole, or --scope for named measurements), never deleted",
        "write": "rb run add <exp> --from <file> --set seed=N --input <resource> --config <resolved>",
        "provenance": "REQUIRED: the receipt is taken when it is recorded; --from and --config are read and hashed; typed numbers are marked typed"},
    "analysis": {
        "thing": "a computation over saved outputs",
        "is": "the comparisons it produced, each with its estimate and interval, the unit it resampled, and where it came from; a reanalysis is an analysis, never a run; a later one supersedes it with a reason, and its readings stay as history",
        "write": 'rb analysis add <exp> --compare "a/b:m=est[lo,hi]" --method "<m>" --unit "<u>" --units N --source <file>',
        "provenance": "REQUIRED: computed from an output file rb hashed, or reported from a document rb checked, or marked typed"},
    "comparison": {
        "thing": "subject against reference on a metric",
        "is": "the unit of state: an estimate, its interval when there is one, what it resampled; changing the metric, the reference or the estimand is a different comparison",
        "write": "read by a criterion; produced by an analysis or by rb from the runs",
        "provenance": "an analysis, or rb's reading of the runs"},
    "result": {
        "thing": "a number a run reports",
        "is": "named <variant>.<metric>, change.<metric> (candidate minus baseline) or plain; compared across variants in rb compare",
        "write": "NAME=VALUE on rb run add, or --from <file>",
        "provenance": "the run's receipt"},
    "resource": {
        "thing": "a dataset, checkpoint, code, environment or artifact",
        "is": "identity (name, version, hash), locations (any provider), and how to get it again; availability is computed: present, changed, remote, reconstructible or lost; a run records what it found when it read or made one",
        "write": "rb resource add <id> --type dataset --name \"<n>\" --url <u>",
        "provenance": "declared; a local file is hashed when recorded"},
    "metric": {
        "thing": "what is measured, by name",
        "is": "unit, direction, aliases; a result naming an alias is read through it",
        "write": "rb metric add <m> --unit <u> --minimize",
        "provenance": "declared"},
    "finding": {
        "thing": "a criterion read on axes, computed",
        "is": "result, rule, timing, units, integrity, review and history, computed from the runs and analyses on every read, never stored; no single word stands for it",
        "write": "read it: rb show <criterion>",
        "provenance": "derived"},
    "decision": {
        "thing": "a call, with what it was made on",
        "is": "accept, reject or investigate a finding, an experiment, a question or a setting; continue, park, stop, pivot or ship a line of work; read against the readings it was made on, with when to revisit; one recorded in an agent session is proposed until the user confirms it (rb confirm), and a past one (--past) is Retrospective",
        "write": 'rb decide <subject> <action> -m "..." --revisit "..."',
        "provenance": "REQUIRED: who made it and why, with the chat session when known and the user's words verbatim when an agent acts on them; for a past call, when it was made and the document that states it"},
    "note": {
        "thing": "words about an experiment or the project",
        "is": "an assumption, a caveat, context; kept verbatim with who wrote it; changes no finding",
        "write": 'rb note add "<text>" -e <exp>',
        "provenance": "text as written"},
    "relation": {
        "thing": "how one experiment stands to another",
        "is": "reproduces, extends, follows_from or compares_with an experiment here, a uid, or a published manifest; structure, not evidence",
        "write": "rb experiment relate <exp> reproduces <target>",
        "provenance": "declared"},
}
