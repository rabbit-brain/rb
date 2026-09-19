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

## 11. Pre-registration record

Appended before any measured run, which section 1 permits: the file is closed to edits from the first measured run onward, and to amendments only after that.

| Item | Value |
|---|---|
| Protocol, first version | `b8aadac` |
| **Protocol, frozen version (attach this to results)** | **`2bbaaec`** |
| `rb.toml`, reference precision, 12 iterations | not yet committed |
| `rb.toml`, RAFT native mixed precision, 12 iterations | not yet committed |
| `rb.toml`, reference precision, 8 iterations | not yet committed |
| Split seed and the two case lists | not yet committed |
| First measured run | not yet performed |

`b8aadac` framed the truncation arm as a positive control, compared the restricted analysis against chance rather than against the output difference's own continuous score, described the repeatability estimate as a noise floor, and recognised only two outcomes. `2bbaaec` corrects all four. Both predate any run, and the difference between them is public so that the tightening cannot be mistaken for post-hoc selection.

The remaining rows are filled in, and this table committed again, at the moment the diagnostics are frozen and before the held-out half is touched.

## 12. Results and amendments (2026-09-20)

Run on an RTX 4090 (torch 2.8.0+cu128, driver 580.178.04), `rabbit-brain` 0.2.2 from PyPI, driver and splits from `1af9d03`, checkpoint `raft-things.pth`, KITTI-2015 training, seed 20260920. Artifacts on the pod volume at `/workspace/exp/`: `RESULTS.md`, `FROZEN.py`, and five comparison JSONs.

### Amendment 1, before the freeze

The driver as committed at `1af9d03` recorded each build's error against ground truth and both trajectories, but **not the per-case difference between the two builds' outputs**. B1 is defined as exactly that and is required to be label-free, so `|candidate_error - baseline_error|` is not B1: it uses ground truth. As committed, the decisive comparison could not be computed.

Fixed by wrapping `adapter.infer` to capture each prediction's output as a side effect, leaving `evaluate_model` and the evaluation path untouched, and computing the mean endpoint difference between the two builds' flow fields with no valid mask. The configuration half was re-run. This happened **before** the freeze and before any held-out case was evaluated: it is missing instrumentation the protocol always required, not a diagnostic chosen after seeing an outcome.

### Gates

| Gate | Result |
|---|---|
| 1. Hook fires once per iteration | PASS: 12, 12 and 8 under the three configurations |
| 2. Adapter agrees with the reference evaluation | PASS: 5 cases, max difference **0 px** against RAFT's own `evaluate.py` |
| 3. No FP16 underflow | PASS: smallest update magnitude 0.0304, against FP16's smallest normal of about 6e-5 |
| 4. Repeatability | **Exactly zero.** Two identical runs, 0 of 100 cases differed, both configurations |

Gate 4 is stronger than the protocol anticipated. With a zero repeat band every non-zero build difference is material, and all 100 cases changed under mixed precision against a pre-registered inconclusive threshold of 20 cases. The arm was decisively testable. **This bounds run-to-run variation on one GPU back to back and nothing more**; `claude/cross-machine-reproduction.md` measures a different source and still applies.

### Effect sizes, configuration half

| Build | median abs change | max | worse | better |
|---|---|---|---|---|
| RAFT native mixed precision | 0.0041 px | 0.547 px | 53 | 47 |
| Truncated 12 to 8 iterations | 0.231 px | 3.84 px | 94 | 6 |

The mixed-precision effect is nearly symmetric, so a method ranking by how much the output moved spends about half its budget on improvements. That is the gap H1 proposed to close.

### Frozen diagnostics

RB score: the candidate build's own `late_update`, higher is worse. One score for both arms; choosing per arm would be double dipping. B1 selection threshold: 0.0295 px, the configuration-half median. Recorded in `FROZEN.py` along with the configuration-half predictions, before the held-out half was evaluated.

### Held-out results

AUROC for worse against better, n = 100 per arm.

| | B1 | RB |
|---|---|---|
| Mixed precision, unrestricted | 0.525 | 0.520 |
| **Mixed precision, restricted (n=46)** | **0.437** | **0.444** |
| Truncation, unrestricted | 0.656 | 0.649 |
| **Truncation, restricted (n=98)** | **0.641** | **0.635** |

Precision at 10: mixed precision B1 0.40, RB 0.50 against a base rate of 0.55; truncation both 0.90 against a base rate of 0.84.

### Verdict under the pre-registered rule

**RB does not beat B1 on the restricted comparison in either arm. H1 is not supported.** The deployment wedge does not become the first commercial hypothesis; historical replay across checkpoints remains the first engagement, and section 1 of `claude/concierge-engagement.md` reverts.

The configuration-half predictions written into `FROZEN.py` before the holdout ran were both confirmed.

### The finding worth carrying forward

In the truncation arm **all 100 cases were still moving more at the last iteration than the 12-iteration build, and 94 were worse**. The condition fires, and fires correctly. Because it fires on everything it has no discriminating power, and the plain output difference still ranked the damage marginally better.

So the trajectory statistic correctly detects that a build did not settle, and adds nothing over an output diff about which cases that hurt.

This does not refute the product's core claim, which concerns two genuinely different checkpoints on cases with no label, where `claude/real-weights-run.md` found rho about 0.9 between last-update magnitude and per-case error. The plausible reading is that trajectory carries information about model quality but not about numerical perturbation, which are different mechanisms.

The open question it leaves, which should be answered before anything is sold: if an output difference ranks regressions as well as the trajectory does in the setting where ground truth is available to check, what is the argument for trusting the trajectory where it is not? Arm 3 (TensorRT, INT8) is not the next step. That question is.

### Amendment 1a: the driver in the repository

The fix described in amendment 1 was applied on the pod and the experiment ran with it, but the version committed at `1af9d03` did not carry it. The repository therefore described a fix it did not contain, which would have stopped anyone reproducing this from computing B1 at all.

Corrected here. The committed driver now captures each build's output by wrapping `adapter.infer`, leaving `evaluate_model` and the evaluation path untouched, and writes the per-case mean endpoint difference between the two builds' flow fields onto each case as a `b1=` tag.

**Honest limit on this artifact.** The committed version is a deterministic replay of the two string patches applied on the pod, from the same `1af9d03` starting point. It was not byte-compared against the copy that produced the results, which remains at `/workspace/exp/build_compare.py` on the pod volume. Anyone reproducing this should compare the two before relying on exact agreement.

### Amendment 2: recorder ablation, pre-registered before the rerun

**Status: pre-registration. Written and committed before the corrected channel was run on either split.** The data underneath is not fresh: both splits have already been examined and the holdout results above are public. Section 9's licensing therefore does not apply to anything below, and the decision rule at the end says what does.

#### What prompted it

The recorder attaches a forward hook to `model.update_block` and keeps output index 2, `delta_flow`. RAFT's update block returns three things, `(net, up_mask, delta_flow)`, and the second one is never read. `up_mask` is what decides how each coarse update is spread over the 8x8 block of output pixels it covers, so the amount the model's output actually moves in an iteration is not `delta_flow` scaled by 8. It is a quantity neither recorded field gives alone. `convergence()` computes every direction and displacement statistic from the same fields, so it inherits this.

The asymmetry matters here specifically. **B1 was computed from `pred.output`, the final full-resolution flow. The trajectory was computed from the coarse channel.** The two methods compared in the held-out table above were therefore not looking at the same thing, and the one that lost was the one looking at the lower-resolution signal. That is a defect in the comparison, not a finding about trajectories.

#### What changes, and what does not

Only which numbers fill `baseline_trajectory` and `candidate_trajectory`. The corrected channel is the mean magnitude of the change in the model's own full-resolution output per iteration: `upsample_flow` wrapped as a bound method, its cumulative outputs unpadded, differenced in FP32 from a flow of exactly zero, scale 1. RAFT initialises `coords1` equal to `coords0` and the adapter passes no `flow_init`, so T iterations give exactly T updates and there is no starting convention to choose.

Both channels are recorded in the same inference pass. The per-case errors, the B1 tags, the splits, the checkpoint, the limits and the analysis code are the same objects in both documents. Every decision rule the product applies to an imported comparison reads the scalar magnitude sequence alone, so this is a one-list-per-case substitution and nothing else.

#### Verified before any number was produced

On real weights (`raft-things.pth`) at true KITTI geometry, 1242 by 375, which pads by 6 and 1 so the crop is not a no-op:

| Check | Result |
|---|---|
| Output unchanged by instrumentation | Bitwise identical, by sha256 of the tensor bytes, not by aggregate EPE |
| The corrected channel's last cumulative state is that output | Bitwise identical |
| Differences sum back to the output | max abs error 7.5e-09 px on an 8 px field |
| One value per iteration, per channel | 12 and 12 |

Two defects in the harness were found by those checks rather than by inspection. The adapter's `infer` already attaches the coarse recorder, so attaching it again in the wrapper put two hooks on one module and recorded every coarse update twice, which depresses the reversal rate and inflates mean cosine. And the driver lent its recorder to the dual recorder, whose `reset()` then emptied it before `evaluate_model` read it, producing empty trajectories with no error raised anywhere. Both are fixed, and the count assert that caught the first is permanent.

The mixed-precision arm cannot be validated off the GPU: `torch.cuda.amp.autocast` is a no-op on CPU, confirmed by byte-identical output digests with it enabled and disabled. Local validation covers the machinery and the truncation arm only.

#### One threshold does not transfer

`max_late_share` and `max_reversals` are scale-free, a ratio and a relative growth count, and carry over unchanged. `max_trajectory_regression` and `max_last_update` are in absolute trajectory units, and the corrected channel's magnitudes are systematically smaller than the coarse channel's. Reusing 0.3 would silently make the corrected channel a stricter test and confound the comparison.

The headline measure, precision at 10 and at 20, is a ranking measure and does not depend on any threshold. Where flag rates are reported, the absolute threshold is re-derived on the **configuration half only**, by the identical procedure used for the coarse channel, and labelled as re-derived.

#### Predictions, recorded before the run

1. The corrected channel's per-iteration magnitudes are smaller than the coarse channel's on the same case, by a roughly constant within-case factor.
2. Spearman rho between the two channels' `late_update` scores, across held-out cases, exceeds 0.8.
3. **The corrected channel does not overturn the verdict.** On the restricted comparison, in both arms, corrected-RB AUROC does not exceed B1's by more than 0.02.

Prediction 3 is the one that makes this worth running. It predicts that the fix fails to rescue the result.

#### Decision rule

If prediction 3 holds, the recorder gap is closed as a correctness matter and nothing else changes: the deployment wedge stays off the plan, and section 12's verdict stands with the defect named and measured rather than merely suspected.

If prediction 3 fails, that is **hypothesis-generating only**. The splits have been examined and the outcome is known, so a corrected channel that wins here cannot be reported as a confirmation of H1. It licenses exactly one thing: a fresh pre-registered test on data not yet touched, with the corrected channel fixed in advance.

#### Unconditional consequence

Whatever the ablation returns, the recorder's documentation is wrong today. `recorder.py` and the adapter both describe the trajectory as the model's refinement, and for RAFT it is the movement of an internal field at one eighth resolution. That is a product correctness issue independent of whether the channel changes any ranking, and it is tracked in `claude/recorder-upsampling-gap.md`.

#### Machine change

The pod lost its GPUs between the frozen run and this one and its data was migrated, so the corrected channel will not be measured on the hardware that produced the table above. Gate 4's exactly-zero repeat variance was a property of that machine. The original coarse configuration is therefore re-run first on the new hardware and compared against the saved numbers. Exact reproduction strengthens gate 4; any difference is itself a finding and bears on `claude/cross-machine-reproduction.md`.

### Amendment 2: results

Run on the migrated pod: RTX 4090, kernel 6.8.0-134, NVIDIA driver 580.159.04, torch 2.8.0+cu128, `rabbit-brain` 0.2.2 from PyPI, checkpoint `raft-things.pth`, the same two frozen splits. Artifacts on the pod volume at `/workspace/exp/ablation/`, with the executed originals preserved under `/workspace/exp/archive/`.

#### The machine check came back exact

The archived driver, unmodified, re-run on the truncation configuration split:

| Field | Identical | max abs difference |
|---|---|---|
| baseline_error | 100/100 | 0.0 |
| candidate_error | 100/100 | 0.0 |
| baseline_trajectory | 100/100 | 0.0 |
| candidate_trajectory | 100/100 | 0.0 |
| B1 tag | 100/100 | 0.0 |

The machines differ: kernel 6.8.0-138 to 6.8.0-134, driver 580.178.04 to 580.159.04, different physical host. Same GPU model, same torch build, same CUDA, same Python.

**Gate 4 is therefore stronger than it was recorded.** It was stated as bounding run-to-run variation on one GPU back to back. It also held across this host, kernel and driver change, on this workload. Stated narrowly, because that is all it supports: it does not show cross-architecture reproduction, and one benign host change does not make host changes generally benign. `claude/cross-machine-reproduction.md` keeps both its scope and its confound. What this rules out is only that gate 4's zero variance was an artifact of the same process, the same allocation and the same driver.

The same comparison against all five archived runs is identical on every field, so the new driver's coarse channel is the old driver's coarse channel. Two identical runs of the corrected channel also differ by exactly zero, so gate 4 covers it too.

#### Amendment 1a's caveat is discharged

The executed driver is on the volume at sha256 `18726e4e62b8af1ba86a01166a613a47ec6d2bfd14fdda2132f47bc65b6245e7`, 9565 bytes. The repository's reconstruction at `edae22c` is byte-identical: same hash, same length. The deterministic replay was exact. The caveat in amendment 1a can be read as discharged rather than outstanding.

#### Instrumentation on the GPU

`verify_channels.py` passes at both precisions on the 4090, including under real FP16 autocast, which CPU cannot exercise. The output tensor is bitwise identical with instrumentation on and off; the corrected channel's last cumulative state is that output, bitwise; the differences sum back to it to 1.5e-05 px on a 193 px field; twelve values per channel.

#### Predictions

| | Predicted | Observed |
|---|---|---|
| P1 | corrected magnitudes smaller, roughly constant within-case factor | median ratio 0.71 to 0.77 across the four blocks, p10 0.56, p90 0.84, max 0.90 |
| P2 | Spearman rho above 0.8 | **0.9984 to 0.9990** |
| P3 | corrected RB does not exceed B1 by more than 0.02 restricted | largest excess 0.009 |

All three hold. P2 holds far more strongly than predicted, and it is the finding that explains the rest.

#### Held-out results, all three channels

AUROC for worse against better, n = 100 per arm, restricted to cases at or above B1's frozen selection threshold of 0.0295 px.

| Arm | n | base rate | | B1 | RB coarse | RB corrected |
|---|---|---|---|---|---|---|
| Mixed precision, unrestricted | 100 | 0.55 | AUROC | 0.525 | 0.520 | 0.520 |
| | | | prec@10 | 0.40 | 0.50 | 0.40 |
| | | | prec@20 | 0.65 | 0.60 | 0.60 |
| **Mixed precision, restricted** | 46 | 0.61 | AUROC | **0.437** | **0.444** | **0.446** |
| | | | prec@10 | 0.40 | 0.50 | 0.40 |
| | | | prec@20 | 0.65 | 0.60 | 0.60 |
| Truncation, unrestricted | 100 | 0.84 | AUROC | 0.656 | 0.649 | 0.647 |
| | | | prec@10 | 0.90 | 0.90 | 0.90 |
| | | | prec@20 | 0.90 | 0.90 | 0.90 |
| **Truncation, restricted** | 98 | 0.85 | AUROC | **0.641** | **0.635** | **0.635** |
| | | | prec@10 | 0.90 | 0.90 | 0.90 |
| | | | prec@20 | 0.90 | 0.90 | 0.90 |

Configuration half, for completeness: mixed precision B1 0.486, coarse 0.502, corrected 0.505 unrestricted, and 0.497, 0.535, 0.537 restricted over 51 cases; truncation B1 0.702, coarse 0.670, corrected 0.665 unrestricted, and 0.710, 0.677, 0.672 restricted over 99 cases.

Two honest notes on reading this table. In the mixed arm every score sits below 0.5, that is below chance, and they are separated by less than 0.01; RB's nominal lead over B1 there is not a win for either, which is why the pre-registered bound in P3 was set at 0.02 rather than at zero. And with n = 100 and these base rates, precision at 10 moves in steps of 0.10, so it is a blunt instrument next to AUROC; the one place the channels differ at all, mixed precision at k = 10, is a single case changing places.

#### Verdict

**The recorder defect was not the reason H1 failed.** Section 12's verdict stands unchanged, now with the suspected confound measured rather than assumed. The deployment wedge stays off the plan and historical replay across checkpoints remains the first engagement.

Under the decision rule, prediction 3 held, so this closes the recorder gap as a correctness matter and licenses nothing further. No fresh test is triggered.

#### Why the correction changed so little

The corrected channel is very nearly a rank-preserving rescaling of the coarse one: Spearman rho of 0.999 on `late_update`, with the corrected value about three quarters of the coarse value. The convex upsampling RAFT applies is a weighted average over a 3 by 3 neighbourhood of the coarse field, and averaging shrinks magnitudes while largely preserving their ordering across cases. So the mask does carry information the recorder never saw, and the shrinkage is real and case-dependent between 0.56 and 0.90, but it is not information that reorders cases.

That is worth stating plainly because it cuts both ways. It means the published table was not distorted by the defect. It also means that for RAFT specifically the coarse channel is an adequate proxy, so the fix is a correctness and documentation matter rather than an accuracy improvement, and the case for recording the output channel rests on models where the upsampling is not close to an average.

#### Provenance of this artifact

Amendment 1a happened because the repository described a fix it did not contain. The copies below were
pulled back off the pod after the run and byte-compared, so the repository carries what executed rather
than a replay of it. `RESULTS-ablation.md` on the pod volume lists the same hashes beside the outputs.

| File | sha256 |
|---|---|
| `dual_recorder.py` | `f83dd0b807b9bef8ad508d70148bc99fd73bb74a4262370f27646a8da12f4d0b` |
| `verify_channels.py` | `045a799540fe7a6ac4e64dcecddf149108330f8a813c8d1388bbd9faaebb45ba` |
| `build_compare.py` | `ef7e0c0bfec4c637daa8955d660a35b506cb2cc03bab363f49a77b513bf5bfc8` |
| `analyze_ablation.py` | `1a141c7470a7dabe47ad41085ca44bd018f64395b6330df1e9a9c91a2f3fb85b` |
| `compare_runs.py` | `855f16310f792230cfcbf16c7cb01d72e6b04741be01a6339e0e04909f89bbbe` |

#### What remains true regardless

The recorder's documentation still describes the trajectory as the model's refinement when for RAFT it is the movement of an internal field at one eighth resolution. That is unchanged by this result and is tracked in `claude/recorder-upsampling-gap.md`.

And the open question from section 12 is untouched. If an output difference ranks regressions as well as the trajectory does where ground truth exists to check, the argument for trusting the trajectory where it does not still has to be made. The corrected channel does not make it.
