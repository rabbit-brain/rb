# The deployment-build experiment: pre-registered protocol

_Question: when a team ships the same source checkpoint through a faster deployment build, do trajectory features pick the cases worth inspecting better than a plain comparison of the two builds' outputs? The cheap baseline is included on purpose and given every advantage: if a per-case output difference already ranks the consequential cases, the refinement geometry adds nothing and the claim is dropped. This file is committed before the first measured run and is not edited after it; anything learned during the feasibility gate is appended as an amendment._

## 1. Pre-registration (fixed before the first measured run)

- The hypothesis (2), the gate (3), the arms (4), the splits (5), the methods (6), the measures (7) and the predictions (8) are fixed before any number is recorded.
- Diagnostics are **frozen** after the configuration split and before the held-out split is touched. Limits, thresholds and any feature selection are written into `rb.toml` and committed at that moment. Frozen at the same time, and named here so none of them can be chosen after seeing an outcome: the B1 selection threshold; the RB score and its direction; the definitions of regression and improvement; the treatment of effectively unchanged cases; the primary metric; and which run of each configuration is primary.
- **The commit SHA of this file and of the exact `rb.toml` configurations is attached to the results.** A git timestamp alone does not establish independent pre-registration, because committer dates are settable by the author. The commit is pushed to the public repository before the first measured run so that an external server-side record exists, and if a result is ever published as a claim rather than as engineering evidence, an externally timestamped snapshot (OSF or equivalent) is registered instead.
- Every run records its `record.json`: checkpoint sha256, model-code commit, dataset hashes, seeds, environment, hook status, adapter agreement, and the exact command.
- Ground truth is never visible to any ranking method. It is used only to compute the outcome in section 7, after the ranking is fixed.

## 2. The hypothesis, stated so it can fail

A per-case output difference between two builds tells you the answer **moved**. It cannot tell you the **direction**: a case that moved and improved is wasted inspection budget. The claim under test is that trajectory stability separates those two.

> **H1.** Among cases where the two builds' outputs already differ, cases where the faster build is still moving at its last iteration are more likely to be worse against ground truth than cases where it settled.

That is narrower than "trajectory finds consequential cases" and it is the only version that isolates what Rabbit Brain adds over an output diff.

**Separating regressions from improvements is not by itself the claim.** Inside the flagged set, the size of the output difference is itself informative: a case that moved further is more likely to have moved somewhere worse. So the restricted analysis compares RB against **B1's continuous score ranked within the same set at the same budget**, not against chance. Otherwise RB could separate the two groups while adding nothing beyond the magnitude B1 already reports, and the result would be credited to refinement geometry that did no work.

A trajectory difference establishes a **behavioural** difference. It does not by itself establish a worse prediction. Nothing in this protocol or any write-up of it may say otherwise.

## 3. Feasibility gate (must pass before anything is measured)

If any of these fails, the hypothesis is untestable in its intended setting and that is the result.

1. **The hook fires once per iteration in both builds.** `rb verify-hook --checkpoint X` already asserts this. Run it under each configuration.
2. **Instrumentation does not perturb the thing being measured.** Run the FP32 configuration with and without the recorder attached; per-case EPE must be identical. This is the adapter-agreement discipline applied to the recorder itself.
3. **Trajectory magnitudes are computed in FP32.** Verified in the shipped code: `TrajectoryRecorder.step` calls `delta.detach().float()` before taking the norm, so a half-precision update is widened before measurement rather than after. Confirm no underflow: RAFT's late updates are around 0.0125 in the 1/8-px internal scale, comfortably above FP16's smallest normal value (about 6e-5). Record the minimum observed update magnitude per run.
4. **A repeatability estimate is made first, per configuration.** Each configuration is run twice, unchanged, on the same machine, and the distribution of per-case EPE differences between the two runs is recorded. **This is a repeatability estimate, not a proof of noise.** "Below the repeat variation" means a difference is not distinguishable from observed run-to-run variation under this protocol; it does not establish that the difference is noise, and it says nothing about any other source of variation. The earlier cross-machine result (`claude/cross-machine-reproduction.md`) measures a different source and is not a substitute.

   Both configurations are repeated, not just the reference, because mixed-precision repeatability can differ from reference repeatability and assuming otherwise would understate the mixed-precision arm's own variability. **Predefine what counts as enough materially changed cases:** if fewer than 20 of the 100 held-out cases show a build difference exceeding the 95th percentile of that configuration's repeat variation, the arm is **inconclusive** and is reported as inconclusive rather than as a null result.

## 4. Materials and arms

Model: RAFT, public repository at a fixed commit. Source checkpoint: `raft-things` (one checkpoint, used for every arm; no retraining anywhere in this protocol). Cases: the 200 KITTI-2015 training pairs with `flow_occ` ground truth. Reference build: FP32, 12 iterations. Hardware, inputs, preprocessing and seeds held constant and recorded.

Both arms are a single line in `rb.toml`. No code is written for this experiment.

**Run allocation, six runs.** Each configuration is run twice: the first run of each is primary and supplies every number in section 7; the second supplies that configuration's repeatability estimate and nothing else. Which run is primary is fixed in advance (section 1) and is not chosen after the fact.

| Configuration | Runs |
|---|---|
| Reference precision, 12 iterations | 2 |
| RAFT native mixed precision, 12 iterations | 2 |
| Reference precision, 8 iterations | 2 |

**Arm 1, iteration budget: 12 to 8 iterations (`[adapter] iterations`).** A **sensitivity arm**, and a real deployment change in its own right: the quality-versus-latency knob specific to iterative models.

Truncation does **not** guarantee continued movement or worse predictions, and this protocol does not assume it does. A case that had converged by iteration 6 is settled at 8 as well. Both outcomes are measured rather than assumed: **report how many truncated cases are still moving at their last iteration, and how many are worse against ground truth.** A positive result here checks that the measurement pipeline and the outcome measure can detect an effect, and supports conclusions **about truncation specifically**. It does not support the mixed-precision hypothesis.

**Arm 2, RAFT's native mixed-precision execution (`[adapter] mixed_precision`).** This is the test that decides the wedge. Describe it precisely: RAFT applies autocast **selectively** around parts of its network and returns some features to full precision explicitly, so this arm is not "the model in FP16". It is the mixed-precision path RAFT's authors wrote, run through `args.mixed_precision`, and the result licenses conclusions about **that configuration**. INT8 and a compiled engine are separate experiments with separate evidence.

**Arm 3, TensorRT: deferred, and deliberately.** A compiled engine may fuse the refinement loop and expose no per-iteration boundary at all, which is a different and much larger instrumentation problem than a forward hook on an eager module. Arm 3 is attempted only if Arm 2 produces a result worth the graph surgery.

## 5. Splits

100 configuration cases and 100 held-out cases, random with a fixed seed, committed before the first run. Diagnostics are frozen on the configuration half; the held-out half is touched once.

KITTI-2015 pairs are independent scenes, so a per-case split is sound here. On any dataset with sequences, split by sequence and keep related frames together, or every number is inflated.

## 6. Methods compared

All four rank the same 100 held-out cases without seeing ground truth.

- **R, random.** The floor.
- **B1, output difference.** Per-case mean endpoint difference between the two builds' flow fields. No labels, no trajectories. This is roughly what activation matching gives you at the output, and it is the baseline that matters.
- **B2, two convergence numbers.** Difference in last-update magnitude and in late-to-early ratio between the builds. Cheap trajectory, no geometry.
- **RB, the shipped rule.** Paired trajectory regression as `rb run` computes it, with limits frozen per section 1.

The primary comparison is **RB against B1**. The secondary is **RB against B2**: if two numbers do the work, the product claim is two numbers, which is cheaper to explain and cheaper to maintain.

## 7. Outcome measures

KITTI ground truth decides whether a case worsened, and it stays outside the deployed diagnostic: no ranking method sees it.

A **true regression** is a held-out case where the faster build's per-case EPE against ground truth exceeds the reference build's by more than the 95th percentile of that configuration's repeat variation (gate 4). An **improvement** is the same in the other direction. Cases inside that band are **effectively unchanged**: they are excluded from the restricted analysis and reported as a count, per the frozen definition in section 1.

- **Primary, unrestricted.** Precision at a fixed inspection budget K (K = 10 and 20, both reported): of the top K cases each method ranks, how many are true regressions.
- **Primary, restricted (this is H1).** Take only the cases where B1 exceeds its frozen selection threshold, that is, where a plain output diff already says something changed. Within that set, rank by RB and, separately, **by B1's own continuous score**, at the same budget. The measure is whether RB's precision at K exceeds B1's **within the set B1 selected**. AUROC for regression against improvement is reported for both methods, never for RB alone.
- **Reported alongside:** how many true regressions each method misses entirely, how many flagged cases were marked borderline, and the count of effectively unchanged cases.

No significance testing at one model, one checkpoint and one build pair. This is an existence check and a magnitude estimate, and the write-up says so.

## 8. Pre-registered predictions and decision rule

- **Arm 1 (sensitivity):** truncation produces materially changed cases, and RB and B1 both beat R. If neither beats R, the machinery or the outcome measure is broken; fix and restart. Report separately how many truncated cases are still moving at the last iteration and how many worsened; if truncation turns out to produce few still-moving cases, that is itself a finding about the trajectory rule.
- **Arm 2, primary unrestricted:** RB beats B1 on precision at K=20 by at least 20 percent relative.
- **Arm 2, primary restricted (the decisive one):** **RB's precision at K exceeds B1's continuous-score precision at K inside B1's selected set.** This is the test the whole wedge rests on, and the one most likely to fail, because the magnitude of the output difference is itself informative about direction.
- **If RB does not beat B1 on the restricted comparison, H1 is not supported.** The deployment wedge does not become the first commercial hypothesis, historical replay across checkpoints remains the first engagement, and section 1 of the go-to-market plan reverts. This is written down now so the result cannot be reinterpreted later.
- **If B2 is within 5 percent of RB:** the claim is "two convergence numbers", not the refinement geometry. Say that, simplify the pitch, and keep the geometry for the fitted scorer.
- **If fewer than 20 held-out cases change materially (gate 4):** the arm is **inconclusive**, not null. RAFT's selective mixed precision is too mild a perturbation to test this on, and the next step is a harsher build, not a harsher claim.

## 9. What each outcome licenses

- **RB beats B1, unrestricted and restricted:** "on this model and this build pair, refinement dynamics picked the cases that mattered better than comparing the outputs." One model, one build pair. Not "works for deployment builds."
- **RB beats B1 unrestricted only:** the gain may be that trajectories detect change, which the output diff also detects. Weak; do not build outreach copy on it.
- **RB does not beat B1 on the restricted comparison:** the deployment wedge is unsupported. Historical replay across checkpoints remains the first engagement, and section 1 of the go-to-market plan reverts.
- **Inconclusive (too few materially changed cases):** nothing may be said about the hypothesis either way. Saying "no effect found" would be a false null.
- **Gate failure:** the claim is untestable as instrumented, which is itself worth publishing, because anyone attempting this will hit the same wall.

## 10. Threats to validity

- One model, one checkpoint, one dataset, one build pair. Establishes existence, never transfer. Transfer needs the separate build, family and customer coverage counts.
- KITTI-2015 is RGB automotive at 1242x375. The deployment leads we have in hand run monochrome and IR stereo indoors on edge hardware. Transfer to that setting is assumed here and is not shown.
- KITTI ground truth is sparse; every per-case number is over the valid mask only, and "true regression" inherits that.
- RAFT's native mixed precision applies autocast selectively and restores some features to full precision, so it is a mild perturbation next to INT8 or a compiled engine. A null result here does not rule out an effect in harsher builds, and must not be reported as if it did.
- The repeatability estimate comes from two runs per configuration. Two runs bound run-to-run variation loosely and say nothing about variation across drivers, GPUs or library versions.
- The outcome measure uses ground truth that a customer in the intended setting may not have. What this experiment establishes about ranking must be re-established against their acceptance tests, not assumed.
