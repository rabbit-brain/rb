# Rabbit Brain

Release review for iterative perception models: optical flow, stereo, depth, anything that refines an answer over iterations.

You have a current checkpoint and a candidate. Give Rabbit Brain the per-case errors of both, plus one number per refinement iteration if your model refines, and it ranks what to look at: the cases that got worse, and the cases whose error improved while the model never stopped changing its answer. Each flagged case comes with the numbers, the reasoning in plain language, and the evidence rendered as an image. The cases you cared about become saved checks, so the next checkpoint gets the same review and CI fails when one of them breaks.

```sh
pip install rabbit-brain
rb onboard                    # describe your setup once; get a configured project and an integration ladder back
```

If your model is RAFT-family, or you already know what goes in the config:

```sh
pip install "rabbit-brain[raft]"
rb init --project my-flow --adapter raft --model-code ./raft --dataset ./data/kitti2015/training
rb doctor && rb verify-hook --checkpoint ckpt/raft-things.pth
rb run --baseline ckpt/raft-things.pth --candidate ckpt/raft-small.pth    # both checkpoints, trajectories recorded for you
rb findings <run> --top 5     # the ranked queue and a verdict
rb check save <run> <case>    # keep a case for the next checkpoint
rb check run <run>            # exit 1 in CI when something regressed
rb report <run> --open        # the same review as a local HTML page, opened from disk

rb import results.json        # or bring per-case results your evaluator already produced (rb schema example)
rb init --demo && rb run --baseline ckpt/synth-current.json --candidate ckpt/synth-candidate.json   # a synthetic dry run on any machine
```

Nothing is sent anywhere. Every run leaves a receipt (`report.md`, `record.json`) with the exact command, the input's hash and the definitions in force, so a colleague, or you after your coding agent ran it, can reproduce the queue and see what every number was computed from. (`rb share` writes a file of anonymised statistics you may choose to send; the tool never sends it.)

**The label-free half.** How much a model is still moving its answer at the last iteration is measurable without a label, and it correlates with that case's error: Spearman 0.88 to 0.92 on 200 cases for each of the four public RAFT checkpoints. That is a correlation, not a verdict. A case that is still moving is worth looking at; it is not thereby wrong, and a settled case is not thereby right. So the same regression test runs twice: once on the error, and once on how much each model was still moving at the end, candidate against current, on the same case. The second one works on cases you have no ground truth for, and it is the reason a case that improved on error can still end up in the queue.

**Why you should believe the numbers.** Before a run reports anything, the adapter has to reproduce the model repository's own evaluation: `rb verify-adapter` runs your model through its own loader, forward call and metric formula and compares, and a disagreement beyond 0.001 relative stops the run and writes nothing. A harness that loads or scores a model slightly differently produces numbers that look plausible and are not about your model; this is the check for that, and every receipt records the result. Findings that turn on a margin thinner than the difference between two machines are marked borderline, so a colleague re-running your review on their own box knows which differences are real.

**What it needs.** Either a checkpoint pair and a case set for a supported adapter (RAFT-family optical flow today, RAFT-Stereo-style depth through a small custom adapter, and any other iterative model the same way; `rb onboard` writes that adapter's scaffold from a description of your setup, with the parts only you can write marked one by one), or one lower-is-better error per case for both models from your own evaluator (any metric with a unit: endpoint error in px, depth error in cm), computed against the same ground truth and valid mask. Trajectories are recorded by the adapter with a forward hook, or by the one-line `TrajectoryRecorder` in your loop. Without trajectories you get the error half and the tool says stability was not assessed.

**What it doesn't do.** Train anything, or certify a model. The stability limits are generic heuristics and a starting point; a scorer fitted to your model is a separate, paid evaluation. It does not replace your evaluator either: the error is whatever your metric says it is.

**Reading a review without the terminal.** `rb report <run> --open` writes `report.html` beside `report.md` and opens it. It is one file with the viewer already inside it, opened from your filesystem: the same case list, evidence and trajectory plots the website shows, computed at the limits the run was checked at. Nothing loads over the network and nothing is uploaded, which is why it works on an air-gapped machine. Evidence images are referenced beside the file; `--embed` puts them inside it instead, for a single attachment on a pull request. The arithmetic behind that page and the arithmetic behind `report.md` are two implementations of the same rules, so a test runs both over the same fixtures and fails the build if they ever disagree.

**What a flagged case looks like.** For each case it flags, `rb` renders the evidence: the inputs, where the two models disagree (no ground truth needed), both flow fields, the per-pixel error of each and where it changed, the per-iteration filmstrips and the trajectory. The decision is a look, not a number.

Below is the one regression in a real review of raft-sintel against raft-things on 200 KITTI-2015 pairs ([examples/raft-kitti](examples/raft-kitti)): +0.41 px on this case, and the sheet shows it is one object, the pole nearest the camera, which both checkpoints keep moving through all twelve iterations. Those two checkpoints were trained on different data, so the mean improvement is a domain gap rather than a release decision; the point of the example is the one case that got worse anyway, and that the receipt names it.

![Evidence sheet for case 000145_10: inputs, disagreement, flow fields, error maps, filmstrips, trajectory plot](examples/raft-kitti/rb-runs/20260918-1852-raft-sintel/evidence/000145_10/case-1400.jpg)

**In CI.** [examples/ci/github-actions.yml](examples/ci/github-actions.yml) is a working release gate: it imports your evaluator's results, runs your saved checks, writes the verdict into the pull request and keeps the run as an artifact. It exits non-zero when a saved check fails, and also when your policy declares a limit this version cannot evaluate, so a required measurement that was never taken does not quietly pass. One thing that file is careful about and worth repeating here: a red job is not a blocked merge. Someone with repository admin has to add the job to branch protection as a required status check before the gate enforces anything.

**For coding agents.** `rb docs` prints [AGENTS.md](AGENTS.md): the workflow, the file format, the JSON output (`--json` on every command), exit codes and every error code with its fix. Tell your agent: *"Review candidate checkpoint B against A on this case set with Rabbit Brain."*

**Research state, for work done with coding agents.** `rb investigation init` starts `.rb/` beside the code: the questions, hypotheses and experiments of a line of work, every setting with where its value came from, claims with their criteria, the evidence attached to them, and who decided what. You and your agent propose; `rb` sets the statuses: a setting is verified only when `rb` reads its source and finds the value there, a claim's verdict is computed from the evidence every time it is read, and a claim with a required setting still unknown is never shown as established. `rb context` is the handoff a fresh agent session reads instead of the last one's summary. See "Research state" in [AGENTS.md](AGENTS.md).

**Install and versions.** `pip install rabbit-brain` (core, pydantic only), `pip install "rabbit-brain[raft]"` (torch and the RAFT adapter's needs), `pip install "rabbit-brain[evidence]"` (numpy and pillow, for evidence sheets with a custom adapter). Python 3.10 or later. [CHANGELOG.md](CHANGELOG.md) for what each version changed.

The trajectory diagnostic comes from a paper that is not public yet; the reference goes here when it is.

Apache-2.0.
