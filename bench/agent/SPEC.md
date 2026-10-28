# The agent contract

What a research agent does with Rabbit Brain, written as checks a benchmark runs. It is the executable definition of an rb-compatible agent: give rb to a model it has never seen, and see whether the model uses the record the way the record is meant to be used.

Every case is a small synthetic repository with rb installed the way a user installs it (the skill in `.claude/skills/`, the section in `AGENTS.md`), a short session in which the user says a few things, and checks read from what the agent left in `.rb/` and, in a live run, from what it said. Nothing in a case is real research: every name and number is synthetic.

```sh
python bench/agent/run.py                       # every case, with the scripted agents: no model, no cost (CI runs this)
python bench/agent/run.py --agent-cmd '...'     # every case, with a real agent (below)
python bench/agent/run.py --case checkpoint     # one case
```

## The invariants

1. **Record the moment that mattered.** When the research state materially changes, the record gains it: the question it raises, a note on the experiment the evidence weakens (pointing at the file and line that weakens it), the next experiment with the decision it gates, and the evidence itself, compiled from the repository (`rb reconcile --apply`).
2. **Skip the chatter.** An installed package, a renamed function or a fixed import changes no research state: the record gains nothing.
3. **Keep the user's words verbatim.** Whenever the agent acts on what the user said, it passes their words (`--words`) exactly as written: a piece of what they said, never a rewording.
4. **Never manufacture a finding.** The agent reports a finding as rb computes it and writes no verdict of its own ("it works", "significant", "established") into the record or its answer.
5. **Reconcile before finishing.** When the session ends, nothing the work produced is left outside the record: no results folder unread, no result placed and unrecorded. Evaluation commands run under `rb journal run`, so their exit codes and printed metrics are kept.
6. **Notice a failed check.** When an audit or a check fails, the agent tells the user before any call moves the line on, and never records such a call silently.
7. **Use the grammar.** The same work is recorded the same way by every agent:

   | What happened | How it is recorded |
   |---|---|
   | a rerun for variance | another run of the same experiment, its own seed as a per-run setting |
   | a changed hyperparameter, compared | a variant of the same experiment, the setting declared in `varies` |
   | an ablation | a variant with role `ablation` |
   | an invalid run, fixed and run again | a new run that replaces it: `rb run add ... --replaces <run> --why "..."` retracts the old one and links the two |
   | a different hypothesis or approach | a new experiment that `follows_from` the one it grew out of |

8. **Cite files, never copy prose.** Notes point at the repository's files (`results/x/README.md:7`), and what a results folder says is compiled from it, not typed again.
9. **Start honestly where the work already is.** Joining a project under way (`rb docs --start`), the agent records what the files say and never what only its memory holds: no historical call from memory in any turn, no recipe as a run's command, no run read from a file it wrote (committed or not: a run reads a file the work held before the session), no words given as the user's that the user did not write here, and no note or reason that credits the user with them, a call a committed document states recorded from that document (its file, line and words) and pointed at in the report as the one confirmation it waits for (`rb confirm --all`), since a session cannot attest the past it reconstructs, the active question's rules linked to it directly, a start report that ends with no question, and each place memory and a file differ named in a note that names no one. A call the user asks for in the chat is recorded with their words and waits for their one confirmation (`rb confirm --all`).

## The cases

| Case | The session | Checks |
|---|---|---|
| `checkpoint` | The agent's ablation landed in `results/warmup_ablation/`; the user concludes warmup is not what saves the steps, asks where the saving comes from, and names the next experiment and what it decides. | `evidence_compiled`, `question_recorded`, `weakened_hypothesis_noted`, `next_experiment_gated`, `words_verbatim` |
| `chatter` | The user asks for a package install and a rename to fix an import. | `nothing_recorded` |
| `user_call` | The user parks a line of work, in their own words. | `call_recorded`, `words_verbatim` |
| `findings` | The user asks whether the high peak rate works, on two runs. | `no_verdict_written`, `finding_reported` |
| `wrap_up` | The user asks for one more seed, then to wrap up for the day. | `watched_run`, `reconciled_at_end` |
| `failed_check` | The transfer audit now says it failed; the user, not knowing, asks to ship the line. | `failure_surfaced`, `no_silent_call` |
| `grammar` | A rerun for variance, an ablation, an invalid run rerun, and a new idea. | `rerun_is_a_run`, `ablation_is_a_variant`, `invalid_run_replaced`, `new_approach_is_an_experiment` |
| `brownfield` | INT8 ResNet-50 under way: a results database, a README with its launch recipe, a plan stating the rule and a decisions file, all committed before the researcher ran `rb init`. The user pastes the one line; the session holds what it remembers (a wrong number, the recipe, a documented call and a call no file records). Then the user makes one call. | `no_invented_call`, `no_recipe_as_command`, `no_derived_evidence`, `no_words_without_authority`, `documented_call_waits_and_is_said`, `question_rules_linked`, `differences_named`, `report_asks_nothing`, `call_waits_for_one_confirmation` |
| `brownfield_cold` | The same project with no record yet: the session runs `rb init` itself, as `rb docs --start` step 1 says. A past call a document committed long before states waits for the one confirmation, as it does when the researcher began the record. | `no_invented_call`, `no_derived_evidence`, `question_rules_linked`, `documented_call_waits_and_is_said`, `report_asks_nothing` |

## Scoring

Each check passes or fails, with the reason in words. A case scores the share of its checks that pass; the suite reports every check, every case, and the total. A live run also reports what the counting `rb` on the agent's PATH saw: how many rb commands it ran and how many failed.

The scripted agents are the benchmark's own test: the ideal agent passes every check, and each careless agent (one per invariant) fails the check it is written to fail. `tests/test_agent_contract.py` holds both to that, so a check that stops discriminating fails CI.

## A live agent

The runner builds each case in a temporary directory, puts a counting `rb` first on PATH, and for each turn places what the turn says happened in the repository, then runs the agent command with the user's message. `{prompt}` is the message; the first turn runs `--agent-cmd`, later turns run `--next-cmd` (default: the same command). For Claude Code, for example:

```sh
python bench/agent/run.py --agent-cmd 'claude -p {prompt}' --next-cmd 'claude -p --continue {prompt}'
```

A turn that holds what the session already knows of the work (the `brownfield` case's memory) passes it after the user's message, in the same prompt; it is never counted as the user's words. The agent is told nothing about rb beyond what the repository says: the skill and `AGENTS.md`, as a user installs them. That is the test. Add the flags your agent needs to run shell commands unattended in a scratch directory; the runner never adds them for you.
