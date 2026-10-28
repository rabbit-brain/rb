# Rabbit Brain

`git status` says what state your code is in. `rb status` says what state your research is in.

Rabbit Brain (`rb`) is a command-line tool that records the ML experiments you run with coding agents. The record is plain YAML in `.rb/`, committed next to your code: the questions the work is trying to answer, each experiment's variants and settings with where each value came from, the criteria a result must meet with who declared each and whether before the evidence, the runs with a receipt, and the decisions made on them.

Agents and humans both write to it. `rb` checks each setting against the file it cites, computes every finding from the runs, and records who made each write.

When an agent runs your experiments, the state of the work lives in its context window and in the summary it writes at the end. The summary drops the caveats that mattered:

- the seed nobody recorded;
- the learning rate copied from a paper and never checked;
- the threshold picked after the number came in;
- the two variants that also differed in batch size.

`rb` keeps each of them on the record and shows it every time the record is read: a per-run setting every run must give, a setting checked against the file that states it, a criterion that says whether it was declared before its evidence, and a confound on every finding that compares the two variants.

## Install

```sh
uv tool install rabbit-brain   # rb in an environment of its own, which keeps itself up to date
pip install rabbit-brain       # or in a project's environment, to import the SDK there
rb tour        # builds a small real project in ./rb-tour and runs the whole loop, every command shown with its real output
```

Either gives the `rb` binary for Linux, macOS and Windows, with the Python SDK and the terminal screen. Python 3.10 or later. Installed as a tool (`uv tool install rabbit-brain`, or `pipx install rabbit-brain`), rb checks for a newer release at most once a day and updates itself in the background; installed with pip, it never touches the project's environment and says one line when a newer rb is out. `rb config auto-update off` turns both off. To try it without installing anything, `uvx --from rabbit-brain rb tour`. After `uvx`, `rb` is not on your path: run the tour's next commands as `uvx --from rabbit-brain rb ui`, or keep `rb` with `uv tool install rabbit-brain`. Each release's wheels are also attached to its [GitHub release](https://github.com/rabbit-brain/rb/releases), with their checksums. The record's format is YAML with the surprises taken out, specified as one JSON Schema: [schema/rb-v1.schema.json](https://github.com/rabbit-brain/rb/blob/main/schema/rb-v1.schema.json). [CHANGELOG.md](https://github.com/rabbit-brain/rb/blob/main/CHANGELOG.md) says what each version changed.

## Quick start

In your own repository, `rb init "<the work's theme>"` creates `.rb/` and installs `rb`'s instructions for the coding agents it detects in the repository (Claude Code, Codex, Cursor). The title names the work's theme, such as a paper's title, a model or a line of work; each thing the work is trying to find out is a question of its own. Then paste this line into your agent's chat:

```
Use Rabbit Brain to track this: read rb context, then record where the work stands.
```

The agent records as it works, and `rb status` shows you where the work stands and what waits on you. In a project already under way, the agent records where the work stands in that same turn, from the files, and asks you nothing up front; `rb context` points it at `rb docs --start`, which says how.

To see first what `rb init` would write, run `rb init --dry-run`: it lists every file and writes nothing. To leave, `rb remove --dry-run` shows what `rb remove` would take out and what it would keep, and why; `rb remove` then takes out only what is `rb`'s. Your research files and git history stay as they were; the few git objects `rb` made stay until git's own gc removes them.

Written out by hand, a session in a repository with its configs in `configs/` and each seed's metrics and resolved config in `out/seed<N>/` looks like this:

```sh
rb init "INT8 ResNet-50"                                               # titled for the work's theme
rb question add "Is INT8 ResNet-50 as good as FP32 on ImageNet?" --id q-imagenet
rb experiment add "INT8 vs FP32" --id int8 -q q-imagenet --hypothesis "INT8 keeps validation loss within 0.05 of FP32" --baseline fp32 --candidate int8 --varies precision
rb spec set int8 --from configs/train.yaml --keys "optim.*,data.*"     # each setting read by key and checked against the file
rb spec set int8 fp32.precision fp32 --source configs/fp32.yaml#precision
rb spec set int8 int8.precision int8 --source configs/int8.yaml#precision
rb spec set int8 seed --per-run                                         # each run gives its own
rb metric add val_loss --unit nats --minimize
rb criterion add "INT8 adds at most 0.05 validation loss" -e int8 -q q-imagenet --metric change.val_loss --at-most 0.05 --min-n 3 --when-met "INT8 stays within the declared accuracy budget on ImageNet."
rb freeze int8 -m "before the held-out runs"                            # locks the rule, recorded with who froze it and why
rb run add int8 --from out/seed1/metrics.json --set seed=1 --config out/seed1/config.yaml
rb status                                                                # where the work stands, and what waits on you
rb context                                                               # what a fresh agent session reads instead of a summary
```

A run's numbers are named for the variant they are for. Each `out/seed<N>/metrics.json` here gives both arms, nested or dotted: `{"fp32": {"val_loss": 1.2}, "int8": {"val_loss": 1.23}}` gives `fp32.val_loss` and `int8.val_loss`, which the criterion on `change.val_loss` reads. A flat `{"val_loss": 1.23}` names no variant, so no criterion reads it: `rb run add` says so in one line, and `--variant int8` reads a file of one variant's numbers.

## Reading a finding

`rb` computes a criterion's finding from its runs and analyses every time it is read. No finding is stored, and none is one word. After seeds 2 and 3 are recorded the same way (`rb run add int8 --from out/seed2/metrics.json --set seed=2 --config out/seed2/config.yaml`), `rb show` of the criterion prints:

```
Criterion cviuxxm on int8 (primary, own): INT8 adds at most 0.05 validation loss
  cviuxxm INT8 adds at most 0.05 validation loss
          criterion met · loss · change.val_loss ≤ 0.05 (n ≥ 3)
          reads:     change.val_loss: +0.02667 [+0.01232, +0.04101] nats · paired difference, t interval, 3 seeds, 95% (rb)
          result:    loss: the interval clear of 0 on the worse side (lower is better)
          rule:      criterion met: estimate +0.02667 ≤ 0.05
          timing:    declared after the evidence existed: 1 of 3 units was produced before it was declared; produced after it: +0.025 nats · paired difference, 2 seeds (rb)
          units:     3 seeds
          integrity: ok
  produced after it was declared: +0.025 nats · paired difference, 2 seeds (rb)
  runso7fgx: difference +0.03 (file) · unit seed=1 · produced before the declaration · seed=1
  runtubqyq: difference +0.02 (file) · unit seed=2 · seed=2
  run2ysivj: difference +0.03 (file) · unit seed=3 · seed=3
```

A loss inside what the rule allows is an ordinary finding. Here the first seed's output existed before the criterion was declared, and the finding says so: evidence is never hidden, and never read as prospective. Integrity lists every problem the finding rests on, such as a setting whose source states another value, a confound between the arms, a frozen experiment that drifted, or a record edited outside `rb`.

**In CI.** `rb status --fail-on unestablished` is the CI gate: it exits 1 when a primary criterion misses, is undetermined or untested, or is not shown to have been declared before what it reads, when a finding has an integrity problem, when a question link or a declared relation cannot be read, or when a record was changed outside `rb`. In CI, check out the whole history (`fetch-depth: 0` with `actions/checkout`): rb tells a clean git merge from an edit made outside `rb` only through git's history.

## How it works

### Beside your tracker

W&B or MLflow keep the curves, checkpoints and dashboards; `rb` keeps what the numbers were meant to test, and whether they do. It sits beside them: a run links to its W&B page (`--link wandb=<url>`), `--config` checks a run's Hydra, W&B or Lightning config against the spec, and a dataset or checkpoint is recorded with where its bytes are, in W&B, MLflow, Hugging Face or S3 (`--at wandb:<ref>` on `rb resource add`).

### What "verified" means

`rb` read the file and found the value stated there: a key in a YAML, JSON or TOML config, a line, or a quote in a saved page. It keeps what it read: the line, the file's hash, and the commit when the file is committed. It is strict on purpose:

- the source must name the setting, not just hold the value;
- a comment never verifies;
- `resnet50` does not state 50, and `adamw` does not state `adam`;
- a file that states a different value is recorded as a conflict, an integrity problem until the value or the source changes.

Every read checks the sources again, so a config edited since is caught. A verified setting is what the config states, not what the run used: for that, record the run with its own resolved config (`--config`), and a run whose config disagrees with the spec is shown and not counted.

### Who did what

`rb` never refuses a write because of who makes it: what keeps the record honest is attribution, not permission. Every write records who made it (you or the agent `rb` detects: Claude Code, Codex and other agent runtimes), the chat session when the runtime exports one, the reason, and the words an agent gives as yours with `--words`, verbatim, which `rb` says as words given until you confirm the call they came with. Calls on results and on lines of work (accept, reject, park, ship) are yours: a call an agent records is proposed, not made, until you confirm it with `rb confirm <id>` in your own terminal. `rb status` lists each one waiting under **Proposed, not made**, with that command, and its Next line names `rb confirm` first. An experiment's design an agent wrote any part of (its hypothesis, settings, criteria and gate) reads `design not confirmed` until you confirm it, and a change an agent makes after that says what changed. A record changed outside `rb` is caught on the next read; `rb doctor` lists it and `rb doctor --restore` puts back what `rb` wrote.

### What the repository already says

Agents already leave research in a repository: a results folder with a report, a manifest of hashes, audit files, a reproduction guide. `rb reconcile` compiles that against the record, experiment by experiment: it quotes the reports' results and next steps word for word with file and line, checks manifests against the files as they are now, reads the audits and the repository's own checks, and prints the `rb run add` line for each result it can place: a number that exactly one experiment's criteria read. A results folder an experiment's recorded runs named is that experiment's: a new number in it is another run of it. It lists everything else as unplaced and never guesses: a folder of results with no report of its own is never made an experiment. `rb reconcile --apply` keeps it. Wrap an evaluation as `rb journal run -- python eval.py` and its command, exit code and output files are kept even when nothing else is written down. For reports and results that predate the record, `rb compile <paths>` lists what the record can place and what it cannot, and writes nothing.

### Reading it

`rb status`, `rb show <id>`, `rb compare <experiment>` and `rb context` in the terminal, and the same with `--json` for agents. For you: `rb ui`, a screen in the terminal; `rb open`, a live, read-only catalogue in the browser, served on your machine only; `rb page`, one self-contained HTML file to send anywhere. `rb open` and `rb ui` say which worktree they show, and show another, or a past version of the record, read only. The browser starts with the inquiries and what needs attention. Tabs separate the overview, experiments, findings, runs, history and resources; comparisons and provenance open when you ask for more detail. None of them says who made a write, you or an agent: that stays in the record, in `--json` and in the SDK. A page also leaves out the words you gave an agent and the platform your runs were recorded on, since anyone may be sent it.

### Sharing and reproducing

`rb export` writes the project's readable page, with a manifest hashing it. `rb export --with-record` writes a portable bundle: the record, the files its checks read, the page, and a manifest with the hash of every file. The record keeps who made each write, agent sessions and the user's words, so it goes out only when asked for. `rb clone <bundle or url>` starts a new line of work from a bundle. The questions, experiments, settings and criteria arrive inherited, and every criterion reads untested until runs made in the new repository test it, then `reproduced` or `not reproduced`. An export is plain files, and where it is hosted is up to you: `rb` itself never publishes.

### For coding agents

`rb init` installs `rb`'s instructions for the agents it detects in the repository: a skill and `rb`'s MCP server for Claude Code, a marked section in AGENTS.md for Codex, a rule for Cursor. It asks once before it changes an `AGENTS.md` or `.mcp.json` that was there, and No is the default (`--yes` answers up front); off a terminal it leaves them as they are and says what to add. `rb skill add` installs the skill for one agent later: Claude Code by default (in your home directory, or `--project` for this repository), `--cursor`, or `--agents-md` for Codex. `rb docs` prints [AGENTS.md](https://github.com/rabbit-brain/rb/blob/main/AGENTS.md) in full: the rules, every command, the JSON envelope every command gives with `--json`, the exit codes, and every error code with its fix.

### From Python

The same record and rules, for the code that does the work:

```python
import rabbit_brain as rb

exp = rb.open().experiment("int8")
with exp.run(seed=2, config="out/seed2/config.yaml") as run:
    ...                                                  # evaluate
    run.log({"fp32.val_loss": fp32_loss, "int8.val_loss": int8_loss})
```

The block records one run when it ends without an error. `rb mcp` serves the same operations over the Model Context Protocol, every call recorded as the agent's.

## What it doesn't do

`rb` does not decide or launch your experiments (`rb journal run` only wraps a command you give it), train anything, or certify a model. It cannot make an agent record a choice that only lived in the chat, and it cannot judge whether a method fits the question. It sees only what is in the repository and what runs through `rb journal run`: on its own it looks in `out/`, `outputs/`, `results/`, `eval-out/`, `metrics/` and any folder a recorded run named, at the files in the repository's top folder, and at .json, .yaml and .toml files git shows as new or changed, and it reads what code, configs, docs and database rows say only where the record points to them. What it does: nothing a session wrote reads as yours until you confirm it, every setting says where its value came from, and every finding is computed from the files, not from the agent's account. A receipt is what `rb` saw when the run was recorded, not proof of what produced it. Everything `rb status`, `rb show` and `rb export` need is in the repository: no account and no network. The record leaves your machine only if someone runs `rb sync`, and the hosted service it syncs to is not available yet. It is not version control: `.rb/` is committed with your code and git versions it, and the branch a run was recorded on is context on its receipt, never part of the record.

## Usage analytics

`rb` collects nothing until someone runs `rb telemetry enable`. Enabled, it sends usage events with a random installation identifier and fixed categories (the operation, whether it succeeded, the platform), never research content, paths, command arguments or names. `rb telemetry preview` shows what would be sent, `rb telemetry disable` stops it and deletes the local state, and `DO_NOT_TRACK=1` or `RB_TELEMETRY=0` keeps it off. [PRIVACY.md](https://github.com/rabbit-brain/rb/blob/main/PRIVACY.md) lists every field.

## Roadmap

Planned: `rb paper`, a paper's criteria and settings as a pre-filled experiment.

## Building from source

The `rb` binary is Rust, in `rust/`; maturin builds it into the wheel beside the Python SDK and the terminal screen in `src/rabbit_brain`. With a stable Rust toolchain, `pip install -e ".[dev]"` builds both, and `pytest` runs the suite against that `rb`. [rust/ARCHITECTURE.md](https://github.com/rabbit-brain/rb/blob/main/rust/ARCHITECTURE.md) is the guide to the code.

## License

Apache-2.0. See [LICENSE](https://github.com/rabbit-brain/rb/blob/main/LICENSE).
