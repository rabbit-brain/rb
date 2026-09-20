# GPU measurement of the frozen streaming policy

_2026-09-20, RTX 4090, torch 2.8.0+cu128, CUDA 12.8, kernel 6.8.0-134, `rabbit-brain` 0.2.2,
checkpoint `raft-things.pth`, KITTI-2015 held-out split (100 cases), padded 376x1248, batch 1,
`mixed_precision = false`, `alternate_corr = false`._

This is the run that closes the gap between the policy evaluated for accuracy and the implementation
measured for speed. Both come from the same execution, on the same cases.

Policy under test: `frozen_rule.py`, sha256 `0345a0292038c3cb2e57bb68fc87d81ed278b24a6801c961d274f7d53c9f8c2b`,
frozen and committed (`d41a409`) before this run existed.

## What was executed

The rule's own score-and-branch, not a replayed schedule. Each case runs 8 iterations, computes
`u8` from the coarse update (including the `.item()` that forces a device synchronisation), compares
it to 0.7017, and continues to 12 or stops. Against it, paired and interleaved with arm order
alternated each round, the equally optimised fixed-12 baseline (upsampling hoisted in both). A third
arm replays each case's own decision without computing the score, so the controller's cost is the
difference between two arms doing otherwise identical work.

## Results, 100 cases, 5 rounds

| | |
|---|---|
| Continued | 42 / 100 |
| Mean iterations | 9.68 (19.3% fewer than 12) |
| Mean EPE, policy | 5.6756 px |
| Mean EPE, fixed 12 | 5.6270 px |
| Delta | +0.0486 px, inside the 0.05 allowance by 0.0014 |
| **Measured latency saving** | **+13.1%** (25.1 ms vs 28.9 ms per case) |
| Controller cost | median +0.075 ms, 499/500 trials positive, 0.26% of a pass |
| Bitwise | policy == stock RAFT at its own count, replay == policy, baseline == stock 12, all 100 |
| Predicted vs executed decisions | 100 agree, 0 disagree |

Split by decision: the 58 stopped cases run 22.18 ms against the baseline's 28.87 ms, a median
saving of 6.70 ms. The 42 continued cases run 29.09 ms against 28.85 ms, paying 0.23 ms for the
controller and the branch. Worst per-case harm is `000091_10` at +1.348 px; next worst +0.44.

The decisions are not knife-edge. The nearest score below the threshold is 0.6940 and the nearest
above is 0.7547, so no case sits within 0.008 of 0.7017.

## Component profile, 12 cases, 5 reps, 12 iterations

| Component | ms/pass | share | calls | ms/call |
|---|---|---|---|---|
| Refinement (`update_block`) | 17.96 | 57.3% | 12 | 1.497 |
| Encoder `fnet` | 4.99 | 15.9% | 1 | 4.987 |
| Correlation lookup | 4.79 | 15.3% | 12 | 0.396 |
| Encoder `cnet` | 2.09 | 6.7% | 1 | 2.086 |
| Correlation build | 1.44 | 4.6% | 1 | 1.439 |
| Upsampling (hoisted) | 0.10 | 0.3% | 1 | 0.094 |

Instrumented total 31.36 ms against a clean end-to-end median of 28.81 ms, so synchronising each
region costs +2.55 ms (+8.8%) by serialising work that otherwise overlaps. Shares above are shares
of the instrumented total and inherit that distortion.

## The two measurements agree

The loop's share of the pass can be had two ways. The profile's instrumented split gives 72.5%.
The policy run gives it without any instrumentation at all, from the 6.70 ms that four fewer
iterations save on the stopped cases: 1.675 ms per iteration, 20.1 ms for twelve, **69.7%** of the
28.81 ms pass. The clean differential is the more trustworthy of the two.

That number predicts the result: 19.3% fewer iterations times a 69.7% loop share is 13.5%, less the
controller's 0.26%, giving 13.2% against 13.1% measured.

It also explains why GPU beats the CPU projection of about 10.7%. The loop is ~70% of the pass here
against 56.7% on CPU, because the encoder is large parallel convolutions the GPU absorbs cheaply
while the loop is many small sequential ops. The `.item()` synchronisation that was the main worry
costs 0.075 ms, a quarter of a percent.

## What this does not establish

The held-out half has now been examined repeatedly, so the accuracy figure is development evidence,
not a test. What is new is that the latency is measured rather than projected, on the same cases and
the same executed policy as the accuracy. A fresh test needs unused scenes: KITTI-2012, or Sintel's
training split. Batch 1 only; a batched or streamed deployment moves the synchronisation cost.

## Artifacts

| File | sha256 |
|---|---|
| `frozen_rule.py` | `0345a0292038c3cb2e57bb68fc87d81ed278b24a6801c961d274f7d53c9f8c2b` |
| `gpu_policy_run.py` | `ce46c5a00b8d222ff14af15987c72b8dbdde4c39023aac6807b03c200565b442` |
| `profile_gpu.py` | `17be9c413379739716e8de0e3dce42d1f0deadf7262153231fcd3fb31873cc59` |
| `results/gpu_policy.json` | `b7c5fb99ee4f467e269bd3f96d0aaa41dedfda69ebae45e3080e76864a7b1642` |
| `results/gpu_profile.json` | `c392c03604fda35f8665049bee12279754d0ca25f396bcd7cb4bd53c474272da` |
