# Pre-registration: does the frozen stopping rule transfer to unused cases?

_Version 2, 2026-09-20. Supersedes version 1 (sha256 `12a44de573f07ad8f29cb73b85d87b7c7d1a3ba4a2a75bb651d75ea0ffe83191`,
committed `308249a`). **Amended before any KITTI-2012 image was downloaded, loaded, scored or
evaluated.** Version 1 remains in the history; the amendment log is at the end and says what changed
and why. The result is reported against this document's hash._

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

This is a transfer test across *related* benchmarks, not a re-run on fresh scenes from the same
distribution. KITTI-2015 was built specifically to add dynamic scenes, which KITTI-2012 largely
lacks, so the flow statistics differ in a known and deliberate way. That is what makes the test
informative: a rule calibrated on scenes with independently moving objects is being asked to hold on
scenes that are mostly ego-motion.

Chosen over Sintel deliberately. The allowance is an absolute 0.05 px, which only means the same
thing at a comparable error scale. KITTI-2012 is the same camera rig with a similar error range, so
the frozen allowance transfers without redefinition. Sintel is a different domain with a different
EPE scale, where an absolute 0.05 px would silently become a much tighter or looser budget; that is
a separate test needing its own pre-registered allowance, and it is not this one.

No adapter change is required, which matters because changing the code would itself be a deviation.
KITTI-2012 ships colour images as `colored_0/` where the adapter expects `image_2/`; a directory
alias fixes that without touching `rabbit_brain`. Ground truth is `flow_occ/` in both.

Checkpoint `raft-things.pth`, the same one throughout. `mixed_precision = false`,
`alternate_corr = false`, batch 1, `InputPadder(mode="kitti")`.

## Primary outcome, fixed now

    delta = mean EPE(policy over all 194) - mean EPE(fixed 12 over all 194)

**PASS if delta <= 0.05 px. FAIL otherwise.** One number, one comparison, one look.

## The decomposition, pre-registered as the diagnostic

The primary outcome factors exactly into two quantities, and both are reported whatever happens:

    delta = (stopping rate) x (mean forgone improvement among stopped cases)

where the forgone improvement of a stopped case is `g_i = EPE_i(8) - EPE_i(12)`, what those four
skipped iterations would have bought. On the KITTI-2015 held-out half this was

    0.58 x (+0.0838 px) = +0.0486 px

with the forgone improvement distributed as median +0.0343, p90 +0.2142, max +1.3483, and 13 of the
58 stopped cases actually *helped* by stopping early.

Reporting both factors separately is what makes a result interpretable rather than just a verdict.
A delta that moves can move because the rule stops more often, or because the cases it stops have
more to lose, and those are different findings with different consequences.

## Prediction, stated in advance

KITTI-2012 has smaller flow magnitudes and mostly static scenes, so iteration-8 updates should be
smaller and more scores should fall below 0.7017. **The prediction is that the stopping rate exceeds
58%**, the rate observed on the KITTI-2015 holdout.

**The direction of the second factor is deliberately not predicted.** A higher stopping rate does not
by itself mean more damage: if the additional stopped cases are ones that had already converged by
iteration 8, their forgone improvement is near zero and the product can stay flat or fall. It is
equally possible that easier scenes converge sooner and the mean forgone improvement *drops*,
offsetting the higher rate entirely. Which of these happens is the substance of the test, and
guessing at it now would be pretending to knowledge I do not have.

So the failure mode being probed is specific: the rate rises and the forgone improvement does not
fall enough to compensate, so their product breaches 0.05 px. That is the shape of what killed the
first frozen absolute threshold, where the continue rate fell from 47 to 36 across halves and the
extra stopping cost 0.022 px more than budgeted.

## Secondary, reported always, deciding nothing

Stopping rate and mean forgone improvement (above); measured paired latency saving; mean iterations;
the full per-case harm distribution; delta as a fraction of baseline EPE; the `u8` score quantiles on
KITTI-2012 beside the KITTI-2015 holdout, since a distribution shift in the score is the mechanism
any transfer failure would run through; bitwise equivalence checks.

These are descriptive. **None of them can turn a FAIL into a PASS**, and no subgroup, filtered
subset or alternative allowance will be introduced after the fact.

## Procedure

`gpu_policy_run.py` (sha256 `ce46c5a00b8d222ff14af15987c72b8dbdde4c39023aac6807b03c200565b442`),
unmodified, `--rounds 5`, on one GPU, with the full 194-case list. The run emits every per-case
decision, score, error and latency, so the analysis is fixed by the script rather than chosen after.

One run. One look. A re-run is permitted only if the run fails for a technical reason **and no
result line was read**; any re-run is recorded here with its reason.

## What each outcome licenses, agreed before seeing it

**PASS.** The frozen threshold transferred from KITTI-2015 to KITTI-2012 at this checkpoint,
resolution and batch size. That supports **a documented benchmark preset**, named for the benchmarks
it was tested on, with its provenance and measured budget attached. It does not support a claim about
"KITTI-like data" as a family, nor about other checkpoints, architectures, resolutions, batched
execution, or other domains. Each of those is a further test, and the preset's scope is exactly the
set of conditions it has been checked under.

**FAIL.** The frozen threshold did not transfer from KITTI-2015 to KITTI-2012. That is what it
establishes, and no more. In particular it does **not** validate per-model calibration as the
alternative product: that route requires its own evidence, namely that recalibrating on a
customer's configuration half reliably produces a threshold that then holds on their held-out half,
repeatedly and across models. Until that is tested, per-model calibration is a plausible route and
nothing stronger. A failure here removes one option; it does not confirm another.

**Either way**, the latency result stands on its own: 13.1% was measured on an executed policy
against an equally optimised baseline on the original workload, and that does not depend on this
outcome.

## What this test cannot do

One checkpoint, one architecture, one resolution, batch 1, two related benchmarks. It says nothing
about whether `u8` is the best available score, only whether this frozen rule holds up. It is a
single test with n = 194 and no correction for being the first of what may become several; the
second dataset tested will need that acknowledged.

## Amendment log

**Version 2, 2026-09-20, before any data was downloaded.** Four corrections, all raised by Astra
against version 1:

1. **The prediction's reference rate was reversed in v1.** It said "more than 42% of cases will fall
   below 0.7017 and stop." 42% was the *continue* rate; 58% stopped. The prediction is now stated
   against 58%, which is the number it always should have been.
2. **v1 asserted that more stopping spends more error.** That does not follow. Damage is the product
   of the stopping rate and the forgone improvement of the cases stopped, and the second factor can
   fall as the first rises. v1's reasoning collapsed the two. The decomposition is now pre-registered
   explicitly and both factors are reported.
3. **v1's PASS clause over-scoped.** It licensed "a default threshold for KITTI-like data", a family
   that has no clear boundary. Narrowed to a documented preset named for the benchmarks actually
   tested.
4. **v1's FAIL clause was a non-sequitur.** It treated failure of transfer as narrowing the product
   to per-model calibration, which reads as failure validating the alternative. It does not; that
   route needs its own repeatability evidence. Corrected.

No data had been downloaded, loaded or inspected at the time of this amendment. Version 1 remains in
the git history at `308249a`.
