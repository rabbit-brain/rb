# Evidence for 000095_10

20260918-2333-raft-small | 000095_10 | regression, unstable, trajectory_regression

error 2.97 -> 25.18 px. Error increased by 22.21 px, exceeding your 0.3 px limit. The candidate was still moving its answer by 1.371 per iteration at the end, 0.955 more than the current model on this case (0.416), above your 0.3 limit. The error regressed as well.

Re-run now: reproduced the run's numbers.

Scales: flow colour saturates at the 95th percentile of the flow magnitudes, with square-root saturation so slow regions keep their colour; error, disagreement and filmstrip maps run to the 99th percentile of their values, capped at four times the mean (regions above that saturate); the filmstrip's scale is shared by both models and comes from their late iterations; the trajectory axis is logarithmic.

Files: inputs.png, flow.png, error.png, filmstrip.png, trajectory.png, case.png.
