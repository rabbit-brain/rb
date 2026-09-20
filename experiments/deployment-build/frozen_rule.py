r"""FROZEN streaming stopping policy for RAFT inference. Do not edit.

Named frozen_rule.py, not frozen_policy.py, because the repository already contains the latter and
the working filesystem is case-insensitive: a name differing only in case silently overwrites.

This file is the complete rule. It is frozen on 2026-09-20, before it has been executed on any
full-resolution timing run and before it has touched any unused scene. Anything that runs this policy
imports these constants rather than restating them, so the specification and the implementation
cannot drift apart. The sha256 of this file is recorded alongside every result it produces.

WHAT IS FROZEN
--------------
Score          u8: the mean per-pixel L2 magnitude of the coarse update produced by iteration 8,
               scaled by 8.0 to full-resolution units, ROUNDED TO 4 DECIMALS. It reads only the
               8-iteration run's own trajectory. The step-8 to step-12 difference is never used,
               and nothing downstream of iteration 8 is consulted.
               The rounding is not cosmetic. TrajectoryRecorder.step stores round(value, 4), so
               THETA was selected against rounded values. A live score that is not rounded the same
               way can decide a boundary case differently from the analysis that chose THETA, which
               would show up later as an unexplained disagreement between predicted and executed
               decisions. Rounding here makes the two exactly reproducible.
Branch point   after iteration 8 of a maximum 12.
Decision       continue to 12 if score >= THETA, otherwise stop and emit at 8.
Threshold      THETA = 0.7017, chosen on the CONFIGURATION half only as the smallest continue-set
               satisfying D_cfg + Z * SE_cfg <= ALLOWANCE_PX, where
                   D(theta)  = mean over cases of [ g_i if the case is stopped, else 0 ]
                   g_i       = EPE_i(8) - EPE_i(12)
                   SE        = std(v) / sqrt(n) for v_i = g_i * 1(stopped)
Guard          Z = 1.645. Selected because every guard from 1.0 to 2.5 met the allowance on the
               held-out half; the insensitivity across that range, not this value, is the evidence.
               Fixing one value here is what makes a fresh test single-look.
Allowance      ALLOWANCE_PX = 0.05 mean EPE against fixed 12.
Upsampling     hoisted: one call after the loop, never per iteration. Verified bitwise equal to
               stock RAFT at both 8 and 12 iterations.

WHAT IS NOT FROZEN, AND MUST NOT BE TUNED AFTERWARDS
----------------------------------------------------
Nothing. If a future run changes the score, the branch point, the threshold, the guard or the
allowance, it is a new policy and needs a new file and a new hash. Reporting a second variant
against the same fresh cases spends the single look.

PROVENANCE, INCLUDING THE FAILURES
----------------------------------
1. A ranking curve was first reported as a policy, with the cutoff read off the held-out half. Not
   achievable by any rule fixed in advance. Withdrawn.
2. The cutoff was then frozen on the configuration half as the smallest continue-set meeting the
   allowance, which is marginal by construction. That rule GENUINELY FAILED on the holdout, missing
   by 0.022 px, because the same cutoff continues 47 of 100 configuration cases and 36 held-out.
3. This guard addresses that calibration failure. On the held-out half it continues 42 of 100, gives
   9.68 mean iterations (19.3% fewer) and 5.6756 mean EPE against 5.6270 for fixed 12, a slack of
   +0.0014 px inside the allowance.

The held-out half has now been examined, so step 3 is development evidence and not a test. A fresh
test needs unused scenes: KITTI-2012 or the Sintel training split.

LATENCY IS UNMEASURED
---------------------
No run has yet executed this threshold on full-resolution cases and timed it. The CPU figure of
roughly 10.7% is a projection composed from separately timed fixed configurations on one image pair,
not a measurement of this rule. It does not establish that any latency gate passed.
"""
from __future__ import annotations

SCORE_NAME = "u8"
THETA = 0.7017
Z = 1.645
BRANCH_AT = 8
MAX_ITERS = 12
ALLOWANCE_PX = 0.05
SCALE = 8.0
FROZEN_ON = "2026-09-20"


def score_from_delta(delta, scale: float = SCALE) -> float:
    """The frozen score, computed from the coarse update tensor of iteration BRANCH_AT.

    `delta` is the update_block's delta_flow, shape (N, 2, H/8, W/8). The `.item()` is part of the
    policy and part of its cost: on GPU it forces a device synchronisation, and any timing that
    omits it is not timing this rule. The reduction matches TrajectoryRecorder.step exactly: that
    method calls .abs() first, which is a no-op here because the values are then squared.
    """
    d = delta.detach().float() * scale
    return round(float((d ** 2).sum(dim=-3).sqrt().mean().item()), 4)


def should_continue(score: float, theta: float = THETA) -> bool:
    """True means spend iterations BRANCH_AT+1 .. MAX_ITERS. Ties continue."""
    return score >= theta
