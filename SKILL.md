---
name: rabbit-brain
description: Rabbit Brain (rb) keeps the record of a project's ML experiments as plain YAML files in .rb/ next to the code, and computes the findings from it; use it when a repository has a .rb/ directory, or when running, recording or reporting experiments.
---

# Rabbit Brain (rb)

rb is the record of the experiments, kept next to the code in `.rb/`: questions, experiments with their hypothesis and variants, criteria declared before the evidence, runs with receipts, analyses, resources and decisions. rb reads the findings from that record, computing each one every time it is read, and every write records who made it (an agent with its model), in which chat session, and why.

In a project with `.rb/`, run `rb context --model <your model id>` first. It is the state of the work, and it replaces any summary, your own from an earlier session included; `--model` is the model you run on, which rb keeps with your writes in this session. In a project already under way whose record holds nothing yet, record where the work stands now, in the same turn, from the files, and ask nothing first: `rb context` says so and points at `rb docs --start`, the protocol for it.

## When to record

Record when the research state materially changes:

- a question or hypothesis is introduced, revised or dropped;
- an experiment or a control is proposed, or run;
- evidence changes a conclusion;
- a baseline, a variant or a metric is established;
- an important uncertainty appears;
- the next experiment, or a decision, is chosen.

"The wider model weakens the warmup hypothesis" is recorded (the run, and a note on what weakened it, pointing at the file and line), and so is "the next experiment removes warmup at recorded checkpoints" (a planned experiment with its hypothesis and the decision it gates). "Installed scipy" and "renamed a function" are implementation chatter: do not record them.

## How

Cite the repository's own files as sources (results, reports, configs, manifests) rather than copying their prose, and keep statements verbatim. When you act on the user's words from the chat, pass them verbatim with `--words "<what they wrote>"`; rb says them as words given, never as the user's, until the user confirms the call they came with. Write the record's text as plain text, since rb shows it exactly as written on every screen: Unicode symbols (≥, ×, Δ, σ, ∼) rather than LaTeX, a name a paper defines as a macro spelled out, no citation keys or table and figure references, and not the name of the agent or tool that did the work (rb records who made each write), nor the user or the chat as what a text rests on ("the researcher's rule", "stated in this chat"): their words go in `--words`.

- `rb question add "<what are you trying to find out?>" --parent <q>`: what the work wants to find out. Each inquiry is a top-level question of its own (no `--parent`), and the project's title names the theme they share.
- `rb experiment add "<title>" -q <q> --hypothesis "<h>" -m "<why>" --if-holds "<then>" --if-not "<else>"`: what is tested, and the decision it gates; it stays planned until something runs it. Arms are `--baseline <b> --candidate <c> --varies <setting>`; `rb experiment gate <exp>` sets or revises the gate later. A control or an ablation is `rb variant add`, a metric `rb metric add`, a setting `rb spec set <exp> <name> --source <file#key>`, a dataset or checkpoint `rb resource add`.
- `rb criterion add "<statement>" -e <exp> -q <q> --metric <m> --at-most <x>`: what would count, declared before the evidence it reads, and the question its finding bears on; `--when-met "<what meeting it would mean for the question>"` keeps the interpretation, verbatim, when there is one.
- `rb criterion file <criterion> <q> --why "..."`: link a criterion already recorded to the question its finding bears on. A question's reading counts only the criteria linked to it, and `rb status` gives this command for each criterion filed under a question and linked to none.
- `rb journal run -- <evaluation command>`, then `rb run add <exp> --from <metrics file> --set seed=<n> --command "..."`: what ran, with a receipt. A command whose last line is `{"metrics": {...}}` has those numbers kept, and `rb reconcile` proposes the run.
- `rb analysis add <exp> --compare "<a>/<b>:<metric>=<est>[<lo>,<hi>]" --method "..." --source <report>`: a computation over saved outputs, never a run.
- `rb note add "<text>" -e <exp>`: an assumption, a caveat or an uncertainty, verbatim; a note changes no finding. A hypothesis weakened is a note on its experiment pointing at the evidence.
- `rb decide <subject> <action> --why "..." --words "..."`: a call (accept, reject, investigate; continue, park, stop, pivot, ship). Calls are the user's: record one when they ask for it. It is proposed, not made, until they confirm it in their own terminal (`rb confirm <id>`); tell them it waits. A call made before the record takes `--past --when "..."`; `--source <file>[:<line>] --quote "..."` cites the document that states it. A past call waits for the same one confirmation: rb says beside it what git shows of the document.
- `rb retract <id> --why "..."`: take back what is wrong; it stays on record and stops counting.
- `rb reconcile`: what the repository's own files say, against the record: each results folder with a report as an experiment (a folder a run was recorded from is that run's experiment's), its reports quoted verbatim with file and line, its manifests checked against the code, its audits and checks with their last outcome, its reproduction guide, and whether git is clean enough to checkpoint. `rb reconcile --apply` keeps it, and starts `.rb/` when there is none. Run it after a stretch of work: it catches what you did not record, and is no substitute for recording it.

`rb status` lists what is open, each item with who usually acts and the exact command; `rb show <id>` prints one record with its finding. Every rb command in your session ends with what changed since you last looked (`Since this session last looked:`; `rb_delta` in JSON): read it, and tell the user what is theirs.

## The grammar

Record the same work the same way, every time:

- a rerun for variance is another run of the same experiment, with its own seed (`--set seed=<n>`);
- a changed hyperparameter, compared, is a variant (`rb variant add <exp> <name> --role candidate`), the setting it changes in `--varies`;
- an ablation is a variant with `--role ablation`;
- an invalid run, fixed and run again, is a new run that replaces it: `rb run add <exp> ... --replaces <run> --why "..."`;
- a different hypothesis or approach is a new experiment that grew out of the old: `rb experiment relate <new> follows_from <old>`;
- work in another branch or worktree is recorded there the same way, never as an experiment per branch: name the experiment and the variant on every run, and the records merge with git (`rb status` says what branches not merged yet hold).

## Boundaries

- Never compute a finding, an error, a ranking or a regression yourself: record the numbers, and read the finding.
- Never edit `.rb/` by hand: rb catches it, and `rb doctor` lists it.
- Never set or unset `RB_ACTOR` or `RB_HUB_TOKEN`, and never read the credentials file `rb login` writes.
- Report a finding by its axes as rb prints them: the comparison with its interval and units, the rule, the timing, and every integrity problem. `criterion undetermined · point only` on 2 units is said in those words, never rounded up to "it works".

Everything else is in `rb docs`. Tools instead of shell: `claude mcp add rabbit-brain -- rb mcp` from the project root; same record, same rules.
