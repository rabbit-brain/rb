"""Deterministic 100/100 split of the KITTI-2015 training set for the deployment-build experiment.

The seed is the date this protocol was frozen (2026-09-20), chosen so that it is transparently
not the product of a search over seeds. Re-running this file reproduces both lists exactly.
"""
import hashlib, random

SEED = 20260920
CASES = [f"{i:06d}_10" for i in range(200)]          # KITTI-2015 training: 000000_10 .. 000199_10

rng = random.Random(SEED)
shuffled = CASES[:]
rng.shuffle(shuffled)
config, holdout = sorted(shuffled[:100]), sorted(shuffled[100:])

assert len(config) == len(holdout) == 100
assert not set(config) & set(holdout)
assert sorted(config + holdout) == CASES

for name, ids in (("config-cases.txt", config), ("holdout-cases.txt", holdout)):
    open(name, "w").write("\n".join(ids) + "\n")
    digest = hashlib.sha256(("\n".join(ids) + "\n").encode()).hexdigest()
    print(f"{name}: {len(ids)} cases, sha256 {digest}")

print(f"seed: {SEED}")
print(f"config first/last: {config[0]} .. {config[-1]}")
print(f"holdout first/last: {holdout[0]} .. {holdout[-1]}")
