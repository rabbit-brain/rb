# Pre-registration: does the frozen stopping rule transfer to unused cases?

_Written 2026-09-20, before any KITTI-2012 image has been loaded, scored or evaluated. Committed
before the data is downloaded. This document is hashed; the result is reported against this hash._

## Why this test exists

Everything measured so far used KITTI-2015. The configuration half chose the threshold and the
held-out half has since been examined many times: for the guard sweep across z, for the decision
equivalence check, for the per-case harm table, and for the GPU run. It is development data now.
The 13.1% latency saving is a real measurement of a real implementation, but the claim that the
quality trade-off *holds* rests on data the rule has effectively been tuned against.

This test touches data the rule has never seen, once.

## The rule under test

`frozen_rule.py`, sha256 `0345a0292038c3cb2e57bb68fc87d81ed278b24a6801c961d274f7d53c9f8c2b`,
committed `d41a409` on 2026-09-20. **Unmodified.** No recalibration, no re-fitting, no new z.

    score      u8, the mean per-pixel L2 magnitude of iteration 8's coarse update, times 8.0,
               rounded to 4 decimals
    branch     after iteration 8 of a maximum 12
    decision   continue if score >= 0.7017, else stop and emit at 8
    allowance  0.05 px of mean EPE against fixed 12

## Dataset

KITTI-2012 (`data_stereo_flow`), training split, **all 194 labelled pairs**, used once.

Chosen over Sintel deliberately. The allowance is an absolute 0.05 px, which only means the same
thing at a comparable error scale. KITTI-2012 is the same camera rig and a similar error range to
KITTI-2015, so the frozen allowance transfers without redefinition. Sintel is a different domain with
a different EPE scale, where an absolute 0.05 px would silently become a much tighter or looser
budget; that is a separate test needing its own pre-registered allowance, and it is not this one.

No adapter change is required, which matters because changing the code would itself be a deviation.
KITTI-2012 ships colour images as `colored_0/` where the adapter expects `image_2/`; a directory
alias fixes that without touching `rabbit_brain`. Ground truth is `flow_occ/` in both.

Checkpoint `raft-things.pth`, the same one throughout. `mixed_precision = false`,
`alternate_corr = false`, batch 1, `InputPadder(mode="kitti")`.

## Primary outcome, fixed now

    delta = mean EPE(policy over all 194) - mean EPE(fixed 12 over all 194)

**PASS if delta <= 0.05 px. FAIL otherwise.** One number, one comparison, one look.

## Secondary, reported always, deciding nothing

Measured paired latency saving; continue rate; mean iterations; per-case harm distribution; delta as
a fraction of baseline EPE; bitwise equivalence checks. These are descriptive. **None of them can
turn a FAIL into a PASS**, and no subgroup, filtered subset or alternative allowance will be
introduced after the fact.

## Procedure

`gpu_policy_run.py` (sha256 `ce46c5a00b8d222ff14af15987c72b8dbdde4c39023aac6807b03c200565b442`),
unmodified, `--rounds 5`, on one GPU, with the full 194-case list. The run emits every per-case
decision, score, error and latency, so the analysis is fixed by the script rather than chosen after.

One run. One look. A re-run is permitted only if the run fails for a technical reason **and no
result line was read**; any re-run is recorded here with its reason.

## The specific failure mode this is designed to catch

The first frozen absolute threshold failed on the KITTI-2015 holdout for one reason: the score
distribution shifted between halves, so the same cutoff continued 47 of 100 configuration cases but
only 36 held-out, and the extra stopping cost 0.022 px more than budgeted.

KITTI-2012 is generally the easier dataset: fewer independently moving objects, smaller flow
magnitudes. Smaller flows should mean smaller iteration-8 updates, so **the prediction is that more
than 42% of cases will fall below 0.7017 and stop.** That buys a larger latency saving and spends
more error. Whether it spends more than 0.05 px is exactly what this test asks.

Stating the direction in advance is the point. If the continue rate drops sharply and the test still
passes, that is strong evidence. If it drops and the test fails, the diagnosis is already written
down rather than constructed afterwards.

## What each outcome licenses, agreed before seeing it

**PASS.** The threshold transfers across scenes within a domain, at this checkpoint. It licenses
shipping a default threshold for KITTI-like data with raft-things, and it licenses the claim that the
quality budget was real rather than fitted. It does **not** license a claim about other checkpoints,
other architectures, other resolutions, batched execution, or other domains.

**FAIL.** The threshold is dataset-specific. The honest product claim narrows from "adaptive stopping
with a default threshold" to "a procedure for calibrating and validating an adaptive-stopping rule
for your model and data, with a quality budget you can state and check." That is still a product,
and arguably the more defensible one, but it is a different one and the messaging must change with
it. A failure will be reported as prominently as a pass.

**Either way**, the latency result already stands on its own: it was measured on an executed policy
against an equally optimised baseline, and that does not depend on this outcome.

## What this test cannot do

It uses one checkpoint, one architecture, one resolution, batch 1, and one domain. It says nothing
about whether the score `u8` is the best available score, only whether this frozen rule holds up. It
is a single test with n = 194 and no correction for the fact that it is the first of what may become
several; the second dataset tested will need that acknowledged.
