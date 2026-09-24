# Rabbit Brain

The research state for ML work done with coding agents. It records claims with criteria fixed in advance, settings with where each value came from, and evidence with a receipt. Your agents propose; `rb` checks the sources and computes the verdicts; you decide.

When an agent runs your experiments, the state of the work lives in its context window and in the summary it writes at the end. The summary drops the caveats that mattered:

- the seed nobody recorded;
- the learning rate copied from a paper and never checked;
- the threshold picked after the number came in;
- the two variants that also differed in batch size.

Rabbit Brain keeps that state as plain files in `.rb/` next to the code, commits with it, and computes from it what is established and what is not, every time you ask.

```sh
pip install "rabbit-brain[yaml]"
rb init "Does INT8 keep RAFT's accuracy on KITTI?"
rb experiment add "INT8 vs FP32" --id int8 --baseline fp32 --candidate int8 --varies precision
rb spec set int8 --from configs/train.yaml --keys "optim.*,data.*"   # every setting read by key and checked against the file
rb spec set int8 seed --per-run                                       # each run gives its own
rb claim add "INT8 costs at most 0.05 px EPE" -e int8 --metric change.epe --at-most 0.05 --min-n 3
rb freeze int8 -m "before the held-out runs"                          # yours: an agent is refused and handed this command
rb evidence attach int8 --from out/metrics.json --set seed=1 --config out/.hydra/config.yaml
rb status                                                              # established, not established and why, what needs you
rb context                                                             # what a fresh agent session reads instead of a summary
```

**What "established" means.** A claim's verdict is computed from its evidence each time it is read, and never stored. A claim is established only when all of these hold:

- the evidence meets the criterion;
- a person fixed the criterion (wrote the claim, or froze the experiment after it existed);
- every required setting is verified against its source, or vouched for by a person;
- no two variants differ in anything the experiment did not declare;
- no source changed since it was checked;
- the margin is not within the run-to-run noise;
- there are enough runs;
- nothing in `.rb/` was changed outside `rb` (`rb doctor --restore` puts back what `rb` wrote).

Otherwise `rb` says `supported, not established` and lists why. `rb status --fail-on unestablished` makes that a CI gate.

**What "verified" means.** `rb` read the file and found the value stated there, at a recorded commit: the key in a YAML, JSON or TOML config, a line, or a quote in a saved page. It is strict on purpose:

- a comment never verifies;
- `resnet50` does not state 50;
- `adamw` does not state `adam`;
- a file that states a different value is recorded as a conflict and blocks.

It says what the config states, not what the run used. For that, attach the run's own resolved config with `--config`, and a run that disagrees with the spec does not count.

**What an agent cannot do.** Four calls are yours:

- freezing an experiment before the runs that count;
- deciding on a claim or vouching for a setting;
- amending a frozen spec;
- retracting what something rests on.

`rb` detects Claude Code, Codex and other agent runtimes, and answers these calls from an agent with the exact command for you to run. Inside an agent session, a person's name in `RB_ACTOR` is ignored. Every write records who made it and how `rb` knew. Evidence attached before its claim, or before the freeze, is shown and never counted.

**Release review, built in.** For iterative perception models (optical flow, stereo, depth, anything that refines an answer over iterations), `rb review` compares a candidate checkpoint against the current one case by case. It ranks the cases that got worse and the cases whose error improved while the model never stopped changing its answer. It renders the evidence for each, and keeps the cases you cared about as checks for the next checkpoint. A review is evidence like any other: `rb evidence attach <exp> --run <run>`.

```sh
pip install "rabbit-brain[raft]"
rb review init --project my-flow --adapter raft --model-code ./raft --dataset ./data/kitti2015/training
rb review run --baseline ckpt/raft-things.pth --candidate ckpt/raft-small.pth
rb review findings <run> --top 5      # the ranked queue and a verdict
rb review check save <run> <case>     # keep a case for the next checkpoint
rb review report <run> --open         # the review as a local HTML page
rb review import results.json         # or per-case results your evaluator already produced
```

Before a review reports anything, the adapter has to reproduce the model repository's own evaluation on a few cases. A disagreement beyond 0.001 relative stops the run. The review has a label-free half as well. How much a model is still moving its answer at the last iteration correlates with that case's error: Spearman 0.88 to 0.92 on 200 cases for each of the four public RAFT checkpoints. So the regression test also runs on that movement, candidate against current, and it works on cases with no ground truth. That is a correlation, not a verdict: a case still moving is worth a look, not thereby wrong.

Below is the one regression in a real review of raft-sintel against raft-things on 200 KITTI-2015 pairs ([examples/raft-kitti](examples/raft-kitti)): +0.41 px on this case. The sheet shows it is one object, the pole nearest the camera, which both checkpoints keep moving through all twelve iterations.

![Evidence sheet for case 000145_10: inputs, disagreement, flow fields, error maps, filmstrips, trajectory plot](examples/raft-kitti/rb-runs/20260918-1852-raft-sintel/evidence/000145_10/case-1400.jpg)

[examples/ci/github-actions.yml](examples/ci/github-actions.yml) is a working release gate. A red job is not a blocked merge, though: someone with repository admin has to make the job a required status check.

**For coding agents.** `rb docs` prints [AGENTS.md](AGENTS.md): the rules, every command, the JSON output (`--json` on every command), exit codes, and every error code with its fix. Tell your agent: *"Use Rabbit Brain to track this: read `rb context` first."*

**What it doesn't do.** Run your experiments, train anything, or certify a model. It records what you and your agents say produced the numbers, and what those numbers support. A receipt is what `rb` saw when the evidence was attached. It is not proof of what produced the evidence. `rb` works on your machine and uploads nothing unless you run `rb workspace push`.

**Install and versions.** `pip install rabbit-brain` (core, pydantic only). The `[yaml]` extra reads YAML configs. The `[raft]` extra adds the RAFT review adapter. The `[evidence]` extra adds evidence sheets for a custom adapter. Python 3.10 or later. [CHANGELOG.md](CHANGELOG.md) for what each version changed. The same state is there from Python (`import rabbit_brain as rb; rb.open()`, with a `with exp.run(...)` block that attaches what your eval logs) and over MCP (`rb mcp`; every call recorded as the agent's). Planned: `rb paper` (a paper's claims and settings as a pre-filled experiment), a local UI, publishing and cloning an investigation with its lineage.

The trajectory diagnostic comes from a paper that is not public yet; the reference goes here when it is.

Apache-2.0.
