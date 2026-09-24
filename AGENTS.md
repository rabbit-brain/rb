# Rabbit Brain: AGENTS.md

Rabbit Brain (`rb`) keeps the research state of ML work done with coding agents, as plain files in `.rb/` next to the code. It holds what the work is trying to establish: claims, each with a criterion fixed before the runs. It holds how it is tested: experiments, their variants, and every setting with where its value came from. It holds what was run, as evidence with a receipt, and what a person decided. **Agents propose; `rb` checks sources and computes verdicts; people decide.** Release review for iterative perception models is built in (`rb review`), and its runs attach as evidence.

**If the project has a `.rb/` directory, run `rb context` before anything else.** It is the state of the work. It replaces anyone's summary of it, including your own from an earlier session.

Read this whole file before running anything. **Do not compute errors, regressions, rankings, stability or verdicts yourself.** `rb` defines them, applies the same definitions every time, and records how. That is what makes a result comparable across runs, sessions and people. If you find yourself writing a script that decides whether a result holds or which model is better, stop. Attach the numbers with `rb evidence attach` and read the verdict with `rb show`. For two checkpoints of a perception model, use `rb review run`, or `rb review import` when the per-case numbers already exist.

## Install

```sh
pip install rabbit-brain             # or: uvx --from rabbit-brain rb
pip install "rabbit-brain[yaml]"     # adds pyyaml, to read settings from YAML configs by key (JSON and TOML need nothing)
pip install "rabbit-brain[raft]"     # release review's RAFT adapter: torch, torchvision, numpy, opencv, scipy, pillow
pip install "rabbit-brain[evidence]" # numpy and pillow only: evidence sheets for a custom review adapter
rb version
rb docs                              # prints this file
```

Python 3.10 or later. The core depends only on pydantic. `rb` works on your machine and uploads nothing unless you run `rb workspace push`.

## The research state (`.rb/`)

`rb` keeps a line of work as JSON files in `.rb/`, one per object. Commit them with the code, and the state travels with the branch. `.rb/log.jsonl` records every write: who made it and how `rb` knew, through what (`via`: the CLI, the SDK, or MCP), what changed, and the hash of what was written. `rb init` sets git to merge it line by line. `rb` also keeps a local copy of each write in `.rb/objects/` (gitignored), which `rb doctor --restore` puts back; a write that came from elsewhere is found in git history.

### The rule: agents propose; rb checks and computes; people decide

- You may add questions, hypotheses, assumptions, experiments and their variants, metrics, settings with their sources, claims, and evidence. You may retract what an agent added while nothing rests on it; what a person wrote is theirs to retract.
- `rb` reads every source it can, and it computes every verdict each time the verdict is read. No verdict is stored. Nothing you write can mark a setting verified. **Never edit `.rb/` by hand.** `rb` compares each file with its last write. While any object is edited, deleted or written into `.rb/` outside `rb`, no claim is established, every `--fail-on` gate fails, and `rb` will not write over that object (`E_STATE_EDITED`). `rb doctor` lists them; `rb doctor --restore` puts back what `rb` last wrote, and a person who wants the change keeps it with `rb doctor --adopt --why "..."`. A file that no longer parses stops commands with `E_STATE_CORRUPT`, and `rb doctor` still runs.
- These calls belong to a person:
  - `rb freeze`, and `rb freeze --amend`
  - `rb decide`
  - `rb retract` of something a person wrote, or that other objects rest on
  - `--amend` on a frozen experiment
  - `rb doctor --adopt`

  From an agent they fail with `E_HUMAN_ONLY`, and `errors[0].handoff.command` holds the exact command line. Give it to the person as it is, to run in their own terminal. Do not set or unset `RB_ACTOR` to get past it.

**Who you are to rb.** Every write records an actor. `rb` works it out in this order:

1. An agent runtime it detects: Claude Code by `CLAUDECODE=1`, `AI_AGENT`, Codex by `CODEX_SANDBOX` (and its other sandbox variables), or GitHub Actions. Inside one, `RB_ACTOR=agent:<name>` names the agent, and `RB_ACTOR=human:<name>` is ignored: a person's name typed inside an agent session is exactly what an agent would type.
2. Otherwise `RB_ACTOR` (`human:<name>` or `agent:<name>`), when set.
3. Otherwise a person, named from git (`user.email`'s local part, else `user.name`), or from the login.

Every command that writes prints who it recorded. A person working in an agent's terminal is recorded as the agent too, and makes their calls from their own terminal, which is what the handoff says.

### A session, start to finish

```sh
rb init "Does INT8 keep RAFT's accuracy on KITTI?"   # creates .rb/ here; commands then work from any subdirectory
rb question add "Can we ship INT8 RAFT?"              # → q67pn (each add prints the id: a kind letter and four characters, or --id)
rb hypothesis add "INT8 keeps EPE within 0.05 px of FP32" -q q67pn -m "late updates are small"
rb experiment add "INT8 vs FP32 on KITTI-2015" --id int8 --hypothesis hw25q --baseline fp32 --candidate int8 --varies precision
rb assumption add "KITTI train is not in the pretraining set" -e int8
rb metric add epe --unit px --minimize --alias endpoint_error
rb spec set int8 --from configs/train.yaml --keys "optim.*,data.batch_size,eval.iters"   # each key a setting, read by key path, verified
rb spec set int8 fp32.precision fp32 --source configs/fp32.yaml#precision                # a variant's own value
rb spec set int8 int8.precision int8 --source configs/int8.yaml#precision
rb spec set int8 seed --per-run                      # each run gives its own: --set seed=N when attaching
rb claim add "INT8 costs at most 0.05 px EPE" -e int8 --metric change.epe --at-most 0.05 --min-n 3 --noise 0.01
rb freeze int8 -m "before the held-out runs"         # a person's call: settings, variants and criteria are now locked
rb evidence attach int8 --from out/seed1/metrics.json --set seed=1 --config out/seed1/.hydra/config.yaml --command "python eval.py --seed 1"
rb evidence attach int8 --run <review run id> --set seed=2                               # or an rb review run
rb status --fail-on unestablished                    # the CI gate: exit 1 unless every claim is established
rb show <claim id>                                   # its verdict, every observation, everything it rests on
rb compare int8                                      # variants by metrics, with the change against the baseline
rb context                                           # the handoff pack a fresh session reads first
```

### Experiments and variants

An experiment compares **variants**, and each variant has a role: `baseline`, `candidate`, `control` or `ablation`. Name them on `rb experiment add` with `--baseline` and `--candidate`, or add one later with `rb variant add <exp> <name> --role ablation`. An experiment has one baseline. `--varies` names the settings the variants differ in on purpose, and `rb spec vary <exp> <name>` declares another later.

A setting belongs either to the whole experiment (`optim.lr`) or to one variant (`int8.precision`, or `--variant int8`). A variant's own value overrides the shared one for that variant. **A setting that differs between variants and is not declared in `varies` is a confound.** It blocks every claim on the experiment until someone declares it, sets it the same in every variant, or a person accepts it: `rb decide int8/data.batch_size accept -m "..."`. `rb experiment add --like <exp>` copies another experiment's shared settings, as provisional, and what it varies.

### Settings: a value, where it comes from, and whether rb has checked it

`rb spec set <exp> <name> <value> --source <where>` records a setting. When the source can be read, `rb` checks it on the spot; `--no-verify` skips the check. Each setting has one of four statuses:

- **verified**: the source states this value at a recorded commit. `rb` keeps the commit, the file's hash and the text it read.
- **provisional**: the value was given but not checked. A url or a note cannot be checked, and neither can a value no source has confirmed yet.
- **unknown** (`--unknown`): nobody has found it yet.
- **inherited**: taken from a parent investigation.

**Sources.**

| form | example | notes |
|---|---|---|
| `file#key.path` | `configs/train.yaml#optim.lr` | YAML needs the `yaml` extra; JSON and TOML need nothing. The value is optional: `rb` reads it. |
| `file:LINE` | `configs/train.yaml:3` | A YAML line becomes its key path when that key is the setting's own (`optim.lr` for `lr`). |
| `file` with `--quote "exact text"` | | For prose. Add `--term "learning rate"` when the line calls the setting something else. |
| `run:PATH#/json/pointer` | `run:rb-runs/<id>#/dataset/count` | A run directory means its `record.json`. |
| `https://...` or `note:text` | | Recorded, but it stays provisional. |

`--commit <sha>` reads the file at that commit, which never goes stale. `--locator "§4.2"` says where a reader finds the value. Paths are relative to where you run the command, and must be inside the project and outside `.rb/` (`E_SOURCE_OUTSIDE` otherwise). `rb spec set <exp> --from <config> --keys "optim.*,model.depth"` records many settings at once from one config, each sourced by its key path. Choose the keys that define the experiment: a key like `log_every_n_steps` should not stop runs counting when it changes. A key whose value is not one (a section, an empty value) is skipped and named, before anything is written.

**What "states" means is strict, on purpose.**

- **The source must name the setting.** A key (`file#optim.lr`), a run pointer, or the line a quote is on must name it: the same last word (`lr`), or the word given with `--term "learning rate"`. A line that happens to hold the value under another name does not verify it.
- A comment line never verifies, and in a config or code file a trailing comment is not read: `lr = 1e-4  # the paper used 3e-4` states 1e-4. On a line with several numbers, the setting's is the one nearest its name: in `default=0.01, help="try 0.05"` it is 0.01, and in "the learning rate is 0.01 and weight decay 0.0001" weight decay's is 0.0001. A `--term` must be the words the text uses for the setting, not a word like "the".
- A number is a standalone token compared exactly. `lr: 0.0001` states `1e-4`, and a sentence ending `... is 0.0001.` states it too, but `resnet50` does not state 50, and `1700000001` does not state 1700000000.
- Text is a whole token: `adamw` does not state `adam`. Text stays text: the version `"11.10"` is not the number 11.1. YAML's `1e-4` is a number.
- A list states its elements in order.

When the source states a *different* value, `rb` records a **conflict** on the setting. The conflict blocks its claims, and re-verifying does not clear it. Only setting the value the source states, or a different source, clears it. Every read re-checks every verified setting:

- the same key or text found on another line is **moved**, which is fine and noted;
- a file that no longer states the value is **stale**, which blocks;
- a file that now states another value is a **conflict**, which blocks.

`rb spec verify <exp> [names]` re-reads the sources. A check that fails is recorded on the setting, and `rb status` then asks for a source that states the value rather than the same check again. It exits 1 when any setting it could check did not verify, with `E_SOURCE_UNRESOLVED` per setting in `data.outcome.failures`. A url or a note is reported as not checkable (`E_SOURCE_UNVERIFIABLE`) and stays provisional without failing the command.

A required setting that is unknown or provisional keeps every claim on its experiment from being established. `--optional` says it should not. A person can vouch for a value nobody can check: `rb decide int8/cuda accept -m "read it off the cluster image"`. The vouch covers that value only; a new value needs a new decision, and a setting with no value has nothing to vouch for.

**Per-run settings and cited values.** `--per-run` is for what each run chooses (a seed). Each piece of evidence must then give its own with `--set seed=2`, or it is refused. `--cited 1e-4 --cited-source <where>` records the value a cited source used (a paper, its evaluation code). A difference between the two makes claims that cite a number `not_comparable`.

`verified` means the source states the value. It does not mean a run used it. To check that, attach the run's own resolved config with `--config` (below).

### Metrics

`rb metric add epe --unit px --minimize --alias endpoint_error` adds a metric to the experiment catalogue: its unit, which way is better (`--minimize`, `--maximize`, or neither), and other names the evidence may use for it. Evidence that reports `endpoint_error` is then read as `epe`. `rb compare` uses the direction to say whether a change is better or worse. `rb status` names any metric the evidence reports that no claim reads.

### Claims

`rb claim add "<statement>" -e <exp> --metric <m> <criterion>` takes exactly one criterion: `--at-most X`, `--at-least X`, or `--equals X --tolerance T`. **Write the claim before the run that tests it.** A criterion counts as fixed when a person wrote it, or, for a claim an agent wrote, when a person freezes (or amends) the experiment after it. Evidence attached before that is exploratory, and it never counts.

`--metric` names a number the evidence reports:

- `epe` reads a number named `epe`;
- `int8.epe` reads variant `int8`'s number;
- `candidate.epe` reads the candidate's;
- `change.epe` is the candidate's minus the baseline's, worked out by `rb` from the same piece of evidence. Attach the variants' own numbers, not a change you computed; a given `change.epe` that disagrees with them is not read.

Two names in one piece of evidence that read as the same metric (an alias and its metric) and disagree are not read either.

`--over each` (the default) means every run must meet the criterion; `--over mean` means their mean must. `--min-n 3` is how many independent confirmatory runs it takes. `--noise 0.01` is the run-to-run spread. A margin smaller than the noise is **borderline** and blocks. Once there are three runs, `rb` also uses twice their standard deviation, whichever is larger: a stated noise never lowers what the runs show. `rb` prints the criterion with its `n` and noise everywhere it shows it, so the person who freezes sees all of it.

A claim with `--source` cites a number someone else stated, such as a paper's table: save the text in the repository and give `--quote` and `--locator`. A file source must state the target, or the claim is refused. Its verdict is `reproduced` or `not_reproduced`.

### Evidence

```sh
rb evidence attach <exp> int8.epe=5.64 fp32.epe=5.61 latency_ms=25.1   # typed numbers; recorded as typed
rb evidence attach <exp> --variant int8 epe=5.64                        # the same as int8.epe=5.64
rb evidence attach <exp> --from metrics.json                            # JSON/YAML/TOML; nested keys join with dots
rb evidence attach <exp> --run <review run id>                          # an rb review run or results file
```

These options add to any form:

- `--set seed=2` gives the per-run settings.
- `--config <file>` checks the run's own resolved config against the spec and the `--set` values: a Hydra `config.yaml`, a W&B `config.yaml` (its `value:` wrappers are read through), or a `hparams.yaml`. A run whose config gives a setting a different value is not counted, and says why; a setting the config does not mention is listed as absent, and a config that mentions none of them leaves the run's config unchecked.
- `--artifact <file>` hashes an output into the evidence.
- `--link wandb=<url>` records where else the run lives.
- `--command` records what produced the numbers. `rb` did not run it.
- `--commit` records the commit that produced them.

The **receipt** records the actor, the time, and the repository's state *when the evidence is attached*: the commit, and whether the tree had uncommitted changes, with a hash covering the diff and the untracked files (`.rb/` left out). For an `rb review run`, the receipt also keeps the run's own receipt and the hash of its `record.json`. Attach evidence from the tree that produced it.

A review run's baseline and candidate become the experiment's baseline and candidate. When the run's model names are the experiment's variants in the other roles, or the experiment has several candidates, the attach is refused: attach the numbers by variant name instead.

A review run attaches:

- `baseline.<metric>`, `candidate.<metric>` and `change.<metric>`, only when some case had ground truth, because an error that was not measured is not a zero;
- `cases`, `with_gt`, `regressions`, `improved`, `flagged`, `unstable` and `borderline`.

The same numbers and files attached twice are recognised as a duplicate and not written again. A deliberate repeat takes `--again --why "..."`; it is kept on record, and the same numbers count once toward a claim however they are attached. Evidence from `rb review example` or the synthetic adapter is marked synthetic and never counts. Wrong evidence is retracted with a reason, never deleted: `rb retract <id> -m "..."`.

### Verdicts, and when a claim is established

`rb` computes a claim's verdict from its evidence every time it is read. Each piece of evidence is an observation with one of three roles:

- **confirmatory**: it counts.
- **exploratory**: attached before a person fixed the claim's criterion: before a person wrote it, or, for a claim an agent wrote, before the person's freeze. It is shown, never counted.
- **not counted**: synthetic, edited outside `rb`, attached under a spec that has changed since, run with a config that disagrees with the spec, or a repeat of a run already counted (the same numbers again, or the same per-run values such as `seed=1` reporting the same metrics).

The verdict comes from the confirmatory observations alone:

| verdict | meaning |
|---|---|
| `untested` | no confirmatory evidence |
| `supported` / `refuted` | own claims |
| `reproduced` / `not_reproduced` | cited claims |
| `mixed` | some runs meet the criterion and some do not |
| `not_comparable` | a cited claim whose settings differ from the source's |
| `inherited` | from a parent investigation |

A claim is **established** only when **all** of these hold:

- it is supported or reproduced on confirmatory evidence;
- **a person fixed the criterion**: a person wrote the claim, or a person froze (or amended) the experiment after the claim existed;
- every required setting is verified or vouched for;
- no conflict, stale source, confound, or change to a frozen spec;
- the cited source was checked, for a claim that cites one;
- no observation is borderline;
- there are at least `--min-n` confirmatory runs;
- the evidence is not synthetic, and nothing in `.rb/` was changed outside `rb`.

Otherwise it is, for example, `supported, not established`, with every reason listed. Report it exactly that way.

### Freezing, amending, deciding, retracting

**Freezing.** `rb freeze <exp> -m "..."` records a hash of everything that decides what the experiment's claims read:

- its variants and what it varies;
- every setting's value, whether it is required, where its value comes from, and the value a cited source used;
- every claim's criterion;
- the catalogue entries the claims' metrics use.

It prints each criterion it locks. After that, changing any of them fails with `E_FROZEN`. A person can make the change anyway by giving `--amend --why "..."` on the same command; the change, the reason and the hash before and after are kept. Verifying a setting changes nothing. A frozen experiment that no longer matches its record (a metric redefined through the catalogue, say) is reported and blocks its claims until a person adopts it as it is with `rb freeze <exp> --amend --why "..."`.

**Deciding.** `rb decide <subject> accept|reject|investigate -m "..."` works on a claim, hypothesis, assumption, question, experiment, or a setting (`<exp>/<name>`):

- A decision on a claim records the verdict at that moment, so the decision is always read against what it was made on. A person may accept a refuted claim, and `rb status` shows both.
- Accepting or rejecting a hypothesis sets it `accepted` or `rejected`. A verdict alone never does.
- A question becomes `answered` or `dropped`, and an assumption `assumed` or `violated`.
- Accepting a setting vouches for its value; accepting one that differs between variants accepts that difference, and lapses when a value changes. Rejecting it blocks its claims until the value changes.

**Retracting.** `rb retract <id or exp/name> -m "..."` takes anything back: an object, a setting, or a variant (`rb retract int8/fp16`). It stays on record and stops counting. Retracting what a person wrote, or what other objects rest on (a metric a claim reads, evidence a claim counts), is a person's call. On a frozen experiment a retraction is recorded as an amendment. A retracted experiment blocks its claims and takes no more settings, claims or evidence.

### Reading the state

`rb status [<exp>]` prints, in order:

1. a tally of the claims;
2. **Needs a person**;
3. **Agent can do**;
4. each claim with its standing and blocking caveats;
5. each experiment with its setting counts.

Every item in the two lists says who can act and gives the exact command. Work from **Agent can do**, and hand **Needs a person** to the person. `--fail-on` takes `unestablished`, `untested`, `refuted`, `not_reproduced`, `undecided`, `unknown` or `stale` (repeatable or comma-separated) and exits 1 when any of them applies. A decision counts only while the verdict it was made on still stands. While anything in `.rb/` is changed outside `rb`, every gate fails.

The other read commands:

- `rb show <id>` prints one object in full: a claim with its verdict and observations, an experiment with its spec, evidence with its receipt, a setting (`int8/optim.lr`) with its history.
- `rb compare <exp>` prints the experiment's own table: variants by metrics, the mean over the evidence that counts (each run once, none run with a config that disagrees with the spec), and the change against the baseline, read in the metric's direction.
- `rb log [-n N] [id]` lists the writes: who, how `rb` knew it, through what, and what changed.
- `rb context` prints the whole state as a Markdown handoff pack. It is what a fresh session, or a colleague, reads first.
- `rb doctor` in a project with `.rb/` checks the research state: who you are recorded as, files changed outside `rb` or left mid-merge, files that do not parse, and sources outside the repository. `rb doctor --restore` puts back what `rb` last wrote; `rb doctor --adopt --why "..."` (a person) keeps the changes as they stand.
- `rb schema <kind>` prints each object's shape. The kinds are `investigation`, `question`, `hypothesis`, `assumption`, `experiment`, `metric`, `claim`, `evidence`, `decision` and `verdict`.

**When you report to a person from this state:**

- quote each claim's verdict, and whether it is established;
- name every blocking reason `rb show` lists;
- say which settings are provisional rather than verified;
- never describe a claim as settled that `rb status` does not show as established.

### From Python, and over MCP

The same state and rules are there for the code that does the work (`via: sdk` in the log):

```python
import rabbit_brain as rb

state = rb.open()                                    # the .rb/ here or above; rb.init("...") starts one
exp = state.experiment("int8")                       # or state.add_experiment("...", baseline="fp32", candidates=["int8"], varies=["precision"])
exp.spec.set("optim.lr", source="configs/train.yaml#optim.lr")   # read from the file and verified
exp.spec.set("seed", per_run=True)                   # each run gives its own
claim = exp.claim("INT8 costs at most 0.05 px EPE", metric="change.epe", at_most=0.05, min_n=3)

with exp.run(seed=2, config="outputs/2/.hydra/config.yaml") as run:
    ...                                              # evaluate
    run.log({"fp32.epe": fp32_epe, "int8.epe": int8_epe})
print(claim.verdict().status, claim.verdict().not_established_because)
```

A run block works like this:

- Its numbers are attached as one piece of evidence when the block ends without an error, recorded as `logged`.
- The receipt's command is the script's own command line.
- A block that raises attaches nothing.
- The last value logged under a name is the one attached. numpy and torch scalars are fine.

`exp.spec.verify()`, `exp.spec.vary(...)`, `exp.attach({...})`, `exp.compare()`, `state.status()` and `state.context()` do what their commands do. The person's calls (`exp.freeze(why=...)`, `state.decide(...)`, `state.retract(...)`, and `amend=` on any change) raise `RBError` with `code == "E_HUMAN_ONLY"` for an agent, and `err.extra["handoff"]["command"]` holds the `rb` line for the person.

`rb mcp` serves the same operations to an agent over the Model Context Protocol (stdio). Each tool is the command of the same name (`context`, `status`, `show`, `compare`, `log`, `question_add`, ..., `spec_set`, `spec_verify`, `spec_vary`, `claim_add`, `evidence_attach`, `retract`, `freeze`, `decide`). It returns the same envelope as `--json`, and there are resources `rb://context`, `rb://status` and `rb://docs`. **Every call on a connection is recorded as `agent:<client name>`, whatever `RB_ACTOR` says,** because a model is making it. So `freeze` and `decide` return `E_HUMAN_ONLY` with `handoff.command` for the person. For Claude Code, add it from the project root: `claude mcp add rabbit-brain -- rb mcp`. `--agent <name>` sets the name recorded, whatever the client sends.

Planned, and not in this version:

- `rb paper`: a paper's claims and settings as a pre-filled experiment.
- `rb open`: a local UI.
- `rb publish` and `rb clone`.

Each of these prints what to do today.

## Release review (built in): `rb review`

Release review compares a candidate checkpoint of an iterative perception model against the current one. It runs both on a case set (or reads results you already have), ranks the cases that regressed on error or never settled during refinement, explains each one, and keeps checks so the next checkpoint gets the same review. Its commands live under `rb review`; the short forms from earlier versions (`rb run`, `rb findings`, ...) still work. A review is evidence like any other: `rb evidence attach <exp> --run <run_id>` attaches its numbers and its receipt to an experiment (see Evidence above).

### When to use it, and when not

Use it when there are two checkpoints (or two models for the same task), a set of cases with or without ground truth, and a release decision to make. Iterative models (RAFT-family optical flow, RAFT-Stereo-style depth) also get stability findings from their own refinement trajectory, which needs no labels.

Do not use it for training, hyperparameter search, certifying a model, or metrics where higher is better (convert those to an error first).

### Two ways in

**Run it** (`rb review run`): the adapter executes both checkpoints on the case set, records the trajectory with a forward hook, computes the per-case error, and writes the run. Built-in adapters: `raft` (princeton-vl/RAFT and forks with the same `core/` layout; KITTI-style datasets) and `synthetic` (a test double that runs anywhere in seconds; its output says so). Any other model: a custom adapter (below).

**Import results** (`rb review import`): per-case errors (and optional trajectories) your evaluator already produced, as version-1 JSON or a metrics CSV. Everything downstream is identical.

### The runner workflow

```sh
rb review onboard                                             # writes brief.json to fill in: task, architecture, checkpoints, data, labels
rb review onboard --brief brief.json                          # → rb.toml, an adapter or a scaffold with named TODOs, INTEGRATION.md
rb review init --project <name> --adapter raft --model-code ./raft --dataset ./data/kitti2015/training [--device cpu]
rb review doctor [--checkpoint <ckpt>]                        # environment, adapter, model code, dataset, checkpoints
rb review verify-hook --checkpoint <ckpt>                     # one case: the recorder must fire once per iteration
rb review verify-adapter --checkpoint <ckpt>                  # a few cases: the adapter must reproduce the model repository's own evaluation
rb review run --baseline <ckpt-A> --candidate <ckpt-B> --json # the review → rb-runs/<run_id>/ (runs the adapter check first)
rb review findings <run_id> --top 5                           # the ranked queue, the summary, the verdict
rb review case <run_id> <case_id> [--render]                  # one case: numbers, trajectory statistics, the reasoning; --render writes its evidence PNGs
rb review check save <run_id> <case_id>                       # keep this case for the next checkpoint
rb review check run <run_id> --checks checks.json             # next time: exit 1 if a saved check fails or a case is flagged
rb review report <run_id> --print                             # the receipt a human reads
rb review report <run_id> --open                              # the same review as report.html, for a human to read in a browser; --embed to make it a single attachable file
rb review share <run_id>                                      # anonymised statistics of the run as share.json, for the human to send if they choose; nothing is sent
```

`rb review init --demo` writes a synthetic project (two demo checkpoints under `ckpt/`) so the whole workflow can be exercised without a model or a GPU; `rb review example` creates a run from built-in example results the same way. `rb review runs` lists runs, `rb review check list` and `rb review check rm <case_id>` manage saved checks, `rb schema <name>` prints a JSON Schema; `<run_id>` may also be a run directory, a `bundle.json`, or a version-1 results file read in place.

Flags on `rb review run`: `--limit N` (first N cases; use it for a pilot), `--no-trajectories` (stability then reads "not assessed", never "settled"), `--skip-reference` (do not check the adapter against the reference evaluation; the receipt says so), `--device cuda|cpu`, `--seed 0`, `--baseline-name/--candidate-name` (default: checkpoint file stems), `--fail-on none|regressions|flags|checks` (default `none`: findings are data, not errors), `--quiet`, and the limits `--max-regression 0.3 --max-late-share 0.25 --max-reversals 2 --max-trajectory-regression 0.3 --max-last-update <off>` (defaults for a project made by `rb review init`; `rb review import` leaves the trajectory-regression limit off unless you pass it; the late-share and reversal limits are generic heuristics, and a scorer fitted to the model is a separate, paid step). `rb review findings` takes `--filter flagged|all|regressions|unstable|improved-unstable|settled-regressions|improved|stable` (default `flagged`), `--sort priority|error-change|candidate-error|current-error|late-share|name` (`candidate-error` answers "where is the candidate worst in absolute terms", which the priority queue does not), `--top N`. Vocabulary: `stable` and `improved` are error outcomes (the change stayed within `max_regression`, or fell below it); `unstable` and `settled` are trajectory words. The findings table prints `late` (the candidate's share of refinement in the last third), `move` (its late movement minus the current model's, in the metric's unit per iteration) and `rev` (its reversals). Every command takes `--runs-dir` (repeated in the `next` hints when set) and `--verbose` (warnings from the model code are otherwise counted on stderr and hidden).

### Starting from a description of the setup: `rb review onboard`

`rb review init` assumes someone already knows which adapter fits and what to put in `rb.toml`. `rb review onboard` is for the case before that: a team describes their setup once and gets a configured project back. `rb review onboard` with no arguments writes `brief.json`, a filled example to replace with their own: project, task (flow | stereo | depth | generic), architecture, framework, where the model code and checkpoints are, how one case is stored, whether there is ground truth (`all` | `some` | `none`), refinement iterations, an optional evaluation script, an optional metric override, and one sentence on the decision the review has to support. `rb schema brief` prints the schema. It is a local file that generates local files.

`rb review onboard --brief brief.json` then writes `rb.toml`, `INTEGRATION.md`, and one of two things. If the architecture is covered by a built-in adapter (RAFT-family flow today), that is all: the config points at it and there is nothing to write. Otherwise it writes `rb_adapter.py`, a scaffold that imports, that `rb review doctor` can load, and whose unwritten parts each carry a TODO saying what that method must return, with the brief's own details (the case layout, the label situation, the metric and its unit, the evaluator's path) written into the docstrings that need them. It is deliberately not a working adapter: nothing can guess a team's loader, their valid mask or their metric formula, and a scaffold that quietly returned plausible numbers would be the exact failure adapter agreement exists to catch.

`INTEGRATION.md` carries the ladder that turns either into a trusted run, each step saying what it proves: `rb review doctor`, `rb review verify-hook`, `rb review verify-adapter`, `rb review run --limit 5`, then the real review. An agent handed the directory should read it and `rb docs` before touching anything.

### rb.toml

```toml
[project]
name = "warehouse-perception"     # saved checks follow it
task = "flow"                     # flow | stereo | depth | generic (sets the default metric and unit)

[adapter]
id = "raft"                       # raft | synthetic, or: module = "package.module:Class"
model_code = "./raft"             # RAFT checkout; its core/ is put on sys.path
iterations = 12                   # refinement iterations per case = trajectory length
device = "cuda"
small = false                     # only for random-weight checks; real checkpoints are read as raft or raft-small from their keys
mixed_precision = false
alternate_corr = false            # RAFT's memory-saving correlation (needs its CUDA extension); false is the default
reference_cases = 5               # rb review run first compares the adapter with the model repository's own evaluation on this many cases (0 = off)

[dataset]
name = "kitti2015-train"          # rb review init takes it from --dataset-name, else the directory's last component; it names the dataset in every receipt
kind = "kitti"                    # image_2/*_10.png + *_11.png; flow_occ/*_10.png optional (no flow_occ = unlabeled cases)
path = "./data/kitti2015/training"
cases = "all"                     # "all", a file with one case id per line, or a number (first N)

[limits]
max_regression = 0.3
max_late_share = 0.25
max_reversals = 2
max_trajectory_regression = 0.3   # candidate late movement above the current model's on the same case, trajectory unit; label-free
# max_last_update = 0.3           # off unless set: a final update larger than this (trajectory unit) = not settled

[evidence]
level = "standard"                # none | standard (the top flagged cases) | full (every case): PNGs under rb-runs/<run>/evidence/
top = 10                          # how many flagged cases get evidence at level standard
```

Case ids come from the data (KITTI: the frame stem, e.g. `000012_10`) and must stay the same across checkpoints. Unlabeled cases (no ground truth) get stability findings and "error not measured"; they never count as regressions or as passing on error. `iterations` is the refinement count both checkpoints run with; keep it fixed across the runs you compare and across the checkpoints a `checks.json` follows (RAFT's own KITTI evaluation uses 24; the examples use 12 because the trajectory statistics are computed per iteration and 12 is what the checkpoints were tuned at). `rb review init` also appends `rb-runs/*/evidence/` to `.gitignore` (evidence PNGs are large; the run's JSON files and report are meant to be committed) and says so.

### The trajectory (recorded for you, or the one line you add)

The `raft` adapter records the trajectory without touching RAFT's code: a forward hook on `model.update_block`, whose output is `(net, up_mask, delta_flow)`; each iteration records the mean |delta_flow| per pixel. For your own model, do one of:

```python
from rabbit_brain import TrajectoryRecorder
rec = TrajectoryRecorder()
with rec.attached(model.update_block, output_index=2):     # (a) no code change: hook the update module
    model(image1, image2, iters=12, test_mode=True)
# or
for k in range(iters):                                       # (b) the one line inside the loop
    delta = update_block(...); flow = flow + delta
    rec.step(delta)
trajectory = rec.values                                      # one number per iteration
```

`rb review verify-hook` proves the wiring: it runs one case and fails with `E_HOOK_NOT_REACHABLE` (never fired) or `E_HOOK_LENGTH` (fired a different number of times than `iterations`). `rb review run` checks every case the same way and records the result in `record.json` under `hook`.

### Adapter agreement: the adapter must reproduce the model's own evaluation

An adapter that loads, preprocesses or scores the model differently from the model repository's own evaluation code produces numbers that look right (shapes, ranges, plausible errors) and are not about the model. So an adapter states a **reference path**, the per-case error computed through the repository's own loader, forward call and metric formula (`reference_value(model, case)`, sharing no code with `infer` / `metric_value`), and `rb` compares the two: `rb review verify-adapter --checkpoint <ckpt>` on a few cases, and `rb review run` on `reference_cases` cases per checkpoint before it evaluates anything. Disagreement beyond 0.001 relative is `E_ADAPTER_DISAGREES` (exit 3) and the run writes nothing; the receipt of every run records the agreement (`record.json` → `adapter_agreement`, and the report's "Adapter agreement" line: agree on N cases with the largest difference, not established when the adapter has no reference path, skipped with `--skip-reference`). The built-in `raft` adapter's reference path is RAFT's `core/datasets.py` KITTI loader, `InputPadder`, `RAFT.forward(test_mode=True)` and the per-image EPE from `evaluate.py`. Agreement on five cases is evidence about those five cases with that checkpoint on that machine; the receipt keeps that scope. Never report findings from a run whose receipt says the adapter disagreed or was skipped without saying so to the human.

### Custom adapters

For a model that is not RAFT, write one file next to `rb.toml` (say `rb_adapter.py`) and point the config at it: `rb review init --adapter rb_adapter:MyAdapter --model-code ./mymodel --dataset ./data/val --kind npz --iterations 8`. The project directory and `model_code` are on `sys.path` when `rb` imports the adapter, so no `PYTHONPATH` is needed; the class takes the `Config` and implements six methods. Everything it needs is importable from `rabbit_brain.adapters`: `Case`, `Prediction`, `Metric`, `RBError`, `read_case_selector`. A complete adapter for a model whose `forward(x, iters)` applies `model.update` once per iteration:

```python
from pathlib import Path
import numpy as np, torch
from rabbit_brain.adapters import Case, Metric, Prediction, RBError, read_case_selector
from mymodel.model import MyModel                      # your code, under [adapter] model_code

class MyAdapter:
    task = "flow"                                       # flow | stereo | depth | generic: sets the default metric and unit
    metric = Metric(id="mean_endpoint_error", name="mean endpoint error", unit="px")
    synthetic = False
    trajectory_scale = 1.0                              # multiply recorded updates by this to be in the metric's unit (RAFT: 8, its updates are at 1/8 resolution)

    def __init__(self, cfg):
        self.cfg = cfg
        self.iterations = cfg.adapter.iterations
        self.root = Path(cfg.dataset.path)
        self.architectures = {}                         # checkpoint path -> a pure architecture label; goes into the receipt, and rb warns when the two differ

    def describe(self):                                 # settings, into the receipt
        return {"id": "mymodel", "iterations": self.iterations, "hook": "forward hook on model.update", "architectures": dict(self.architectures)}

    def load(self, checkpoint: Path, device: str):
        if not checkpoint.exists():
            raise RBError("E_CHECKPOINT_NOT_FOUND", message=f"No such checkpoint: {checkpoint}")
        model = MyModel()
        model.load_state_dict(torch.load(checkpoint, map_location="cpu", weights_only=True))
        self.architectures[str(checkpoint)] = "MyModel(hidden=16)"   # architecture only, never a learned value
        return model.to(device).eval()

    def cases(self):                                    # one Case per sample; ids stable across checkpoints; gt=None when unlabeled
        ids, limit = read_case_selector(self.cfg)       # honours [dataset] cases = "all" | a file of ids | N
        for i, path in enumerate(sorted(self.root.glob("*.npz"))):
            if ids is not None and path.stem not in ids:
                continue
            if limit is not None and i >= limit:
                break
            yield Case(id=path.stem, name=path.stem, inputs=str(path), gt=str(path))

    def infer(self, model, case, rec):                  # rec records one value per refinement iteration
        d = np.load(case.inputs)
        x = torch.from_numpy(np.concatenate([d["frame1"], d["frame2"]], 0)).float()[None] / 255.0
        with torch.no_grad(), rec.attached(model.update):    # the hook records model.update's output; output_index=k if it returns a tuple
            out, _ = model(x.to(next(model.parameters()).device), iters=self.iterations)
        return Prediction(output=out[0].cpu())         # (C, H, W) or (H, W, C); evidence renders 2-channel fields, read_gt must match its shape

    def metric_value(self, pred, case):                 # the error, lower is better; None when case.gt is None
        if case.gt is None:
            return None
        gt = torch.from_numpy(np.load(case.gt)["field"]).float()
        return float(torch.sqrt(((pred.output - gt) ** 2).sum(0)).mean())

    def expected_iterations(self):
        return self.iterations

    # optional: adapter agreement (strongly recommended) and evidence
    def reference_description(self):
        return "mymodel/eval.py: its own sample() loader and epe() formula, iters=8"
    def reference_value(self, model, case):            # the same number through YOUR evaluator's code path, sharing nothing with infer/metric_value
        import importlib.util
        spec = importlib.util.spec_from_file_location("my_eval", Path(self.cfg.adapter.model_code) / "eval.py"); ev = importlib.util.module_from_spec(spec); spec.loader.exec_module(ev)
        x, gt = ev.sample(case.inputs)
        with torch.no_grad():
            out, _ = model(x, iters=8)
        return ev.epe(out[0], gt)
    def read_images(self, case):                        # list of HxWx3 uint8 arrays
        d = np.load(case.inputs); return [np.transpose(d["frame1"], (1, 2, 0)), np.transpose(d["frame2"], (1, 2, 0))]
    def read_gt(self, case):                            # (field, valid) in the prediction's layout; valid HxW bool
        d = np.load(case.gt); return d["field"], np.ones(d["field"].shape[1:], bool)
```

What the recorder stores: for each call of the hooked module (or each `rec.step(delta)`), the mean over pixels of the L2 norm of the update vector, times `trajectory_scale`; the fields themselves are kept for the run's convergence statistics and the filmstrips. Late share uses the last third of the iterations (the last `n - 2n//3`; 3 of 8), late movement the last quarter (`n//4`, at least 1; 2 of 8). The reference path's iteration count is your evaluator's, so a run with a different `[adapter] iterations` will disagree with it by design; either keep them equal or run with `--skip-reference` and say so. `rb review doctor` reports a missing method by name; `rb review verify-hook` proves the hook fires `iterations` times; `rb review verify-adapter` proves the numbers. Errors an adapter raises with `RBError(code, message=...)` reach the human with the table's fix text. `rb review init` for a custom adapter writes only the generic keys; the built-in `src/rabbit_brain/adapters/raft.py` is the full reference and `synthetic.py` the smallest one.

### The results file for `rb review import` (version 1)

```json
{
  "version": 1,
  "project": "My perception project",
  "baseline": "model-v1", "candidate": "model-v2",
  "dataset": "Regression set", "metric": "mean_endpoint_error", "unit": "px",
  "cases": [
    { "id": "seq-001", "name": "First sequence", "baseline_error": 2.1, "candidate_error": 2.4,
      "candidate_trajectory": [2.3, 1.4, 0.9, 0.6, 0.4, 0.3, 0.2, 0.15, 0.1, 0.08, 0.06, 0.05] }
  ]
}
```

1–500 cases; ids match `^[a-zA-Z0-9_.-]{1,80}$`; errors are the same lower-is-better metric per case for both models, computed against the same ground truth and valid mask; trajectories are optional (2–64 values, mean |update| per iteration); `baseline_frames`/`candidate_frames` are optional paired per-frame series. `rb schema example` prints this file; `rb schema comparison-v1` the JSON Schema.

**CSV.** `rb schema csv` prints a sample. Columns `case_id, baseline_error, candidate_error` are required; `name, tags, baseline_trajectory, candidate_trajectory, baseline_frames, candidate_frames, notes` optional; series cells hold numbers separated by `;` (spaces work too). Your evaluator's own column names need no conversion script: map them with `--columns case_id=frame,baseline_error=epe_current,candidate_error=epe_candidate,candidate_trajectory=updates`. The names come from flags, and `--note` puts a sentence into the receipt (where the numbers came from, which file, what was converted). Worked example, an evaluator's CSV with two RAFT checkpoints on 200 KITTI pairs:

```sh
rb review import kitti_eval.csv --project raft-kitti --baseline-name raft-things --candidate-name raft-sintel \
   --dataset "KITTI-2015 training, 200 pairs" --metric endpoint_error --unit px \
   --columns case_id=frame,baseline_error=epe_current,candidate_error=epe_candidate,baseline_trajectory=current_update_norms,candidate_trajectory=candidate_update_norms \
   --note "from our evaluator's kitti_eval.csv, 12 iterations" --json
rb review findings <run_id> --top 5 --max-trajectory-regression 0.3   # the paired label-free test is off for imports until you pass it: do so when both trajectories are in the metric's unit
rb review case <run_id> <case_id>
```

An imported run has numbers only, so `rb review case --render` is not available for it (evidence needs the model and the data, which is `rb review run`). A CSV error names the row and column: "row 14 (000012_10), column updates: ... is not a series of numbers (separate values with ';')".

### What a review writes, and the definitions it applies

A run directory `rb-runs/<run_id>/` holds `bundle.json` (every case's numbers, trajectories, stability statistics, flags and borderline limits), `record.json` (the receipt: rb version, the exact command, both checkpoints with sha256, model-code git SHA, dataset hash, seeds, environment, hook status, adapter agreement, skipped cases), `findings.json` (the ranked queue, the summary, the verdict under those limits) and `report.md` (the human receipt). Those four are small and committable; `checks.json` belongs in the repo next to the model code. The files and the `--json` output of the corresponding commands are the same documents (nulls written explicitly). `record.json` → `dataset.case_list_hash` is a hash of the case ids (the same set of cases); `dataset.content_hash` is a hash of the input and ground-truth files themselves (the same data), written by `rb review run` when the adapter's cases name files. `rb review runs` lists every run with its models and verdict. Schemas: `rb schema bundle|record|findings|checks|envelope`.

Definitions `rb` applies, and restates in every report: a case is a **regression** when the candidate's error exceeds the current model's by more than `max_regression`; **late share** is the fraction of all refinement that happened in the last third of the iterations; a **reversal** is an iteration where the update grew by more than 5% over the previous one, a size and not a direction (the direction measure is the separate `sign_reversal_rate`); a case is **unstable** when late share exceeds `max_late_share` or reversals exceed `max_reversals`, when it is a trajectory regression, or, only when `max_last_update` is set, when the **last update** (the size of the final refinement step, in the trajectory's unit) exceeds it. A **trajectory regression** is paired and label-free: the candidate's **late movement** (mean update over the last quarter of iterations, in the trajectory's unit) exceeds the current model's on the same case by more than `max_trajectory_regression`. It is the same test as the error regression, applied to the refinement instead of the answer, so it also works on cases without ground truth; on labeled cases it mostly coincides with error regressions and ranks the worst of them first (flag `trajectory_regression`). A case is **borderline** when moving one limit by a tenth (one reversal, for the reversal limit) would change what the case is called: its `borderline` field names those limits, the findings table and the report mark it, and the summary counts it. The margin it marks is about what the same checkpoints, data and code produce from one machine to another, so a borderline outcome is the one a re-run elsewhere may not reproduce; it changes no verdict, and a review is not "clean" because its flags are borderline. Cases are ranked regression+unstable, then regression, then **improved but unstable** (they pass on error and still need a look), then the rest, by error change; unlabeled cases rank after labeled ones within a group. A **saved check** is an absolute limit for a case id in a project: candidate error ≤ `max_error`, optionally late share ≤ `max_late_share` and reversals ≤ `max_reversals`; a saved case missing from a later run fails the check. `rb review check save` defaults `max_error` to the better of the two models on that case plus `max_regression` (a case that improved keeps its improvement; a case that regressed must come back to the current model's level) and requires a settled trajectory unless `--no-settled`; a check saved from a run can therefore fail on that same run, which is the point: it fails until the case is fixed. `rb review check run` prints the flagged cases and the check results (`--all` prints every case) and exits 1 when anything fails; its `--json` has `ok: true` with `data.failed: true`, because findings are data. Checks are meant for runs of the same project, case set, `iterations` and limits; a run made with different settings is a different question.

**Evidence.** `rb review run` renders the top flagged cases (`[evidence] level = "standard"`, `top`; `--evidence none|standard|full` overrides) and `rb review case <run> <id> --render` renders any case: it re-runs both checkpoints on that case and writes `rb-runs/<run>/evidence/<case>/` with `inputs.png` (the inputs, and where the two models disagree: |candidate - current| per pixel, which needs no ground truth), `flow.png` (current | candidate | ground truth on one colour scale), `error.png` (per-pixel error of each model over the dimmed input, and the change: red where the candidate is worse, blue where it is better; only when ground truth exists), `filmstrip.png` (one tile per refinement iteration, the update size per pixel, candidate then current, one colour scale, the last quarter framed), `trajectory.png` (both trajectories on a log axis, the last quarter shaded) and `case.png` (all of it stacked, 1920 px wide, with the finding as caption), plus a README that states the scales. Scales show structure, not extremes: flow colour saturates at the 95th percentile with square-root saturation; heat maps run to the 99th percentile capped at four times the mean, so a region above that saturates, and saturation is itself the finding (a near object the model keeps moving, say). Every tile label says its scale. The caption and the JSON `evidence.check` say whether the re-run reproduced the run's error and trajectory for that case. Evidence directories are gitignored (large); `bundle.json` and the report list which cases have them. Rendering needs numpy and pillow (`E_EVIDENCE_DEPS` otherwise); a run never fails because a rendering did. Custom adapters opt in with `read_images(case)` and `read_gt(case)`; without them only the disagreement map, the filmstrips and the trajectory plot are drawn. Show the human `case.png` for the case the verdict names; do not describe images you have not opened.

Every trajectory also yields the absolute convergence statistics (`stability.candidate` in bundle.json and `rb review case`): `last_update`, `late_update` and `early_update` (mean update over the last and first quarter of iterations), `late_to_early`. A run with an instrumented model adds, from the update fields themselves: `sign_reversal_rate` (share of consecutive updates pointing in opposite directions, cosine below zero), `mean_cos`, `displacement_mean` / `displacement_max` / `displacement_initial` (distance of the intermediate estimates from the final one) and `update_energy`. A run's report ends with the median / p90 / max of these per model, so you can see where the limits sit against the case set. Do not derive verdicts from these numbers yourself; they are there to be read, compared across checkpoints and, with consent, shared for calibration.

## Output

Every command accepts `--json` and prints exactly one JSON object on stdout, the envelope; progress, logs and warnings go to stderr. `ok` is false only when the command could not do its job; a check that ran and failed is data (`ok: true`, exit 1). `errors[]` carries `code`, `message` and `fix`, and `handoff` on a person's call. `next` lists the commands that usually follow. Research-state commands put the object they wrote or read in `data.object` (with its `kind` and `id`), a check's result in `data.outcome` (`passed`, `failures`), counts under `*_count` names, and who was recorded in `data.actor`. `rb schema envelope` prints the shape.

A review run's envelope:

```json
{"ok": true, "rb_version": "0.5.0", "command": "review run", "run_id": "20260925-1412-raft-small",
 "data": {"run_dir": "rb-runs/20260925-1412-raft-small",
          "summary": {"cases": 200, "with_gt": 200, "with_trajectories": 200, "regressions": 3, "unstable": 4, "improved_unstable": 2, "settled_regressions": 1, "flagged": 5},
          "verdict": {"status": "investigate", "ready": false, "line": "Not ready: 3 error regressions, 2 unstable cases that pass on error. Start with 000012_10.", "start": "000012_10"},
          "hook": {"status": "recorded", "verified": true, "iterations": 12, "expected": 12}},
 "errors": [],
 "next": ["rb review findings 20260925-1412-raft-small --top 5", "rb review case 20260925-1412-raft-small 000012_10"]}
```

## Exit codes

`0` ok · `1` a research-state gate failed (`rb status --fail-on`), a setting did not verify (`rb spec verify`, or `rb spec set` with a checkable source), `rb doctor` found a problem in `.rb/`, or a saved review check failed or is missing, or a case is flagged (`rb review check run`, default `--fail-on any`; `rb review run` only with `--fail-on`); the JSON has `ok: true` because the result is data · `2` invalid input or config, including a command line that does not parse (`E_USAGE`) · `3` environment failure (`rb review doctor` when something fails; adapter, hook, device or adapter-agreement problems). Read `errors[].fix`.

## Errors and what to do

| code | meaning | do this |
|---|---|---|
| `E_CONFIG_MISSING` | no `rb.toml` here | `rb review init …` in the project root, or `rb review init --demo` |
| `E_CONFIG_INVALID` | `rb.toml` has a bad field, or an unknown adapter id | fix the named field; `rb review init --force` rewrites the file |
| `E_ADAPTER_IMPORT` | the adapter or the model's dependencies did not import | `rb review doctor` names the module: a custom adapter file goes next to rb.toml or under `model_code` (both are on `sys.path`); a missing dependency is installed here (`pip install "rabbit-brain[raft]"` for RAFT) |
| `E_DOCTOR` | `rb review doctor` found a failing check | read `data.checks`: every failed check has a detail and a fix |
| `E_MODEL_CODE_MISSING` | `model_code` is not a RAFT checkout | point it at the repository (must contain `core/raft.py`) |
| `E_CHECKPOINT_NOT_FOUND` | a checkpoint is missing, is not a torch state dict, or does not match RAFT's layers | check the path and that it is a RAFT checkpoint (raft-small is detected from the file); do not download weights without asking the human |
| `E_DATASET_EMPTY` | no cases found | check `[dataset] path` and `kind`, or the cases file |
| `E_ADAPTER_DISAGREES` | the adapter's per-case error differs from the model repository's own evaluation on the same cases | `rb review verify-adapter --checkpoint <path>` shows both columns; fix the adapter's loading, preprocessing (input range, padding, colour order), forward call or metric formula; `rb review run --skip-reference` runs anyway and the receipt says so |
| `E_HOOK_NOT_REACHABLE` | the recorder never fired | custom adapter: call `rec.step(delta)` per iteration or use `rec.attached(...)`; or run with `--no-trajectories` and say so to the human |
| `E_HOOK_LENGTH` | fired a different number of times than `iterations` | the hook must fire once per refinement iteration; align `[adapter] iterations` |
| `E_DEVICE` | CUDA requested but not available | `--device cpu` (slow) or a GPU machine |
| `E_INFERENCE_FAILED` | every case failed inference | `rb review verify-hook --checkpoint <path>` shows the first error |
| `E_IMPORT_INVALID` | the results file is not a valid comparison | fix every problem listed in `errors[0].problems`; start from `rb schema example` |
| `E_IMPORT_NOT_JSON` | the file is not JSON | check for a missing comma or bracket |
| `E_IMPORT_TOO_LARGE` | over 2 MB / 500 cases | split the case set or drop per-frame series |
| `E_IMPORT_CONFIG` | a CSV import is missing names | pass `--project --baseline-name --candidate-name --dataset --metric --unit` |
| `E_CSV_INVALID` | the CSV could not be converted | required columns `case_id, baseline_error, candidate_error`; series are semicolon-separated |
| `E_FILE_NOT_FOUND` | a path does not exist | paths are relative to the current directory |
| `E_RUN_NOT_FOUND` | no such run | `rb review runs`; or pass a `bundle.json` / results file path |
| `E_CASE_NOT_FOUND` | no such case id in this run | `rb review findings <run> --filter all` lists every id |
| `E_CHECKS_INVALID` | the checks file is not valid | `rb schema checks` |
| `E_CHECKS_PROJECT_MISMATCH` | the named checks file is for another project | same project name, or another `--checks` file (an unnamed `checks.json` for another project is ignored with a warning) |
| `E_LIMITS_INVALID` | a limit is out of range | `--max-regression ≥ 0`, `0 ≤ --max-late-share ≤ 1`, `0 ≤ --max-reversals ≤ 64`, `--max-trajectory-regression ≥ 0`, `--max-last-update ≥ 0` |
| `E_NOT_AVAILABLE` | the command is planned, not in this version | `rb paper`, `rb open`, `rb publish`, `rb clone` and the older `rb rerun`, `rb serve`, `rb reproduce` are not here yet; the message says what to use today |
| `E_WRITE_FAILED` | a file could not be written | check permissions, or `--runs-dir` |
| `E_EVIDENCE_DEPS` | evidence rendering needs numpy and pillow | `pip install numpy pillow` (included in `rabbit-brain[raft]`) |
| `E_INTERNAL` | unexpected failure | re-run with `--json` and report it |
| `E_WORKSPACE_NO_TOKEN` | no `RB_WORKSPACE_TOKEN` | the workspace is the paid feature; the tool needs no account. `rb plans` needs no token |
| `E_WORKSPACE_AUTH` | the workspace rejected the token | it may be revoked or for another deployment; create a new one |
| `E_WORKSPACE_PAYMENT_REQUIRED` | no active subscription on that workspace | `rb workspace checkout` returns a link for a person to approve |
| `E_WORKSPACE_NOT_SELLABLE` | that plan is published but unfinished | `rb plans` shows which plans can be bought today |
| `E_WORKSPACE_UNREACHABLE` | the workspace host could not be reached | exit 3. Nothing local was affected; the run is still on disk |
| `E_WORKSPACE_REJECTED` | the workspace refused the request | the message says why; push the `bundle.json` unmodified |
| `E_NO_INVESTIGATION` | no `.rb/` here or in any directory above | `rb init "<what you are trying to establish>"` in the project root |
| `E_INVESTIGATION_EXISTS` | `.rb/` already exists here or above | use it: `rb status` |
| `E_OBJECT_NOT_FOUND` | no object with that id, or no such setting | `rb status --json` lists every object; `rb show <experiment>` its variants, settings, claims and evidence; a setting is `<experiment>/<name>` |
| `E_OBJECT_INVALID` | the values do not make a valid object (a claim with two criteria, `--equals` without `--tolerance`, a taken id, a second baseline) | do what the message says; `rb schema <kind>` |
| `E_FROZEN` | the experiment is frozen and this changes a setting, a variant, what it varies, or a claim's criterion | a person runs the same command with `--amend --why "<reason>"`; the amendment is kept |
| `E_HUMAN_ONLY` | freeze, decide, amend, or retract what something rests on, and rb records you as an agent | give the person `errors[0].handoff.command` to run in their own terminal; do not set or unset `RB_ACTOR` |
| `E_ACTOR_INVALID` | `RB_ACTOR` is not `human:<name>` or `agent:<name>` | set it correctly, or unset it |
| `E_SOURCE_UNRESOLVED` | the source was read and does not state the value (a different value is recorded as a conflict), or could not be read | fix the path, key, line, quote or commit, or set the value the source states |
| `E_SOURCE_UNVERIFIABLE` | a url, a note, or a file source with no key, line or quote | point at a config key, a line, a quote in a saved copy, or a run's JSON; or a person vouches: `rb decide <exp>/<name> accept -m "..."` |
| `E_SOURCE_OUTSIDE` | the source is outside the project, so nobody else can check it | copy it into the repository, commit it, and point `--source` at the copy |
| `E_STATE_EDITED` | an object in `.rb/` was edited, deleted or written by hand, and rb will not write over it | `rb doctor` lists it; `rb doctor --restore` puts back what rb last wrote; if the change is right, a person runs `rb doctor --adopt --why "..."` |
| `E_STATE_CORRUPT` | a file under `.rb/` is not valid, usually a hand edit or a merge left half done | `rb doctor` lists it; `rb doctor --restore` puts back what rb last wrote |
| `E_USAGE` | the command line did not parse | `rb <command> --help`; negative numbers are written as they are (`--at-most -0.001`) |

## Worked examples

### A question handed to an agent

Prompt from the person: *"Find out whether INT8 costs us accuracy on KITTI. The eval script is scripts/eval.py."*

```sh
rb context                                           # if .rb/ exists: read it first, and work from "Agent can do"
rb init "Does INT8 cost RAFT accuracy on KITTI?"      # otherwise, start it
rb experiment add "INT8 vs FP32 on KITTI-2015" --id int8 --baseline fp32 --candidate int8 --varies precision
rb spec set int8 --from configs/eval.yaml --keys "model.*,data.*,eval.iters"
rb spec set int8 seed --per-run
rb metric add epe --unit px --minimize
rb claim add "INT8 costs at most 0.05 px EPE" -e int8 --metric change.epe --at-most 0.05 --min-n 3
rb freeze int8 -m "..."                              # refused: E_HUMAN_ONLY. Give the person errors[0].handoff.command and wait
                                                     # (it prints each criterion it locks, so they see what they are signing)
python scripts/eval.py --precision fp32 --seed 1 ... # then the runs, after the freeze
rb evidence attach int8 --from out/seed1/metrics.json --set seed=1 --config out/seed1/config.yaml --command "python scripts/eval.py ..."
rb show <claim id>
```

Then report to the person, in this order:

1. the claim's standing, exactly as `rb show` prints it (for example `supported, not established: unknown settings and 1 more`);
2. every blocking reason;
3. the settings that are provisional or unknown;
4. the command they need to run, if there is one.

Do not round "supported, not established" up to "INT8 is fine".

### A release review

Prompt from the human: *"Review candidate checkpoint ckpt/raft-small.pth against ckpt/raft-things.pth on the KITTI cases with Rabbit Brain and tell me what to look at."*

```sh
rb review doctor --checkpoint ckpt/raft-things.pth --checkpoint ckpt/raft-small.pth --json    # if there is no rb.toml: rb review init first; doctor prints each checkpoint's architecture and parameter count
rb review verify-hook --checkpoint ckpt/raft-things.pth --json && rb review verify-hook --checkpoint ckpt/raft-small.pth --json   # both checkpoints
rb review verify-adapter --checkpoint ckpt/raft-small.pth --json
rb review run --baseline ckpt/raft-things.pth --candidate ckpt/raft-small.pth --json           # add --limit 20 for a pilot
rb review findings <run_id> --top 5 --json
rb review case <run_id> <top case id> --json
rb review report <run_id>
```

Then report to the human, in this order: the verdict line; the top cases with their `why` text (it says when a case is borderline, and a borderline case is worth naming as such rather than reporting as settled fact); the run id; the path to `report.md`, and to `report.html` if you wrote one (`rb review report <run_id> --html`, which a human can open in a browser; do not use `--open`, which tries to launch one); the command that reproduces the queue (`rb review findings <run_id>` with the limits used, as the report's Reproduce section prints it). Quote numbers only from `findings.json`. If `hook.status` is not `recorded`, say that stability was not assessed and why. If `adapter_agreement` is not `agree` for both checkpoints, say so first. If the two checkpoints are different architectures (the receipt's checkpoint lines and `rb review doctor` say so), say that the review compares two models rather than a retrain of one.

When a later checkpoint arrives: `rb review check run` against a new `rb review run` of it (same project, same case set) answers "are the cases we cared about still fine?" and exits 1 in CI if not; then `rb review findings` for the full review.

## Sharing a review's statistics (a file a person reads, and sends or not)

`rb review share <run_id>` writes `rb-runs/<run_id>/share.json` and prints what is in it and what is not. In: the task and metric, the adapter id and architecture labels, iteration count, environment versions, the limits, per case the errors, the trajectories, the stability and convergence statistics, flags and outcomes (cases numbered, not named), and the run's summary. Out: case ids and names, tags, notes, file paths, dataset name and hashes, checkpoint paths, names and hashes, the project name, the command line, evidence, the verdict text. `rb schema share` prints the schema. The file is for the calibration corpus that sets the per-family limits `rb` ships with; the human reads it and decides whether to send it (as an attachment to a GitHub issue on `rabbit-brain/rb`, or by email). An agent may write the file and show the human where it is; an agent never sends it.

## The workspace: what payment is for, and how to buy it without a sales call

Everything above is free and local, and stays that way: comparing, investigating, saving checks,
failing CI. What a subscription buys is that a finished review stops living only in the terminal
that produced it.

    rb plans                          what a workspace costs. No token, no account
    rb workspace status               is this workspace active, can it accept comparisons
    rb workspace checkout             a link for a person to open and approve
    rb workspace push <run>           push one finished comparison into the workspace

The token comes from `RB_WORKSPACE_TOKEN` and from nowhere else. `rb` never writes it to disk, never
puts it in a URL, and never prints it, so a CI secret is enough and there is no credential file to
leak.

**A person completes every purchase.** `rb workspace checkout` returns a URL and stops. It cannot
pay, and that is deliberate. The sequence an agent should follow when a push is refused:

    rb workspace push <run>    ->  E_WORKSPACE_PAYMENT_REQUIRED, with the plan and its price
    rb workspace checkout      ->  a URL. Give it to the human and say what it costs
    rb workspace status        ->  poll until `active` is true, then push again

Exit codes follow the table above: 2 for anything the caller can fix, 3 when the host is unreachable.
A refused push changes nothing locally; the run is still on disk and still complete.

## Boundaries

`rb` does not run experiments: it records what the caller says produced the numbers, who attached them, the repository's state at that moment, and what the numbers support. A verified setting is what a file states, not proof a run used it; a receipt is what `rb` saw when the evidence was attached, not proof of what produced it. `rb` does not train, does not modify model code (the recorder is a forward hook or one line you add, and `rb review verify-hook` checks it), and does not certify a model. Stability limits are generic heuristics and a starting point; a scorer fitted to the model is a separate, paid step. `rb` uploads nothing unless you run `rb workspace push`, which posts one comparison you name to a workspace you configured; `rb review share` writes a file for a person to send. Your adapter and model code run with your permissions and may do what they like. The synthetic adapter is a test double and every output of it says so.

## For humans: verify what your agent did

Open `rb-runs/<run_id>/report.md`. The header lists the run id, the `rb` version, the exact command, both checkpoints with their sha256, the environment, the hook status and whether the adapter agreed with the model's own evaluation; the body restates the definitions with the limits in force and lists every case. `record.json` adds the model-code git SHA, the dataset hash, seeds and skipped cases. Re-run the command from the header, or `rb review findings <run_id>` with the same limits, and compare. If what the agent told you differs from the report, the report is right.

For research state: `rb status` and `rb show <id>` are computed from `.rb/`, not from what anyone wrote about it. `rb log` lists every write with its actor and how `rb` knew it (inside an agent session your name in `RB_ACTOR` is ignored, and the entry says so), and `git log -p .rb/` shows every change. A setting is verified only with the text `rb` read, which `rb show <experiment>/<name>` prints. `rb doctor` lists anything changed outside `rb`, and `rb doctor --restore` puts back what `rb` wrote. It cannot catch someone who edits a file and also rewrites `log.jsonl` to match; git can, so review `git log -p .rb/` for changes to `log.jsonl` other than new lines. If what the agent told you differs from `rb status`, `rb status` is right.

Re-running on a different machine is a weaker check than it looks, and the report says so where it matters. Same code, same checkpoints, same data, a different GPU or torch build: most cases land on the same numbers to several decimals, a few land far enough apart to cross a limit. On the RAFT examples in this repository, run on two machines, 53 of 200 cases differed by more than 0.01 px and six cases changed what they were called; all six are marked borderline by the first machine's own numbers, without knowing the second machine's. So: a difference on a case the report marks borderline is the machine, not a discrepancy; a difference on any other case, or a different verdict, is worth chasing.
