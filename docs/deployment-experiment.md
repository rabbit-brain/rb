# The deployment-build experiment: pre-registered protocol

_Question: when a team ships the same source checkpoint through a faster deployment build, do trajectory features pick the cases worth inspecting better than a plain comparison of the two builds' outputs? The cheap baseline is included on purpose and given every advantage: if a per-case output difference already ranks the consequential cases, the refinement geometry adds nothing and the claim is dropped. This file is committed before the first measured run and is not edited after it; anything learned during the feasibility gate is appended as an amendment._

## 1. Pre-registration (fixed before the first measured run)

- The hypothesis (2), the gate (3), the arms (4), the splits (5), the methods (6), the measures (7) and the predictions (8) are fixed before any number is recorded.
- Diagnostics are **frozen** after the configuration split and before the held-out split is touched. Limits, thresholds and any feature selection are written into `rb.toml` and committed at that moment.
- Every run records its `record.json`: checkpoint sha256, model-code commit, dataset hashes, seeds, environment, hook status, adapter agreement, and the exact command.
- Ground truth is never visible to any ranking method. It is used only to compute the outcome in section 7, after the ranking is fixed.

## 2. The hypothesis, stated so it can fail

A per-case output difference between two builds tells you the answer **moved**. It cannot tell you the **direction**: a case that moved and improved is wasted inspection budget. The claim under test is that trajectory stability separates those two.

> **H1.** Among cases where the two builds' outputs already differ, cases where the faster build is still moving at its last iteration are more likely to be worse against ground truth than cases where it settled.

That is narrower than "trajectory finds consequential cases" and it is the only version that isolates what Rabbit Brain adds over an output diff. H1 is what section 7's restricted analysis measures.

A trajectory difference establishes a **behavioural** difference. It does not by itself establish a worse prediction. Nothing in this protocol or any write-up of it may say otherwise.

## 3. Feasibility gate (must pass before anything is measured)

If any of these fails, the hypothesis is untestable in its intended setting and that is the result.

1. **The hook fires once per iteration in both builds.** `rb verify-hook --checkpoint X` already asserts this. Run it under each configuration.
2. **Instrumentation does not perturb the thing being measured.** Run the FP32 configuration with and without the recorder attached; per-case EPE must be identical. This is the adapter-agreement discipline applied to the recorder itself.
3. **Trajectory magnitudes are computed in FP32.** Verified in the shipped code: `TrajectoryRecorder.step` calls `delta.detach().float()` before taking the norm, so a half-precision update is widened before measurement rather than after. Confirm no underflow: RAFT's late updates are around 0.0125 in the 1/8-px internal scale, comfortably above FP16's smallest normal value (about 6e-5). Record the minimum observed update magnitude per run.
4. **The nondeterminism floor is measured first.** Run the reference configuration twice on the same machine, unchanged, and record the distribution of per-case EPE differences. Any build difference below that floor is noise, and `rb`'s borderline marking already exists to say so. **If the arm's effect does not exceed the floor on a meaningful number of cases, that arm has nothing to find and the protocol says so rather than reporting the noise.**

## 4. Materials and arms

Model: RAFT, public repository at a fixed commit. Source checkpoint: `raft-things` (one checkpoint, used for every arm; no retraining anywhere in this protocol). Cases: the 200 KITTI-2015 training pairs with `flow_occ` ground truth. Reference build: FP32, 12 iterations. Hardware, inputs, preprocessing and seeds held constant and recorded.

Both arms are a single line in `rb.toml`. No code is written for this experiment.

**Arm 1, iteration budget: 12 to 8 iterations (`[adapter] iterations`).** This is a **positive control, not evidence.** A model truncated before it settled is "still moving at the last iteration" close to by construction, so a win here confirms only that the machinery and the outcome measure can detect an effect that is known to exist. If Arm 1 does not beat random, something is broken and the protocol stops. A win licenses nothing about H1.

**Arm 2, precision: FP32 to autocast FP16 (`[adapter] mixed_precision`).** This is the actual test. The flag is plumbed to RAFT's own `args.mixed_precision`, so the model runs the mixed-precision path its authors wrote.

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

A **true regression** is a held-out case where the faster build's per-case EPE against ground truth exceeds the reference build's by more than the nondeterminism floor from gate 4.

- **Primary, unrestricted.** Precision at a fixed inspection budget K (K = 10 and 20, both reported): of the top K cases each method ranks, how many are true regressions.
- **Primary, restricted (this is H1).** Take only the cases where B1 exceeds its own threshold, that is, the cases where a plain output diff already says something changed. Within that set, does RB rank true regressions above cases that changed and improved? Report AUROC and precision at K.
- **Reported alongside:** how many true regressions each method misses entirely, and how many flagged cases were marked borderline.

No significance testing at one model, one checkpoint and one build pair. This is an existence check and a magnitude estimate, and the write-up says so.

## 8. Pre-registered predictions and decision rule

- **Arm 1 (control):** RB and B1 both beat R comfortably. If they do not, the machinery or the outcome measure is broken; fix and restart.
- **Arm 2, primary:** RB beats B1 on precision at K=20 by at least 20 percent relative. **If RB does not beat B1, H1 is not supported and the deployment wedge does not become the first commercial hypothesis.** The positioning does not change on a null result, and the plan says so in advance.
- **Arm 2, restricted:** RB achieves AUROC above 0.6 at separating regressed from improved within B1's flagged set. This is the sharpest test and the one most likely to fail.
- **If B2 is within 5 percent of RB:** the claim is "two convergence numbers", not the refinement geometry. Say that, simplify the pitch, and keep the geometry for the fitted scorer.
- **If the FP16 effect falls below the nondeterminism floor on more than about 90 percent of cases:** autocast is too mild a perturbation to test this on. Report it, and move to a harsher build rather than to a harsher claim.

## 9. What each outcome licenses

- **RB beats B1, unrestricted and restricted:** "on this model and this build pair, refinement dynamics picked the cases that mattered better than comparing the outputs." One model, one build pair. Not "works for deployment builds."
- **RB beats B1 unrestricted only:** the gain may be that trajectories detect change, which the output diff also detects. Weak; do not build outreach copy on it.
- **RB does not beat B1:** the deployment wedge is unsupported. Historical replay across checkpoints remains the first engagement, and section 1 of the go-to-market plan reverts.
- **Gate failure:** the claim is untestable as instrumented, which is itself worth publishing, because anyone attempting this will hit the same wall.

## 10. Threats to validity

- One model, one checkpoint, one dataset, one build pair. Establishes existence, never transfer. Transfer needs the separate build, family and customer coverage counts.
- KITTI-2015 is RGB automotive at 1242x375. The deployment leads we have in hand run monochrome and IR stereo indoors on edge hardware. Transfer to that setting is assumed here and is not shown.
- KITTI ground truth is sparse; every per-case number is over the valid mask only, and "true regression" inherits that.
- Autocast FP16 is a mild perturbation next to INT8 or a compiled engine. A null result here does not rule out an effect in harsher builds, and must not be reported as if it did.
- The outcome measure uses ground truth that a customer in the intended setting may not have. What this experiment establishes about ranking must be re-established against their acceptance tests, not assumed.
