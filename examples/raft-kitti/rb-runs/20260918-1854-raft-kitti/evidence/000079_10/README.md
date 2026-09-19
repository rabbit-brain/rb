# Evidence for 000079_10

20260918-1854-raft-kitti | 000079_10 | improved, unstable, improved_unstable

error 4.68 -> 0.78 px. Error fell by 3.90 px on this case. The candidate made 4% of its refinement in the last third of its iterations and reversed direction 3 times, above your 25% / 2 limits. The current model settled at 6%. The error looks fine; the answer is not settled.

Re-run now: reproduced the run's numbers.

Scales: flow colour saturates at the 95th percentile of the flow magnitudes, with square-root saturation so slow regions keep their colour; error, disagreement and filmstrip maps run to the 99th percentile of their values, capped at four times the mean (regions above that saturate); the filmstrip's scale is shared by both models and comes from their late iterations; the trajectory axis is logarithmic.

Files: inputs.png, flow.png, error.png, filmstrip.png, trajectory.png, case.png.
